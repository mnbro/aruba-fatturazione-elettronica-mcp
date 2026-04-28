from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from aruba_fatturazione_elettronica_mcp.auth import ArubaAuthManager
from aruba_fatturazione_elettronica_mcp.config import Settings
from aruba_fatturazione_elettronica_mcp.models import ArubaToken


class NoopLimiter:
    async def acquire(self, bucket: str, limit_per_minute: int) -> None:
        return None


def settings() -> Settings:
    return Settings(username="user", password="pass")


def token_payload(
    access: str = "access", refresh: str = "refresh", expires_in: int = 1800
) -> dict[str, Any]:
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": expires_in,
    }


@pytest.fixture(autouse=True)
def no_auth_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aruba_fatturazione_elettronica_mcp.auth.rate_limiter", NoopLimiter())


@pytest.mark.asyncio
async def test_signin_success() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/auth/signin"
        assert request.headers["content-type"] == "application/x-www-form-urlencoded;charset=UTF-8"
        assert b"grant_type=password" in request.content
        return httpx.Response(200, json=token_payload())

    manager = ArubaAuthManager(
        settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    token = await manager.sign_in()
    assert token.access_token_value() == "access"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_refresh_success() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=token_payload(access=f"access-{calls}"))

    manager = ArubaAuthManager(
        settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    await manager.sign_in()
    token = await manager.refresh()
    assert token.access_token_value() == "access-2"


@pytest.mark.asyncio
async def test_expired_access_token_triggers_refresh() -> None:
    manager = ArubaAuthManager(
        settings(),
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=token_payload("new")))
        ),
    )
    manager._token = ArubaToken(  # noqa: SLF001
        access_token="old",
        refresh_token="refresh",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert await manager.get_access_token() == "new"


@pytest.mark.asyncio
async def test_refresh_invalid_grant_triggers_signin_once() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json=token_payload("signed-in"))

    manager = ArubaAuthManager(
        settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    manager._token = ArubaToken(  # noqa: SLF001
        access_token="old",
        refresh_token="refresh",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert await manager.get_access_token() == "signed-in"
    assert calls == 2


@pytest.mark.asyncio
async def test_concurrent_get_access_token_calls_only_signin_once() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=token_payload())

    manager = ArubaAuthManager(
        settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    results = await asyncio.gather(*(manager.get_access_token() for _ in range(10)))
    assert results == ["access"] * 10
    assert calls == 1


def test_credentials_tokens_do_not_appear_in_token_repr_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    token = ArubaToken(
        access_token="secret-access",
        refresh_token="secret-refresh",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    logging.getLogger("aruba_fatturazione_elettronica_mcp.operations").info("token=%r", token)
    assert "secret-access" not in repr(token)
    assert "secret-refresh" not in caplog.text
