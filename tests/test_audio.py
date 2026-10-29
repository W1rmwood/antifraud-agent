"""Тесты звука. Распознавание Whisper — только при RUN_ASR=1 (нужна скачанная модель, ~минута)."""
import asyncio
import os

import numpy as np
import pytest
import soundfile as sf

from audio import listen
from audio.capture import SAMPLE_RATE, load_channels, resample, to_phone_quality
from audio.vad import split_speech
from contracts.models import AudioSource, Speaker, Utterance


def collect(source, call_id="t", **kw):
    async def run():
        return [u async for u in listen(source, call_id, **kw)]
    return asyncio.run(run())


def test_resample_and_load(tmp_path):
    sr = 44_100
    stereo = np.zeros((sr, 2), np.float32)
    path = tmp_path / "s.wav"
    sf.write(str(path), stereo, sr)
    channels = load_channels(path)
    assert len(channels) == 2
    assert all(len(c) == SAMPLE_RATE for c in channels)
    assert len(resample(np.zeros(8000, np.float32), 8000)) == SAMPLE_RATE


def test_phone_quality_keeps_length():
    tone = np.sin(2 * np.pi * 1000 * np.arange(SAMPLE_RATE) / SAMPLE_RATE).astype(np.float32) * 0.5
    out = to_phone_quality(tone)
    assert out.dtype == np.float32
    assert abs(len(out) - len(tone)) <= 2
    assert np.abs(out).max() <= 1.0


def test_vad_silence_has_no_speech():
    assert split_speech(np.zeros(SAMPLE_RATE * 2, np.float32)) == []


def test_vad_finds_phrases_in_call():
    channels = load_channels("data/calls/011.wav")
    segments = split_speech(channels[0])
    assert 3 <= len(segments) <= 8
    assert all(s.end > s.start for s in segments)
    assert segments == sorted(segments, key=lambda s: s.start)


def test_listen_from_transcript():
    utts = collect(AudioSource(kind="file", path="data/calls/001.txt"), "c001")
    assert len(utts) == 12
    assert [u.seq for u in utts] == list(range(12))
    assert utts[0].speaker == Speaker.caller and utts[1].speaker == Speaker.user
    assert all(isinstance(u, Utterance) and u.call_id == "c001" for u in utts)
    assert all(a.t_end <= b.t_start for a, b in zip(utts, utts[1:]))


def test_listen_missing_file():
    with pytest.raises(FileNotFoundError):
        collect(AudioSource(kind="file", path="data/calls/nope.wav"))


@pytest.mark.skipif(os.environ.get("RUN_ASR") != "1", reason="RUN_ASR=1 включает распознавание")
def test_listen_wav_stereo_and_mono():
    stereo = collect(AudioSource(kind="file", path="data/calls/012.wav"), "c012")
    assert {u.speaker for u in stereo} == {Speaker.caller, Speaker.user}
    assert [u.seq for u in stereo] == list(range(len(stereo)))
    assert all(a.t_start <= b.t_start for a, b in zip(stereo, stereo[1:]))
    assert "суббот" in " ".join(u.text.lower() for u in stereo)

    mono = collect(AudioSource(kind="file", path="data/calls/011.wav"), "c011")
    assert mono and {u.speaker for u in mono} == {Speaker.unknown}


@pytest.mark.parametrize("text", [
    "ПОЛИЦЕЙСКАЯ СИРЕНА",                 # описание звука капсом, как в субтитрах
    "Увидимся в следующем видео!",
    "Подписывайтесь на наш канал!",
    "Редактор субтитров Н.Закомолдина",
    "Корректор А.Егорова",
    "Продолжение следует...",
    "Спасибо за просмотр.",
    "[музыка]",
    "(смех)",
])
def test_whisper_hallucinations_are_dropped(text):
    from audio.asr import is_hallucination
    assert is_hallucination(text)


@pytest.mark.parametrize("text", [
    "Алло.", "Здравствуйте.", "Да.", "ЦБ", "Сбербанк, служба безопасности.",
    "не могли бы вы скинуть номер своей карты.",
    "Переведите деньги на резервный счёт ЦБ РФ.",
    "Это МВД, вам звонит майор Кузнецов.",
])
def test_real_phrases_are_kept(text):
    from audio.asr import is_hallucination
    assert not is_hallucination(text)


@pytest.mark.skipif(os.environ.get("RUN_ASR") != "1", reason="RUN_ASR=1 включает распознавание")
def test_noise_bursts_do_not_become_phrases():
    """Полсекунды шума или «сирены» с микрофона — не реплика (раньше выходило «Подписывайтесь на наш канал!»)."""
    from audio.asr import transcribe
    rng = np.random.default_rng(0)
    t = np.arange(int(SAMPLE_RATE * 0.4)) / SAMPLE_RATE
    siren = (0.3 * np.sin(2 * np.pi * (700 + 300 * np.sin(2 * np.pi * 3 * t)) * t)).astype(np.float32)
    noise = (rng.standard_normal(SAMPLE_RATE) * 0.005).astype(np.float32)
    assert transcribe(siren) == "" and transcribe(noise) == ""
