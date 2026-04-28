# Quickstart

```bash
uv sync
cp .env.example .env
```

Edit `.env`:

```env
ARUBA_ENV=demo
ARUBA_USERNAME=...
ARUBA_PASSWORD=...
```

Run:

```bash
uv run aruba-fatturazione-elettronica-mcp
```

MCP client config:

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
