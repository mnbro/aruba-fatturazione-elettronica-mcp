"""Configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import __version__


class Settings(BaseSettings):
    """Runtime settings for the MCP server."""

    env: Literal["demo", "production"] = Field(default="demo", validation_alias="ARUBA_ENV")
    username: str = Field(..., validation_alias="ARUBA_USERNAME")
    password: SecretStr = Field(..., validation_alias="ARUBA_PASSWORD")
    timeout_seconds: float = Field(default=30, gt=0, validation_alias="ARUBA_TIMEOUT_SECONDS")
    token_refresh_skew_seconds: int = Field(
        default=120, ge=0, validation_alias="ARUBA_TOKEN_REFRESH_SKEW_SECONDS"
    )
    confirm_sensitive_reads: bool = Field(
        default=False, validation_alias="ARUBA_CONFIRM_SENSITIVE_READS"
    )
    redact_base64_in_logs: bool = Field(
        default=True, validation_alias="ARUBA_REDACT_BASE64_IN_LOGS"
    )
    max_binary_response_bytes: int = Field(
        default=10_485_760, gt=0, validation_alias="ARUBA_MAX_BINARY_RESPONSE_BYTES"
    )
    audit_log_enabled: bool = Field(default=True, validation_alias="ARUBA_AUDIT_LOG_ENABLED")
    http_user_agent: str | None = Field(default=None, validation_alias="ARUBA_HTTP_USER_AGENT")
    index_db_path: str = Field(
        default=".aruba-invoice-index.sqlite3", validation_alias="ARUBA_INDEX_DB_PATH"
    )
    auth_rate_limit_per_minute: int = Field(
        default=1, gt=0, validation_alias="ARUBA_AUTH_RATE_LIMIT_PER_MINUTE"
    )
    find_sent_rate_limit_per_minute: int = Field(
        default=12, gt=0, validation_alias="ARUBA_FIND_SENT_RATE_LIMIT_PER_MINUTE"
    )
    find_received_rate_limit_per_minute: int = Field(
        default=12, gt=0, validation_alias="ARUBA_FIND_RECEIVED_RATE_LIMIT_PER_MINUTE"
    )
    notification_sent_rate_limit_per_minute: int = Field(
        default=12, gt=0, validation_alias="ARUBA_NOTIFICATION_SENT_RATE_LIMIT_PER_MINUTE"
    )
    notification_received_rate_limit_per_minute: int = Field(
        default=12, gt=0, validation_alias="ARUBA_NOTIFICATION_RECEIVED_RATE_LIMIT_PER_MINUTE"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ARUBA_USERNAME must not be empty")
        return value.strip()

    @property
    def auth_base_url(self) -> str:
        if self.env == "demo":
            return "https://demoauth.fatturazioneelettronica.aruba.it"
        return "https://auth.fatturazioneelettronica.aruba.it"

    @property
    def ws_base_url(self) -> str:
        if self.env == "demo":
            return "https://demows.fatturazioneelettronica.aruba.it"
        return "https://ws.fatturazioneelettronica.aruba.it"

    @property
    def user_agent(self) -> str:
        return self.http_user_agent or f"aruba-fatturazione-elettronica-mcp/{__version__}"

    def rate_limit_for_bucket(self, bucket: str) -> int:
        mapping = {
            "auth": self.auth_rate_limit_per_minute,
            "find_sent": self.find_sent_rate_limit_per_minute,
            "find_received": self.find_received_rate_limit_per_minute,
            "notification_sent": self.notification_sent_rate_limit_per_minute,
            "notification_received": self.notification_received_rate_limit_per_minute,
        }
        return mapping.get(bucket, 60)


@lru_cache
def get_settings() -> Settings:
    """Return cached settings."""

    return Settings()  # type: ignore[call-arg]


def clear_settings_cache() -> None:
    """Clear settings cache for tests."""

    get_settings.cache_clear()
