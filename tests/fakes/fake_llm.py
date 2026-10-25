"""Подмена LLM: ответы вычисляет функция от промпта, модель не запускается."""

from collections.abc import Callable

from pydantic import BaseModel

from agent.llm import LLMError, Messages
from agent.prompts import Analysis, CallSummary

# Правило фейка: по тексту промпта решает, что «ответила бы» модель.
AnalysisRule = Callable[[str], Analysis]


class FakeLLM:
    """`analyze` превращает промпт в Analysis; если оно бросает LLMError — это «упавшая» модель.

    `summary=None` заставляет вызов отчёта упасть, чтобы проверить запасной вариант.
    """

    def __init__(self, analyze: AnalysisRule, summary: CallSummary | None = None) -> None:
        self._analyze = analyze
        self._summary = summary
        self.prompts: list[str] = []  # все полученные промпты: тесты проверяют, что в них попало

    async def generate[T: BaseModel](self, messages: Messages, schema: type[T]) -> T:
        """Тот же интерфейс, что у OllamaLLM. Отвечает по типу запрошенной схемы."""
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if schema is Analysis:
            return self._analyze(prompt)  # type: ignore[return-value]
        if schema is CallSummary:
            if self._summary is None:
                raise LLMError("fake summary failure")
            return self._summary  # type: ignore[return-value]
        raise NotImplementedError(schema)


def no_fraud(prompt: str) -> Analysis:
    """Правило «обычный звонок»: схемы нет, уверенность низкая."""
    return Analysis(
        scheme_id=None,
        stage_n=None,
        confidence=0.1,
        hard_red_flags=[],
        evidence=[],
        suggestion="",
        suggestion_kind="question",
        warning=None,
    )
