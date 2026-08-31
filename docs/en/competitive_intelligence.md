# Competitive Intelligence Usage

Competitive Intelligence collects public-source evidence once, records source coverage, and projects
the same immutable run into a briefing and timeline.

High-impact findings still require review against the linked primary source. A provider failure is
inconclusive and must not be presented as a zero-result finding.

## Install and activate

Use the guided installer for the complete Claude Code, Codex, or GitHub Copilot Skill + MCP plugin:

```bash
bash install.sh
```

For an MCP server without the Skill, install the Python distribution:

```bash
python -m pip install "open-pharma-plugins[competitive-intelligence]"
open-pharma-plugins-competitive-intelligence --version
```

Start a new task or restart the host after installing or updating a plugin so it loads the new tool
inventory.

## Configure

| Variable | Required | Purpose |
|---|---|---|
| `SERPER_API_KEY`, `TAVILY_API_KEY`, or `EXA_API_KEY` | For news | Web-search evidence |
| `OPENFDA_API_KEY` | No | Higher openFDA daily quota |
| `NCBI_API_KEY` | No | Higher PubMed request rate |
| `OPEN_PHARMA_CI_DATA_DIR` | No | Watchlist, cache, runs, and reports |
| `CI_CACHE_TTL_HOURS` | No | Cache lifetime; default `24` |

Put one `KEY=VALUE` pair on each line in `~/.open-pharma-plugins/config`. Comments must be on
separate lines because text after a value, including `#`, is parsed as part of that value.

openFDA allows 240 requests per minute in both modes. Its daily quota is 1,000 requests per IP
without a key and 120,000 requests per key with one.

`ci_status` reports whether a key is configured, not whether the provider accepts it. Credentials
are excluded from evidence URLs, cache identities, runs, and reports.

## Run one evidence collection

```text
ci_status
ci_track action="add" entity_type="drug" name="ExampleDrug" aliases=["examplemab"]
ci_track action="add" entity_type="company" name="Example Pharma"
ci_refresh entities=["ExampleDrug", "Example Pharma"]
```

Track a drug and its manufacturer as separate entities. Company aliases are company identities and
must not be inferred as products.

Inspect every returned source status, query, source URL, retrieval time, record count, cache state,
and limitation before interpreting findings.

## Interpret coverage

- `complete`: the bounded provider request completed; zero records is trustworthy for that request.
- `partial`: usable records exist, but some requested coverage is incomplete.
- `failed`: the provider or parser did not produce trustworthy records.
- `not_configured`: required provider configuration is absent.
- `not_applicable`: the source does not apply to that identity.

`failed` and `not_configured` are inconclusive. One successful source does not make another failed
source complete.

A larger `total_available` means the bounded run did not inspect every matching record. Report both
the returned count and the larger available count.

## Create artifacts from the same run

Reuse the `run_id` returned by `ci_refresh`:

```text
ci_report run_id="<run_id>"
ci_timeline run_id="<run_id>" months_back=12
```

The report and timeline manifests must contain the same `run_records_sha256`. Do not repeat
collection merely to render another view.

Legacy `ci_report focus=...` and `ci_timeline entities=[...]` selectors create one new run before
rendering. Prefer an explicit `ci_refresh` when reproducibility matters.

## Troubleshoot openFDA

If openFDA coverage is `failed` while DailyMed succeeds:

1. Treat openFDA as inconclusive; do not report that no FDA event exists.
2. Run `ci_status` and check whether `OPENFDA_API_KEY` is configured.
3. Verify the key in the provider account, or remove it from the config file and process environment.
4. Remember that an environment value overrides the file. Restart the host after changing either.
5. Confirm the expected key presence with `ci_status`; never print the key during diagnosis.
6. Create a new evidence run after correcting configuration; existing runs remain immutable.

The transport intentionally redacts credential-bearing request details. A generic HTTP failure can
therefore represent a rejected key, rate limit, or another provider response.

## Local data and review boundary

`OPEN_PHARMA_CI_DATA_DIR` defaults to `~/.open-pharma-plugins/competitive-intelligence` and contains
the watchlist, schema-v2 cache, immutable runs, and non-overwriting report directories.

Local permissions are not encryption, tenant isolation, retention policy, or enterprise access
control. Confirm high-impact findings against primary sources before commercial or medical use.

See [Configuration](configuration.md), [Data security](data_security.md), and [Testing](testing.md).
