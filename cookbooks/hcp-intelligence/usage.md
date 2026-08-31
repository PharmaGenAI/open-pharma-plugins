# HCP Intelligence

Search public sources for HCP and HCO evidence, build a structured profile, and persist reviewed
enrichment against a demo CRM account or export a batch for business review.

## Tools

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

For exact schemas, check the installed Skill or MCP tool list.

## Install

Register the marketplace once, then install the Skill and MCP server for your agent host. If the
marketplace is already registered, skip its `add` command and refresh it before installing a newly
released capability.

The marketplace catalog pins this capability to its current immutable release tag.

### Claude Code

```bash
claude plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
claude plugin install open-pharma-plugins-hcp-intelligence@open-pharma-plugins
```

### Codex

```bash
codex plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
codex plugin marketplace upgrade open-pharma-plugins
codex plugin add open-pharma-plugins-hcp-intelligence@open-pharma-plugins
```

### GitHub Copilot CLI

```bash
copilot plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
copilot plugin install open-pharma-plugins-hcp-intelligence@open-pharma-plugins
```

### Python MCP server only

The Python distribution installs the server without the companion Skill:

```bash
python -m pip install "open-pharma-plugins[hcp-intelligence]"
```

The guided installer, local-checkout setup, and rollback instructions are in the shared
[installation guide](../../docs/en/installation.md).

## Configure

`NCBI_API_KEY` is optional. Web searches require `SERPER_API_KEY`, `TAVILY_API_KEY`, or
`EXA_API_KEY`; `OPEN_PHARMA_SEARCH_BACKEND` selects a provider or uses the first configured provider
in Serper, Tavily, Exa order. Mutable demo-account enrichments default to
`~/.open-pharma-plugins/hcp-intelligence` and can be moved with `OPEN_PHARMA_HCP_DATA_DIR`.

Raw public-source collection does not require a model key. Optional batch profile synthesis requires
`OPENROUTER_API_KEY` and sends selected account fields plus gathered evidence to the configured
OpenRouter-compatible endpoint.

## Recommended workflow

1. Identify the HCP or HCO with enough context to distinguish namesakes: full name, institution,
   specialty, country, or a known identifier.
2. Search the relevant primary public sources and use web search only where it adds missing context.
3. Reconcile conflicts and record source URLs, access dates, identity notes, evidence gaps, and
   confidence. A zero result is not proof that an activity does not exist.
4. Synthesize the profile from the gathered evidence. There is no separate `build_profile` tool.
5. Review the profile before updating a demo account or exporting batch results for business use.

## Example requests

```text
Build an evidence-backed profile of Dr Sarah Chen at Example University, focusing on oncology
publications, clinical trials, grants, guidelines, and congress activity. Flag any identity
ambiguity or evidence gaps.

@accounts.csv
Enrich the Singapore oncology HCPs in this file and create a reviewable summary. Dry-run the exact
scope before any provider calls and do not write results back to the demo CRM.

Profile Example Cancer Centre as an HCO. Summarize its research activity, relevant trials, clinical
focus, and public affiliations, with links and confidence notes for every section.
```

## Outputs and safeguards

Single-account enrichments persist structured `profile_json` only when `update_account` is called.
Preserve source URLs, access dates, disambiguation notes, confidence, and completeness. Public-source
results can be incomplete, outdated, or about a namesake; verify identity before operational use.

Batch runs create canonical `<id>.json` records, a stable formula-safe 27-column
`batch_summary.csv`, and a schema-v2 `batch_manifest.json`. The manifest records input and output
hashes, settings, counts, and account statuses. Resume reuses valid JSON and regenerates the CSV and
manifest, so manual edits to generated outputs are not preserved. Partial, failed, or CSV-export-
failed runs exit non-zero while preserving available evidence.

The bundled account data is fictional. Follow applicable privacy, consent, access, provider-use,
and retention requirements before processing real HCP or HCO data.

## Advanced usage

### Direct tool workflow

```text
list_accounts account_type="hcp" status="pending"
get_account account_id="HCP001"
search_orcid name="Sarah Chen" affiliation="Example University"
search_publications author_name="Sarah Chen" affiliation="Example University"
search_clinical_trials investigator_name="Sarah Chen" country="US"
search_grants pi_name="Sarah Chen" institution="Example University"
search_congresses name="Sarah Chen" specialty="oncology"
search_hcp_web name="Sarah Chen" institution="Example University"
update_account account_id="HCP001" status="enriched"
  profile_json="<evidence-backed JSON>"
```

### Batch enrichment from CSV

The packaged batch console uses the `hcp-intelligence-synth` extra when model synthesis is needed.
Dry-run the exact input, output directory, IDs, country, and account type without network or model
calls:

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

The CSV must use this header:

```csv
id,name,specialty,country,account_type,institution
```

`id`, `name`, `country`, and `account_type` must be non-empty; `account_type` must be `HCP` or
`HCO`. IDs must be unique and safe as filenames. Keep the optional `specialty` and `institution`
columns even when values are blank.

Compare the printed IDs, count, and HCP/HCO split with the requested scope. Any changed or omitted
filter, extra or missing ID, or count/type mismatch stops execution. After an exact dry run and the
required provider approval, copy the command, remove only `--dry-run`, and add:

```text
--synthesize --resume --concurrency 3
```

Omit `--synthesize` when raw evidence is the intended output. Paths alone do not authorize provider
calls. If the user explicitly asked to process at most ten validated accounts, execution may follow
the matching dry run; more than ten accounts requires explicit provider-call approval. User-curated
files do not write to the bundled demo store unless `--write-back` is explicitly supplied.

`--resume` supports both a new output directory and continuation of an existing run. Monitor the
process through exit and report completed, partial, failed, and skipped counts plus the absolute
output, `batch_summary.csv`, and `batch_manifest.json` paths. Never substitute another revision if
the pinned installation fails.

The default synthesis route uses OpenRouter at `https://openrouter.ai/api/v1` with the pinned
`deepseek/deepseek-v4-flash-0731` model and high reasoning effort. The request uses the capability's
Pydantic profile schema as a strict structured-output contract, and local Pydantic validation is the
persistence gate. Requests have a 120-second default timeout, SDK automatic retries are disabled,
and `--reasoning-effort xhigh` or `--synthesis-timeout-seconds` can deliberately override the
defaults. Inspect and deliberately resume failures. `OPENROUTER_BASE_URL` or `--base-url` selects a
different approved OpenRouter-compatible endpoint.

The repository-only `scripts/batch_enrich.py` wrapper is for source-checkout development and is not
available in a marketplace plugin cache. It is never a fallback for a failed pinned install. See the
detailed [HCP batch guide](../../docs/en/hcp_batch.md) for filters, execution flags, output safety,
resume behavior, exit codes, and source-checkout examples.
