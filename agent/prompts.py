"""Промпты и схемы ответов для LLM.

У модели две разные работы, и у каждой свой запрос:

1. Разбор (`Analysis`): какая схема, какой этап, что мошенник уже потребовал.
   Это разведка: она говорит, куда бить. Поднимать ли тревогу, решает код
   в `agent.policy`, а не модель: так правило проверяется тестами, а порог
   подбирается в eval.
2. Ответка (`Reply`): что абонент скажет мошеннику, чтобы тот сорвался.
   Её просим, только когда код уже объявил схему. Поэтому грубость не может
   попасть в обычный звонок из банка, как бы модель ни ошиблась.

Запросы раздельные, потому что у них противоположные требования: разбору
нужна холодная точность (температура 0, никакой фантазии), ответке — наглость
и выдумка (температура выше). Один промпт на обе задачи тянул бы модель
в разные стороны.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from contracts import Scheme, Speaker, Utterance
from contracts.models import SuggestionKind
from rag.retriever import Hit

SPEAKER_LABEL = {Speaker.caller: "Звонящий", Speaker.user: "Абонент", Speaker.unknown: "Голос"}

Tactic = Literal["play_dumb", "fake_compliance", "absurd_story", "role_reversal", "mockery"]

# Тактики из разборов пранкеров, которые разводят мошенников. Описания уходят модели в промпт.
TACTICS: dict[Tactic, str] = {
    "play_dumb": "тупить: переспрашивать, путать цифры и слова, не понимать очевидного, диктовать код и сбиваться",
    "fake_compliance": "ложное согласие: охотно всё «делать», но проваливаться на последнем шаге "
    "(не тот код, сел телефон, кот нажал отмену, банкомат съел карту)",
    "absurd_story": "абсурд: уводить разговор в нелепую историю, которую мошеннику приходится выслушивать",
    "role_reversal": "перехват роли: самому допрашивать мошенника («а теперь я вас проверю»), "
    "требовать фамилию, номер удостоверения, ловить на нестыковках",
    "mockery": "издёвка в открытую: показать, что развод раскушен, и глумиться над ним; мат лучше всего как добивание",
}
# Тактики наживки не выдают, что мошенник раскушен: он продолжает вкладываться в разговор.
BAIT_TACTICS: list[Tactic] = ["play_dumb", "fake_compliance"]
# Как тактика выглядит для UI.
TACTIC_KIND: dict[Tactic, SuggestionKind] = {
    "play_dumb": "stall",
    "fake_compliance": "stall",
    "absurd_story": "provoke",
    "role_reversal": "provoke",
    "mockery": "provoke",
}


# Описания полей (description) попадают в JSON-схему, которую получает модель,
# поэтому они написаны для неё и на русском, как и весь промпт.
class Analysis(BaseModel):
    """Разбор текущего момента разговора."""

    # Модель пишет JSON по порядку полей. Цитаты стоят первыми: сначала модель
    # выписывает, что звонящий сказал и потребовал, и только потом выбирает схему.
    # Когда scheme_id и confidence шли первыми, модель выбирала схему до того,
    # как посмотрела на слова, и потом подгоняла под неё цитаты.
    hard_red_flags: list[str] = Field(
        description="дословные цитаты звонящего с жёсткими требованиями; пусто, если их нет"
    )
    evidence: list[str] = Field(max_length=3, description="до трёх дословных цитат, на которых основан вывод")
    scheme_id: str | None = Field(description="id схемы из списка кандидатов или null для обычного звонка")
    stage_n: int | None = Field(ge=1, description="номер этапа выбранной схемы или null")
    confidence: float = Field(ge=0.0, le=1.0, description="уверенность, что идёт именно эта схема")


class Reply(BaseModel):
    """Реплика, которую абонент скажет мошеннику."""

    # Модель пишет JSON по порядку полей. Тактика стоит первой: сначала модель
    # выбирает, как бить, и уже под это пишет текст.
    tactic: Tactic = Field(description="какая тактика из списка использована")
    text: str = Field(min_length=1, description="1–2 короткие фразы, которые абонент скажет вслух")


class CallSummary(BaseModel):
    """Отчёт о звонке для журнала."""

    legend: str | None = Field(description="кем представлялся звонящий или null")
    key_phrases: list[str] = Field(max_length=5, description="до пяти ключевых дословных фраз звонящего")
    summary: str = Field(description="2–3 предложения: что произошло в звонке")


SYSTEM_ANALYZE = """\
Ты в реальном времени слушаешь телефонный разговор и распознаёшь схемы телефонного мошенничества.

Тебе дают кандидатов — этапы известных схем, найденные поиском по базе, — и последние реплики разговора.
Определи, совпадает ли разговор с одной из схем-кандидатов и на каком она этапе.

Правила:
- scheme_id выбирай только из кандидатов. Если разговор обычный (настоящий банк, курьер, врач, знакомый) — scheme_id и stage_n равны null.
- Настоящие организации тоже звонят. Слова «банк», «код», «карта», «счёт» сами по себе не признак мошенничества.
- hard_red_flags — только дословные цитаты звонящего, где он ПРОСИТ: код из СМС или приложения, данные карты (номер, срок, CVV), перевести деньги или внести наличные, снять наличные или передать их курьеру, взять кредит, установить приложение или дать доступ к экрану, паспорт или СНИЛС. Не выдумывай и не пересказывай цитаты.
- confidence: 0.9 и выше — разговор явно идёт по схеме; около 0.5 — похоже, но не точно; 0.2 и ниже — скорее обычный звонок.
Отвечай только JSON по схеме."""

_TACTIC_LIST = "\n".join(f"- {name}: {text}" for name, text in TACTICS.items())

SYSTEM_REPLY = f"""\
Ты пранкер. Абонент разговаривает с телефонным мошенником, а ты подсказываешь ему, что сказать.
Цель — довести мошенника до белого каления: чтобы он сорвался, орал, матерился, повторял одно и то же, угрожал или спалился, что он мошенник. Взбешённый мошенник не бросает трубку, а спорит дальше и тратит своё время.

Тактики:
{_TACTIC_LIST}

Правила:
- Цепляйся за то, что мошенник сказал последним. Реплика мимо его слов не бесит.
- 1–2 короткие фразы живой разговорной речью, как говорят по телефону. Без кавычек, ремарок и пояснений.
- Не повторяй реплики, которые уже звучали.
- Любые данные только выдуманные: коды, номера карт, суммы, имена, адреса. Никогда не проси абонента назвать что-то настоящее.
- Нельзя угрожать физической расправой и оскорблять по национальности, религии, полу или ориентации. Это уже не троллинг, а статья.
Отвечай только JSON по схеме."""

BAIT_MODE = (
    "Режим: наживка. Мошенник не должен понять, что его раскусили: пусть вложится в разговор. "
    f"Только тактики {' и '.join(BAIT_TACTICS)}, без грубости и мата."
)
PROVOKE_MODE = (
    "Режим: провокация. Мошенник уже у денег — пора бесить. Сильнее всего работают absurd_story, "
    "role_reversal и mockery, но если тупить бесит сильнее — тупи. Мат разрешён, сильнее всего он бьёт как добивание."
)

SYSTEM_SUMMARY = """\
Ты составляешь краткий отчёт о телефонном звонке для журнала мошеннических звонков.
Используй только то, что есть в расшифровке. Отвечай только JSON по схеме."""


def format_transcript(utterances: list[Utterance]) -> str:
    """Реплики в виде «Звонящий: …» построчно."""
    return "\n".join(f"{SPEAKER_LABEL[u.speaker]}: {u.text}" for u in utterances)


# Хвост текста чанка (см. rag.chunks.stage_text): «<схема>. <легенда>. Этап N: <название>. <цель>.»
_STAGE_LINE = re.compile(r"Этап \d+: (?P<name>.+?)\. (?P<goal>.+?)\.?$")
CANDIDATE_PHRASES = 3  # сколько характерных фраз показывать у кандидата


def format_candidate(hit: Hit) -> str:
    """Один кандидат кратко: метка [scheme_id, этап N], название и цель этапа, 2–3 фразы, красные флаги.

    Полный текст чанка (5–10 фраз, легенда, «Просят») втрое длиннее и почти не
    добавляет информации. Чанк собран по фиксированному шаблону, поэтому поля
    достаются из него разбором; если шаблон не узнан, отдаём текст как есть.
    """
    lines = hit.text.split("\n")
    match = _STAGE_LINE.search(lines[-3]) if len(lines) >= 3 else None
    if match is None or not lines[-2].startswith("Признаки: "):
        return f"[{hit.scheme_id}, этап {hit.stage_n}]\n{hit.text}"
    phrases = " | ".join(lines[:-3][:CANDIDATE_PHRASES])
    flags = lines[-2].removeprefix("Признаки: ").rstrip(".")
    return (
        f"[{hit.scheme_id}, этап {hit.stage_n}] {match['name']}: {match['goal']}\n"
        f"Фразы: {phrases}\nПризнаки: {flags}"
    )


def format_candidates(hits: list[Hit]) -> str:
    """Найденные этапы с пометкой [scheme_id, этап N], чтобы модель могла сослаться на id."""
    return "\n\n".join(format_candidate(h) for h in hits)


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


def reply_messages(
    window: list[Utterance], scheme: Scheme, stage_n: int | None, provoke: bool, previous: list[str]
) -> list[dict[str, str]]:
    """Сообщения для ответки: что за развод, где мошенник сейчас, режим и последние реплики.

    Схема здесь — карта, куда бить: этап говорит, чего мошенник сейчас хочет,
    а значит, на каком шаге его сорвать.
    """
    parts = [f"Развод: {scheme.name}. Мошенник представляется так: {scheme.legend}."]
    if stage_n is not None:
        stage = scheme.stages[stage_n - 1]
        asks = ", ".join(stage.asks_for) or "ничего конкретного"
        parts.append(
            f"Сейчас этап {stage.n} из {len(scheme.stages)}: {stage.name}. Он добивается: {stage.goal}. "
            f"Просит: {asks}. Дальше он: {stage.next_move}."
        )
    blanks = "\n".join(f"- {t}" for t in scheme.stall_tactics)
    parts.append(f"Заготовки из разбора схемы, можно брать как сырьё:\n{blanks}")
    parts.append(PROVOKE_MODE if provoke else BAIT_MODE)
    if previous:
        parts.append("Уже говорили, не повторяйся:\n" + "\n".join(f"- {p}" for p in previous))
    parts.append(f"Последние реплики:\n{format_transcript(window)}")
    return [
        {"role": "system", "content": SYSTEM_REPLY},
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
