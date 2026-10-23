"""Собирает тестовые звонки .wav из эталонных транскриптов синтезом речи (macOS `say`, голос Milena).

    python data/calls/make_tts_calls.py            # все записи из index.json, у которых нет .wav
    python data/calls/make_tts_calls.py --force    # пересобрать все

Это заготовки для проверки конвейера, а не постановочные звонки из ТЗ: их нужно заменить
живыми записями (тот же файл, тот же транскрипт, та же строка в index.json).
Стерео: левый канал — caller, правый — user; моно — обе роли в одном канале.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

CALLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CALLS_DIR.parent.parent))

from audio.stream import parse_transcript  # noqa: E402
from contracts.models import CallIndexEntry, Speaker  # noqa: E402

SR = 16_000
VOICE = "Milena"
RATE = {Speaker.caller: 190, Speaker.user: 165, Speaker.unknown: 180}
GAP_S = 0.8


def synth(text: str, rate: int, tmp: Path) -> np.ndarray:
    out = tmp / "line.aiff"
    subprocess.run(["say", "-v", VOICE, "-r", str(rate), "-o", str(out),
                    f"--data-format=BEI16@{SR}", text], check=True)
    audio, sr = sf.read(str(out), dtype="float32")
    assert sr == SR
    return audio


def build(entry: CallIndexEntry, force: bool) -> bool:
    wav = CALLS_DIR / entry.file
    if wav.exists() and not force:
        return False
    lines = parse_transcript(CALLS_DIR / entry.transcript)
    left, right = [np.zeros(int(SR * 0.5), np.float32)], [np.zeros(int(SR * 0.5), np.float32)]
    with tempfile.TemporaryDirectory() as tmp:
        for speaker, text in lines:
            voice = synth(text, RATE[speaker], Path(tmp))
            silence = np.zeros_like(voice)
            gap = np.zeros(int(SR * GAP_S), np.float32)
            if speaker == Speaker.user:
                left += [silence, gap]
                right += [voice, gap]
            else:
                left += [voice, gap]
                right += [silence, gap]
    left_a, right_a = np.concatenate(left), np.concatenate(right)
    if entry.channels == "stereo":
        data = np.stack([left_a, right_a], axis=1)
    else:
        data = left_a + right_a
    sf.write(str(wav), data * 0.8, SR, subtype="PCM_16")
    print(f"{entry.file}: {len(lines)} реплик, {len(left_a) / SR:.1f} с, {entry.channels}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    entries = [CallIndexEntry.model_validate(e)
               for e in json.loads((CALLS_DIR / "index.json").read_text(encoding="utf-8"))]
    built = sum(build(e, args.force) for e in entries)
    print(f"собрано {built} из {len(entries)}")


if __name__ == "__main__":
    main()
