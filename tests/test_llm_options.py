"""Параметры вызова Ollama (keep_alive, num_predict) и компактный формат кандидатов."""

from types import SimpleNamespace

from agent.llm import OllamaLLM
from agent.prompts import Analysis, CallSummary, Reply, format_candidates
from rag.chunks import load_schemes, scheme_chunks
from rag.retriever import Hit


class FakeClient:
    """Подмена ollama.AsyncClient: запоминает аргументы chat и отвечает готовым JSON."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content=self.answers.pop(0)))


ANALYSIS = '{"scheme_id": null, "stage_n": null, "confidence": 0.1, "hard_red_flags": [], "evidence": []}'


async def test_keep_alive_and_num_predict_reach_client():
    llm = OllamaLLM("m", "http://localhost:1", keep_alive="10m", max_tokens={"Analysis": 77})
    llm._client = FakeClient([ANALYSIS])
    await llm.generate([{"role": "user", "content": "x"}], Analysis)
    call = llm._client.calls[0]
    assert call["keep_alive"] == "10m"
    assert call["options"]["num_predict"] == 77


async def test_num_predict_depends_on_schema():
    llm = OllamaLLM("m", "http://localhost:1")
    llm._client = FakeClient(
        ['{"tactic": "play_dumb", "text": "а?"}', '{"legend": null, "key_phrases": [], "summary": "s"}']
    )
    await llm.generate([], Reply)
    await llm.generate([], CallSummary)
    reply, summary = (c["options"]["num_predict"] for c in llm._client.calls)
    assert summary > reply
    assert llm._client.calls[0]["keep_alive"]  # значение по умолчанию из настроек


async def test_truncated_json_is_retried():
    llm = OllamaLLM("m", "http://localhost:1", retries=1)
    llm._client = FakeClient([ANALYSIS[:40], ANALYSIS])
    result = await llm.generate([{"role": "user", "content": "x"}], Analysis)
    assert result.confidence == 0.1
    assert len(llm._client.calls) == 2


def test_compact_candidates_keep_id_and_stage():
    chunks = scheme_chunks(load_schemes("data/schemes"))
    hits = [Hit(**c.model_dump(), score=1.0) for c in chunks[:5]]
    compact = format_candidates(hits)
    for hit in hits:
        assert f"[{hit.scheme_id}, этап {hit.stage_n}]" in compact
    assert len(compact) < sum(len(h.text) for h in hits)
