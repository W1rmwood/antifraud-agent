"""MCP tools through a real MCP client, with the server running in-process on fakes."""

from datetime import datetime

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer

from contracts import CallCase, NumberInfo, Scheme
from mcp_server.server import build_server
from rag.chunks import load_schemes, scheme_chunks
from rag.retriever import Retriever
from tests.fakes import InMemoryStorage

PHONE = "+79991234567"


@pytest.fixture
def server() -> MCPServer:
    # A fixture yielding an open Client would enter and exit anyio cancel
    # scopes in different tasks, so each test opens the client itself.
    schemes = load_schemes("data/schemes")
    retriever = Retriever(scheme_chunks(schemes), mode="bm25")
    return build_server(retriever, InMemoryStorage(), schemes)


def make_case(call_id: str) -> CallCase:
    return CallCase(
        call_id=call_id,
        phone=PHONE,
        started_at=datetime(2026, 10, 23, 12, 0),
        ended_at=None,
        scheme_id="gosuslugi",
        max_stage=2,
        legend="поддержка Госуслуг",
        key_phrases=["продиктуйте код"],
        summary="test",
        transcript_path=f"data/runs/{call_id}.jsonl",
    )


async def test_lists_all_tools(server: MCPServer) -> None:
    async with Client(server) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert tools == {"search_schemes", "get_scheme", "lookup_number", "save_case", "list_recent_cases"}


async def test_search_schemes(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.call_tool("search_schemes", {"query": "продиктуйте код из смс от госуслуг", "k": 3})
        assert not result.is_error
        hits = result.structured_content["result"]
        assert len(hits) == 3
        assert "gosuslugi" in {h["scheme_id"] for h in hits}


async def test_get_scheme(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_scheme", {"scheme_id": "safe-account"})
        assert Scheme.model_validate(result.structured_content).id == "safe-account"

        missing = await client.call_tool("get_scheme", {"scheme_id": "no-such-scheme"})
        assert missing.is_error


async def test_save_case_then_history(server: MCPServer) -> None:
    async with Client(server) as client:
        case = make_case("call-1")
        saved = await client.call_tool("save_case", {"case": case.model_dump(mode="json")})
        assert saved.structured_content == {"result": "call-1"}

        info = await client.call_tool("lookup_number", {"phone": "8 999 123-45-67"})
        assert NumberInfo.model_validate(info.structured_content).schemes == ["gosuslugi"]

        recent = await client.call_tool("list_recent_cases", {"limit": 5})
        assert [CallCase.model_validate(c) for c in recent.structured_content["result"]] == [case]


async def test_bad_phone_is_tool_error_not_crash(server: MCPServer) -> None:
    async with Client(server) as client:
        result = await client.call_tool("lookup_number", {"phone": "+380441234567"})
        assert result.is_error
        assert "380441234567" in result.content[0].text
        # The server is still alive after the failure.
        assert not (await client.call_tool("list_recent_cases", {})).is_error
