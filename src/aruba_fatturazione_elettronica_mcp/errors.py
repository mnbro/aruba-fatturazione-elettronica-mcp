"""Typed errors returned by the Aruba MCP server."""

from typing import Any


class ArubaMCPError(Exception):
    """Base error for this package."""

    error = "aruba_mcp_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        error_description: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.error_description = error_description
        if retryable is not None:
            self.retryable = retryable

    def to_dict(self, endpoint: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "error": self.error,
            "message": self.message,
            "retryable": self.retryable,
        }
        if endpoint:
            payload["endpoint"] = endpoint
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.error_code:
            payload["error_code"] = self.error_code
        if self.error_description:
            payload["error_description"] = self.error_description
        return payload


class ArubaAuthError(ArubaMCPError):
    """Authentication failed."""

    error = "auth_error"


class ArubaAPIError(ArubaMCPError):
    """Aruba API returned an error."""

    error = "api_error"


class ArubaRateLimitError(ArubaMCPError):
    """Per-process rate limiter rejected the request."""

    error = "rate_limit_exceeded"
    retryable = True


class ArubaValidationError(ArubaMCPError):
    """Local validation failed before calling Aruba."""

    error = "validation_error"


class ArubaSensitiveReadConfirmationRequired(ArubaMCPError):
    """Sensitive read needs explicit confirmation."""

    error = "sensitive_read_confirmation_required"

    def __init__(self, message: str = "Set confirm_read=true to run this sensitive read.") -> None:
        super().__init__(message)

    def to_dict(self, endpoint: str | None = None) -> dict[str, Any]:
        payload = super().to_dict(endpoint)
        payload["required_param"] = "confirm_read"
        return payload


class ArubaBinaryResponseTooLarge(ArubaMCPError):
    """Binary response exceeded configured limit."""

    error = "binary_response_too_large"
