"""Per-process async rate limiter."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Callable

from .audit import operations_logger


class AsyncRateLimiter:
    """Sliding-window rate limiter scoped to this process."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._clock = clock or time.monotonic

    async def acquire(self, bucket: str, limit_per_minute: int) -> None:
        """Wait until the bucket has capacity."""

        if limit_per_minute <= 0:
            return
        async with self._locks[bucket]:
            while True:
                now = self._clock()
                events = self._events[bucket]
                window_start = now - 60
                while events and events[0] <= window_start:
                    events.popleft()
                if len(events) < limit_per_minute:
                    events.append(now)
                    return
                sleep_for = max(60 - (now - events[0]), 0.01)
                operations_logger.info(
                    "rate_limit_wait bucket=%s limit_per_minute=%s sleep_seconds=%.2f",
                    bucket,
                    limit_per_minute,
                    sleep_for,
                )
                await asyncio.sleep(sleep_for)


rate_limiter = AsyncRateLimiter()
