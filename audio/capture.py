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


# ---------------------------------------------------------------- живой звук (mic / loopback)

BLOCK_MS = 30  # размер блока, который колбэк sounddevice отдаёт в очередь
LOOPBACK_ENV = "ANTIFRAUD_LOOPBACK_DEVICE"

_LOOPBACK_HINT = (
    "Системный звук (loopback) нужно подключить как устройство ввода: "
    "macOS — установите BlackHole (brew install blackhole-2ch) и соберите Multi-Output устройство; "
    "Windows — включите «Стерео микшер» или используйте WASAPI loopback; "
    "Linux (PulseAudio) — используйте monitor-источник (`pactl list short sources`, имя вида *.monitor). "
    f"Имя (или его часть) устройства задаётся переменной {LOOPBACK_ENV}."
)


def find_input_device(source_kind: str, environ: dict | None = None) -> int | None:
    """Индекс устройства ввода: для mic — None (системное по умолчанию), для loopback — по имени из окружения."""
    import os
    if source_kind == "mic":
        return None
    env = os.environ if environ is None else environ
    name = (env.get(LOOPBACK_ENV) or "").strip()
    if not name:
        raise RuntimeError(f"Не задана переменная {LOOPBACK_ENV}. {_LOOPBACK_HINT}")
    import sounddevice as sd
    wanted = name.lower()
    inputs = [(i, d["name"]) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    for i, dev_name in inputs:
        if wanted in dev_name.lower():
            return i
    available = ", ".join(n for _, n in inputs) or "нет"
    raise RuntimeError(f"Устройство ввода {name!r} не найдено (доступны: {available}). {_LOOPBACK_HINT}")


async def open_capture(source, block_ms: int = BLOCK_MS):
    """Асинхронный поток блоков float32 моно 16 кГц с микрофона или loopback-устройства.

    Колбэк sounddevice вызывается из чужого потока, поэтому блоки попадают в asyncio.Queue
    через loop.call_soon_threadsafe. Поток закрывается при закрытии генератора
    (отмена задачи или выход потребителя из async for).
    """
    import asyncio
    try:
        import sounddevice as sd
    except ImportError as e:  # pragma: no cover - зависит от окружения
        raise RuntimeError("Для mic/loopback нужен пакет sounddevice: pip install sounddevice") from e

    device = find_input_device(source.kind)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    info = sd.query_devices(device, "input")
    channels = max(1, min(int(info["max_input_channels"]), 2))

    def make_callback(rate: int):
        def callback(indata, frames, time_info, status):
            block = indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0]
            if rate != SAMPLE_RATE:
                block = resample(block, rate)
            loop.call_soon_threadsafe(queue.put_nowait, np.array(block, dtype=np.float32))
        return callback

    stream = None
    last_error: Exception | None = None
    # Сначала просим 16 кГц у системы; если устройство не умеет — берём родную частоту и ресемплируем.
    for rate in (SAMPLE_RATE, int(info["default_samplerate"])):
        try:
            stream = sd.InputStream(samplerate=rate, channels=channels, dtype="float32",
                                    blocksize=int(rate * block_ms / 1000), device=device,
                                    callback=make_callback(rate))
            stream.start()
            break
        except Exception as e:
            stream, last_error = None, e
    if stream is None:
        raise RuntimeError(f"Не удалось открыть устройство ввода {info['name']!r}: {last_error}. "
                           "Проверьте разрешение на микрофон в настройках системы.") from last_error
    try:
        while True:
            yield await queue.get()
    finally:
        stream.stop()
        stream.close()
