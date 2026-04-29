# Docker

The project publishes a Debian slim based container image to GitHub Container Registry:

```text
ghcr.io/mnbro/aruba-fatturazione-elettronica-mcp:latest
```

The image runs as a non-root user and starts the MCP server over stdio. It does not contain credentials or persistent Aruba data.

## Run

Pass credentials as environment variables. Prefer inheriting them from the host or your secret manager instead of writing values directly into shell history.

```bash
docker run --rm -i \
  --env ARUBA_ENV=demo \
  --env ARUBA_USERNAME \
  --env ARUBA_PASSWORD \
  ghcr.io/mnbro/aruba-fatturazione-elettronica-mcp:latest
```

For the optional local invoice index, mount a writable directory and point `ARUBA_INDEX_DB_PATH` to it:

```bash
docker run --rm -i \
  --env ARUBA_ENV=demo \
  --env ARUBA_USERNAME \
  --env ARUBA_PASSWORD \
  --env ARUBA_INDEX_DB_PATH=/data/aruba-index.sqlite3 \
  --mount type=volume,src=aruba-mcp-data,dst=/data \
  ghcr.io/mnbro/aruba-fatturazione-elettronica-mcp:latest
```

## MCP Client Config

```json
{
  "mcpServers": {
    "aruba-fatturazione-elettronica-readonly": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "--env",
        "ARUBA_ENV=demo",
        "--env",
        "ARUBA_USERNAME",
        "--env",
        "ARUBA_PASSWORD",
        "ghcr.io/mnbro/aruba-fatturazione-elettronica-mcp:latest"
      ]
    }
  }
}
```

## Build Locally

```bash
docker build -t aruba-fatturazione-elettronica-mcp:local .
```

Use `:latest` for the current master build, `:vX.Y.Z` for a release, or `:sha-...` for an immutable commit build.
