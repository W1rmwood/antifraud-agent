"""Запуск одного звонка целиком: слушатель → агент → события.

`run_call` — то, что вызывает UI. Как скрипт печатает события звонка:

    python -m agent.run data/calls/001.wav --phone +79990000001
"""

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp.server.mcpserver import MCPServer

from agent.config import Settings, get_settings
from agent.events import EventSink
from agent.graph import AgentDeps, CallAgent
from agent.llm import OllamaLLM
from agent.tools import ToolClient
from contracts import AgentEvent, AudioSource, CallCase, CallRequest, Listener
from rag.retriever import Retriever

# К чему подключается MCP-клиент: к серверу в том же процессе или к отдельному процессу по stdio.
McpTarget = MCPServer | StdioServerParameters


async def run_call(
    request: CallRequest,
    listener: Listener,
    deps: AgentDeps,
    mcp_target: McpTarget,
    queue: asyncio.Queue[AgentEvent] | None = None,
) -> CallCase:
    """Проводит звонок от первой фразы до сохранённого отчёта и возвращает отчёт.

    События идут в `queue` (её читает UI) и в data/runs/<call_id>.jsonl.
    `listener` — настоящий audio.stream.listen или FakeListener в тестах.
    """
    log_path = Path(deps.settings.runs_dir) / f"{request.call_id}.jsonl"
    sink = EventSink(request.call_id, queue, log_path)
    # MCP-клиент открывается и закрывается внутри одной задачи asyncio:
    # MCP SDK построен на anyio, а его области отмены (cancel scopes) ломаются,
    # если клиент открыть в одной задаче, а закрыть в другой.
    async with Client(mcp_target) as client:
        agent = CallAgent(request.call_id, deps, sink, ToolClient(client, sink))
        await agent.start(request.source, request.phone)
        async for utterance in listener(request.source, request.call_id):
            await agent.on_utterance(utterance)
        return await agent.finish()


def default_deps(settings: Settings) -> AgentDeps:
    """Настоящие зависимости агента: Ollama и поиск по индексу на диске."""
    llm = OllamaLLM(
        settings.llm_model,
        settings.ollama_host,
        temperature=settings.llm_temperature,
        retries=settings.llm_retries,
        timeout_s=settings.llm_timeout_s,
    )
    return AgentDeps(retriever=Retriever.from_settings(settings), llm=llm, settings=settings)


def default_mcp_target(settings: Settings, retriever: Retriever) -> McpTarget:
    """MCP-сервер по настройке mcp_transport."""
    if settings.mcp_transport == "stdio":
        return StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"])
    from mcp_server.server import build_default_server  # noqa: PLC0415

    # В одном процессе сервер получает уже загруженный поиск агента,
    # чтобы не держать в памяти второй экземпляр индекса.
    return build_default_server(settings, retriever)


async def _main(path: str, phone: str | None) -> None:
    """Прогон звонка из файла с печатью событий в консоль."""
    # Настоящий модуль звука, его владельцы — Юра и Руслан.
    from audio.stream import listen  # noqa: PLC0415

    settings = get_settings()
    deps = default_deps(settings)
    request = CallRequest(
        call_id=f"cli-{uuid.uuid4().hex[:8]}", source=AudioSource(kind="file", path=path), phone=phone
    )
    queue: asyncio.Queue[AgentEvent] = asyncio.Queue()

    async def printer() -> None:
        """Печатает события из очереди по мере поступления, как это делал бы UI."""
        while True:
            event = await queue.get()
            print(event.model_dump_json())

    printing = asyncio.create_task(printer())
    await run_call(request, listen, deps, default_mcp_target(settings, deps.retriever), queue)
    await asyncio.sleep(0)  # даём печати забрать последние события из очереди
    printing.cancel()


def main() -> None:
    """Точка входа `python -m agent.run <файл>`."""
    parser = argparse.ArgumentParser(description="Run the agent on one call file and print its events.")
    parser.add_argument("path", help="audio file or a .txt transcript ('caller: ...' lines)")
    parser.add_argument("--phone", default=None, help="caller number, +7XXXXXXXXXX")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(_main(args.path, args.phone))


if __name__ == "__main__":
    main()
