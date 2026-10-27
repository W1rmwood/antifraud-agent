"""Разметка реальных разговоров (data/calls/real/): labels.tsv и episodes.json сходятся друг с другом и со схемами.

Тексты (dialogs.jsonl, transcripts/) в git нет — их проверки пропускаются, если сборка не запускалась.
"""
import json
from pathlib import Path

import pytest

from audio.stream import parse_transcript
from contracts.models import Scheme

REAL = Path("data/calls/real")
CODES = set("MJNOX?")


@pytest.fixture(scope="module")
def labels():
    rows = [line.split("\t") for line in (REAL / "labels.tsv").read_text(encoding="utf-8").splitlines() if line]
    return rows


@pytest.fixture(scope="module")
def schemes():
    return {p.stem: Scheme.model_validate_json(p.read_text(encoding="utf-8"))
            for p in Path("data/schemes").glob("*.json")}


def test_labels_codes_and_ids(labels):
    assert len(labels) > 10_000
    assert all(len(r) == 2 and r[1] in CODES for r in labels)
    ids = [r[0] for r in labels]
    assert len(set(ids)) == len(ids)
    videos = {int(i[1:3]) for i in ids}
    assert videos == set(range(1, 22))


def test_episodes_match_labels_and_schemes(labels, schemes):
    order = {r[0]: i for i, r in enumerate(labels)}
    episodes = json.loads((REAL / "episodes.json").read_text(encoding="utf-8"))
    assert len(episodes) == 21
    assert {"escort-prepayment", "salon-booking-extortion"} <= {e["scheme_id"] for e in episodes}
    for e in episodes:
        scheme = schemes[e["scheme_id"]]
        lo, hi = order[e["first_id"]], order[e["last_id"]]
        assert lo < hi
        positions = [order[s["first_id"]] for s in e["stages"]]
        assert positions[0] == lo and positions == sorted(set(positions)) and positions[-1] <= hi, e["call_id"]
        assert all(1 <= s["n"] <= len(scheme.stages) for s in e["stages"]), e["call_id"]
        scammer = sum(1 for r in labels[lo:hi + 1] if r[1] == "M")
        assert scammer >= 50, e["call_id"]


def test_new_scheme_phrases_only_from_dev_videos():
    """typical_phrases новых схем взяты из dev-видео: ссылки на отложенные видео в sources не попадают."""
    videos = {v["video_index"]: v["url"] for v in json.loads((REAL / "videos.json").read_text(encoding="utf-8"))}
    held_out = {9, 13, 14, 15, 18, 19, 17, 20, 21}
    for sid in ("escort-prepayment", "salon-booking-extortion"):
        sources = json.loads(Path(f"data/schemes/{sid}.json").read_text(encoding="utf-8"))["sources"]
        assert not {videos[v] for v in held_out} & set(sources), sid


@pytest.mark.skipif(not (REAL / "dialogs.jsonl").exists(), reason="датасет не собран (build.py)")
def test_built_dataset():
    rows = [json.loads(line) for line in (REAL / "dialogs.jsonl").open(encoding="utf-8")]
    assert all(r["speaker"] == {"scammer": "caller", "target": "user"}.get(r["role"], "unknown") for r in rows)
    assert all((r["stage_n"] is not None) == (r["role"] == "scammer" and r["call_id"] is not None) for r in rows)
    calls = [json.loads(line) for line in (REAL / "calls.jsonl").open(encoding="utf-8")]
    for c in calls:
        lines = parse_transcript(REAL / c["transcript"])
        assert len(lines) == c["utterances"] and lines, c["call_id"]
