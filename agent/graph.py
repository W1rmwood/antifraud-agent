"""Агент одного звонка.

На каждую реплику звонящего запускается небольшой граф LangGraph:

    retrieve (RAG: похожие этапы схем)
      -> analyze (LLM: схема, этап, цитаты, подсказка в виде JSON)
      -> act (правило решает; события уходят в UI; get_scheme при новой схеме)

Инструменты вызываются в заранее известные моменты, а не по выбору LLM:
lookup_number в начале звонка, get_scheme при объявлении схемы, save_case
в конце. Модель на 8B, выбирающая инструменты сама, медленнее и
непредсказуемее, а здесь правильный момент для каждого инструмента известен заранее.
"""

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
from agent.llm import LLM, LLMError
from agent.policy import decide, quote_is_said
from agent.prompts import Analysis, CallSummary, analysis_messages, summary_messages
from agent.tools import ToolClient
from contracts import AudioSource, CallCase, EventType, NumberInfo, Scheme, Speaker, Utterance
from rag.retriever import Hit, Retriever

log = logging.getLogger(__name__)


class TurnState(TypedDict, total=False):
    """Состояние графа на один ход (одну реплику).

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
    """След одной разобранной реплики. Нужен eval: задержка и подбор порога без повторного запуска LLM."""

    seq: int
    caller_text: str
    candidates: list[str]
    analysis: Analysis | None  # None — LLM не ответила или поиск ничего не нашёл
    latency_s: float


@dataclass
class CallState:
    """Что агент знает о звонке на текущий момент. Живёт весь звонок, в отличие от TurnState."""

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    phone: str | None = None
    transcript: list[Utterance] = field(default_factory=list)
    scheme: Scheme | None = None  # объявленная схема
    stage_n: int | None = None  # текущий этап
    max_stage: int | None = None  # самый дальний этап за звонок; уходит в CallCase
    flags: list[str] = field(default_factory=list)  # все подтверждённые красные флаги за звонок
    # Последние отправленные подсказка и предупреждение, чтобы не слать одно и то же
    # в UI на каждой реплике.
    last_suggestion: str | None = None
    last_warning: str | None = None


class CallAgent:
    """Ведёт один звонок от начала до отчёта.

    Порядок вызовов: start() → on_utterance() на каждую фразу → finish().
    """

    def __init__(self, call_id: str, deps: AgentDeps, sink: EventSink, tools: ToolClient) -> None:
        self.call_id = call_id
        self.state = CallState()
        self.turns: list[Turn] = []
        self._deps = deps
        self._sink = sink
        self._tools = tools
        self._schemes: dict[str, Scheme] = {}  # кэш схем, уже полученных через get_scheme
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
        """Обрабатывает одну фразу: показывает её в UI и, если говорит звонящий, запускает граф."""
        self.state.transcript.append(utterance)
        await self._sink.emit(EventType.transcript, utterance.model_dump(mode="json"))
        if utterance.speaker == Speaker.user:
            # Агент реагирует на звонящего. Слова абонента при этом остаются
            # в окне: модели нужен контекст, на что именно отвечает мошенник.
            return
        window = self.state.transcript[-self._deps.settings.window_size :]
        started = time.perf_counter()
        result = await self._graph.ainvoke({"window": window})
        self.turns.append(
            Turn(
                seq=utterance.seq,
                caller_text=result.get("caller_text", ""),
                candidates=[h.scheme_id for h in result.get("hits", [])],
                analysis=result.get("analysis"),
                latency_s=time.perf_counter() - started,
            )
        )

    async def finish(self) -> CallCase:
        """Конец звонка: отчёт, сохранение через save_case и событие call_ended."""
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
        """Собирает граф retrieve → analyze → act с ранними выходами."""
        graph = StateGraph(TurnState)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("analyze", self._analyze)
        graph.add_node("act", self._act)
        graph.add_edge(START, "retrieve")
        # Ранние выходы: если поиск ничего не нашёл, LLM не вызываем (нечего
        # сравнивать — экономим секунды). Если LLM не ответила, act пропускаем.
        graph.add_conditional_edges("retrieve", lambda s: "analyze" if s.get("hits") else END, ["analyze", END])
        graph.add_conditional_edges("analyze", lambda s: "act" if "analysis" in s else END, ["act", END])
        graph.add_edge("act", END)
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

    async def _act(self, state: TurnState) -> TurnState:
        """Узел действий: применяет правило тревоги и отправляет события в UI."""
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
        )
        call.flags += [f for f in decision.flags if f not in call.flags]
        if decision.scheme_id is None:
            # Схема не объявлена: подсказки и предупреждения не показываем,
            # чтобы не пугать человека на обычном звонке.
            return {}

        if call.scheme is None or decision.scheme_id != call.scheme.id:
            # Новая схема (впервые или сменилась): берём её целиком через MCP.
            scheme = await self._get_scheme(decision.scheme_id)
            if scheme is None:
                return {}
            call.scheme, call.stage_n = scheme, None
            # В доказательства идут подтверждённые флаги и только те цитаты
            # модели, которые реально прозвучали. dict.fromkeys убирает
            # повторы с сохранением порядка.
            evidence = decision.flags + [q for q in analysis.evidence if quote_is_said(q, caller_text)]
            await self._sink.emit(
                EventType.scheme_detected,
                {
                    "scheme_id": scheme.id,
                    "scheme_name": scheme.name,
                    "confidence": analysis.confidence,
                    "evidence": list(dict.fromkeys(evidence)),
                },
            )

        await self._update_stage(decision.stage_n)
        if analysis.suggestion.strip() and analysis.suggestion != call.last_suggestion:
            call.last_suggestion = analysis.suggestion
            await self._sink.emit(
                EventType.suggestion, {"text": analysis.suggestion, "kind": analysis.suggestion_kind}
            )
        if analysis.warning and analysis.warning != call.last_warning:
            call.last_warning = analysis.warning
            await self._sink.emit(EventType.warning, {"text": analysis.warning})
        return {}

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
