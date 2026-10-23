"""Scheme stages as retrieval chunks: one chunk = one stage of one scheme.

A stage is the unit the agent has to recognize, so it is also the unit of
search. The chunk text is what both the vector index and BM25 see, the
metadata is what the agent gets back to know which scheme and stage matched.
"""

import re
from pathlib import Path

from pydantic import BaseModel

from contracts import Scheme, Stage


class Chunk(BaseModel):
    id: str  # "<scheme_id>#<stage_n>"
    scheme_id: str
    stage_n: int
    category: str
    text: str

    def metadata(self) -> dict[str, str | int]:
        return {"scheme_id": self.scheme_id, "stage_n": self.stage_n, "category": self.category}


def load_schemes(schemes_dir: str | Path) -> dict[str, Scheme]:
    schemes: dict[str, Scheme] = {}
    for path in sorted(Path(schemes_dir).glob("*.json")):
        scheme = Scheme.model_validate_json(path.read_text(encoding="utf-8"))
        if scheme.id != path.stem:
            raise ValueError(f"{path}: id {scheme.id!r} does not match file name")
        schemes[scheme.id] = scheme
    return schemes


def stage_text(scheme: Scheme, stage: Stage) -> str:
    # Typical phrases come first: callers' words are closest to them, and they
    # matter most for embeddings that truncate long inputs.
    parts = [
        *stage.typical_phrases,
        f"{scheme.name}. {scheme.legend}. Этап {stage.n}: {stage.name}. {stage.goal}.",
        "Признаки: " + "; ".join(stage.red_flags) + ".",
        "Просят: " + "; ".join(stage.asks_for) + ".",
    ]
    return "\n".join(parts)


def scheme_chunks(schemes: dict[str, Scheme]) -> list[Chunk]:
    return [
        Chunk(
            id=f"{scheme.id}#{stage.n}",
            scheme_id=scheme.id,
            stage_n=stage.n,
            category=scheme.category.value,
            text=stage_text(scheme, stage),
        )
        for scheme in schemes.values()
        for stage in scheme.stages
    ]


_WORD = re.compile(r"\w+")


def tokenize(text: str, stem_len: int = 5) -> list[str]:
    """Lowercase words cut to `stem_len` characters.

    Russian is highly inflected ("код", "кода", "коду"), and BM25 matches
    exact tokens. Cutting words to a prefix is a crude but dependency-free
    stemmer; whether it helps is measured in eval, not assumed.
    """
    words = _WORD.findall(text.lower().replace("ё", "е"))
    if stem_len:
        words = [w[:stem_len] for w in words]
    return words
