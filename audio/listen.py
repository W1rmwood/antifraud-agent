"""Печатает реплики звонка в формате Utterance (одна JSON-строка на реплику).

    python -m audio.listen --file data/calls/001.wav
    python -m audio.listen --file data/calls/001.wav --realtime --model medium

С --realtime в stderr пишется задержка «конец фразы → Utterance» (цель ≤ 3 с).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from audio.stream import listen
from contracts.models import AudioSource


async def _run(args: argparse.Namespace) -> None:
    call_id = args.call_id or Path(args.file).stem
    started = time.monotonic()
    delays = []
    async for u in listen(AudioSource(kind="file", path=args.file), call_id,
                          realtime=args.realtime, model_size=args.model):
        print(u.model_dump_json(), flush=True)
        if args.realtime:
            delay = time.monotonic() - started - u.t_end
            delays.append(delay)
            print(f"  задержка {delay:.2f} с", file=sys.stderr, flush=True)
    total = time.monotonic() - started
    print(f"готово за {total:.1f} с", file=sys.stderr)
    if delays:
        delays.sort()
        p95 = delays[min(len(delays) - 1, int(round(0.95 * (len(delays) - 1))))]
        print(f"задержка: среднее {sum(delays) / len(delays):.2f} с, p95 {p95:.2f} с, "
              f"макс {delays[-1]:.2f} с", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", required=True, help="аудиофайл (.wav) или транскрипт (.txt)")
    parser.add_argument("--call-id", help="call_id (по умолчанию имя файла)")
    parser.add_argument("--model", default=None, help="модель Whisper: small, medium, …")
    parser.add_argument("--realtime", action="store_true", help="выдавать реплики в темпе живого звонка")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
