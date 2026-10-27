"""data/mock_events/example.jsonl is the reference call for the UI, so it must follow the contract."""

from pathlib import Path

import pytest

from typing import get_args

from contracts import AgentEvent, CallCase, EventType, NumberInfo, Scheme, Utterance
from contracts.models import SuggestionKind

EXAMPLE = Path("data/mock_events/example.jsonl")

# Payload keys per event type, as documented in CLAUDE.md.
PAYLOAD_KEYS: dict[EventType, set[str]] = {
    EventType.call_started: {"phone", "source"},
    EventType.scheme_detected: {"scheme_id", "scheme_name", "confidence", "evidence"},
    EventType.stage_changed: {"scheme_id", "stage_n", "stage_name", "next_move"},
    EventType.suggestion: {"text", "kind"},
    EventType.warning: {"text"},
    EventType.tool_call: {"tool", "args"},
    EventType.tool_result: {"tool", "ok", "result"},
    EventType.call_ended: set(),
}
# Keys added later with backward compatibility: they may be missing in older logs.
OPTIONAL_KEYS: dict[EventType, set[str]] = {EventType.suggestion: {"tactic"}}


def assert_payload_keys(event: AgentEvent) -> None:
    keys, required = set(event.payload), PAYLOAD_KEYS[event.type]
    assert required <= keys <= required | OPTIONAL_KEYS.get(event.type, set()), event


@pytest.fixture(scope="module")
def events() -> list[AgentEvent]:
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    return [AgentEvent.model_validate_json(line) for line in lines]


def test_covers_all_event_types(events: list[AgentEvent]) -> None:
    assert {e.type for e in events} == set(EventType)


def test_single_call_in_time_order(events: list[AgentEvent]) -> None:
    assert len({e.call_id for e in events}) == 1
    assert events[0].type == EventType.call_started
    assert events[-1].type == EventType.call_ended
    assert [e.ts for e in events] == sorted(e.ts for e in events)


def test_payloads_match_contract(events: list[AgentEvent]) -> None:
    for e in events:
        if e.type == EventType.transcript:
            Utterance.model_validate(e.payload)
        elif e.type == EventType.summary:
            CallCase.model_validate(e.payload)
        else:
            assert_payload_keys(e)
        if e.type == EventType.suggestion:
            assert e.payload["kind"] in get_args(SuggestionKind)


def test_transcript_seq_is_continuous(events: list[AgentEvent]) -> None:
    seqs = [e.payload["seq"] for e in events if e.type == EventType.transcript]
    assert seqs == list(range(len(seqs)))


def test_refers_to_real_scheme_and_stages(events: list[AgentEvent]) -> None:
    detected = next(e for e in events if e.type == EventType.scheme_detected)
    scheme_id = detected.payload["scheme_id"]
    scheme = Scheme.model_validate_json(
        Path(f"data/schemes/{scheme_id}.json").read_text(encoding="utf-8")
    )
    assert detected.payload["scheme_name"] == scheme.name
    stages = {s.n: s for s in scheme.stages}
    for e in events:
        if e.type == EventType.stage_changed:
            stage = stages[e.payload["stage_n"]]
            assert e.payload["stage_name"] == stage.name
            assert e.payload["next_move"] == stage.next_move


def test_lookup_number_result_is_number_info(events: list[AgentEvent]) -> None:
    for e in events:
        if e.type == EventType.tool_result and e.payload["tool"] == "lookup_number":
            NumberInfo.model_validate(e.payload["result"])
