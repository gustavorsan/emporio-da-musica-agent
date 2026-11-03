"""Thin wrapper around the OpenAI SDK. The agent depends only on this interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import openai

from emporio import config


class LLMError(RuntimeError):
    """The provider could not produce a reply after the SDK's own retries."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LLMReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(Protocol):
    model: str

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMReply: ...


class OpenAIClient:
    def __init__(self, settings: config.LLMSettings) -> None:
        self.model = settings.model
        self._client = openai.OpenAI(
            api_key=settings.api_key,
            timeout=config.LLM_TIMEOUT_SECONDS,
            max_retries=config.LLM_MAX_RETRIES,
        )

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        # temperature is left unset because some models accept only the default value.
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                max_completion_tokens=config.MAX_COMPLETION_TOKENS,
            )
        except openai.OpenAIError as exc:
            raise LLMError(type(exc).__name__) from exc
        if not response.choices:
            raise LLMError("empty response")
        message = response.choices[0].message
        calls = [
            ToolCall(call.id, call.function.name, call.function.arguments)
            for call in message.tool_calls or []
            if call.type == "function"
        ]
        return LLMReply(message.content, calls)
