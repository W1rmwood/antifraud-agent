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


# Размер кадра silero-vad при 16 кГц: модель принимает ровно 512 отсчётов (32 мс).
FRAME = 512
MIN_SPEECH_S = 0.25  # короче — шум, как min_speech_duration_ms в файловом режиме
PAD_MS = 150         # отступ вокруг речи, как speech_pad_ms в файловом режиме


class StreamSegmenter:
    """Потоковая нарезка на фразы (VADIterator): feed() принимает блоки любого размера.

    Параметры те же, что у split_speech: MIN_SILENCE_MS — пауза, закрывающая фразу,
    MAX_SPEECH_S — слишком длинную речь режем принудительно. Времена — секунды от начала потока.
    """

    def __init__(self, sr: int = SAMPLE_RATE, min_silence_ms: int = MIN_SILENCE_MS,
                 max_speech_s: float = MAX_SPEECH_S, threshold: float = 0.5):
        from silero_vad import VADIterator, load_silero_vad
        self.sr = sr
        self.max_speech = int(max_speech_s * sr)
        # Своя копия модели: у ONNX-модели есть состояние, делить его с файловым режимом нельзя.
        self._it = VADIterator(load_silero_vad(onnx=True), threshold=threshold, sampling_rate=sr,
                               min_silence_duration_ms=min_silence_ms, speech_pad_ms=PAD_MS)
        self._pending = np.zeros(0, np.float32)  # хвост, не дотянувший до целого кадра
        self._buf = np.zeros(0, np.float32)      # звук от _buf_start (нужен для вырезания фразы)
        self._buf_start = 0
        self._total = 0                          # сколько отсчётов уже отдано в VAD
        self._start: int | None = None           # начало текущей фразы (отсчёты)

    def feed(self, block: np.ndarray) -> list[tuple[float, float, np.ndarray]]:
        """Возвращает законченные фразы (start, end, audio) — их может быть 0, 1 или больше."""
        import torch
        block = np.ascontiguousarray(block, dtype=np.float32)
        self._buf = np.concatenate([self._buf, block])
        data = np.concatenate([self._pending, block])
        n = len(data) // FRAME * FRAME
        self._pending = data[n:]
        done = []
        for i in range(0, n, FRAME):
            self._total += FRAME
            event = self._it(torch.from_numpy(data[i:i + FRAME]))
            if event and "start" in event:
                self._start = max(int(event["start"]), self._buf_start)
            elif event and "end" in event and self._start is not None:
                done.append(self._cut(self._start, int(event["end"])))
                self._start = None
            elif self._start is not None and self._total - self._start >= self.max_speech:
                # речь не кончается: режем и даём итератору начать новую фразу с ближайшего кадра
                done.append(self._cut(self._start, self._total))
                self._it.triggered, self._it.temp_end, self._start = False, 0, None
        self._trim()
        return [d for d in done if d is not None]

    def flush(self) -> list[tuple[float, float, np.ndarray]]:
        """Конец потока: отдаёт незакрытую фразу, если звук оборвался посреди речи."""
        if self._start is None:
            return []
        seg = self._cut(self._start, self._total)
        self._start = None
        return [seg] if seg else []

    def _cut(self, start: int, end: int):
        end = min(end, self._buf_start + len(self._buf))
        if (end - start) / self.sr < MIN_SPEECH_S:
            return None
        chunk = self._buf[start - self._buf_start:end - self._buf_start].copy()
        return start / self.sr, end / self.sr, chunk

    def _trim(self) -> None:
        """Держим в буфере только нужное: текущую фразу или последнюю секунду (запас под отступ)."""
        keep_from = self._start if self._start is not None else self._total - self.sr
        drop = keep_from - self._buf_start
        if drop > self.sr:  # режем пачками, чтобы не копировать массив на каждом блоке
            self._buf = self._buf[drop:]
            self._buf_start = keep_from
