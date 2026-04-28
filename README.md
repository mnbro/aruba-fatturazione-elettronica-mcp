# Aruba Fatturazione Elettronica MCP Read-only

[![PyPI version](https://img.shields.io/pypi/v/aruba-fatturazione-elettronica-mcp.svg)](https://pypi.org/project/aruba-fatturazione-elettronica-mcp/)
[![Python versions](https://img.shields.io/pypi/pyversions/aruba-fatturazione-elettronica-mcp.svg)](https://pypi.org/project/aruba-fatturazione-elettronica-mcp/)
[![License](https://img.shields.io/github/license/mnbro/aruba-fatturazione-elettronica-mcp.svg)](LICENSE)
[![CI](https://github.com/mnbro/aruba-fatturazione-elettronica-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/mnbro/aruba-fatturazione-elettronica-mcp/actions/workflows/ci.yml)
[![Ruff](https://img.shields.io/badge/lint-ruff-46a2f1)](https://docs.astral.sh/ruff/)
[![mypy](https://img.shields.io/badge/type%20checked-mypy-blue)](https://mypy-lang.org/)
[![Coverage](https://img.shields.io/codecov/c/github/mnbro/aruba-fatturazione-elettronica-mcp.svg)](https://codecov.io/gh/mnbro/aruba-fatturazione-elettronica-mcp)
[![GitHub release](https://img.shields.io/github/v/release/mnbro/aruba-fatturazione-elettronica-mcp)](https://github.com/mnbro/aruba-fatturazione-elettronica-mcp/releases)
[![Downloads](https://img.shields.io/pypi/dm/aruba-fatturazione-elettronica-mcp.svg)](https://pypistats.org/packages/aruba-fatturazione-elettronica-mcp)

Documentation: https://mnbro.github.io/aruba-fatturazione-elettronica-mcp/

This MCP server exposes read-only Aruba Fatturazione Elettronica API operations. It does not send invoices, upload files, accept or reject invoices, update resources, delete resources, or expose business POST/PUT/PATCH/DELETE operations.

Authentication uses `POST /auth/signin` because Aruba requires it for signin and refresh token lifecycle. Those POST requests are internal auth operations only.

Badges for PyPI, release, downloads and Codecov become active after the first publication/release and Codecov setup.

## Install

```bash
uv sync
cp .env.example .env
```

Set `ARUBA_ENV=demo` first unless you have a specific reason to start in production.

## Run

```bash
uv run aruba-fatturazione-elettronica-mcp
```

`python -m aruba_fatturazione_elettronica_mcp` is also supported.

## MCP client config

```json
{
  "mcpServers": {
    "aruba-fatturazione-elettronica-readonly": {
      "command": "uvx",
      "args": ["aruba-fatturazione-elettronica-mcp"],
      "env": {
        "ARUBA_ENV": "demo",
        "ARUBA_USERNAME": "...",
        "ARUBA_PASSWORD": "..."
      }
    }
  }
}
```

## API parity table

| Aruba section | Aruba endpoint | MCP tool | Implemented | Sensitive read | Binary |
|---|---|---|---:|---:|---:|
| Auth | `GET /auth/userInfo` | `aruba_get_user_info` | yes | no | no |
| Auth | `GET /auth/multicedenti` | `aruba_list_multicedenti` | yes | no | no |
| Auth | `GET /auth/multicedenti/{id}` | `aruba_get_multicedente_by_id` | yes | no | no |
| Sent invoices | `GET /services/invoice/out/findByUsername` | `aruba_find_sent_invoices` | yes | no | no |
| Sent invoices | `GET /services/invoice/out/getByFilename` | `aruba_get_sent_invoice_by_filename` | yes | conditional | no |
| Sent invoices | `GET /services/invoice/out/getZipByFilename` | `aruba_get_sent_invoice_zip_by_filename` | yes | yes | yes |
| Sent invoices | `GET /services/invoice/out/{invoiceId}` | `aruba_get_sent_invoice_by_id` | yes | conditional | no |
| Sent invoices | `GET /services/invoice/out/getByIdSdi` | `aruba_get_sent_invoice_by_sdi_id` | yes | conditional | no |
| Sent invoices | `GET /services/invoice/out/pdd` | `aruba_get_sent_invoice_pdd` | yes | yes | yes |
| Received invoices | `GET /services/invoice/in/findByUsername` | `aruba_find_received_invoices` | yes | no | no |
| Received invoices | `GET /services/invoice/in/getByFilename` | `aruba_get_received_invoice_by_filename` | yes | conditional | no |
| Received invoices | `GET /services/invoice/in/getZipByFilename` | `aruba_get_received_invoice_zip_by_filename` | yes | yes | yes |
| Received invoices | `GET /services/invoice/in/{invoiceId}` | `aruba_get_received_invoice_by_id` | yes | conditional | no |
| Received invoices | `GET /services/invoice/in/getByIdSdi` | `aruba_get_received_invoice_by_sdi_id` | yes | conditional | no |
| Received invoices | `GET /services/invoice/in/getInvoiceWithUnsignedFile` | `aruba_get_received_invoice_unsigned_file` | yes | yes | no |
| Received invoices | `GET /services/invoice/in/pdd` | `aruba_get_received_invoice_pdd` | yes | yes | yes |
| Sent notifications | `GET /services/notification/out/getByFilename` | `aruba_get_sent_notification_by_filename` | yes | yes | no |
| Sent notifications | `GET /services/notification/out/getByInvoiceFilename` | `aruba_get_sent_notifications_by_invoice_filename` | yes | yes | no |
| Sent notifications | `GET /services/notification/out/{invoiceId}` | `aruba_get_sent_notifications_by_invoice_id` | yes | yes | no |
| Received notifications | `GET /services/notification/in/getByFilename` | `aruba_get_received_notification_by_filename` | yes | yes | no |
| Received notifications | `GET /services/notification/in/getByInvoiceFilename` | `aruba_get_received_notifications_by_invoice_filename` | yes | yes | no |
| Received notifications | `GET /services/notification/in/{invoiceId}` | `aruba_get_received_notifications_by_invoice_id` | yes | yes | no |
| Customer result | `GET /services/invoice/in/sendEsitoCommittente/{filename}` | `aruba_get_customer_result_status` | yes | no | no |

## Security notes

Some responses can contain fiscal XML, PDF, ZIP or Base64 payloads with sensitive tax data. Credentials are read only from environment variables. Tokens, passwords, Base64, XML, PDF, ZIP and likely VAT/fiscal codes are redacted from logs.

Use dedicated credentials where possible, not shared admin accounts. Verify contractually that Aruba Web Services/API access is enabled for your account. Start with `ARUBA_ENV=demo`.

## Attribution

Inspired by MIT-licensed repositories:

- `zangetsu02/fatturazione-elettronica-aruba`
- `andreafalzetti/node-fatturazione-elettronica-aruba`
- `mnbro/listmonk-mcp-bridge`

No code was copied verbatim in this initial implementation. Preserve MIT notices if future changes adapt source code.
