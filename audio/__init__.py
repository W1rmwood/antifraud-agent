"""Звук → текстовые реплики (контракт №2).

    from audio import listen
    async for u in listen(AudioSource(kind="file", path="data/calls/001.wav"), "c001"): ...

Реализация — в audio/stream.py. Не импортируйте модуль audio.listen: это CLI
(python -m audio.listen), и импорт затёр бы функцию listen в пакете.
"""
from audio.stream import listen

__all__ = ["listen"]
