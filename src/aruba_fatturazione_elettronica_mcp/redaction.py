"""Redaction helpers for logs and errors."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

SECRET_KEYS = ("password", "token", "authorization", "secret", "grant_type")
PII_KEYS = (
    "address",
    "cap",
    "codicefiscale",
    "fiscal",
    "fiscalcode",
    "iban",
    "indirizzo",
    "pec",
    "sdi_code",
    "vat",
    "vatcode",
)
BASE64_RE = re.compile(r"^[A-Za-z0-9+/=\s]{120,}$")


def hash_value(value: str) -> str:
    """Hash sensitive identifiers without retaining the original value."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def looks_like_sensitive_payload(value: str) -> bool:
    """Return true for XML, PDF/ZIP base64-like or long encoded payloads."""

    stripped = value.strip()
    if stripped.startswith(("<?xml", "<FatturaElettronica", "%PDF", "PK")):
        return True
    return bool(BASE64_RE.match(stripped))


def redact(value: Any, *, key: str = "") -> Any:
    """Recursively redact secrets, tokens, PII identifiers, XML and Base64 payloads."""

    key_lower = key.lower()
    if any(marker in key_lower for marker in SECRET_KEYS):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {
            str(item_key): redact(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact(item, key=key) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item, key=key) for item in value)
    if value is None or isinstance(value, bool | int | float):
        return value
    text = str(value)
    if any(marker in key_lower for marker in PII_KEYS):
        return {"redacted": True, "sha256": hash_value(text)}
    if looks_like_sensitive_payload(text):
        return {"redacted": True, "kind": "sensitive_payload", "length": len(text)}
    return value
