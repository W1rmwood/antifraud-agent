"""Тесты живых режимов (mic/loopback) на подставном источнике блоков — настоящий микрофон не трогаем.

Распознавание Whisper — только при RUN_ASR=1; иначе asr.transcribe подменяется фейком.
"""
import asyncio
import os
import time

import numpy as np
import pytest

from audio import asr
from audio.capture import LOOPBACK_ENV, SAMPLE_RATE, find_input_device, load_channels
from audio.stream import listen
from audio.vad import StreamSegmenter, split_speech
from contracts.models import AudioSource, Speaker

BLOCK = int(SAMPLE_RATE * 0.03)
REAL_ASR = os.environ.get("RUN_ASR") == "1"


def wav_blocks(path="data/calls/011.wav", pace=0.0, tail_s=1.5, state=None):
    """Фабрика потока блоков из WAV; pace=1.0 — в темпе живого звука.

    state["hang"]=True — после файла поток «не кончается», как микрофон; state["closed"] ставится при закрытии.
    """
    audio = load_channels(path)[0]
    audio = np.concatenate([audio, np.zeros(int(tail_s * SAMPLE_RATE), np.float32)])

    def factory(source):
        async def gen():
            try:
                t0 = time.monotonic()
                if state is not None:
                    state["t0"] = t0  # начало «захвата»: от него отсчитываются t_start/t_end
                for i in range(0, len(audio), BLOCK):
                    if pace:
                        await asyncio.sleep(max(0.0, t0 + (i + BLOCK) / SAMPLE_RATE * pace - time.monotonic()))
                    yield audio[i:i + BLOCK]
                    await asyncio.sleep(0)
                while state is not None and state.get("hang"):
                    await asyncio.sleep(0.01)
            finally:
                if state is not None:
                    state["closed"] = True
        return gen()
    return factory


@pytest.fixture
def fake_asr(monkeypatch):
    if not REAL_ASR:
        monkeypatch.setattr(asr, "transcribe", lambda audio, size=None, **kw: f"фраза {len(audio)}")


@pytest.mark.parametrize("kind", ["mic", "loopback"])
def test_live_phrases_in_order(fake_asr, kind):
    async def go():
        return [u async for u in listen(AudioSource(kind=kind), "live", blocks=wav_blocks())]
    utts = asyncio.run(go())
    assert 3 <= len(utts) <= 8
    assert [u.seq for u in utts] == list(range(len(utts)))
    assert {u.speaker for u in utts} == {Speaker.unknown}
    assert all(u.t_end > u.t_start for u in utts)
    assert all(a.t_end <= b.t_start + 0.4 for a, b in zip(utts, utts[1:]))
    assert all(u.call_id == "live" for u in utts)


def test_stream_segmenter_matches_file_mode():
    audio = load_channels("data/calls/011.wav")[0]
    audio = np.concatenate([audio, np.zeros(SAMPLE_RATE * 2, np.float32)])
    seg = StreamSegmenter()
    phrases = []
    for i in range(0, len(audio), BLOCK):
        phrases += seg.feed(audio[i:i + BLOCK])
    phrases += seg.flush()
    reference = split_speech(audio)
    assert abs(len(phrases) - len(reference)) <= 1
    assert phrases[0][0] == pytest.approx(reference[0].start, abs=0.5)


def test_empty_transcription_skipped(monkeypatch):
    calls = iter(range(100))
    monkeypatch.setattr(asr, "transcribe", lambda a, s=None, **kw: "" if next(calls) % 2 == 0 else "текст")

    async def go():
        return [u async for u in listen(AudioSource(kind="mic"), "x", blocks=wav_blocks())]
    utts = asyncio.run(go())
    assert utts and [u.seq for u in utts] == list(range(len(utts)))


def test_closed_on_cancel(fake_asr):
    async def go():
        state = {"hang": True}

        async def consume():
            async for _ in listen(AudioSource(kind="mic"), "c", blocks=wav_blocks(state=state)):
                pass
        task = asyncio.create_task(consume())
        await asyncio.sleep(3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return state
    assert asyncio.run(go()).get("closed")


def test_closed_on_consumer_exit(fake_asr):
    async def go():
        state = {"hang": True}
        gen = listen(AudioSource(kind="mic"), "c", blocks=wav_blocks(state=state))
        async for _ in gen:
            break
        await gen.aclose()
        return state
    assert asyncio.run(go()).get("closed")


def test_capture_error_propagates():
    def broken(source):
        async def gen():
            raise RuntimeError("нет устройства")
            yield
        return gen()

    async def go():
        return [u async for u in listen(AudioSource(kind="loopback"), "e", blocks=broken)]
    with pytest.raises(RuntimeError, match="нет устройства"):
        asyncio.run(go())


def test_loopback_device_errors_have_hint():
    with pytest.raises(RuntimeError, match="BlackHole"):
        find_input_device("loopback", environ={})
    assert find_input_device("mic") is None
    with pytest.raises(RuntimeError, match=LOOPBACK_ENV):
        find_input_device("loopback", environ={LOOPBACK_ENV: "нет-такого-устройства-xyz"})


@pytest.mark.skipif(not REAL_ASR, reason="RUN_ASR=1 включает распознавание")
def test_live_realtime_latency():
    """Задержка «конец фразы → Utterance» на потоке в темпе реального времени."""
    asr.transcribe(np.zeros(SAMPLE_RATE, np.float32) + 0.01, asr.DEFAULT_MODEL)  # прогрев: в звонке модель уже загружена

    async def go():
        state = {}
        out = []
        async for u in listen(AudioSource(kind="mic"), "lat", blocks=wav_blocks(pace=1.0, state=state)):
            out.append((u, time.monotonic() - state["t0"] - u.t_end))
        return out
    out = asyncio.run(go())
    assert out and {u.speaker for u, _ in out} == {Speaker.unknown}
    delays = sorted(d for _, d in out)
    print("задержки, с:", [round(d, 2) for d in delays])
    assert delays[-1] <= 3.5
