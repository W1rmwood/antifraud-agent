"""MCP-сервер с инструментами агента: поиск схем, получение схемы и история звонков.

`build_server` получает зависимости явно: тесты передают фейки, а агент может
запустить сервер в своём же процессе. `python -m mcp_server.server` запускает
настоящий сервер по stdio.

Docstring каждого инструмента — это его описание для MCP-клиента.
"""

import asyncio
from collections.abc import Callable
from types import ModuleType

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from agent.config import Settings, get_settings
from contracts import CallCase, NumberInfo, Scheme, Storage
from rag.chunks import load_schemes
from rag.retriever import Hit, Retriever

INSTRUCTIONS = (
    "Tools of an anti-fraud phone assistant: search fraud schemes by what the caller says, "
    "get a scheme by id, check a phone number's call history and save a finished call."
)


def build_server(
    retriever: Retriever, storage: Storage | ModuleType, schemes: dict[str, Scheme]
) -> MCPServer:
    """Создаёт MCP-сервер с пятью инструментами поверх переданных поиска, хранилища и схем.

    `storage` — модуль storage.db (функции уровня модуля) или объект вроде
    InMemoryStorage: оба подходят под протокол Storage.
    """
    server = MCPServer("antifraud", instructions=INSTRUCTIONS)

    # MCP SDK строит схему входа по аннотациям аргументов, а схему выхода —
    # по аннотации возврата. Поэтому Pydantic-модели (Hit, Scheme, CallCase)
    # уходят клиенту как структурированный JSON, а не как текст.

    @server.tool()
    async def search_schemes(query: str, k: int = 5) -> list[Hit]:
        """Найти этапы схем мошенничества, больше всего похожие на слова звонящего."""
        return await retriever.asearch(query, k)

    @server.tool()
    async def get_scheme(scheme_id: str) -> Scheme:
        """Вернуть схему мошенничества со всеми этапами."""
        if scheme_id not in schemes:
            raise ToolError(f"unknown scheme: {scheme_id}")
        return schemes[scheme_id]

    @server.tool()
    async def lookup_number(phone: str) -> NumberInfo:
        """История номера телефона: сколько звонков, какие схемы, метки."""
        return await _storage_call(storage.lookup_number, phone)

    @server.tool()
    async def save_case(case: CallCase) -> str:
        """Сохранить отчёт о завершённом звонке; возвращает call_id."""
        return await _storage_call(storage.save_case, case)

    @server.tool()
    async def list_recent_cases(limit: int = 20) -> list[CallCase]:
        """Последние сохранённые звонки, от новых к старым."""
        return await _storage_call(storage.list_recent_cases, limit)

    return server


async def _storage_call[T](func: Callable[..., T], *args: object) -> T:
    """Вызывает синхронную функцию хранилища в отдельном потоке."""
    # Хранилище — синхронный SQLite, поэтому выносим вызов из event loop.
    # ValueError там означает плохой ввод (например, иностранный номер):
    # клиент должен увидеть ошибку инструмента (ToolError), а не падение сервера.
    try:
        return await asyncio.to_thread(func, *args)
    except ValueError as error:
        raise ToolError(str(error)) from error


def build_default_server(settings: Settings, retriever: Retriever | None = None) -> MCPServer:
    """Сервер на настоящем SQLite. Передай `retriever`, чтобы не загружать индекс второй раз."""
    # Настоящее хранилище импортируем только при реальном запуске:
    # тестам с фейками SQLite не нужен.
    from storage import db  # noqa: PLC0415

    db.init_db(settings.db_path)
    return build_server(
        retriever or Retriever.from_settings(settings), db, load_schemes(settings.schemes_dir)
    )


def main() -> None:
    """Точка входа `python -m mcp_server.server`: сервер по stdio."""
    build_default_server(get_settings()).run("stdio")


if __name__ == "__main__":
    main()
