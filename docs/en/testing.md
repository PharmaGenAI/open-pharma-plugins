# Testing

The default suite is deterministic and offline. It covers handlers, schemas, filesystem containment, secret-safe caching, compliance gates, documentation/manifests, console entry points, and real MCP initialize → tools/list → tools/call sessions. Mutable runtime data is isolated per test.

## Local gates

```bash
uv sync --all-extras --dev --locked
uv run pytest -m "not reachability"
uv run ruff format --check .
uv run ruff check .
uv run python scripts/check_manifests.py
uv run python scripts/gen_env_docs.py --check docs/en/configuration.md
uv run zizmor --pedantic .github/workflows
bash -n install.sh
```

Build and test the distributable artifact, not only the source tree:

```bash
uv build
uv run twine check dist/*
uv run python scripts/smoke_wheel.py dist/*.whl
```

The wheel smoke script creates an empty virtual environment, installs `open-pharma-plugins[all]`, invokes all six console scripts, and completes initialize/tools-list handshakes.

Competitive Intelligence provider tests use sanitized recorded fixtures and remain offline. Its run
and output tests verify one-pass collection, hash-checked immutable loading, coverage semantics,
formula-safe CSV, and DOM-safe report/timeline rendering.

When configuration guidance changes, keep `CONFIG_FIELDS`, `install.sh`, `.env.example`, and all
three localized configuration pages aligned. Keep comments on separate config lines in examples.

Live credential diagnosis must not log keys. Compare provider coverage, safe error codes, and a
credential-free request. A failed provider remains inconclusive even when another source succeeds.

## HCP batch checks

The HCP batch unit tests are offline. They cover CSV validation, path/output
preflight, provider request contracts, resume, schema-v2 manifests, and the stable formula-safe
summary CSV:

```bash
uv run --all-extras python -m pytest -q \
  tests/capabilities/test_hcp_batch.py \
  tests/capabilities/test_hcp_batch_csv.py
```

From a deliberate repository checkout, the bundled fixture can also exercise the source wrapper's
no-provider-call preflight:

```bash
uv run --all-extras python scripts/batch_enrich.py \
  --input-file src/capabilities/hcp-intelligence/open_pharma_plugins_hcp_intelligence/fixtures/sample_accounts.csv \
  --output-dir data/hcp-intelligence \
  --dry-run
```

This checkout-only command must not be presented as an installed marketplace-cache path. See
[HCP batch processing](hcp_batch.md) for the pinned installed command and live-provider boundary.

## Live reachability

Public-network checks are opt-in:

```bash
OPEN_PHARMA_RUN_REACHABILITY=1 uv run pytest -m reachability tests/integration/test_reachability.py
```

Do not enable reachability in the default CI suite. Provider results and rate limits are external state; these checks answer only whether a basic endpoint can be reached at that moment.

See the [Competitive Intelligence usage guide](competitive_intelligence.md) for the evidence-run
workflow and openFDA troubleshooting boundary.
