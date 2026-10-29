"""Всё, что нужно интерфейсу от бэкенда, — в одном модуле.

UI (Streamlit, ui/app.py) не импортирует граф, LLM и звук: он запускает звонок
отдельным процессом и читает события из лога data/runs/<call_id>.jsonl (ТЗ,
контракты №3 и №7). Этот модуль закрывает типовую работу вокруг этого:

    call = start_call(source="file", path="data/calls/001.wav", phone="+79990000001")
    tail = EventTail(call.events_path)
    ...                              # в цикле обновления экрана:
    tail.read_new()                  # дочитать новые события
    view = call_view(tail.events)    # готовое состояние экрана
    ...
    call.stop()                      # кнопка «Стоп»: звонок заканчивается штатно, кейс сохраняется

Проигрывание записанного звонка (data/mock_events/*.jsonl, data/runs/demo.jsonl)
идёт через тот же лог: `start_replay(...)` — и дальше тот же EventTail.
Так живой режим и проигрывание работают одним кодом экранов.
"""

import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from contracts import AgentEvent, CallCase, CallIndexEntry, EventType, NumberInfo, Scheme, parse_payload
from contracts.models import (
    SchemeDetectedPayload,
    StageChangedPayload,
    StageInfo,
    SuggestionPayload,
    ToolCallPayload,
    ToolResultPayload,
)

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "data" / "runs"
CALLS_DIR = ROOT / "data" / "calls"
SCHEMES_DIR = ROOT / "data" / "schemes"
STOP_TIMEOUT_S = 45.0  # сколько ждать штатного конца после «Стоп»: доработка разбора, отчёт LLM, save_case


# --- Запуск и остановка ---


@dataclass
class CallProcess:
    """Звонок (или проигрывание), идущий отдельным процессом."""

    call_id: str
    events_path: Path  # лог событий, который читает EventTail
    stderr_path: Path  # ошибки процесса: если звонок упал, причина здесь
    process: subprocess.Popen[bytes]

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    @property
    def returncode(self) -> int | None:
        """None — ещё идёт; 0 — закончился штатно; иное — упал (см. stderr_path)."""
        return self.process.poll()

    def stop(self, timeout_s: float = STOP_TIMEOUT_S) -> None:
        """Кнопка «Стоп»: SIGTERM — агент дорабатывает, пишет отчёт и call_ended; не успел — kill."""
        if not self.running:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def error_text(self) -> str:
        """Последние строки stderr процесса — показать пользователю, если звонок упал."""
        if not self.stderr_path.exists():
            return ""
        return "\n".join(self.stderr_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:])


def _spawn(args: list[str], call_id: str) -> CallProcess:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    events_path = RUNS_DIR / f"{call_id}.jsonl"
    stderr_path = RUNS_DIR / f"{call_id}.stderr.log"
    # Старый лог с тем же call_id убираем до запуска: иначе UI успел бы показать прошлый звонок,
    # пока новый процесс не открыл файл.
    events_path.unlink(missing_ok=True)
    # Модели — только из локального кэша: во время звонка никаких запросов в интернет (ТЗ).
    # Лог пишется туда же, где его будет читать EventTail: папку передаём процессу явно
    # (её читают и agent.config.Settings.runs_dir, и agent.replay).
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "ANTIFRAUD_RUNS_DIR": str(RUNS_DIR)}
    with stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-m", *args], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=stderr
        )
    return CallProcess(call_id, events_path, stderr_path, process)


def new_call_id() -> str:
    return f"ui-{uuid.uuid4().hex[:8]}"


def start_call(
    source: Literal["file", "mic", "loopback"] = "file",
    path: str | None = None,
    phone: str | None = None,
    call_id: str | None = None,
    realtime: bool = True,
) -> CallProcess:
    """Кнопка «Старт»: запускает агента командой контракта №7.

    `realtime` — для файла: реплики идут в темпе живого звонка (для демо — то, что нужно).
    Для mic и loopback звонок идёт, пока не вызовут stop().
    """
    call_id = call_id or new_call_id()
    args = ["agent.run", "--call-id", call_id, "--source", source, "--json"]
    if source == "file":
        if not path:
            raise ValueError("для source='file' нужен path")
        args += ["--path", path] + (["--realtime"] if realtime else [])
    if phone:
        args += ["--phone", phone]
    return _spawn(args, call_id)


def start_replay(events_file: str | Path, call_id: str | None = None, speed: float = 1.0) -> CallProcess:
    """Проигрывает записанный звонок (.jsonl событий) с настоящими паузами — для разработки UI и демо."""
    call_id = call_id or new_call_id()
    return _spawn(["agent.replay", str(events_file), "--call-id", call_id, "--speed", str(speed)], call_id)


# --- Чтение событий ---


class EventTail:
    """Дочитывает лог событий по мере того, как агент его пишет.

    Файла может ещё не быть (процесс только стартует), последняя строка может быть
    дописана наполовину — оба случая read_new() переживает и вернёт её на следующем вызове.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.events: list[AgentEvent] = []
        self._offset = 0
        self._partial = b""

    def read_new(self) -> list[AgentEvent]:
        """Новые события с прошлого вызова (и добавляет их в self.events)."""
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if size < self._offset:
            # Файл начали заново (тот же call_id запустили повторно) — читаем с начала.
            self.events, self._offset, self._partial = [], 0, b""
        with self.path.open("rb") as f:
            f.seek(self._offset)
            chunk = f.read()
        self._offset += len(chunk)
        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()  # после последнего \n — недописанная строка или пусто
        new = [AgentEvent.model_validate_json(line) for line in lines if line.strip()]
        self.events += new
        return new

    @property
    def finished(self) -> bool:
        """Пришло ли call_ended — звонок закончен, отчёт сохранён."""
        return any(e.type == EventType.call_ended for e in self.events)


def read_events(path: str | Path) -> list[AgentEvent]:
    """Все события из готового лога (например, data/mock_events/example.jsonl)."""
    tail = EventTail(path)
    tail.read_new()
    return tail.events


# --- Состояние экрана ---


@dataclass
class Line:
    """Реплика в ленте транскрипта."""

    speaker: str  # caller / user / unknown
    text: str
    t_start: float


@dataclass
class CallView:
    """Всё, что показывает экран «Живой звонок», собранное из событий.

    Пересчитывается целиком по списку событий: так одинаково работают и живой
    звонок, и проигрывание, и повторное открытие уже законченного звонка.
    """

    call_id: str | None = None
    phone: str | None = None
    source: dict | None = None
    transcript: list[Line] = field(default_factory=list)
    scheme: SchemeDetectedPayload | None = None  # последняя объявленная схема
    stages: list[StageInfo] = field(default_factory=list)  # этапы этой схемы — для прогресса
    stage: StageChangedPayload | None = None  # текущий этап и «что будет дальше»
    warnings: list[str] = field(default_factory=list)  # красные плашки, по порядку
    suggestions: list[SuggestionPayload] = field(default_factory=list)  # все подсказки, по порядку
    tools: list[ToolCallPayload | ToolResultPayload] = field(default_factory=list)  # лог инструментов
    summary: CallCase | None = None  # отчёт после звонка
    ended: bool = False

    @property
    def suggestion(self) -> SuggestionPayload | None:
        """Подсказка «что сказать» для крупного блока: последняя не-hangup."""
        return next((s for s in reversed(self.suggestions) if s.kind != "hangup"), None)

    @property
    def hangup(self) -> SuggestionPayload | None:
        """Безопасный выход («положите трубку…») — показывать всегда, когда он есть."""
        return next((s for s in reversed(self.suggestions) if s.kind == "hangup"), None)

    @property
    def verdict(self) -> Literal["scheme", "warning", "none"]:
        """Для цвета карточки: схема определена / есть тревожные признаки / ничего."""
        if self.scheme is not None:
            return "scheme"
        return "warning" if self.warnings else "none"


def call_view(events: list[AgentEvent]) -> CallView:
    """Сворачивает события звонка в состояние экрана. Событие с неожиданной формой пропускается."""
    view = CallView()
    for event in events:
        view.call_id = event.call_id
        try:
            payload = parse_payload(event)
        except ValidationError:
            continue
        match event.type:
            case EventType.call_started:
                view.phone, view.source = payload.phone, payload.source.model_dump()  # type: ignore[attr-defined]
            case EventType.transcript:
                view.transcript.append(Line(payload.speaker.value, payload.text, payload.t_start))  # type: ignore[attr-defined]
            case EventType.scheme_detected:
                view.scheme, view.stage = payload, None  # type: ignore[assignment]
                view.stages = list(payload.stages)  # type: ignore[attr-defined]
            case EventType.stage_changed:
                view.stage = payload  # type: ignore[assignment]
            case EventType.warning:
                view.warnings.append(payload.text)  # type: ignore[attr-defined]
            case EventType.suggestion:
                view.suggestions.append(payload)  # type: ignore[arg-type]
            case EventType.tool_call | EventType.tool_result:
                view.tools.append(payload)  # type: ignore[arg-type]
            case EventType.summary:
                view.summary = payload  # type: ignore[assignment]
            case EventType.call_ended:
                view.ended = True
    if view.scheme is not None and not view.stages:
        # Лог записан до 30.10, этапов в событии нет — берём их из схемы.
        scheme = get_scheme(view.scheme.scheme_id)
        view.stages = [StageInfo(n=s.n, name=s.name) for s in scheme.stages] if scheme else []
    return view


# --- Данные для экранов выбора звонка и истории ---


@dataclass(frozen=True)
class CallFile:
    """Звонок, который можно запустить из UI."""

    path: str  # относительно корня проекта — его и передавать в start_call
    title: str
    phone: str | None
    scheme_id: str | None  # None — звонок без мошенничества (для real-* схема известна из разметки)
    kind: Literal["audio", "transcript"]


def list_test_calls() -> list[CallFile]:
    """Тестовые звонки: записи data/calls/ (index.json) и реальные разговоры data/calls/real/ текстом."""
    calls = []
    for raw in json.loads((CALLS_DIR / "index.json").read_text(encoding="utf-8")):
        entry = CallIndexEntry.model_validate(raw)
        label = entry.scheme_id or "без мошенничества"
        calls.append(CallFile(f"data/calls/{entry.file}", f"{entry.file} — {label}", entry.phone, entry.scheme_id, "audio"))
    real = CALLS_DIR / "real" / "calls.jsonl"
    if real.exists():
        for line in real.read_text(encoding="utf-8").splitlines():
            c = json.loads(line)
            calls.append(CallFile(f"data/calls/real/{c['transcript']}", f"{c['call_id']} — {c['scheme_name']} (реальный)",
                                  None, c["scheme_id"], "transcript"))
    return calls


def get_scheme(scheme_id: str) -> Scheme | None:
    """Схема целиком (этапы, тактики затягивания) — для карточки схемы."""
    path = SCHEMES_DIR / f"{scheme_id}.json"
    return Scheme.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


def recent_cases(limit: int = 20) -> list[CallCase]:
    """Экран «История»: последние кейсы."""
    from storage import db  # noqa: PLC0415 — база нужна только экрану истории

    return db.list_recent_cases(limit)


def number_history(phone: str) -> tuple[NumberInfo, list[CallCase]]:
    """Экран «История»: поиск по номеру. Сырой номер («8 999 …») можно, неверный — ValueError."""
    from storage import db  # noqa: PLC0415

    return db.lookup_number(phone), db.get_cases_by_number(phone)
