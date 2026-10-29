"""Checks against a running Ollama. Skipped by default; run with `pytest -m integration`."""

import pytest

from agent.config import get_settings
from agent.llm import OllamaLLM
from agent.prompts import Analysis, analysis_messages
from contracts import Speaker, Utterance
from rag.chunks import load_schemes, scheme_chunks
from rag.embeddings import OllamaEmbedder
from rag.retriever import Retriever

pytestmark = pytest.mark.integration


def test_bge_m3_embeddings() -> None:
    settings = get_settings()
    [vector] = OllamaEmbedder(settings.embed_model, settings.ollama_host).embed(["Продиктуйте код из СМС"])
    assert len(vector) == 1024


async def test_llm_answers_by_schema() -> None:
    settings = get_settings()
    llm = OllamaLLM(settings.llm_model, settings.ollama_host, retries=settings.llm_retries)
    retriever = Retriever(scheme_chunks(load_schemes(settings.schemes_dir)), mode="bm25")
    text = "Служба поддержки Госуслуг. Вам пришло СМС, продиктуйте мне код, чтобы мы заблокировали взлом."
    window = [Utterance(call_id="it", seq=0, speaker=Speaker.caller, text=text, t_start=0, t_end=5)]

    analysis = await llm.generate(analysis_messages(window, retriever.search(text, k=5), None, None), Analysis)

    assert analysis.scheme_id == "gosuslugi"
    assert analysis.hard_red_flags
