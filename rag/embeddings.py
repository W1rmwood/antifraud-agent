"""Эмбеддинги текста. Настоящие считает bge-m3 в Ollama, тесты подставляют свои."""

from typing import Protocol

import ollama


class Embedder(Protocol):
    """Всё, что умеет превратить список текстов в список векторов той же длины."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    """Эмбеддинги через Ollama.

    Синхронный, потому что индексация идёт офлайн. Асинхронный код
    (поиск во время звонка) вызывает его через `asyncio.to_thread`.
    """

    def __init__(self, model: str, host: str) -> None:
        self._model = model
        self._client = ollama.Client(host=host)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Один запрос на весь список текстов: так быстрее, чем по одному."""
        response = self._client.embed(model=self._model, input=texts)
        return [list(vector) for vector in response.embeddings]
