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
    format_decimal,
    maybe_decimal,
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


def normalize_fiscal_document_payload(
    *,
    document_id: str,
    direction: str,
    document_type: str | None,
    raw_document: dict[str, Any],
    parsed_xml: dict[str, Any] | None,
) -> dict[str, Any]:
    """Normalize Aruba/raw FatturaPA data into a stable fiscal document shape."""

    warnings: list[str] = []
    parsed = parsed_xml or {}
    document = parsed.get("document", {}) if parsed else {}
    amounts = parsed.get("amounts", {}) if parsed else {}
    counterparty = _counterparty_for(direction, parsed)
    normalized_type = document_type or _document_type_from_code(document.get("type"))
    normalized = {
        "documentId": document_id,
        "direction": direction,
        "documentType": normalized_type,
        "number": document.get("number") or first_value(raw_document, ("number", "invoiceNumber")),
        "issueDate": document.get("date") or first_value(raw_document, ("date", "invoiceDate")),
        "transmissionDate": first_value(
            raw_document, ("transmissionDate", "sentDate", "dataTrasmissione")
        ),
        "receivedDate": first_value(raw_document, ("receivedDate", "dataRicezione")),
        "status": first_value(raw_document, ("status", "invoiceStatus", "state")),
        "sdiStatus": first_value(raw_document, ("sdiStatus", "statusSdi", "esito", "outcome")),
        "currency": document.get("currency") or first_value(raw_document, ("currency", "divisa")),
        "amounts": {
            "taxableAmount": amounts.get("net") or _raw_amount(raw_document, "taxableAmount"),
            "taxAmount": amounts.get("vat") or _raw_amount(raw_document, "taxAmount"),
            "totalAmount": amounts.get("gross") or _raw_amount(raw_document, "totalAmount"),
        },
        "counterparty": normalize_party(counterparty),
        "lineItems": parsed.get("line_items", []),
        "rawRefs": {
            "filename": invoice_filename(raw_document),
            "idSdi": first_value(raw_document, ("idSdi", "sdiId", "identificativoSdI")),
            "aruba": raw_document,
        },
        "warnings": warnings,
    }
    for key in ("number", "issueDate", "currency"):
        if not normalized.get(key):
            warnings.append(f"{key} is not available in the Aruba response.")
    if not parsed_xml:
        warnings.append("No parseable FatturaPA XML was found; normalized fields are best effort.")
    if not normalized["counterparty"].get("name"):
        warnings.append("Counterparty name is not available.")
    return normalized


def normalize_party(party: dict[str, Any]) -> dict[str, Any]:
    vat = cast(dict[str, Any], party.get("vat")) if isinstance(party.get("vat"), dict) else {}
    address = (
        cast(dict[str, Any], party.get("address")) if isinstance(party.get("address"), dict) else {}
    )
    return {
        "name": party.get("name"),
        "vatId": vat.get("code") or party.get("vatId"),
        "fiscalCode": party.get("fiscal_code") or party.get("fiscalCode"),
        "email": party.get("email"),
        "pec": party.get("pec"),
        "sdiCode": party.get("sdi_code") or party.get("sdiCode"),
        "address": address or party.get("address"),
    }


def document_lifecycle(
    normalized: dict[str, Any], notifications: list[dict[str, Any]]
) -> dict[str, Any]:
    status = str(normalized.get("status") or "").lower()
    sdi_status = str(normalized.get("sdiStatus") or "").lower()
    notification_status = derive_status(
        [str(item.get("human_status") or item.get("outcome") or "") for item in notifications]
    )
    combined = " ".join([status, sdi_status, notification_status]).strip()
    stage = "unknown"
    if any(marker in combined for marker in ("scarto", "reject", "rejected", "ko", "error")):
        stage = "rejected"
    elif any(marker in combined for marker in ("refused", "rifiut")):
        stage = "refused"
    elif any(marker in combined for marker in ("accepted", "accett")):
        stage = "accepted"
    elif any(marker in combined for marker in ("consegna", "delivered", "ricevuta")):
        stage = "delivered"
    elif normalized.get("direction") == "inbound" and any(
        marker in combined for marker in ("received", "ricev")
    ):
        stage = "received"
    elif any(marker in combined for marker in ("stored", "conserv")):
        stage = "stored"
    elif any(marker in combined for marker in ("sent", "transmitted", "inviat")):
        stage = "sent"
    elif any(marker in combined for marker in ("uploaded", "caricat")):
        stage = "uploaded"
    is_problematic = stage in {"rejected", "refused", "unknown"}
    return {
        "documentId": normalized.get("documentId"),
        "direction": normalized.get("direction"),
        "currentStatus": normalized.get("status") or notification_status,
        "sdiStatus": normalized.get("sdiStatus") or notification_status,
        "lifecycleStage": stage,
        "notifications": notifications,
        "isProblematic": is_problematic,
        "problemReason": "Status is rejected/refused/unknown." if is_problematic else None,
        "warnings": [] if stage != "unknown" else ["Unable to infer a precise lifecycle stage."],
    }


def validate_fiscal_consistency(normalized: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    blockers: list[str] = []
    checks: list[dict[str, Any]] = []
    amounts = normalized.get("amounts", {})
    taxable = maybe_decimal(amounts.get("taxableAmount"))
    tax = maybe_decimal(amounts.get("taxAmount"))
    total = maybe_decimal(amounts.get("totalAmount"))
    amount_ok = (
        taxable is None
        or tax is None
        or total is None
        or abs((taxable + tax) - total) <= Decimal("0.02")
    )
    checks.append({"name": "total_matches_taxable_plus_tax", "passed": amount_ok})
    if not amount_ok:
        blockers.append("Total amount differs from taxable amount plus tax.")
    if normalized.get("direction") not in {"outbound", "inbound"}:
        blockers.append("Direction must be outbound or inbound.")
    if not normalized.get("issueDate"):
        warnings.append("Issue date is missing.")
    party = normalized.get("counterparty", {})
    if not party.get("vatId") and not party.get("fiscalCode"):
        warnings.append("Counterparty VAT ID and fiscal code are missing.")
    if not normalized.get("status") and not normalized.get("sdiStatus"):
        warnings.append("Document status is missing.")
    for line in normalized.get("lineItems", []):
        if not line.get("vat_rate") and not line.get("nature"):
            warnings.append("At least one line item has no VAT rate or VAT nature.")
            break
    checks.extend(
        [
            {"name": "has_issue_date", "passed": bool(normalized.get("issueDate"))},
            {
                "name": "has_counterparty_identifier",
                "passed": bool(party.get("vatId") or party.get("fiscalCode")),
            },
            {"name": "has_currency", "passed": bool(normalized.get("currency"))},
        ]
    )
    if not normalized.get("currency"):
        warnings.append("Currency is missing.")
    return {"valid": not blockers, "warnings": warnings, "blockers": blockers, "checks": checks}


def fiscal_document_risk(
    normalized: dict[str, Any],
    lifecycle: dict[str, Any],
    *,
    pdd_available: bool,
    downloadable_files: list[dict[str, Any]],
) -> dict[str, Any]:
    validation = validate_fiscal_consistency(normalized)
    warnings = list(validation["warnings"])
    blockers = list(validation["blockers"])
    if lifecycle.get("isProblematic"):
        blockers.append(lifecycle.get("problemReason") or "Document lifecycle is problematic.")
    if not pdd_available and lifecycle.get("lifecycleStage") in {"delivered", "accepted", "stored"}:
        warnings.append("PDD is not marked as available for a finalized document.")
    if not downloadable_files:
        warnings.append("No downloadable file reference was detected.")
    risk_level = "low"
    if blockers:
        risk_level = "high"
    elif warnings:
        risk_level = "medium"
    return {
        "riskLevel": risk_level,
        "warnings": warnings,
        "blockers": blockers,
        "recommendations": [
            "Review the original Aruba payload and SDI notifications before taking external action."
        ]
        if risk_level != "low"
        else [],
    }


def fiscal_document_summary_payload(normalized: dict[str, Any]) -> dict[str, Any]:
    party = normalized.get("counterparty", {})
    amounts = normalized.get("amounts", {})
    summary = (
        f"{normalized.get('documentType') or 'Fiscal document'} "
        f"{normalized.get('number') or 'unknown number'} dated "
        f"{normalized.get('issueDate') or 'unknown date'} for "
        f"{amounts.get('totalAmount') or 'unknown amount'} "
        f"{normalized.get('currency') or ''}."
    ).strip()
    return {
        "summary": summary,
        "keyFacts": {
            "documentId": normalized.get("documentId"),
            "direction": normalized.get("direction"),
            "counterpartyName": party.get("name"),
            "status": normalized.get("status"),
            "sdiStatus": normalized.get("sdiStatus"),
            "totalAmount": amounts.get("totalAmount"),
            "currency": normalized.get("currency"),
        },
        "warnings": normalized.get("warnings", []),
    }


def period_summary_from_documents(
    documents: list[dict[str, Any]], from_date: str | None, to_date: str | None
) -> dict[str, Any]:
    outbound = [item for item in documents if item.get("direction") == "outbound"]
    inbound = [item for item in documents if item.get("direction") == "inbound"]
    problematic = [item for item in documents if document_lifecycle(item, []).get("isProblematic")]
    return {
        "period": {"from": from_date, "to": to_date},
        "outbound": _direction_totals(outbound),
        "inbound": _direction_totals(inbound),
        "rejectedCount": sum(1 for item in problematic if "reject" in json.dumps(item).lower()),
        "problematicCount": len(problematic),
        "warnings": [],
    }


def tax_summary_from_documents(
    documents: list[dict[str, Any]], from_date: str | None, to_date: str | None
) -> dict[str, Any]:
    by_rate: defaultdict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"taxableAmount": Decimal("0"), "taxAmount": Decimal("0")}
    )
    taxable_total = Decimal("0")
    tax_total = Decimal("0")
    exempt = Decimal("0")
    reverse_charge = Decimal("0")
    for document in documents:
        for line in document.get("lineItems", []):
            rate = str(line.get("vat_rate") or line.get("nature") or "unknown")
            line_total = maybe_decimal(line.get("total")) or Decimal("0")
            by_rate[rate]["taxableAmount"] += line_total
            taxable_total += line_total
            if str(line.get("nature") or "").startswith("N"):
                exempt += line_total
        amount_tax = maybe_decimal(document.get("amounts", {}).get("taxAmount")) or Decimal("0")
        tax_total += amount_tax
    return {
        "period": {"from": from_date, "to": to_date},
        "taxableAmount": format_decimal(taxable_total),
        "taxAmount": format_decimal(tax_total),
        "byTaxRate": [
            {"rate": rate, **{key: format_decimal(value) for key, value in values.items()}}
            for rate, values in sorted(by_rate.items())
        ],
        "exemptAmount": format_decimal(exempt),
        "reverseChargeAmount": format_decimal(reverse_charge),
        "warnings": ["Informational summary only; it does not replace accounting or tax review."],
    }


def fiscal_events_from_documents(
    documents: list[dict[str, Any]], event_types: list[str] | None = None
) -> list[dict[str, Any]]:
    allowed = set(event_types or [])
    events = []
    for document in documents:
        lifecycle = document_lifecycle(document, [])
        event_type = _event_type_for_stage(lifecycle["lifecycleStage"])
        if event_type is None:
            continue
        if allowed and event_type not in allowed:
            continue
        events.append(
            {
                "eventType": event_type,
                "eventId": f"{document.get('documentId')}:{event_type}",
                "occurredAt": document.get("transmissionDate")
                or document.get("receivedDate")
                or document.get("issueDate"),
                "documentId": document.get("documentId"),
                "direction": document.get("direction"),
                "documentNumber": document.get("number"),
                "counterparty": document.get("counterparty"),
                "amount": document.get("amounts", {}).get("totalAmount"),
                "status": document.get("status"),
                "sdiStatus": document.get("sdiStatus"),
                "derived": True,
                "metadata": {"reason": "Derived from current Aruba document status."},
            }
        )
    return events


def document_markdown(
    normalized: dict[str, Any],
    notifications: list[dict[str, Any]],
    *,
    include_line_items: bool,
    include_notifications: bool,
    include_raw_refs: bool,
) -> dict[str, Any]:
    title = f"Fiscal document {normalized.get('number') or normalized.get('documentId')}"
    total = normalized.get("amounts", {}).get("totalAmount")
    currency = normalized.get("currency") or ""
    lines = [
        f"# {title}",
        "",
        f"- Direction: {normalized.get('direction')}",
        f"- Issue date: {normalized.get('issueDate')}",
        f"- Status: {normalized.get('status') or normalized.get('sdiStatus')}",
        f"- Total: {total} {currency}".rstrip(),
    ]
    if include_line_items:
        lines.extend(["", "## Line items"])
        for item in normalized.get("lineItems", []):
            lines.append(f"- {item.get('description') or 'Line'}: {item.get('total')}")
    if include_notifications:
        lines.extend(["", "## Notifications"])
        for item in notifications:
            lines.append(
                f"- {item.get('notification_type') or 'notification'}: {item.get('human_status')}"
            )
    if include_raw_refs:
        lines.extend(
            [
                "",
                "## Raw references",
                "```json",
                json.dumps(normalized.get("rawRefs"), default=str, indent=2),
                "```",
            ]
        )
    return {
        "title": title,
        "markdown": "\n".join(lines),
        "metadata": {
            "documentId": normalized.get("documentId"),
            "direction": normalized.get("direction"),
            "number": normalized.get("number"),
        },
    }


def counterparty_match_hints(normalized: dict[str, Any]) -> dict[str, Any]:
    party = normalized.get("counterparty", {})
    email = party.get("email") or party.get("pec")
    domain = email.split("@", 1)[1] if isinstance(email, str) and "@" in email else None
    populated = sum(
        1 for key in ("name", "vatId", "fiscalCode", "email", "pec", "sdiCode") if party.get(key)
    )
    return {
        "counterpartyMatchHints": {**party, "domain": domain},
        "documentMatchHints": {
            "number": normalized.get("number"),
            "issueDate": normalized.get("issueDate"),
            "totalAmount": normalized.get("amounts", {}).get("totalAmount"),
            "currency": normalized.get("currency"),
            "direction": normalized.get("direction"),
        },
        "confidence": min(1.0, populated / 4),
        "warnings": normalized.get("warnings", []),
    }


def _counterparty_for(direction: str, parsed: dict[str, Any]) -> dict[str, Any]:
    if not parsed:
        return {}
    if direction == "outbound":
        return cast(dict[str, Any], parsed.get("customer") or {})
    return cast(dict[str, Any], parsed.get("supplier") or {})


def _document_type_from_code(code: str | None) -> str | None:
    category = CODE_CATEGORY.get(code or "")
    return category or code


CODE_CATEGORY = {
    "TD01": "invoice",
    "TD04": "credit_note",
    "TD05": "debit_note",
}


def _raw_amount(payload: dict[str, Any], logical_name: str) -> Any:
    keys = {
        "taxableAmount": ("taxableAmount", "net", "netAmount", "imponibile"),
        "taxAmount": ("taxAmount", "vat", "vatAmount", "imposta"),
        "totalAmount": ("totalAmount", "gross", "grossAmount", "amount", "total"),
    }
    return first_value(payload, keys[logical_name])


def _direction_totals(documents: list[dict[str, Any]]) -> dict[str, Any]:
    total = Decimal("0")
    tax = Decimal("0")
    for document in documents:
        amounts = document.get("amounts", {})
        total += maybe_decimal(amounts.get("totalAmount")) or Decimal("0")
        tax += maybe_decimal(amounts.get("taxAmount")) or Decimal("0")
    return {
        "count": len(documents),
        "totalAmount": format_decimal(total),
        "taxAmount": format_decimal(tax),
    }


def _event_type_for_stage(stage: str) -> str | None:
    return {
        "uploaded": "document_uploaded",
        "sent": "document_sent",
        "received": "document_received",
        "delivered": "document_delivered",
        "rejected": "document_rejected",
        "accepted": "document_accepted",
        "refused": "document_refused",
        "stored": "document_stored",
    }.get(stage)
