"""Захват звука: чтение файла и приведение к 16 кГц float32 по каналам.

Договорённость по каналам (ТЗ, этап 1): в стерео-файле левый канал — мошенник (caller),
правый — мы (user). Моно-файл → speaker = unknown.
"""
from __future__ import annotations

from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, resample_poly, sosfilt

SAMPLE_RATE = 16_000


def resample(audio: np.ndarray, sr_from: int, sr_to: int = SAMPLE_RATE) -> np.ndarray:
    if sr_from == sr_to:
        return audio.astype(np.float32, copy=False)
    g = gcd(sr_from, sr_to)
    return resample_poly(audio, sr_to // g, sr_from // g).astype(np.float32)


def load_channels(path: str | Path) -> list[np.ndarray]:
    """Читает аудиофайл (wav/flac/ogg) и возвращает каналы, каждый — float32 16 кГц.

    Больше двух каналов не бывает в наших записях: берём первые два.
    """
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    channels = [data[:, i] for i in range(min(data.shape[1], 2))]
    return [resample(ch, sr) for ch in channels]


def to_phone_quality(audio: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Имитирует телефонный канал: полоса 300–3400 Гц, 8 кГц, μ-law 8 бит, обратно в sr.

    Используется для замера WER «в телефонном качестве».
    """
    narrow = resample(audio, sr, 8_000)
    sos = butter(4, [300, 3400], btype="bandpass", fs=8_000, output="sos")
    narrow = sosfilt(sos, narrow)
    # μ-law компандирование с квантованием до 8 бит (как в G.711)
    mu = 255.0
    x = np.clip(narrow, -1.0, 1.0)
    y = np.sign(x) * np.log1p(mu * np.abs(x)) / np.log1p(mu)
    y = np.round(y * 127) / 127
    narrow = np.sign(y) * (np.power(1 + mu, np.abs(y)) - 1) / mu
    return resample(narrow.astype(np.float32), 8_000, sr)
