"""Этапы схем как чанки для поиска: один чанк = один этап одной схемы.

Агент должен узнать именно этап, поэтому этап — это и единица поиска.
Текст чанка видят и векторный индекс, и BM25. Метаданные агент получает
обратно, чтобы понять, какая схема и какой этап совпали.
"""

import re
from pathlib import Path

from pydantic import BaseModel

from contracts import Scheme, Stage


class Chunk(BaseModel):
    """Кусок текста для поиска плюс то, к какой схеме и какому этапу он относится."""

    id: str  # "<scheme_id>#<stage_n>", например "safe-account#2"
    scheme_id: str
    stage_n: int
    category: str
    text: str

    def metadata(self) -> dict[str, str | int]:
        """Метаданные для Chroma: там допустимы только плоские значения str/int/float/bool."""
        return {"scheme_id": self.scheme_id, "stage_n": self.stage_n, "category": self.category}


def load_schemes(schemes_dir: str | Path) -> dict[str, Scheme]:
    """Читает все data/schemes/*.json и проверяет их по контракту Scheme.

    Возвращает словарь id → схема. Падает, если id внутри файла не совпадает
    с именем файла: иначе get_scheme("x") искал бы схему не там.
    """
    schemes: dict[str, Scheme] = {}
    for path in sorted(Path(schemes_dir).glob("*.json")):
        scheme = Scheme.model_validate_json(path.read_text(encoding="utf-8"))
        if scheme.id != path.stem:
            raise ValueError(f"{path}: id {scheme.id!r} does not match file name")
        schemes[scheme.id] = scheme
    return schemes


def stage_text(scheme: Scheme, stage: Stage) -> str:
    """Собирает текст чанка из этапа и общих сведений о схеме."""
    # Типичные фразы идут первыми: слова звонящего ближе всего именно к ним.
    # К тому же модели эмбеддингов обрезают длинный текст, и начало важнее конца.
    parts = [
        *stage.typical_phrases,
        f"{scheme.name}. {scheme.legend}. Этап {stage.n}: {stage.name}. {stage.goal}.",
        "Признаки: " + "; ".join(stage.red_flags) + ".",
        "Просят: " + "; ".join(stage.asks_for) + ".",
    ]
    return "\n".join(parts)


def scheme_chunks(schemes: dict[str, Scheme]) -> list[Chunk]:
    """Превращает все схемы в плоский список чанков, по одному на этап."""
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


# \w в Python 3 понимает кириллицу, отдельный шаблон для русских букв не нужен.
_WORD = re.compile(r"\w+")


def tokenize(text: str, stem_len: int = 5) -> list[str]:
    """Слова в нижнем регистре, обрезанные до `stem_len` символов.

    В русском много словоформ («код», «кода», «коду»), а BM25 сравнивает
    токены только на точное совпадение. Обрезка слова до начала — грубый,
    но не требующий библиотек стеммер: все три формы станут «код».
    Помогает ли он на самом деле, проверяется в eval, а не принимается на веру.
    """
    # «ё» → «е»: в речи и в схемах её пишут по-разному, а для поиска это одна буква.
    words = _WORD.findall(text.lower().replace("ё", "е"))
    if stem_len:
        words = [w[:stem_len] for w in words]
    return words
