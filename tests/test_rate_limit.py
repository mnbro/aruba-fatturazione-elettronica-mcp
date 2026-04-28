from __future__ import annotations

import pytest

from aruba_fatturazione_elettronica_mcp.rate_limit import AsyncRateLimiter


@pytest.mark.asyncio
async def test_rate_limit_allows_capacity() -> None:
    now = 100.0
    limiter = AsyncRateLimiter(clock=lambda: now)
    await limiter.acquire("bucket", 2)
    await limiter.acquire("bucket", 2)
