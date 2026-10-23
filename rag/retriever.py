"""Hybrid search over scheme stages: vectors + BM25, fused with RRF, then reranked.

Every mode is kept selectable (`vector`, `bm25`, `hybrid`, `hybrid_rerank`)
because eval compares them; the agent uses whichever the config says.
"""

import asyncio
from pathlib import Path
from typing import Protocol

from chromadb.api.models.Collection import Collection
from pydantic import BaseModel
from rank_bm25 import BM25Okapi

from agent.config import RetrievalMode, Settings
from rag.chunks import Chunk, load_schemes, scheme_chunks, tokenize
from rag.embeddings import Embedder, OllamaEmbedder
from rag.index import COLLECTION, fingerprint, open_client


class Hit(BaseModel):
    chunk_id: str
    scheme_id: str
    stage_n: int
    category: str
    text: str
    score: float


class Reranker(Protocol):
    def score(self, query: str, chunks: list[Chunk]) -> list[float]: ...


class FlashRankReranker:
    """Cross-encoder reranker. Downloads the model into `cache_dir` on first use."""

    def __init__(self, model_name: str, cache_dir: str) -> None:
        from flashrank import Ranker  # noqa: PLC0415  heavy import, only when reranking is on

        self._ranker = Ranker(model_name=model_name, cache_dir=cache_dir)

    def score(self, query: str, chunks: list[Chunk]) -> list[float]:
        from flashrank import RerankRequest  # noqa: PLC0415

        passages = [{"id": i, "text": c.text} for i, c in enumerate(chunks)]
        ranked = self._ranker.rerank(RerankRequest(query=query, passages=passages))
        scores = [0.0] * len(chunks)
        for item in ranked:
            scores[item["id"]] = float(item["score"])
        return scores


def rrf(rankings: list[list[str]], k: int) -> dict[str, float]:
    """Reciprocal Rank Fusion: sum of 1 / (k + rank) over every ranking a document appears in.

    Uses ranks, not scores, so cosine distances and BM25 scores need no common scale.
    """
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return fused


class Retriever:
    def __init__(
        self,
        chunks: list[Chunk],
        *,
        embedder: Embedder | None = None,
        collection: Collection | None = None,
        reranker: Reranker | None = None,
        mode: RetrievalMode = "hybrid_rerank",
        candidates_k: int = 20,
        rrf_k: int = 60,
        stem_len: int = 5,
    ) -> None:
        self._chunks = {c.id: c for c in chunks}
        self._order = [c.id for c in chunks]
        self._embedder = embedder
        self._collection = collection
        self._reranker = reranker
        self._mode: RetrievalMode = mode
        self._candidates_k = candidates_k
        self._rrf_k = rrf_k
        self._stem_len = stem_len
        self._bm25 = BM25Okapi([tokenize(c.text, stem_len) for c in chunks])

    @classmethod
    def from_settings(cls, settings: Settings) -> "Retriever":
        chunks = scheme_chunks(load_schemes(settings.schemes_dir))
        embedder = collection = reranker = None
        if settings.retrieval_mode != "bm25":
            embedder = OllamaEmbedder(settings.embed_model, settings.ollama_host)
            collection = open_index(settings.chroma_dir, chunks, settings.embed_model)
        if settings.retrieval_mode == "hybrid_rerank":
            reranker = FlashRankReranker(settings.reranker_model, settings.reranker_cache_dir)
        return cls(
            chunks,
            embedder=embedder,
            collection=collection,
            reranker=reranker,
            mode=settings.retrieval_mode,
            candidates_k=settings.candidates_k,
            rrf_k=settings.rrf_k,
            stem_len=settings.bm25_stem_len,
        )

    def search(self, query: str, k: int = 5, mode: RetrievalMode | None = None) -> list[Hit]:
        mode = mode or self._mode
        if mode == "bm25":
            scored = self._bm25_search(query)
        elif mode == "vector":
            scored = self._vector_search(query)
        else:
            vector, bm25 = self._vector_search(query), self._bm25_search(query)
            fused = rrf([[i for i, _ in vector], [i for i, _ in bm25]], self._rrf_k)
            scored = sorted(fused.items(), key=lambda item: item[1], reverse=True)
            if mode == "hybrid_rerank":
                scored = self._rerank(query, [i for i, _ in scored[: self._candidates_k]])
        return [self._hit(chunk_id, score) for chunk_id, score in scored[:k]]

    async def asearch(self, query: str, k: int = 5, mode: RetrievalMode | None = None) -> list[Hit]:
        return await asyncio.to_thread(self.search, query, k, mode)

    def _bm25_search(self, query: str) -> list[tuple[str, float]]:
        scores = self._bm25.get_scores(tokenize(query, self._stem_len))
        ranked = sorted(zip(self._order, scores), key=lambda item: item[1], reverse=True)
        # Chunks without a single shared token are not candidates at all.
        return [(i, float(s)) for i, s in ranked[: self._candidates_k] if s > 0]

    def _vector_search(self, query: str) -> list[tuple[str, float]]:
        if self._embedder is None or self._collection is None:
            raise RuntimeError("vector search needs an embedder and a Chroma collection")
        [vector] = self._embedder.embed([query])
        result = self._collection.query(
            query_embeddings=[vector], n_results=min(self._candidates_k, len(self._order)), include=["distances"]
        )
        distances = result["distances"]
        assert distances is not None
        return [(i, 1.0 - d) for i, d in zip(result["ids"][0], distances[0])]  # cosine similarity

    def _rerank(self, query: str, chunk_ids: list[str]) -> list[tuple[str, float]]:
        if self._reranker is None:
            raise RuntimeError("mode 'hybrid_rerank' needs a reranker")
        scores = self._reranker.score(query, [self._chunks[i] for i in chunk_ids])
        return sorted(zip(chunk_ids, scores), key=lambda item: item[1], reverse=True)

    def _hit(self, chunk_id: str, score: float) -> Hit:
        chunk = self._chunks[chunk_id]
        return Hit(
            chunk_id=chunk.id,
            scheme_id=chunk.scheme_id,
            stage_n=chunk.stage_n,
            category=chunk.category,
            text=chunk.text,
            score=score,
        )


def open_index(chroma_dir: str, chunks: list[Chunk], embed_model: str) -> Collection:
    """Open the Chroma collection and refuse to use it if it was built from other schemes or model."""
    if not Path(chroma_dir).exists():
        raise RuntimeError(f"no index in {chroma_dir}: run `python -m rag.index` first")
    collection = open_client(chroma_dir).get_collection(COLLECTION, embedding_function=None)
    stored = (collection.metadata or {}).get("fingerprint")
    if stored != fingerprint(chunks, embed_model):
        raise RuntimeError("index is stale (schemes or embedding model changed): run `python -m rag.index`")
    return collection
