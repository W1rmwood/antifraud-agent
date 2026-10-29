from pathlib import Path

from agent.config import Settings
from agent.graph import AgentDeps, Turn
from agent.prompts import Analysis
from eval.dataset import EvalCase, EvalUtterance, load_cases
from eval.run_eval import CaseRun, eval_retrieval, replay, run_case, score, threshold_sweep
from mcp_server.server import build_server
from rag.chunks import load_schemes, scheme_chunks
from rag.retriever import Retriever
from tests.fakes import InMemoryStorage
from tests.fakes.fake_llm import FakeLLM, no_fraud

SCHEMES = load_schemes("data/schemes")
FLAG = "продиктуйте код из смс"


def fraud_case(case_id: str = "f1") -> EvalCase:
    return EvalCase(
        id=case_id,
        utterances=[EvalUtterance(speaker="caller", text=f"Госуслуги, {FLAG}")],
        expected_scheme="gosuslugi",
        expected_stage=2,
    )


def normal_case(case_id: str = "n1") -> EvalCase:
    return EvalCase(
        id=case_id,
        utterances=[EvalUtterance(speaker="caller", text="Курьер, буду через час")],
        expected_scheme=None,
        expected_stage=None,
    )


def turn(confidence: float, scheme_id: str | None = "gosuslugi", stage: int | None = 2) -> Turn:
    analysis = Analysis(
        scheme_id=scheme_id,
        stage_n=stage,
        confidence=confidence,
        hard_red_flags=[FLAG],
        evidence=[],
    )
    return Turn(seq=0, caller_text=f"Госуслуги, {FLAG}", candidates=["gosuslugi"], analysis=analysis, latency_s=1.0)


def test_bm25_retrieval_on_dataset() -> None:
    retriever = Retriever(scheme_chunks(SCHEMES), mode="bm25")
    row = eval_retrieval(retriever, load_cases(), "bm25", k=5)
    assert row.hit1 <= row.hitk
    assert row.hitk >= 0.8  # guards against regressions in chunking or tokenization


def test_score_counts_hits_and_false_alarms() -> None:
    runs = [
        CaseRun(fraud_case("f1"), "gosuslugi", 2, [turn(0.9)]),
        CaseRun(fraud_case("f2"), "gosuslugi", 1, [turn(0.9)]),  # right scheme, wrong stage
        CaseRun(fraud_case("f3"), None, None, [turn(0.2)]),
        CaseRun(normal_case("n1"), None, None, [turn(0.1, None, None)]),
        CaseRun(normal_case("n2"), "safe-account", 1, [turn(0.7)]),
    ]
    row = score("fake", runs)
    assert row.scheme_acc == 2 / 3
    assert row.stage_acc == 1 / 3
    assert row.false_alarms == 1 / 2
    assert row.accuracy == 3 / 5


def test_replay_applies_threshold() -> None:
    run = CaseRun(fraud_case(), None, None, [turn(0.65)])
    assert replay(run, 0.6, SCHEMES) == ("gosuslugi", 2)
    assert replay(run, 0.7, SCHEMES) == (None, None)

    row = score("fake", [run, CaseRun(normal_case(), None, None, [turn(0.1, None, None)])])
    sweep = dict((th, found) for th, found, _ in threshold_sweep(row, SCHEMES))
    assert sweep[0.6] == 1.0 and sweep[0.7] == 0.0


async def test_run_case_records_turns(tmp_path: Path) -> None:
    retriever = Retriever(scheme_chunks(SCHEMES), mode="bm25")
    settings = Settings(runs_dir=str(tmp_path), retrieval_mode="bm25")
    deps = AgentDeps(retriever=retriever, llm=FakeLLM(no_fraud), settings=settings)
    server = build_server(retriever, InMemoryStorage(), SCHEMES)

    run = await run_case(fraud_case(), deps, server)

    assert run.scheme_id is None
    assert len(run.turns) == 1 and run.turns[0].analysis is not None
    assert "gosuslugi" in run.turns[0].candidates
