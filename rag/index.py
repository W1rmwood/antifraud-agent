"""Офлайн-индексация: data/schemes/*.json → коллекция эмбеддингов этапов в Chroma.

Запускается один раз после изменения схем: `python -m rag.index`.
BM25 на диске не хранится: он пересобирается из файлов схем в памяти
при старте, а на несколько сотен чанков это миллисекунды.
"""

import hashlib
import logging

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings as ChromaSettings

from agent.config import get_settings
from rag.chunks import Chunk, load_schemes, scheme_chunks
from rag.embeddings import Embedder, OllamaEmbedder

COLLECTION = "scheme-stages"

log = logging.getLogger(__name__)


def fingerprint(chunks: list[Chunk], embed_model: str) -> str:
    """Отпечаток индекса: хэш текстов всех чанков и имени модели эмбеддингов.

    Меняется, как только поменялась хоть одна схема или модель. Отпечаток
    сохраняется в метаданных коллекции, и при открытии индекса его сверяют
    с текущим (см. retriever.open_index). Так устаревший индекс не будет
    молча выдавать результаты по старым схемам.
    """
    digest = hashlib.sha256(embed_model.encode())
    for chunk in chunks:
        digest.update(chunk.id.encode())
        digest.update(chunk.text.encode())
    return digest.hexdigest()[:16]


def open_client(chroma_dir: str) -> chromadb.ClientAPI:
    """Клиент Chroma, который хранит данные в папке на диске."""
    # Телеметрия выключена: проект работает без интернета.
    return chromadb.PersistentClient(path=chroma_dir, settings=ChromaSettings(anonymized_telemetry=False))


def build_index(
    chunks: list[Chunk], embedder: Embedder, chroma_dir: str, embed_model: str
) -> Collection:
    """Создаёт коллекцию заново, чтобы удалённые схемы не оставались в индексе."""
    client = open_client(chroma_dir)
    if COLLECTION in {c.name for c in client.list_collections()}:
        client.delete_collection(COLLECTION)
    collection = client.create_collection(
        COLLECTION,
        # Векторы считаем сами через bge-m3. Без None Chroma скачала бы свою
        # модель по умолчанию, а интернета у нас нет.
        embedding_function=None,
        # Косинусное расстояние: для эмбеддингов текста важен угол между
        # векторами, а не их длина.
        configuration={"hnsw": {"space": "cosine"}},
        metadata={"fingerprint": fingerprint(chunks, embed_model), "embed_model": embed_model},
    )
    collection.add(
        ids=[c.id for c in chunks],
        embeddings=embedder.embed([c.text for c in chunks]),
        documents=[c.text for c in chunks],
        metadatas=[c.metadata() for c in chunks],
    )
    return collection


def main() -> None:
    """Точка входа `python -m rag.index`: берёт пути и модель из настроек и строит индекс."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    chunks = scheme_chunks(load_schemes(settings.schemes_dir))
    embedder = OllamaEmbedder(settings.embed_model, settings.ollama_host)
    build_index(chunks, embedder, settings.chroma_dir, settings.embed_model)
    log.info("Indexed %d stages into %s", len(chunks), settings.chroma_dir)


if __name__ == "__main__":
    main()
