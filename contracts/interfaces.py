"""Интерфейсы модулей, от которых зависит агент, но которыми он не владеет.

Агент написан против этих протоколов, а не против конкретных модулей.
Настоящие реализации (audio/, storage/) и фейки для тестов (tests/fakes/)
подходят к ним структурно, без наследования. Поэтому заменить одно на другое —
это одна настройка, а не правка кода агента.
"""

from collections.abc import AsyncIterator
from typing import Protocol

from contracts.models import AudioSource, CallCase, NumberInfo, Utterance


class Listener(Protocol):
    """Превращает источник звука в поток фраз.

    В audio/ реализован как функция `async def listen(source, call_id)`.
    Подходит любой вызываемый объект с такой же сигнатурой, например FakeListener.
    """

    def __call__(self, source: AudioSource, call_id: str) -> AsyncIterator[Utterance]: ...


class Storage(Protocol):
    """Постоянная история номеров и звонков.

    В storage/db.py реализован как функции уровня модуля: модуль с функциями
    нужных имён тоже подходит под этот протокол. Методы синхронные, поэтому
    асинхронный код должен вызывать их через `asyncio.to_thread`.
    """

    def init_db(self, path: str) -> None: ...

    def lookup_number(self, phone: str) -> NumberInfo:
        """История номера. У неизвестного номера calls_count=0."""
        ...

    def save_case(self, case: CallCase) -> str: ...

    def get_cases_by_number(self, phone: str) -> list[CallCase]: ...

    def list_recent_cases(self, limit: int = 20) -> list[CallCase]: ...

    def tag_number(self, phone: str, tag: str) -> None: ...
