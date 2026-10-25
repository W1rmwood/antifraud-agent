"""Все настройки агента, RAG и MCP-сервера в одном месте.

Любое поле можно переопределить переменной окружения `ANTIFRAUD_<ПОЛЕ>`
(например, `ANTIFRAUD_LLM_MODEL=qwen3:4b`) или в файле `.env`.
"""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Режимы поиска, которые сравнивает eval. Подробнее — в rag/retriever.py.
RetrievalMode = Literal["vector", "bm25", "hybrid", "hybrid_rerank"]


class Settings(BaseSettings):
    """Настройки проекта. Значения по умолчанию рассчитаны на запуск из корня репозитория."""

    model_config = SettingsConfigDict(
        # populate_by_name: поле db_path можно задать и по имени, и через ANTIFRAUD_DB.
        env_prefix="ANTIFRAUD_", env_file=".env", extra="ignore", populate_by_name=True
    )

    # Ollama
    ollama_host: str = "http://localhost:11434"
    llm_model: str = "qwen3:8b"
    embed_model: str = "bge-m3"
    # 0 — модель всегда выбирает самый вероятный ответ: результат воспроизводим,
    # и eval можно повторить с теми же цифрами.
    llm_temperature: float = 0.0
    llm_retries: int = Field(default=2, ge=0)  # сколько раз переспросить после невалидного JSON
    llm_timeout_s: float = 120.0

    # Пути
    schemes_dir: str = "data/schemes"
    chroma_dir: str = "data/chroma"
    runs_dir: str = "data/runs"
    # Та же переменная окружения, что читает storage/db.py: одна настройка
    # переключает базу и у агента, и у хранилища.
    db_path: str = Field(
        default="data/antifraud.db", validation_alias=AliasChoices("ANTIFRAUD_DB", "db_path")
    )

    # Поиск
    retrieval_mode: RetrievalMode = "hybrid_rerank"
    top_k: int = Field(default=5, ge=1)  # сколько этапов-кандидатов получает LLM
    candidates_k: int = Field(default=20, ge=1)  # кандидатов от каждого поиска до слияния и реранкера
    rrf_k: int = Field(default=60, ge=1)  # сглаживание в RRF, 60 — стандартное значение из статьи
    bm25_stem_len: int = Field(default=5, ge=0)  # до скольких букв обрезать слова; 0 — не обрезать
    reranker_model: str = "ms-marco-MultiBERT-L-12"  # многоязычная модель, понимает русский
    reranker_cache_dir: str = "models/flashrank"

    # Агент
    window_size: int = Field(default=8, ge=1)  # сколько последних реплик видит LLM
    detect_threshold: float = Field(default=0.6, ge=0.0, le=1.0)  # порог уверенности для тревоги
    # inprocess — MCP-сервер в том же процессе (быстрее, один индекс в памяти);
    # stdio — отдельным процессом, как настоящий внешний MCP-сервер.
    mcp_transport: Literal["inprocess", "stdio"] = "inprocess"


@lru_cache
def get_settings() -> Settings:
    """Настройки читаются один раз за запуск и дальше берутся из кэша."""
    return Settings()
