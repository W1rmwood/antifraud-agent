"""Сравнение моделей распознавания: WER и скорость, в исходном и телефонном качестве.

    python -m audio.bench_asr                              # small и medium, первые 5 записей
    python -m audio.bench_asr --models small medium large-v3-turbo --limit 12

Для каждой записи из data/calls/index.json: VAD → Whisper по фразам, склейка текста,
WER против эталонного транскрипта (регистр, ё/е и пунктуация не учитываются).
Телефонное качество — audio.capture.to_phone_quality (8 кГц, 300–3400 Гц, μ-law).
Итог печатается и пишется в audio/asr_report.md.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date
from pathlib import Path

import jiwer
import numpy as np

from audio import asr
from audio.capture import SAMPLE_RATE, load_channels, to_phone_quality
from audio.stream import parse_transcript
from audio.vad import split_speech

ROOT = Path(__file__).resolve().parent.parent
CALLS = ROOT / "data" / "calls"
REPORT = Path(__file__).with_name("asr_report.md")


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def recognize(channels: list[np.ndarray], model: str) -> str:
    parts = []
    for audio in channels:
        for seg in split_speech(audio):
            chunk = audio[int(seg.start * SAMPLE_RATE):int(seg.end * SAMPLE_RATE)]
            parts.append((seg.start, asr.transcribe(chunk, model)))
    parts.sort()
    return " ".join(t for _, t in parts if t)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=["small", "medium"])
    parser.add_argument("--limit", type=int, default=5, help="сколько записей взять")
    args = parser.parse_args()

    entries = json.loads((CALLS / "index.json").read_text(encoding="utf-8"))[:args.limit]
    rows = []
    for model in args.models:
        asr.load_model(model)  # загрузка не входит в замер скорости
        for quality in ("исходное", "телефонное"):
            refs, hyps, audio_s, proc_s = [], [], 0.0, 0.0
            for e in entries:
                channels = load_channels(CALLS / e["file"])
                if quality == "телефонное":
                    # реальный телефонный звонок — один канал
                    mono = np.sum(channels, axis=0) if len(channels) > 1 else channels[0]
                    channels = [to_phone_quality(mono)]
                ref = " ".join(t for _, t in parse_transcript(CALLS / e["transcript"]))
                t0 = time.perf_counter()
                hyp = recognize(channels, model)
                proc_s += time.perf_counter() - t0
                audio_s += len(channels[0]) / SAMPLE_RATE
                refs.append(normalize(ref))
                hyps.append(normalize(hyp))
                print(f"{model:>8} {quality:>10} {e['file']}: WER {jiwer.wer(refs[-1], hyps[-1]):.1%}", flush=True)
            wer = jiwer.wer(refs, hyps)
            rows.append((model, quality, len(entries), wer, audio_s, proc_s))
            print(f"== {model} {quality}: WER {wer:.1%}, RTF {proc_s / audio_s:.2f}", flush=True)

    lines = [
        f"# Сравнение моделей распознавания ({date.today().isoformat()})",
        "",
        f"Записи: первые {len(entries)} из data/calls/index.json. "
        f"Команда: `python -m audio.bench_asr --models {' '.join(args.models)} --limit {args.limit}`.",
        "RTF — время распознавания / длительность звука (меньше 1 — быстрее реального времени). "
        "Телефонное качество: каналы сведены в моно, 8 кГц, полоса 300–3400 Гц, μ-law 8 бит.",
        "",
        "| Модель | Качество | Записей | WER | Звук, с | Распознавание, с | RTF |",
        "|---|---|---|---|---|---|---|",
    ]
    for model, quality, n, wer, audio_s, proc_s in rows:
        lines.append(f"| {model} | {quality} | {n} | {wer:.1%} | {audio_s:.0f} | {proc_s:.0f} | {proc_s / audio_s:.2f} |")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nотчёт: {REPORT}")


if __name__ == "__main__":
    main()
