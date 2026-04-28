from __future__ import annotations

import pytest

from aruba_fatturazione_elettronica_mcp.config import Settings
from aruba_fatturazione_elettronica_mcp.server import aruba_get_sent_invoice_by_filename


class FakeClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.called = False

    async def ws_get(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.called = True
        return {"invoice": "ok"}

    async def auth_get(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.called = True
        return {"auth": "ok"}


@pytest.mark.asyncio
async def test_sensitive_read_requires_confirm_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient(Settings(username="user", password="pass", confirm_sensitive_reads=True))
    monkeypatch.setattr("aruba_fatturazione_elettronica_mcp.server._client", fake)
    result = await aruba_get_sent_invoice_by_filename("IT.xml", includeFile=True)
    assert result["ok"] is False
    assert result["error"] == "sensitive_read_confirmation_required"
    assert fake.called is False


@pytest.mark.asyncio
async def test_sensitive_read_proceeds_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient(Settings(username="user", password="pass", confirm_sensitive_reads=False))
    monkeypatch.setattr("aruba_fatturazione_elettronica_mcp.server._client", fake)
    result = await aruba_get_sent_invoice_by_filename("IT.xml", includeFile=True)
    assert result["ok"] is True
    assert fake.called is True
