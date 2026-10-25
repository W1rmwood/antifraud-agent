"""Детерминированный эмбеддер для тестов: хэшированный «мешок» обрезанных слов, без модели."""

import hashlib
import math

from rag.chunks import tokenize

DIM = 256


class FakeEmbedder:
    """Подменяет bge-m3: одинаковый текст всегда даёт одинаковый вектор.

    Тексты с общими словами получаются близкими по косинусу. Этого хватает,
    чтобы проверить код векторного поиска, но не его качество.
    """

    def __init__(self) -> None:
        self.calls = 0  # сколько раз звали embed: тесты проверяют, что лишних вызовов нет

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        """Каждое слово через md5 попадает в одну из DIM ячеек; вектор нормируется до длины 1."""
        # md5, а не встроенный hash(): hash() строк меняется от запуска к запуску,
        # и тесты стали бы недетерминированными.
        vector = [0.0] * DIM
        for token in tokenize(text):
            vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]
