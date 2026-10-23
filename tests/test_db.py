from datetime import datetime, timedelta

import pytest

from contracts.models import CallCase, NumberInfo
from storage import db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.init_db(str(tmp_path / "test.db"))


def make_case(call_id: str, phone: str | None, days_ago: int = 0, scheme_id: str | None = "safe-account",
              **kw) -> CallCase:
    started = datetime(2026, 10, 20, 12, 0) - timedelta(days=days_ago)
    return CallCase(
        call_id=call_id, phone=phone, started_at=started, ended_at=started + timedelta(minutes=5),
        scheme_id=scheme_id, max_stage=2, legend="сотрудник ЦБ", key_phrases=["безопасный счёт"],
        summary="тест", transcript_path=f"data/runs/{call_id}.jsonl", **kw,
    )


# --- нормализация номеров ---

@pytest.mark.parametrize("raw, expected", [
    ("+79991234567", "+79991234567"),
    ("89991234567", "+79991234567"),
    ("8 (999) 123-45-67", "+79991234567"),
    ("+7 999 123 45 67", "+79991234567"),
    ("7-999-123-45-67", "+79991234567"),
    ("9991234567", "+79991234567"),
    ("  8 999 123.45.67 ", "+79991234567"),
    ("+380 44 123 45 67", "+380441234567"),
    ("+1 (202) 555-0123", "+12025550123"),
    ("+375291234567", "+375291234567"),
])
def test_normalize_phone(raw, expected):
    assert db.normalize_phone(raw) == expected


@pytest.mark.parametrize("garbage", ["", "   ", "abc", "номер неизвестен", "()--", "+", "8999abc1234", "7+999"])
def test_normalize_phone_garbage(garbage):
    with pytest.raises(ValueError):
        db.normalize_phone(garbage)


# --- init_db ---

def test_init_db_idempotent(tmp_path):
    path = tmp_path / "sub" / "x.db"
    db.init_db(str(path))
    db.save_case(make_case("c1", "+79991234567"))
    db.init_db(str(path))  # повторный вызов не стирает данные
    assert path.exists()
    assert db.lookup_number("+79991234567").calls_count == 1


# --- lookup_number ---

def test_lookup_unknown_number():
    info = db.lookup_number("+79990009999")
    assert isinstance(info, NumberInfo)
    assert info.calls_count == 0
    assert info.first_seen is None and info.last_seen is None
    assert info.schemes == [] and info.tags == []


def test_lookup_normalizes_input():
    db.save_case(make_case("c1", "8 (999) 123-45-67"))
    info = db.lookup_number("+7 999 123 45 67")
    assert info.phone == "+79991234567"
    assert info.calls_count == 1


def test_lookup_garbage_raises():
    with pytest.raises(ValueError):
        db.lookup_number("мусор")


def test_lookup_history():
    db.save_case(make_case("c1", "+79991234567", days_ago=10, scheme_id="safe-account"))
    db.save_case(make_case("c2", "89991234567", days_ago=3, scheme_id="gosuslugi"))
    db.save_case(make_case("c3", "+79991234567", days_ago=1, scheme_id="safe-account"))
    db.save_case(make_case("c4", "+79991234567", days_ago=0, scheme_id=None))
    info = db.lookup_number("+79991234567")
    assert info.calls_count == 4
    assert info.first_seen == datetime(2026, 10, 10, 12, 0)
    assert info.last_seen == datetime(2026, 10, 20, 12, 0)
    assert info.schemes == ["safe-account", "gosuslugi"]


# --- save_case ---

def test_save_case_returns_id_and_roundtrips():
    case = make_case("c1", "+79991234567")
    assert db.save_case(case) == "c1"
    [stored] = db.get_cases_by_number("+79991234567")
    assert stored == case


def test_save_case_normalizes_phone():
    db.save_case(make_case("c1", "8 999 123-45-67"))
    [stored] = db.list_recent_cases()
    assert stored.phone == "+79991234567"


def test_save_case_without_phone():
    db.save_case(make_case("c1", None))
    [stored] = db.list_recent_cases()
    assert stored.phone is None


def test_save_case_twice_does_not_double_count():
    db.save_case(make_case("c1", "+79991234567"))
    db.save_case(make_case("c1", "+79991234567", scheme_id="gosuslugi"))
    info = db.lookup_number("+79991234567")
    assert info.calls_count == 1
    assert info.schemes == ["gosuslugi"]


def test_save_case_phone_change_recounts_old_number():
    db.save_case(make_case("c1", "+79991111111"))
    db.save_case(make_case("c1", "+79992222222"))
    assert db.lookup_number("+79991111111").calls_count == 0
    assert db.lookup_number("+79992222222").calls_count == 1


def test_save_case_garbage_phone_raises():
    with pytest.raises(ValueError):
        db.save_case(make_case("c1", "не номер"))


def test_save_case_accepts_dict():
    db.save_case(make_case("c1", "+79991234567").model_dump(mode="json"))
    assert db.lookup_number("+79991234567").calls_count == 1


# --- get_cases_by_number ---

def test_get_cases_by_number_order_and_filter():
    db.save_case(make_case("old", "+79991234567", days_ago=5))
    db.save_case(make_case("new", "+79991234567", days_ago=1))
    db.save_case(make_case("other", "+79990000000", days_ago=0))
    assert [c.call_id for c in db.get_cases_by_number("89991234567")] == ["new", "old"]


def test_get_cases_by_unknown_number():
    assert db.get_cases_by_number("+79990009999") == []


# --- list_recent_cases ---

def test_list_recent_cases_limit_and_order():
    for i in range(25):
        db.save_case(make_case(f"c{i:02d}", "+79991234567", days_ago=i))
    recent = db.list_recent_cases()
    assert len(recent) == 20
    assert recent[0].call_id == "c00"
    assert [c.call_id for c in db.list_recent_cases(limit=3)] == ["c00", "c01", "c02"]


def test_list_recent_cases_empty():
    assert db.list_recent_cases() == []


# --- tag_number ---

def test_tag_number_unknown_and_dedup():
    db.tag_number("8 (999) 000-00-01", "fraud")
    db.tag_number("+79990000001", "fraud")
    db.tag_number("+79990000001", "bank")
    info = db.lookup_number("+79990000001")
    assert info.tags == ["fraud", "bank"]
    assert info.calls_count == 0


def test_tag_survives_save_case():
    db.tag_number("+79991234567", "fraud")
    db.save_case(make_case("c1", "+79991234567"))
    info = db.lookup_number("+79991234567")
    assert info.tags == ["fraud"] and info.calls_count == 1


def test_tag_number_garbage():
    with pytest.raises(ValueError):
        db.tag_number("xxx", "fraud")
    with pytest.raises(ValueError):
        db.tag_number("+79991234567", "  ")


# --- демо-БД ---

def test_seed_demo(tmp_path):
    from storage import seed_demo
    n = seed_demo.seed(str(tmp_path / "demo.db"))
    assert n >= 20
    assert len({p for p, *_ in seed_demo.DEMO_NUMBERS}) == 20
    info = db.lookup_number("+79990000001")
    assert info.calls_count == 4 and "fraud" in info.tags
