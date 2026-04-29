# Security

Fiscal XML, PDF, ZIP and Base64 payloads can contain sensitive tax data. Logs redact passwords, tokens, authorization headers, Base64-like payloads, XML/PDF/ZIP payloads and likely VAT/fiscal identifiers.

Sensitive reads include file/PDF invoice content, ZIP/PDD downloads, notification files and unsigned files. When `ARUBA_CONFIRM_SENSITIVE_READS=true`, these tools return a structured error until `confirm_read=true` is passed.

Use dedicated API credentials where possible. Do not pass credentials in query strings. Verify that Web Services/API access is enabled for your Aruba contract.

Aruba documented rate limits include:

- Auth: 1 request/min/IP.
- Find sent invoices: 12 requests/min/IP.
- Find received invoices: 12 requests/min/IP.
- Find sent notifications: 12 requests/min/IP.
- Find received notifications: 12 requests/min/IP.

This server implements per-process async rate limiting and token caching. Multi-process deployments still need external coordination if they share the same public IP.

## License

This project is licensed under the [PolyForm Internal Use License 1.0.0](https://polyformproject.org/licenses/internal-use/1.0.0).

You may use and modify it for your own internal business operations, including commercial internal use. You may not redistribute it, resell it, sublicense it, or offer it as a productized service to third parties.
