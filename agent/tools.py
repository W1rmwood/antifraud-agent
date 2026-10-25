"""Клиентская сторона MCP: каждый вызов инструмента дублируется в UI событиями tool_call / tool_result."""

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
    """Результат вызова инструмента."""

    ok: bool
    result: Any  # значение инструмента или, если ok=False, текст ошибки


class ToolClient:
    """Обёртка над MCP-клиентом, которая отправляет события и никогда не бросает исключения.

    Упавший инструмент не должен обрывать звонок: агент получает ok=False
    и решает сам, что делать дальше (например, продолжить без истории номера).
    """

    def __init__(self, client: Client, sink: EventSink) -> None:
        self._client = client
        self._sink = sink

    async def call(self, tool: str, args: dict[str, Any]) -> ToolResult:
        """Вызывает инструмент `tool` с аргументами `args`."""
        await self._sink.emit(EventType.tool_call, {"tool": tool, "args": args})
        # Ошибка в MCP приходит двумя путями:
        # - MCPError — сбой протокола (нет такого инструмента, неверные аргументы);
        # - is_error=True — инструмент сам сообщил об ошибке (например, ToolError
        #   на иностранный номер). Текст ошибки тогда лежит в content.
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
    """Достаёт значение инструмента из обёртки MCP."""
    # Структурированный результат в MCP — всегда объект. Поэтому строку или
    # список (save_case, search_schemes) MCP заворачивает в {"result": значение}.
    if content is not None and set(content) == {"result"}:
        return content["result"]
    return content
