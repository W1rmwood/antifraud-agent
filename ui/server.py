"""Веб-интерфейс «Антимошенника»: HTTP-мост между браузером и agent/ui_api.py.

    .venv/bin/python -m ui.server [--port 8642]     # потом открыть http://127.0.0.1:8642

Браузер не умеет запускать процессы и читать data/runs/, поэтому мост делает ровно
то, что в docs/frontend.md делает Streamlit: start_call / start_replay / stop и хвост
лога событий. События уходят в браузер как есть (контракт №3) через Server-Sent Events,
экран собирает из них состояние сам (ui/web/src/callView.js повторяет call_view).

Только стандартная библиотека: зависимости проекта не меняются. Слушает 127.0.0.1 —
интерфейс локальный, как и весь агент. Собранный фронт берётся из ui/web/dist
(`npm run build` в ui/web); при разработке его отдаёт `npm run dev` с прокси на этот порт.
"""

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import BaseModel

from agent import ui_api
from agent.ui_api import CallProcess, EventTail

WEB_DIST = Path(__file__).resolve().parent / "web" / "dist"
POLL_S = 0.2  # как часто дочитывать лог звонка
HEARTBEAT_S = 15.0  # пустая строка в поток, чтобы прокси и браузер не закрыли тихое соединение
CALL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # call_id — часть имени файла в data/runs/
UPLOADS_DIR = ui_api.ROOT / "data" / "uploads"  # записи, загруженные через интерфейс
UPLOAD_TYPES = {".wav", ".flac", ".ogg", ".mp3", ".txt"}  # что читает audio.stream.listen (soundfile или транскрипт)
UPLOAD_MAX_BYTES = 300 * 1024 * 1024
NO_DB = "База кейсов ещё не создана: .venv/bin/python -m storage.seed_demo (или проведите первый звонок)"

# Записанные звонки для режима «Запись (без модели)»: путь от корня проекта → подпись.
RECORDS: dict[str, str] = {
    "data/runs/demo.jsonl": "Демо для защиты: «мама, я попал в аварию»",
    "data/mock_events/fraud_relative.jsonl": "«Мама, я попал в аварию» — родственник в беде",
    "data/mock_events/fraud_bank_call.jsonl": "Звонок «из ЦБ» — агент ошибся схемой",
    "data/mock_events/no_fraud.jsonl": "Настоящий звонок из банка — без мошенничества",
    "data/mock_events/example.jsonl": "Эталон Александра — все типы событий",
}


class Calls:
    """Звонки, запущенные этим сервером. Модель одна, поэтому идёт не больше одного звонка."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, CallProcess] = {}
        self._sources: dict[str, str] = {}  # call_id → file / mic / loopback / replay
        self._stopping: set[str] = set()

    def get(self, call_id: str) -> CallProcess | None:
        return self._calls.get(call_id)

    def running(self) -> CallProcess | None:
        return next((c for c in self._calls.values() if c.running), None)

    def start(self, body: dict[str, Any]) -> CallProcess:
        """Запускает звонок или проигрывание. ValueError — неверный запрос, RuntimeError — уже идёт звонок."""
        with self._lock:
            if (busy := self.running()) is not None:
                raise RuntimeError(f"уже идёт звонок {busy.call_id}")
            source = body.get("source")
            if source == "replay":
                path = body.get("path")
                if path not in RECORDS:
                    raise ValueError("нет такой записи")
                call = ui_api.start_replay(path, speed=float(body.get("speed") or 1.0))
            elif source == "file":
                path = body.get("path") or ""
                chosen = next((c for c in ui_api.list_test_calls() if c.path == path), None)
                if chosen is None and not _is_upload(path):
                    raise ValueError("нет такого файла: выберите тестовый звонок или загрузите запись")
                phone = _phone(body.get("phone")) or (chosen.phone if chosen else None)
                call = ui_api.start_call(source="file", path=path, phone=phone, realtime=bool(body.get("realtime", True)))
            elif source in ("mic", "loopback"):
                call = ui_api.start_call(source=source, phone=_phone(body.get("phone")))
            else:
                raise ValueError("source: file, mic, loopback или replay")
            self._calls[call.call_id] = call
            self._sources[call.call_id] = source
            return call

    def stop(self, call: CallProcess) -> None:
        """«Стоп» ждёт до 45 с, пока агент допишет отчёт, — поэтому в отдельном потоке."""
        if call.call_id in self._stopping:
            return
        self._stopping.add(call.call_id)
        threading.Thread(target=call.stop, daemon=True).start()

    def source(self, call_id: str) -> str | None:
        return self._sources.get(call_id)

    def status(self, call: CallProcess) -> dict[str, Any]:
        running = call.running
        return {
            "call_id": call.call_id,
            # Проигрывание пишет в лог исходный source записи — экрану нужно знать, что это запись.
            "source": self._sources.get(call.call_id),
            "running": running,
            "stopping": running and call.call_id in self._stopping,
            "returncode": call.returncode,
            "error": "" if running or call.returncode == 0 else call.error_text(),
        }


def _json(value: Any) -> Any:
    """Модели, датаклассы и списки из ui_api — в то, что понимает json.dumps."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list | tuple):
        return [_json(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value


class Handler(BaseHTTPRequestHandler):
    calls: Calls  # задаётся в make_server
    server_version = "antifraud-ui"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — так зовётся параметр в базовом классе
        pass  # браузер опрашивает часто, журнал запросов только мешает

    # --- маршруты ---

    def do_GET(self) -> None:  # noqa: N802 — имя задаёт http.server
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        query = parse_qs(url.query)
        if parts[:1] != ["api"]:
            return self._static(url.path)
        match parts[1:]:
            case ["sources"]:
                busy = self.calls.running()
                self._send(200, {
                    "calls": _json(ui_api.list_test_calls()),
                    "records": [{"path": p, "title": t} for p, t in RECORDS.items() if (ui_api.ROOT / p).exists()],
                    "busy": busy.call_id if busy else None,
                })
            case ["calls", call_id, "events"]:
                self._events(call_id)
            case ["calls", call_id]:
                call = self.calls.get(call_id)
                self._send(200, self.calls.status(call)) if call else self._error(404, "звонок не найден")
            case ["runs"]:
                self._send(200, _runs(self.calls))
            case ["health"]:
                self._send(200, _health())
            case ["schemes"]:
                self._send(200, [_scheme_brief(p) for p in sorted(ui_api.SCHEMES_DIR.glob("*.json"))])
            case ["schemes", scheme_id]:
                scheme = ui_api.get_scheme(scheme_id) if CALL_ID.match(scheme_id) else None
                self._send(200, _json(scheme)) if scheme else self._error(404, "схема не найдена")
            case ["cases"]:
                limit = int(query.get("limit", ["20"])[0])
                try:
                    self._send(200, _json(ui_api.recent_cases(min(max(limit, 1), 500))))
                except sqlite3.OperationalError:
                    self._error(503, NO_DB)
            case ["numbers", phone]:
                try:
                    info, cases = ui_api.number_history(phone)
                except ValueError as exc:
                    return self._error(400, str(exc))
                except sqlite3.OperationalError:
                    return self._error(503, NO_DB)
                self._send(200, {"info": _json(info), "cases": _json(cases)})
            case _:
                self._error(404, "нет такого адреса")

    def do_POST(self) -> None:  # noqa: N802
        parts = [p for p in urlparse(self.path).path.strip("/").split("/") if p]
        length = int(self.headers.get("Content-Length") or 0)
        if parts == ["api", "upload"] and length > UPLOAD_MAX_BYTES:
            return self._error(413, f"файл больше {UPLOAD_MAX_BYTES // 1024 // 1024} МБ")
        body_bytes = self.rfile.read(length)
        body: dict[str, Any] = {}
        if parts != ["api", "upload"]:
            try:
                body = json.loads(body_bytes or b"{}")
            except json.JSONDecodeError:
                return self._error(400, "тело запроса — не JSON")
        match parts:
            case ["api", "calls"]:
                try:
                    call = self.calls.start(body)
                except ValueError as exc:
                    return self._error(400, str(exc))
                except RuntimeError as exc:
                    return self._error(409, str(exc))
                self._send(201, self.calls.status(call))
            case ["api", "upload"]:
                try:
                    self._send(201, _save_upload(parse_qs(urlparse(self.path).query).get("name", [""])[0], body_bytes))
                except ValueError as exc:
                    self._error(400, str(exc))
            case ["api", "calls", call_id, "stop"]:
                call = self.calls.get(call_id)
                if call is None:
                    return self._error(404, "звонок не найден")
                self.calls.stop(call)
                self._send(202, self.calls.status(call))
            case _:
                self._error(404, "нет такого адреса")

    # --- поток событий ---

    def _events(self, call_id: str) -> None:
        """Server-Sent Events: события из лога по мере записи, потом состояние процесса.

        `id` события — его номер в логе: при переподключении браузер присылает Last-Event-ID,
        и поток продолжается с места обрыва. Для звонка, который запускал не этот сервер
        (старый лог из data/runs/), отдаётся то, что записано, и поток закрывается.
        """
        if not CALL_ID.match(call_id):
            return self._error(400, "неверный call_id")
        call = self.calls.get(call_id)
        path = ui_api.RUNS_DIR / f"{call_id}.jsonl"
        stderr = ui_api.RUNS_DIR / f"{call_id}.stderr.log"
        if call is None and not path.exists() and not stderr.exists():
            return self._error(404, "лога звонка нет")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        tail = EventTail(path)
        sent = int(self.headers.get("Last-Event-ID") or -1) + 1
        quiet_since = time.monotonic()
        last_status = ""
        try:
            while True:
                tail.read_new()
                alive = call is not None and call.running
                for i in range(sent, len(tail.events)):
                    self._sse("agent", tail.events[i].model_dump_json(), i)
                if len(tail.events) > sent:
                    sent, quiet_since = len(tail.events), time.monotonic()
                if call is not None:
                    # Состояние процесса — только когда оно меняется (идёт → завершаю → закончен).
                    status = json.dumps(self.calls.status(call), ensure_ascii=False)
                    if status != last_status:
                        self._sse("process", status)
                        last_status = status
                if tail.finished or not alive:
                    # Процесс мог дописать последние строки между чтением и проверкой — дочитываем.
                    tail.read_new()
                    for i in range(sent, len(tail.events)):
                        self._sse("agent", tail.events[i].model_dump_json(), i)
                    if call is None and not tail.finished and stderr.exists():
                        # Звонок запускал прошлый сервер и он оборвался: причина — в его stderr.
                        error = "\n".join(stderr.read_text(encoding="utf-8", errors="replace").splitlines()[-20:])
                        status = {"call_id": call_id, "source": None, "running": False, "stopping": False, "returncode": -1, "error": error}
                        self._sse("process", json.dumps(status, ensure_ascii=False))
                    self._sse("end", "{}")
                    return
                if time.monotonic() - quiet_since > HEARTBEAT_S:
                    self.wfile.write(b": heartbeat\n\n")
                    quiet_since = time.monotonic()
                self.wfile.flush()
                time.sleep(POLL_S)
        except (BrokenPipeError, ConnectionResetError):
            return  # вкладку закрыли

    def _sse(self, event: str, data: str, event_id: int | None = None) -> None:
        head = f"id: {event_id}\n" if event_id is not None else ""
        self.wfile.write(f"{head}event: {event}\ndata: {data}\n\n".encode())
        self.wfile.flush()

    # --- ответы ---

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"error": message})

    def _static(self, url_path: str) -> None:
        """Собранный фронт; неизвестный путь — index.html (экраны переключает сам фронт)."""
        if not (WEB_DIST / "index.html").exists():
            body = "Фронт не собран: cd ui/web && npm install && npm run build".encode()
            self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        file = (WEB_DIST / unquote(url_path).lstrip("/")).resolve()
        if not file.is_file() or WEB_DIST.resolve() not in file.parents:
            file = WEB_DIST / "index.html"
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _CONTENT_TYPES.get(file.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        # Файлы сборки с хэшем в имени не меняются, index.html — меняется.
        self.send_header("Cache-Control", "no-cache" if file.name == "index.html" else "max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(body)


_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _phone(raw: Any) -> str | None:
    """Номер звонящего из формы: «8 999 …» → +7999…; пусто — None, неверный — ValueError."""
    if not raw:
        return None
    from storage.db import normalize_phone  # noqa: PLC0415

    return normalize_phone(str(raw))


def _is_upload(path: str) -> bool:
    file = (ui_api.ROOT / path).resolve()
    return file.is_file() and UPLOADS_DIR.resolve() in file.parents


def _save_upload(name: str, data: bytes) -> dict[str, Any]:
    """Запись звонка из браузера → data/uploads/<время>-<имя>. Путь отдаётся для start_call."""
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOAD_TYPES:
        raise ValueError(f"агент читает {', '.join(sorted(UPLOAD_TYPES))}, а это {suffix or 'файл без расширения'}")
    if not data:
        raise ValueError("файл пустой")
    stem = re.sub(r"[^\w.-]+", "-", Path(name).stem, flags=re.UNICODE).strip("-")[:60] or "call"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOADS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{stem}{suffix}"
    target.write_bytes(data)
    return {"path": str(target.relative_to(ui_api.ROOT)), "name": name, "size": len(data)}


def _scheme_brief(path: Path) -> dict[str, Any]:
    """Строка каталога схем: без этапов целиком, чтобы список грузился сразу."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    keys = ("id", "name", "aliases", "category", "legend", "description")
    return {**{k: raw.get(k) for k in keys}, "stages": [{"n": s["n"], "name": s["name"]} for s in raw["stages"]]}


def _runs(calls: Calls | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Звонки этого компьютера из data/runs/: история, даже если кейса в базе нет.

    Проигрывание записи кейс в базу не сохраняет — без этого списка звонок, показанный
    на защите, не попал бы в историю. replay=True, если отчёт в логе — от другого звонка
    (проигрыватель переписывает call_id событий, но не содержимое отчёта).
    """
    logs = [p for p in ui_api.RUNS_DIR.glob("*.jsonl") if p.name != "demo.jsonl" and CALL_ID.match(p.stem)]
    out: list[dict[str, Any]] = []
    for path in sorted(logs, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            events = ui_api.read_events(path)
        except ValueError:
            continue  # лог повреждён — в истории его не показываем
        if not events:
            continue
        summary = next((e.payload for e in reversed(events) if e.type == "summary"), None)
        started = next((e.payload for e in events if e.type == "call_started"), {})
        scheme = next((e.payload for e in reversed(events) if e.type == "scheme_detected"), None)
        proc = calls.get(path.stem) if calls else None
        out.append({
            "call_id": path.stem,
            "started_at": events[0].ts.isoformat(),
            "last_at": events[-1].ts.isoformat(),
            "phone": started.get("phone"),
            "running": bool(proc and proc.running),
            "ended": events[-1].type == "call_ended",
            "summary": summary,
            "scheme": {"id": scheme["scheme_id"], "name": scheme["scheme_name"], "confidence": scheme["confidence"]} if scheme else None,
            "warnings": sum(e.type == "warning" for e in events),
            "lines": sum(e.type == "transcript" for e in events),
            "replay": (bool(summary) and summary.get("call_id") != path.stem) or (calls is not None and calls.source(path.stem) == "replay"),
        })
    return out


# Что нужно агенту для звонка через модель (запись проигрывается и без этого).
AGENT_MODULES = {
    "mcp": "MCP-клиент", "langgraph": "граф агента", "ollama": "клиент Ollama",
    "chromadb": "индекс схем", "faster_whisper": "распознавание речи", "sounddevice": "звук с микрофона",
}


def _health() -> dict[str, Any]:
    """Готов ли компьютер к звонку через модель — чтобы не узнавать это по упавшему агенту."""
    from agent.config import get_settings  # noqa: PLC0415 — настройки нужны только этой проверке

    settings = get_settings()
    checks: list[dict[str, Any]] = []
    missing = [m for m in AGENT_MODULES if importlib.util.find_spec(m) is None]
    checks.append({
        "key": "deps", "label": "Зависимости агента", "ok": not missing,
        "detail": "установлены" if not missing else f"нет: {', '.join(missing)}",
        "fix": ".venv/bin/pip install -r requirements.txt",
    })
    models: list[str] = []
    try:
        with urllib.request.urlopen(f"{settings.ollama_host}/api/tags", timeout=0.5) as resp:
            models = [m["name"] for m in json.loads(resp.read()).get("models", [])]
        ollama_ok = True
    except (OSError, ValueError):
        ollama_ok = False
    has = lambda name: any(m == name or m.split(":")[0] == name for m in models)  # noqa: E731
    for key, name in (("llm", settings.llm_model), ("embed", settings.embed_model)):
        ok = ollama_ok and has(name)
        checks.append({
            "key": key, "label": f"Модель {name}", "ok": ok,
            "detail": "готова" if ok else ("Ollama не запущена" if not ollama_ok else "не скачана"),
            "fix": "ollama serve" if not ollama_ok else f"ollama pull {name}",
        })
    index = ui_api.ROOT / settings.chroma_dir
    checks.append({
        "key": "index", "label": "Индекс схем", "ok": index.is_dir() and any(index.iterdir()),
        "detail": "собран" if index.is_dir() and any(index.iterdir()) else "не собран",
        "fix": ".venv/bin/python -m rag.index",
    })
    db_file = ui_api.ROOT / settings.db_path
    checks.append({
        "key": "db", "label": "База кейсов", "ok": db_file.exists(),
        "detail": "есть" if db_file.exists() else "не создана",
        "fix": ".venv/bin/python -m storage.seed_demo",
    })
    # Записи звонков лежат в Git LFS: без `git lfs pull` вместо .wav — указатель на 130 байт.
    wavs = list(ui_api.CALLS_DIR.glob("*.wav"))
    pointers = [w for w in wavs if w.stat().st_size < 1024]
    checks.append({
        "key": "audio", "label": "Записи тестовых звонков", "ok": bool(wavs) and not pointers,
        "detail": "скачаны" if wavs and not pointers else "не скачаны из Git LFS",
        "fix": "git lfs install && git lfs pull",
    })
    # Модель распознавания речи качается при первом распознавании, а во время звонка интернета нет:
    # без этой проверки звонок со звуком падает уже после «Старт». Расшифровкам (.txt) она не нужна.
    whisper = os.environ.get("ANTIFRAUD_WHISPER_MODEL", "small")
    hub = Path(os.environ.get("HF_HUB_CACHE", Path.home() / ".cache" / "huggingface" / "hub"))
    cached = (hub / f"models--Systran--faster-whisper-{whisper}").is_dir()
    checks.append({
        "key": "asr", "label": f"Распознавание речи (whisper {whisper})", "ok": cached,
        "detail": "модель скачана" if cached else "модель не скачана",
        "fix": f".venv/bin/python -c \"from faster_whisper import WhisperModel; WhisperModel('{whisper}')\"",
    })
    model_ready = all(c["ok"] for c in checks if c["key"] in ("deps", "llm", "embed", "index"))
    return {"model_ready": model_ready, "checks": checks}


def make_server(port: int = 8642, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"calls": Calls()})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True  # открытые потоки событий не держат сервер при выходе
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Веб-интерфейс «Антимошенника».")
    parser.add_argument("--port", type=int, default=8642)
    args = parser.parse_args()
    server = make_server(args.port)
    print(f"Антимошенник: http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Идущий звонок останавливаем штатно: агент допишет отчёт и сохранит кейс.
        if (call := server.RequestHandlerClass.calls.running()) is not None:
            call.stop()
        server.server_close()


if __name__ == "__main__":
    main()
