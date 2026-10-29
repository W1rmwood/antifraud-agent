from pathlib import Path

import pytest

from agent.policy import decide, hangup_text, provoke_mode, quote_is_said
from agent.prompts import Analysis
from contracts import Scheme

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


def test_switches_scheme_when_clearly_more_confident() -> None:
    decision = decide(
        analysis(confidence=0.9), CALLER, CANDIDATES, threshold=0.6,
        current="safe-account", current_stage=1, current_confidence=0.7,
    )
    assert decision.scheme_id == "bank-security-service"
    assert decision.challenger is None


def test_does_not_switch_on_one_win_within_margin() -> None:
    decision = decide(
        analysis(confidence=0.95), CALLER, CANDIDATES, threshold=0.6,
        current="safe-account", current_stage=1, current_confidence=0.9,
    )
    assert (decision.scheme_id, decision.stage_n) == ("safe-account", 1)
    assert decision.challenger == "bank-security-service"
    assert decision.flags == ["продиктуйте мне код из сообщения"]  # предупреждение всё равно будет


def test_switches_on_second_consecutive_win() -> None:
    decision = decide(
        analysis(confidence=0.95), CALLER, CANDIDATES, threshold=0.6,
        current="safe-account", current_stage=1, current_confidence=0.9,
        challenger="bank-security-service",
    )
    assert (decision.scheme_id, decision.stage_n) == ("bank-security-service", 3)
    assert decision.challenger is None


def test_different_challenger_does_not_count_as_second_win() -> None:
    decision = decide(
        analysis(confidence=0.95), CALLER, CANDIDATES, threshold=0.6,
        current="safe-account", current_stage=1, current_confidence=0.9,
        challenger="gosuslugi",
    )
    assert decision.scheme_id == "safe-account"
    assert decision.challenger == "bank-security-service"


def test_weak_answer_breaks_challenger_streak() -> None:
    decision = decide(
        analysis(confidence=0.3), CALLER, CANDIDATES, threshold=0.6,
        current="safe-account", current_stage=1, current_confidence=0.9,
        challenger="bank-security-service",
    )
    assert decision.scheme_id == "safe-account"
    assert decision.challenger is None


@pytest.mark.parametrize(
    "category, phrase",
    [
        ("bank", "по номеру с обратной стороны карты"),
        ("relative", "на его обычный номер"),
        ("other", "по номеру с официального сайта"),
    ],
)
def test_hangup_text_depends_on_category(category: str, phrase: str) -> None:
    scheme = Scheme.model_validate_json(Path("data/schemes/safe-account.json").read_text(encoding="utf-8"))
    text = hangup_text(scheme.model_copy(update={"category": category}))
    assert text.startswith("Положите трубку") and phrase in text


@pytest.mark.parametrize(
    "stage_n, n_stages, provoke",
    [
        (None, 4, False),  # stage unknown: stay safe and keep baiting
        (1, 4, False),
        (2, 4, False),
        (3, 4, True),  # second half of the scheme, close to the money
        (4, 4, True),
        (2, 3, True),
    ],
)
def test_provoke_only_in_second_half(stage_n: int | None, n_stages: int, provoke: bool) -> None:
    assert provoke_mode(stage_n, n_stages) is provoke
