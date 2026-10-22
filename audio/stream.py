"""Поток реплик из звука (контракт №2): listen(source, call_id) → AsyncIterator[Utterance].

Тяжёлые шаги (чтение файла, VAD, Whisper) идут в отдельном потоке через run_in_executor,
event loop агента не блокируется.
"""
from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import AsyncIterator

from audio import asr
from audio.capture import SAMPLE_RATE, load_channels
from audio.vad import split_speech
from contracts.models import AudioSource, Speaker, Utterance

# Один поток: модель Whisper не рассчитана на параллельные вызовы, а CPU и так занят целиком.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")

_TRANSCRIPT_LINE = re.compile(r"^\s*(caller|user|unknown)\s*:\s*(.+?)\s*$", re.IGNORECASE)


async def listen(source: AudioSource, call_id: str, *, realtime: bool = False,
                 model_size: str | None = None) -> AsyncIterator[Utterance]:
    """Выдаёт реплики звонка в порядке времени.

    kind="file": .wav/.flac/.ogg — VAD + Whisper; стерео: левый канал caller, правый user;
    моно: unknown. Файл .txt — эталонный транскрипт («caller: текст» построчно), без распознавания.
    realtime=True — реплика выдаётся не раньше, чем фраза «прозвучала» бы в живом звонке
    (для демо и замера задержки); по умолчанию файл обрабатывается так быстро, как получится.
    """
    if source.kind != "file":
        raise NotImplementedError(f"режим {source.kind!r} — опция после КТ3, пока только file")
    if not source.path:
        raise ValueError("для kind='file' нужен path")
    path = Path(source.path)
    if not path.exists():
        raise FileNotFoundError(path)

    if path.suffix.lower() == ".txt":
        async for u in _listen_transcript(path, call_id, realtime):
            yield u
        return

    loop = asyncio.get_running_loop()
    channels = await loop.run_in_executor(_executor, load_channels, path)
    speakers = [Speaker.caller, Speaker.user] if len(channels) == 2 else [Speaker.unknown]

    phrases = []
    for audio, speaker in zip(channels, speakers):
        for seg in await loop.run_in_executor(_executor, split_speech, audio):
            phrases.append((seg, speaker, audio))
    phrases.sort(key=lambda p: (p[0].start, p[0].end))

    size = model_size or asr.DEFAULT_MODEL
    t0 = loop.time()
    seq = 0
    for seg, speaker, audio in phrases:
        if realtime:
            await asyncio.sleep(max(0.0, t0 + seg.end - loop.time()))
        chunk = audio[int(seg.start * SAMPLE_RATE):int(seg.end * SAMPLE_RATE)]
        text = await loop.run_in_executor(_executor, asr.transcribe, chunk, size)
        if not text:
            continue
        yield Utterance(call_id=call_id, seq=seq, speaker=speaker, text=text,
                        t_start=round(seg.start, 2), t_end=round(seg.end, 2))
        seq += 1


def parse_transcript(path: str | Path) -> list[tuple[Speaker, str]]:
    """Читает эталонный транскрипт: строки «caller: …», «user: …», «unknown: …»."""
    lines = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = _TRANSCRIPT_LINE.match(line)
        if m:
            lines.append((Speaker(m.group(1).lower()), m.group(2)))
    return lines


async def _listen_transcript(path: Path, call_id: str, realtime: bool) -> AsyncIterator[Utterance]:
    """Реплики из текста: длительность фразы оценивается по числу слов (~2,5 слова/с)."""
    t = 0.0
    for seq, (speaker, text) in enumerate(parse_transcript(path)):
        duration = max(1.0, len(text.split()) / 2.5)
        if realtime:
            await asyncio.sleep(duration)
        yield Utterance(call_id=call_id, seq=seq, speaker=speaker, text=text,
                        t_start=round(t, 2), t_end=round(t + duration, 2))
        t += duration + 0.5
