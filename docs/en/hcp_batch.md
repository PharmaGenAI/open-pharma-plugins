# HCP Batch Processing and CSV Review

Use the packaged HCP batch console when a commercial user supplies an account CSV path and an output
directory. The installed workflow is independent of a repository checkout and pins the HCP
Intelligence `1.0.2` source.

## Input and preflight

The CSV header is:

```csv
id,name,specialty,country,account_type,institution
```

All six columns are required. `id`, `name`, `country`, and `account_type` must contain values;
`specialty` and `institution` may be blank. IDs must be unique, contain no control characters, and be
safe as file names. `account_type` is `HCP` or `HCO`. UTF-8 files with or without a BOM are accepted.

Resolve both user paths to absolute paths, reject control characters, and quote each as one shell
argument. Never use command substitution or `eval` with a user path. Preserve only filters the user
actually supplied: give every requested ID its own quoted argument after `--ids`, and preserve the
supplied values for `--country` and `--account-type`. This all-filter example starts with a dry run:

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

The dry run validates the complete file and output location, shows the selected IDs, count, HCP/HCO
split, provider/model settings, and planned artifacts, makes no external calls, and does not create
the output directory. Compare those selected IDs, count, and HCP/HCO split with the requested scope,
not just with an expected count. Any omitted or changed filter, extra or missing ID, or count/type
mismatch stops execution until the command is corrected and a new dry run succeeds. If the user
explicitly requested processing and the exactly matched selection contains at most ten accounts,
execution can follow. Paths alone require confirmation. More than ten accounts require explicit
provider-call approval; clear advance approval in the original request satisfies that requirement.

## Approved synthesis run

Configure `OPENROUTER_API_KEY` and verify that the selected data may be sent to the provider. Copy the
validated dry-run command, remove only `--dry-run`, and add `--synthesize`, `--resume`, and
`--concurrency 3`; keep the source, paths, and filters argument-for-argument identical:

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

Do not fall back to `main`, another tag, an unpinned package, or a checkout if `uvx` cannot install
the pinned source. Raw evidence collection can use the same console without `--synthesize`; it does
not require an LLM key, although web search needs a configured Serper, Tavily, or Exa provider.

## Output contract

Each selected account has a canonical `<id>.json` evidence artifact. `batch_summary.csv` is a
business-review projection, not the canonical data store. Its stable schema version 1 contains these
27 columns in this order:

```text
account_id, account_type, input_name, input_specialty, input_country,
input_institution, status, profile_validated, profile_completeness,
profile_name, profile_specialty, profile_country, current_title,
organization_type, affiliations, qualifications, research_or_clinical_focus,
professional_roles, key_publication_count, clinical_trial_count,
active_grant_count, congress_activity_count, source_count, source_urls,
tools_failed, error, json_file
```

The CSV uses a UTF-8 BOM and CRLF records for spreadsheet compatibility. Multi-value fields use
` | ` in their original order after duplicate removal. Every textual cell beginning with `=`, `+`,
`-`, or `@` is prefixed with an apostrophe to neutralize spreadsheet formulas. Numeric fields remain
numeric when a validated profile provides them; unavailable values are blank. Review the referenced
JSON and sources before acting on any flattened CSV value.

`batch_manifest.json` uses schema version 2. It records the input path and SHA-256, timestamps,
effective synthesis endpoint/model/reasoning/timeout/concurrency, selected and status counts,
per-account artifact state, and CSV status/path/schema/row count/SHA-256. It does not store the API
key or duplicate account names.

## Output-directory and resume safety

The output must be a directory. A new or empty directory is accepted; a non-empty directory requires
`--resume`. On supported POSIX systems, created directories use `0700` and files use `0600`.
Resume skips usable account JSON, reuses raw evidence for requested synthesis, and reprocesses
malformed or invalid artifacts. It preserves unrelated directory entries, but atomically regenerates
selected `<id>.json` files as needed, `batch_summary.csv`, and `batch_manifest.json`.

Do not add manual review notes directly to generated CSV, JSON, or manifest files: a resumed run does
not preserve those edits. Copy the CSV to a separate review workbook or system of record first.

## Completion and exit codes

Monitor the command until it exits, then report completed, partial, failed, and skipped counts plus
the absolute output-directory, summary-CSV, and manifest paths.

- Exit `0`: dry run or execution completed without partial/failed accounts or CSV export failure;
  an empty filtered selection also exits `0` and produces no artifacts.
- Exit `1`: one or more accounts are partial/failed, or CSV export failed. Available JSON and the
  schema-v2 manifest are preserved for inspection and deliberate resume.
- Exit `2`: command-line or preflight usage error, such as invalid CSV, unsafe paths, invalid values,
  or non-empty output without `--resume`; no provider calls are made.

## Provider and human-review boundary

Raw search sends necessary query terms to configured public APIs/search providers but does not call
an LLM. Synthesis sends selected account fields and gathered evidence to `OPENROUTER_BASE_URL` and
requires `OPENROUTER_API_KEY`. Extraction and synthesis default to DeepSeek V4 Flash
(`deepseek/deepseek-v4-flash-0731`), `high` reasoning effort, a 120-second per-request timeout,
strict structured output, and zero SDK retries. Confirm provider
approval, logging/retention terms, and data minimization before execution. Human reviewers must still
confirm HCP/HCO identity, source provenance, completeness, and every partial/failed record.

## Checkout development

Only in a deliberate repository checkout, test the source wrapper separately:

```bash
uv run --all-extras python scripts/batch_enrich.py \
  --input-file src/capabilities/hcp-intelligence/open_pharma_plugins_hcp_intelligence/fixtures/sample_accounts.csv \
  --output-dir data/hcp-intelligence \
  --ids "HCP-SG-001" "HCP-SG-002" \
  --country "Singapore" \
  --account-type "HCP" \
  --dry-run
```

After the same exact-scope gate and approval, execute by copying that checkout command, removing only
`--dry-run`, and adding the execution flags:

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

The repository script is not present in an installed marketplace plugin cache and is never a
fallback for a failed pinned installed command.
