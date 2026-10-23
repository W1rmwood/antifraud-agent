"""LLM stand-in: answers are produced by a function of the prompt, no model is run."""

from collections.abc import Callable

from pydantic import BaseModel

from agent.llm import LLMError, Messages
from agent.prompts import Analysis, CallSummary

AnalysisRule = Callable[[str], Analysis]


class FakeLLM:
    """`analyze` maps the user prompt to an Analysis; raising LLMError imitates a dead model.

    `summary=None` makes the summary call fail, to exercise the fallback.
    """

    def __init__(self, analyze: AnalysisRule, summary: CallSummary | None = None) -> None:
        self._analyze = analyze
        self._summary = summary
        self.prompts: list[str] = []

    async def generate[T: BaseModel](self, messages: Messages, schema: type[T]) -> T:
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
