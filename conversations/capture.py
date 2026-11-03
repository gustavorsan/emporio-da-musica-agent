"""Capture the example conversations by running the real CLI with scripted customer messages.

Usage: python conversations/capture.py [name ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

from emporio.cli import main as cli_main

BASE_DIR = Path(__file__).resolve().parent

# Demo customer of each session; None runs without identification.
SESSIONS = {
    "01_catalogo_e_pagamento": None,
    "02_promocao_e_regras": None,
    "03_pedido_e_acesso": 6,
    "04_devolucao": None,
    "05_loja_e_escopo": None,
}


def main(names: list[str]) -> int:
    for name in names or SESSIONS:
        arguments = [
            "chat",
            "--script", str(BASE_DIR / "inputs" / f"{name}.txt"),
            "--transcript", str(BASE_DIR / f"{name}.md"),
        ]
        if SESSIONS[name] is not None:
            arguments += ["--customer-id", str(SESSIONS[name])]
        status = cli_main(arguments)
        if status:
            return status
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
