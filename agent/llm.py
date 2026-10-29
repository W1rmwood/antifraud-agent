"""Вызовы LLM со структурированным ответом: результат — всегда проверенная Pydantic-модель.

Параметр `format` в Ollama ограничивает генерацию JSON-схемой, но маленькая
модель всё равно может выдать JSON, который не проходит проверку (число вне
диапазона, пустая обязательная строка). Тогда ошибка возвращается модели,
и у неё есть ещё попытка.
"""

import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, Protocol

import httpx  # транспорт клиента ollama; его таймауты ollama не оборачивает в свои ошибки
import ollama
from pydantic import BaseModel, ValidationError

from agent.config import get_settings

log = logging.getLogger(__name__)

Messages = list[dict[str, str]]
# Функция, которая отправляет сообщения вместе с JSON-схемой и возвращает сырой текст ответа.
# Вынесена отдельно, чтобы логику повторов можно было проверить без Ollama.
RawChat = Callable[[Messages, dict[str, Any]], Awaitable[str]]


class LLMError(Exception):
    """Модель недоступна или не дала валидного ответа за все попытки."""


class LLM(Protocol):
    """Всё, что по сообщениям возвращает ответ в виде заданной Pydantic-модели.

    `temperature` — разброс ответов на этот вызов; None — значение по умолчанию.
    """

    async def generate[T: BaseModel](
        self, messages: Messages, schema: type[T], temperature: float | None = None
    ) -> T: ...


async def generate_structured[T: BaseModel](
    chat: RawChat, messages: Messages, schema: type[T], retries: int
) -> T:
    """Спрашивает модель, пока ответ не пройдёт проверку схемой, но не больше `retries + 1` раз.

    При неудаче в историю добавляются невалидный ответ модели и текст ошибки:
    модель видит, что именно не так, и исправляет, а не генерирует наугад заново.
    """
    history = list(messages)  # копия: не портим список, который передал вызывающий код
    json_schema = schema.model_json_schema()
    for attempt in range(retries + 1):
        raw = await chat(history, json_schema)
        try:
            return schema.model_validate_json(raw)
        except ValidationError as error:
            log.warning("invalid LLM answer (attempt %d): %s", attempt + 1, error)
            history += [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": f"Ответ не прошёл проверку: {error}. Ответь заново строго по JSON-схеме.",
                },
            ]
    raise LLMError(f"no valid {schema.__name__} after {retries + 1} attempts")


class OllamaLLM:
    """Настоящая LLM через асинхронный клиент Ollama: не блокирует event loop во время генерации.

    `keep_alive` держит модель в памяти между вызовами. `max_tokens` — потолок длины
    ответа по имени схемы; схема без записи (и значение `None`) берёт потолок разбора.
    Не заданные явно `keep_alive` и `max_tokens` берутся из настроек.
    """

    def __init__(
        self,
        model: str,
        host: str,
        *,
        temperature: float = 0.0,
        retries: int = 2,
        timeout_s: float = 120.0,
        keep_alive: str | None = None,
        max_tokens: dict[str, int] | None = None,
    ) -> None:
        settings = get_settings()
        self.model = model
        self._client = ollama.AsyncClient(host=host, timeout=timeout_s)
        self._temperature = temperature
        self._retries = retries
        self._keep_alive = settings.llm_keep_alive if keep_alive is None else keep_alive
        self._max_tokens = max_tokens or {
            "Analysis": settings.llm_num_predict_analyze,
            "Reply": settings.llm_num_predict_reply,
            "CallSummary": settings.llm_num_predict_summary,
        }
        self._default_max_tokens = self._max_tokens.get("Analysis", settings.llm_num_predict_analyze)

    async def generate[T: BaseModel](
        self, messages: Messages, schema: type[T], temperature: float | None = None
    ) -> T:
        """Ответ модели в виде `schema`, с повтором при невалидном JSON."""
        options = {
            "temperature": self._temperature if temperature is None else temperature,
            "num_predict": self._max_tokens.get(schema.__name__, self._default_max_tokens),
        }
        return await generate_structured(partial(self._chat, options=options), messages, schema, self._retries)

    async def _chat(self, messages: Messages, json_schema: dict[str, Any], options: dict[str, float | int]) -> str:
        """Один запрос к Ollama. Сетевые ошибки превращаются в LLMError, чтобы агент обрабатывал один тип."""
        try:
            response = await self._client.chat(
                model=self.model,
                messages=messages,
                format=json_schema,
                # Режим рассуждений qwen3 добавляет секунды задержки на каждую реплику,
                # а подсказка нужна, пока мошенник ещё на линии.
                think=False,
                options=options,
                keep_alive=self._keep_alive,
            )
        except (ollama.ResponseError, ConnectionError, httpx.TimeoutException) as error:
            raise LLMError(f"Ollama call failed: {error}") from error
        return response.message.content or ""
