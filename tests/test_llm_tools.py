import base64

from aruba_fatturazione_elettronica_mcp.invoice_xml import (
    CODE_EXPLANATIONS,
    compare_invoice_totals,
    extract_requested_fields,
    parse_invoice_xml_base64,
    summarize_invoice,
    validate_invoice_structure,
)
from aruba_fatturazione_elettronica_mcp.llm_tools import (
    counterparty_match_hints,
    document_lifecycle,
    document_markdown,
    duplicate_candidates,
    fiscal_document_risk,
    fiscal_document_summary_payload,
    fiscal_events_from_documents,
    normalize_fiscal_document_payload,
    period_summary_from_documents,
    redact_invoice,
    table_from_rows,
    tax_summary_from_documents,
    validate_fiscal_consistency,
)
from aruba_fatturazione_elettronica_mcp.local_index import InvoiceIndex


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def _invoice_xml() -> str:
    return _b64(
        """<?xml version="1.0" encoding="UTF-8"?>
<FatturaElettronica>
  <FatturaElettronicaHeader>
    <DatiTrasmissione>
      <CodiceDestinatario>ABC1234</CodiceDestinatario>
      <PECDestinatario>client@example.test</PECDestinatario>
    </DatiTrasmissione>
    <CedentePrestatore>
      <DatiAnagrafici>
        <IdFiscaleIVA><IdPaese>IT</IdPaese><IdCodice>12345678901</IdCodice></IdFiscaleIVA>
        <Anagrafica><Denominazione>Supplier SRL</Denominazione></Anagrafica>
        <RegimeFiscale>RF01</RegimeFiscale>
      </DatiAnagrafici>
      <Sede><Indirizzo>Main</Indirizzo><Comune>Rome</Comune><Nazione>IT</Nazione></Sede>
    </CedentePrestatore>
    <CessionarioCommittente>
      <DatiAnagrafici>
        <IdFiscaleIVA><IdPaese>IT</IdPaese><IdCodice>10987654321</IdCodice></IdFiscaleIVA>
        <Anagrafica><Denominazione>Customer SRL</Denominazione></Anagrafica>
      </DatiAnagrafici>
    </CessionarioCommittente>
  </FatturaElettronicaHeader>
  <FatturaElettronicaBody>
    <DatiGenerali>
      <DatiGeneraliDocumento>
        <TipoDocumento>TD01</TipoDocumento>
        <Divisa>EUR</Divisa>
        <Data>2026-01-15</Data>
        <Numero>INV-1</Numero>
        <ImportoTotaleDocumento>122.00</ImportoTotaleDocumento>
      </DatiGeneraliDocumento>
    </DatiGenerali>
    <DatiBeniServizi>
      <DettaglioLinee>
        <NumeroLinea>1</NumeroLinea>
        <Descrizione>Service</Descrizione>
        <Quantita>1.00</Quantita>
        <PrezzoUnitario>100.00</PrezzoUnitario>
        <PrezzoTotale>100.00</PrezzoTotale>
        <AliquotaIVA>22.00</AliquotaIVA>
      </DettaglioLinee>
      <DatiRiepilogo>
        <AliquotaIVA>22.00</AliquotaIVA>
        <ImponibileImporto>100.00</ImponibileImporto>
        <Imposta>22.00</Imposta>
      </DatiRiepilogo>
    </DatiBeniServizi>
    <DatiPagamento>
      <DettaglioPagamento>
        <ModalitaPagamento>MP05</ModalitaPagamento>
        <DataScadenzaPagamento>2026-02-15</DataScadenzaPagamento>
        <ImportoPagamento>122.00</ImportoPagamento>
        <IBAN>IT60X0542811101000000123456</IBAN>
      </DettaglioPagamento>
    </DatiPagamento>
  </FatturaElettronicaBody>
</FatturaElettronica>
"""
    )


def test_parse_summarize_extract_and_validate_invoice_xml() -> None:
    parsed = parse_invoice_xml_base64(_invoice_xml())

    assert parsed["document"]["number"] == "INV-1"
    assert parsed["amounts"] == {"net": "100.00", "vat": "22.00", "gross": "122.00"}
    assert summarize_invoice(parsed)["gross_amount"] == "122.00"
    assert extract_requested_fields(parsed, ["supplier_name", "payment_method"]) == {
        "supplier_name": "Supplier SRL",
        "payment_method": "MP05",
    }
    assert validate_invoice_structure(parsed) == {"valid": True, "warnings": []}
    assert compare_invoice_totals(parsed)["consistent"] is True
    assert CODE_EXPLANATIONS["document_type"]["TD01"]["label"] == "Invoice"


def test_duplicate_table_redaction_and_index(tmp_path) -> None:
    parsed = parse_invoice_xml_base64(_invoice_xml())
    rows = [{"parsed": parsed, "filename": "a.xml"}, {"parsed": parsed, "filename": "b.xml"}]

    assert len(duplicate_candidates(rows)) == 1
    assert table_from_rows([{"filename": "a.xml", "status": "delivered"}], ["filename"]) == {
        "columns": ["filename"],
        "rows": [["a.xml"]],
    }
    assert "IT60X0542811101000000123456" not in str(redact_invoice(parsed, "standard"))

    index = InvoiceIndex(str(tmp_path / "index.sqlite3"))
    index.upsert_invoice(
        direction="out",
        filename="a.xml",
        raw={"filename": "a.xml"},
        parsed=parsed,
        status="delivered",
    )
    assert index.stats()["invoice_count"] == 1
    assert index.search({"vat_code": "12345678901"})[0]["filename"] == "a.xml"


def test_normalize_fiscal_document_and_summary() -> None:
    parsed = parse_invoice_xml_base64(_invoice_xml())

    normalized = normalize_fiscal_document_payload(
        document_id="doc-1",
        direction="outbound",
        document_type=None,
        raw_document={"filename": "IT123_INV-1.xml", "status": "delivered"},
        parsed_xml=parsed,
    )

    assert normalized["documentId"] == "doc-1"
    assert normalized["documentType"] == "invoice"
    assert normalized["number"] == "INV-1"
    assert normalized["counterparty"]["name"] == "Customer SRL"
    assert normalized["amounts"]["totalAmount"] == "122.00"
    assert fiscal_document_summary_payload(normalized)["keyFacts"]["totalAmount"] == "122.00"


def test_lifecycle_consistency_risk_and_events() -> None:
    parsed = parse_invoice_xml_base64(_invoice_xml())
    normalized = normalize_fiscal_document_payload(
        document_id="doc-1",
        direction="outbound",
        document_type=None,
        raw_document={"status": "delivered", "pdf": "available", "pdd": "available"},
        parsed_xml=parsed,
    )
    lifecycle = document_lifecycle(
        normalized, [{"human_status": "delivered", "notification_type": "RC"}]
    )
    risk = fiscal_document_risk(
        normalized,
        lifecycle,
        pdd_available=True,
        downloadable_files=[{"type": "pdf", "available": True}],
    )

    assert lifecycle["lifecycleStage"] == "delivered"
    assert validate_fiscal_consistency(normalized)["valid"] is True
    assert risk["riskLevel"] == "low"
    assert fiscal_events_from_documents([normalized])[0]["eventType"] == "document_delivered"


def test_period_tax_markdown_and_match_hints() -> None:
    parsed = parse_invoice_xml_base64(_invoice_xml())
    outbound = normalize_fiscal_document_payload(
        document_id="doc-1",
        direction="outbound",
        document_type=None,
        raw_document={"status": "delivered"},
        parsed_xml=parsed,
    )
    inbound = normalize_fiscal_document_payload(
        document_id="doc-2",
        direction="inbound",
        document_type=None,
        raw_document={"status": "received"},
        parsed_xml=parsed,
    )

    period = period_summary_from_documents([outbound, inbound], "2026-01-01", "2026-01-31")
    taxes = tax_summary_from_documents([outbound], "2026-01-01", "2026-01-31")
    markdown = document_markdown(
        outbound,
        [{"notification_type": "RC", "human_status": "delivered"}],
        include_line_items=True,
        include_notifications=True,
        include_raw_refs=False,
    )
    hints = counterparty_match_hints(outbound)

    assert period["outbound"]["count"] == 1
    assert period["inbound"]["totalAmount"] == "122.00"
    assert taxes["taxAmount"] == "22.00"
    assert "# Fiscal document INV-1" in markdown["markdown"]
    assert hints["documentMatchHints"]["number"] == "INV-1"
