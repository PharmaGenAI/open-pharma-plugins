# Competitive Intelligence

Track explicit drug/company identities, collect public-source evidence once, and generate
reproducible briefings and timelines from the same immutable run.

Localized guides: [English](../../docs/en/competitive_intelligence.md) ·
[日本語](../../docs/jp/competitive_intelligence.md) ·
[简体中文](../../docs/zh/competitive_intelligence.md)

## Tools available

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
| `ci_report` | Write immutable HTML/JSON/CSV briefing artifacts |
| `ci_timeline` | Write an immutable, calendar-filtered HTML timeline |

## Install and configure

```bash
python -m pip install "open-pharma-plugins[competitive-intelligence]"
```

Web search needs a Serper, Tavily, or Exa key. `OPENFDA_API_KEY` and `NCBI_API_KEY` are optional.

`OPEN_PHARMA_CI_DATA_DIR` defaults to `~/.open-pharma-plugins/competitive-intelligence`.
`CI_CACHE_TTL_HOURS` defaults to 24.

Credentials are transport-only and excluded from persisted cache identities and evidence URLs.

Keep configuration comments on separate lines. Text after a value, including `#`, is parsed as part
of that value. `ci_status` reports key presence, not provider acceptance.

openFDA permits 240 requests per minute in both modes. The daily quota is 1,000 requests per IP
without a key and 120,000 requests per key with one.

## Recommended workflow

```text
ci_track action="add" entity_type="drug" name="ExampleDrug" aliases=["examplemab"]
ci_track action="add" entity_type="company" name="Example Pharma"
ci_refresh entities=["ExampleDrug", "Example Pharma"]

# Inspect coverage and limitations, then reuse the returned run ID.
ci_report run_id="<run_id>"
ci_timeline run_id="<run_id>" months_back=12
```

The report and timeline manifests must contain the same `run_records_sha256`. Legacy report/timeline
selectors remain available, but each creates a new run before rendering.

## Interpret coverage before findings

- `complete` can include a trustworthy zero-result response.
- `partial` means only part of the requested coverage is trustworthy.
- `failed` and `not_configured` are inconclusive, not zero-result findings.
- `not_applicable` means the source does not apply to the tracked identity.

Reports show returned records separately from a provider's larger `total_available`. Confirm
high-impact findings against linked primary sources before commercial or medical decisions.

If openFDA is `failed` while DailyMed succeeds, verify or remove `OPENFDA_API_KEY` from the winning
configuration source. Process environment values override the config file. Restart the host, confirm
the expected key presence with `ci_status`, and create a new run. Do not interpret the failed source
as evidence that no FDA event exists.

## Local artifacts

The configured CI directory contains `watchlist.json`, `cache/`, immutable `runs/<run_id>/`, and
non-overwriting `reports/<run_id>/<artifact_id>/` directories.

Report CSV text is formula-neutralized, and HTML uses restrictive local-only policies. Apply
approved access, encryption, retention, and backup controls to the directory.
