"""All tunable settings of the agent, RAG and MCP server in one place.

Every field can be overridden with an environment variable `ANTIFRAUD_<FIELD>`
(for example `ANTIFRAUD_LLM_MODEL=qwen3:4b`) or a `.env` file.
"""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RetrievalMode = Literal["vector", "bm25", "hybrid", "hybrid_rerank"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANTIFRAUD_", env_file=".env", extra="ignore", populate_by_name=True
    )

    # Ollama
    ollama_host: str = "http://localhost:11434"
    llm_model: str = "qwen3:8b"
    embed_model: str = "bge-m3"
    llm_temperature: float = 0.0
    llm_retries: int = Field(default=2, ge=0)  # extra attempts after an invalid JSON answer
    llm_timeout_s: float = 120.0

    # Paths
    schemes_dir: str = "data/schemes"
    chroma_dir: str = "data/chroma"
    runs_dir: str = "data/runs"
    # Same variable as storage/db.py uses, so one setting moves both.
    db_path: str = Field(
        default="data/antifraud.db", validation_alias=AliasChoices("ANTIFRAUD_DB", "db_path")
    )

    # Retrieval
    retrieval_mode: RetrievalMode = "hybrid_rerank"
    top_k: int = Field(default=5, ge=1)
    candidates_k: int = Field(default=20, ge=1)  # per retriever, before fusion and reranking
    rrf_k: int = Field(default=60, ge=1)
    bm25_stem_len: int = Field(default=5, ge=0)  # 0 disables prefix stemming
    reranker_model: str = "ms-marco-MultiBERT-L-12"  # multilingual, handles Russian
    reranker_cache_dir: str = "models/flashrank"

    # Agent
    window_size: int = Field(default=8, ge=1)  # last utterances the LLM sees
    detect_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    mcp_transport: Literal["inprocess", "stdio"] = "inprocess"


@lru_cache
def get_settings() -> Settings:
    return Settings()
