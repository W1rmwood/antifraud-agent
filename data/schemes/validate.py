"""Проверка базы схем развода (контракт №1).

    python data/schemes/validate.py            # все data/schemes/*.json
    python data/schemes/validate.py --strict   # предупреждения тоже считаются ошибками

Ошибки (код возврата 1): JSON не читается, не проходит Scheme.model_validate, id повторяется
или не совпадает с именем файла.
Предупреждения — отступления от правил ТЗ: 3–7 этапов, номера этапов 1..N, 5–10 фраз на этап,
известная категория, ссылки в sources, одинаковые фразы в разных схемах.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

SCHEMES_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCHEMES_DIR.parent.parent))

from pydantic import ValidationError  # noqa: E402

from contracts.models import Scheme  # noqa: E402

CATEGORIES = {"bank", "gosuslugi", "relative", "investment", "delivery", "other"}
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _norm(phrase: str) -> str:
    return re.sub(r"[^\w ]", "", phrase.lower().replace("ё", "е")).strip()


def check_scheme(scheme: Scheme) -> list[str]:
    warnings = []
    if not SLUG.match(scheme.id):
        warnings.append(f"id {scheme.id!r} — не slug латиницей")
    if scheme.category not in CATEGORIES:
        warnings.append(f"неизвестная категория {scheme.category!r}")
    if not 3 <= len(scheme.stages) <= 7:
        warnings.append(f"{len(scheme.stages)} этапов, нужно 3–7")
    if [s.n for s in scheme.stages] != list(range(1, len(scheme.stages) + 1)):
        warnings.append("номера этапов не идут 1, 2, 3, …")
    for s in scheme.stages:
        if not 5 <= len(s.typical_phrases) <= 10:
            warnings.append(f"этап {s.n}: {len(s.typical_phrases)} фраз, нужно 5–10")
        if not s.red_flags:
            warnings.append(f"этап {s.n}: пустые red_flags")
        if not s.next_move.strip():
            warnings.append(f"этап {s.n}: пустой next_move")
    sentences = [x for x in re.split(r"[.!?]+", scheme.description) if x.strip()]
    if not 3 <= len(sentences) <= 6:
        warnings.append(f"description: {len(sentences)} предложений, нужно 3–5")
    if not scheme.stall_tactics:
        warnings.append("пустые stall_tactics")
    if not scheme.sources:
        warnings.append("нет источников")
    for src in scheme.sources:
        if not src.startswith(("http://", "https://")):
            warnings.append(f"источник не ссылка: {src!r}")
    return warnings


def validate(directory: Path = SCHEMES_DIR) -> tuple[list[Scheme], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    schemes: list[Scheme] = []
    ids: dict[str, Path] = {}
    phrases: dict[str, set[str]] = defaultdict(set)

    files = sorted(directory.glob("*.json"))
    if not files:
        errors.append(f"в {directory} нет ни одной схемы")
    for path in files:
        try:
            scheme = Scheme.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            errors.append(f"{path.name}: невалидный JSON: {e}")
            continue
        except ValidationError as e:
            errors.append(f"{path.name}: не проходит Scheme:\n{e}")
            continue
        if scheme.id in ids:
            errors.append(f"{path.name}: id {scheme.id!r} уже есть в {ids[scheme.id].name}")
        ids[scheme.id] = path
        if path.stem != scheme.id:
            errors.append(f"{path.name}: id {scheme.id!r} не совпадает с именем файла")
        warnings += [f"{path.name}: {w}" for w in check_scheme(scheme)]
        for stage in scheme.stages:
            for p in stage.typical_phrases:
                phrases[_norm(p)].add(scheme.id)
        schemes.append(scheme)

    for phrase, owners in phrases.items():
        if len(owners) > 1:
            warnings.append(f"фраза в нескольких схемах {sorted(owners)}: {phrase!r}")
    return schemes, errors, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path, default=SCHEMES_DIR)
    parser.add_argument("--strict", action="store_true", help="предупреждения → ошибки")
    args = parser.parse_args()

    schemes, errors, warnings = validate(args.dir)
    for w in warnings:
        print(f"ПРЕДУПРЕЖДЕНИЕ {w}")
    for e in errors:
        print(f"ОШИБКА {e}")
    stages = sum(len(s.stages) for s in schemes)
    phrases = sum(len(st.typical_phrases) for s in schemes for st in s.stages)
    by_cat: dict[str, int] = defaultdict(int)
    for s in schemes:
        by_cat[s.category] += 1
    print(f"\nсхем: {len(schemes)}, этапов: {stages}, фраз: {phrases}; "
          + ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items())))
    print(f"ошибок: {len(errors)}, предупреждений: {len(warnings)}")
    sys.exit(1 if errors or (args.strict and warnings) else 0)


if __name__ == "__main__":
    main()
