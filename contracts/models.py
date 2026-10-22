"""Контракты данных между модулями (ТЗ, раздел «Контракты данных»).

Меняет только Александр. Текст моделей перенесён из ТЗ v2 один к одному.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, model_validator


# 1. Схема развода — data/schemes/<id>.json

class Stage(BaseModel):
    n: int                       # номер этапа, с 1
    name: str                    # «Звонок от службы безопасности банка»
    goal: str                    # чего мошенник добивается на этапе
    typical_phrases: list[str]   # 5–10 реальных фраз мошенника
    red_flags: list[str]         # признаки, по которым узнаётся этап
    asks_for: list[str]          # что просит: код из СМС, перевод, данные карты
    next_move: str               # что обычно происходит дальше


class Scheme(BaseModel):
    id: str                      # slug латиницей: "safe-account"
    name: str                    # «Перевод на безопасный счёт»
    aliases: list[str] = []      # другие названия схемы
    category: str                # bank | gosuslugi | relative | investment | delivery | other
    legend: str                  # кем представляются
    description: str             # 3–5 предложений своими словами
    stages: list[Stage]          # 3–7 этапов по порядку
    pressure_points: list[str]   # срочность, страх, авторитет и т.п.
    stall_tactics: list[str]     # чем тянуть время именно на этой схеме
    sources: list[str]           # ссылки на источники


# 2. Реплика из звука — Utterance

class Speaker(str, Enum):
    caller = "caller"            # мошенник
    user = "user"                # мы
    unknown = "unknown"          # если каналы не разделены


class Utterance(BaseModel):
    call_id: str
    seq: int                     # порядковый номер реплики в звонке, с 0
    speaker: Speaker
    text: str
    t_start: float               # секунды от начала звонка
    t_end: float


class AudioSource(BaseModel):
    kind: Literal["file", "mic", "loopback"]
    path: str | None = None      # для kind="file"


# 4. Хранилище — storage/db.py

class NumberInfo(BaseModel):
    phone: str
    calls_count: int
    first_seen: datetime | None
    last_seen: datetime | None
    schemes: list[str]           # id схем, с которыми звонили с номера
    tags: list[str]


class CallCase(BaseModel):
    call_id: str
    phone: str | None
    started_at: datetime
    ended_at: datetime | None
    scheme_id: str | None
    max_stage: int | None        # до какого этапа дошёл мошенник
    legend: str | None           # «сотрудник ЦБ Иван Петров»
    key_phrases: list[str]
    summary: str
    transcript_path: str


# 3. События агента — AgentEvent

class CallStartedPayload(BaseModel):
    phone: str | None            # None — номер неизвестен
    source: AudioSource


class SchemeDetectedPayload(BaseModel):
    verdict: Literal["scheme", "unclear", "no_fraud"]
    scheme_id: str | None        # None, если verdict != "scheme"
    scheme_name: str | None
    confidence: float            # 0–1
    evidence: list[str]          # фразы-доказательства


class StageChangedPayload(BaseModel):
    scheme_id: str
    stage_n: int
    stage_name: str
    next_move: str


class SuggestionPayload(BaseModel):
    text: str
    kind: Literal["stall", "question", "hangup"]


class WarningPayload(BaseModel):
    text: str


class ToolCallPayload(BaseModel):
    tool: str
    args: dict


class ToolResultPayload(BaseModel):
    tool: str
    ok: bool
    result: Any


class EmptyPayload(BaseModel):
    pass


EventType = Literal["call_started", "transcript", "scheme_detected", "stage_changed",
                    "suggestion", "warning", "tool_call", "tool_result",
                    "summary", "call_ended"]

PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "call_started": CallStartedPayload, "transcript": Utterance,
    "scheme_detected": SchemeDetectedPayload, "stage_changed": StageChangedPayload,
    "suggestion": SuggestionPayload, "warning": WarningPayload,
    "tool_call": ToolCallPayload, "tool_result": ToolResultPayload,
    "summary": CallCase, "call_ended": EmptyPayload,
}


class AgentEvent(BaseModel):
    call_id: str
    seq: int                     # порядковый номер события в звонке, с 0
    ts: datetime
    type: EventType
    payload: dict

    @model_validator(mode="after")
    def check_payload(self):     # payload обязан соответствовать type
        PAYLOAD_MODELS[self.type].model_validate(self.payload)
        return self


# 7. Запуск звонка и описание записей — CallRequest, data/calls/index.json

class CallRequest(BaseModel):
    call_id: str
    source: AudioSource
    phone: str | None = None     # номер звонящего; None — номер неизвестен


class CallIndexEntry(BaseModel):
    """Одна запись data/calls/index.json."""
    file: str                    # "001.wav"
    phone: str | None
    scheme_id: str | None        # None — звонок без мошенничества
    channels: Literal["stereo", "mono"]
    transcript: str              # "001.txt"
