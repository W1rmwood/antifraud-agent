"""Подставной агент для remote-звонка: читает PCM из stdin, как agent.run --source remote, но без моделей.

    python -m tests.fakes.fake_remote_agent --call-id c1 [--phone +7…]

Пишет в лог ($ANTIFRAUD_RUNS_DIR/<call_id>.jsonl) call_started, а на конце stdin — summary
и call_ended, и выходит с кодом 0 — как настоящий агент.
Всё прочитанное сохраняет в <call_id>.pcm рядом с логом: тест сверяет, что звук дошёл ровно один раз.
"""

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from contracts import AgentEvent, AudioSource, EventType


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--call-id", required=True)
    parser.add_argument("--phone", default=None)
    args = parser.parse_args()
    runs = Path(os.environ["ANTIFRAUD_RUNS_DIR"])
    log = runs / f"{args.call_id}.jsonl"
    seq = 0

    def emit(type_: EventType, payload: dict) -> None:
        nonlocal seq
        event = AgentEvent(call_id=args.call_id, seq=seq, ts=datetime.now(UTC), type=type_, payload=payload)
        with log.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
        seq += 1

    emit(EventType.call_started, {"phone": args.phone, "source": AudioSource(kind="remote").model_dump()})
    received = bytearray()
    while chunk := sys.stdin.buffer.read1(65536):
        received += chunk
    (runs / f"{args.call_id}.pcm").write_bytes(bytes(received))
    emit(EventType.summary, {"call_id": args.call_id, "summary": f"принято {len(received)} байт"})
    emit(EventType.call_ended, {})


if __name__ == "__main__":
    main()
