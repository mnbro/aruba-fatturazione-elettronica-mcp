# LLM Tools

These tools are built on top of the read-only Aruba GET endpoints. They do not upload invoices, send customer results, accept/reject invoices, update Aruba state or delete anything.

`aruba_sync_invoice_index` is the only tool with a local side effect: it writes a SQLite cache at `ARUBA_INDEX_DB_PATH` to make searches and reports faster. It still reads Aruba only through GET endpoints.

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
