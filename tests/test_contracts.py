from datetime import datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from contracts import (
    AgentEvent,
    AudioSource,
    CallCase,
    EventType,
    NumberInfo,
    Scheme,
    Speaker,
    Utterance,
)

STARTED_AT = datetime(2026, 10, 23, 12, 0)


def make_stage(n: int) -> dict[str, Any]:
    return {
        "n": n,
        "name": f"stage {n}",
        "goal": "gain trust",
        "typical_phrases": [f"phrase {i}" for i in range(5)],
        "red_flags": ["asks for SMS code"],
        "asks_for": ["SMS code"],
        "next_move": "move to the next stage",
    }


def make_scheme(**overrides: Any) -> dict[str, Any]:
    scheme = {
        "id": "safe-account",
        "name": "Safe account",
        "category": "bank",
        "legend": "bank security service",
        "description": "victim is persuaded to move money to a 'safe' account",
        "stages": [make_stage(n) for n in (1, 2, 3)],
        "pressure_points": ["urgency"],
        "stall_tactics": ["ask for the employee's full name"],
        "sources": ["https://example.org"],
    }
    return scheme | overrides


def make_case(**overrides: Any) -> dict[str, Any]:
    case = {
        "call_id": "c1",
        "phone": "+79991234567",
        "started_at": STARTED_AT,
        "ended_at": STARTED_AT + timedelta(minutes=5),
        "scheme_id": "safe-account",
        "max_stage": 2,
        "legend": "bank security service",
        "key_phrases": [],
        "summary": "",
        "transcript_path": "data/runs/c1.jsonl",
    }
    return case | overrides


class TestScheme:
    def test_valid_scheme(self) -> None:
        scheme = Scheme.model_validate(make_scheme())
        assert scheme.aliases == []
        assert [stage.n for stage in scheme.stages] == [1, 2, 3]

    @pytest.mark.parametrize("bad_id", ["Safe-Account", "safe_account", "safe--account", "-safe", ""])
    def test_id_must_be_slug(self, bad_id: str) -> None:
        with pytest.raises(ValidationError):
            Scheme.model_validate(make_scheme(id=bad_id))

    def test_unknown_category_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Scheme.model_validate(make_scheme(category="crypto"))

    @pytest.mark.parametrize("count", [2, 8])
    def test_stage_count_limits(self, count: int) -> None:
        stages = [make_stage(n) for n in range(1, count + 1)]
        with pytest.raises(ValidationError):
            Scheme.model_validate(make_scheme(stages=stages))

    @pytest.mark.parametrize("numbers", [(1, 3, 4), (2, 3, 4), (1, 1, 2), (2, 1, 3)])
    def test_stages_must_be_numbered_in_order(self, numbers: tuple[int, ...]) -> None:
        stages = [make_stage(n) for n in numbers]
        with pytest.raises(ValidationError, match="numbered"):
            Scheme.model_validate(make_scheme(stages=stages))

    def test_too_few_typical_phrases(self) -> None:
        stage = make_stage(1) | {"typical_phrases": ["only one"]}
        stages = [stage, make_stage(2), make_stage(3)]
        with pytest.raises(ValidationError):
            Scheme.model_validate(make_scheme(stages=stages))

    def test_typo_in_field_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Scheme.model_validate(make_scheme(stall_tactic=["typo"]))

    def test_blank_strings_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Scheme.model_validate(make_scheme(name="   "))


class TestUtterance:
    def test_valid_utterance(self) -> None:
        utterance = Utterance(
            call_id="c1", seq=0, speaker="caller", text="Здравствуйте", t_start=0.0, t_end=1.5
        )
        assert utterance.speaker is Speaker.caller

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(ValidationError, match="t_end"):
            Utterance(call_id="c1", seq=0, speaker="user", text="Алло", t_start=2.0, t_end=1.0)

    def test_is_immutable(self) -> None:
        utterance = Utterance(
            call_id="c1", seq=0, speaker="user", text="Алло", t_start=0.0, t_end=1.0
        )
        with pytest.raises(ValidationError):
            utterance.text = "changed"  # type: ignore[misc]


class TestAudioSource:
    def test_file_requires_path(self) -> None:
        with pytest.raises(ValidationError, match="path"):
            AudioSource(kind="file")

    def test_mic_without_path(self) -> None:
        assert AudioSource(kind="mic").path is None


class TestAgentEvent:
    def test_json_round_trip(self) -> None:
        event = AgentEvent(
            call_id="c1",
            ts=STARTED_AT,
            type=EventType.suggestion,
            payload={"text": "Уточните ФИО сотрудника", "kind": "stall"},
        )
        restored = AgentEvent.model_validate_json(event.model_dump_json())
        assert restored == event

    def test_type_serialized_as_plain_string(self) -> None:
        event = AgentEvent(call_id="c1", ts=STARTED_AT, type="call_ended", payload={})
        assert event.model_dump(mode="json")["type"] == "call_ended"

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentEvent(call_id="c1", ts=STARTED_AT, type="hangup", payload={})


class TestStorageModels:
    @pytest.mark.parametrize("bad_phone", ["89991234567", "+7 999 123-45-67", "+7999123456", "79991234567"])
    def test_phone_must_be_normalized(self, bad_phone: str) -> None:
        with pytest.raises(ValidationError):
            NumberInfo(
                phone=bad_phone, calls_count=0, first_seen=None, last_seen=None, schemes=[], tags=[]
            )

    def test_unknown_number(self) -> None:
        info = NumberInfo(
            phone="+79991234567", calls_count=0, first_seen=None, last_seen=None, schemes=[], tags=[]
        )
        assert info.calls_count == 0

    def test_valid_case(self) -> None:
        assert CallCase.model_validate(make_case()).max_stage == 2

    def test_case_without_scheme(self) -> None:
        case = CallCase.model_validate(make_case(scheme_id=None, max_stage=None, phone=None))
        assert case.scheme_id is None

    def test_case_ends_before_start_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ended_at"):
            CallCase.model_validate(make_case(ended_at=STARTED_AT - timedelta(seconds=1)))

    def test_max_stage_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            CallCase.model_validate(make_case(max_stage=0))
