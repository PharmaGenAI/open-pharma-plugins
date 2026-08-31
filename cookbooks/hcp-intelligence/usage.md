# HCP Intelligence

Search public sources for HCP/HCO evidence and persist a structured enrichment against a demo CRM account.

## Tools available

| Tool | Purpose |
|---|---|
| `list_accounts` | List and filter demo CRM accounts |
| `get_account` | Retrieve one account and its saved enrichment |
| `update_account` | Save a structured profile and enrichment status |
| `search_publications` | Search PubMed |
| `search_guidelines` | Search guideline and advisory-role evidence |
| `search_clinical_trials` | Search ClinicalTrials.gov |
| `search_grants` | Search NIH RePORTER and configured web fallback |
| `search_orcid` | Search ORCID |
| `search_congresses` | Search congress participation evidence |
| `search_hcp_web` | Search configured web providers for an HCP |
| `search_hco_web` | Search configured web providers for an HCO |

## Install and configure

```bash
pip install "open-pharma-plugins[hcp-intelligence]"
```

`NCBI_API_KEY` is optional. Web tools require `SERPER_API_KEY`, `TAVILY_API_KEY`, or `EXA_API_KEY`; `OPEN_PHARMA_SEARCH_BACKEND` selects the provider. Mutable enrichments default to `~/.open-pharma-plugins/hcp-intelligence` and can be moved with `OPEN_PHARMA_HCP_DATA_DIR`.

## Example workflow

```text
list_accounts account_type="hcp" status="pending"
get_account account_id="HCP001"
search_orcid name="Sarah Chen" affiliation="Example University"
search_publications author_name="Sarah Chen" affiliation="Example University"
search_clinical_trials investigator_name="Sarah Chen" country="US"
search_grants pi_name="Sarah Chen" institution="Example University"
search_congresses name="Sarah Chen" specialty="oncology"
search_hcp_web name="Sarah Chen" institution="Example University"
update_account account_id="HCP001" status="complete" profile_json="<evidence-backed JSON>"
```

The agent synthesizes the profile; there is no separate `build_profile` tool. Preserve source URLs, access dates, disambiguation notes, and confidence in `profile_json`.

## Batch enrichment from CSV

For an installed plugin, share an input CSV path and output-directory path. Resolve and quote both
paths. Preserve only the filters the user supplied: pass each requested ID as its own quoted value
after `--ids`, and preserve supplied `--country` and `--account-type` values. The following all-filter
example validates the batch without network or model calls:

```bash
uvx --from \
  "open-pharma-plugins[hcp-intelligence-synth] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-hcp-intelligence-v1.0.2" \
  open-pharma-plugins-hcp-batch \
  --input-file "/absolute/path/accounts.csv" \
  --output-dir "/absolute/path/results" \
  --ids "HCP-SG-001" "HCP-SG-002" \
  --country "Singapore" \
  --account-type "HCP" \
  --dry-run
```

The input file must use this header:

```csv
id,name,specialty,country,account_type,institution
```

`id`, `name`, `country`, and `account_type` must be non-empty; `account_type` must be `HCP` or
`HCO`. IDs must be unique and safe as file names. Keep `specialty` and `institution` columns even
when a value is blank. The bundled
`open_pharma_plugins_hcp_intelligence/fixtures/sample_accounts.csv` is the template.

Compare the printed selected IDs, count, and HCP/HCO split with the requested scope. Any changed or
omitted filter, extra or missing ID, or count/type mismatch stops execution until the command is
corrected and a new dry run succeeds. The at-most-ten automatic proceed rule applies only after exact
scope equality.

After that gate and the required provider approval, copy the validated command, remove only
`--dry-run`, and add the execution flags shown below. Paths and filter arguments remain identical;
`--resume` safely supports both a new directory and continuation of an existing run:

```bash
uvx --from \
  "open-pharma-plugins[hcp-intelligence-synth] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-hcp-intelligence-v1.0.2" \
  open-pharma-plugins-hcp-batch \
  --input-file "/absolute/path/accounts.csv" \
  --output-dir "/absolute/path/results" \
  --ids "HCP-SG-001" "HCP-SG-002" \
  --country "Singapore" \
  --account-type "HCP" \
  --synthesize \
  --resume \
  --concurrency 3
```

Raw public-source collection does not use an LLM or require `OPENROUTER_API_KEY`. Omit
`--synthesize` when raw evidence is the intended output. Web search still needs an approved Serper,
Tavily, or Exa configuration. Synthesis requires `OPENROUTER_API_KEY` and sends selected account
fields plus gathered evidence to the configured OpenRouter endpoint.

If the user explicitly asked to run or process at most ten validated accounts, execution may follow
the dry run. Paths alone require confirmation; more than ten accounts require explicit provider-call
approval, although clear advance approval in the original request need not be repeated. Monitor the
process to exit and report completed/partial/failed/skipped counts plus the absolute output directory,
`batch_summary.csv`, and `batch_manifest.json` paths. Never fall back to another revision if the
pinned install fails.

For deliberate source-checkout development only, use the repository wrapper:

```bash
uv run --all-extras python scripts/batch_enrich.py \
  --input-file src/capabilities/hcp-intelligence/open_pharma_plugins_hcp_intelligence/fixtures/sample_accounts.csv \
  --output-dir data/hcp-intelligence \
  --ids "HCP-SG-001" "HCP-SG-002" \
  --country "Singapore" \
  --account-type "HCP" \
  --dry-run
```

After the same exact-scope gate, execute by copying that checkout command, removing `--dry-run`, and
adding the execution flags:

```bash
uv run --all-extras python scripts/batch_enrich.py \
  --input-file src/capabilities/hcp-intelligence/open_pharma_plugins_hcp_intelligence/fixtures/sample_accounts.csv \
  --output-dir data/hcp-intelligence \
  --ids "HCP-SG-001" "HCP-SG-002" \
  --country "Singapore" \
  --account-type "HCP" \
  --synthesize \
  --resume \
  --concurrency 3
```

That script is not available in a marketplace plugin cache and is never a fallback for a failed
pinned installed command. See the detailed
[HCP batch guide](../../docs/en/hcp_batch.md) for the CSV schema, output safety, resume behavior,
exit codes, and multilingual links.

The default synthesis provider uses OpenRouter at `https://openrouter.ai/api/v1` with the pinned
`deepseek/deepseek-v4-flash-0731` model. `OPENROUTER_BASE_URL` overrides the endpoint; use
`--base-url`, `--api-key-env`, or `--model` for a deliberate per-run override. The Python `openai`
package is the protocol-compatible client and does not require an OpenAI account or
`OPENAI_API_KEY`. Extraction and synthesis use `high` reasoning effort by default; use
`--reasoning-effort xhigh` for an explicit higher-effort run. Each model request has a 120-second
timeout by default (`--synthesis-timeout-seconds` overrides it), and SDK automatic retries are
disabled so an operator can inspect and deliberately resume a failed account. Search-provider
settings remain independent. The request sends the capability's Pydantic profile schema as a strict
structured-output contract and requires an OpenRouter route that supports it; local Pydantic
validation remains the persistence gate.

Each account produces canonical `<id>.json`. The stable, formula-safe 27-column
`batch_summary.csv` is a business review projection, while schema-v2 `batch_manifest.json` records
the input SHA-256, settings, counts, account statuses, and CSV hash. Resume reuses valid JSON and
regenerates the CSV and manifest, so manual edits to generated outputs are not preserved. A partial,
failed, or CSV-export-failed run exits non-zero while preserving available JSON and manifest evidence.

Use `--country`, `--account-type`, or `--ids` to stage a large run. User-curated files do not write
to the bundled demo enrichment store unless `--write-back` is explicitly supplied. Review identity
disambiguation, sources, completeness, and every partial/failed record before commercial use. With
`--synthesize`, the account fields and gathered evidence are sent to the configured OpenRouter-
compatible endpoint; confirm that provider use is approved for the data before starting the run.

## Data boundary

The bundled account CSV is fictional demo data. Public-source results may be incomplete or refer to a namesake; verify identity before operational use and follow applicable privacy, consent, and retention requirements.
