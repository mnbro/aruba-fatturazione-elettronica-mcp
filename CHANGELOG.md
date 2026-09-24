# Changelog

## [0.4.1](https://github.com/mnbro/aruba-fatturazione-elettronica-mcp/compare/v0.4.0...v0.4.1) (2026-09-24)


### Bug Fixes

* **deps:** bump the python-security group across 1 directory with 9 updates ([067bc68](https://github.com/mnbro/aruba-fatturazione-elettronica-mcp/commit/067bc682da3954d435771a91895a02e18f46ba99))
* **deps:** bump the python-security group across 1 directory with 9 updates ([3cca67e](https://github.com/mnbro/aruba-fatturazione-elettronica-mcp/commit/3cca67eb55b1a1522c1ca0d2740a0a1d3e161e22))

## 0.4.0

- Added generic fiscal document helper tools for normalization, context, lifecycle status, risk checks, consistency checks, period summaries, tax summaries, fiscal event exports, Markdown exports and match hints.
- Clarified that the project remains an Aruba Fatturazione Elettronica domain MCP, not a cross-system business orchestrator.

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

- Initial read-only MCP server for Aruba Fatturazione Elettronica.
- Added Aruba auth token cache and refresh lifecycle.
- Added GET endpoint parity for read-only invoice, notification, account and customer result status operations.
- Added MkDocs documentation and GitHub Actions workflows.
