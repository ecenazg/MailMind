"""
config/settings.py
──────────────────
Central Pydantic-based settings loader.  Every module imports `settings`
from here — no module reads os.environ directly.
"""
from __future__ import annotations

import os
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All configuration values.  Reads from .env (or environment variables).
    Validation is performed at startup; the app will exit with a clear
    message if a required value is missing.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── OpenAI ────────────────────────────────────────────
    openai_api_key: str = Field(..., description="OpenAI API key")
    openai_model: str = Field("gpt-4o", description="Model used for classification")

    # ── Google OAuth2 ─────────────────────────────────────
    google_client_id: str = Field(...)
    google_client_secret: str = Field(...)
    google_token_path: Path = Field(Path("config/token.json"))
    google_credentials_path: Path = Field(Path("config/credentials.json"))

    # ── Gmail ─────────────────────────────────────────────
    gmail_poll_interval_seconds: int = Field(30)
    gmail_label_processed: str = Field("MailMind/Processed")
    gmail_user_id: str = Field("me")

    # ── ClickUp ───────────────────────────────────────────
    clickup_api_token: str = Field(...)
    clickup_list_id: str = Field(...)

    # ── Google Sheets ─────────────────────────────────────
    google_sheet_id: str = Field(...)
    google_sheet_audit_tab: str = Field("AuditLog")

    # ── Webhooks ──────────────────────────────────────────
    webhook_host: str = Field("0.0.0.0")
    webhook_port: int = Field(8000)
    webhook_secret: str = Field("change-me")

    # ── Langfuse ──────────────────────────────────────────
    langfuse_public_key: str = Field(...)
    langfuse_secret_key: str = Field(...)
    langfuse_host: str = Field("https://cloud.langfuse.com")

    # ── Logging ───────────────────────────────────────────
    log_level: str = Field("INFO")
    log_file: Path = Field(Path("logs/mailmind.jsonl"))

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return v.upper()

    @field_validator("google_token_path", "google_credentials_path", mode="before")
    @classmethod
    def _ensure_path(cls, v):
        return Path(v)


# Singleton — import this everywhere
settings = Settings()
