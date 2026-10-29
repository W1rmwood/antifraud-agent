"""Запуск одного звонка целиком: слушатель → агент → события.

`run_call` — то, что вызывает UI. Как скрипт печатает события звонка:

    python -m agent.run data/calls/001.wav --phone +79990000001 [--realtime] [--json]
    python -m agent.run --mic        # живой звонок на громкой связи, Ctrl+C — закончить

Слух и разбор идут параллельно: фраза попадает на экран сразу после
распознавания, а LLM разбирает то, что накопилось, пока она думала над
прошлой пачкой. Так подсказка не отстаёт от разговора, а очередь не растёт.
"""

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from mcp import Client, StdioServerParameters
from mcp.server.mcpserver import MCPServer

from agent.config import Settings, get_settings
from agent.events import EventSink
from agent.graph import AgentDeps, CallAgent
from agent.llm import OllamaLLM
from agent.tools import ToolClient
from contracts import AgentEvent, AudioSource, CallCase, CallRequest, EventType, Listener, Speaker, Utterance
from rag.retriever import Retriever

log = logging.getLogger(__name__)

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

    Если звук или разбор упали, кейс всё равно сохраняется (finish), а ошибка
    поднимается дальше уже после этого.
    """
    log_path = Path(deps.settings.runs_dir) / f"{request.call_id}.jsonl"
    sink = EventSink(request.call_id, queue, log_path)
    # MCP-клиент открывается и закрывается внутри одной задачи asyncio — этой:
    # MCP SDK построен на anyio, а его области отмены (cancel scopes) ломаются,
    # если клиент открыть в одной задаче, а закрыть в другой. Задачи слуха и
    # разбора только вызывают инструменты через уже открытый клиент.
    async with Client(mcp_target) as client:
        agent = CallAgent(request.call_id, deps, sink, ToolClient(client, sink))
        await agent.start(request.source, request.phone)
        error: Exception | None = None
        try:
            await converse(agent, listener(request.source, request.call_id))
        except Exception as exc:
            # TaskGroup заворачивает ошибку в ExceptionGroup; одну ошибку достаём, чтобы
            # снаружи видели исходную (FileNotFoundError, LLM и т. п.), а не обёртку.
            error = exc.exceptions[0] if isinstance(exc, ExceptionGroup) and len(exc.exceptions) == 1 else exc
            log.error("call %s interrupted: %r", request.call_id, error)
        case = await agent.finish()
    if error is not None:
        raise error
    return case


async def converse(agent: CallAgent, utterances: AsyncIterator[Utterance]) -> None:
    """Слух и разбор звонка двумя параллельными задачами с очередью между ними.

    «Слух» читает реплики и сразу показывает их (record), как бы ни была занята модель.
    «Мысль» ждёт новую реплику, забирает из очереди всё накопившееся и делает
    один analyze по свежему окну. Так за минуту звонка ходов столько, сколько
    успевает модель, а последний ход всегда видит последние реплики.
    Возвращается, когда реплики кончились и последняя пачка разобрана.
    """
    inbox: asyncio.Queue[Utterance | None] = asyncio.Queue()  # None — реплик больше не будет

    async def hear() -> None:
        try:
            async for utterance in utterances:
                await agent.record(utterance)
                inbox.put_nowait(utterance)
        finally:
            # И при обычном конце, и при ошибке звука: «мысль» дообработает пачку и выйдет.
            inbox.put_nowait(None)

    async def think() -> None:
        ended = False
        while not ended:
            batch = [await inbox.get()]
            while not inbox.empty():
                batch.append(inbox.get_nowait())
            ended = batch[-1] is None  # None кладётся последним, после него очередь пуста
            fresh = [u for u in batch if u is not None]
            # LLM запускают реплики звонящего и unknown (моно-запись); реплики абонента
            # уже в окне как контекст, но разбор не вызывают.
            if any(u.speaker != Speaker.user for u in fresh):
                await agent.analyze(batch_size=len(fresh))

    # TaskGroup: если одна задача упала, вторая отменяется, и никто не висит
    # в ожидании очереди или звука.
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(hear())
        tasks.create_task(think())


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


async def until_stopped(utterances: AsyncIterator[Utterance], stop: asyncio.Event) -> AsyncIterator[Utterance]:
    """Пропускает реплики, пока не взведён `stop`; потом закрывает источник и заканчивается.

    Живой звук (микрофон) сам не кончается: звонок завершают по Ctrl+C. Обрывать
    весь процесс нельзя — потеряется отчёт и кейс. Поэтому Ctrl+C только взводит
    событие, слушатель штатно заканчивается, и run_call доходит до finish().
    """
    iterator = aiter(utterances)
    stopper = asyncio.ensure_future(stop.wait())
    try:
        while True:
            following = asyncio.ensure_future(anext(iterator))
            done, _ = await asyncio.wait({following, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if following not in done:
                following.cancel()
                # Дожидаемся отмены: иначе источник (микрофон) закроется уже после выхода отсюда.
                await asyncio.gather(following, return_exceptions=True)
                return
            try:
                yield following.result()
            except StopAsyncIteration:
                return
    finally:
        stopper.cancel()
        with contextlib.suppress(RuntimeError, StopAsyncIteration):
            await iterator.aclose()  # type: ignore[attr-defined]


_COLORS = {"red": "31", "yellow": "33", "cyan": "36", "green": "32", "grey": "90", "bold": "1"}


def format_event(event: AgentEvent, elapsed_s: float, color: bool = False) -> str | None:
    """Строка для терминала: что увидел бы человек в UI. None — событие не показываем."""

    def paint(text: str, name: str) -> str:
        return f"\033[{_COLORS[name]}m{text}\033[0m" if color else text

    p = event.payload
    stamp = paint(f"[{int(elapsed_s) // 60:02d}:{int(elapsed_s) % 60:02d}]", "grey")
    match event.type:
        case EventType.call_started:
            line = f"Звонок начат. Номер: {p['phone'] or 'неизвестен'}"
        case EventType.transcript:
            who = {"caller": "Звонящий", "user": "Абонент"}.get(p["speaker"], "Голос")
            line = f"{paint(who, 'bold')}: {p['text']}"
        case EventType.warning:
            line = paint(f"⚠ {p['text']}", "red")
        case EventType.scheme_detected:
            evidence = "; ".join(f"«{q}»" for q in p["evidence"][:2])
            line = paint(f"СХЕМА: {p['scheme_name']} (уверенность {p['confidence']:.2f})", "cyan")
            line += f" — {evidence}" if evidence else ""
        case EventType.stage_changed:
            line = paint(f"Этап {p['stage_n']}: {p['stage_name']}. Дальше: {p['next_move']}", "cyan")
        case EventType.suggestion:
            title = {"hangup": "ВЫХОД", "stall": "ТЯНУТЬ ВРЕМЯ", "question": "ВОПРОС", "provoke": "ПОДДЕТЬ"}
            line = paint(f"{title.get(p['kind'], 'ПОДСКАЗКА')}: {p['text']}", "yellow")
        case EventType.tool_result:
            status = "ок" if p["ok"] else "ошибка"
            line = paint(f"инструмент {p['tool']}: {status}", "grey")
        case EventType.summary:
            line = paint(f"ИТОГ: {p['summary']}", "green")
        case EventType.call_ended:
            line = "Звонок завершён, кейс сохранён."
        case _:
            return None
    return f"{stamp} {line}"


async def _main(
    source: AudioSource, phone: str | None, realtime: bool = False, raw_json: bool = False
) -> None:
    """Прогон одного звонка с печатью событий в консоль.

    `realtime` — для файла: фразы выдаются в темпе живого звонка, а не так быстро,
    как распознаются; только так видно, успевает ли разбор за разговором.
    `raw_json` — печатать события как есть, по строке JSON, вместо текста для человека.
    """
    # Настоящий модуль звука, его владельцы — Юра и Руслан.
    from audio.stream import listen  # noqa: PLC0415

    settings = get_settings()
    deps = default_deps(settings)
    request = CallRequest(call_id=f"cli-{uuid.uuid4().hex[:8]}", source=source, phone=phone)
    queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
    started = time.monotonic()
    color = sys.stdout.isatty()

    async def printer() -> None:
        """Печатает события из очереди по мере поступления, как это делал бы UI."""
        while True:
            event = await queue.get()
            line = event.model_dump_json() if raw_json else format_event(event, time.monotonic() - started, color)
            if line:
                print(line, flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_interrupt() -> None:
        # Первый Ctrl+C штатно заканчивает звонок, второй — обычное прерывание.
        loop.remove_signal_handler(signal.SIGINT)
        print("\nЗаканчиваю звонок: дожидаюсь разбора и сохраняю кейс… (ещё раз Ctrl+C — прервать)", flush=True)
        stop.set()

    loop.add_signal_handler(signal.SIGINT, on_interrupt)

    def listener(src: AudioSource, call_id: str) -> AsyncIterator[Utterance]:
        # Контракт Listener — (source, call_id); темп и остановку подставляем здесь.
        return until_stopped(listen(src, call_id, realtime=realtime), stop)

    printing = asyncio.create_task(printer())
    try:
        await run_call(request, listener, deps, default_mcp_target(settings, deps.retriever), queue)
    finally:
        await asyncio.sleep(0)  # даём печати забрать последние события из очереди
        printing.cancel()


def main() -> None:
    """Точка входа `python -m agent.run <файл> | --mic | --loopback`."""
    parser = argparse.ArgumentParser(description="Run the agent on one call and print what the user would see.")
    parser.add_argument("path", nargs="?", help="audio file or a .txt transcript ('caller: ...' lines)")
    live = parser.add_mutually_exclusive_group()
    live.add_argument("--mic", action="store_true", help="live call from the microphone (speakerphone); Ctrl+C ends it")
    live.add_argument("--loopback", action="store_true", help="live call from system audio (ANTIFRAUD_LOOPBACK_DEVICE)")
    parser.add_argument("--phone", default=None, help="caller number, +7XXXXXXXXXX")
    parser.add_argument("--realtime", action="store_true", help="file only: emit utterances at the pace of a live call")
    parser.add_argument("--json", action="store_true", help="print raw events as JSON lines")
    args = parser.parse_args()
    if bool(args.path) == (args.mic or args.loopback):
        parser.error("укажите файл звонка или --mic / --loopback")
    if args.path:
        source = AudioSource(kind="file", path=args.path)
    else:
        source = AudioSource(kind="mic" if args.mic else "loopback")
    # В читаемом режиме служебные логи (HTTP-запросы к Ollama) только мешают.
    logging.basicConfig(level=logging.INFO if args.json else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(_main(source, args.phone, args.realtime, args.json))


if __name__ == "__main__":
    main()
