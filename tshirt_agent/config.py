"""Settings read from environment variables (and .env), with validated defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "atliq_tshirts.db"

PROVIDERS = {
    "google": {"model": "gemini-3.5-flash", "key": "GOOGLE_API_KEY"},
    "openai": {"model": "gpt-5.5", "key": "OPENAI_API_KEY"},
}


class ConfigError(ValueError):
    """A setting is missing or invalid."""


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}.") from error
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}, got {value}.")
    return value


def _optional_float(name: str, minimum: float, maximum: float) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be a number, got {raw!r}.") from error
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}, got {value}.")
    return value


@dataclass(frozen=True)
class Settings:
    """Every tunable value in one place. See .env.example for the variables."""

    database_url: str = f"sqlite:///{DEFAULT_SQLITE_PATH}"
    llm_provider: str = "google"
    llm_model: str = PROVIDERS["google"]["model"]
    temperature: float | None = None
    max_answer_tokens: int = 2000
    request_timeout: int = 60
    max_retries: int = 2
    max_result_rows: int = 50
    query_timeout_seconds: int = 10
    max_tool_rounds: int = 6
    max_question_chars: int = 500
    log_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "logs")

    def __post_init__(self) -> None:
        if self.llm_provider not in PROVIDERS:
            raise ConfigError(f"LLM_PROVIDER must be one of {', '.join(PROVIDERS)}, got {self.llm_provider!r}.")

    @property
    def api_key_env(self) -> str:
        return PROVIDERS[self.llm_provider]["key"]


def load_settings() -> Settings:
    provider = (os.getenv("LLM_PROVIDER") or "google").strip().lower()
    if provider not in PROVIDERS:
        raise ConfigError(f"LLM_PROVIDER must be one of {', '.join(PROVIDERS)}, got {provider!r}.")
    log_dir = Path(os.getenv("LOG_DIR") or "logs")
    return Settings(
        database_url=os.getenv("DATABASE_URL") or Settings.database_url,
        llm_provider=provider,
        llm_model=os.getenv("LLM_MODEL") or PROVIDERS[provider]["model"],
        temperature=_optional_float("LLM_TEMPERATURE", 0.0, 2.0),
        max_answer_tokens=_int("LLM_MAX_TOKENS", Settings.max_answer_tokens, 100, 32000),
        request_timeout=_int("LLM_TIMEOUT_SECONDS", Settings.request_timeout, 5, 600),
        max_retries=_int("LLM_MAX_RETRIES", Settings.max_retries, 0, 10),
        max_result_rows=_int("MAX_RESULT_ROWS", Settings.max_result_rows, 1, 1000),
        query_timeout_seconds=_int("QUERY_TIMEOUT_SECONDS", Settings.query_timeout_seconds, 1, 120),
        max_tool_rounds=_int("AGENT_MAX_TOOL_ROUNDS", Settings.max_tool_rounds, 1, 12),
        log_dir=log_dir if log_dir.is_absolute() else PROJECT_ROOT / log_dir,
    )


def require_api_key(settings: Settings) -> None:
    """Fail early, with the fix in the message, when the provider's key is missing."""
    name = settings.api_key_env
    key = os.getenv(name, "").strip()
    if not key or key.startswith("your_"):
        raise ConfigError(
            f"{name} is not set. Copy .env.example to .env and add your key (LLM_PROVIDER={settings.llm_provider})."
        )
