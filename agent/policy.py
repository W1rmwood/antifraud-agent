"""Правила, которые решает код, а не модель: когда сказать «это мошенник» и когда начать его бесить.

Схема объявляется, только если модель достаточно уверена И звонящий
действительно произнёс хотя бы один жёсткий красный флаг. «Действительно
произнёс» проверяется здесь, в коде: цитаты модели должны найтись в словах
звонящего. Поэтому выдуманный моделью флаг не поднимет тревогу на обычном звонке.
"""

from dataclasses import dataclass

from agent.prompts import Analysis
from contracts import Scheme
from rag.chunks import tokenize

QUOTE_OVERLAP = 0.7  # какая доля слов цитаты должна встретиться в речи звонящего


def quote_is_said(quote: str, caller_text: str) -> bool:
    """True, если цитата есть в тексте, с допуском на мелкий пересказ и другие формы слов."""
    # Сравниваем не строки целиком, а обрезанные слова (как в BM25): модель
    # часто меняет падеж или пропускает слово, и точное совпадение строк
    # отбросило бы настоящие флаги. 70% слов — компромисс: пересказ проходит,
    # а выдуманная фраза — нет.
    quote_tokens = tokenize(quote)
    if not quote_tokens:
        return False
    said = set(tokenize(caller_text))
    return sum(t in said for t in quote_tokens) / len(quote_tokens) >= QUOTE_OVERLAP


SWITCH_MARGIN = 0.15  # ТЗ, «Поведение агента»: на сколько новая схема должна быть увереннее текущей


@dataclass(frozen=True)
class Decision:
    """Итог правила: какая схема и этап сейчас объявлены и какие флаги подтверждены."""

    scheme_id: str | None
    stage_n: int | None
    flags: list[str]  # жёсткие красные флаги, найденные в словах звонящего
    # Схема, которая на этом ходу обошла объявленную, но сменить её пока не смогла.
    # Если она выиграет и на следующем ходу — схема сменится.
    challenger: str | None = None

    @property
    def declared(self) -> bool:
        """Объявлена ли схема."""
        return self.scheme_id is not None


def decide(
    analysis: Analysis,
    caller_text: str,
    candidates: set[str],
    threshold: float,
    current: str | None = None,
    current_stage: int | None = None,
    current_confidence: float | None = None,
    challenger: str | None = None,
    margin: float = SWITCH_MARGIN,
) -> Decision:
    """Решает по ответу модели, объявлять ли схему.

    `candidates` — схемы, которые нашёл поиск; `current`, `current_stage` и
    `current_confidence` — уже объявленные схема, этап и уверенность в ней;
    `challenger` — схема, которая обошла объявленную на прошлом ходу.
    """
    # Оставляем только флаги, которые звонящий правда произнёс.
    flags = [q for q in analysis.hard_red_flags if quote_is_said(q, caller_text)]
    kept = Decision(current, current_stage, flags)

    if current is not None and analysis.scheme_id == current:
        # Объявленная схема держится: чтобы следить за её этапами, новые флаги не нужны.
        # Иначе на этапе «изоляции», где мошенник ничего не просит, тревога бы пропала.
        return Decision(current, analysis.stage_n or current_stage, flags)

    qualifies = (
        analysis.scheme_id is not None
        # Модель могла назвать схему, которой не было среди кандидатов (выдумать id).
        and analysis.scheme_id in candidates
        and analysis.confidence >= threshold
        and bool(flags)
    )
    if not qualifies:
        # Неуверенный ответ не объявляет новую схему, но и не снимает уже объявленную:
        # одна неудачная реплика не должна сбрасывать тревогу.
        return kept
    if current is None:
        return Decision(analysis.scheme_id, analysis.stage_n, flags)

    # Смена уже объявленной схемы (ТЗ, «Устойчивость»): только если новая увереннее
    # на запас или выигрывает второй ход подряд. Иначе подсказки и этапы в UI
    # скакали бы между схемами на каждой реплике.
    clearly_better = analysis.confidence >= (current_confidence or 0.0) + margin
    if clearly_better or analysis.scheme_id == challenger:
        return Decision(analysis.scheme_id, analysis.stage_n, flags)
    return Decision(current, current_stage, flags, challenger=analysis.scheme_id)


# Безопасный выход, который ТЗ требует держать среди подсказок: закончить разговор
# и проверить всё самому по официальному каналу. Текст зависит от того, кем
# представляется мошенник, — «перезвоните в банк» не поможет, если звонил «внук».
HANGUP_BY_CATEGORY: dict[str, str] = {
    "bank": "Положите трубку и перезвоните в банк по номеру с обратной стороны карты.",
    "gosuslugi": "Положите трубку. Зайдите в Госуслуги сами или позвоните по номеру с официального сайта — "
    "настоящая поддержка коды не спрашивает.",
    "relative": "Положите трубку и перезвоните родственнику на его обычный номер.",
    "investment": "Положите трубку. Проверьте компанию в справочнике участников финансового рынка на сайте ЦБ.",
    "delivery": "Положите трубку. Проверьте заказ в приложении магазина или службы доставки.",
}
HANGUP_DEFAULT = "Положите трубку. Если звонили от организации — перезвоните ей по номеру с официального сайта."


def hangup_text(scheme: Scheme) -> str:
    """Подсказка «закончить разговор» для схемы — её пишет код, а не модель."""
    return HANGUP_BY_CATEGORY.get(scheme.category, HANGUP_DEFAULT)


def provoke_mode(stage_n: int | None, n_stages: int) -> bool:
    """Пора ли бесить мошенника, или ещё подыгрывать ему.

    Вызывается только для объявленной схемы, а объявление требует жёсткого
    флага: мошенник уже что-то попросил. Поэтому граница «никакой грубости
    в обычном звонке» соблюдена до этой функции, а здесь решается только
    эскалация. Первая половина схемы — наживка (тупить и соглашаться, чтобы
    мошенник вложился), вторая, ближе к деньгам, — провокация.
    Этап неизвестен — подыгрываем: это безопаснее.
    """
    return stage_n is not None and stage_n > n_stages / 2
