# LLM Tools

These tools are built on top of the read-only Aruba GET endpoints. They do not upload invoices, send customer results, accept/reject invoices, update Aruba state or delete anything.

This project remains an Aruba Fatturazione Elettronica domain MCP. It exposes Aruba FE API capabilities, fiscal/e-invoicing helpers, safety checks and normalized exports. It does not orchestrate external systems, call other MCP servers or hardcode business workflows.

`aruba_sync_invoice_index` is the only tool with a local side effect: it writes a SQLite cache at `ARUBA_INDEX_DB_PATH` to make searches and reports faster. It still reads Aruba only through GET endpoints.

## Fiscal document helpers

These are the recommended LLM-friendly tools for agents that need stable fiscal document context without knowing every Aruba endpoint detail.

| Tool | Purpose | Sensitive read |
|---|---|---:|
| `normalize_fiscal_document` | Normalize one Aruba document into a stable JSON shape with document, amount, counterparty and raw reference fields. | yes |
| `get_document_context` | Return the raw document, normalized fields, lifecycle status, notifications, file hints and warnings. | yes |
| `fiscal_document_summary` | Produce a short structured summary and key facts for one document. | yes |
| `document_lifecycle_status` | Explain the current fiscal/SDI lifecycle stage: uploaded, sent, delivered, rejected, received, accepted, refused, stored or unknown. | yes |
| `document_risk_check` | Check technical/fiscal data-quality risks such as rejected status, missing counterparty IDs, inconsistent totals or missing downloads. | yes |
| `validate_fiscal_document_consistency` | Run technical consistency checks. This is not legal or tax advice. | yes |
| `counterparty_document_history` | Aggregate document history for a generic counterparty by name, VAT ID, fiscal code, email, PEC or SDI code. | no |
| `list_pending_or_problem_documents` | List documents that appear to need attention based on lifecycle and data-quality checks. | no |
| `fiscal_period_summary` | Summarize inbound/outbound document counts and totals for a period. | no |
| `tax_summary` | Informational tax summary for a period. It does not replace accounting review. | no |
| `export_fiscal_events` | Export standardized generic fiscal events derived from current Aruba data. | no |
| `export_document_markdown` | Export one document as generic Markdown for audit, archiving or documentation. | yes |
| `export_period_markdown` | Export a generic period report as Markdown. | no |
| `export_counterparty_markdown` | Export a generic counterparty document history report as Markdown. | no |
| `prepare_document_match_hints` | Return generic counterparty and document matching hints for external consumers. | yes |

Example:

```json
{
  "documentId": "IT01234567890_00001.xml",
  "direction": "outbound",
  "confirm_read": true
}
```

Period report example:

```json
{
  "fromDate": "2026-01-01",
  "toDate": "2026-01-31",
  "direction": "all",
  "includeTaxSummary": true,
  "includeProblemDocuments": true
}
```

Upload and customer outcome helpers are intentionally not exposed in this read-only release. They should only be added if explicit low-level Aruba mutating wrappers are introduced with separate confirmations, dry-run behavior, idempotency and audit logging.

## Invoice context and search

| Tool | Purpose | Sensitive read |
|---|---|---:|
| `aruba_get_invoice_full_context` | Fetch invoice payload, parsed XML, notifications, timeline, parties, amounts and optional PDD. | yes |
| `aruba_search_invoices` | Conceptual search across sent, received or both invoice lists. | conditional |
| `aruba_summarize_invoice` | Compact structured invoice summary from FatturaPA XML. | yes |
| `aruba_extract_invoice_fields` | Extract selected fields such as invoice number, supplier VAT, totals, payment method, CIG/CUP and SDI code. | yes |
| `aruba_get_invoice_timeline` | Build a timeline from invoice metadata and SDI notifications. | yes |
| `aruba_answer_invoice_question` | Collect structured context for an LLM to answer a scoped natural-language question. | no |

## Notifications and status

| Tool | Purpose | Sensitive read |
|---|---|---:|
| `aruba_explain_sdi_notifications` | Parse and explain SDI notifications in human-oriented terms. | yes |
| `aruba_find_missing_notifications` | Find invoices with no notifications or no interpreted final status. | yes |
| `aruba_invoice_status_report` | Aggregate sent/received/problematic invoice counts. | no |

## Accounting summaries

| Tool | Purpose | Sensitive read |
|---|---|---:|
| `aruba_vat_summary` | Totals grouped by VAT rate or nature from parsed XML. | yes |
| `aruba_counterparty_report` | Aggregate invoice count and totals by supplier/customer. | yes |
| `aruba_monthly_summary` | Monthly status summary. | no |
| `aruba_yearly_summary` | Yearly status summary. | no |
| `aruba_tax_breakdown_report` | Tax breakdown wrapper over VAT summary. | yes |
| `aruba_payment_terms_report` | Extract due dates, payment methods, IBAN and payment amounts from XML. | yes |
| `aruba_reconcile_sent_received` | Read-only sent vs received summary for a period. | no |
| `aruba_export_invoices_table` | Return invoice list data as a clean JSON table. | no |

## Data quality

| Tool | Purpose | Sensitive read |
|---|---|---:|
| `aruba_detect_invoice_anomalies` | Detect structural warnings, total mismatches and possible duplicates. | yes |
| `aruba_find_duplicate_invoices` | Find likely duplicate invoices by supplier VAT, number, date and gross total. | yes |
| `aruba_validate_invoice_xml_structure` | Run non-official sanity checks on a Base64 FatturaPA XML. | caller-provided data |
| `aruba_compare_invoice_totals` | Compare document total with line totals plus VAT. | caller-provided data |
| `aruba_find_invoices_without_pdf_or_xml` | Find list entries that appear to lack PDF/XML references. | no |
| `aruba_find_invoices_without_pdd` | Find list entries that appear to lack PDD references. | no |

## Parsing and privacy

| Tool | Purpose | Sensitive read |
|---|---|---:|
| `aruba_parse_invoice_xml` | Parse caller-provided Base64 FatturaPA XML. | caller-provided data |
| `aruba_parse_notification_xml` | Parse caller-provided Base64 SDI notification XML. | caller-provided data |
| `aruba_explain_invoice_type` | Explain common FatturaPA codes such as `TD01`, `TD04`, `N2.2`, `MP05` and `RF19`. | no |
| `aruba_redact_invoice` | Redact a supplied invoice payload for safer analysis. | caller-provided data |
| `aruba_get_safe_invoice_summary` | Fetch and summarize one invoice, then redact sensitive values. | yes |

## Local index

| Tool | Purpose | Aruba state |
|---|---|---:|
| `aruba_sync_invoice_index` | Populate/update a local SQLite index from Aruba read-only data. | unchanged |
| `aruba_search_invoice_index` | Search cached invoices by text, direction, VAT code or party name. | unchanged |
| `aruba_get_index_stats` | Show indexed invoice counts, directions and date coverage. | unchanged |

## Confirmation behavior

When `ARUBA_CONFIRM_SENSITIVE_READS=true`, tools that fetch XML, PDF, ZIP, PDD, notification files or unsigned files require `confirm_read=true`. If confirmation is missing, the server returns a structured error before calling Aruba.

Parsing tools that receive Base64/XML as input do not call Aruba, but the caller is still responsible for handling that data as sensitive fiscal information.
