import pytest

from agent.policy import decide, quote_is_said
from agent.prompts import Analysis

CALLER = "Служба безопасности. Вам придёт смс, продиктуйте мне код из сообщения."
CANDIDATES = {"bank-security-service", "safe-account"}


def analysis(
    scheme_id: str | None = "bank-security-service",
    stage_n: int | None = 3,
    confidence: float = 0.9,
    flags: list[str] | None = None,
) -> Analysis:
    return Analysis(
        scheme_id=scheme_id,
        stage_n=stage_n,
        confidence=confidence,
        hard_red_flags=["продиктуйте мне код из сообщения"] if flags is None else flags,
        evidence=[],
        suggestion="Подождите, телефон разряжается.",
        suggestion_kind="stall",
        warning=None,
    )


@pytest.mark.parametrize(
    "quote, said",
    [
        ("продиктуйте мне код из сообщения", True),
        ("Продиктуйте код из сообщений!", True),  # other word forms and case
        ("переведите деньги на безопасный счёт", False),  # never said
        ("", False),
    ],
)
def test_quote_is_said(quote: str, said: bool) -> None:
    assert quote_is_said(quote, CALLER) is said


def test_declares_with_confidence_and_said_flag() -> None:
    decision = decide(analysis(), CALLER, CANDIDATES, threshold=0.6)
    assert decision.declared
    assert (decision.scheme_id, decision.stage_n) == ("bank-security-service", 3)
    assert decision.flags == ["продиктуйте мне код из сообщения"]


@pytest.mark.parametrize(
    "result",
    [
        analysis(confidence=0.5),  # below threshold
        analysis(flags=[]),  # no hard red flag
        analysis(flags=["переведите деньги на безопасный счёт"]),  # hallucinated flag
        analysis(scheme_id="gosuslugi"),  # not among retrieved candidates
        analysis(scheme_id=None, stage_n=None),
    ],
)
def test_no_alarm_without_all_conditions(result: Analysis) -> None:
    assert not decide(result, CALLER, CANDIDATES, threshold=0.6).declared


def test_declared_scheme_follows_stages_without_new_flags() -> None:
    decision = decide(
        analysis(stage_n=2, confidence=0.3, flags=[]),
        "Назовите, пожалуйста, ваше имя.",
        set(),
        threshold=0.6,
        current="bank-security-service",
        current_stage=3,
    )
    assert (decision.scheme_id, decision.stage_n) == ("bank-security-service", 2)


def test_unsure_answer_keeps_declared_scheme() -> None:
    decision = decide(
        analysis(scheme_id=None, stage_n=None, confidence=0.1, flags=[]),
        CALLER,
        CANDIDATES,
        threshold=0.6,
        current="safe-account",
        current_stage=2,
    )
    assert (decision.scheme_id, decision.stage_n) == ("safe-account", 2)


def test_switches_scheme_only_when_new_one_qualifies() -> None:
    decision = decide(analysis(), CALLER, CANDIDATES, threshold=0.6, current="safe-account", current_stage=1)
    assert decision.scheme_id == "bank-security-service"
