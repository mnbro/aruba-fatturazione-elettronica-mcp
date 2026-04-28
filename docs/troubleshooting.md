# Troubleshooting

## 401 responses

The client refreshes the token once and retries once. If refresh fails with invalid grant or 400/401, the auth manager signs in once.

## Rate limiting

Aruba auth is limited to 1 request/min/IP. Repeated starts with invalid credentials can quickly hit this limit.

## Sensitive read errors

If `ARUBA_CONFIRM_SENSITIVE_READS=true`, pass `confirm_read=true` for tools that can return Base64 XML/PDF/ZIP or notification content.

## Binary too large

Increase `ARUBA_MAX_BINARY_RESPONSE_BYTES` only after confirming your MCP client and logs can safely handle the payload size.
