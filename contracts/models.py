"""Контракты данных, общие для всех модулей проекта.

Модули не обращаются друг к другу напрямую: звук, агент, хранилище и UI
обмениваются только моделями из этого файла. Любое изменение здесь затрагивает
всю команду, поэтому новые поля добавляются только со значением по умолчанию,
а старые поля молча не удаляются.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Границы размеров схемы: меньше трёх этапов — это не схема, а одна фраза;
# больше семи — схему стоит разбить на две.
MIN_STAGES = 3
MAX_STAGES = 7
MIN_TYPICAL_PHRASES = 5
MAX_TYPICAL_PHRASES = 10

# slug латиницей через дефис: "safe-account". Он же имя файла data/schemes/<id>.json.
SchemeId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
# Только нормализованный российский номер. Приводить к этому виду — задача того,
# кто создаёт модель (storage/db.py: normalize_phone), а не самой модели.
Phone = Annotated[str, StringConstraints(pattern=r"^\+7\d{10}$")]
# Строка, в которой после обрезки пробелов осталось хоть что-то.
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Contract(BaseModel):
    """Базовый класс всех контрактов.

    `extra="forbid"` превращает опечатку в имени поля в рукописном JSON схемы
    в ошибку, а не в молча проигнорированное поле. `frozen=True` делает
    сообщения неизменяемыми после передачи между модулями: чтобы получить
    изменённую копию, используй `model_copy(update=...)`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class SchemeCategory(StrEnum):
    """Категория схемы: под кого маскируется мошенник."""

    bank = "bank"
    gosuslugi = "gosuslugi"
    relative = "relative"
    investment = "investment"
    delivery = "delivery"
    other = "other"


class Stage(Contract):
    """Один этап схемы развода. Он же единица поиска в RAG (один чанк)."""

    n: int = Field(ge=1)  # номер этапа, с 1
    name: NonEmptyStr
    goal: NonEmptyStr  # чего мошенник добивается на этапе
    typical_phrases: list[NonEmptyStr] = Field(
        min_length=MIN_TYPICAL_PHRASES, max_length=MAX_TYPICAL_PHRASES
    )
    red_flags: list[NonEmptyStr]
    asks_for: list[NonEmptyStr]  # что просят: код из СМС, перевод, данные карты
    next_move: NonEmptyStr  # что мошенник сделает дальше


class Scheme(Contract):
    """Схема развода целиком: легенда, этапы и как тянуть время. Файл data/schemes/<id>.json."""

    id: SchemeId
    name: NonEmptyStr
    aliases: list[NonEmptyStr] = []
    category: SchemeCategory
    legend: NonEmptyStr  # кем представляются
    description: NonEmptyStr
    stages: list[Stage] = Field(min_length=MIN_STAGES, max_length=MAX_STAGES)
    pressure_points: list[NonEmptyStr]
    stall_tactics: list[NonEmptyStr]
    sources: list[NonEmptyStr]

    @model_validator(mode="after")
    def _stages_numbered_in_order(self) -> Self:
        """Этапы должны идти подряд: 1, 2, 3, ... без пропусков и повторов."""
        # Поиск хранит stage_n в метаданных чанка, и агент берёт этап как
        # stages[stage_n - 1]. Пропуск или дубль номера увёл бы «этап 3»
        # не на тот чанк.
        numbers = [stage.n for stage in self.stages]
        expected = list(range(1, len(self.stages) + 1))
        if numbers != expected:
            raise ValueError(f"stages must be numbered {expected}, got {numbers}")
        return self


class Speaker(StrEnum):
    """Кто говорит. В стерео левый канал — звонящий (caller), правый — абонент (user)."""

    caller = "caller"
    user = "user"
    unknown = "unknown"  # моно-запись: голоса не различить


class Utterance(Contract):
    """Одна фраза из звонка, уже распознанная в текст. Звук до агента не доходит."""

    call_id: NonEmptyStr
    seq: int = Field(ge=0)  # порядковый номер фразы в звонке, с 0
    speaker: Speaker
    text: NonEmptyStr
    t_start: float = Field(ge=0)  # секунды от начала звонка
    t_end: float = Field(ge=0)

    @model_validator(mode="after")
    def _ends_after_start(self) -> Self:
        """Фраза не может закончиться раньше, чем началась."""
        if self.t_end < self.t_start:
            raise ValueError(f"t_end ({self.t_end}) is before t_start ({self.t_start})")
        return self


class AudioSource(Contract):
    """Откуда брать звук: файл, микрофон или системный звук (loopback)."""

    kind: Literal["file", "mic", "loopback"]
    path: str | None = None

    @model_validator(mode="after")
    def _file_needs_path(self) -> Self:
        """Для файла путь обязателен, для микрофона и loopback он не нужен."""
        if self.kind == "file" and not self.path:
            raise ValueError("audio source of kind 'file' requires a path")
        return self


class EventType(StrEnum):
    """Типы событий агента. Что лежит в payload каждого типа — в таблице в CLAUDE.md."""

    call_started = "call_started"
    transcript = "transcript"
    scheme_detected = "scheme_detected"
    stage_changed = "stage_changed"
    suggestion = "suggestion"
    warning = "warning"
    tool_call = "tool_call"
    tool_result = "tool_result"
    summary = "summary"
    call_ended = "call_ended"


class AgentEvent(Contract):
    """Один шаг агента. Уходит в UI и пишется в лог data/runs/<call_id>.jsonl.

    Форма payload для каждого типа события описана в CLAUDE.md.
    """

    call_id: NonEmptyStr
    ts: datetime
    type: EventType
    # Намеренно просто словарь: форма зависит от type, а строгая модель на
    # каждый тип пока не нужна. Проверяет её tests/test_mock_events.py.
    payload: dict[str, Any]


class NumberInfo(Contract):
    """История номера из хранилища. Неизвестный номер — calls_count=0, а не ошибка."""

    phone: Phone
    calls_count: int = Field(ge=0)
    first_seen: datetime | None
    last_seen: datetime | None
    schemes: list[SchemeId]  # схемы прошлых звонков с этого номера
    tags: list[NonEmptyStr]


class CallCase(Contract):
    """Отчёт о завершённом звонке. Агент сохраняет его через MCP-инструмент save_case."""

    call_id: NonEmptyStr
    phone: Phone | None
    started_at: datetime
    ended_at: datetime | None
    scheme_id: SchemeId | None  # None — схема не найдена
    max_stage: Annotated[int, Field(ge=1)] | None  # самый дальний этап, до которого дошёл мошенник
    legend: str | None
    key_phrases: list[str]
    summary: str
    transcript_path: str

    @model_validator(mode="after")
    def _ends_after_start(self) -> Self:
        """Звонок не может закончиться раньше, чем начался."""
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at is before started_at")
        return self


class CallRequest(Contract):
    """С чего начинается звонок: откуда звук и кто звонит."""

    call_id: NonEmptyStr
    source: AudioSource
    phone: Phone | None = None


class CallIndexEntry(Contract):
    """Одна запись data/calls/index.json. Пути — относительно data/calls/.

    В стерео-файле левый канал — звонящий, правый — абонент.
    """

    file: NonEmptyStr
    phone: Phone | None
    scheme_id: SchemeId | None  # None — звонок без мошенничества
    channels: Literal["stereo", "mono"]
    transcript: NonEmptyStr
