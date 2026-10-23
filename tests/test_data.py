"""Проверки данных Юры и Руслана: база схем, описание звонков, демо-БД."""
import json
from pathlib import Path

import soundfile as sf

from contracts.models import CallIndexEntry
from audio.stream import parse_transcript

ROOT = Path(__file__).resolve().parent.parent
CALLS = ROOT / "data" / "calls"


def test_schemes_validate():
    from data.schemes.validate import validate  # noqa: PLC0415
    schemes, errors, warnings = validate()
    assert errors == []
    assert warnings == []
    assert len(schemes) >= 30


def _scheme_ids():
    return {p.stem for p in (ROOT / "data" / "schemes").glob("*.json")}


def test_calls_index():
    entries = [CallIndexEntry.model_validate(e)
               for e in json.loads((CALLS / "index.json").read_text(encoding="utf-8"))]
    assert len(entries) >= 10
    assert sum(e.scheme_id is None for e in entries) >= 2
    assert len({e.file for e in entries}) == len(entries)
    ids = _scheme_ids()
    for e in entries:
        assert e.scheme_id is None or e.scheme_id in ids, e
        info = sf.info(str(CALLS / e.file))
        assert info.channels == (2 if e.channels == "stereo" else 1), e.file
        assert parse_transcript(CALLS / e.transcript), e.transcript


def test_seed_schemes_exist():
    from storage.seed_demo import DEMO_NUMBERS  # noqa: PLC0415
    ids = _scheme_ids()
    for phone, scheme_id, *_ in DEMO_NUMBERS:
        assert scheme_id is None or scheme_id in ids, (phone, scheme_id)
