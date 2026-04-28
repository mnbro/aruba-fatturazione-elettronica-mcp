from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from aruba_fatturazione_elettronica_mcp.client import ArubaFatturazioneClient, serialize_params
from aruba_fatturazione_elettronica_mcp.config import Settings
from aruba_fatturazione_elettronica_mcp.errors import ArubaBinaryResponseTooLarge
from aruba_fatturazione_elettronica_mcp.models import ArubaToken


class FakeAuth:
    def __init__(self) -> None:
        self.refresh_calls = 0

    async def get_access_token(self) -> str:
        return "token"

    async def refresh(self) -> ArubaToken:
        self.refresh_calls += 1
        return ArubaToken(
            access_token="token2",
            refresh_token="refresh",
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )

    async def close(self) -> None:
        return None


def settings(**kwargs: object) -> Settings:
    return Settings(username="user", password="pass", **kwargs)


@pytest.mark.asyncio
async def test_get_attaches_authorization_bearer_and_serializes_params() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer token"
        assert request.url.params["includeFile"] == "true"
        assert request.url.params["vatcodeSender"] == "IT123"
        return httpx.Response(200, json={"ok": True})

    client = ArubaFatturazioneClient(
        settings(),
        auth_manager=FakeAuth(),  # type: ignore[arg-type]
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await client.ws_get(
        "/services/test", {"includeFile": True, "vatcodeSender": "IT123"}
    ) == {"ok": True}


@pytest.mark.asyncio
async def test_401_triggers_refresh_and_one_retry() -> None:
    calls = 0
    auth = FakeAuth()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(401, json={"message": "expired"})
        return httpx.Response(200, json={"retried": True})

    client = ArubaFatturazioneClient(
        settings(),
        auth_manager=auth,  # type: ignore[arg-type]
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await client.ws_get("/services/test") == {"retried": True}
    assert auth.refresh_calls == 1
    assert calls == 2


def test_bool_params_are_lowercase() -> None:
    assert serialize_params({"includePdf": False, "includeFile": True}) == {
        "includePdf": "false",
        "includeFile": "true",
    }


@pytest.mark.asyncio
async def test_binary_zip_response_converted_to_base64() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"zip-bytes",
            headers={
                "content-type": "application/zip",
                "content-disposition": 'attachment; filename="a.zip"',
            },
        )

    client = ArubaFatturazioneClient(
        settings(),
        auth_manager=FakeAuth(),  # type: ignore[arg-type]
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    binary = await client.ws_get("/services/invoice/out/getZipByFilename", expected_binary=True)
    assert binary["filename"] == "a.zip"
    assert binary["content_type"] == "application/zip"
    assert binary["data_base64"] == "emlwLWJ5dGVz"


@pytest.mark.asyncio
async def test_over_max_binary_response_raises() -> None:
    client = ArubaFatturazioneClient(
        settings(max_binary_response_bytes=2),
        auth_manager=FakeAuth(),  # type: ignore[arg-type]
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"abc"))
        ),
    )
    with pytest.raises(ArubaBinaryResponseTooLarge):
        await client.ws_get("/zip", expected_binary=True)
