# Competitive Intelligence

Track explicit drug and company identities, collect public-source evidence once, and generate
reproducible briefings and timelines from the same immutable run.

## Tools

| Tool | Purpose |
|---|---|
| `ci_status` | Show non-secret provider, cache, and watchlist readiness |
| `ci_track` | Add, remove, or list watchlist entities |
| `ci_refresh` | Collect one immutable evidence run |
| `ci_scan_trials` | Search ClinicalTrials.gov |
| `ci_trial_detail` | Retrieve one full trial record |
| `ci_scan_regulatory` | Search openFDA and DailyMed |
| `ci_scan_news` | Search a configured web provider |
| `ci_scan_publications` | Search PubMed |
| `ci_extract_events` | Extract event candidates from a document |
| `ci_landscape` | Build a therapeutic-area landscape |
| `ci_report` | Write immutable HTML, JSON, and CSV briefing artifacts |
| `ci_timeline` | Write an immutable, calendar-filtered HTML timeline |

For exact schemas, check the installed Skill or MCP tool list.

## Install

Register the marketplace once, then install the Skill and MCP server for your agent host. If the
marketplace is already registered, skip its `add` command and refresh it before installing a newly
released capability.

The marketplace catalog pins this capability to its current immutable release tag.

### Claude Code

```bash
claude plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
claude plugin install open-pharma-plugins-competitive-intelligence@open-pharma-plugins
```

### Codex

```bash
codex plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
codex plugin marketplace upgrade open-pharma-plugins
codex plugin add open-pharma-plugins-competitive-intelligence@open-pharma-plugins
```

### GitHub Copilot CLI

```bash
copilot plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
copilot plugin install open-pharma-plugins-competitive-intelligence@open-pharma-plugins
```

### Python MCP server only

The Python distribution installs the server without the companion Skill:

```bash
python -m pip install "open-pharma-plugins[competitive-intelligence]"
```

The guided installer, local-checkout setup, and rollback instructions are in the shared
[installation guide](../../docs/en/installation.md).

## Configure

News search requires `SERPER_API_KEY`, `TAVILY_API_KEY`, or `EXA_API_KEY`.
`OPEN_PHARMA_SEARCH_BACKEND` can select the provider; `auto` chooses the first configured provider
in Serper, Tavily, Exa order. `OPENFDA_API_KEY` and `NCBI_API_KEY` are optional.

`OPEN_PHARMA_CI_DATA_DIR` defaults to `~/.open-pharma-plugins/competitive-intelligence`, and
`CI_CACHE_TTL_HOURS` defaults to 24. Credentials are transport-only and are excluded from persisted
cache identities and evidence URLs. Keep configuration comments on separate lines because text
after a value, including `#`, is parsed as part of that value.

openFDA permits 240 requests per minute. The daily quota is 1,000 requests per IP without a key and
120,000 requests with a key. `ci_status` reports key presence, not provider acceptance.

## Recommended workflow

1. Check provider readiness, then define explicit drug and company identities with useful aliases.
2. Add those identities to the watchlist or provide them directly for a refresh.
3. Collect one immutable evidence run and inspect every source's coverage status and limitations.
4. Reuse the returned `run_id` to create the briefing and timeline without refetching.
5. Confirm high-impact findings against the linked primary sources before commercial or medical
   decisions.

The report and timeline manifests must contain the same `run_records_sha256`. Legacy report and
timeline selectors remain available, but each creates a new run before rendering.

## Example requests

```text
Track ExampleDrug and Example Pharma, refresh public evidence, and create a competitive briefing
and 12-month timeline. Separate source failures from trustworthy zero-result findings.

Compare the phase 3 NSCLC pipeline for Example Pharma, Sample Therapeutics, and Demo Bio. Show the
trial sponsors, status, interventions, and linked primary records.

@competitor-release.pdf
Extract possible trial, regulatory, label, pricing, market-access, and partnership events. Treat
them as candidates and show the supporting passages for analyst review.
```

## Outputs and safeguards

Coverage must be interpreted before findings:

- `complete` can include a trustworthy zero-result response.
- `partial` means only part of the requested coverage is trustworthy.
- `failed` and `not_configured` are inconclusive, not zero-result findings.
- `not_applicable` means the source does not apply to the tracked identity.

Reports distinguish returned records from a provider's larger `total_available`. If openFDA is
`failed` while DailyMed succeeds, verify or remove `OPENFDA_API_KEY` from the winning configuration
source. Process environment values override the config file. Restart the host, check `ci_status`,
and create a new run. Do not interpret the failed source as evidence that no FDA event exists.

The configured data directory contains `watchlist.json`, `cache/`, immutable `runs/<run_id>/`, and
non-overwriting `reports/<run_id>/<artifact_id>/` directories. Report CSV text is formula-neutralized,
and HTML uses restrictive local-only policies. Apply approved access, encryption, retention, and
backup controls to the directory.

## Advanced usage

The equivalent direct tool sequence is:

```text
ci_track action="add" entity_type="drug" name="ExampleDrug" aliases=["examplemab"]
ci_track action="add" entity_type="company" name="Example Pharma"
ci_refresh entities=["ExampleDrug", "Example Pharma"]
ci_report run_id="<run_id>"
ci_timeline run_id="<run_id>" months_back=12
```

Localized detailed guides: [English](../../docs/en/competitive_intelligence.md) ·
[日本語](../../docs/jp/competitive_intelligence.md) ·
[简体中文](../../docs/zh/competitive_intelligence.md).
