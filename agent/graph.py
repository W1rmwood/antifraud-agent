"""Агент одного звонка.

Ход агента — разбор окна последних реплик небольшим графом LangGraph:

    retrieve (RAG: похожие этапы схем)
      -> analyze (LLM: схема, этап, цитаты требований — разведка, куда бить)
      -> decide (код: объявлять ли схему; события схемы, этапа и предупреждения в UI)

Каждая стрелка может закончить ход раньше: поиск ничего не нашёл — LLM не зовём;
LLM не ответила — решение не принимаем.

Реплику, которая бесит мошенника, LLM пишет уже после хода, фоновой задачей
и только для объявленной схемы: грубость не попадёт в обычный звонок, а разбор
(схема, этап, предупреждение, подсказка «положить трубку») не ждёт ещё ~8 с генерации.
Одновременно идёт не больше одной такой генерации.

Запись реплики (record) и разбор (analyze) разделены: run_call показывает
фразу сразу, а разбирает пачкой, когда модель освободится.

Инструменты вызываются в заранее известные моменты, а не по выбору LLM:
lookup_number в начале звонка, get_scheme при объявлении схемы, save_case
в конце. Модель на 8B, выбирающая инструменты сама, медленнее и
непредсказуемее, а здесь правильный момент для каждого инструмента известен заранее.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.config import Settings
from agent.events import EventSink
from agent.llm import LLM, LLMError, Messages
from agent.policy import HANGUP_DEFAULT, decide, hangup_text, provoke_mode, quote_is_said
from agent.rules import instant_flags, warning_text
from agent.prompts import TACTIC_KIND, Analysis, CallSummary, Reply, analysis_messages, reply_messages, summary_messages
from agent.tools import ToolClient
from contracts import AudioSource, CallCase, EventType, NumberInfo, Scheme, Speaker, Utterance
from rag.retriever import Hit, Retriever

log = logging.getLogger(__name__)


class TurnState(TypedDict, total=False):
    """Состояние графа на один ход (разбор одного окна реплик).

    Каждый узел возвращает только те ключи, которые меняет, а LangGraph
    сливает их в общее состояние. total=False — потому что ключи появляются
    по ходу графа: на входе есть только window.
    """

    window: list[Utterance]
    caller_text: str
    hits: list[Hit]
    analysis: Analysis


@dataclass
class AgentDeps:
    """Зависимости агента. В тестах сюда подставляются фейки, в работе — настоящие модули."""

    retriever: Retriever
    llm: LLM
    settings: Settings


@dataclass(frozen=True)
class Turn:
    """След одного хода разбора. Нужен eval: задержка и подбор порога без повторного запуска LLM."""

    seq: int  # номер последней реплики в окне хода
    caller_text: str
    candidates: list[str]
    analysis: Analysis | None  # None — LLM не ответила или поиск ничего не нашёл
    latency_s: float  # время разбора, без фоновой генерации реплики модели
    # Сколько новых реплик накопилось к ходу: больше 1 — модель не успевала
    # за разговором, и run_call разобрал их одной пачкой.
    batch_size: int = 1


@dataclass
class CallState:
    """Что агент знает о звонке на текущий момент. Живёт весь звонок, в отличие от TurnState."""

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    phone: str | None = None
    transcript: list[Utterance] = field(default_factory=list)
    scheme: Scheme | None = None  # объявленная схема
    confidence: float | None = None  # уверенность модели в объявленной схеме на последнем её ходу
    challenger: str | None = None  # схема, которая обошла объявленную на прошлом ходу
    stage_n: int | None = None  # текущий этап
    max_stage: int | None = None  # самый дальний этап за звонок; уходит в CallCase
    flags: list[str] = field(default_factory=list)  # подтверждённые требования мошенника после объявления схемы
    offer_hangup: bool = False  # на этом ходу показать подсказку «закончить разговор»
    rule_quotes: list[str] = field(default_factory=list)  # фразы, на которые уже сработали правила без LLM
    rule_hangup_sent: bool = False  # общая подсказка hangup от правил уже показана
    suggestions: list[str] = field(default_factory=list)  # отправленные подсказки: модели — чтобы не повторялась


class CallAgent:
    """Ведёт один звонок от начала до отчёта.

    Порядок вызовов: start() → on_utterance() на каждую фразу → finish().
    В живом звонке run_call вместо on_utterance зовёт record() на каждую фразу
    и analyze() на пачку фраз, когда модель свободна.
    """

    def __init__(self, call_id: str, deps: AgentDeps, sink: EventSink, tools: ToolClient) -> None:
        self.call_id = call_id
        self.state = CallState()
        self.turns: list[Turn] = []
        self._deps = deps
        self._sink = sink
        self._tools = tools
        self._schemes: dict[str, Scheme] = {}  # кэш схем, уже полученных через get_scheme
        self._reply_task: asyncio.Task[None] | None = None  # фоновая генерация реплики модели
        self._graph = self._build_graph()

    async def start(self, source: AudioSource, phone: str | None = None) -> None:
        """Начало звонка: событие call_started и проверка номера по истории."""
        self.state.phone = phone
        await self._sink.emit(
            EventType.call_started, {"phone": phone, "source": source.model_dump(mode="json")}
        )
        if phone is None:
            return
        found = await self._tools.call("lookup_number", {"phone": phone})
        if not found.ok:
            return  # без истории номера звонок всё равно продолжается
        info = NumberInfo.model_validate(found.result)
        if info.calls_count:
            schemes = f", схемы: {', '.join(info.schemes)}" if info.schemes else ""
            await self._sink.emit(
                EventType.warning,
                {"text": f"С этого номера уже звонили {info.calls_count} раз{schemes}."},
            )

    async def on_utterance(self, utterance: Utterance) -> None:
        """Пошаговый режим: показывает фразу и, если говорит не абонент, разбирает окно.

        Ждёт и реплику модели, так что после вызова ход закончен целиком. Так
        работают eval (фрагмент за фрагментом, без гонок) и тесты; в живом
        звонке run_call зовёт record и analyze по отдельности.
        """
        await self.record(utterance)
        if utterance.speaker == Speaker.user:
            # Агент реагирует на звонящего. Слова абонента при этом остаются
            # в окне: модели нужен контекст, на что именно отвечает мошенник.
            return
        await self.analyze()
        await self.wait_reply()

    async def record(self, utterance: Utterance) -> None:
        """Добавляет фразу в транскрипт и сразу показывает её в UI, не дожидаясь модели.

        Слова звонящего здесь же проверяются правилами без LLM (agent.rules): красная плашка
        появляется через доли секунды после фразы, а не после разбора моделью (ТЗ:
        «предупреждения не зависят от LLM»).
        """
        call = self.state
        call.transcript.append(utterance)
        await self._sink.emit(EventType.transcript, utterance.model_dump(mode="json"))
        if utterance.speaker == Speaker.user:
            return
        hits = [h for h in instant_flags(utterance.text) if h.quote not in call.rule_quotes]
        if not hits:
            return
        call.rule_quotes += [h.quote for h in hits]
        await self._sink.emit(EventType.warning, {"text": warning_text(hits)})
        if not call.rule_hangup_sent and call.scheme is None:
            # Схема ещё не определена, но звонящий уже чего-то требует: безопасный выход
            # показываем сразу, общим текстом. После объявления схемы его заменит текст по её категории.
            call.rule_hangup_sent = True
            await self._sink.emit(EventType.suggestion, {"text": HANGUP_DEFAULT, "kind": "hangup"})

    async def analyze(self, batch_size: int = 1) -> Turn:
        """Один ход: граф по окну последних реплик, подсказка hangup и запуск фоновой реплики модели.

        `batch_size` — сколько новых реплик пришло с прошлого хода; пишется в Turn.
        """
        window = self.state.transcript[-self._deps.settings.window_size :]
        started = time.perf_counter()
        result = await self._graph.ainvoke({"window": window})
        latency = time.perf_counter() - started
        if self.state.offer_hangup and self.state.scheme is not None:
            # Сразу после разбора, не дожидаясь реплики модели: безопасный выход
            # нужен человеку первым, а реплика придёт через несколько секунд.
            # Текст пишет код: модель не может «забыть» предложить положить трубку.
            self.state.offer_hangup = False
            await self._sink.emit(
                EventType.suggestion, {"text": hangup_text(self.state.scheme), "kind": "hangup"}
            )
        if "analysis" in result and self.state.scheme is not None:
            # Ответку генерируем только при объявленной схеме: это и есть граница
            # «никакой грубости в обычном звонке», зашитая в код, а не в промпт.
            self._start_reply(window)
        turn = Turn(
            seq=window[-1].seq,
            caller_text=result.get("caller_text", ""),
            candidates=[h.scheme_id for h in result.get("hits", [])],
            analysis=result.get("analysis"),
            latency_s=latency,
            batch_size=batch_size,
        )
        self.turns.append(turn)
        return turn

    async def wait_reply(self, timeout_s: float | None = None) -> None:
        """Дожидается фоновой реплики модели, если она ещё пишется.

        По истечении `timeout_s` генерация отменяется: отчёт о звонке важнее
        запоздавшей подсказки. None — ждать сколько потребуется.
        """
        task = self._reply_task
        if task is None:
            return
        try:
            # wait_for сам отменяет задачу, если время вышло.
            await asyncio.wait_for(task, timeout_s)
        except TimeoutError:
            log.warning("reply for %s cancelled after %.0f s", self.call_id, timeout_s)

    async def finish(self) -> CallCase:
        """Конец звонка: дожидается реплики модели, затем отчёт, save_case и событие call_ended."""
        try:
            await self.wait_reply(self._deps.settings.reply_wait_s)
        except Exception:
            # Сбой подсказки не должен помешать сохранить кейс.
            log.exception("reply failed for %s", self.call_id)
        summary = await self._summarize()
        case = CallCase(
            call_id=self.call_id,
            phone=self.state.phone,
            started_at=self.state.started_at,
            ended_at=datetime.now(UTC),
            scheme_id=self.state.scheme.id if self.state.scheme else None,
            max_stage=self.state.max_stage,
            legend=summary.legend,
            key_phrases=summary.key_phrases,
            summary=summary.summary,
            transcript_path=str(Path(self._deps.settings.runs_dir) / f"{self.call_id}.jsonl"),
        )
        await self._sink.emit(EventType.summary, case.model_dump(mode="json"))
        await self._tools.call("save_case", {"case": case.model_dump(mode="json")})
        await self._sink.emit(EventType.call_ended, {})
        return case

    # --- граф ---

    def _build_graph(self) -> CompiledStateGraph:
        """Собирает граф retrieve → analyze → decide с ранними выходами."""
        graph = StateGraph(TurnState)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("analyze", self._analyze)
        graph.add_node("decide", self._decide)
        graph.add_edge(START, "retrieve")
        # Ранние выходы: если поиск ничего не нашёл, LLM не вызываем (нечего
        # сравнивать — экономим секунды). Если LLM не ответила, decide пропускаем.
        graph.add_conditional_edges("retrieve", lambda s: "analyze" if s.get("hits") else END, ["analyze", END])
        graph.add_conditional_edges("analyze", lambda s: "decide" if "analysis" in s else END, ["decide", END])
        graph.add_edge("decide", END)
        return graph.compile()

    async def _retrieve(self, state: TurnState) -> TurnState:
        """Узел поиска: находит этапы схем, похожие на слова звонящего в окне."""
        # Ищем только по словам звонящего: слова абонента («а что случилось?»)
        # только размывали бы запрос.
        caller_text = " ".join(u.text for u in state["window"] if u.speaker != Speaker.user)
        settings = self._deps.settings
        if not caller_text:
            return {"caller_text": "", "hits": []}
        hits = await self._deps.retriever.asearch(caller_text, settings.top_k, settings.retrieval_mode)
        return {"caller_text": caller_text, "hits": hits}

    async def _analyze(self, state: TurnState) -> TurnState:
        """Узел LLM: просит модель разобрать окно реплик по найденным кандидатам."""
        messages = analysis_messages(state["window"], state["hits"], self.state.scheme, self.state.stage_n)
        try:
            return {"analysis": await self._deps.llm.generate(messages, Analysis)}
        except LLMError as error:
            # Пропускаем ход, а не роняем звонок: следующая реплика даст новую попытку.
            log.error("analysis skipped for %s: %s", self.call_id, error)
            return {}

    async def _decide(self, state: TurnState) -> TurnState:
        """Узел решения: применяет правило тревоги и отправляет события схемы, этапа и предупреждения."""
        analysis, caller_text = state["analysis"], state["caller_text"]
        call = self.state
        candidates = {h.scheme_id for h in state["hits"]}
        decision = decide(
            analysis,
            caller_text,
            candidates,
            self._deps.settings.detect_threshold,
            current=call.scheme.id if call.scheme else None,
            current_stage=call.stage_n,
            current_confidence=call.confidence,
            challenger=call.challenger,
            margin=self._deps.settings.switch_margin,
            rule_hit=bool(call.rule_quotes),
        )
        call.challenger = decision.challenger
        if decision.scheme_id is None:
            # Схема не объявлена: подсказки и предупреждения не показываем,
            # чтобы не пугать человека на обычном звонке.
            return {}
        if analysis.scheme_id == decision.scheme_id:
            # Запоминаем уверенность, только когда модель выбрала объявленную схему:
            # с ней сравнивается следующий претендент.
            call.confidence = analysis.confidence

        if call.scheme is None or decision.scheme_id != call.scheme.id:
            # Новая схема (впервые или сменилась): берём её целиком через MCP.
            scheme = await self._get_scheme(decision.scheme_id)
            if scheme is None:
                return {}
            call.scheme, call.stage_n = scheme, None
            call.offer_hangup = True
            # В доказательства идут подтверждённые флаги и только те цитаты
            # модели, которые реально прозвучали. dict.fromkeys убирает
            # повторы с сохранением порядка.
            evidence = decision.flags + [q for q in analysis.evidence if quote_is_said(q, caller_text)]
            await self._sink.emit(
                EventType.scheme_detected,
                {
                    "scheme_id": scheme.id,
                    "scheme_name": scheme.name,
                    # У общей метки своей уверенности у модели нет (она её не выбирала):
                    # показываем порог объявления — тревога поднята ровно на нём.
                    "confidence": analysis.confidence if analysis.scheme_id == scheme.id
                    else self._deps.settings.detect_threshold,
                    "evidence": list(dict.fromkeys(evidence)),
                    # Все этапы схемы: UI рисует прогресс по событиям, не читая data/schemes/.
                    "stages": [{"n": s.n, "name": s.name} for s in scheme.stages],
                },
            )

        await self._update_stage(decision.stage_n)
        # Предупреждение собирает код из подтверждённой цитаты, а не пишет модель:
        # так в нём не может оказаться того, чего мошенник не говорил.
        # Требование, о котором уже предупредили правила без LLM, второй плашкой не дублируем.
        new_flags = [
            f for f in decision.flags
            if f not in call.flags and not any(quote_is_said(f, q) for q in call.rule_quotes)
        ]
        if new_flags:
            call.flags += new_flags
            # Мошенник выдвинул новое требование (код, перевод, наличные) — снова напоминаем про выход.
            call.offer_hangup = True
            quotes = "; ".join(f"«{f}»" for f in new_flags)
            text = f"Мошенник требует: {quotes}. Ничего не называй и не переводи."
            await self._sink.emit(EventType.warning, {"text": text})
        return {}

    def _start_reply(self, window: list[Utterance]) -> None:
        """Запускает фоновую генерацию реплики модели, если предыдущая уже закончилась."""
        previous_task = self._reply_task
        if previous_task is not None:
            if not previous_task.done():
                # Single-flight: две генерации разом только замедлили бы обе
                # и разбор вместе с ними. Следующий ход даст новую попытку.
                log.info("reply for %s skipped: previous one is still generating", self.call_id)
                return
            previous_task.result()  # поднимает ошибку прошлой генерации, если она упала не на LLM
        call, settings = self.state, self._deps.settings
        assert call.scheme is not None
        # Промпт собираем сейчас, по окну и этапу этого хода: пока задача ждёт
        # своей очереди, разбор следующей пачки может их поменять.
        provoke = provoke_mode(call.stage_n, len(call.scheme.stages))
        previous = call.suggestions[-settings.recent_replies :] if settings.recent_replies else []
        messages = reply_messages(window, call.scheme, call.stage_n, provoke, previous)
        self._reply_task = asyncio.create_task(self._reply(call.scheme, messages))

    async def _reply(self, scheme: Scheme, messages: Messages) -> None:
        """Фоновая ответка: LLM пишет реплику, которая бесит мошенника, с учётом схемы и этапа."""
        call, settings = self.state, self._deps.settings
        try:
            reply = await self._deps.llm.generate(messages, Reply, temperature=settings.reply_temperature)
        except LLMError as error:
            log.error("reply skipped for %s: %s", self.call_id, error)
            return
        if call.scheme is not scheme:
            # Пока модель писала, разбор сменил схему: реплика про старую легенду уже не к месту.
            log.info("reply for %s dropped: scheme changed", self.call_id)
            return
        text = reply.text.strip()
        if text and text not in call.suggestions:
            call.suggestions.append(text)
            await self._sink.emit(
                EventType.suggestion, {"text": text, "kind": TACTIC_KIND[reply.tactic], "tactic": reply.tactic}
            )

    async def _update_stage(self, stage_n: int | None) -> None:
        """Переключает этап и шлёт stage_changed, если этап действительно поменялся."""
        call = self.state
        assert call.scheme is not None
        if stage_n is None or not 1 <= stage_n <= len(call.scheme.stages) or stage_n == call.stage_n:
            # Модель может назвать этап, которого в схеме нет: оставляем последний правильный.
            return
        stage = call.scheme.stages[stage_n - 1]
        call.stage_n = stage_n
        # Этап может откатиться назад (мошенник вернулся к уговорам),
        # а max_stage запоминает, как далеко он успел зайти.
        call.max_stage = max(call.max_stage or 0, stage_n)
        await self._sink.emit(
            EventType.stage_changed,
            {
                "scheme_id": call.scheme.id,
                "stage_n": stage.n,
                "stage_name": stage.name,
                "next_move": stage.next_move,
            },
        )

    async def _get_scheme(self, scheme_id: str) -> Scheme | None:
        """Схема по id через MCP-инструмент get_scheme, с кэшем на время звонка."""
        if scheme_id not in self._schemes:
            found = await self._tools.call("get_scheme", {"scheme_id": scheme_id})
            if not found.ok:
                return None
            self._schemes[scheme_id] = Scheme.model_validate(found.result)
        return self._schemes[scheme_id]

    async def _summarize(self) -> CallSummary:
        """Итоговый отчёт от LLM, а если она недоступна — из того, что агент уже знает."""
        call = self.state
        messages = summary_messages(call.transcript, call.scheme, call.max_stage)
        try:
            return await self._deps.llm.generate(messages, CallSummary)
        except LLMError as error:
            # Кейс нужно сохранить даже без LLM: история номера важнее красивого текста.
            log.error("summary fallback for %s: %s", self.call_id, error)
            verdict = f"Схема: {call.scheme.name}, этап {call.max_stage}." if call.scheme else "Схема не найдена."
            return CallSummary(
                legend=call.scheme.legend if call.scheme else None,
                key_phrases=call.flags[:5],
                summary=f"{verdict} Реплик в звонке: {len(call.transcript)}.",
            )
