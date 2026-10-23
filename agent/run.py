"""Run one call end to end: listener -> agent -> events.

`run_call` is what the UI calls. As a script it prints events of a call:

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

McpTarget = MCPServer | StdioServerParameters


async def run_call(
    request: CallRequest,
    listener: Listener,
    deps: AgentDeps,
    mcp_target: McpTarget,
    queue: asyncio.Queue[AgentEvent] | None = None,
) -> CallCase:
    log_path = Path(deps.settings.runs_dir) / f"{request.call_id}.jsonl"
    sink = EventSink(request.call_id, queue, log_path)
    # The MCP client is opened and closed inside this task: anyio cancel
    # scopes break if a client crosses task boundaries.
    async with Client(mcp_target) as client:
        agent = CallAgent(request.call_id, deps, sink, ToolClient(client, sink))
        await agent.start(request.source, request.phone)
        async for utterance in listener(request.source, request.call_id):
            await agent.on_utterance(utterance)
        return await agent.finish()


def default_deps(settings: Settings) -> AgentDeps:
    llm = OllamaLLM(
        settings.llm_model,
        settings.ollama_host,
        temperature=settings.llm_temperature,
        retries=settings.llm_retries,
        timeout_s=settings.llm_timeout_s,
    )
    return AgentDeps(retriever=Retriever.from_settings(settings), llm=llm, settings=settings)


def default_mcp_target(settings: Settings, retriever: Retriever) -> McpTarget:
    if settings.mcp_transport == "stdio":
        return StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"])
    from mcp_server.server import build_default_server  # noqa: PLC0415

    return build_default_server(settings, retriever)


async def _main(path: str, phone: str | None) -> None:
    from audio.stream import listen  # noqa: PLC0415  real audio module, owned by Yura and Ruslan

    settings = get_settings()
    deps = default_deps(settings)
    request = CallRequest(
        call_id=f"cli-{uuid.uuid4().hex[:8]}", source=AudioSource(kind="file", path=path), phone=phone
    )
    queue: asyncio.Queue[AgentEvent] = asyncio.Queue()

    async def printer() -> None:
        while True:
            event = await queue.get()
            print(event.model_dump_json())

    printing = asyncio.create_task(printer())
    await run_call(request, listen, deps, default_mcp_target(settings, deps.retriever), queue)
    await asyncio.sleep(0)  # let the printer drain the last events
    printing.cancel()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent on one call file and print its events.")
    parser.add_argument("path", help="audio file or a .txt transcript ('caller: ...' lines)")
    parser.add_argument("--phone", default=None, help="caller number, +7XXXXXXXXXX")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(_main(args.path, args.phone))


if __name__ == "__main__":
    main()
