"""FastMCP server exposing Aruba GET endpoints as read-only tools."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

from .annotations import READ_ONLY_TOOL
from .audit import Timer, audit_event
from .client import ArubaFatturazioneClient
from .config import Settings, get_settings
from .endpoints import ENDPOINT_BY_NAME, ENDPOINTS, Endpoint
from .errors import (
    ArubaMCPError,
    ArubaSensitiveReadConfirmationRequired,
    ArubaValidationError,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("aruba-fatturazione-elettronica-readonly")

_settings: Settings | None = None
_client: ArubaFatturazioneClient | None = None


def get_client() -> ArubaFatturazioneClient:
    """Return a lazily initialized client."""

    global _client, _settings
    if _client is not None:
        return _client
    if _settings is None:
        _settings = get_settings()
    _client = ArubaFatturazioneClient(_settings)
    return _client


def _endpoint(name: str) -> Endpoint:
    return ENDPOINT_BY_NAME[name]


async def _execute(
    endpoint: Endpoint,
    *,
    tool_name: str,
    path_values: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    confirm_read: bool = False,
    sensitive_now: bool | None = None,
) -> dict[str, Any]:
    client = get_client()
    settings = client.settings
    sensitive = endpoint.sensitive_read if sensitive_now is None else sensitive_now
    if settings.confirm_sensitive_reads and sensitive and not confirm_read:
        return ArubaSensitiveReadConfirmationRequired().to_dict(endpoint.endpoint_label)
    timer = Timer()
    status_code: int | None = None
    try:
        path = _format_path(endpoint.path_template, path_values or {})
        if endpoint.base == "auth":
            data = await client.auth_get(path, params=params)
        else:
            data = await client.ws_get(
                path,
                params=params,
                expected_binary=endpoint.returns_binary,
                bucket=endpoint.rate_limit_bucket,
            )
        status_code = 200
        payload: dict[str, Any] = {
            "ok": True,
            "endpoint": f"{endpoint.method} {path}",
            "environment": settings.env,
        }
        if endpoint.returns_binary:
            payload["binary"] = data
        else:
            payload["data"] = data
        return payload
    except ArubaMCPError as exc:
        status_code = exc.status_code
        return exc.to_dict(endpoint.endpoint_label)
    finally:
        audit_event(
            tool_name=tool_name,
            endpoint=endpoint.endpoint_label,
            environment=settings.env,
            status_code=status_code,
            duration_ms=timer.elapsed_ms,
            context={"params": params, "path_values": path_values},
            enabled=settings.audit_log_enabled,
        )


def _format_path(template: str, values: dict[str, str]) -> str:
    path = template
    for key, value in values.items():
        _require_non_empty(key, value)
        path = path.replace("{" + key + "}", value)
    return path


def _require_non_empty(name: str, value: str | None) -> None:
    if value is None or not str(value).strip():
        raise ArubaValidationError(f"{name} must not be empty.")


def _validate_page_size(page: int, size: int) -> None:
    if page < 1:
        raise ArubaValidationError("page must be >= 1.")
    if size < 1 or size > 100:
        raise ArubaValidationError("size must be between 1 and 100.")


def _validate_iso8601(name: str, value: str | None) -> None:
    if value is None:
        return
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArubaValidationError(f"{name} must be ISO 8601.") from exc


def _exactly_one(**values: str | None) -> None:
    present = [key for key, value in values.items() if value is not None and str(value).strip()]
    if len(present) != 1:
        keys = ", ".join(values)
        raise ArubaValidationError(f"Exactly one of {keys} must be provided.")


def _find_params(
    username: str | None,
    page: int,
    size: int,
    startDate: str | None,
    endDate: str | None,
    countrySender: str | None,
    vatcodeSender: str | None,
    fiscalcodeSender: str | None,
    countryReceiver: str | None,
    vatcodeReceiver: str | None,
    fiscalcodeReceiver: str | None,
) -> dict[str, Any]:
    _validate_page_size(page, size)
    _validate_iso8601("startDate", startDate)
    _validate_iso8601("endDate", endDate)
    settings = get_client().settings
    return {
        "username": username or settings.username,
        "page": page,
        "size": size,
        "startDate": startDate,
        "endDate": endDate,
        "countrySender": countrySender,
        "vatcodeSender": vatcodeSender,
        "fiscalcodeSender": fiscalcodeSender,
        "countryReceiver": countryReceiver,
        "vatcodeReceiver": vatcodeReceiver,
        "fiscalcodeReceiver": fiscalcodeReceiver,
    }


def _file_sensitive(includeFile: bool, includePdf: bool) -> bool:
    return includeFile or includePdf


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_auth_status() -> dict[str, Any]:
    """Return local auth cache status without access_token or refresh_token."""

    client = get_client()
    status = await client.auth.get_auth_status()
    status["auth_base_url_host"] = urlparse(status.pop("auth_base_url")).hostname
    status["ws_base_url_host"] = urlparse(status.pop("ws_base_url")).hostname
    return {"ok": True, **status}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_user_info() -> dict[str, Any]:
    """GET /auth/userInfo. Read-only account information."""

    return await _execute(_endpoint("user_info"), tool_name="aruba_get_user_info")


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_list_multicedenti(
    countryCode: str | None = None,
    vatCode: str | None = None,
    status: str | None = None,
    size: int = 10,
    page: int = 1,
) -> dict[str, Any]:
    """GET /auth/multicedenti. Read-only multiseller listing."""

    _validate_page_size(page, size)
    return await _execute(
        _endpoint("multicedenti"),
        tool_name="aruba_list_multicedenti",
        params={
            "countryCode": countryCode,
            "vatCode": vatCode,
            "status": status,
            "size": size,
            "page": page,
        },
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_multicedente_by_id(id: str) -> dict[str, Any]:
    """GET /auth/multicedenti/{id}. Read-only multiseller details."""

    _require_non_empty("id", id)
    return await _execute(
        _endpoint("multicedente_by_id"),
        tool_name="aruba_get_multicedente_by_id",
        path_values={"id": id},
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_find_sent_invoices(
    username: str | None = None,
    page: int = 1,
    size: int = 10,
    startDate: str | None = None,
    endDate: str | None = None,
    countrySender: str | None = None,
    vatcodeSender: str | None = None,
    fiscalcodeSender: str | None = None,
    countryReceiver: str | None = None,
    vatcodeReceiver: str | None = None,
    fiscalcodeReceiver: str | None = None,
) -> dict[str, Any]:
    """GET /services/invoice/out/findByUsername. Read-only sent invoice search."""

    return await _execute(
        _endpoint("find_sent_invoices"),
        tool_name="aruba_find_sent_invoices",
        params=_find_params(
            username,
            page,
            size,
            startDate,
            endDate,
            countrySender,
            vatcodeSender,
            fiscalcodeSender,
            countryReceiver,
            vatcodeReceiver,
            fiscalcodeReceiver,
        ),
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_find_received_invoices(
    username: str | None = None,
    page: int = 1,
    size: int = 10,
    startDate: str | None = None,
    endDate: str | None = None,
    countrySender: str | None = None,
    vatcodeSender: str | None = None,
    fiscalcodeSender: str | None = None,
    countryReceiver: str | None = None,
    vatcodeReceiver: str | None = None,
    fiscalcodeReceiver: str | None = None,
) -> dict[str, Any]:
    """GET /services/invoice/in/findByUsername. Read-only received invoice search."""

    return await _execute(
        _endpoint("find_received_invoices"),
        tool_name="aruba_find_received_invoices",
        params=_find_params(
            username,
            page,
            size,
            startDate,
            endDate,
            countrySender,
            vatcodeSender,
            fiscalcodeSender,
            countryReceiver,
            vatcodeReceiver,
            fiscalcodeReceiver,
        ),
    )


async def _invoice_by_filename(
    endpoint_name: str,
    tool_name: str,
    filename: str,
    includePdf: bool,
    includeFile: bool,
    confirm_read: bool,
) -> dict[str, Any]:
    _require_non_empty("filename", filename)
    return await _execute(
        _endpoint(endpoint_name),
        tool_name=tool_name,
        params={"filename": filename, "includePdf": includePdf, "includeFile": includeFile},
        confirm_read=confirm_read,
        sensitive_now=_file_sensitive(includeFile, includePdf),
    )


async def _invoice_by_id(
    endpoint_name: str,
    tool_name: str,
    invoiceId: str,
    includePdf: bool,
    includeFile: bool,
    confirm_read: bool,
) -> dict[str, Any]:
    _require_non_empty("invoiceId", invoiceId)
    return await _execute(
        _endpoint(endpoint_name),
        tool_name=tool_name,
        path_values={"invoiceId": invoiceId},
        params={"includePdf": includePdf, "includeFile": includeFile},
        confirm_read=confirm_read,
        sensitive_now=_file_sensitive(includeFile, includePdf),
    )


async def _invoice_by_sdi(
    endpoint_name: str,
    tool_name: str,
    idSdi: str,
    includePdf: bool,
    includeFile: bool,
    confirm_read: bool,
) -> dict[str, Any]:
    _require_non_empty("idSdi", idSdi)
    return await _execute(
        _endpoint(endpoint_name),
        tool_name=tool_name,
        params={"idSdi": idSdi, "includePdf": includePdf, "includeFile": includeFile},
        confirm_read=confirm_read,
        sensitive_now=_file_sensitive(includeFile, includePdf),
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_sent_invoice_by_filename(
    filename: str,
    includePdf: bool = False,
    includeFile: bool = True,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """GET /services/invoice/out/getByFilename. Sensitive when file/PDF Base64 is requested."""

    return await _invoice_by_filename(
        "sent_invoice_by_filename",
        "aruba_get_sent_invoice_by_filename",
        filename,
        includePdf,
        includeFile,
        confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_received_invoice_by_filename(
    filename: str,
    includePdf: bool = False,
    includeFile: bool = True,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """GET /services/invoice/in/getByFilename. Sensitive when file/PDF Base64 is requested."""

    return await _invoice_by_filename(
        "received_invoice_by_filename",
        "aruba_get_received_invoice_by_filename",
        filename,
        includePdf,
        includeFile,
        confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_sent_invoice_zip_by_filename(
    filename: str, confirm_read: bool = False
) -> dict[str, Any]:
    """GET /services/invoice/out/getZipByFilename. Sensitive Base64 ZIP read."""

    _require_non_empty("filename", filename)
    return await _execute(
        _endpoint("sent_invoice_zip_by_filename"),
        tool_name="aruba_get_sent_invoice_zip_by_filename",
        params={"filename": filename},
        confirm_read=confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_received_invoice_zip_by_filename(
    filename: str, confirm_read: bool = False
) -> dict[str, Any]:
    """GET /services/invoice/in/getZipByFilename. Sensitive Base64 ZIP read."""

    _require_non_empty("filename", filename)
    return await _execute(
        _endpoint("received_invoice_zip_by_filename"),
        tool_name="aruba_get_received_invoice_zip_by_filename",
        params={"filename": filename},
        confirm_read=confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_sent_invoice_by_id(
    invoiceId: str,
    includePdf: bool = False,
    includeFile: bool = True,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """GET /services/invoice/out/{invoiceId}. Sensitive when file/PDF Base64 is requested."""

    return await _invoice_by_id(
        "sent_invoice_by_id",
        "aruba_get_sent_invoice_by_id",
        invoiceId,
        includePdf,
        includeFile,
        confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_received_invoice_by_id(
    invoiceId: str,
    includePdf: bool = False,
    includeFile: bool = True,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """GET /services/invoice/in/{invoiceId}. Sensitive when file/PDF Base64 is requested."""

    return await _invoice_by_id(
        "received_invoice_by_id",
        "aruba_get_received_invoice_by_id",
        invoiceId,
        includePdf,
        includeFile,
        confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_sent_invoice_by_sdi_id(
    idSdi: str,
    includePdf: bool = False,
    includeFile: bool = True,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """GET /services/invoice/out/getByIdSdi. Sensitive when file/PDF Base64 is requested."""

    return await _invoice_by_sdi(
        "sent_invoice_by_sdi_id",
        "aruba_get_sent_invoice_by_sdi_id",
        idSdi,
        includePdf,
        includeFile,
        confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_received_invoice_by_sdi_id(
    idSdi: str,
    includePdf: bool = False,
    includeFile: bool = True,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """GET /services/invoice/in/getByIdSdi. Sensitive when file/PDF Base64 is requested."""

    return await _invoice_by_sdi(
        "received_invoice_by_sdi_id",
        "aruba_get_received_invoice_by_sdi_id",
        idSdi,
        includePdf,
        includeFile,
        confirm_read,
    )


async def _pdd(
    endpoint_name: str,
    tool_name: str,
    invoiceFilename: str | None,
    invoiceId: str | None,
    confirm_read: bool,
) -> dict[str, Any]:
    _exactly_one(invoiceFilename=invoiceFilename, invoiceId=invoiceId)
    return await _execute(
        _endpoint(endpoint_name),
        tool_name=tool_name,
        params={"invoiceFilename": invoiceFilename, "invoiceId": invoiceId},
        confirm_read=confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_sent_invoice_pdd(
    invoiceFilename: str | None = None,
    invoiceId: str | None = None,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """GET /services/invoice/out/pdd. Sensitive Base64 ZIP PDD read."""

    return await _pdd(
        "sent_invoice_pdd",
        "aruba_get_sent_invoice_pdd",
        invoiceFilename,
        invoiceId,
        confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_received_invoice_pdd(
    invoiceFilename: str | None = None,
    invoiceId: str | None = None,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """GET /services/invoice/in/pdd.

    Sensitive Base64 ZIP PDD read; docs example has out/pdd typo.
    """

    return await _pdd(
        "received_invoice_pdd",
        "aruba_get_received_invoice_pdd",
        invoiceFilename,
        invoiceId,
        confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_received_invoice_unsigned_file(
    invoiceId: str | None = None,
    filename: str | None = None,
    includeFile: bool = False,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """GET /services/invoice/in/getInvoiceWithUnsignedFile. Sensitive unsignedFile Base64 read."""

    _exactly_one(invoiceId=invoiceId, filename=filename)
    return await _execute(
        _endpoint("received_invoice_unsigned_file"),
        tool_name="aruba_get_received_invoice_unsigned_file",
        params={"invoiceId": invoiceId, "filename": filename, "includeFile": includeFile},
        confirm_read=confirm_read,
        sensitive_now=True,
    )


async def _notification(
    endpoint_name: str,
    tool_name: str,
    *,
    filename: str | None = None,
    invoiceFilename: str | None = None,
    invoiceId: str | None = None,
    confirm_read: bool,
) -> dict[str, Any]:
    path_values = {"invoiceId": invoiceId} if invoiceId is not None else None
    if filename is not None:
        _require_non_empty("filename", filename)
    if invoiceFilename is not None:
        _require_non_empty("invoiceFilename", invoiceFilename)
    if invoiceId is not None:
        _require_non_empty("invoiceId", invoiceId)
    return await _execute(
        _endpoint(endpoint_name),
        tool_name=tool_name,
        path_values=path_values,
        params={"filename": filename, "invoiceFilename": invoiceFilename},
        confirm_read=confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_sent_notification_by_filename(
    filename: str, confirm_read: bool = False
) -> dict[str, Any]:
    """GET /services/notification/out/getByFilename. Sensitive notification Base64 read."""

    return await _notification(
        "sent_notification_by_filename",
        "aruba_get_sent_notification_by_filename",
        filename=filename,
        confirm_read=confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_sent_notifications_by_invoice_filename(
    invoiceFilename: str, confirm_read: bool = False
) -> dict[str, Any]:
    """GET /services/notification/out/getByInvoiceFilename. Sensitive notification Base64 read."""

    return await _notification(
        "sent_notifications_by_invoice_filename",
        "aruba_get_sent_notifications_by_invoice_filename",
        invoiceFilename=invoiceFilename,
        confirm_read=confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_sent_notifications_by_invoice_id(
    invoiceId: str, confirm_read: bool = False
) -> dict[str, Any]:
    """GET /services/notification/out/{invoiceId}. Sensitive notification Base64 read."""

    return await _notification(
        "sent_notifications_by_invoice_id",
        "aruba_get_sent_notifications_by_invoice_id",
        invoiceId=invoiceId,
        confirm_read=confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_received_notification_by_filename(
    filename: str, confirm_read: bool = False
) -> dict[str, Any]:
    """GET /services/notification/in/getByFilename. Sensitive notification Base64 read."""

    return await _notification(
        "received_notification_by_filename",
        "aruba_get_received_notification_by_filename",
        filename=filename,
        confirm_read=confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_received_notifications_by_invoice_filename(
    invoiceFilename: str, confirm_read: bool = False
) -> dict[str, Any]:
    """GET /services/notification/in/getByInvoiceFilename. Sensitive notification Base64 read."""

    return await _notification(
        "received_notifications_by_invoice_filename",
        "aruba_get_received_notifications_by_invoice_filename",
        invoiceFilename=invoiceFilename,
        confirm_read=confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_received_notifications_by_invoice_id(
    invoiceId: str, confirm_read: bool = False
) -> dict[str, Any]:
    """GET /services/notification/in/{invoiceId}. Sensitive notification Base64 read."""

    return await _notification(
        "received_notifications_by_invoice_id",
        "aruba_get_received_notifications_by_invoice_id",
        invoiceId=invoiceId,
        confirm_read=confirm_read,
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_customer_result_status(filename: str) -> dict[str, Any]:
    """GET /services/invoice/in/sendEsitoCommittente/{filename}.

    Read-only customer result status.
    """

    _require_non_empty("filename", filename)
    return await _execute(
        _endpoint("customer_result_status"),
        tool_name="aruba_get_customer_result_status",
        path_values={"filename": filename},
    )


REGISTERED_BUSINESS_TOOLS = tuple(endpoint.tool_name for endpoint in ENDPOINTS)


def main() -> None:
    """Run the FastMCP server."""

    mcp.run()
