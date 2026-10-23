"""Offline indexing: data/schemes/*.json -> Chroma collection of stage embeddings.

Run once after schemes change: `python -m rag.index`. BM25 is not stored:
it is rebuilt from the scheme files in memory at startup, which takes
milliseconds for a few hundred chunks.
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
    """Changes whenever chunk texts or the embedding model change, i.e. when the index is stale."""
    digest = hashlib.sha256(embed_model.encode())
    for chunk in chunks:
        digest.update(chunk.id.encode())
        digest.update(chunk.text.encode())
    return digest.hexdigest()[:16]


def open_client(chroma_dir: str) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=chroma_dir, settings=ChromaSettings(anonymized_telemetry=False))


def build_index(
    chunks: list[Chunk], embedder: Embedder, chroma_dir: str, embed_model: str
) -> Collection:
    """Recreate the collection from scratch, so deleted schemes do not linger in the index."""
    client = open_client(chroma_dir)
    if COLLECTION in {c.name for c in client.list_collections()}:
        client.delete_collection(COLLECTION)
    collection = client.create_collection(
        COLLECTION,
        embedding_function=None,  # we pass vectors ourselves, Chroma must not download its default model
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
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    chunks = scheme_chunks(load_schemes(settings.schemes_dir))
    embedder = OllamaEmbedder(settings.embed_model, settings.ollama_host)
    build_index(chunks, embedder, settings.chroma_dir, settings.embed_model)
    log.info("Indexed %d stages into %s", len(chunks), settings.chroma_dir)


if __name__ == "__main__":
    main()
