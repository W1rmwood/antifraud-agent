"""Куда уходят события агента: в очередь UI и в лог звонка data/runs/<call_id>.jsonl."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contracts import AgentEvent, EventType


class EventSink:
    """Приёмник событий одного звонка.

    Каждое событие получает время, сохраняется в `events` (для тестов и eval),
    дописывается строкой в лог и, если есть очередь, отправляется в UI.
    Очередь и лог необязательны: eval работает без обоих.
    """

    def __init__(
        self, call_id: str, queue: asyncio.Queue[AgentEvent] | None = None, log_path: Path | None = None
    ) -> None:
        self.call_id = call_id
        self._queue = queue
        self._log_path = log_path
        self.events: list[AgentEvent] = []
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # Повторный прогон с тем же call_id начинает лог с чистого листа,
            # а не дописывает в старый.
            log_path.write_text("", encoding="utf-8")

    async def emit(self, type_: EventType, payload: dict[str, Any]) -> AgentEvent:
        """Создаёт событие и рассылает его во все приёмники. Возвращает созданное событие."""
        event = AgentEvent(call_id=self.call_id, ts=datetime.now(UTC), type=type_, payload=payload)
        self.events.append(event)
        if self._log_path is not None:
            # Одна короткая строка на событие: запись достаточно дешёвая,
            # чтобы не выносить её из event loop в отдельный поток.
            with self._log_path.open("a", encoding="utf-8") as log:
                log.write(event.model_dump_json() + "\n")
        if self._queue is not None:
            await self._queue.put(event)
        return event
