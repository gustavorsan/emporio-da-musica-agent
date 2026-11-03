"""Command line entry point."""

from __future__ import annotations

import argparse
import sys

from emporio import config
from emporio.loader import LoadError, build_database


def _prepare(_: argparse.Namespace) -> int:
    try:
        counts = build_database(config.DATA_DIR, config.KNOWLEDGE_DIR, config.DB_PATH)
    except LoadError as exc:
        print(f"Falha na carga; nenhum banco foi alterado.\n{exc}", file=sys.stderr)
        return 1
    summary = ", ".join(f"{table}={count}" for table, count in counts.items())
    print(f"Banco gerado em {config.DB_PATH}\n{summary}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="emporio", description="Agente Empório da Música")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="gera o banco a partir das fontes")
    prepare.set_defaults(handler=_prepare)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles may default to a legacy code page that cannot print Portuguese text.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.handler(args)
