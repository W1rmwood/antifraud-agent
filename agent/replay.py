"""Проигрывание записанного звонка в лог data/runs/<call_id>.jsonl с настоящими паузами.

    python -m agent.replay data/mock_events/example.jsonl --call-id demo [--speed 2]

Зачем: UI читает события только из лога (ТЗ, контракт №3). Проигрыватель пишет
записанный звонок туда же и с теми же паузами, что были в живом звонке, — и интерфейс
не отличит его от работающего агента. Так экраны разрабатываются без модели,
а на защите запасной вариант (data/runs/demo.jsonl) идёт тем же кодом UI.

call_id, seq и ts в событиях переписываются: call_id — на заданный, seq — заново с 0,
ts — на текущее время (с исходными промежутками между событиями).
"""

import argparse
import asyncio
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

from agent.events import EventSink
from agent.ui_api import RUNS_DIR, read_events

MAX_PAUSE_S = 15.0  # длинные паузы в записи (модель думала) сокращаем, чтобы демо не провисало


async def replay(source: Path, call_id: str, speed: float = 1.0, runs_dir: Path = RUNS_DIR) -> int:
    """Пишет события из `source` в лог звонка `call_id`. Возвращает число записанных событий."""
    events = read_events(source)
    sink = EventSink(call_id, log_path=runs_dir / f"{call_id}.jsonl")
    previous: datetime | None = None
    for event in events:
        if previous is not None:
            pause = (event.ts - previous).total_seconds() / speed
            await asyncio.sleep(min(max(pause, 0.0), MAX_PAUSE_S))
        previous = event.ts
        await sink.emit(event.type, event.payload)
    return len(events)


def main() -> None:
    """Точка входа `python -m agent.replay <events.jsonl>`."""
    parser = argparse.ArgumentParser(description="Replay a recorded call into data/runs/<call-id>.jsonl.")
    parser.add_argument("events", help="recorded events, e.g. data/mock_events/example.jsonl")
    parser.add_argument("--call-id", default=f"replay-{datetime.now(UTC):%H%M%S}")
    parser.add_argument("--speed", type=float, default=1.0, help="2 — вдвое быстрее, 0.5 — вдвое медленнее")
    args = parser.parse_args()
    if args.speed <= 0:
        parser.error("--speed должен быть больше нуля")
    # «Стоп» из UI (SIGTERM) просто прекращает проигрывание.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    # Та же переменная, что у агента (Settings.runs_dir): UI задаёт папку логов одинаково для обоих.
    runs_dir = Path(os.environ.get("ANTIFRAUD_RUNS_DIR", RUNS_DIR))
    count = asyncio.run(replay(Path(args.events), args.call_id, args.speed, runs_dir))
    print(f"проиграно {count} событий в {runs_dir / (args.call_id + '.jsonl')}", file=sys.stderr)


if __name__ == "__main__":
    main()
