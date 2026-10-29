"""Гибридный поиск по этапам схем: векторы + BM25, слияние через RRF, затем реранкер.

Все режимы оставлены переключаемыми (`vector`, `bm25`, `hybrid`, `hybrid_rerank`),
потому что eval сравнивает их между собой. Агент использует тот, что задан в настройках.

Зачем два поиска сразу:
- векторы ловят смысл, даже если слова другие («переведите на резервный счёт»
  ≈ «отправьте на защищённую ячейку»);
- BM25 ловит точные слова, которые векторы иногда размывают («СНИЛС», «CVV»).
"""

import asyncio
from pathlib import Path
from typing import Protocol

from chromadb.api.models.Collection import Collection
from rank_bm25 import BM25Okapi

from agent.config import RetrievalMode, Settings
from rag.chunks import Chunk, load_schemes, scheme_chunks, tokenize
from rag.embeddings import Embedder, OllamaEmbedder
from rag.index import COLLECTION, fingerprint, open_client


class Hit(Chunk):
    """Один результат поиска: найденный чанк-этап и его оценка.

    Шкала score зависит от режима: косинусная близость, оценка BM25,
    сумма RRF или оценка реранкера. Сравнивать score можно только внутри
    одной выдачи, не между режимами.
    """

    score: float


class Reranker(Protocol):
    """Переоценивает кандидатов: возвращает по одной оценке на чанк, в том же порядке."""

    def score(self, query: str, chunks: list[Chunk]) -> list[float]: ...


class FlashRankReranker:
    """Реранкер на кросс-энкодере. При первом запуске скачивает модель в `cache_dir`.

    Кросс-энкодер читает запрос и чанк вместе, поэтому точнее векторов,
    но медленнее. Его запускают только на 20 лучших кандидатах после слияния,
    а не на всей базе.
    """

    def __init__(self, model_name: str, cache_dir: str) -> None:
        # Тяжёлый импорт (onnxruntime), поэтому только когда реранкер включён.
        from flashrank import Ranker  # noqa: PLC0415

        self._ranker = Ranker(model_name=model_name, cache_dir=cache_dir)

    def score(self, query: str, chunks: list[Chunk]) -> list[float]:
        from flashrank import RerankRequest  # noqa: PLC0415

        # FlashRank возвращает пассажи, отсортированные по оценке. Чтобы вернуть
        # оценки в исходном порядке чанков, кладём в id индекс чанка и
        # раскладываем результат обратно по этим индексам.
        passages = [{"id": i, "text": c.text} for i, c in enumerate(chunks)]
        ranked = self._ranker.rerank(RerankRequest(query=query, passages=passages))
        scores = [0.0] * len(chunks)
        for item in ranked:
            scores[item["id"]] = float(item["score"])
        return scores


def rrf(rankings: list[list[str]], k: int) -> dict[str, float]:
    """Reciprocal Rank Fusion: для каждого документа сумма 1 / (k + место) по всем выдачам.

    Работает с местами в выдаче, а не с оценками. Поэтому косинусную близость
    и оценку BM25 не нужно приводить к одной шкале. Документ, который высоко
    в обеих выдачах, получает больше всего. k (обычно 60) сглаживает разницу
    между первым и вторым местом, чтобы одна выдача не перевешивала другую.
    """
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return fused


class Retriever:
    """Поиск этапов схем по тексту звонящего.

    Компоненты передаются в конструктор: в тестах — фейки, в работе —
    настоящие (см. `from_settings`). Режим без векторов (`bm25`) работает
    без Ollama и без индекса Chroma.
    """

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
        # BM25 возвращает оценки списком в порядке документов, без id.
        # Этот список сопоставляет позицию оценки с id чанка.
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
        """Собирает настоящий поиск по настройкам. Загружает только то, что нужно режиму."""
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
        """Возвращает k лучших этапов. `mode` позволяет eval сравнить режимы на одном индексе."""
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
        return [Hit(**self._chunks[chunk_id].model_dump(), score=score) for chunk_id, score in scored[:k]]

    async def asearch(self, query: str, k: int = 5, mode: RetrievalMode | None = None) -> list[Hit]:
        """То же, что search, но в отдельном потоке: эмбеддинги и реранкер не блокируют event loop."""
        return await asyncio.to_thread(self.search, query, k, mode)

    def _bm25_search(self, query: str) -> list[tuple[str, float]]:
        """Лучшие кандидаты по BM25 в виде пар (id чанка, оценка)."""
        scores = self._bm25.get_scores(tokenize(query, self._stem_len))
        ranked = sorted(zip(self._order, scores), key=lambda item: item[1], reverse=True)
        # Чанк без единого общего слова с запросом — вообще не кандидат.
        # Иначе в RRF попали бы случайные чанки с нулевой оценкой.
        return [(i, float(s)) for i, s in ranked[: self._candidates_k] if s > 0]

    def _vector_search(self, query: str) -> list[tuple[str, float]]:
        """Лучшие кандидаты по косинусной близости эмбеддингов."""
        if self._embedder is None or self._collection is None:
            raise RuntimeError("vector search needs an embedder and a Chroma collection")
        [vector] = self._embedder.embed([query])
        result = self._collection.query(
            query_embeddings=[vector], n_results=min(self._candidates_k, len(self._order)), include=["distances"]
        )
        distances = result["distances"]
        assert distances is not None
        # Chroma отдаёт косинусное расстояние (0 — одинаковые). Переводим
        # в близость, чтобы во всех режимах «больше» значило «лучше».
        return [(i, 1.0 - d) for i, d in zip(result["ids"][0], distances[0])]

    def _rerank(self, query: str, chunk_ids: list[str]) -> list[tuple[str, float]]:
        """Пересортировывает кандидатов по оценке реранкера."""
        if self._reranker is None:
            raise RuntimeError("mode 'hybrid_rerank' needs a reranker")
        scores = self._reranker.score(query, [self._chunks[i] for i in chunk_ids])
        return sorted(zip(chunk_ids, scores), key=lambda item: item[1], reverse=True)


def open_index(chroma_dir: str, chunks: list[Chunk], embed_model: str) -> Collection:
    """Открывает коллекцию Chroma и отказывается работать, если она собрана по другим схемам или модели."""
    if not Path(chroma_dir).exists():
        raise RuntimeError(f"no index in {chroma_dir}: run `python -m rag.index` first")
    collection = open_client(chroma_dir).get_collection(COLLECTION, embedding_function=None)
    # Сверяем отпечаток из индекса с отпечатком текущих схем (см. index.fingerprint).
    stored = (collection.metadata or {}).get("fingerprint")
    if stored != fingerprint(chunks, embed_model):
        raise RuntimeError("index is stale (schemes or embedding model changed): run `python -m rag.index`")
    return collection
