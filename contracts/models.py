"""Data contracts shared by every module of the project.

Modules never talk to each other directly: audio, agent, storage and UI only
exchange the models defined here. Any change to this file affects the whole
team, so new fields must come with defaults and old fields must not be removed
silently.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MIN_STAGES = 3
MAX_STAGES = 7
MIN_TYPICAL_PHRASES = 5
MAX_TYPICAL_PHRASES = 10

SchemeId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
Phone = Annotated[str, StringConstraints(pattern=r"^\+7\d{10}$")]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Contract(BaseModel):
    """Base for all contracts.

    `extra="forbid"` turns a typo in a hand-written scheme JSON into an error
    instead of a silently ignored field. `frozen=True` keeps messages immutable
    once they are passed between modules; use `model_copy(update=...)` instead.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class SchemeCategory(StrEnum):
    bank = "bank"
    gosuslugi = "gosuslugi"
    relative = "relative"
    investment = "investment"
    delivery = "delivery"
    other = "other"


class Stage(Contract):
    n: int = Field(ge=1)
    name: NonEmptyStr
    goal: NonEmptyStr
    typical_phrases: list[NonEmptyStr] = Field(
        min_length=MIN_TYPICAL_PHRASES, max_length=MAX_TYPICAL_PHRASES
    )
    red_flags: list[NonEmptyStr]
    asks_for: list[NonEmptyStr]
    next_move: NonEmptyStr


class Scheme(Contract):
    id: SchemeId
    name: NonEmptyStr
    aliases: list[NonEmptyStr] = []
    category: SchemeCategory
    legend: NonEmptyStr
    description: NonEmptyStr
    stages: list[Stage] = Field(min_length=MIN_STAGES, max_length=MAX_STAGES)
    pressure_points: list[NonEmptyStr]
    stall_tactics: list[NonEmptyStr]
    sources: list[NonEmptyStr]

    @model_validator(mode="after")
    def _stages_numbered_in_order(self) -> Self:
        # Retrieval stores stage_n as chunk metadata, so gaps or duplicates
        # would make "stage 3" point to the wrong chunk.
        numbers = [stage.n for stage in self.stages]
        expected = list(range(1, len(self.stages) + 1))
        if numbers != expected:
            raise ValueError(f"stages must be numbered {expected}, got {numbers}")
        return self


class Speaker(StrEnum):
    caller = "caller"
    user = "user"
    unknown = "unknown"


class Utterance(Contract):
    call_id: NonEmptyStr
    seq: int = Field(ge=0)
    speaker: Speaker
    text: NonEmptyStr
    t_start: float = Field(ge=0)
    t_end: float = Field(ge=0)

    @model_validator(mode="after")
    def _ends_after_start(self) -> Self:
        if self.t_end < self.t_start:
            raise ValueError(f"t_end ({self.t_end}) is before t_start ({self.t_start})")
        return self


class AudioSource(Contract):
    kind: Literal["file", "mic", "loopback"]
    path: str | None = None

    @model_validator(mode="after")
    def _file_needs_path(self) -> Self:
        if self.kind == "file" and not self.path:
            raise ValueError("audio source of kind 'file' requires a path")
        return self


class EventType(StrEnum):
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
    """One step of the agent, streamed to the UI and logged to data/runs/.

    Payload shape per event type is documented in CLAUDE.md.
    """

    call_id: NonEmptyStr
    ts: datetime
    type: EventType
    payload: dict[str, Any]


class NumberInfo(Contract):
    phone: Phone
    calls_count: int = Field(ge=0)
    first_seen: datetime | None
    last_seen: datetime | None
    schemes: list[SchemeId]
    tags: list[NonEmptyStr]


class CallCase(Contract):
    call_id: NonEmptyStr
    phone: Phone | None
    started_at: datetime
    ended_at: datetime | None
    scheme_id: SchemeId | None
    max_stage: Annotated[int, Field(ge=1)] | None
    legend: str | None
    key_phrases: list[str]
    summary: str
    transcript_path: str

    @model_validator(mode="after")
    def _ends_after_start(self) -> Self:
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at is before started_at")
        return self


class CallRequest(Contract):
    """What starts a call: where the audio comes from and who is calling."""

    call_id: NonEmptyStr
    source: AudioSource
    phone: Phone | None = None


class CallIndexEntry(Contract):
    """One record of data/calls/index.json, paths relative to data/calls/.

    In a stereo file the left channel is the caller and the right one is the user.
    """

    file: NonEmptyStr
    phone: Phone | None
    scheme_id: SchemeId | None  # None for a call without fraud
    channels: Literal["stereo", "mono"]
    transcript: NonEmptyStr
