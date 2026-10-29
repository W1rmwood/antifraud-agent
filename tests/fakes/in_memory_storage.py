"""Хранилище в словаре вместо SQLite.

Выполняет контракт `contracts.Storage` с тем же поведением, что storage/db.py:
сохранение кейса с существующим call_id заменяет его, у неизвестного номера
calls_count=0, номера приводятся к виду +7XXXXXXXXXX или отклоняются.
Совпадение поведения проверяют общие тесты в tests/test_fakes.py.
"""

import re

from contracts import CallCase, NumberInfo


def normalize_phone(phone: str) -> str:
    """Упрощённая нормализация: только цифры, 8/7 в начале 11-значного номера → +7."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(f"not a Russian phone number: {phone!r}")
    return "+7" + digits


class InMemoryStorage:
    """Фейковое хранилище. Живёт, пока жив объект; init_db просто всё очищает."""

    def __init__(self) -> None:
        self._cases: dict[str, CallCase] = {}  # call_id → кейс
        self._tags: dict[str, list[str]] = {}  # номер → метки

    def init_db(self, path: str) -> None:
        """Путь не используется: «новая база» — это пустые словари."""
        self._cases.clear()
        self._tags.clear()

    def lookup_number(self, phone: str) -> NumberInfo:
        """История номера, собранная по сохранённым кейсам."""
        phone = normalize_phone(phone)
        cases = self.get_cases_by_number(phone)
        times = [c.started_at for c in cases]
        # Схемы в порядке первого появления, как в storage/db.py.
        schemes: list[str] = []
        for case in sorted(cases, key=lambda c: c.started_at):
            if case.scheme_id is not None and case.scheme_id not in schemes:
                schemes.append(case.scheme_id)
        return NumberInfo(
            phone=phone,
            calls_count=len(cases),
            first_seen=min(times, default=None),
            last_seen=max(times, default=None),
            schemes=schemes,
            tags=list(self._tags.get(phone, [])),
        )

    def save_case(self, case: CallCase) -> str:
        """Сохраняет кейс (повтор с тем же call_id заменяет старый) и возвращает call_id."""
        if case.phone is not None:
            # Модель frozen, поэтому нормализованный номер кладём в копию.
            case = case.model_copy(update={"phone": normalize_phone(case.phone)})
        self._cases[case.call_id] = case
        return case.call_id

    def get_cases_by_number(self, phone: str) -> list[CallCase]:
        """Все кейсы с номера, от новых к старым."""
        phone = normalize_phone(phone)
        cases = [c for c in self._cases.values() if c.phone == phone]
        return sorted(cases, key=lambda c: c.started_at, reverse=True)

    def list_recent_cases(self, limit: int = 20) -> list[CallCase]:
        """Последние кейсы по времени начала. Отрицательный limit даёт пустой список, как в storage/db.py."""
        return sorted(self._cases.values(), key=lambda c: c.started_at, reverse=True)[
            : max(0, limit)
        ]

    def tag_number(self, phone: str, tag: str) -> None:
        """Добавляет номеру метку. Повтор метки игнорируется, пустая метка — ошибка."""
        tag = tag.strip()
        if not tag:
            raise ValueError("empty tag")
        tags = self._tags.setdefault(normalize_phone(phone), [])
        if tag not in tags:
            tags.append(tag)
