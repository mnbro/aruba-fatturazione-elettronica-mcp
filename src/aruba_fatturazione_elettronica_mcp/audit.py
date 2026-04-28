"""Structured audit and operation logging."""

from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Any

from .redaction import redact

audit_logger = logging.getLogger("aruba_fatturazione_elettronica_mcp.audit")
operations_logger = logging.getLogger("aruba_fatturazione_elettronica_mcp.operations")


def audit_event(
    *,
    tool_name: str,
    endpoint: str,
    environment: str,
    status_code: int | None,
    duration_ms: float,
    context: dict[str, Any] | None = None,
    enabled: bool = True,
) -> None:
    """Emit a redacted audit event."""

    if not enabled:
        return
    payload = {
        "tool_name": tool_name,
        "endpoint": endpoint,
        "environment": environment,
        "status_code": status_code,
        "duration_ms": round(duration_ms, 2),
        "context": redact(context or {}),
    }
    audit_logger.info("aruba_tool_call %s", json.dumps(payload, sort_keys=True))


class Timer:
    """Small monotonic timer helper."""

    def __init__(self) -> None:
        self._start = perf_counter()

    @property
    def elapsed_ms(self) -> float:
        return (perf_counter() - self._start) * 1000
