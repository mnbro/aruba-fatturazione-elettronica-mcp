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

Or run the Debian slim based container image:

```bash
docker run --rm -i \
  --env ARUBA_ENV=demo \
  --env ARUBA_USERNAME \
  --env ARUBA_PASSWORD \
  ghcr.io/mnbro/aruba-fatturazione-elettronica-mcp:latest
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
