"""Command line entry point."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

from emporio import config
from emporio.agent import Agent, AgentReply
from emporio.db import DatabaseNotReady, connect_readonly, sources_changed
from emporio.llm import OpenAIClient
from emporio.loader import LoadError, build_database
from emporio.orders import Customer, find_customer

_EXIT_WORDS = {"sair", "exit", "quit"}


def _prepare(_: argparse.Namespace) -> int:
    try:
        counts = build_database(config.DATA_DIR, config.KNOWLEDGE_DIR, config.DB_PATH)
    except LoadError as exc:
        print(f"Falha na carga; nenhum banco foi alterado.\n{exc}", file=sys.stderr)
        return 1
    summary = ", ".join(f"{table}={count}" for table, count in counts.items())
    print(f"Banco gerado em {config.DB_PATH}\n{summary}")
    return 0


def _interactive_messages() -> Iterable[str]:
    while True:
        try:
            text = input("\nVocê: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if text.casefold() in _EXIT_WORDS:
            return
        if text:
            yield text


def _script_messages(path: Path) -> Iterable[str]:
    for line in path.read_text("utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            print(f"\nVocê: {text}")
            yield text


def _describe_tool_calls(reply: AgentReply) -> list[str]:
    lines = []
    for trace in reply.tool_traces:
        result = f"erro: {trace.error}" if trace.error else "ok"
        sources = f", seções {', '.join(trace.sources)}" if trace.sources else ""
        lines.append(f"`{trace.name}({trace.arguments})` → {result}{sources}")
    return lines


class _Transcript:
    """Records a real run: the user messages, the tool calls and the model's replies."""

    def __init__(self, path: Path | None, model: str, customer: Customer | None) -> None:
        self._path = path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        session = f"cliente de demonstração nº {customer.customer_id}" if customer else "sem cliente identificado"
        started = datetime.now(config.STORE_TIMEZONE).strftime("%d/%m/%Y %H:%M")
        path.write_text(
            f"# Conversa — {path.stem}\n\n"
            f"Execução real da CLI. Modelo: `{model}`. Sessão: {session}. Início: {started} (horário de Campo Grande/MS).\n",
            "utf-8",
        )

    def add(self, user_text: str, reply: AgentReply) -> None:
        if self._path is None:
            return
        parts = [f"\n**Cliente:** {user_text}\n"]
        calls = _describe_tool_calls(reply)
        if calls:
            parts.append("\n<details><summary>Ferramentas consultadas</summary>\n\n")
            parts.extend(f"- {call}\n" for call in calls)
            parts.append("\n</details>\n")
        parts.append(f"\n**Agente:** {reply.text}\n")
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write("".join(parts))


def _chat(args: argparse.Namespace) -> int:
    try:
        settings = config.load_llm_settings()
        conn = connect_readonly(config.DB_PATH)
    except (config.ConfigError, DatabaseNotReady) as exc:
        print(exc, file=sys.stderr)
        return 2

    try:
        if sources_changed(conn, config.DATA_DIR, config.KNOWLEDGE_DIR):
            print(
                "Aviso: as fontes mudaram desde a última carga. Execute: python -m emporio prepare",
                file=sys.stderr,
            )
        customer = None
        if args.customer_id is not None:
            customer = find_customer(conn, args.customer_id)
            if customer is None:
                print(f"Cliente de demonstração não encontrado: {args.customer_id}", file=sys.stderr)
                return 2

        llm = OpenAIClient(settings)
        agent = Agent(llm, conn, customer, log_path=config.LOG_PATH)
        transcript = _Transcript(args.transcript, settings.model, customer)
        print("Empório da Música — assistente virtual (protótipo). Digite 'sair' para encerrar.")
        messages = _script_messages(args.script) if args.script else _interactive_messages()
        for text in messages:
            reply = agent.respond(text)
            print(f"\nEmpório: {reply.text}")
            if args.show_tools:
                for line in _describe_tool_calls(reply):
                    print(f"  [ferramenta] {line}", file=sys.stderr)
            transcript.add(text, reply)
    except sqlite3.Error as exc:
        print(f"Falha ao acessar o banco: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="emporio", description="Agente Empório da Música")
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="gera o banco e o índice a partir das fontes")
    prepare.set_defaults(handler=_prepare)

    chat = commands.add_parser("chat", help="inicia a conversa")
    chat.add_argument(
        "--customer-id",
        type=int,
        help="cliente de demonstração da sessão; identificação ilustrativa, não é autenticação",
    )
    chat.add_argument("--script", type=Path, help="arquivo com uma mensagem por linha")
    chat.add_argument("--transcript", type=Path, help="grava a conversa em Markdown")
    chat.add_argument("--show-tools", action="store_true", help="mostra as ferramentas usadas")
    chat.set_defaults(handler=_chat)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles may default to a legacy code page that cannot print Portuguese text.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.handler(args)
