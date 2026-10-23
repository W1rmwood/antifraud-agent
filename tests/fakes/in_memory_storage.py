"""Storage kept in a dict instead of SQLite.

Satisfies `contracts.Storage` with the same semantics as storage/db.py:
saving a case with an existing call_id replaces it, an unknown number has
calls_count=0, numbers are normalized to +7XXXXXXXXXX or rejected.
"""

import re

from contracts import CallCase, NumberInfo


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError(f"not a Russian phone number: {phone!r}")
    return "+7" + digits


class InMemoryStorage:
    def __init__(self) -> None:
        self._cases: dict[str, CallCase] = {}
        self._tags: dict[str, list[str]] = {}

    def init_db(self, path: str) -> None:
        self._cases.clear()
        self._tags.clear()

    def lookup_number(self, phone: str) -> NumberInfo:
        phone = normalize_phone(phone)
        cases = self.get_cases_by_number(phone)
        times = [c.started_at for c in cases]
        # Ordered by first appearance, like storage/db.py.
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
        if case.phone is not None:
            case = case.model_copy(update={"phone": normalize_phone(case.phone)})
        self._cases[case.call_id] = case
        return case.call_id

    def get_cases_by_number(self, phone: str) -> list[CallCase]:
        phone = normalize_phone(phone)
        cases = [c for c in self._cases.values() if c.phone == phone]
        return sorted(cases, key=lambda c: c.started_at, reverse=True)

    def list_recent_cases(self, limit: int = 20) -> list[CallCase]:
        return sorted(self._cases.values(), key=lambda c: c.started_at, reverse=True)[
            : max(0, limit)
        ]

    def tag_number(self, phone: str, tag: str) -> None:
        tag = tag.strip()
        if not tag:
            raise ValueError("empty tag")
        tags = self._tags.setdefault(normalize_phone(phone), [])
        if tag not in tags:
            tags.append(tag)
