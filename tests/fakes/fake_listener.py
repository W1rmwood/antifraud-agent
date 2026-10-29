"""Слушатель, который читает текстовую расшифровку вместо звука.

Выполняет контракт `contracts.Listener`: `FakeListener()(source, call_id)`
выдаёт `Utterance`, так что агент не отличит его от настоящего модуля звука.
Время фраз условное: длительность пропорциональна числу слов.
"""

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path

from contracts import AudioSource, Speaker, Utterance

# Строка вида "caller: текст". Регистр имени говорящего не важен, пробелы вокруг двоеточия — тоже.
_LINE = re.compile(r"^\s*(caller|user|unknown)\s*:\s*(.+?)\s*$", re.IGNORECASE)

WORDS_PER_SECOND = 2.5  # примерный темп разговорной речи
PAUSE_S = 0.4  # пауза между фразами


def parse_transcript(text: str) -> list[tuple[Speaker, str]]:
    """Разбирает строки `говорящий: фраза`. Пустые строки пропускаются, всё остальное — ошибка."""
    lines: list[tuple[Speaker, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        match = _LINE.match(raw)
        if match is None:
            raise ValueError(f"line {number}: expected 'speaker: text', got {raw!r}")
        lines.append((Speaker(match.group(1).lower()), match.group(2)))
    return lines


class FakeListener:
    """Проигрывает файл расшифровки (`source.path`) или список строк, переданный в конструктор.

    `delay_s` — пауза между фразами, чтобы изобразить живой звонок; 0 — мгновенно.
    """

    def __init__(self, lines: list[tuple[Speaker, str]] | None = None, delay_s: float = 0.0) -> None:
        self._lines = lines
        self._delay_s = delay_s

    async def __call__(self, source: AudioSource, call_id: str) -> AsyncIterator[Utterance]:
        # Асинхронный генератор: вызов возвращает итератор, который читают через `async for`,
        # ровно как настоящий audio.stream.listen.
        lines = self._lines
        if lines is None:
            if source.path is None:
                raise ValueError("FakeListener needs either lines or source.path to a transcript")
            lines = parse_transcript(Path(source.path).read_text(encoding="utf-8"))

        t = 0.0
        for seq, (speaker, text) in enumerate(lines):
            # Не короче полсекунды: даже «да» занимает какое-то время.
            duration = max(len(text.split()) / WORDS_PER_SECOND, 0.5)
            if seq and self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield Utterance(
                call_id=call_id,
                seq=seq,
                speaker=speaker,
                text=text,
                t_start=round(t, 2),
                t_end=round(t + duration, 2),
            )
            t += duration + PAUSE_S
