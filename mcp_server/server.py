"""MCP server with the agent's tools: scheme search, scheme lookup and call history.

`build_server` takes its dependencies explicitly, so tests pass fakes and the
agent can run the server in-process. `python -m mcp_server.server` starts the
real one over stdio.
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
    server = MCPServer("antifraud", instructions=INSTRUCTIONS)

    @server.tool()
    async def search_schemes(query: str, k: int = 5) -> list[Hit]:
        """Find fraud scheme stages most similar to the caller's words."""
        return await retriever.asearch(query, k)

    @server.tool()
    async def get_scheme(scheme_id: str) -> Scheme:
        """Return a fraud scheme with all its stages."""
        if scheme_id not in schemes:
            raise ToolError(f"unknown scheme: {scheme_id}")
        return schemes[scheme_id]

    @server.tool()
    async def lookup_number(phone: str) -> NumberInfo:
        """History of a phone number: how many calls, which schemes, tags."""
        return await _storage_call(storage.lookup_number, phone)

    @server.tool()
    async def save_case(case: CallCase) -> str:
        """Save the report of a finished call; returns call_id."""
        return await _storage_call(storage.save_case, case)

    @server.tool()
    async def list_recent_cases(limit: int = 20) -> list[CallCase]:
        """Most recent saved calls, newest first."""
        return await _storage_call(storage.list_recent_cases, limit)

    return server


async def _storage_call[T](func: Callable[..., T], *args: object) -> T:
    # Storage is synchronous SQLite: keep it off the event loop. A ValueError
    # there means bad input (e.g. a foreign phone number), which the caller
    # should see as a failed tool, not as a server crash.
    try:
        return await asyncio.to_thread(func, *args)
    except ValueError as error:
        raise ToolError(str(error)) from error


def build_default_server(settings: Settings, retriever: Retriever | None = None) -> MCPServer:
    """Server on the real SQLite storage. Pass `retriever` to share an already loaded index."""
    from storage import db  # noqa: PLC0415  real SQLite only when running for real

    db.init_db(settings.db_path)
    return build_server(
        retriever or Retriever.from_settings(settings), db, load_schemes(settings.schemes_dir)
    )


def main() -> None:
    build_default_server(get_settings()).run("stdio")


if __name__ == "__main__":
    main()
