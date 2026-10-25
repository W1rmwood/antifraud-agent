"""Промпты и схемы ответов для LLM.

Модель только сообщает, что видит (схему, этап, уверенность, цитаты).
Поднимать ли тревогу, решает код в `agent.policy`. Так правило можно
проверить тестами, а его порог подобрать в eval, а не прятать в промпте.
"""

from typing import Literal

from pydantic import BaseModel, Field

from contracts import Scheme, Speaker, Utterance
from rag.retriever import Hit

SPEAKER_LABEL = {Speaker.caller: "Звонящий", Speaker.user: "Абонент", Speaker.unknown: "Голос"}


# Описания полей (description) попадают в JSON-схему, которую получает модель,
# поэтому они написаны для неё и на русском, как и весь промпт.
class Analysis(BaseModel):
    """Разбор текущего момента разговора."""

    scheme_id: str | None = Field(description="id схемы из списка кандидатов или null для обычного звонка")
    stage_n: int | None = Field(ge=1, description="номер этапа выбранной схемы или null")
    confidence: float = Field(ge=0.0, le=1.0, description="уверенность, что идёт именно эта схема")
    hard_red_flags: list[str] = Field(
        description="дословные цитаты звонящего с жёсткими требованиями; пусто, если их нет"
    )
    evidence: list[str] = Field(max_length=3, description="до трёх дословных цитат, на которых основан вывод")
    suggestion: str = Field(description="короткая реплика, которую абонент может сказать, чтобы затянуть разговор")
    suggestion_kind: Literal["stall", "question"]
    warning: str | None = Field(description="короткое предупреждение абоненту или null")


class CallSummary(BaseModel):
    """Отчёт о звонке для журнала."""

    legend: str | None = Field(description="кем представлялся звонящий или null")
    key_phrases: list[str] = Field(max_length=5, description="до пяти ключевых дословных фраз звонящего")
    summary: str = Field(description="2–3 предложения: что произошло в звонке")


SYSTEM_ANALYZE = """\
Ты помощник, который в реальном времени слушает телефонный разговор и распознаёт схемы телефонного мошенничества.

Тебе дают кандидатов — этапы известных схем, найденные поиском по базе, — и последние реплики разговора.
Определи, совпадает ли разговор с одной из схем-кандидатов и на каком она этапе.

Правила:
- scheme_id выбирай только из кандидатов. Если разговор обычный (настоящий банк, курьер, врач, знакомый) — scheme_id и stage_n равны null.
- Настоящие организации тоже звонят. Слова «банк», «код», «карта», «счёт» сами по себе не признак мошенничества.
- hard_red_flags — только дословные цитаты звонящего, где он ПРОСИТ: код из СМС или приложения, данные карты (номер, срок, CVV), перевести деньги или внести наличные, снять наличные или передать их курьеру, взять кредит, установить приложение или дать доступ к экрану, паспорт или СНИЛС. Не выдумывай и не пересказывай цитаты.
- confidence: 0.9 и выше — разговор явно идёт по схеме; около 0.5 — похоже, но не точно; 0.2 и ниже — скорее обычный звонок.
- suggestion — одна короткая естественная фраза для абонента, которая затягивает разговор и ничего не выдаёт: уточняющий вопрос (question) или повод потянуть время (stall). Никогда не предлагай назвать код, данные или перевести деньги.
- warning — одна короткая фраза: что опасного просит звонящий и чего делать нельзя. Если опасности нет — null.
Отвечай только JSON по схеме."""

SYSTEM_SUMMARY = """\
Ты составляешь краткий отчёт о телефонном звонке для журнала мошеннических звонков.
Используй только то, что есть в расшифровке. Отвечай только JSON по схеме."""


def format_transcript(utterances: list[Utterance]) -> str:
    """Реплики в виде «Звонящий: …» построчно."""
    return "\n".join(f"{SPEAKER_LABEL[u.speaker]}: {u.text}" for u in utterances)


def format_candidates(hits: list[Hit]) -> str:
    """Найденные этапы с пометкой [scheme_id, этап N], чтобы модель могла сослаться на id."""
    return "\n\n".join(f"[{h.scheme_id}, этап {h.stage_n}]\n{h.text}" for h in hits)


def format_scheme(scheme: Scheme) -> str:
    """Схема целиком, коротко: все этапы и тактики затягивания."""
    stages = "\n".join(f"  {s.n}. {s.name}: {s.goal}. Просят: {', '.join(s.asks_for) or '—'}" for s in scheme.stages)
    tactics = "\n".join(f"  - {t}" for t in scheme.stall_tactics)
    return f"{scheme.id} — {scheme.name}\nЭтапы:\n{stages}\nКак тянуть время:\n{tactics}"


def analysis_messages(
    window: list[Utterance], hits: list[Hit], current: Scheme | None, current_stage: int | None
) -> list[dict[str, str]]:
    """Сообщения для разбора очередной реплики: кандидаты, текущая схема (если есть) и окно реплик."""
    parts = [f"Кандидаты:\n{format_candidates(hits)}"]
    if current is not None:
        # Поиск по последним репликам может не найти уже объявленную схему
        # (например, на этапе изоляции мошенник говорит общие слова).
        # Поэтому схему целиком даём модели явно: так она может отслеживать
        # этапы, даже если поиск этот этап не вернул.
        parts.append(
            f"Ранее определена схема (этап {current_stage}). Если разговор идёт по ней, "
            f"выбери её и актуальный этап:\n{format_scheme(current)}"
        )
    parts.append(f"Последние реплики:\n{format_transcript(window)}")
    return [
        {"role": "system", "content": SYSTEM_ANALYZE},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def summary_messages(
    transcript: list[Utterance], scheme: Scheme | None, max_stage: int | None
) -> list[dict[str, str]]:
    """Сообщения для итогового отчёта: вывод агента и полная расшифровка звонка."""
    verdict = (
        f"Агент определил схему «{scheme.name}», дошли до этапа {max_stage}."
        if scheme is not None
        else "Агент не определил мошенническую схему."
    )
    return [
        {"role": "system", "content": SYSTEM_SUMMARY},
        {"role": "user", "content": f"{verdict}\n\nРасшифровка:\n{format_transcript(transcript)}"},
    ]
