"""The agent on fakes: transcript instead of audio, scripted LLM, in-memory storage, real MCP in-process."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent.config import Settings
from agent.graph import AgentDeps
from agent.llm import LLMError, generate_structured
from agent.prompts import Analysis, CallSummary, analysis_messages
from agent.run import run_call
from contracts import AgentEvent, AudioSource, CallCase, CallRequest, EventType, Speaker, Utterance
from mcp_server.server import build_server
from rag.chunks import load_schemes, scheme_chunks
from rag.retriever import Hit, Retriever
from tests.fakes import FakeListener, InMemoryStorage
from tests.fakes.fake_llm import FakeLLM, no_fraud
from tests.test_mock_events import PAYLOAD_KEYS

PHONE = "+79990000001"
CALL_001 = "data/calls/001.txt"
TRANSFER = "их нужно сегодня же перевести на защищённый резервный счёт Центробанка"


def safe_account_rule(prompt: str) -> Analysis:
    """Imitates a model that recognizes the call 001 once money transfer is demanded."""
    transcript = prompt.split("Последние реплики:")[-1]
    if "резервный счёт" not in transcript:
        return no_fraud(prompt)
    stage = 2 if "Никому не сообщайте" in transcript else 3
    if "снимите наличные" in transcript:
        stage = 3
    return Analysis(
        scheme_id="safe-account",
        stage_n=stage,
        confidence=0.9,
        hard_red_flags=[TRANSFER],
        evidence=["служба финансовой безопасности Центрального банка", "выдуманная цитата"],
        suggestion="Подождите, я найду очки.",
        suggestion_kind="stall",
        warning="Просят перевести деньги на «резервный счёт». Ничего не переводите.",
    )


class Harness:
    def __init__(self, tmp_path: Path, llm: FakeLLM) -> None:
        schemes = load_schemes("data/schemes")
        retriever = Retriever(scheme_chunks(schemes), mode="bm25")
        self.storage = InMemoryStorage()
        self.server = build_server(retriever, self.storage, schemes)
        self.settings = Settings(runs_dir=str(tmp_path / "runs"), retrieval_mode="bm25")
        self.deps = AgentDeps(retriever=retriever, llm=llm, settings=self.settings)
        self.queue: asyncio.Queue[AgentEvent] = asyncio.Queue()

    async def run(self, call_id: str, listener: FakeListener, phone: str | None = PHONE) -> CallCase:
        request = CallRequest(call_id=call_id, source=AudioSource(kind="file", path=CALL_001), phone=phone)
        return await run_call(request, listener, self.deps, self.server, self.queue)

    def events(self) -> list[AgentEvent]:
        return [self.queue.get_nowait() for _ in range(self.queue.qsize())]


def seed_history(storage: InMemoryStorage) -> None:
    storage.save_case(
        CallCase(
            call_id="old",
            phone=PHONE,
            started_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
            ended_at=None,
            scheme_id="safe-account",
            max_stage=2,
            legend=None,
            key_phrases=[],
            summary="old call",
            transcript_path="data/runs/old.jsonl",
        )
    )


async def test_fraud_call_end_to_end(tmp_path: Path) -> None:
    summary = CallSummary(legend="сотрудник ЦБ", key_phrases=["резервный счёт"], summary="Мошенник от имени ЦБ.")
    harness = Harness(tmp_path, FakeLLM(safe_account_rule, summary))
    seed_history(harness.storage)

    case = await harness.run("call-001", FakeListener())
    events = harness.events()
    types = [e.type for e in events]

    # Event stream follows the contract the UI is built on.
    for event in events:
        if event.type not in (EventType.transcript, EventType.summary):
            assert set(event.payload) == PAYLOAD_KEYS[event.type], event
    assert types[0] == EventType.call_started and types[-1] == EventType.call_ended
    assert types.count(EventType.transcript) == 12

    # Known number: history checked before the first utterance, with a warning.
    first_transcript = types.index(EventType.transcript)
    assert EventType.warning in types[:first_transcript]
    assert events[1].payload == {"tool": "lookup_number", "args": {"phone": PHONE}}

    # The scheme is declared once, only after the transfer demand, with real quotes only.
    detected = [e for e in events if e.type == EventType.scheme_detected]
    assert len(detected) == 1
    assert detected[0].payload["scheme_id"] == "safe-account"
    assert TRANSFER in detected[0].payload["evidence"]
    assert "выдуманная цитата" not in detected[0].payload["evidence"]
    transcripts_before = [
        e.payload["text"] for e in events[: events.index(detected[0])] if e.type == EventType.transcript
    ]
    assert any("резервный счёт" in t for t in transcripts_before)

    stages = [e.payload["stage_n"] for e in events if e.type == EventType.stage_changed]
    assert stages == [3, 2, 3]
    assert [e.payload["kind"] for e in events if e.type == EventType.suggestion] == ["stall"]  # no repeats

    # The case is saved through MCP and matches the summary event.
    assert case.scheme_id == "safe-account" and case.max_stage == 3
    assert case.legend == "сотрудник ЦБ"
    assert CallCase.model_validate(events[-4].payload) == case
    assert events[-3].payload["tool"] == "save_case" and events[-2].payload["ok"]
    assert harness.storage.lookup_number(PHONE).calls_count == 2

    # Everything the UI got is also in the call log.
    log = Path(harness.settings.runs_dir) / "call-001.jsonl"
    logged = [AgentEvent.model_validate_json(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert logged == events


async def test_ordinary_call_raises_no_alarm(tmp_path: Path) -> None:
    harness = Harness(tmp_path, FakeLLM(no_fraud, CallSummary(legend=None, key_phrases=[], summary="Курьер.")))
    listener = FakeListener([(Speaker.caller, "Это курьер, скажите код домофона, пожалуйста.")])

    case = await harness.run("call-ok", listener, phone=None)

    types = {e.type for e in harness.events()}
    assert not types & {EventType.scheme_detected, EventType.suggestion, EventType.warning}
    assert "lookup_number" not in str(harness.events())
    assert case.scheme_id is None and case.max_stage is None


async def test_dead_llm_does_not_break_the_call(tmp_path: Path) -> None:
    def dead(prompt: str) -> Analysis:
        raise LLMError("ollama is down")

    harness = Harness(tmp_path, FakeLLM(dead, summary=None))

    case = await harness.run("call-dead", FakeListener())

    assert case.scheme_id is None
    assert "Схема не найдена" in case.summary
    assert harness.storage.list_recent_cases()[0].call_id == "call-dead"


async def test_impossible_stage_is_ignored(tmp_path: Path) -> None:
    def wrong_stage(prompt: str) -> Analysis:
        return safe_account_rule(prompt).model_copy(update={"stage_n": 42})

    harness = Harness(tmp_path, FakeLLM(wrong_stage, summary=None))
    case = await harness.run("call-stage", FakeListener())

    assert case.scheme_id == "safe-account"
    assert case.max_stage is None
    assert EventType.stage_changed not in {e.type for e in harness.events()}


async def test_user_utterances_do_not_trigger_analysis(tmp_path: Path) -> None:
    llm = FakeLLM(no_fraud, CallSummary(legend=None, key_phrases=[], summary="."))
    harness = Harness(tmp_path, llm)
    lines = [(Speaker.user, "Алло"), (Speaker.caller, "Здравствуйте, это банк"), (Speaker.user, "Да")]

    await harness.run("call-user", FakeListener(lines), phone=None)

    analysis_prompts = [p for p in llm.prompts if p.startswith("Кандидаты")]
    assert len(analysis_prompts) == 1
    assert "Абонент: Алло" in analysis_prompts[0]  # the user's words are still context


def test_analysis_prompt_contains_candidates_and_dialogue() -> None:
    hit = Hit(chunk_id="gosuslugi#2", scheme_id="gosuslugi", stage_n=2, category="gosuslugi", text="Выманивание кода", score=1.0)
    window = [
        Utterance(call_id="c", seq=0, speaker=Speaker.caller, text="Продиктуйте код", t_start=0, t_end=1),
        Utterance(call_id="c", seq=1, speaker=Speaker.user, text="Зачем?", t_start=1, t_end=2),
    ]
    scheme = load_schemes("data/schemes")["gosuslugi"]

    [system, user] = analysis_messages(window, [hit], scheme, 2)

    assert system["role"] == "system"
    assert "[gosuslugi, этап 2]" in user["content"]
    assert "Звонящий: Продиктуйте код\nАбонент: Зачем?" in user["content"]
    assert scheme.stall_tactics[0] in user["content"]


class ScriptedChat:
    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.histories: list[list[dict[str, str]]] = []

    async def __call__(self, messages: list[dict[str, str]], schema: dict) -> str:
        self.histories.append(list(messages))
        return self.answers.pop(0)


async def test_invalid_json_is_retried_with_the_error() -> None:
    valid = CallSummary(legend=None, key_phrases=[], summary="ok").model_dump_json()
    chat = ScriptedChat(["not json", valid])

    result = await generate_structured(chat, [{"role": "user", "content": "hi"}], CallSummary, retries=2)

    assert result.summary == "ok"
    retry_prompt = chat.histories[1][-1]["content"]
    assert "не прошёл проверку" in retry_prompt


async def test_gives_up_after_retries() -> None:
    chat = ScriptedChat(['{"confidence": 5}'] * 3)
    with pytest.raises(LLMError):
        await generate_structured(chat, [{"role": "user", "content": "hi"}], Analysis, retries=2)
    assert len(chat.histories) == 3
