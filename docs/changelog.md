# Changelog

## 0.3.0

- Added a Debian slim based Docker image for the MCP server.
- Added a GitHub Container Registry publishing workflow and package badge.
- Added Docker usage documentation for direct runs, MCP clients and local index volumes.

## 0.2.1

- Changed project licensing to PolyForm Internal Use License 1.0.0.
- Replaced the dynamic GitHub license badge with an explicit PolyForm license badge.

## 0.2.0

- Added LLM-friendly composed tools for invoice context, search, summaries, timelines, SDI notification explanations, VAT/counterparty reports, anomalies, duplicates and safe redaction.
- Added FatturaPA invoice and notification XML parsing helpers using `defusedxml`.
- Added optional local SQLite invoice index tools for faster local search and stats.
- Expanded documentation for composed tools and local index configuration.

## 0.1.0

- Initial read-only MCP server.
- Aruba auth token cache and refresh lifecycle.
- GET endpoint parity for Aruba v1 read-only invoice, notification, account and customer result status operations.
- MkDocs documentation and GitHub Actions workflows.
