"""Best-effort FatturaPA XML parsing and reporting helpers."""

from __future__ import annotations

import base64
import binascii
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as ET

from .redaction import redact

BASE64_MIN_LENGTH = 80


def parse_invoice_xml_base64(xml_base64: str, *, redact_sensitive: bool = False) -> dict[str, Any]:
    """Parse a Base64-encoded FatturaPA XML document."""

    return parse_invoice_xml(decode_base64_text(xml_base64), redact_sensitive=redact_sensitive)


def parse_notification_xml_base64(xml_base64: str) -> dict[str, Any]:
    """Parse a Base64-encoded SDI notification XML document."""

    return parse_notification_xml(decode_base64_text(xml_base64))


def decode_base64_text(value: str) -> str:
    """Decode Base64 text, tolerating whitespace."""

    try:
        raw = base64.b64decode(re.sub(r"\s+", "", value), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid Base64 payload.") from exc
    for encoding in ("utf-8", "utf-16", "iso-8859-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_invoice_xml(xml_text: str, *, redact_sensitive: bool = False) -> dict[str, Any]:
    """Parse relevant FatturaPA fields into a stable, LLM-friendly shape."""

    root = ET.fromstring(xml_text)
    supplier_node = first_descendant(root, "CedentePrestatore")
    customer_node = first_descendant(root, "CessionarioCommittente")
    document_node = first_descendant(root, "DatiGeneraliDocumento")
    line_nodes = descendants(root, "DettaglioLinee")
    tax_nodes = descendants(root, "DatiRiepilogo")
    payment_nodes = descendants(root, "DettaglioPagamento")
    parsed: dict[str, Any] = {
        "header": {
            "transmission": element_to_dict(first_descendant(root, "DatiTrasmissione")),
            "document": element_to_dict(document_node),
        },
        "body": {
            "general": element_to_dict(first_descendant(root, "DatiGenerali")),
            "goods_services": element_to_dict(first_descendant(root, "DatiBeniServizi")),
            "payment": element_to_dict(first_descendant(root, "DatiPagamento")),
        },
        "supplier": parse_party(supplier_node),
        "customer": parse_party(customer_node),
        "document": {
            "type": text(document_node, "TipoDocumento"),
            "number": text(document_node, "Numero"),
            "date": text(document_node, "Data"),
            "currency": text(document_node, "Divisa"),
            "total": decimal_text(text(document_node, "ImportoTotaleDocumento")),
        },
        "line_items": [parse_line(line) for line in line_nodes],
        "tax_summary": [parse_tax(tax) for tax in tax_nodes],
        "payments": [parse_payment(payment) for payment in payment_nodes],
        "references": parse_references(root),
    }
    parsed["amounts"] = compute_amounts(parsed)
    parsed["dates"] = extract_dates(parsed)
    if redact_sensitive:
        parsed = redact(parsed)
    return parsed


def parse_notification_xml(xml_text: str) -> dict[str, Any]:
    """Parse a notification XML with broad SDI field names."""

    root = ET.fromstring(xml_text)
    notification_type = local_name(root.tag)
    errors = []
    for error_node in descendants(root, "Errore"):
        errors.append(
            {
                "code": text(error_node, "Codice"),
                "description": text(error_node, "Descrizione"),
            }
        )
    outcome = first_non_empty(
        text(root, "Esito"),
        text(root, "Descrizione"),
        text(root, "Scarto"),
        text(root, "Message"),
    )
    return {
        "notification_type": notification_type,
        "invoice_filename": first_non_empty(
            text(root, "NomeFile"), text(root, "IdentificativoSdI")
        ),
        "sdi_id": first_non_empty(text(root, "IdentificativoSdI"), text(root, "IdSdi")),
        "date": first_non_empty(
            text(root, "DataOraRicezione"), text(root, "DataOraConsegna"), text(root, "Data")
        ),
        "outcome": outcome,
        "errors": errors,
        "human_status": explain_notification_status(notification_type, outcome, errors),
        "raw": element_to_dict(root),
    }


def parse_party(node: Element | None) -> dict[str, Any]:
    if node is None:
        return {}
    return {
        "name": first_non_empty(
            text(node, "Denominazione"),
            " ".join(filter(None, [text(node, "Nome"), text(node, "Cognome")])),
        ),
        "vat": {
            "country": text(node, "IdPaese"),
            "code": text(node, "IdCodice"),
        },
        "fiscal_code": text(node, "CodiceFiscale"),
        "tax_regime": text(node, "RegimeFiscale"),
        "address": {
            "street": text(node, "Indirizzo"),
            "number": text(node, "NumeroCivico"),
            "zip": text(node, "CAP"),
            "city": text(node, "Comune"),
            "province": text(node, "Provincia"),
            "country": text(node, "Nazione"),
        },
        "pec": text(node, "PECDestinatario"),
        "sdi_code": text(node, "CodiceDestinatario"),
    }


def parse_line(node: Element) -> dict[str, Any]:
    return {
        "number": text(node, "NumeroLinea"),
        "description": text(node, "Descrizione"),
        "quantity": decimal_text(text(node, "Quantita")),
        "unit_price": decimal_text(text(node, "PrezzoUnitario")),
        "total": decimal_text(text(node, "PrezzoTotale")),
        "vat_rate": decimal_text(text(node, "AliquotaIVA")),
        "nature": text(node, "Natura"),
    }


def parse_tax(node: Element) -> dict[str, Any]:
    return {
        "vat_rate": decimal_text(text(node, "AliquotaIVA")),
        "nature": text(node, "Natura"),
        "net": decimal_text(text(node, "ImponibileImporto")),
        "vat": decimal_text(text(node, "Imposta")),
        "exigibility": text(node, "EsigibilitaIVA"),
        "reference": text(node, "RiferimentoNormativo"),
    }


def parse_payment(node: Element) -> dict[str, Any]:
    return {
        "method": text(node, "ModalitaPagamento"),
        "due_date": text(node, "DataScadenzaPagamento"),
        "amount": decimal_text(text(node, "ImportoPagamento")),
        "iban": text(node, "IBAN"),
        "beneficiary": text(node, "Beneficiario"),
    }


def parse_references(root: Element) -> dict[str, Any]:
    return {
        "cig": first_text(root, "CodiceCIG"),
        "cup": first_text(root, "CodiceCUP"),
        "order": element_to_dict(first_descendant(root, "DatiOrdineAcquisto")),
        "contract": element_to_dict(first_descendant(root, "DatiContratto")),
    }


def compute_amounts(parsed: dict[str, Any]) -> dict[str, str | None]:
    tax_summary = parsed.get("tax_summary", [])
    net = sum_decimal(item.get("net") for item in tax_summary)
    vat = sum_decimal(item.get("vat") for item in tax_summary)
    gross = maybe_decimal(parsed.get("document", {}).get("total"))
    if gross is None and net is not None and vat is not None:
        gross = net + vat
    return {
        "net": format_decimal(net),
        "vat": format_decimal(vat),
        "gross": format_decimal(gross),
    }


def extract_dates(parsed: dict[str, Any]) -> dict[str, Any]:
    payments = parsed.get("payments", [])
    return {
        "invoice_date": parsed.get("document", {}).get("date"),
        "payment_due_dates": [item.get("due_date") for item in payments if item.get("due_date")],
    }


def summarize_invoice(parsed: dict[str, Any]) -> dict[str, Any]:
    supplier = parsed.get("supplier", {}).get("name")
    customer = parsed.get("customer", {}).get("name")
    document = parsed.get("document", {})
    amounts = parsed.get("amounts", {})
    return {
        "summary": (
            f"Invoice {document.get('number') or 'unknown'} dated "
            f"{document.get('date') or 'unknown'} "
            f"issued by {supplier or 'unknown supplier'} to {customer or 'unknown customer'}."
        ),
        "invoice_type": document.get("type"),
        "number": document.get("number"),
        "date": document.get("date"),
        "supplier": supplier,
        "customer": customer,
        "net_amount": amounts.get("net"),
        "vat_amount": amounts.get("vat"),
        "gross_amount": amounts.get("gross"),
        "payment_terms": parsed.get("payments", []),
        "line_items_summary": parsed.get("line_items", [])[:20],
        "tax_breakdown": parsed.get("tax_summary", []),
        "detected_issues": validate_invoice_structure(parsed)["warnings"],
    }


def validate_invoice_structure(parsed: dict[str, Any]) -> dict[str, Any]:
    warnings = []
    for label, value in (
        ("invoice_number", parsed.get("document", {}).get("number")),
        ("invoice_date", parsed.get("document", {}).get("date")),
        ("supplier", parsed.get("supplier", {}).get("name")),
        ("customer", parsed.get("customer", {}).get("name")),
    ):
        if not value:
            warnings.append(f"Missing {label}.")
    if not parsed.get("line_items"):
        warnings.append("No line items found.")
    if not parsed.get("tax_summary"):
        warnings.append("No VAT summary found.")
    return {"valid": not warnings, "warnings": warnings}


def compare_invoice_totals(parsed: dict[str, Any]) -> dict[str, Any]:
    document_total = maybe_decimal(parsed.get("amounts", {}).get("gross"))
    line_total = sum_decimal(item.get("total") for item in parsed.get("line_items", []))
    vat_total = maybe_decimal(parsed.get("amounts", {}).get("vat")) or Decimal("0")
    computed_total = None if line_total is None else line_total + vat_total
    difference = None
    if document_total is not None and computed_total is not None:
        difference = document_total - computed_total
    warnings = []
    if difference is not None and abs(difference) > Decimal("0.02"):
        warnings.append("Document total differs from line totals plus VAT.")
    return {
        "consistent": not warnings,
        "document_total": format_decimal(document_total),
        "computed_total": format_decimal(computed_total),
        "difference": format_decimal(difference),
        "warnings": warnings,
    }


def aggregate_vat(parsed_invoices: list[dict[str, Any]]) -> dict[str, Any]:
    totals: defaultdict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"net": Decimal("0"), "vat": Decimal("0"), "gross": Decimal("0")}
    )
    grand = {"net": Decimal("0"), "vat": Decimal("0"), "gross": Decimal("0")}
    for parsed in parsed_invoices:
        for item in parsed.get("tax_summary", []):
            rate = str(item.get("vat_rate") or item.get("nature") or "unknown")
            net = maybe_decimal(item.get("net")) or Decimal("0")
            vat = maybe_decimal(item.get("vat")) or Decimal("0")
            gross = net + vat
            for key, value in (("net", net), ("vat", vat), ("gross", gross)):
                totals[rate][key] += value
                grand[key] += value
    return {
        "totals": {key: format_decimal(value) for key, value in grand.items()},
        "by_vat_rate": [
            {"rate": rate, **{key: format_decimal(value) for key, value in values.items()}}
            for rate, values in sorted(totals.items())
        ],
    }


def extract_requested_fields(parsed: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    mapping = {
        "invoice_number": parsed.get("document", {}).get("number"),
        "invoice_date": parsed.get("document", {}).get("date"),
        "supplier_name": parsed.get("supplier", {}).get("name"),
        "supplier_vat": parsed.get("supplier", {}).get("vat", {}).get("code"),
        "customer_name": parsed.get("customer", {}).get("name"),
        "customer_vat": parsed.get("customer", {}).get("vat", {}).get("code"),
        "total_amount": parsed.get("amounts", {}).get("gross"),
        "vat_amount": parsed.get("amounts", {}).get("vat"),
        "payment_due_date": parsed.get("dates", {}).get("payment_due_dates", [None])[0],
        "payment_method": parsed.get("payments", [{}])[0].get("method")
        if parsed.get("payments")
        else None,
        "line_items": parsed.get("line_items"),
        "tax_summary": parsed.get("tax_summary"),
        "cig": parsed.get("references", {}).get("cig"),
        "cup": parsed.get("references", {}).get("cup"),
        "pec": first_non_empty(
            parsed.get("supplier", {}).get("pec"), parsed.get("customer", {}).get("pec")
        ),
        "sdi_code": first_non_empty(
            parsed.get("supplier", {}).get("sdi_code"), parsed.get("customer", {}).get("sdi_code")
        ),
    }
    return {field: mapping.get(field) for field in fields}


def find_xml_base64(payload: Any) -> str | None:
    """Find the first Base64 string that decodes to XML in a nested payload."""

    for candidate in iter_strings(payload):
        if len(candidate) < BASE64_MIN_LENGTH:
            continue
        try:
            text_value = decode_base64_text(candidate)
        except ValueError:
            continue
        if (
            "<" in text_value
            and ">" in text_value
            and ("FatturaElettronica" in text_value or "?xml" in text_value)
        ):
            return candidate
    return None


def iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        items = []
        for item in value.values():
            items.extend(iter_strings(item))
        return items
    if isinstance(value, list):
        items = []
        for item in value:
            items.extend(iter_strings(item))
        return items
    return []


def element_to_dict(node: Element | None) -> Any:
    if node is None:
        return None
    children = list(node)
    if not children:
        return clean_text(node.text)
    result: dict[str, Any] = {}
    grouped: dict[str, list[Any]] = defaultdict(list)
    for child in children:
        grouped[local_name(child.tag)].append(element_to_dict(child))
    for key, values in grouped.items():
        result[key] = values[0] if len(values) == 1 else values
    return result


def descendants(root: Element, name: str) -> list[Element]:
    return [node for node in root.iter() if local_name(node.tag) == name]


def first_descendant(root: Element | None, name: str) -> Element | None:
    if root is None:
        return None
    for node in root.iter():
        if local_name(node.tag) == name:
            return node
    return None


def text(root: Element | None, name: str) -> str | None:
    node = first_descendant(root, name)
    return clean_text(node.text if node is not None else None)


def first_text(root: Element, name: str) -> str | None:
    return text(root, name)


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def decimal_text(value: str | None) -> str | None:
    return format_decimal(maybe_decimal(value))


def maybe_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def sum_decimal(values: Any) -> Decimal | None:
    total = Decimal("0")
    found = False
    for value in values:
        parsed = maybe_decimal(value)
        if parsed is not None:
            total += parsed
            found = True
    return total if found else None


def format_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01")))


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value:
            return value
    return None


def explain_notification_status(
    notification_type: str | None, outcome: str | None, errors: list[dict[str, Any]]
) -> str:
    code = (notification_type or "").upper()
    if errors or "SC" in code or "NS" in code:
        return "rejected"
    if any(marker in code for marker in ("RC", "MC", "EC")):
        return "delivered_or_processed"
    if outcome:
        return "has_outcome"
    return "unknown"


CODE_EXPLANATIONS: dict[str, dict[str, dict[str, str]]] = {
    "document_type": {
        "TD01": {"label": "Invoice", "description": "Standard invoice.", "category": "invoice"},
        "TD04": {"label": "Credit note", "description": "Credit note.", "category": "adjustment"},
        "TD05": {"label": "Debit note", "description": "Debit note.", "category": "adjustment"},
        "TD24": {
            "label": "Deferred invoice",
            "description": "Deferred invoice.",
            "category": "invoice",
        },
    },
    "nature": {
        "N1": {"label": "Excluded", "description": "Excluded from VAT.", "category": "vat_nature"},
        "N2.1": {
            "label": "Not subject",
            "description": "Not subject to VAT under art. 7-7-septies.",
            "category": "vat_nature",
        },
        "N2.2": {
            "label": "Other not subject",
            "description": "Other transactions not subject to VAT.",
            "category": "vat_nature",
        },
        "N3.1": {
            "label": "Non-taxable exports",
            "description": "Non-taxable export transactions.",
            "category": "vat_nature",
        },
        "N4": {
            "label": "Exempt",
            "description": "VAT-exempt transaction.",
            "category": "vat_nature",
        },
    },
    "payment_method": {
        "MP01": {"label": "Cash", "description": "Cash payment.", "category": "payment"},
        "MP05": {"label": "Bank transfer", "description": "Bank transfer.", "category": "payment"},
        "MP08": {"label": "Payment card", "description": "Payment card.", "category": "payment"},
    },
    "tax_regime": {
        "RF01": {
            "label": "Ordinary",
            "description": "Ordinary tax regime.",
            "category": "tax_regime",
        },
        "RF19": {
            "label": "Flat-rate",
            "description": "Regime forfettario.",
            "category": "tax_regime",
        },
    },
}
