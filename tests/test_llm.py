"""OpenAI wrapper: how provider responses and errors reach the agent, without any network."""

from __future__ import annotations

from types import SimpleNamespace

import openai
import pytest

from emporio.config import LLMSettings
from emporio.llm import LLMError, OpenAIClient


def _client(monkeypatch, outcome) -> OpenAIClient:
    client = OpenAIClient(LLMSettings(api_key="not-a-real-key", model="any"))

    def create(**_):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(client._client.chat.completions, "create", create)
    return client


def _response(content, finish_reason):
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


def test_complete_reply_is_returned(monkeypatch):
    reply = _client(monkeypatch, _response("Olá!", "stop")).complete([], [])
    assert reply.content == "Olá!"


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
def test_cut_off_reply_is_a_failure(monkeypatch, finish_reason):
    client = _client(monkeypatch, _response("O preço do Kalani é R$", finish_reason))
    with pytest.raises(LLMError, match=finish_reason):
        client.complete([], [])


def test_provider_error_exposes_only_its_type(monkeypatch):
    class InvalidKey(openai.OpenAIError):
        pass

    with pytest.raises(LLMError) as caught:
        _client(monkeypatch, InvalidKey("Incorrect API key: sk-abc123")).complete([], [])
    assert str(caught.value) == "InvalidKey"
