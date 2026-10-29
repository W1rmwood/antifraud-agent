"""Фасад для UI (agent/ui_api.py) и проигрыватель (agent/replay.py) — без модели."""

import time
from pathlib import Path

import pytest

from agent import ui_api
from agent.replay import replay
from agent.ui_api import EventTail, call_view, list_test_calls, read_events, start_replay
from contracts import EventType, parse_payload

EXAMPLE = Path("data/mock_events/example.jsonl")


def test_event_tail_waits_for_file_and_keeps_half_written_line(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    tail = EventTail(path)
    assert tail.read_new() == []  # процесс ещё не создал лог

    lines = EXAMPLE.read_bytes().splitlines(keepends=True)
    path.write_bytes(lines[0] + lines[1][:20])  # вторая строка дописана наполовину
    assert [e.type for e in tail.read_new()] == [EventType.call_started]
    with path.open("ab") as f:
        f.write(lines[1][20:])
    assert len(tail.read_new()) == 1 and len(tail.events) == 2
    assert not tail.finished


def test_event_tail_restarts_when_log_is_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    path.write_bytes(EXAMPLE.read_bytes())
    tail = EventTail(path)
    tail.read_new()
    assert tail.finished
    path.write_bytes(EXAMPLE.read_bytes().splitlines(keepends=True)[0])  # тот же call_id запустили заново
    tail.read_new()
    assert len(tail.events) == 1 and not tail.finished


def test_call_view_builds_the_live_call_screen() -> None:
    view = call_view(read_events(EXAMPLE))
    assert view.ended and view.summary is not None
    assert view.phone == "+79990000001"
    assert view.scheme is not None and view.scheme.scheme_id == "safe-account"
    # В старом логе этапов в событии нет — фасад берёт их из схемы, чтобы UI нарисовал прогресс.
    assert [s.n for s in view.stages] == list(range(1, len(view.stages) + 1)) and view.stages
    assert view.stage is not None and view.stage.stage_n >= 1
    assert view.transcript and view.warnings and view.tools
    assert view.verdict == "scheme"
    assert view.suggestion is None or view.suggestion.kind != "hangup"


def test_call_view_of_an_empty_call() -> None:
    view = call_view([])
    assert view.verdict == "none" and not view.ended and view.suggestion is None and view.hangup is None


def test_every_mock_event_file_follows_the_contract() -> None:
    for path in sorted(Path("data/mock_events").glob("*.jsonl")):
        events = read_events(path)
        assert events, path
        for event in events:
            parse_payload(event)  # бросит ValidationError, если форма не та
        assert events[0].type == EventType.call_started and events[-1].type == EventType.call_ended, path


def test_list_test_calls_points_to_real_files() -> None:
    calls = list_test_calls()
    assert len(calls) >= 12
    assert any(c.scheme_id is None for c in calls)
    for c in calls:
        assert (ui_api.ROOT / c.path).exists(), c.path


async def test_replay_rewrites_ids_and_keeps_pauses(tmp_path: Path) -> None:
    started = time.monotonic()
    count = await replay(EXAMPLE, "r1", speed=1000, runs_dir=tmp_path)
    events = read_events(tmp_path / "r1.jsonl")
    assert count == len(events) == len(read_events(EXAMPLE))
    assert {e.call_id for e in events} == {"r1"}
    assert [e.seq for e in events] == list(range(len(events)))
    assert time.monotonic() - started < 5


def test_start_replay_runs_as_a_process_and_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui_api, "RUNS_DIR", tmp_path)
    call = start_replay(EXAMPLE, call_id="r2", speed=0.01)  # очень медленно: успеем остановить посреди звонка
    tail = EventTail(call.events_path)
    deadline = time.monotonic() + 20
    while not tail.events and time.monotonic() < deadline:
        tail.read_new()
        time.sleep(0.05)
    assert tail.events and call.running
    call.stop(timeout_s=5)
    assert not call.running and call.returncode == 0
    tail.read_new()
    assert not tail.finished  # проигрывание прервали, call_ended не пришёл


def test_start_call_requires_a_path_for_files() -> None:
    with pytest.raises(ValueError):
        ui_api.start_call(source="file")
