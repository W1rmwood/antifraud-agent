"""CLI agent.run: остановка живого звонка по Ctrl+C и текст событий для терминала."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from agent.run import format_event, until_stopped
from contracts import AgentEvent, EventType, Speaker, Utterance


def utterance(seq: int) -> Utterance:
    return Utterance(call_id="c", seq=seq, speaker=Speaker.caller, text=f"фраза {seq}", t_start=seq, t_end=seq + 1)


async def endless() -> AsyncIterator[Utterance]:
    """Как микрофон: реплики идут, пока источник не закроют."""
    seq = 0
    try:
        while True:
            await asyncio.sleep(0.01)
            yield utterance(seq)
            seq += 1
    finally:
        endless.closed = True  # type: ignore[attr-defined]


async def test_until_stopped_ends_a_live_source_and_closes_it() -> None:
    stop = asyncio.Event()
    got = []
    async for u in until_stopped(endless(), stop):
        got.append(u)
        if len(got) == 3:
            stop.set()
    assert [u.seq for u in got] == [0, 1, 2]
    assert endless.closed  # type: ignore[attr-defined]


async def test_until_stopped_passes_a_finite_source_through() -> None:
    async def three() -> AsyncIterator[Utterance]:
        for i in range(3):
            yield utterance(i)

    assert [u.seq async for u in until_stopped(three(), asyncio.Event())] == [0, 1, 2]


def event(type_: EventType, payload: dict) -> AgentEvent:
    return AgentEvent(call_id="c", ts=datetime.now(UTC), type=type_, payload=payload)


def test_format_event_reads_like_the_ui() -> None:
    line = format_event(event(EventType.transcript, utterance(0).model_dump(mode="json")), 65)
    assert line == "[01:05] Звонящий: фраза 0"
    warning = format_event(event(EventType.warning, {"text": "Признак развода: код из СМС"}), 3)
    assert warning is not None and "Признак развода" in warning
    hangup = format_event(event(EventType.suggestion, {"text": "Положите трубку.", "kind": "hangup"}), 3)
    assert hangup == "[00:03] ВЫХОД: Положите трубку."
    assert format_event(event(EventType.tool_call, {"tool": "save_case", "args": {}}), 3) is None
