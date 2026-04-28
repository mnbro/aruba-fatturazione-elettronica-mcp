"""Pydantic models used by the client and auth manager."""

from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr


def parse_aruba_datetime(value: Any) -> datetime | None:
    """Parse Aruba token date fields such as .issued and .expires."""

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, int | float):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=UTC)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return datetime.fromisoformat(stripped.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            parsed = parsedate_to_datetime(stripped)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    return None


class ArubaToken(BaseModel):
    """Cached Aruba OAuth-like token with redacted representation."""

    access_token: SecretStr = Field(repr=False)
    refresh_token: SecretStr | None = Field(default=None, repr=False)
    token_type: str = "Bearer"  # noqa: S105
    expires_at: datetime
    refresh_expires_at: datetime | None = None
    issued_at: datetime | None = None
    username: str | None = None

    model_config = ConfigDict(frozen=True)

    @classmethod
    def from_response(cls, payload: dict[str, Any], username: str | None = None) -> "ArubaToken":
        now = datetime.now(UTC)
        issued_at = parse_aruba_datetime(payload.get(".issued")) or now
        expires_at = parse_aruba_datetime(payload.get(".expires"))
        if expires_at is None:
            expires_in = int(payload.get("expires_in", 1800))
            expires_at = issued_at + timedelta(seconds=expires_in)
        refresh_expires_at = issued_at + timedelta(minutes=60)
        refresh_token = payload.get("refresh_token")
        return cls(
            access_token=SecretStr(str(payload["access_token"])),
            refresh_token=SecretStr(str(refresh_token)) if refresh_token else None,
            token_type=str(payload.get("token_type", "Bearer")),
            expires_at=expires_at,
            refresh_expires_at=refresh_expires_at,
            issued_at=issued_at,
            username=username,
        )

    def access_token_value(self) -> str:
        return self.access_token.get_secret_value()

    def refresh_token_value(self) -> str | None:
        if self.refresh_token is None:
            return None
        return self.refresh_token.get_secret_value()
