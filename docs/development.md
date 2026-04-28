# Development

```bash
uv sync --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=aruba_fatturazione_elettronica_mcp --cov-report=xml --cov-report=term-missing
uv run mkdocs build --strict
```

The endpoint registry in `src/aruba_fatturazione_elettronica_mcp/endpoints.py` is the source of truth for Aruba GET parity. Tests fail if a business endpoint is not GET-only or if mutating tool names are introduced.
