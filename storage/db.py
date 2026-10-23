"""SQLite-хранилище номеров и кейсов (контракт №4).

Путь к базе задаёт init_db(path); до вызова берётся переменная окружения
ANTIFRAUD_DB или data/antifraud.db. Каждая функция открывает своё соединение,
поэтому модуль можно звать из разных потоков (MCP-сервер, UI).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from contracts.models import CallCase, NumberInfo

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = os.environ.get("ANTIFRAUD_DB", str(ROOT / "data" / "antifraud.db"))

_db_path: str = DEFAULT_DB_PATH

_ALLOWED_PHONE_CHARS = re.compile(r"^[\d\s()\-+.]*$")


def normalize_phone(phone: str) -> str:
    """Приводит номер к виду +79991234567.

    Ведущая 8 (и 7 без плюса) у 11-значного номера → +7, 10-значный номер на 9 → +79…,
    пробелы, скобки, дефисы и точки убираются, другой код страны сохраняется как +<код>….
    Строка без цифр или с посторонними символами → ValueError.
    """
    if not isinstance(phone, str):
        raise ValueError(f"номер должен быть строкой: {phone!r}")
    raw = phone.strip()
    if not _ALLOWED_PHONE_CHARS.match(raw) or "+" in raw[1:]:
        raise ValueError(f"недопустимые символы в номере: {phone!r}")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError(f"в номере нет цифр: {phone!r}")
    if raw.startswith("+"):
        return "+" + digits
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    return "+" + digits


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_case(row: sqlite3.Row) -> CallCase:
    return CallCase(
        call_id=row["call_id"],
        phone=row["phone"],
        started_at=_dt(row["started_at"]),
        ended_at=_dt(row["ended_at"]),
        scheme_id=row["scheme_id"],
        max_stage=row["max_stage"],
        legend=row["legend"],
        key_phrases=json.loads(row["key_phrases"]),
        summary=row["summary"],
        transcript_path=row["transcript_path"],
    )


def _recount_number(conn: sqlite3.Connection, phone: str) -> None:
    """Пересчитывает calls_count, first_seen и last_seen по таблице calls."""
    conn.execute("INSERT OR IGNORE INTO numbers (phone) VALUES (?)", (phone,))
    conn.execute(
        """
        UPDATE numbers SET
            calls_count = (SELECT COUNT(*) FROM calls WHERE phone = :p),
            first_seen  = (SELECT MIN(started_at) FROM calls WHERE phone = :p),
            last_seen   = (SELECT MAX(started_at) FROM calls WHERE phone = :p)
        WHERE phone = :p
        """,
        {"p": phone},
    )


def init_db(path: str) -> None:
    """Создаёт базу и таблицы (если их нет) и делает её текущей."""
    global _db_path
    _db_path = str(path)
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def lookup_number(phone: str) -> NumberInfo:
    """История номера. Неизвестный номер → calls_count = 0, не ошибка."""
    phone = normalize_phone(phone)
    with _connect() as conn:
        row = conn.execute("SELECT * FROM numbers WHERE phone = ?", (phone,)).fetchone()
        schemes = [
            r["scheme_id"]
            for r in conn.execute(
                """SELECT scheme_id, MIN(started_at) AS first FROM calls
                   WHERE phone = ? AND scheme_id IS NOT NULL
                   GROUP BY scheme_id ORDER BY first""",
                (phone,),
            )
        ]
    if row is None:
        return NumberInfo(phone=phone, calls_count=0, first_seen=None, last_seen=None,
                          schemes=[], tags=[])
    return NumberInfo(
        phone=phone,
        calls_count=row["calls_count"],
        first_seen=_dt(row["first_seen"]),
        last_seen=_dt(row["last_seen"]),
        schemes=schemes,
        tags=json.loads(row["tags"]),
    )


def save_case(case: CallCase) -> str:
    """Сохраняет кейс (повторный вызов с тем же call_id перезаписывает его) и обновляет numbers.

    Возвращает call_id.
    """
    case = CallCase.model_validate(case)
    phone = normalize_phone(case.phone) if case.phone else None
    with _connect() as conn:
        old = conn.execute("SELECT phone FROM calls WHERE call_id = ?", (case.call_id,)).fetchone()
        if phone:
            conn.execute("INSERT OR IGNORE INTO numbers (phone) VALUES (?)", (phone,))
        conn.execute(
            """
            INSERT OR REPLACE INTO calls
                (call_id, phone, started_at, ended_at, scheme_id, max_stage, legend,
                 key_phrases, summary, transcript_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case.call_id, phone, _iso(case.started_at), _iso(case.ended_at),
                case.scheme_id, case.max_stage, case.legend,
                json.dumps(case.key_phrases, ensure_ascii=False), case.summary,
                case.transcript_path,
            ),
        )
        for p in {phone, old["phone"] if old else None} - {None}:
            _recount_number(conn, p)
    return case.call_id


def get_cases_by_number(phone: str) -> list[CallCase]:
    """Все кейсы с номера, от новых к старым."""
    phone = normalize_phone(phone)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM calls WHERE phone = ? ORDER BY started_at DESC", (phone,)
        ).fetchall()
    return [_row_to_case(r) for r in rows]


def list_recent_cases(limit: int = 20) -> list[CallCase]:
    """Последние кейсы по времени начала звонка."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM calls ORDER BY started_at DESC LIMIT ?", (max(0, int(limit)),)
        ).fetchall()
    return [_row_to_case(r) for r in rows]


def tag_number(phone: str, tag: str) -> None:
    """Добавляет метку номеру (номер без звонков тоже можно пометить). Повтор метки игнорируется."""
    phone = normalize_phone(phone)
    tag = tag.strip()
    if not tag:
        raise ValueError("пустая метка")
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO numbers (phone) VALUES (?)", (phone,))
        row = conn.execute("SELECT tags FROM numbers WHERE phone = ?", (phone,)).fetchone()
        tags = json.loads(row["tags"])
        if tag not in tags:
            tags.append(tag)
            conn.execute("UPDATE numbers SET tags = ? WHERE phone = ?",
                         (json.dumps(tags, ensure_ascii=False), phone))
