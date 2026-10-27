"""Application settings from environment variables (prefix ``KOMPAS_``) or a ``.env`` file."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Nothing clinical lives here (that is ``knowledge_data``).

    ``database_url``: SQLAlchemy async URL, e.g. from ``kompas-devdb start``.
    ``jwt_secret``: signing key for access tokens (HS256 only, ≥ 32 chars).
    ``invite_secret``: HMAC key for invite codes and stream tickets (≥ 32 chars).
    ``access_token_minutes``: lifetime of an access token.
    ``knowledge_dir`` / ``ohlp_csv``: knowledge files loaded at startup.
    ``outbox_poll_seconds``: how often the worker looks for pending outbox messages.
    ``web_dir``: the built website (``npm run build`` in src/frontend) served at ``/`` (D-114); a
    missing directory just means no website (API only).
    """

    model_config = SettingsConfigDict(env_prefix="KOMPAS_", env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: SecretStr
    invite_secret: SecretStr
    access_token_minutes: int = 60
    invite_days: int = 14
    knowledge_dir: Path = Path(__file__).resolve().parents[3] / "knowledge_data"
    ohlp_csv: Path | None = None
    web_dir: Path | None = Path(__file__).resolve().parents[4] / "frontend" / "dist"
    # «Попробовать как пациент / как врач» on the login page (throwaway synthetic accounts, D-088)
    demo_mode: bool = False
    outbox_poll_seconds: float = 1.0
    outbox_lease_seconds: float = 300.0
    alert_retry_base_seconds: float = 30.0
    alert_retry_max_attempts: int = 12
    # Diary text model (worker only, CPU). "keywords" disables the language model.
    text_model: Literal["llm", "keywords"] = "llm"
    text_model_id: str = "Qwen/Qwen3-4B"
    text_model_revision: str | None = "1cfa9a7208912126459214e8b04321603b3df60c"
    # best measured recall (D-075): translate RU→EN locally, then the English few-shot prompt
    text_model_prompt: Literal["zero_shot", "few_shot", "translate_few_shot"] = "translate_few_shot"
    text_model_threads: int = 8

    @field_validator("jwt_secret", "invite_secret")
    @classmethod
    def _long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("secret must be at least 32 characters")
        return value


def load_settings() -> Settings:
    """Read and validate settings (secrets must be ≥ 32 characters). Called once at startup; request
    code reads them from ``app.state`` via ``api.deps.get_settings``."""
    return Settings()
