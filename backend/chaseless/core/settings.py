from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    api_public_url: str = "http://localhost:8000"
    web_public_url: str = "http://localhost:3000"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    database_url: str = "sqlite:///./chaseless.db"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    app_session_secret: str = "development-only-change-me-please"
    internal_service_token: str = "development-service-token"
    field_encryption_key: str = ""
    reviewer_mask_pii: bool = True

    razorpay_mode: Literal["test"] = "test"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    razorpay_previous_webhook_secret: str = ""
    razorpay_api_base_url: str = "https://api.razorpay.com"

    llm_provider: str = "disabled"
    llm_model: str = "gemini-3.6-flash"
    llm_api_key: str = ""
    llm_fallback_provider: str = "disabled"
    llm_fallback_model: str = "openai/gpt-oss-20b"
    llm_fallback_api_key: str = ""
    llm_timeout_seconds: int = 20
    llm_max_calls_per_run: int = 25

    messaging_provider: str = "mock"
    messaging_api_key: str = ""
    voice_provider: str = "mock"
    voice_api_key: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""
    twilio_whatsapp_test_to: str = ""
    twilio_whatsapp_content_sid: str = ""
    twilio_voice_from: str = ""
    twilio_voice_test_to: str = ""
    twilio_sms_from: str = ""
    twilio_sms_test_to: str = ""
    twilio_sms_template: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_test_to: str = ""
    sarvam_api_key: str = ""

    default_merchant_timezone: str = "Asia/Kolkata"
    default_attribution_window_hours: int = 72
    default_recovery_budget_minor: int = 100_000
    default_contact_budget: int = 100
    max_replans_per_case: int = 3
    evaluation_output_dir: str = "evaluation/results"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
