"""MCP client side: every tool call is mirrored to the UI as tool_call / tool_result events."""

import logging
from dataclasses import dataclass
from typing import Any

from mcp import Client
from mcp_types import TextContent
from mcp.shared.exceptions import MCPError

from agent.events import EventSink
from contracts import EventType

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    result: Any  # the tool's value, or the error text when not ok


class ToolClient:
    def __init__(self, client: Client, sink: EventSink) -> None:
        self._client = client
        self._sink = sink

    async def call(self, tool: str, args: dict[str, Any]) -> ToolResult:
        await self._sink.emit(EventType.tool_call, {"tool": tool, "args": args})
        try:
            response = await self._client.call_tool(tool, args)
        except MCPError as error:
            result = ToolResult(ok=False, result=str(error))
        else:
            if response.is_error:
                text = next((c.text for c in response.content if isinstance(c, TextContent)), "tool failed")
                result = ToolResult(ok=False, result=text)
            else:
                result = ToolResult(ok=True, result=_unwrap(response.structured_content))
        if not result.ok:
            log.warning("tool %s failed: %s", tool, result.result)
        await self._sink.emit(EventType.tool_result, {"tool": tool, "ok": result.ok, "result": result.result})
        return result


def _unwrap(content: dict[str, Any] | None) -> Any:
    # MCP wraps non-object results (str, list) as {"result": value}.
    if content is not None and set(content) == {"result"}:
        return content["result"]
    return content
