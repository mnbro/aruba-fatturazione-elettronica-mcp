"""LLM-friendly read-only tools built on top of Aruba GET endpoints."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any, cast

from .client import ArubaFatturazioneClient
from .errors import ArubaSensitiveReadConfirmationRequired
from .invoice_xml import (
    find_xml_base64,
    parse_invoice_xml_base64,
    parse_notification_xml_base64,
)
from .redaction import redact


def ensure_confirm(client: ArubaFatturazioneClient, sensitive: bool, confirm_read: bool) -> None:
    if client.settings.confirm_sensitive_reads and sensitive and not confirm_read:
        raise ArubaSensitiveReadConfirmationRequired()


async def fetch_invoice_by_filename(
    client: ArubaFatturazioneClient,
    direction: str,
    filename: str,
    *,
    include_file: bool,
    include_pdf: bool,
) -> dict[str, Any]:
    path = f"/services/invoice/{direction}/getByFilename"
    data = await client.ws_get(
        path,
        params={"filename": filename, "includeFile": include_file, "includePdf": include_pdf},
        bucket="find_sent" if direction == "out" else "find_received",
    )
    return data if isinstance(data, dict) else {"data": data}


async def fetch_notifications(
    client: ArubaFatturazioneClient,
    direction: str,
    invoice_filename: str,
) -> list[dict[str, Any]]:
    data = await client.ws_get(
        f"/services/notification/{direction}/getByInvoiceFilename",
        params={"invoiceFilename": invoice_filename},
        bucket="notification_sent" if direction == "out" else "notification_received",
    )
    if isinstance(data, list):
        return [item if isinstance(item, dict) else {"data": item} for item in data]
    if isinstance(data, dict):
        for key in ("notifications", "items", "content", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item if isinstance(item, dict) else {"data": item} for item in value]
        return [data]
    return [{"data": data}]


async def fetch_pdd(
    client: ArubaFatturazioneClient,
    direction: str,
    invoice_filename: str,
) -> dict[str, Any]:
    data = await client.ws_get(
        f"/services/invoice/{direction}/pdd",
        params={"invoiceFilename": invoice_filename},
        expected_binary=True,
        bucket="find_sent" if direction == "out" else "find_received",
    )
    return cast(dict[str, Any], data)


async def find_invoices(
    client: ArubaFatturazioneClient,
    direction: str,
    *,
    date_from: str | None,
    date_to: str | None,
    page: int = 1,
    size: int = 100,
    vat_code: str | None = None,
) -> list[dict[str, Any]]:
    params = {
        "username": client.settings.username,
        "page": page,
        "size": size,
        "startDate": date_from,
        "endDate": date_to,
    }
    if vat_code:
        if direction == "out":
            params["vatcodeReceiver"] = vat_code
        else:
            params["vatcodeSender"] = vat_code
    data = await client.ws_get(
        f"/services/invoice/{direction}/findByUsername",
        params=params,
        bucket="find_sent" if direction == "out" else "find_received",
    )
    return normalize_invoice_list(data)


def normalize_invoice_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item if isinstance(item, dict) else {"data": item} for item in data]
    if isinstance(data, dict):
        for key in ("items", "content", "invoices", "data", "result"):
            value = data.get(key)
            if isinstance(value, list):
                return [item if isinstance(item, dict) else {"data": item} for item in value]
        return [data]
    return [{"data": data}]


def invoice_filename(invoice: dict[str, Any]) -> str | None:
    for key in ("filename", "fileName", "invoiceFilename", "nomeFile", "name"):
        value = invoice.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def parse_invoice_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    xml_base64 = find_xml_base64(payload)
    if not xml_base64:
        return None
    return parse_invoice_xml_base64(xml_base64)


def parse_notifications(notifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed = []
    for notification in notifications:
        xml_base64 = find_xml_base64(notification)
        if xml_base64:
            try:
                parsed.append(parse_notification_xml_base64(xml_base64))
                continue
            except ValueError:
                pass
        parsed.append(
            {"raw": notification, "human_status": infer_status_from_payload(notification)}
        )
    return parsed


def infer_status_from_payload(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, default=str).lower()
    if any(marker in text for marker in ("scarto", "reject", "rejected", "ko", "errore")):
        return "rejected"
    if any(marker in text for marker in ("consegna", "delivered", "ricevuta", "accepted", "ok")):
        return "delivered"
    if any(marker in text for marker in ("pending", "attesa", "in elaborazione")):
        return "pending"
    return "unknown"


def timeline_for(
    filename: str,
    invoice: dict[str, Any],
    parsed_xml: dict[str, Any] | None,
    notifications: list[dict[str, Any]],
) -> dict[str, Any]:
    events = []
    invoice_date = (
        parsed_xml.get("dates", {}).get("invoice_date")
        if parsed_xml
        else first_value(invoice, ("date", "invoiceDate", "createdAt"))
    )
    if invoice_date:
        events.append(
            {
                "date": invoice_date,
                "event": "invoice_created",
                "description": "Invoice document date.",
            }
        )
    for notification in notifications:
        events.append(
            {
                "date": notification.get("date")
                or first_value(notification.get("raw", {}), ("date", "createdAt")),
                "event": "notification_received",
                "notification_type": notification.get("notification_type"),
                "description": notification.get("human_status") or "SDI notification.",
            }
        )
    events.sort(key=lambda item: str(item.get("date") or ""))
    statuses = [item.get("human_status") for item in notifications if item.get("human_status")]
    current_status = derive_status([str(status) for status in statuses])
    return {
        "filename": filename,
        "timeline": events,
        "current_status": current_status,
        "final": current_status in {"delivered", "delivered_or_processed", "rejected"},
    }


def derive_status(statuses: list[str]) -> str:
    if any(status == "rejected" for status in statuses):
        return "rejected"
    if any(status in {"delivered", "delivered_or_processed"} for status in statuses):
        return "delivered"
    if any(status == "pending" for status in statuses):
        return "pending"
    return "unknown"


def first_value(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value:
            return value
    return None


def filter_invoices(
    invoices: list[dict[str, Any]],
    *,
    party_name_contains: str | None = None,
    invoice_number: str | None = None,
    status: str | None = None,
    min_total: float | None = None,
    max_total: float | None = None,
    text_contains: str | None = None,
) -> list[dict[str, Any]]:
    filtered = []
    for invoice in invoices:
        searchable = json.dumps(invoice, default=str).lower()
        if party_name_contains and party_name_contains.lower() not in searchable:
            continue
        if invoice_number and invoice_number.lower() not in searchable:
            continue
        if status and status.lower() not in searchable:
            continue
        if text_contains and text_contains.lower() not in searchable:
            continue
        total = find_numeric_total(invoice)
        if min_total is not None and (total is None or total < Decimal(str(min_total))):
            continue
        if max_total is not None and (total is None or total > Decimal(str(max_total))):
            continue
        filtered.append(invoice)
    return filtered


def find_numeric_total(payload: dict[str, Any]) -> Decimal | None:
    for key in ("gross_total", "grossTotal", "total", "amount", "importoTotaleDocumento"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return Decimal(str(value))
        except (ValueError, ArithmeticError):
            continue
    return None


async def get_full_context(
    client: ArubaFatturazioneClient,
    *,
    direction: str,
    filename: str,
    include_file: bool,
    include_pdf: bool,
    include_notifications: bool,
    include_pdd: bool,
    confirm_read: bool,
) -> dict[str, Any]:
    ensure_confirm(
        client, include_file or include_pdf or include_notifications or include_pdd, confirm_read
    )
    invoice = await fetch_invoice_by_filename(
        client, direction, filename, include_file=include_file, include_pdf=include_pdf
    )
    parsed_xml = parse_invoice_from_payload(invoice)
    notifications = []
    parsed_notifications = []
    if include_notifications:
        notifications = await fetch_notifications(client, direction, filename)
        parsed_notifications = parse_notifications(notifications)
    pdd = await fetch_pdd(client, direction, filename) if include_pdd else None
    timeline = timeline_for(filename, invoice, parsed_xml, parsed_notifications)
    return {
        "ok": True,
        "direction": direction,
        "filename": filename,
        "invoice": invoice,
        "parsed_xml": parsed_xml,
        "notifications": parsed_notifications,
        "timeline": timeline["timeline"],
        "status_summary": timeline["current_status"],
        "parties": {
            "supplier": parsed_xml.get("supplier") if parsed_xml else None,
            "customer": parsed_xml.get("customer") if parsed_xml else None,
        },
        "amounts": parsed_xml.get("amounts") if parsed_xml else None,
        "dates": parsed_xml.get("dates") if parsed_xml else None,
        "pdd": pdd,
        "warnings": [] if parsed_xml else ["No parseable invoice XML was found in Aruba response."],
    }


def status_report(invoices_by_direction: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    by_status: Counter[str] = Counter()
    problematic = []
    for direction, invoices in invoices_by_direction.items():
        for invoice in invoices:
            status = infer_status_from_payload(invoice)
            by_status[status] += 1
            if status in {"rejected", "pending", "unknown"}:
                problematic.append({"direction": direction, "invoice": invoice, "status": status})
    return {
        "totals": {
            "sent": len(invoices_by_direction.get("out", [])),
            "received": len(invoices_by_direction.get("in", [])),
            "delivered": by_status.get("delivered", 0),
            "rejected": by_status.get("rejected", 0),
            "pending": by_status.get("pending", 0),
            "unknown": by_status.get("unknown", 0),
        },
        "by_status": [{"status": key, "count": value} for key, value in by_status.items()],
        "problematic_invoices": problematic[:100],
    }


def duplicate_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        parsed = row.get("parsed") or {}
        key = (
            parsed.get("supplier", {}).get("vat", {}).get("code") or row.get("supplier_vat"),
            parsed.get("document", {}).get("number") or row.get("invoice_number"),
            parsed.get("document", {}).get("date") or row.get("invoice_date"),
            parsed.get("amounts", {}).get("gross") or row.get("gross_total"),
        )
        if any(key):
            groups[key].append(row)
    return [{"criteria": key, "invoices": items} for key, items in groups.items() if len(items) > 1]


def table_from_rows(rows: list[dict[str, Any]], columns: list[str]) -> dict[str, Any]:
    return {
        "columns": columns,
        "rows": [[row.get(column) for column in columns] for row in rows],
    }


def redact_invoice(invoice: dict[str, Any], level: str) -> dict[str, Any]:
    redacted = redact(invoice)
    if level == "light":
        return cast(dict[str, Any], redacted)
    redacted = redact(redacted)
    if level == "strict":
        return {"redacted": True, "summary": summarize_redacted(redacted)}
    return cast(dict[str, Any], redacted)


def summarize_redacted(invoice: dict[str, Any]) -> dict[str, Any]:
    return {
        "keys": sorted(invoice.keys()),
        "contains_payload": "data_base64" in json.dumps(invoice, default=str),
    }
