"""Демо-БД: 20 вымышленных номеров с историей звонков.

    python -m storage.seed_demo [--db data/antifraud.db] [--keep]

По умолчанию база пересоздаётся с нуля. Номера вымышленные (диапазон +7999000xxxx),
реальных номеров и ФИО здесь нет.
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta
from pathlib import Path

from contracts.models import CallCase
from storage import db

# (номер, схема, сколько звонков, метки, легенда)
DEMO_NUMBERS: list[tuple[str, str | None, int, list[str], str | None]] = [
    ("+79990000001", "safe-account", 4, ["fraud", "bank"], "сотрудник службы безопасности ЦБ Андрей Смирнов"),
    ("+79990000002", "relative-accident", 2, ["fraud"], "следователь Сергей Волков"),
    ("+79990000003", "gosuslugi", 3, ["fraud"], "специалист Госуслуг"),
    ("+79990000004", "bank-security-service", 5, ["fraud", "bank"], "служба безопасности банка"),
    ("+79990000005", "marketplace-delivery", 1, ["fraud", "delivery"], "курьер службы доставки"),
    ("+79990000006", "investment-broker", 2, ["fraud", "investment"], "финансовый аналитик Максим"),
    ("+79990000007", "law-enforcement", 3, ["fraud"], "майор ФСБ Олег Кузнецов"),
    ("+79990000008", "sim-card-renewal", 2, ["fraud"], "оператор сотовой связи"),
    ("+79990000009", "prize-compensation", 1, ["fraud"], "юрист фонда компенсаций"),
    ("+79990000010", "job-offer", 1, ["fraud"], "HR-менеджер маркетплейса"),
    ("+79990000011", None, 1, [], None),
    ("+79990000012", "pension-recalculation", 2, ["fraud"], "специалист Социального фонда"),
    ("+79990000013", "intercom-replacement", 3, ["fraud"], "мастер управляющей компании"),
    ("+79990000014", "credit-cancel", 2, ["fraud", "bank"], "кредитный отдел банка"),
    ("+79990000015", "fake-boss", 1, ["fraud"], "директор предприятия"),
    ("+79990000016", "medical-policy", 2, ["fraud"], "сотрудник поликлиники"),
    ("+79990000017", None, 2, ["courier-ok"], None),
    ("+79990000018", "tax-refund", 1, ["fraud"], "инспектор ФНС"),
    ("+79990000019", "power-of-attorney", 2, ["fraud"], "сотрудник Росреестра"),
    ("+79990000020", "tech-support-remote", 1, ["fraud"], "техподдержка банка"),
]

SUMMARIES = {
    None: "Обычный звонок, признаков мошенничества нет.",
}


def seed(db_path: str, keep: bool = False, seed_value: int = 42) -> int:
    path = Path(db_path)
    if not keep and path.exists():
        path.unlink()
    db.init_db(str(path))
    rnd = random.Random(seed_value)
    now = datetime(2026, 10, 20, 12, 0, 0)
    saved = 0
    for i, (phone, scheme_id, count, tags, legend) in enumerate(DEMO_NUMBERS, start=1):
        for k in range(count):
            started = now - timedelta(days=rnd.randint(1, 90), minutes=rnd.randint(0, 600))
            ended = started + timedelta(seconds=rnd.randint(60, 900))
            db.save_case(CallCase(
                call_id=f"demo-{i:02d}-{k + 1}",
                phone=phone,
                started_at=started,
                ended_at=ended,
                scheme_id=scheme_id,
                max_stage=rnd.randint(1, 4) if scheme_id else None,
                legend=legend,
                key_phrases=[] if scheme_id is None else ["срочно", "никому не говорите"],
                summary=SUMMARIES.get(scheme_id, f"Звонок по схеме {scheme_id}; собеседник положил трубку."),
                transcript_path=f"data/runs/demo-{i:02d}-{k + 1}.jsonl",
            ))
            saved += 1
        for tag in tags:
            db.tag_number(phone, tag)
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=db.DEFAULT_DB_PATH, help="путь к SQLite-файлу")
    parser.add_argument("--keep", action="store_true", help="не удалять существующую базу")
    args = parser.parse_args()
    n = seed(args.db, keep=args.keep)
    print(f"Демо-БД {args.db}: {len(DEMO_NUMBERS)} номеров, {n} звонков")


if __name__ == "__main__":
    main()
