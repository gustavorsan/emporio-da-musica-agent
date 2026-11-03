"""Project paths, runtime limits and environment-based settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# The package is installed in editable mode, so the repository root is reachable from here.
ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"
VAR_DIR = ROOT_DIR / "var"
DB_PATH = VAR_DIR / "emporio.db"
LOG_PATH = VAR_DIR / "logs" / "tools.jsonl"

# Campo Grande/MS runs one hour behind Sao Paulo, which matters at the edges of store hours.
STORE_TIMEZONE = ZoneInfo("America/Campo_Grande")

MAX_TOOL_ROUNDS = 6
MAX_TOOL_RESULT_CHARS = 8_000
MAX_HISTORY_TURNS = 12
# Reasoning models count their reasoning against this limit, so it is kept generous.
MAX_COMPLETION_TOKENS = 4_000
LLM_TIMEOUT_SECONDS = 60.0
LLM_MAX_RETRIES = 2


class ConfigError(RuntimeError):
    """Raised when required settings are missing."""


@dataclass(frozen=True)
class LLMSettings:
    api_key: str
    model: str


def load_llm_settings() -> LLMSettings:
    """Read the OpenAI settings from the environment or the local .env file."""
    load_dotenv(ROOT_DIR / ".env")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", "").strip()
    missing = [
        name
        for name, value in (("OPENAI_API_KEY", api_key), ("OPENAI_MODEL", model))
        if not value
    ]
    if missing:
        raise ConfigError(
            f"Configuração ausente: {', '.join(missing)}. "
            "Copie .env.example para .env e preencha os valores."
        )
    return LLMSettings(api_key=api_key, model=model)
