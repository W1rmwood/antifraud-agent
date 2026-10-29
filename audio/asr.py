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

# Whisper учился в том числе на субтитрах YouTube и на шуме, щелчках, сиренах «досочиняет»
# оттуда текст: титры, концовки роликов, описания звуков. Такие обрывки VAD иногда принимает
# за фразы (живой микрофон: 0,3–0,5 с шума), и агент потом разбирает их как слова звонящего.
_HALLUCINATIONS = re.compile(
    r"(субтитр|продолжение следует|редактор|корректор|dimatorzok|amara\.org|"
    r"подписыва\w* на\b|наш\w* канал|ставьте лайк|спасибо за (просмотр|внимание)|"
    r"в следующ\w+ (видео|выпуск|серии)|до новых встреч|всем пока|увидимся)",
    re.IGNORECASE,
)
_ANNOTATION = re.compile(r"^\s*[\[(*♪].*[\])*♪]\s*$")  # [музыка], (смех), ♪ … ♪

# Пороги по статистике сегментов Whisper. Замер: у настоящей речи no_speech_prob 0,00–0,02 и
# avg_logprob около −0,2; у выдумок на шуме no_speech_prob 0,65–0,94.
MAX_NO_SPEECH = 0.5
MIN_AVG_LOGPROB = -1.0  # стандартный порог Whisper для «модель сама не уверена»


def is_hallucination(text: str) -> bool:
    """Похоже ли это не на речь, а на то, что Whisper «досочинил» на шуме."""
    if _HALLUCINATIONS.search(text) or _ANNOTATION.match(text):
        return True
    # Описание звука капсом, как в субтитрах: «ПОЛИЦЕЙСКАЯ СИРЕНА». Речь Whisper капсом не пишет.
    letters = [ch for ch in text if ch.isalpha()]
    return len(letters) >= 6 and all(ch.isupper() for ch in letters)


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
    kept = [s.text.strip() for s in segments
            if s.no_speech_prob < MAX_NO_SPEECH and s.avg_logprob > MIN_AVG_LOGPROB]
    kept = [t for t in kept if t and not is_hallucination(t)]
    return " ".join(kept).strip()
