"""Тестовые подмены, которые выполняют те же контракты, что и настоящие модули."""

from tests.fakes.fake_listener import FakeListener, parse_transcript
from tests.fakes.in_memory_storage import InMemoryStorage

__all__ = ["FakeListener", "InMemoryStorage", "parse_transcript"]
