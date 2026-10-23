"""Fakes must behave like the real modules, otherwise tests on fakes prove nothing."""

from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from contracts import AudioSource, CallCase, Speaker, Storage
from storage import db
from tests.fakes import FakeListener, InMemoryStorage, parse_transcript

STARTED_AT = datetime(2026, 10, 20, 12, 0)


def make_case(call_id: str, phone: str | None, days_ago: int, scheme_id: str | None) -> CallCase:
    started = STARTED_AT - timedelta(days=days_ago)
    return CallCase(
        call_id=call_id,
        phone=phone,
        started_at=started,
        ended_at=started + timedelta(minutes=3),
        scheme_id=scheme_id,
        max_stage=2 if scheme_id else None,
        legend=None,
        key_phrases=[],
        summary="test",
        transcript_path=f"data/runs/{call_id}.jsonl",
    )


@pytest.fixture(params=["fake", "sqlite"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> Storage | ModuleType:
    impl: Storage | ModuleType = InMemoryStorage() if request.param == "fake" else db
    impl.init_db(str(tmp_path / "test.db"))
    return impl


def test_unknown_number_has_no_history(storage: Storage) -> None:
    info = storage.lookup_number("+79990000000")
    assert info.calls_count == 0
    assert info.first_seen is None and info.schemes == [] and info.tags == []


def test_history_of_number(storage: Storage) -> None:
    phone = "+79991234567"
    storage.save_case(make_case("c1", phone, days_ago=5, scheme_id="safe-account"))
    storage.save_case(make_case("c2", phone, days_ago=1, scheme_id="gosuslugi"))
    storage.save_case(make_case("c3", phone, days_ago=3, scheme_id="safe-account"))
    storage.save_case(make_case("c4", "+79990000001", days_ago=0, scheme_id=None))

    info = storage.lookup_number("8 (999) 123-45-67")
    assert info.calls_count == 3
    assert info.schemes == ["safe-account", "gosuslugi"]
    assert info.first_seen == STARTED_AT - timedelta(days=5)
    assert info.last_seen == STARTED_AT - timedelta(days=1)
    assert [c.call_id for c in storage.get_cases_by_number(phone)] == ["c2", "c3", "c1"]
    assert [c.call_id for c in storage.list_recent_cases(limit=2)] == ["c4", "c2"]


def test_save_same_call_replaces_case(storage: Storage) -> None:
    storage.save_case(make_case("c1", "+79991234567", days_ago=0, scheme_id=None))
    assert storage.save_case(make_case("c1", "+79991234567", days_ago=0, scheme_id="gosuslugi")) == "c1"
    cases = storage.get_cases_by_number("+79991234567")
    assert [c.scheme_id for c in cases] == ["gosuslugi"]


def test_tags_are_unique(storage: Storage) -> None:
    storage.tag_number("+79991234567", "fraud")
    storage.tag_number("89991234567", "fraud")
    assert storage.lookup_number("+79991234567").tags == ["fraud"]
    with pytest.raises(ValueError):
        storage.tag_number("+79991234567", "  ")


def test_foreign_number_is_rejected(storage: Storage) -> None:
    with pytest.raises(ValueError):
        storage.lookup_number("+380441234567")


def test_parse_transcript() -> None:
    lines = parse_transcript("caller: Алло\n\nUSER :  Да? \n")
    assert lines == [(Speaker.caller, "Алло"), (Speaker.user, "Да?")]
    with pytest.raises(ValueError, match="line 1"):
        parse_transcript("просто текст")


async def test_fake_listener_replays_call_file() -> None:
    path = Path("data/calls/001.txt")
    expected = parse_transcript(path.read_text(encoding="utf-8"))
    source = AudioSource(kind="file", path=str(path))

    utterances = [u async for u in FakeListener()(source, "call-1")]

    assert [(u.speaker, u.text) for u in utterances] == expected
    assert [u.seq for u in utterances] == list(range(len(expected)))
    assert all(u.call_id == "call-1" for u in utterances)
    for prev, cur in zip(utterances, utterances[1:]):
        assert prev.t_end <= cur.t_start


async def test_fake_listener_from_lines() -> None:
    listener = FakeListener([(Speaker.caller, "Это служба безопасности банка")])
    utterances = [u async for u in listener(AudioSource(kind="mic"), "call-2")]
    assert len(utterances) == 1 and utterances[0].t_end > 0
