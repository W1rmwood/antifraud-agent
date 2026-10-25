"""Формат data/eval/cases.jsonl: фрагмент звонка и что агент должен в нём найти."""

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import Speaker
from contracts.models import NonEmptyStr, SchemeId

CASES_PATH = "data/eval/cases.jsonl"


class EvalUtterance(BaseModel):
    """Одна реплика фрагмента. Без времени и seq: их подставляет eval при прогоне."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    speaker: Speaker
    text: NonEmptyStr


class EvalCase(BaseModel):
    """Один тестовый фрагмент звонка с правильным ответом."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyStr
    utterances: list[EvalUtterance] = Field(min_length=1)
    expected_scheme: SchemeId | None  # None — обычный звонок, агент не должен поднимать тревогу
    expected_stage: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _stage_only_with_scheme(self) -> Self:
        """Этап указан тогда и только тогда, когда указана схема."""
        if (self.expected_scheme is None) != (self.expected_stage is None):
            raise ValueError("expected_stage must be set exactly when expected_scheme is set")
        return self

    @property
    def is_fraud(self) -> bool:
        """Мошеннический ли это фрагмент."""
        return self.expected_scheme is not None


def load_cases(path: str | Path = CASES_PATH) -> list[EvalCase]:
    """Читает набор построчно, пропуская пустые строки, и проверяет каждую запись."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [EvalCase.model_validate_json(line) for line in lines if line.strip()]
