"""Сборка датасета реальных разговоров с мошенниками из расшифровок YouTube-видео.

    python data/calls/real/build.py --src ~/Downloads/датасет_реплики.jsonl

Вход:
  --src          исходные реплики (WhisperX + pyannote): video_id, video_index, turn, speaker, start, end, text
  labels.tsv     роль каждого предложения: <id>\\t<код>, коды M J N O X ? (см. README.md)
  episodes.json  звонки внутри видео: границы, схема (id из data/schemes/), этапы с первым предложением

Выход (рядом с этим файлом):
  dialogs.jsonl      по строке на предложение: роль, speaker по контракту, звонок, схема, этап
  calls.jsonl        по строке на звонок: схема, легенда мошенника, этапы, статистика
  transcripts/*.txt  транскрипт звонка «caller: …» / «user: …» — его читает listen() (kind="file")

Метки диаризации исходника сохранены в поле diar_speaker, но для ролей не используются:
они перепутаны, а в одной реплике бывают слова обоих собеседников.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from contracts.models import Scheme, Speaker, Utterance  # noqa: E402

ROLES = {
    "M": "scammer",   # мошенник и все на его стороне
    "J": "target",    # жертва / пранкер, играющий жертву
    "N": "narrator",  # закадровый текст для зрителей
    "O": "other",     # третьи лица, автоответчик, фон
    "X": "mixed",     # в предложении слова обеих сторон
    "?": "unclear",
}
SPEAKER = {"scammer": Speaker.caller, "target": Speaker.user}

# Конец предложения: . ! ? … и пробел перед заглавной буквой, цифрой или кавычкой.
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[«\"A-ZА-ЯЁ0-9])")
# Длинные последовательности цифр (номера карт, телефонов, счетов) в репозиторий не кладём.
LONG_NUMBER = re.compile(r"(?:\d[\s-]?){7,}\d")


def split_sentences(turns: list[dict]) -> list[dict]:
    """Режет реплики на предложения; время делится пропорционально длине текста."""
    segments = []
    for r in turns:
        parts = [p.strip() for p in SENTENCE_END.split(r["text"]) if p.strip()] or [r["text"]]
        total = sum(len(p) for p in parts) or 1
        t, dur = r["start"], r["end"] - r["start"]
        for s, text in enumerate(parts):
            share = dur * len(text) / total
            segments.append({
                "id": f"v{r['video_index']:02d}-t{r['turn']:03d}-s{s}",
                "video_index": r["video_index"], "video_id": r["video_id"],
                "turn": r["turn"], "seg": s, "diar_speaker": r["speaker"],
                "start": round(t, 2), "end": round(t + share, 2),
                "text": LONG_NUMBER.sub("[номер]", text),
            })
            t += share
    return segments


def load_labels(path: Path) -> dict[str, str]:
    labels = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            seg_id, code = line.split("\t")
            labels[seg_id] = ROLES[code]
    return labels


def load_schemes() -> dict[str, Scheme]:
    schemes = {}
    for p in sorted((ROOT / "data" / "schemes").glob("*.json")):
        s = Scheme.model_validate_json(p.read_text(encoding="utf-8"))
        schemes[s.id] = s
    return schemes


def merge_turns(segs: list[dict], call_id: str) -> list[Utterance]:
    """Склеивает подряд идущие предложения одной стороны в реплики по контракту Utterance.

    Предложения narrator/other/mixed/unclear в разговор не входят.
    """
    utterances: list[Utterance] = []
    cur: dict | None = None
    for s in segs:
        speaker = SPEAKER.get(s["role"])
        if speaker is None:
            continue
        if cur and cur["speaker"] == speaker:
            cur["text"] += " " + s["text"]
            cur["t_end"] = max(cur["t_end"], s["end"])
            continue
        if cur:
            utterances.append(Utterance(call_id=call_id, seq=len(utterances), **cur))
        cur = {"speaker": speaker, "text": s["text"], "t_start": s["start"], "t_end": s["end"]}
    if cur:
        utterances.append(Utterance(call_id=call_id, seq=len(utterances), **cur))
    return utterances


def build(src: Path) -> None:
    turns = [json.loads(line) for line in src.open(encoding="utf-8")]
    videos = {}
    for v in json.loads((HERE / "videos.json").read_text(encoding="utf-8")):
        videos[v["video_index"]] = v
    segments = split_sentences(turns)
    labels = load_labels(HERE / "labels.tsv")
    missing = [s["id"] for s in segments if s["id"] not in labels]
    extra = set(labels) - {s["id"] for s in segments}
    if missing or extra:
        raise SystemExit(f"labels.tsv не совпадает с исходником: нет {len(missing)} id, лишних {len(extra)}")

    schemes = load_schemes()
    episodes = json.loads((HERE / "episodes.json").read_text(encoding="utf-8"))
    order = {s["id"]: i for i, s in enumerate(segments)}

    for s in segments:
        s["role"] = labels[s["id"]]
        s["speaker"] = SPEAKER.get(s["role"], Speaker.unknown).value
        s["call_id"] = s["scheme_id"] = s["stage_n"] = None

    calls = []
    for ep in episodes:
        scheme = schemes.get(ep["scheme_id"])
        if scheme is None:
            raise SystemExit(f"{ep['call_id']}: схемы {ep['scheme_id']!r} нет в data/schemes/")
        lo, hi = order[ep["first_id"]], order[ep["last_id"]]
        # Этапы перечислены в порядке разговора. Номер этапа может вернуться назад:
        # в реальных звонках «деньги не дошли» и «доплатите залог» идут по кругу.
        starts = [(order[st["first_id"]], st["n"]) for st in ep["stages"]]
        positions = [pos for pos, _ in starts]
        if positions[0] != lo or positions != sorted(set(positions)) or positions[-1] > hi:
            raise SystemExit(f"{ep['call_id']}: этапы должны начинаться с first_id и идти по порядку разговора")
        if any(not 1 <= n <= len(scheme.stages) for _, n in starts):
            raise SystemExit(f"{ep['call_id']}: этап вне диапазона схемы {scheme.id}")
        for i in range(lo, hi + 1):
            s = segments[i]
            s["call_id"], s["scheme_id"] = ep["call_id"], scheme.id
            if s["role"] == "scammer":
                s["stage_n"] = [n for pos, n in starts if pos <= i][-1]
        call_segs = segments[lo:hi + 1]
        utterances = merge_turns(call_segs, ep["call_id"])
        roles = Counter(s["role"] for s in call_segs)
        video = videos[ep["video_index"]]
        calls.append({
            "call_id": ep["call_id"], "video_index": ep["video_index"], "video_id": video["video_id"],
            "title": video["title"], "url": video["url"],
            "first_id": ep["first_id"], "last_id": ep["last_id"],
            "t_start": call_segs[0]["start"], "t_end": call_segs[-1]["end"],
            "scheme_id": scheme.id, "scheme_name": scheme.name,
            "legend": ep["legend"], "personas": ep["personas"],
            "stages": [{"n": n, "name": scheme.stages[n - 1].name, "first_id": segments[pos]["id"]}
                       for pos, n in starts],
            "max_stage": max(n for _, n in starts),
            "asks_for": ep["asks_for"], "pressure": ep["pressure"],
            "roles": dict(roles), "utterances": len(utterances),
            "transcript": f"transcripts/{ep['call_id']}.txt",
        })
        lines = [f"{u.speaker.value}: {u.text}" for u in utterances]
        (HERE / "transcripts").mkdir(exist_ok=True)
        (HERE / "transcripts" / f"{ep['call_id']}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    fields = ["id", "video_index", "video_id", "turn", "seg", "start", "end", "role", "speaker",
              "diar_speaker", "call_id", "scheme_id", "stage_n", "text"]
    with (HERE / "dialogs.jsonl").open("w", encoding="utf-8") as f:
        for s in segments:
            f.write(json.dumps({k: s[k] for k in fields}, ensure_ascii=False) + "\n")
    with (HERE / "calls.jsonl").open("w", encoding="utf-8") as f:
        for c in calls:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    roles = Counter(s["role"] for s in segments)
    print(f"предложений: {len(segments)} из {len(turns)} реплик; роли: {dict(roles)}")
    print(f"звонков: {len(calls)}; схемы: {dict(Counter(c['scheme_id'] for c in calls))}")
    in_calls = sum(1 for s in segments if s["call_id"])
    print(f"предложений внутри звонков: {in_calls}, фраз мошенника с этапом: "
          f"{sum(1 for s in segments if s['stage_n'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", type=Path, required=True, help="датасет_реплики.jsonl")
    build(parser.parse_args().src.expanduser())


if __name__ == "__main__":
    main()
