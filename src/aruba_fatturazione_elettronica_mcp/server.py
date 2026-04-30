"""FastMCP server exposing Aruba GET endpoints as read-only tools."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

from .annotations import LOCAL_INDEX_WRITE_TOOL, READ_ONLY_TOOL
from .audit import Timer, audit_event
from .client import ArubaFatturazioneClient
from .config import Settings, get_settings
from .endpoints import ENDPOINT_BY_NAME, ENDPOINTS, Endpoint
from .errors import (
    ArubaMCPError,
    ArubaSensitiveReadConfirmationRequired,
    ArubaValidationError,
)
from .invoice_xml import (
    CODE_EXPLANATIONS,
    aggregate_vat,
    compare_invoice_totals,
    extract_requested_fields,
    parse_invoice_xml_base64,
    parse_notification_xml_base64,
    summarize_invoice,
    validate_invoice_structure,
)
from .llm_tools import (
    counterparty_match_hints,
    derive_status,
    document_lifecycle,
    document_markdown,
    duplicate_candidates,
    fetch_invoice_by_filename,
    fetch_notifications,
    filter_invoices,
    find_invoices,
    fiscal_document_risk,
    fiscal_document_summary_payload,
    fiscal_events_from_documents,
    get_full_context,
    normalize_fiscal_document_payload,
    parse_invoice_from_payload,
    parse_notifications,
    period_summary_from_documents,
    redact_invoice,
    status_report,
    table_from_rows,
    tax_summary_from_documents,
    validate_fiscal_consistency,
)
from .local_index import InvoiceIndex

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


def _directions(direction: str) -> list[str]:
    if direction == "both":
        return ["out", "in"]
    if direction in {"out", "in"}:
        return [direction]
    raise ArubaValidationError("direction must be one of: in, out, both.")


async def _collect_invoices(
    direction: str,
    date_from: str | None,
    date_to: str | None,
    *,
    limit: int = 100,
    vat_code: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    client = get_client()
    remaining = max(limit, 1)
    result: dict[str, list[dict[str, Any]]] = {}
    for item_direction in _directions(direction):
        if remaining <= 0:
            break
        invoices = await find_invoices(
            client,
            item_direction,
            date_from=date_from,
            date_to=date_to,
            size=min(remaining, 100),
            vat_code=vat_code,
        )
        result[item_direction] = invoices[:remaining]
        remaining -= len(result[item_direction])
    return result


def _flatten_by_direction(
    invoices_by_direction: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows = []
    for direction, invoices in invoices_by_direction.items():
        for invoice in invoices:
            row = {"direction": direction, **invoice}
            rows.append(row)
    return rows


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_invoice_full_context(
    direction: str,
    filename: str,
    include_file: bool = True,
    include_pdf: bool = False,
    include_notifications: bool = True,
    include_pdd: bool = False,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """LLM-friendly full invoice context from read-only Aruba GET endpoints.

    Aggregates invoice payload, parsed XML, notifications, timeline and optional PDD.
    Sensitive when XML/PDF/notifications/PDD are requested.
    """

    _require_non_empty("filename", filename)
    if direction not in {"in", "out"}:
        return ArubaValidationError("direction must be in or out.").to_dict()
    try:
        return await get_full_context(
            get_client(),
            direction=direction,
            filename=filename,
            include_file=include_file,
            include_pdf=include_pdf,
            include_notifications=include_notifications,
            include_pdd=include_pdd,
            confirm_read=confirm_read,
        )
    except ArubaMCPError as exc:
        return exc.to_dict()


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_search_invoices(
    direction: str = "both",
    date_from: str | None = None,
    date_to: str | None = None,
    party_name_contains: str | None = None,
    vat_code: str | None = None,
    invoice_number: str | None = None,
    status: str | None = None,
    min_total: float | None = None,
    max_total: float | None = None,
    text_contains: str | None = None,
    include_parsed_xml: bool = False,
    limit: int = 100,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Conceptual invoice search over read-only Aruba invoice lists."""

    try:
        _validate_iso8601("date_from", date_from)
        _validate_iso8601("date_to", date_to)
        invoices_by_direction = await _collect_invoices(
            direction, date_from, date_to, limit=limit, vat_code=vat_code
        )
        rows = filter_invoices(
            _flatten_by_direction(invoices_by_direction),
            party_name_contains=party_name_contains,
            invoice_number=invoice_number,
            status=status,
            min_total=min_total,
            max_total=max_total,
            text_contains=text_contains,
        )[:limit]
        if include_parsed_xml:
            if get_client().settings.confirm_sensitive_reads and not confirm_read:
                raise ArubaSensitiveReadConfirmationRequired()
            for row in rows:
                parsed = parse_invoice_from_payload(row)
                if parsed:
                    row["parsed_xml"] = parsed
        return {"ok": True, "count": len(rows), "invoices": rows}
    except ArubaMCPError as exc:
        return exc.to_dict()


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_summarize_invoice(
    direction: str,
    filename: str,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Fetch and summarize one invoice in a compact structured form."""

    context = cast(
        dict[str, Any],
        await aruba_get_invoice_full_context(
            direction,
            filename,
            include_file=True,
            include_pdf=False,
            include_notifications=False,
            include_pdd=False,
            confirm_read=confirm_read,
        ),
    )
    if not context.get("ok"):
        return context
    parsed = cast(dict[str, Any] | None, context.get("parsed_xml"))
    if not parsed:
        return {"ok": False, "error": "invoice_xml_not_found", "message": "No parseable XML found."}
    return {"ok": True, "summary": summarize_invoice(parsed)}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_extract_invoice_fields(
    direction: str,
    filename: str,
    fields: list[str],
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Extract precise fields from a FatturaPA XML returned by Aruba."""

    summary = cast(
        dict[str, Any],
        await aruba_summarize_invoice(direction, filename, confirm_read=confirm_read),
    )
    if not summary.get("ok"):
        return summary
    context = cast(
        dict[str, Any],
        await aruba_get_invoice_full_context(direction, filename, confirm_read=confirm_read),
    )
    parsed = cast(dict[str, Any], context.get("parsed_xml") or {})
    return {"ok": True, "fields": extract_requested_fields(parsed, fields)}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_invoice_timeline(
    direction: str,
    filename: str,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Build a timeline from invoice metadata and SDI notifications."""

    context = cast(
        dict[str, Any],
        await aruba_get_invoice_full_context(direction, filename, confirm_read=confirm_read),
    )
    if not context.get("ok"):
        return context
    return {
        "ok": True,
        "filename": filename,
        "timeline": context.get("timeline", []),
        "current_status": context.get("status_summary"),
        "final": context.get("status_summary")
        in {"delivered", "delivered_or_processed", "rejected"},
    }


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_explain_sdi_notifications(
    direction: str,
    invoice_filename: str,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Translate SDI notifications into human-oriented status information."""

    try:
        if get_client().settings.confirm_sensitive_reads and not confirm_read:
            raise ArubaSensitiveReadConfirmationRequired()
        if direction not in {"in", "out"}:
            raise ArubaValidationError("direction must be in or out.")
        notifications = await fetch_notifications(get_client(), direction, invoice_filename)
        parsed = parse_notifications(notifications)
        status = derive_status([item.get("human_status", "unknown") for item in parsed])
        return {
            "ok": True,
            "notifications": parsed,
            "human_explanation": f"The current interpreted SDI status is {status}.",
            "status": status,
            "next_action_hint": (
                "Informational only: review Aruba/SDI details before taking any external action."
            ),
        }
    except ArubaMCPError as exc:
        return exc.to_dict()


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_find_missing_notifications(
    direction: str = "out",
    date_from: str | None = None,
    date_to: str | None = None,
    expected_final_status: bool = True,
    limit: int = 100,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Find invoices that appear to be missing expected final SDI notifications."""

    try:
        if get_client().settings.confirm_sensitive_reads and not confirm_read:
            raise ArubaSensitiveReadConfirmationRequired()
        invoices_by_direction = await _collect_invoices(direction, date_from, date_to, limit=limit)
        missing = []
        for row in _flatten_by_direction(invoices_by_direction):
            filename = row.get("filename") or row.get("fileName") or row.get("invoiceFilename")
            if not filename:
                continue
            notifications = parse_notifications(
                await fetch_notifications(get_client(), row["direction"], str(filename))
            )
            status = derive_status([item.get("human_status", "unknown") for item in notifications])
            if not notifications or (
                expected_final_status and status not in {"delivered", "rejected"}
            ):
                missing.append(
                    {"invoice": row, "status": status, "notification_count": len(notifications)}
                )
        return {"ok": True, "missing_or_incomplete": missing}
    except ArubaMCPError as exc:
        return exc.to_dict()


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_invoice_status_report(
    date_from: str | None = None,
    date_to: str | None = None,
    direction: str = "both",
    limit: int = 200,
) -> dict[str, Any]:
    """Aggregate invoice status counts for a period."""

    try:
        invoices = await _collect_invoices(direction, date_from, date_to, limit=limit)
        return {"ok": True, "period": {"from": date_from, "to": date_to}, **status_report(invoices)}
    except ArubaMCPError as exc:
        return exc.to_dict()


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_vat_summary(
    direction: str = "both",
    date_from: str | None = None,
    date_to: str | None = None,
    group_by: str = "vat_rate",
    limit: int = 100,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Summarize VAT totals from parseable invoice XML."""

    _ = group_by
    parsed = await _parsed_invoices_for_period(direction, date_from, date_to, limit, confirm_read)
    return {"ok": True, **aggregate_vat(parsed)}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_counterparty_report(
    direction: str = "in",
    date_from: str | None = None,
    date_to: str | None = None,
    group_by: str = "vat_code",
    limit: int = 100,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Aggregate invoices by supplier or customer."""

    parsed = await _parsed_invoices_for_period(direction, date_from, date_to, limit, confirm_read)
    groups: dict[str, dict[str, Any]] = {}
    for item in parsed:
        party_value = item.get("supplier") if direction == "in" else item.get("customer")
        party = party_value if isinstance(party_value, dict) else {}
        key = party.get("vat", {}).get("code") if group_by == "vat_code" else party.get("name")
        key = key or "unknown"
        entry = groups.setdefault(
            key,
            {
                "name": party.get("name"),
                "vat_code": party.get("vat", {}).get("code"),
                "invoice_count": 0,
                "net_total": Decimal("0"),
                "vat_total": Decimal("0"),
                "gross_total": Decimal("0"),
                "first_invoice_date": None,
                "last_invoice_date": None,
            },
        )
        entry["invoice_count"] += 1
        for source, target in (
            ("net", "net_total"),
            ("vat", "vat_total"),
            ("gross", "gross_total"),
        ):
            if item.get("amounts", {}).get(source):
                entry[target] += Decimal(str(item["amounts"][source]))
        date = item.get("dates", {}).get("invoice_date")
        if date:
            entry["first_invoice_date"] = min(filter(None, [entry["first_invoice_date"], date]))
            entry["last_invoice_date"] = max(filter(None, [entry["last_invoice_date"], date]))
    for entry in groups.values():
        for key in ("net_total", "vat_total", "gross_total"):
            entry[key] = str(entry[key].quantize(Decimal("0.01")))
    return {"ok": True, "counterparties": list(groups.values())}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_detect_invoice_anomalies(
    direction: str = "both",
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Detect data-quality anomalies without providing definitive tax advice."""

    parsed = await _parsed_invoices_for_period(direction, date_from, date_to, limit, confirm_read)
    anomalies = []
    for item in parsed:
        for warning in validate_invoice_structure(item)["warnings"]:
            anomalies.append(
                {
                    "severity": "medium",
                    "type": "structure_warning",
                    "invoice": item.get("document"),
                    "explanation": warning,
                }
            )
        comparison = compare_invoice_totals(item)
        if not comparison["consistent"]:
            anomalies.append(
                {
                    "severity": "high",
                    "type": "total_mismatch",
                    "invoice": item.get("document"),
                    "explanation": comparison["warnings"],
                }
            )
    duplicates = duplicate_candidates([{"parsed": item} for item in parsed])
    for duplicate in duplicates:
        anomalies.append(
            {
                "severity": "medium",
                "type": "possible_duplicate",
                "invoice": duplicate,
                "explanation": "Same supplier VAT, number, date and gross total.",
            }
        )
    return {"ok": True, "anomalies": anomalies}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_find_duplicate_invoices(
    direction: str = "both",
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 200,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Find possible duplicate invoices."""

    parsed = await _parsed_invoices_for_period(direction, date_from, date_to, limit, confirm_read)
    return {"ok": True, "duplicates": duplicate_candidates([{"parsed": item} for item in parsed])}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_reconcile_sent_received(
    date_from: str | None = None,
    date_to: str | None = None,
    group_by: str = "month",
    limit: int = 200,
) -> dict[str, Any]:
    """Read-only reconciliation summary between sent and received Aruba invoices."""

    invoices = await _collect_invoices("both", date_from, date_to, limit=limit)
    return {
        "ok": True,
        "group_by": group_by,
        "sent_count": len(invoices.get("out", [])),
        "received_count": len(invoices.get("in", [])),
        "sent": invoices.get("out", []),
        "received": invoices.get("in", []),
    }


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_export_invoices_table(
    direction: str = "both",
    date_from: str | None = None,
    date_to: str | None = None,
    format: str = "json_table",
    columns: list[str] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Return a clean JSON table for invoices."""

    if format != "json_table":
        return ArubaValidationError("Only json_table is supported.").to_dict()
    columns = columns or ["direction", "filename", "date", "status"]
    rows = _flatten_by_direction(
        await _collect_invoices(direction, date_from, date_to, limit=limit)
    )
    return {"ok": True, **table_from_rows(rows, columns)}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_answer_invoice_question(
    question: str,
    date_from: str | None = None,
    date_to: str | None = None,
    direction: str = "both",
    limit: int = 100,
) -> dict[str, Any]:
    """Collect structured context for an LLM to answer a natural-language invoice question."""

    _require_non_empty("question", question)
    invoices = await _collect_invoices(direction, date_from, date_to, limit=limit)
    return {
        "ok": True,
        "interpreted_query": {
            "question": question,
            "direction": direction,
            "date_from": date_from,
            "date_to": date_to,
        },
        "data": _flatten_by_direction(invoices),
        "suggested_answer_context": status_report(invoices),
    }


@mcp.tool(annotations=LOCAL_INDEX_WRITE_TOOL)
async def aruba_sync_invoice_index(
    direction: str = "both",
    date_from: str | None = None,
    date_to: str | None = None,
    include_xml: bool = True,
    include_notifications: bool = False,
    confirm_read: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    """Build/update a local SQLite index from read-only Aruba GET data.

    This writes only to ARUBA_INDEX_DB_PATH and never modifies Aruba.
    """

    if (
        get_client().settings.confirm_sensitive_reads
        and (include_xml or include_notifications)
        and not confirm_read
    ):
        return ArubaSensitiveReadConfirmationRequired().to_dict()
    index = InvoiceIndex(get_client().settings.index_db_path)
    invoices = _flatten_by_direction(
        await _collect_invoices(direction, date_from, date_to, limit=limit)
    )
    indexed = 0
    for row in invoices:
        filename = row.get("filename") or row.get("fileName") or row.get("invoiceFilename")
        payload = row
        if include_xml and filename:
            payload = await fetch_invoice_by_filename(
                get_client(),
                row["direction"],
                str(filename),
                include_file=True,
                include_pdf=False,
            )
        parsed = parse_invoice_from_payload(payload) if include_xml else None
        index.upsert_invoice(
            direction=row["direction"],
            filename=str(filename or indexed),
            raw=payload,
            parsed=parsed,
            status=derive_status([json.dumps(row, default=str)]),
        )
        indexed += 1
    return {"ok": True, "indexed_invoices": indexed, "period": {"from": date_from, "to": date_to}}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_search_invoice_index(
    text: str | None = None,
    direction: str | None = None,
    vat_code: str | None = None,
    party_name_contains: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Search the local SQLite invoice index."""

    rows = InvoiceIndex(get_client().settings.index_db_path).search(
        {
            "text": text,
            "direction": direction,
            "vat_code": vat_code,
            "party_name_contains": party_name_contains,
        },
        limit=limit,
    )
    return {"ok": True, "count": len(rows), "rows": rows}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_index_stats() -> dict[str, Any]:
    """Return local invoice index statistics."""

    return {"ok": True, **InvoiceIndex(get_client().settings.index_db_path).stats()}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_parse_invoice_xml(
    xml_base64: str, redact_sensitive: bool = False
) -> dict[str, Any]:
    """Parse Base64 FatturaPA XML into structured fields."""

    try:
        return {
            "ok": True,
            "parsed": parse_invoice_xml_base64(xml_base64, redact_sensitive=redact_sensitive),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "invoice_xml_parse_error", "message": str(exc)}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_parse_notification_xml(xml_base64: str) -> dict[str, Any]:
    """Parse Base64 SDI notification XML into structured fields."""

    try:
        return {"ok": True, "parsed": parse_notification_xml_base64(xml_base64)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "notification_xml_parse_error", "message": str(exc)}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_explain_invoice_type(code_type: str, code: str) -> dict[str, Any]:
    """Explain common FatturaPA codes such as TD01, N2.2, MP05 or RF19."""

    explanation = CODE_EXPLANATIONS.get(code_type, {}).get(code)
    if not explanation:
        return {
            "ok": True,
            "code": code,
            "label": None,
            "description": "Unknown code.",
            "category": code_type,
        }
    return {"ok": True, "code": code, **explanation}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_monthly_summary(
    year: int, month: int, direction: str = "both", limit: int = 200
) -> dict[str, Any]:
    """Monthly invoice status summary."""

    date_from = f"{year:04d}-{month:02d}-01"
    date_to = f"{year + (month // 12):04d}-{(month % 12) + 1:02d}-01"
    return cast(
        dict[str, Any],
        await aruba_invoice_status_report(
            date_from=date_from, date_to=date_to, direction=direction, limit=limit
        ),
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_yearly_summary(
    year: int, direction: str = "both", limit: int = 1000
) -> dict[str, Any]:
    """Yearly invoice status summary."""

    return cast(
        dict[str, Any],
        await aruba_invoice_status_report(
            date_from=f"{year:04d}-01-01",
            date_to=f"{year + 1:04d}-01-01",
            direction=direction,
            limit=limit,
        ),
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_tax_breakdown_report(
    direction: str = "both",
    date_from: str | None = None,
    date_to: str | None = None,
    group_by: str = "vat_rate",
    limit: int = 100,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Tax breakdown report grouped by VAT rate/nature."""

    return cast(
        dict[str, Any],
        await aruba_vat_summary(direction, date_from, date_to, group_by, limit, confirm_read),
    )


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_payment_terms_report(
    direction: str = "both",
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Extract payment terms from invoice XML."""

    parsed = await _parsed_invoices_for_period(direction, date_from, date_to, limit, confirm_read)
    return {
        "ok": True,
        "payments": [
            {"document": item.get("document"), "payments": item.get("payments")} for item in parsed
        ],
    }


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_validate_invoice_xml_structure(xml_base64: str) -> dict[str, Any]:
    """Run non-official sanity checks on a Base64 FatturaPA XML."""

    parsed = parse_invoice_xml_base64(xml_base64)
    return {"ok": True, **validate_invoice_structure(parsed)}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_compare_invoice_totals(xml_base64: str) -> dict[str, Any]:
    """Compare document total with line and VAT totals."""

    parsed = parse_invoice_xml_base64(xml_base64)
    return {"ok": True, **compare_invoice_totals(parsed)}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_find_invoices_without_pdf_or_xml(
    direction: str = "both",
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Find invoice list entries that appear to lack PDF/XML references."""

    rows = _flatten_by_direction(
        await _collect_invoices(direction, date_from, date_to, limit=limit)
    )
    missing = [
        row
        for row in rows
        if "pdf" not in json.dumps(row, default=str).lower()
        or "xml" not in json.dumps(row, default=str).lower()
    ]
    return {"ok": True, "invoices": missing}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_find_invoices_without_pdd(
    direction: str = "both",
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Find invoice list entries that appear to lack PDD references."""

    rows = _flatten_by_direction(
        await _collect_invoices(direction, date_from, date_to, limit=limit)
    )
    missing = [row for row in rows if "pdd" not in json.dumps(row, default=str).lower()]
    return {"ok": True, "invoices": missing}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_redact_invoice(
    invoice: dict[str, Any], redaction_level: str = "standard"
) -> dict[str, Any]:
    """Return a redacted invoice payload for safer LLM analysis."""

    if redaction_level not in {"light", "standard", "strict"}:
        return ArubaValidationError("redaction_level must be light, standard or strict.").to_dict()
    return {"ok": True, "invoice": redact_invoice(invoice, redaction_level)}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def aruba_get_safe_invoice_summary(
    direction: str,
    filename: str,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Summarize one invoice with sensitive values redacted."""

    summary = cast(
        dict[str, Any],
        await aruba_summarize_invoice(direction, filename, confirm_read=confirm_read),
    )
    if not summary.get("ok"):
        return summary
    return {"ok": True, "summary": redact_invoice(summary["summary"], "standard")}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def normalize_fiscal_document(
    documentId: str,
    direction: str,
    documentType: str | None = None,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Normalize one Aruba fiscal document into a stable JSON shape.

    This is read-only and uses existing Aruba invoice download wrappers.
    """

    try:
        payload, parsed = await _load_document_for_helper(documentId, direction, confirm_read)
        return {
            "ok": True,
            "document": normalize_fiscal_document_payload(
                document_id=documentId,
                direction=_fiscal_direction(direction),
                document_type=documentType,
                raw_document=payload,
                parsed_xml=parsed,
            ),
        }
    except ArubaMCPError as exc:
        return exc.to_dict()


@mcp.tool(annotations=READ_ONLY_TOOL)
async def get_document_context(
    documentId: str,
    direction: str,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Return complete read-only context for one Aruba fiscal document."""

    try:
        payload, parsed = await _load_document_for_helper(documentId, direction, confirm_read)
        api_direction = _api_direction(direction)
        normalized = normalize_fiscal_document_payload(
            document_id=documentId,
            direction=_fiscal_direction(direction),
            document_type=None,
            raw_document=payload,
            parsed_xml=parsed,
        )
        notifications = await _safe_notifications(api_direction, documentId, confirm_read)
        lifecycle = document_lifecycle(normalized, notifications)
        files = _downloadable_files(payload)
        return {
            "ok": True,
            "document": payload,
            "normalized": normalized,
            "lifecycleStatus": lifecycle,
            "notifications": notifications,
            "pddAvailable": _has_pdd_reference(payload),
            "downloadableFiles": files,
            "relatedDocuments": _related_documents(payload),
            "warnings": normalized.get("warnings", []),
        }
    except ArubaMCPError as exc:
        return exc.to_dict()


@mcp.tool(annotations=READ_ONLY_TOOL)
async def fiscal_document_summary(
    documentId: str,
    direction: str,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Return a compact LLM-friendly summary for one fiscal document."""

    result = cast(
        dict[str, Any],
        await normalize_fiscal_document(documentId, direction, confirm_read=confirm_read),
    )
    if not result.get("ok"):
        return result
    return {"ok": True, **fiscal_document_summary_payload(result["document"])}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def document_lifecycle_status(
    documentId: str,
    direction: str,
    asOfDate: str | None = None,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Explain one document's fiscal/SDI lifecycle status."""

    try:
        _validate_iso8601("asOfDate", asOfDate)
        context = cast(
            dict[str, Any],
            await get_document_context(documentId, direction, confirm_read=confirm_read),
        )
        if not context.get("ok"):
            return context
        status = cast(dict[str, Any], context["lifecycleStatus"])
        status["asOfDate"] = asOfDate
        return {"ok": True, **status}
    except ArubaMCPError as exc:
        return exc.to_dict()


@mcp.tool(annotations=READ_ONLY_TOOL)
async def document_risk_check(
    documentId: str,
    direction: str,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Evaluate fiscal/technical document risks without giving legal or tax advice."""

    context = cast(
        dict[str, Any], await get_document_context(documentId, direction, confirm_read=confirm_read)
    )
    if not context.get("ok"):
        return context
    return {
        "ok": True,
        **fiscal_document_risk(
            context["normalized"],
            context["lifecycleStatus"],
            pdd_available=bool(context.get("pddAvailable")),
            downloadable_files=cast(list[dict[str, Any]], context.get("downloadableFiles") or []),
        ),
    }


@mcp.tool(annotations=READ_ONLY_TOOL)
async def validate_fiscal_document_consistency(
    documentId: str,
    direction: str,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Run technical consistency checks on one normalized fiscal document."""

    result = cast(
        dict[str, Any],
        await normalize_fiscal_document(documentId, direction, confirm_read=confirm_read),
    )
    if not result.get("ok"):
        return result
    return {"ok": True, **validate_fiscal_consistency(result["document"])}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def counterparty_document_history(
    counterparty: dict[str, Any],
    fromDate: str | None = None,
    toDate: str | None = None,
    direction: str = "all",
    limit: int = 200,
) -> dict[str, Any]:
    """Aggregate Aruba document history for a generic counterparty."""

    try:
        documents = await _normalized_documents_for_period(direction, fromDate, toDate, limit)
        matched = [item for item in documents if _matches_counterparty(item, counterparty)]
        statuses = Counter(
            str(item.get("status") or item.get("sdiStatus") or "unknown") for item in matched
        )
        outbound = [item for item in matched if item.get("direction") == "outbound"]
        inbound = [item for item in matched if item.get("direction") == "inbound"]
        problematic = [item for item in matched if document_lifecycle(item, [])["isProblematic"]]
        dates = [str(item.get("issueDate")) for item in matched if item.get("issueDate")]
        return {
            "ok": True,
            "counterparty": counterparty,
            "documentsCount": len(matched),
            "outboundCount": len(outbound),
            "inboundCount": len(inbound),
            "totalOutboundAmount": _sum_document_amounts(outbound),
            "totalInboundAmount": _sum_document_amounts(inbound),
            "rejectedCount": sum(1 for item in problematic if "reject" in json.dumps(item).lower()),
            "problematicCount": len(problematic),
            "lastDocumentDate": max(dates) if dates else None,
            "commonStatuses": [
                {"status": status, "count": count} for status, count in statuses.most_common()
            ],
            "warnings": [] if matched else ["No matching documents were found."],
        }
    except ArubaMCPError as exc:
        return exc.to_dict()


@mcp.tool(annotations=READ_ONLY_TOOL)
async def list_pending_or_problem_documents(
    fromDate: str | None = None,
    toDate: str | None = None,
    direction: str = "all",
    includeProblemOnly: bool = True,
    limit: int = 200,
) -> dict[str, Any]:
    """List fiscal documents that appear to need attention."""

    try:
        documents = await _normalized_documents_for_period(direction, fromDate, toDate, limit)
        rows = []
        for document in documents:
            lifecycle = document_lifecycle(document, [])
            risk = fiscal_document_risk(
                document,
                lifecycle,
                pdd_available=_has_pdd_reference(document.get("rawRefs", {}).get("aruba", {})),
                downloadable_files=_downloadable_files(
                    document.get("rawRefs", {}).get("aruba", {})
                ),
            )
            if includeProblemOnly and risk["riskLevel"] == "low":
                continue
            rows.append(
                {
                    "documentId": document.get("documentId"),
                    "direction": document.get("direction"),
                    "number": document.get("number"),
                    "counterpartyName": document.get("counterparty", {}).get("name"),
                    "issueDate": document.get("issueDate"),
                    "status": document.get("status"),
                    "sdiStatus": document.get("sdiStatus"),
                    "problemReason": lifecycle.get("problemReason") or "; ".join(risk["warnings"]),
                    "riskLevel": risk["riskLevel"],
                }
            )
        return {"ok": True, "count": len(rows), "documents": rows, "warnings": []}
    except ArubaMCPError as exc:
        return exc.to_dict()


@mcp.tool(annotations=READ_ONLY_TOOL)
async def fiscal_period_summary(
    fromDate: str | None = None,
    toDate: str | None = None,
    direction: str = "all",
    limit: int = 200,
) -> dict[str, Any]:
    """Return an informational fiscal document summary for a period."""

    try:
        documents = await _normalized_documents_for_period(direction, fromDate, toDate, limit)
        return {"ok": True, **period_summary_from_documents(documents, fromDate, toDate)}
    except ArubaMCPError as exc:
        return exc.to_dict()


@mcp.tool(annotations=READ_ONLY_TOOL)
async def tax_summary(
    fromDate: str | None = None,
    toDate: str | None = None,
    direction: str = "all",
    limit: int = 200,
) -> dict[str, Any]:
    """Return an informational tax summary for a period; not accounting advice."""

    try:
        documents = await _normalized_documents_for_period(direction, fromDate, toDate, limit)
        return {"ok": True, **tax_summary_from_documents(documents, fromDate, toDate)}
    except ArubaMCPError as exc:
        return exc.to_dict()


@mcp.tool(annotations=READ_ONLY_TOOL)
async def export_fiscal_events(
    fromDate: str | None = None,
    toDate: str | None = None,
    direction: str = "all",
    eventTypes: list[str] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Export standardized generic fiscal document events."""

    try:
        documents = await _normalized_documents_for_period(direction, fromDate, toDate, limit)
        return {"ok": True, "events": fiscal_events_from_documents(documents, eventTypes)}
    except ArubaMCPError as exc:
        return exc.to_dict()


@mcp.tool(annotations=READ_ONLY_TOOL)
async def export_document_markdown(
    documentId: str,
    direction: str,
    includeLineItems: bool = True,
    includeNotifications: bool = True,
    includeRawRefs: bool = False,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Export one fiscal document as generic Markdown."""

    context = cast(
        dict[str, Any], await get_document_context(documentId, direction, confirm_read=confirm_read)
    )
    if not context.get("ok"):
        return context
    return {
        "ok": True,
        **document_markdown(
            context["normalized"],
            cast(list[dict[str, Any]], context.get("notifications") or []),
            include_line_items=includeLineItems,
            include_notifications=includeNotifications,
            include_raw_refs=includeRawRefs,
        ),
    }


@mcp.tool(annotations=READ_ONLY_TOOL)
async def export_period_markdown(
    fromDate: str | None = None,
    toDate: str | None = None,
    direction: str = "all",
    includeTaxSummary: bool = True,
    includeProblemDocuments: bool = True,
    limit: int = 200,
) -> dict[str, Any]:
    """Export a generic period report as Markdown."""

    summary = cast(
        dict[str, Any],
        await fiscal_period_summary(fromDate, toDate, direction=direction, limit=limit),
    )
    if not summary.get("ok"):
        return summary
    lines = [f"# Fiscal period {fromDate or 'start'} to {toDate or 'end'}", ""]
    lines.append(f"- Outbound documents: {summary['outbound']['count']}")
    lines.append(f"- Inbound documents: {summary['inbound']['count']}")
    lines.append(f"- Problematic documents: {summary['problematicCount']}")
    if includeTaxSummary:
        taxes = await tax_summary(fromDate, toDate, direction=direction, limit=limit)
        lines.extend(
            ["", "## Tax summary", "```json", json.dumps(taxes, default=str, indent=2), "```"]
        )
    if includeProblemDocuments:
        problems = await list_pending_or_problem_documents(
            fromDate, toDate, direction=direction, limit=limit
        )
        lines.extend(
            [
                "",
                "## Documents needing attention",
                "```json",
                json.dumps(problems, default=str, indent=2),
                "```",
            ]
        )
    return {
        "ok": True,
        "title": f"Fiscal period {fromDate or 'start'} to {toDate or 'end'}",
        "markdown": "\n".join(lines),
        "metadata": {"fromDate": fromDate, "toDate": toDate, "direction": direction},
    }


@mcp.tool(annotations=READ_ONLY_TOOL)
async def export_counterparty_markdown(
    counterparty: dict[str, Any],
    fromDate: str | None = None,
    toDate: str | None = None,
    direction: str = "all",
    limit: int = 200,
) -> dict[str, Any]:
    """Export a generic counterparty fiscal document report as Markdown."""

    history = cast(
        dict[str, Any],
        await counterparty_document_history(counterparty, fromDate, toDate, direction, limit),
    )
    if not history.get("ok"):
        return history
    title = f"Counterparty document history - {counterparty.get('name') or 'unknown'}"
    lines = [
        f"# {title}",
        "",
        f"- Documents: {history['documentsCount']}",
        f"- Outbound amount: {history['totalOutboundAmount']}",
        f"- Inbound amount: {history['totalInboundAmount']}",
        f"- Problematic documents: {history['problematicCount']}",
    ]
    return {"ok": True, "title": title, "markdown": "\n".join(lines), "metadata": history}


@mcp.tool(annotations=READ_ONLY_TOOL)
async def prepare_document_match_hints(
    documentId: str,
    direction: str,
    confirm_read: bool = False,
) -> dict[str, Any]:
    """Return generic document and counterparty matching hints for external consumers."""

    result = cast(
        dict[str, Any],
        await normalize_fiscal_document(documentId, direction, confirm_read=confirm_read),
    )
    if not result.get("ok"):
        return result
    return {"ok": True, **counterparty_match_hints(result["document"])}


async def _parsed_invoices_for_period(
    direction: str,
    date_from: str | None,
    date_to: str | None,
    limit: int,
    confirm_read: bool,
) -> list[dict[str, Any]]:
    if get_client().settings.confirm_sensitive_reads and not confirm_read:
        raise ArubaSensitiveReadConfirmationRequired()
    rows = _flatten_by_direction(
        await _collect_invoices(direction, date_from, date_to, limit=limit)
    )
    parsed = []
    for row in rows:
        filename = row.get("filename") or row.get("fileName") or row.get("invoiceFilename")
        payload = row
        if filename:
            try:
                payload = await fetch_invoice_by_filename(
                    get_client(),
                    row["direction"],
                    str(filename),
                    include_file=True,
                    include_pdf=False,
                )
            except ArubaMCPError:
                payload = row
        parsed_invoice = parse_invoice_from_payload(payload)
        if parsed_invoice:
            parsed.append(parsed_invoice)
    return parsed


def _api_direction(direction: str) -> str:
    if direction in {"out", "outbound"}:
        return "out"
    if direction in {"in", "inbound"}:
        return "in"
    raise ArubaValidationError("direction must be outbound or inbound.")


def _fiscal_direction(direction: str) -> str:
    if direction in {"out", "outbound"}:
        return "outbound"
    if direction in {"in", "inbound"}:
        return "inbound"
    raise ArubaValidationError("direction must be outbound or inbound.")


def _period_direction(direction: str) -> str:
    if direction in {"all", "both"}:
        return "both"
    if direction in {"out", "outbound"}:
        return "out"
    if direction in {"in", "inbound"}:
        return "in"
    raise ArubaValidationError("direction must be outbound, inbound or all.")


async def _load_document_for_helper(
    document_id: str, direction: str, confirm_read: bool
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    _require_non_empty("documentId", document_id)
    api_direction = _api_direction(direction)
    if get_client().settings.confirm_sensitive_reads and not confirm_read:
        raise ArubaSensitiveReadConfirmationRequired()
    payload = await _fetch_document_by_id_or_filename(api_direction, document_id)
    return payload, parse_invoice_from_payload(payload)


async def _fetch_document_by_id_or_filename(api_direction: str, document_id: str) -> dict[str, Any]:
    client = get_client()
    bucket = "find_sent" if api_direction == "out" else "find_received"
    if _looks_like_filename(document_id):
        return await fetch_invoice_by_filename(
            client,
            api_direction,
            document_id,
            include_file=True,
            include_pdf=False,
        )
    data = await client.ws_get(
        f"/services/invoice/{api_direction}/{document_id}",
        params={"includeFile": True, "includePdf": False},
        bucket=bucket,
    )
    return data if isinstance(data, dict) else {"data": data}


def _looks_like_filename(value: str) -> bool:
    lowered = value.lower()
    return lowered.endswith((".xml", ".p7m", ".zip", ".pdf")) or "/" in lowered


async def _safe_notifications(
    api_direction: str, document_id: str, confirm_read: bool
) -> list[dict[str, Any]]:
    if get_client().settings.confirm_sensitive_reads and not confirm_read:
        return []
    try:
        return parse_notifications(
            await fetch_notifications(get_client(), api_direction, document_id)
        )
    except ArubaMCPError:
        return []


def _downloadable_files(payload: dict[str, Any]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    text = json.dumps(payload, default=str).lower()
    for label in ("xml", "pdf", "zip", "pdd"):
        if label in text:
            files.append({"type": label, "available": True})
    return files


def _has_pdd_reference(payload: dict[str, Any]) -> bool:
    return "pdd" in json.dumps(payload, default=str).lower()


def _related_documents(payload: dict[str, Any]) -> list[dict[str, Any]]:
    related: list[dict[str, Any]] = []
    for key in ("relatedDocuments", "linkedDocuments", "documents"):
        value = payload.get(key)
        if isinstance(value, list):
            related.extend(item if isinstance(item, dict) else {"value": item} for item in value)
    return related


async def _normalized_documents_for_period(
    direction: str, from_date: str | None, to_date: str | None, limit: int
) -> list[dict[str, Any]]:
    _validate_iso8601("fromDate", from_date)
    _validate_iso8601("toDate", to_date)
    rows = _flatten_by_direction(
        await _collect_invoices(_period_direction(direction), from_date, to_date, limit=limit)
    )
    documents = []
    for index, row in enumerate(rows):
        api_direction = row.pop("direction")
        document_id = str(
            row.get("id")
            or row.get("invoiceId")
            or row.get("filename")
            or row.get("fileName")
            or index
        )
        parsed = parse_invoice_from_payload(row)
        documents.append(
            normalize_fiscal_document_payload(
                document_id=document_id,
                direction="outbound" if api_direction == "out" else "inbound",
                document_type=None,
                raw_document=row,
                parsed_xml=parsed,
            )
        )
    return documents


def _matches_counterparty(document: dict[str, Any], counterparty: dict[str, Any]) -> bool:
    party = document.get("counterparty", {})
    if not counterparty:
        return True
    for key in ("vatId", "fiscalCode", "email", "pec", "sdiCode"):
        expected = counterparty.get(key)
        if expected and str(party.get(key) or "").lower() == str(expected).lower():
            return True
    expected_name = counterparty.get("name")
    if expected_name and str(expected_name).lower() in str(party.get("name") or "").lower():
        return True
    return False


def _sum_document_amounts(documents: list[dict[str, Any]]) -> str:
    total = Decimal("0")
    for document in documents:
        value = document.get("amounts", {}).get("totalAmount")
        if value is not None:
            total += Decimal(str(value))
    return str(total.quantize(Decimal("0.01")))


REGISTERED_BUSINESS_TOOLS = tuple(endpoint.tool_name for endpoint in ENDPOINTS)


def main() -> None:
    """Run the FastMCP server."""

    mcp.run()
