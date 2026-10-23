"""Interfaces of the modules the agent depends on but does not own.

The agent is written against these protocols, not against concrete modules.
Real implementations (audio/, storage/) and test fakes (tests/fakes/) satisfy
them structurally, so swapping one for another is a single config change.
"""

from collections.abc import AsyncIterator
from typing import Protocol

from contracts.models import AudioSource, CallCase, NumberInfo, Utterance


class Listener(Protocol):
    """Turns an audio source into a stream of utterances.

    Implemented as `async def listen(source, call_id)` in audio/.
    """

    def __call__(self, source: AudioSource, call_id: str) -> AsyncIterator[Utterance]: ...


class Storage(Protocol):
    """Persistent history of numbers and calls.

    Implemented as module-level functions in storage/db.py: a module with
    matching functions satisfies this protocol. Methods are synchronous, so
    async callers must run them via `asyncio.to_thread`.
    """

    def init_db(self, path: str) -> None: ...

    def lookup_number(self, phone: str) -> NumberInfo:
        """Return history of the number; an unknown number has calls_count=0."""
        ...

    def save_case(self, case: CallCase) -> str: ...

    def get_cases_by_number(self, phone: str) -> list[CallCase]: ...

    def list_recent_cases(self, limit: int = 20) -> list[CallCase]: ...

    def tag_number(self, phone: str, tag: str) -> None: ...
