"""Chat command: provider failures must be visible and must not become example conversations."""

from __future__ import annotations

import pytest

from emporio import cli, config
from emporio.agent import PROVIDER_FAILURE_REPLY
from emporio.llm import LLMError, LLMReply


class _ScriptedLLM:
    model = "scripted"

    def __init__(self, replies):
        self._replies = list(replies)

    def complete(self, messages, tools):
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def run_chat(db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "tools.jsonl")
    monkeypatch.setattr(config, "load_llm_settings", lambda: config.LLMSettings("key", "scripted"))

    def run(replies, *arguments):
        monkeypatch.setattr(cli, "OpenAIClient", lambda settings: _ScriptedLLM(replies))
        args = cli.build_parser().parse_args(["chat", *map(str, arguments)])
        return args.handler(args)

    return run


@pytest.fixture
def script(tmp_path):
    path = tmp_path / "messages.txt"
    path.write_text("Oi\nTudo bem?\n", "utf-8")
    return path


def test_successful_script_saves_the_transcript(run_chat, script, tmp_path):
    transcript = tmp_path / "saida.md"
    assert run_chat([LLMReply("Olá!"), LLMReply("Tudo!")], "--script", script, "--transcript", transcript) == 0
    text = transcript.read_text("utf-8")
    assert "Execução real" in text
    assert "**Cliente:** Tudo bem?" in text
    assert "**Agente:** Tudo!" in text


def test_provider_failure_stops_a_script_and_warns(run_chat, script, tmp_path, capsys):
    transcript = tmp_path / "saida.md"
    status = run_chat(
        [LLMReply("Olá!"), LLMError("AuthenticationError")], "--script", script, "--transcript", transcript
    )
    captured = capsys.readouterr()
    assert status == 1
    assert not transcript.exists()
    assert "provider:AuthenticationError" in captured.err
    assert "OPENAI_API_KEY" in captured.err
    assert PROVIDER_FAILURE_REPLY in captured.out


def test_failed_run_keeps_the_previous_evidence(run_chat, script, tmp_path):
    transcript = tmp_path / "saida.md"
    transcript.write_text("conversa anterior", "utf-8")
    run_chat([LLMError("APIConnectionError")], "--script", script, "--transcript", transcript)
    assert transcript.read_text("utf-8") == "conversa anterior"


def test_interactive_session_continues_after_a_failure(run_chat, tmp_path, capsys, monkeypatch):
    typed = iter(["Oi", "De novo", "sair"])
    monkeypatch.setattr("builtins.input", lambda _: next(typed))
    transcript = tmp_path / "saida.md"
    status = run_chat([LLMError("APITimeoutError"), LLMReply("Voltei!")], "--transcript", transcript)
    captured = capsys.readouterr()
    assert status == 0
    assert "Voltei!" in captured.out
    assert "provider:APITimeoutError" in captured.err
    assert not transcript.exists()
