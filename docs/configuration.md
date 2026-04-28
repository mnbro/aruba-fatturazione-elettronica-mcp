# Configuration

| Variable | Default | Description |
|---|---:|---|
| `ARUBA_ENV` | `demo` | `demo` or `production`. |
| `ARUBA_USERNAME` | required | Aruba username. |
| `ARUBA_PASSWORD` | required | Aruba password. |
| `ARUBA_TIMEOUT_SECONDS` | `30` | HTTP timeout. |
| `ARUBA_TOKEN_REFRESH_SKEW_SECONDS` | `120` | Refresh before access token expiry. |
| `ARUBA_CONFIRM_SENSITIVE_READS` | `false` | Require `confirm_read=true` for sensitive reads. |
| `ARUBA_REDACT_BASE64_IN_LOGS` | `true` | Redact encoded payloads. |
| `ARUBA_MAX_BINARY_RESPONSE_BYTES` | `10485760` | Maximum binary body before Base64 wrapping. |
| `ARUBA_AUDIT_LOG_ENABLED` | `true` | Emit redacted audit logs. |
| `ARUBA_HTTP_USER_AGENT` | package/version | Custom User-Agent. |
| `ARUBA_INDEX_DB_PATH` | `.aruba-invoice-index.sqlite3` | Local SQLite path used only by optional invoice index tools. |

Demo URLs:

- Auth: `https://demoauth.fatturazioneelettronica.aruba.it`
- WS: `https://demows.fatturazioneelettronica.aruba.it`

Production URLs:

- Auth: `https://auth.fatturazioneelettronica.aruba.it`
- WS: `https://ws.fatturazioneelettronica.aruba.it`
