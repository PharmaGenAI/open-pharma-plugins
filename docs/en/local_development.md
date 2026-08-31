# Local Development

Run commands from the repository root. The lock file covers Python 3.10–3.13.

## Environment

```bash
uv sync --all-extras --dev --locked
```

Run a server directly from source:

```bash
uv run python -m open_pharma_plugins_territory_alignment --version
uv run python -m open_pharma_plugins_territory_alignment --check-system
uv run python -m open_pharma_plugins_territory_alignment
```

For a source MCP registration:

```bash
claude mcp add open-pharma-plugins-territory-alignment -- \
  uv run --directory "$(pwd)" python -m open_pharma_plugins_territory_alignment
```

Remove that registration after testing. Use `bash install.sh local` only in a dedicated clone because local mode deliberately rewrites tracked manifests; restore them with `bash install.sh local --restore`.

## Development loop

Write a failing regression test, implement the smallest complete fix, run the focused test, then run the release-facing gates in [Testing](testing.md). Unit tests isolate mutable capability directories so test runs do not write into the checkout or your home directory.
