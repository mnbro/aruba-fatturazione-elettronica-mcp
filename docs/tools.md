# Tools

Every business tool has MCP annotations:

- `readOnlyHint=true`
- `destructiveHint=false`
- `idempotentHint=true`
- `openWorldHint=true`

Additional local tool:

- `aruba_auth_status`: returns environment, auth/WS hosts and token cache timing without tokens.
- `aruba_sync_invoice_index`: builds a local SQLite index from read-only Aruba GET data. This is not read-only to the local filesystem, but it never modifies Aruba.
- `aruba_search_invoice_index`: searches the local SQLite index.
- `aruba_get_index_stats`: reports local index coverage.

See [API Parity](api-parity.md) for the complete endpoint-to-tool table.

See [LLM Tools](llm-tools.md) for composed tools that parse, summarize, aggregate and redact invoice data.
