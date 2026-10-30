"""Real-time call: hearing and analysis run in parallel, utterances piled up while the LLM is busy
are analyzed as one batch, the model reply is generated in the background, one at a time."""

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

from mcp import Client
from pydantic import BaseModel

from agent.events import EventSink
from agent.graph import CallAgent
from agent.llm import Messages
from agent.prompts import Analysis, CallSummary, Reply
from agent.run import converse
from agent.tools import ToolClient
from contracts import AudioSource, EventType, Speaker
from tests.fakes import FakeListener
from tests.fakes.fake_llm import DEFAULT_REPLY, AnalysisRule, FakeLLM
from tests.test_agent import CALL_001, TRANSFER, Harness, safe_account_rule

SUMMARY = CallSummary(legend=None, key_phrases=[], summary=".")

# Every line demands the transfer, so every analysis declares safe-account.
FRAUD_LINES = [(Speaker.caller, f"Это Центробанк, реплика {i}: {TRANSFER}.") for i in range(6)]


class SlowLLM(FakeLLM):
    """FakeLLM that thinks: analysis and reply take time, like qwen3:8b, but in tenths of a second."""

    def __init__(self, analyze: AnalysisRule, analysis_s: float = 0.0, reply_s: float = 0.0, **kwargs) -> None:
        super().__init__(analyze, SUMMARY, **kwargs)
        self.analysis_s = analysis_s
        self.reply_s = reply_s
        self.analysis_done: list[datetime] = []  # when each analysis finished
        self.replying = 0  # replies being generated right now
        self.max_replying = 0

    async def generate[T: BaseModel](
        self, messages: Messages, schema: type[T], temperature: float | None = None
    ) -> T:
        if schema is Reply:
            self.replying += 1
            self.max_replying = max(self.max_replying, self.replying)
            try:
                await asyncio.sleep(self.reply_s)
                return await super().generate(messages, schema, temperature)
            finally:
                self.replying -= 1
        if schema is Analysis:
            await asyncio.sleep(self.analysis_s)
            self.analysis_done.append(datetime.now(UTC))
        return await super().generate(messages, schema, temperature)


def analysis_prompts(llm: FakeLLM) -> list[str]:
    return [p for p in llm.prompts if p.startswith("Кандидаты")]


async def test_transcript_is_shown_while_analysis_runs(tmp_path: Path) -> None:
    llm = SlowLLM(safe_account_rule, analysis_s=0.3)
    harness = Harness(tmp_path, llm)

    await harness.run("call-ears", FakeListener(FRAUD_LINES[:4], delay_s=0.05), phone=None)

    # All four utterances reach the UI long before the first 0.3 s analysis is over.
    transcripts = [e.ts for e in harness.events() if e.type == EventType.transcript]
    assert len(transcripts) == 4
    assert all(ts < llm.analysis_done[0] for ts in transcripts)


async def test_busy_llm_analyzes_a_batch_on_a_fresh_window(tmp_path: Path) -> None:
    llm = SlowLLM(safe_account_rule, analysis_s=0.2)
    harness = Harness(tmp_path, llm)
    sink = EventSink("call-batch")
    async with Client(harness.server) as client:
        agent = CallAgent("call-batch", harness.deps, sink, ToolClient(client, sink))
        listener = FakeListener(FRAUD_LINES, delay_s=0.05)
        await converse(agent, listener(AudioSource(kind="file", path=CALL_001), "call-batch"))
        await agent.finish()

    # Six caller utterances in 0.25 s, a 0.2 s analysis: the queue does not grow,
    # fewer analyses than utterances, and none of the utterances is lost.
    prompts = analysis_prompts(llm)
    assert 1 < len(prompts) < len(FRAUD_LINES)
    assert len(agent.turns) == len(prompts)
    assert sum(t.batch_size for t in agent.turns) == len(FRAUD_LINES)
    assert any(t.batch_size > 1 for t in agent.turns)
    # The last analysis sees the last utterance of the call.
    assert FRAUD_LINES[-1][1] in prompts[-1]
    assert agent.turns[-1].seq == len(FRAUD_LINES) - 1


async def test_reply_that_finishes_in_time_is_shown(tmp_path: Path) -> None:
    llm = SlowLLM(safe_account_rule, reply_s=0.05)
    harness = Harness(tmp_path, llm)

    await harness.run("call-reply", FakeListener(FRAUD_LINES[:2], delay_s=0.3), phone=None)

    kinds = [e.payload["kind"] for e in harness.events() if e.type == EventType.suggestion]
    # The way out comes at once (rules, then the scheme), the model reply right after the analysis.
    assert kinds[:2] == ["hangup", "hangup"] and "provoke" in kinds


async def test_no_suggestions_after_the_call_ended(tmp_path: Path) -> None:
    llm = SlowLLM(safe_account_rule, reply_s=5.0)
    harness = Harness(tmp_path, llm)
    started = time.monotonic()

    case = await harness.run("call-hang", FakeListener(FRAUD_LINES[:1]), phone=None)

    # The unfinished reply is cancelled, not awaited: no hint after the conversation is over,
    # and «Стоп» saves the case at once instead of waiting for the model.
    events = harness.events()
    kinds = [e.payload["kind"] for e in events if e.type == EventType.suggestion]
    assert kinds == ["hangup", "hangup"]  # rules + analysis; the reply was cancelled
    assert time.monotonic() - started < 2
    assert events[-1].type == EventType.call_ended
    assert case.scheme_id == "safe-account"
    assert harness.storage.list_recent_cases()[0].call_id == "call-hang"


async def test_new_batch_cancels_a_stale_reply(tmp_path: Path) -> None:
    # Phrases come faster than the model writes a reply: each new batch cancels the reply to the
    # old window, so the analysis never waits behind it (Ollama serves one request at a time).
    llm = SlowLLM(safe_account_rule, analysis_s=0.02, reply_s=1.0)
    harness = Harness(tmp_path, llm)
    started = time.monotonic()

    await harness.run("call-fast", FakeListener(FRAUD_LINES, delay_s=0.15), phone=None)

    kinds = [e.payload["kind"] for e in harness.events() if e.type == EventType.suggestion]
    assert "provoke" not in kinds  # every reply was outdated by the next phrase
    assert llm.max_replying == 1
    assert time.monotonic() - started < 3  # no 1-second reply waited for on any turn


async def test_one_reply_generation_at_a_time(tmp_path: Path) -> None:
    llm = SlowLLM(safe_account_rule, reply_s=0.2)
    harness = Harness(tmp_path, llm)

    await harness.run("call-single", FakeListener(FRAUD_LINES, delay_s=0.05), phone=None)

    # Analyses are fast, so every utterance gets one; a new analysis cancels the reply to the
    # previous window, so there is never more than one reply in flight.
    assert len(analysis_prompts(llm)) == len(FRAUD_LINES)
    assert llm.max_replying == 1


async def test_audio_failure_still_saves_the_case(tmp_path: Path) -> None:
    class BrokenListener(FakeListener):
        async def __call__(self, source, call_id):  # type: ignore[no-untyped-def, override]
            async for utterance in super().__call__(source, call_id):
                yield utterance
            raise OSError("microphone unplugged")

    harness = Harness(tmp_path, SlowLLM(safe_account_rule))

    try:
        await harness.run("call-broken", BrokenListener(FRAUD_LINES[:2]), phone=None)
    except OSError as error:
        assert "microphone" in str(error)
    else:
        raise AssertionError("the audio error must not be swallowed")

    # The error surfaces only after the call is closed properly.
    types = [e.type for e in harness.events()]
    assert types[-1] == EventType.call_ended
    assert harness.storage.list_recent_cases()[0].call_id == "call-broken"

