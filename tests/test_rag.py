from pathlib import Path

import pytest

from rag.chunks import Chunk, load_schemes, scheme_chunks, tokenize
from rag.index import build_index
from rag.retriever import Retriever, open_index, rrf
from tests.fakes.fake_embedder import FakeEmbedder

SCHEMES_DIR = "data/schemes"
EMBED_MODEL = "fake"

# Paraphrases, not copies of typical_phrases, so search has to generalize a little.
QUERIES = [
    ("Вам сейчас придёт смс от портала госуслуг, назовите мне цифры из сообщения", "gosuslugi"),
    ("Мама, я попал в аварию, сбил человека, нужны деньги срочно", "relative-accident"),
    ("Ваши сбережения под угрозой, переведите их на резервный счёт центробанка", "safe-account"),
]


@pytest.fixture(scope="module")
def chunks() -> list[Chunk]:
    return scheme_chunks(load_schemes(SCHEMES_DIR))


@pytest.fixture(scope="module")
def retriever(chunks: list[Chunk], tmp_path_factory: pytest.TempPathFactory) -> Retriever:
    chroma_dir = str(tmp_path_factory.mktemp("chroma"))
    embedder = FakeEmbedder()
    collection = build_index(chunks, embedder, chroma_dir, EMBED_MODEL)
    return Retriever(chunks, embedder=embedder, collection=collection, reranker=ReverseReranker(), mode="hybrid")


class ReverseReranker:
    """Scores chunks in reverse of the order it got, so its effect is visible."""

    def score(self, query: str, chunks: list[Chunk]) -> list[float]:
        return [float(i) for i in range(len(chunks))]


def test_one_chunk_per_stage(chunks: list[Chunk]) -> None:
    schemes = load_schemes(SCHEMES_DIR)
    assert len(chunks) == sum(len(s.stages) for s in schemes.values())
    assert len({c.id for c in chunks}) == len(chunks)
    chunk = next(c for c in chunks if c.id == "gosuslugi#2")
    assert chunk.metadata() == {"scheme_id": "gosuslugi", "stage_n": 2, "category": "gosuslugi"}
    assert "Выманивание кода" in chunk.text


def test_tokenize_stems_russian_forms() -> None:
    assert tokenize("Код, КОДА; коду!") == ["код", "кода", "коду"]
    assert tokenize("Переведите переводом", stem_len=5) == ["перев", "перев"]
    assert tokenize("ёлка", stem_len=0) == ["елка"]


def test_rrf_rewards_agreement() -> None:
    fused = rrf([["a", "b", "c"], ["b", "a"]], k=60)
    assert fused["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert fused["b"] == fused["a"]
    assert fused["c"] < fused["a"]


@pytest.mark.parametrize("mode", ["bm25", "vector", "hybrid"])
@pytest.mark.parametrize("query, scheme_id", QUERIES)
def test_finds_scheme_in_top_k(retriever: Retriever, mode: str, query: str, scheme_id: str) -> None:
    hits = retriever.search(query, k=5, mode=mode)  # type: ignore[arg-type]
    assert scheme_id in [h.scheme_id for h in hits]
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_rerank_reorders_fused_candidates(retriever: Retriever) -> None:
    query = QUERIES[0][0]
    hybrid = retriever.search(query, k=20, mode="hybrid")
    reranked = retriever.search(query, k=20, mode="hybrid_rerank")
    assert [h.chunk_id for h in reranked] == [h.chunk_id for h in reversed(hybrid)]


def test_bm25_without_overlap_returns_nothing(retriever: Retriever) -> None:
    assert retriever.search("qwerty zxcvb", mode="bm25") == []


def test_bm25_mode_needs_no_vectors(chunks: list[Chunk]) -> None:
    retriever = Retriever(chunks, mode="bm25")
    assert retriever.search(QUERIES[0][0], k=3)
    with pytest.raises(RuntimeError, match="embedder"):
        retriever.search(QUERIES[0][0], mode="vector")


def test_stale_index_is_rejected(chunks: list[Chunk], tmp_path: Path) -> None:
    build_index(chunks, FakeEmbedder(), str(tmp_path), EMBED_MODEL)
    assert open_index(str(tmp_path), chunks, EMBED_MODEL).count() == len(chunks)
    with pytest.raises(RuntimeError, match="stale"):
        open_index(str(tmp_path), chunks[:-1], EMBED_MODEL)
    with pytest.raises(RuntimeError, match="stale"):
        open_index(str(tmp_path), chunks, "other-model")


def test_rebuild_drops_removed_chunks(chunks: list[Chunk], tmp_path: Path) -> None:
    build_index(chunks, FakeEmbedder(), str(tmp_path), EMBED_MODEL)
    assert build_index(chunks[:5], FakeEmbedder(), str(tmp_path), EMBED_MODEL).count() == 5


async def test_async_search(retriever: Retriever) -> None:
    hits = await retriever.asearch(QUERIES[1][0], k=3, mode="bm25")
    assert len(hits) == 3
