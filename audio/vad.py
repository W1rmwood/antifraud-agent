"""Нарезка речи на фразы через silero-vad."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from audio.capture import SAMPLE_RATE

# Пауза, после которой считаем фразу законченной. Меньше — быстрее реплики, но рвутся фразы.
MIN_SILENCE_MS = 600
# Whisper работает окнами по 30 с: длинную речь режем раньше.
MAX_SPEECH_S = 20.0


@dataclass(frozen=True)
class Segment:
    start: float  # секунды от начала записи
    end: float


@lru_cache(maxsize=1)
def _model():
    from silero_vad import load_silero_vad
    return load_silero_vad(onnx=True)


def split_speech(audio: np.ndarray, sr: int = SAMPLE_RATE,
                 min_silence_ms: int = MIN_SILENCE_MS,
                 threshold: float = 0.5) -> list[Segment]:
    """Возвращает отрезки речи в порядке времени."""
    import torch
    from silero_vad import get_speech_timestamps

    if audio.size == 0:
        return []
    stamps = get_speech_timestamps(
        torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32)),
        _model(),
        sampling_rate=sr,
        threshold=threshold,
        min_speech_duration_ms=250,
        min_silence_duration_ms=min_silence_ms,
        max_speech_duration_s=MAX_SPEECH_S,
        speech_pad_ms=150,
    )
    return [Segment(s["start"] / sr, s["end"] / sr) for s in stamps]
