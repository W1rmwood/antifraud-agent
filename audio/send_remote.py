"""Проверочный клиент: ведёт себя как приложение на iPhone, но звук берёт из файла.

    python -m audio.send_remote data/calls/001.wav --server http://127.0.0.1:8642 [--token 123456] [--phone +7…]

Запускает на сервере (ui/server.py) звонок с source="remote", шлёт файл кусками PCM s16le 16 кГц
моно по 250 мс в реальном темпе (POST /api/calls/<id>/audio?seq=N), печатает события из SSE,
а когда файл кончился, жмёт «Стоп» и дочитывает поток до call_ended. Так проверяется вся цепочка
«звук → мост → stdin агента → события» без iPhone. API — docs/ios/architecture.md.

Код возврата: 0 — пришёл call_ended, 1 — нет (ошибка сервера, звонок упал или не дождались).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request

import numpy as np

from audio.capture import SAMPLE_RATE, load_channels

CHUNK_MS = 250  # как у телефона: 8000 байт на кусок
RETRIES = 5  # повторов одного куска при обрыве сети
END_WAIT_S = 180.0  # сколько ждать call_ended после «Стоп»: агент дораспознаёт хвост и пишет отчёт


class Server:
    """Запросы к мосту с кодом доступа."""

    def __init__(self, base: str, token: str | None) -> None:
        self.base = base.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, data: bytes | None = None,
                content_type: str = "application/json", timeout: float = 15.0) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", content_type)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()

    def json(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        status, raw = self.request(method, path, data)
        try:
            return status, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return status, {"error": raw.decode(errors="replace")}


def pcm_chunks(path: str, chunk_ms: int = CHUNK_MS) -> list[bytes]:
    """Файл → куски PCM s16le 16 кГц моно. Стерео сводится в моно: телефон слышит обе стороны сразу."""
    channels = load_channels(path)
    mono = np.mean(channels, axis=0) if len(channels) > 1 else channels[0]
    raw = (np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes()
    size = SAMPLE_RATE * chunk_ms // 1000 * 2
    return [raw[i:i + size] for i in range(0, len(raw), size)]


def describe(event: dict) -> str:
    """Строка события для терминала — коротко, чтобы видеть, что агент живой."""
    p = event.get("payload") or {}
    match event.get("type"):
        case "call_started":
            return f"звонок начат, источник {p.get('source', {}).get('kind')}, номер {p.get('phone') or 'неизвестен'}"
        case "transcript":
            return f"реплика ({p.get('speaker')}): {p.get('text')}"
        case "scheme_detected":
            return f"СХЕМА: {p.get('scheme_name')} (уверенность {p.get('confidence')})"
        case "stage_changed":
            return f"этап {p.get('stage_n')}: {p.get('stage_name')}"
        case "warning":
            return f"⚠ {p.get('text')}"
        case "suggestion":
            return f"подсказка [{p.get('kind')}]: {p.get('text')}"
        case "summary":
            return f"ИТОГ: {p.get('summary')}"
        case "call_ended":
            return "звонок завершён, кейс сохранён"
        case other:
            return str(other)


class EventReader(threading.Thread):
    """Читает SSE звонка и печатает события; при обрыве переподключается с Last-Event-ID."""

    def __init__(self, server: Server, call_id: str) -> None:
        super().__init__(daemon=True)
        self.server, self.call_id = server, call_id
        self.last_id: str | None = None
        self.ended = threading.Event()  # пришёл call_ended
        self.done = threading.Event()  # поток закрыт сервером (event: end) или сдались
        self.process: dict = {}
        self.started = time.monotonic()

    def run(self) -> None:
        failures = 0
        while not self.done.is_set() and failures < 10:
            req = urllib.request.Request(f"{self.server.base}/api/calls/{self.call_id}/events")
            if self.server.token:
                req.add_header("Authorization", f"Bearer {self.server.token}")
            if self.last_id is not None:
                req.add_header("Last-Event-ID", self.last_id)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    failures = 0
                    self._read(resp)
            except urllib.error.HTTPError as err:
                print(f"! поток событий: HTTP {err.code} {err.read().decode(errors='replace')}", file=sys.stderr)
                if err.code in (401, 404):
                    break
                failures += 1
            except OSError as err:
                print(f"! поток событий оборвался ({err}), переподключаюсь", file=sys.stderr)
                failures += 1
                time.sleep(min(5.0, 0.5 * failures))
        self.done.set()

    def _read(self, resp) -> None:
        event, data, event_id = None, [], None
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith(":"):
                continue  # heartbeat
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data.append(line[6:])
            elif line.startswith("id: "):
                event_id = line[4:]
            elif line == "" and data:
                self._handle(event, "\n".join(data), event_id)
                event, data, event_id = None, [], None
                if self.done.is_set():
                    return

    def _handle(self, event: str | None, data: str, event_id: str | None) -> None:
        if event_id is not None:
            self.last_id = event_id
        payload = json.loads(data)
        elapsed = time.monotonic() - self.started
        if event == "agent":
            print(f"[{int(elapsed) // 60:02d}:{int(elapsed) % 60:02d}] {describe(payload)}", flush=True)
            if payload.get("type") == "call_ended":
                self.ended.set()
        elif event == "process":
            self.process = payload
            if not payload.get("running") and payload.get("returncode") not in (0, None):
                print(f"! агент упал (код {payload.get('returncode')}):\n{payload.get('error')}", file=sys.stderr)
        elif event == "end":
            self.done.set()


def send(server: Server, call_id: str, chunks: list[bytes], chunk_ms: int, speed: float) -> bool:
    """Шлёт куски по порядку в темпе звонка; обрыв сети — повтор того же seq. False — звонок кончился раньше."""
    t0 = time.monotonic()
    for seq, chunk in enumerate(chunks):
        if speed > 0:
            time.sleep(max(0.0, t0 + seq * chunk_ms / 1000 / speed - time.monotonic()))
        for attempt in range(RETRIES):
            try:
                status, body = server.request("POST", f"/api/calls/{call_id}/audio?seq={seq}", chunk,
                                              "application/octet-stream")
            except OSError as err:
                print(f"! кусок {seq}: {err}, повтор", file=sys.stderr)
                time.sleep(0.5 * (attempt + 1))
                continue
            if status == 204:
                break
            # 404/409 — звонок закончился, дальше слать некуда; 400/401/413 — ошибка клиента, повтор не поможет.
            print(f"! кусок {seq}: HTTP {status} {body.decode(errors='replace')}", file=sys.stderr)
            return False
        else:
            print(f"! кусок {seq} не ушёл за {RETRIES} попыток — пропускаю", file=sys.stderr)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Шлёт звук из файла на ui.server как телефон (source=remote).")
    parser.add_argument("file", help="запись звонка (.wav/.flac/.ogg); стерео сводится в моно")
    parser.add_argument("--server", default="http://127.0.0.1:8642", help="адрес ui.server")
    parser.add_argument("--token", default=None, help="код доступа (нужен, если сервер не на этом компьютере)")
    parser.add_argument("--phone", default=None, help="номер звонящего, +7XXXXXXXXXX")
    parser.add_argument("--chunk-ms", type=int, default=CHUNK_MS, help="длина куска, мс")
    parser.add_argument("--speed", type=float, default=1.0, help="темп: 1 — реальный, 2 — вдвое быстрее, 0 — без пауз")
    args = parser.parse_args(argv)

    server = Server(args.server, args.token)
    try:
        status, info = server.json("GET", "/api/info")
    except OSError as err:
        print(f"Сервер {args.server} недоступен: {err}", file=sys.stderr)
        return 1
    if status != 200 or info.get("app") != "antifraud":
        print(f"По адресу {args.server} не наш сервер: HTTP {status} {info}", file=sys.stderr)
        return 1
    chunks = pcm_chunks(args.file, args.chunk_ms)
    print(f"Файл {args.file}: {len(chunks)} кусков по {args.chunk_ms} мс "
          f"(~{len(chunks) * args.chunk_ms / 1000:.0f} с звука)", flush=True)

    body = {"source": "remote", **({"phone": args.phone} if args.phone else {})}
    status, started = server.json("POST", "/api/calls", body)
    if status != 201:
        print(f"Звонок не запустился: HTTP {status} {started.get('error', started)}", file=sys.stderr)
        return 1
    call_id = started["call_id"]
    print(f"Звонок {call_id} запущен, шлю звук…", flush=True)
    reader = EventReader(server, call_id)
    reader.start()
    try:
        complete = send(server, call_id, chunks, args.chunk_ms, args.speed)
        print("Звук отправлен целиком, жму «Стоп»…" if complete else "Звонок закончился раньше файла.", flush=True)
    except KeyboardInterrupt:
        print("\nПрервано, жму «Стоп»…", flush=True)
    server.json("POST", f"/api/calls/{call_id}/stop", {})
    reader.done.wait(END_WAIT_S)
    if reader.ended.is_set():
        print(f"Готово: call_ended получен, лог data/runs/{call_id}.jsonl", flush=True)
        return 0
    print(f"call_ended не пришёл (состояние процесса: {reader.process or 'неизвестно'})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
