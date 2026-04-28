"""Async Aruba Fatturazione Elettronica HTTP client."""

from __future__ import annotations

import base64
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin

import httpx

from .auth import ArubaAuthManager
from .config import Settings
from .errors import ArubaAPIError, ArubaBinaryResponseTooLarge
from .rate_limit import rate_limiter


class ArubaFatturazioneClient:
    """Read-only client for Aruba auth and WS GET endpoints."""

    def __init__(
        self,
        settings: Settings,
        auth_manager: ArubaAuthManager | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.auth = auth_manager or ArubaAuthManager(settings)
        self._client = http_client
        self._owns_client = http_client is None

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
        await self.auth.close()

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.settings.timeout_seconds,
                headers={
                    "User-Agent": self.settings.user_agent,
                    "Accept": "application/json",
                },
            )
        return self._client

    async def auth_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET an authentication-base Aruba endpoint."""

        return await self._get(
            "auth", path, params=params, expected_binary=False, bucket="auth_read"
        )

    async def ws_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        expected_binary: bool = False,
        bucket: str = "find_sent",
    ) -> Any:
        """GET a WS-base Aruba endpoint."""

        return await self._get(
            "ws", path, params=params, expected_binary=expected_binary, bucket=bucket
        )

    async def _get(
        self,
        base: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        expected_binary: bool,
        bucket: str,
    ) -> Any:
        limit = self.settings.rate_limit_for_bucket(bucket)
        if bucket != "auth_read":
            await rate_limiter.acquire(bucket, limit)
        return await self._request_get(base, path, params, expected_binary, retry_auth=True)

    async def _request_get(
        self,
        base: str,
        path: str,
        params: dict[str, Any] | None,
        expected_binary: bool,
        *,
        retry_auth: bool,
    ) -> Any:
        token = await self.auth.get_access_token()
        url = self._url_for(base, path)
        response = await self._http_client().get(
            url,
            params=serialize_params(params or {}),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if response.status_code == 401 and retry_auth:
            await self.auth.refresh()
            return await self._request_get(base, path, params, expected_binary, retry_auth=False)
        if not response.is_success:
            raise _api_error(response)
        if expected_binary:
            return self._binary_envelope(response, path)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ArubaAPIError(
                "Aruba returned non-JSON content for an endpoint expected to be JSON.",
                status_code=response.status_code,
            ) from exc

    def _url_for(self, base: str, path: str) -> str:
        base_url = self.settings.auth_base_url if base == "auth" else self.settings.ws_base_url
        return urljoin(f"{base_url}/", path.lstrip("/"))

    def _binary_envelope(self, response: httpx.Response, path: str) -> dict[str, Any]:
        content = response.content
        content_length = len(content)
        if content_length > self.settings.max_binary_response_bytes:
            raise ArubaBinaryResponseTooLarge(
                "Aruba binary response exceeded ARUBA_MAX_BINARY_RESPONSE_BYTES.",
                status_code=response.status_code,
            )
        return {
            "filename": filename_from_response(response, path),
            "content_type": response.headers.get("content-type", "application/octet-stream"),
            "content_length": content_length,
            "data_base64": base64.b64encode(content).decode("ascii"),
        }


def serialize_params(params: dict[str, Any]) -> dict[str, str]:
    """Serialize query params using Aruba's original parameter names."""

    serialized: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            serialized[key] = "true" if value else "false"
        else:
            serialized[key] = str(value)
    return serialized


def filename_from_response(response: httpx.Response, path: str) -> str:
    disposition = response.headers.get("content-disposition", "")
    for part in disposition.split(";"):
        part = part.strip()
        if part.lower().startswith("filename="):
            return str(part.split("=", 1)[1].strip('"'))
    basename = PurePosixPath(path).name
    return basename if basename and "{" not in basename else "aruba-response.bin"


def _api_error(response: httpx.Response) -> ArubaAPIError:
    try:
        body = response.json()
    except ValueError:
        body = {"message": response.text}
    if not isinstance(body, dict):
        body = {"data": body}
    return ArubaAPIError(
        str(body.get("message") or body.get("error_description") or f"HTTP {response.status_code}"),
        status_code=response.status_code,
        error_code=str(body.get("error") or body.get("errorCode") or ""),
        error_description=str(body.get("error_description") or body.get("description") or ""),
        retryable=response.status_code in {408, 429, 500, 502, 503, 504},
    )
