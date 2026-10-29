"""Метрики качества одной командой: `python -m eval.run_eval`.

1. Поиск: hit@1, hit@k и MRR правильной схемы для каждого режима поиска
   (векторы / BM25 / гибрид / гибрид + реранкер) на мошеннических фрагментах.
2. Агент: точность схемы и этапа, ложные тревоги на обычных звонках и
   задержка от реплики до ответа, для каждой запрошенной модели LLM.
3. Подбор порога: правило тревоги, пересчитанное по записанным ответам LLM.

Части, которым нужны Ollama, индекс Chroma или модель реранкера, при их
отсутствии помечаются в отчёте как недоступные, а не роняют прогон.
Поэтому цифры BM25 получаются на любой машине.
"""

import argparse
import asyncio
import statistics
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import ollama
from mcp import Client

from agent.config import RetrievalMode, Settings, get_settings
from agent.events import EventSink
from agent.graph import AgentDeps, CallAgent, Turn
from agent.llm import OllamaLLM
from agent.policy import decide
from agent.tools import ToolClient
from contracts import AudioSource, Scheme, Utterance
from eval.dataset import EvalCase, load_cases
from mcp_server.server import build_server
from rag.chunks import load_schemes, scheme_chunks
from rag.embeddings import OllamaEmbedder
from rag.retriever import FlashRankReranker, Retriever, open_index

MODES: list[RetrievalMode] = ["vector", "bm25", "hybrid", "hybrid_rerank"]
THRESHOLDS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
RESULTS_DIR = Path("eval/results")


# --- поиск ---


@dataclass
class RetrievalRow:
    """Строка таблицы поиска: метрики одного режима."""

    mode: str
    hit1: float = 0.0  # правильная схема на первом месте
    hitk: float = 0.0  # правильная схема среди первых k
    mrr: float = 0.0  # среднее 1 / место правильной схемы; 1.0 — всегда первая
    stage_hitk: float = 0.0  # среди первых k есть именно ожидаемый этап, а не только схема
    latency_ms: float = 0.0  # медиана времени одного поиска
    unavailable: str | None = None  # почему режим не посчитан


def caller_text(case: EvalCase) -> str:
    """Слова звонящего из фрагмента одной строкой — так же, как ищет агент."""
    return " ".join(u.text for u in case.utterances if u.speaker != "user")


def eval_retrieval(retriever: Retriever, cases: list[EvalCase], mode: RetrievalMode, k: int) -> RetrievalRow:
    """Считает метрики поиска одного режима на мошеннических фрагментах.

    Обычные звонки здесь не участвуют: у них нет правильной схемы, которую можно найти.
    """
    fraud = [c for c in cases if c.is_fraud]
    hit1 = hitk = rr = stage_hit = 0.0
    latencies = []
    for case in fraud:
        started = time.perf_counter()
        hits = retriever.search(caller_text(case), k=k, mode=mode)
        latencies.append(time.perf_counter() - started)
        schemes = [h.scheme_id for h in hits]
        if case.expected_scheme in schemes:
            # Место первого вхождения схемы: у одной схемы в выдаче может быть несколько этапов.
            rank = schemes.index(case.expected_scheme) + 1
            hit1 += rank == 1
            hitk += 1
            rr += 1 / rank
        stage_hit += f"{case.expected_scheme}#{case.expected_stage}" in {h.id for h in hits}
    n = len(fraud)
    return RetrievalRow(mode, hit1 / n, hitk / n, rr / n, stage_hit / n, 1000 * statistics.median(latencies))


def build_eval_retriever(settings: Settings) -> tuple[Retriever, dict[str, str]]:
    """Поиск из тех компонентов, что доступны, плюс причины, почему остальных нет.

    Возвращает (поиск, {режим: причина недоступности}).
    """
    chunks = scheme_chunks(load_schemes(settings.schemes_dir))
    missing: dict[str, str] = {}
    embedder = collection = reranker = None
    try:
        collection = open_index(settings.chroma_dir, chunks, settings.embed_model)
        embedder = OllamaEmbedder(settings.embed_model, settings.ollama_host)
        # Пробный запрос: сразу узнаём, отвечает ли Ollama, а не посреди прогона.
        embedder.embed(["проверка"])
    except (RuntimeError, ConnectionError, ollama.ResponseError) as error:
        embedder = collection = None
        for mode in ("vector", "hybrid", "hybrid_rerank"):
            missing[mode] = f"нет векторного поиска: {error}"
    try:
        reranker = FlashRankReranker(settings.reranker_model, settings.reranker_cache_dir)
    except Exception as error:  # noqa: BLE001  любой сбой скачивания или onnx просто выключает эту строку
        missing.setdefault("hybrid_rerank", f"нет реранкера: {error}")
    retriever = Retriever(
        chunks,
        embedder=embedder,
        collection=collection,
        reranker=reranker,
        candidates_k=settings.candidates_k,
        rrf_k=settings.rrf_k,
        stem_len=settings.bm25_stem_len,
    )
    return retriever, missing


# --- агент ---


@dataclass
class CaseRun:
    """Результат прогона агента на одном фрагменте."""

    case: EvalCase
    scheme_id: str | None  # что объявил агент к концу фрагмента
    stage_n: int | None  # самый дальний этап (max_stage)
    turns: list[Turn]  # ответы LLM по ходам — для пересчёта порога


@dataclass
class AgentRow:
    """Строка таблицы агента: метрики одной модели."""

    model: str
    scheme_acc: float = 0.0  # доля мошеннических фрагментов, где объявлена правильная схема
    stage_acc: float = 0.0  # ...и при этом правильный этап
    false_alarms: float = 0.0  # доля обычных звонков, где объявлена хоть какая-то схема
    accuracy: float = 0.0  # все фрагменты: правильная схема или правильно «ничего»
    p50_s: float = 0.0
    p95_s: float = 0.0
    llm_failures: int = 0
    runs: list[CaseRun] = field(default_factory=list)
    unavailable: str | None = None


async def run_case(case: EvalCase, deps: AgentDeps, server: object) -> CaseRun:
    """Прогоняет агента по одному фрагменту, реплика за репликой, как в живом звонке."""
    sink = EventSink(case.id)
    async with Client(server) as client:  # type: ignore[arg-type]
        agent = CallAgent(case.id, deps, sink, ToolClient(client, sink))
        # В наборе нет времени реплик: подставляем условные 3 секунды на фразу
        # и полсекунды паузы. На метрики это не влияет.
        t = 0.0
        for seq, u in enumerate(case.utterances):
            await agent.on_utterance(
                Utterance(call_id=case.id, seq=seq, speaker=u.speaker, text=u.text, t_start=t, t_end=t + 3)
            )
            t += 3.5
    scheme = agent.state.scheme
    return CaseRun(case, scheme.id if scheme else None, agent.state.max_stage, agent.turns)


def score(model: str, runs: list[CaseRun]) -> AgentRow:
    """Сводит прогоны одной модели в метрики."""
    fraud = [r for r in runs if r.case.is_fraud]
    normal = [r for r in runs if not r.case.is_fraud]
    right_scheme = [r for r in fraud if r.scheme_id == r.case.expected_scheme]
    latencies = sorted(t.latency_s for r in runs for t in r.turns)
    correct = len(right_scheme) + sum(r.scheme_id is None for r in normal)
    return AgentRow(
        model=model,
        scheme_acc=len(right_scheme) / len(fraud),
        # Этап засчитывается только при правильной схеме: «этап 2» чужой схемы — не попадание.
        stage_acc=sum(r.stage_n == r.case.expected_stage for r in right_scheme) / len(fraud),
        false_alarms=sum(r.scheme_id is not None for r in normal) / len(normal),
        accuracy=correct / len(runs),
        p50_s=statistics.median(latencies),
        p95_s=latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))],
        # Сбой — ход, где поиск дал кандидатов, а ответа LLM нет.
        llm_failures=sum(t.analysis is None for r in runs for t in r.turns if t.candidates),
        runs=runs,
    )


def replay(run: CaseRun, threshold: float, schemes: dict[str, Scheme]) -> tuple[str | None, int | None]:
    """Применяет правило тревоги к записанным ответам модели с другим порогом.

    Это приближение: в промпт попадает уже объявленная схема, поэтому
    с другим порогом модель могла бы ответить на следующих ходах иначе.
    Зато подбор порога не требует перезапускать LLM на каждое значение.
    """
    scheme_id: str | None = None
    stage: int | None = None
    max_stage: int | None = None
    confidence: float | None = None
    challenger: str | None = None
    for turn in run.turns:
        if turn.analysis is None:
            continue
        decision = decide(
            turn.analysis, turn.caller_text, set(turn.candidates), threshold,
            scheme_id, stage, confidence, challenger,
        )
        challenger = decision.challenger
        if decision.scheme_id is not None and turn.analysis.scheme_id == decision.scheme_id:
            confidence = turn.analysis.confidence  # как в CallAgent._decide
        if decision.scheme_id != scheme_id:
            scheme_id, stage = decision.scheme_id, None
        # Та же проверка номера этапа, что в CallAgent._update_stage.
        if scheme_id and decision.stage_n and 1 <= decision.stage_n <= len(schemes[scheme_id].stages):
            stage = decision.stage_n
            max_stage = max(max_stage or 0, stage)
    return scheme_id, max_stage


def threshold_sweep(row: AgentRow, schemes: dict[str, Scheme]) -> list[tuple[float, float, float]]:
    """Для каждого порога: (порог, доля найденных схем, доля ложных тревог).

    Чем выше порог, тем меньше ложных тревог, но и тем больше пропущенных
    мошенников. Таблица показывает, где компромисс.
    """
    fraud = [r for r in row.runs if r.case.is_fraud]
    normal = [r for r in row.runs if not r.case.is_fraud]
    sweep = []
    for th in THRESHOLDS:
        found = sum(replay(r, th, schemes)[0] == r.case.expected_scheme for r in fraud)
        alarms = sum(replay(r, th, schemes)[0] is not None for r in normal)
        sweep.append((th, found / len(fraud), alarms / len(normal)))
    return sweep


def missing_model(settings: Settings, model: str) -> str | None:
    """Почему модель нельзя прогнать (Ollama не запущена или модель не скачана), или None."""
    try:
        installed = {m.model for m in ollama.Client(host=settings.ollama_host).list().models}
    except ConnectionError as error:
        return f"Ollama недоступна: {error}"
    # Ollama хранит модель без тега как "<имя>:latest".
    if model not in installed and f"{model}:latest" not in installed:
        return f"модель не скачана: ollama pull {model}"
    return None


async def eval_agent(
    settings: Settings, model: str, retriever: Retriever, mode: RetrievalMode, cases: list[EvalCase]
) -> AgentRow:
    """Прогоняет агента с моделью `model` по всему набору."""
    if reason := missing_model(settings, model):
        return AgentRow(model=model, unavailable=reason)
    # Настоящее хранилище, но на временном файле: прогон не должен
    # засорять рабочую базу тестовыми кейсами.
    from storage import db  # noqa: PLC0415

    db.init_db(str(Path(tempfile.mkdtemp()) / "eval.db"))
    run_settings = settings.model_copy(update={"llm_model": model, "retrieval_mode": mode})
    llm = OllamaLLM(model, settings.ollama_host, temperature=settings.llm_temperature, retries=settings.llm_retries)
    deps = AgentDeps(retriever=retriever, llm=llm, settings=run_settings)
    server = build_server(retriever, db, load_schemes(settings.schemes_dir))
    # Фрагменты по очереди, не параллельно: иначе модели в Ollama
    # конкурировали бы за CPU/GPU, и замер задержки был бы нечестным.
    runs = [await run_case(case, deps, server) for case in cases]
    return score(model, runs)


# --- отчёт ---


def pct(x: float) -> str:
    """Доля в процентах без дробной части: 0.894 → "89%"."""
    return f"{100 * x:.0f}%"


def render(
    settings: Settings,
    cases: list[EvalCase],
    k: int,
    retrieval: list[RetrievalRow],
    agent_mode: str,
    agents: list[AgentRow],
    schemes: dict[str, Scheme],
) -> str:
    """Собирает отчёт в Markdown: таблица поиска, таблица агента, подбор порога и список ошибок."""
    fraud = sum(c.is_fraud for c in cases)
    lines = [
        f"# Оценка качества — {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Набор: {len(cases)} фрагментов ({fraud} мошеннических, {len(cases) - fraud} обычных). "
        f"Порог тревоги: {settings.detect_threshold}. Окно: {settings.window_size} реплик.",
        "",
        f"## Поиск (на {fraud} мошеннических фрагментах, k={k})",
        "",
        "| Режим | hit@1 | hit@k | MRR | этап в top-k | задержка, мс |",
        "|---|---|---|---|---|---|",
    ]
    for r in retrieval:
        if r.unavailable:
            lines.append(f"| {r.mode} | — | — | — | — | — |")
        else:
            lines.append(
                f"| {r.mode} | {pct(r.hit1)} | {pct(r.hitk)} | {r.mrr:.2f} | {pct(r.stage_hitk)} | {r.latency_ms:.1f} |"
            )
    lines += [f"- {r.mode}: {r.unavailable}" for r in retrieval if r.unavailable]

    lines += [
        "",
        f"## Агент (поиск: {agent_mode})",
        "",
        "| Модель | схема | этап | ложные тревоги | общая точность | задержка p50, с | p95, с | сбои LLM |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in agents:
        if a.unavailable:
            lines.append(f"| {a.model} | — | — | — | — | — | — | — |")
        else:
            lines.append(
                f"| {a.model} | {pct(a.scheme_acc)} | {pct(a.stage_acc)} | {pct(a.false_alarms)} | "
                f"{pct(a.accuracy)} | {a.p50_s:.1f} | {a.p95_s:.1f} | {a.llm_failures} |"
            )
    lines += [f"- {a.model}: {a.unavailable}" for a in agents if a.unavailable]

    for a in agents:
        if a.unavailable:
            continue
        lines += [
            "",
            f"### Порог тревоги, {a.model}",
            "",
            "Пересчёт правила по записанным ответам модели (приближённо: в промпт попадает уже найденная схема).",
            "",
            "| Порог | схема найдена | ложные тревоги |",
            "|---|---|---|",
        ]
        lines += [f"| {th} | {pct(found)} | {pct(alarms)} |" for th, found, alarms in threshold_sweep(a, schemes)]
        # Список ошибок по id фрагментов: по нему удобно разбирать, что пошло не так.
        errors = [r for r in a.runs if r.scheme_id != r.case.expected_scheme]
        if errors:
            lines += ["", f"Ошибки {a.model}:", ""]
            lines += [f"- {r.case.id}: ожидали {r.case.expected_scheme}, получили {r.scheme_id}" for r in errors]
    return "\n".join(lines) + "\n"


def main() -> None:
    """Точка входа `python -m eval.run_eval`: считает всё доступное и пишет отчёт в eval/results/."""
    parser = argparse.ArgumentParser(description="Evaluate retrieval and the agent on data/eval/cases.jsonl.")
    parser.add_argument("--models", nargs="+", default=["qwen3:8b", "qwen3:4b"])
    parser.add_argument("--agent-mode", default=None, help="retrieval mode for the agent (default: best available)")
    parser.add_argument("--skip-agent", action="store_true", help="only retrieval metrics, no LLM")
    parser.add_argument("--limit", type=int, default=None, help="use only the first N cases (quick check)")
    parser.add_argument("--out", default=str(RESULTS_DIR / "latest.md"))
    args = parser.parse_args()

    settings = get_settings()
    cases = load_cases()[: args.limit]
    schemes = load_schemes(settings.schemes_dir)
    k = settings.top_k

    retriever, missing = build_eval_retriever(settings)
    retrieval = [
        RetrievalRow(mode, unavailable=missing[mode]) if mode in missing else eval_retrieval(retriever, cases, mode, k)
        for mode in MODES
    ]

    # Агент по умолчанию работает с самым полным доступным режимом поиска:
    # MODES перечислены от простого к сложному, reversed даёт лучший первым.
    available = [m for m in reversed(MODES) if m not in missing]
    agent_mode: RetrievalMode = args.agent_mode or available[0]
    agents: list[AgentRow] = []
    if not args.skip_agent:
        agents = [asyncio.run(eval_agent(settings, m, retriever, agent_mode, cases)) for m in args.models]

    report = render(settings, cases, k, retrieval, agent_mode, agents, schemes)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
