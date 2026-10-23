"""Text embeddings. The real one is bge-m3 in Ollama; tests pass their own."""

from typing import Protocol

import ollama


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    """Synchronous: indexing runs offline, and async callers wrap it in `asyncio.to_thread`."""

    def __init__(self, model: str, host: str) -> None:
        self._model = model
        self._client = ollama.Client(host=host)

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embed(model=self._model, input=texts)
        return [list(vector) for vector in response.embeddings]
