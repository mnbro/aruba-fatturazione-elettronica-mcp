# Changelog

## 0.2.1

- Changed project licensing to PolyForm Internal Use License 1.0.0.
- Replaced the dynamic GitHub license badge with an explicit PolyForm license badge.

## 0.2.0

- Added LLM-friendly composed tools for invoice context, search, summaries, timelines, SDI notification explanations, VAT/counterparty reports, anomalies, duplicates and safe redaction.
- Added FatturaPA invoice and notification XML parsing helpers using `defusedxml`.
- Added optional local SQLite invoice index tools for faster local search and stats.
- Expanded documentation for composed tools and local index configuration.

## 0.1.0

- Initial read-only MCP server for Aruba Fatturazione Elettronica.
- Added Aruba auth token cache and refresh lifecycle.
- Added GET endpoint parity for read-only invoice, notification, account and customer result status operations.
- Added MkDocs documentation and GitHub Actions workflows.
