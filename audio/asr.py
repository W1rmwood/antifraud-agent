"""Распознавание речи: faster-whisper, русский язык, CPU int8.

Модель задаётся переменной окружения ANTIFRAUD_WHISPER_MODEL (small по умолчанию);
выбор между small и medium — по таблице audio/asr_report.md.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

import numpy as np

from audio.capture import SAMPLE_RATE

DEFAULT_MODEL = os.environ.get("ANTIFRAUD_WHISPER_MODEL", "small")

# Whisper на тишине и шуме «досочиняет» титры из обучающих данных.
_HALLUCINATIONS = re.compile(
    r"(субтитр|продолжение следует|редактор субтитров|корректор|dimatorzok|"
    r"спасибо за просмотр|подписывайтесь на канал|amara\.org)",
    re.IGNORECASE,
)


@lru_cache(maxsize=2)
def load_model(size: str = DEFAULT_MODEL):
    from faster_whisper import WhisperModel
    return WhisperModel(size, device="cpu", compute_type="int8",
                        cpu_threads=max(1, (os.cpu_count() or 4) - 1))


def transcribe(audio: np.ndarray, model_size: str = DEFAULT_MODEL, beam_size: int = 5) -> str:
    """Распознаёт одну фразу (16 кГц float32). Пустая строка — речи нет."""
    if audio.size < SAMPLE_RATE * 0.2:
        return ""
    segments, _ = load_model(model_size).transcribe(
        audio,
        language="ru",
        beam_size=beam_size,
        vad_filter=False,               # нарезку уже сделал silero-vad
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
    )
    text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.8).strip()
    if _HALLUCINATIONS.search(text):
        return ""
    return text
