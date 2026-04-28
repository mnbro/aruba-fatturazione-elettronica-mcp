"""Authentication manager for Aruba token lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .audit import operations_logger
from .config import Settings
from .errors import ArubaAuthError
from .models import ArubaToken
from .rate_limit import rate_limiter


class ArubaAuthManager:
    """Caches Aruba tokens and refreshes them under an async lock."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = http_client
        self._owns_client = http_client is None
        self._token: ArubaToken | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def token(self) -> ArubaToken | None:
        return self._token

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.settings.timeout_seconds,
                headers={"User-Agent": self.settings.user_agent, "Accept": "application/json"},
            )
        return self._client

    def _needs_refresh(self, token: ArubaToken) -> bool:
        skewed_now = datetime.now(UTC) + timedelta(seconds=self.settings.token_refresh_skew_seconds)
        return token.expires_at <= skewed_now

    async def get_access_token(self) -> str:
        """Return a valid access token, signing in or refreshing when required."""

        token = self._token
        if token is not None and not self._needs_refresh(token):
            return token.access_token_value()
        async with self._lock:
            token = self._token
            if token is not None and not self._needs_refresh(token):
                return token.access_token_value()
            if token is not None and token.refresh_token_value():
                try:
                    token = await self.refresh()
                    return token.access_token_value()
                except ArubaAuthError as exc:
                    if exc.status_code not in {400, 401} and exc.error_code != "invalid_grant":
                        raise
                    operations_logger.info(
                        "refresh_failed_falling_back_to_signin status_code=%s", exc.status_code
                    )
            token = await self.sign_in()
            return token.access_token_value()

    async def sign_in(self) -> ArubaToken:
        """POST /auth/signin with password grant. This is the only business-external POST."""

        payload = {
            "grant_type": "password",
            "username": self.settings.username,
            "password": self.settings.password.get_secret_value(),
        }
        token = await self._post_form_auth(payload)
        self._token = token
        operations_logger.info("auth_signin_success environment=%s", self.settings.env)
        return token

    async def refresh(self) -> ArubaToken:
        """POST /auth/signin with refresh_token grant."""

        if self._token is None or not self._token.refresh_token_value():
            raise ArubaAuthError("No refresh token is cached.")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._token.refresh_token_value(),
        }
        token = await self._post_form_auth(payload)
        self._token = token
        operations_logger.info("auth_refresh_success environment=%s", self.settings.env)
        return token

    async def _post_form_auth(self, payload: Mapping[str, str | None]) -> ArubaToken:
        await rate_limiter.acquire("auth", self.settings.auth_rate_limit_per_minute)
        response = await self._http_client().post(
            f"{self.settings.auth_base_url}/auth/signin",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        body = _safe_json(response)
        if not response.is_success:
            raise ArubaAuthError(
                "Aruba authentication failed.",
                status_code=response.status_code,
                error_code=str(body.get("error") or body.get("errorCode") or ""),
                error_description=str(body.get("error_description") or body.get("message") or ""),
            )
        try:
            return ArubaToken.from_response(body, username=self.settings.username)
        except Exception as exc:  # noqa: BLE001
            raise ArubaAuthError(
                "Aruba authentication response did not include a valid token."
            ) from exc

    async def get_auth_status(self) -> dict[str, Any]:
        """Return token cache status without secrets."""

        token = self._token
        return {
            "environment": self.settings.env,
            "auth_base_url": self.settings.auth_base_url,
            "ws_base_url": self.settings.ws_base_url,
            "token_cached": token is not None,
            "expires_at": token.expires_at.isoformat() if token else None,
            "refresh_expires_at": token.refresh_expires_at.isoformat()
            if token and token.refresh_expires_at
            else None,
            "username": token.username if token else None,
        }


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"text": response.text}
    if isinstance(payload, dict):
        return payload
    return {"data": payload}
