"""Application configuration. Secrets come from the environment only — never hard-coded."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AIMode = Literal["auto", "llm", "heuristic"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- app ---
    environment: str = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api"
    cors_origins: str = "http://localhost:3000"

    # --- database ---
    database_url: str = "postgresql+asyncpg://recoverai:recoverai@localhost:5433/recoverai"
    db_echo: bool = False

    # --- ai ---
    ai_mode: AIMode = "auto"
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None
    ai_model: str = "gemini-3.8-flash"
    ai_timeout_seconds: float = 20.0
    ai_max_concurrency: int = 8
    ai_llm_budget_per_run: int = 150
    ai_max_output_tokens: int = 400

    # --- messaging (WhatsApp / SMS) ---
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    #: Twilio WhatsApp sender, e.g. "whatsapp:+14155238886" (the sandbox number).
    twilio_whatsapp_from: str | None = None
    #: Twilio SMS sender, e.g. "+15005550006".
    twilio_sms_from: str | None = None
    #: Master switch. Off by default so no message can leave the machine by accident.
    messaging_enabled: bool = False
    #: Preferred channel; falls back to the other when the preferred one is unavailable.
    messaging_preferred_channel: str = "whatsapp"

    # --- razorpay (TEST MODE ONLY) ---
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    razorpay_webhook_secret: str | None = None
    razorpay_enabled: bool = False

    # --- policy defaults ---
    policy_max_attempts: int = Field(default=3, ge=1, le=10)
    policy_max_notifications: int = Field(default=2, ge=0, le=10)
    policy_cooldown_minutes: int = Field(default=0, ge=0)
    #: Cart value above which a discount needs manual approval (paise). ₹10,000.
    policy_max_discount_minor: int = 1_000_000
    policy_opportunity_ttl_hours: int = 168

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql://") and not v.startswith("postgresql+"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @field_validator("cors_origins")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_available(self) -> bool:
        return bool(self.gemini_api_key or self.anthropic_api_key)

    @property
    def twilio_configured(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token)

    @property
    def whatsapp_configured(self) -> bool:
        return bool(self.twilio_configured and self.twilio_whatsapp_from)

    @property
    def sms_configured(self) -> bool:
        return bool(self.twilio_configured and self.twilio_sms_from)

    @property
    def messaging_live(self) -> bool:
        """True only when messages can genuinely be delivered to a real handset."""
        return bool(
            self.messaging_enabled and (self.whatsapp_configured or self.sms_configured)
        )

    @property
    def razorpay_configured(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def razorpay_webhook_configured(self) -> bool:
        return bool(self.razorpay_webhook_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
