"""Structured LLM calls: the answer is always a validated Pydantic model.

Ollama's `format` constrains decoding to the JSON schema, but a small model
can still produce JSON that fails validation (a value out of range, an empty
required string). Then the error goes back to the model and it gets another try.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx  # transport of the ollama client; its timeouts are not wrapped by ollama
import ollama
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

Messages = list[dict[str, str]]
# Sends messages with a JSON schema, returns the raw text of the answer.
RawChat = Callable[[Messages, dict[str, Any]], Awaitable[str]]


class LLMError(Exception):
    """The model is unreachable or gave no valid answer after all retries."""


class LLM(Protocol):
    async def generate[T: BaseModel](self, messages: Messages, schema: type[T]) -> T: ...


async def generate_structured[T: BaseModel](
    chat: RawChat, messages: Messages, schema: type[T], retries: int
) -> T:
    history = list(messages)
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
    def __init__(
        self, model: str, host: str, *, temperature: float = 0.0, retries: int = 2, timeout_s: float = 120.0
    ) -> None:
        self.model = model
        self._client = ollama.AsyncClient(host=host, timeout=timeout_s)
        self._options = {"temperature": temperature}
        self._retries = retries

    async def generate[T: BaseModel](self, messages: Messages, schema: type[T]) -> T:
        return await generate_structured(self._chat, messages, schema, self._retries)

    async def _chat(self, messages: Messages, json_schema: dict[str, Any]) -> str:
        try:
            response = await self._client.chat(
                model=self.model,
                messages=messages,
                format=json_schema,
                think=False,  # qwen3 reasoning adds seconds of latency per utterance
                options=self._options,
            )
        except (ollama.ResponseError, ConnectionError, httpx.TimeoutException) as error:
            raise LLMError(f"Ollama call failed: {error}") from error
        return response.message.content or ""
