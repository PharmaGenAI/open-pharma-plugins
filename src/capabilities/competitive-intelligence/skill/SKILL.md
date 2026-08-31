---
name: open-pharma-plugins-competitive-intelligence
description: |
  Competitive intelligence: track competitor drugs and companies, collect
  trial, regulatory, news, and publication evidence, and create reproducible
  briefings and timelines. Use for competitor activity, pipeline, landscape,
  launch-planning, monitoring, and CI briefing requests.
---

# Competitive Intelligence

Collect public-source evidence once, inspect source coverage, and project the same immutable run
into briefing and timeline artifacts. High-impact findings still require primary-source review.

## Available MCP tools

| Tool | Purpose | Returns |
|---|---|---|
| `ci_status` | Check configuration, source readiness, cache, and watchlist | Non-secret readiness summary |
| `ci_track` | Add, remove, or list explicit drug/company identities | Validated watchlist |
| `ci_refresh` | Collect one immutable evidence run for tracked entities | Run ID, hash, coverage, limitations |
| `ci_scan_trials` | Search ClinicalTrials.gov | Trials plus evidence coverage |
| `ci_trial_detail` | Retrieve one NCT record | Trial detail plus evidence coverage |
| `ci_scan_regulatory` | Search openFDA and DailyMed | Regulatory and label-history evidence |
| `ci_scan_news` | Search the configured web provider | News results plus evidence coverage |
| `ci_scan_publications` | Search PubMed | Publications plus evidence coverage |
| `ci_extract_events` | Extract CI events from a local document | Structured event candidates |
| `ci_landscape` | Build a therapeutic-area landscape | Cross-referenced landscape |
| `ci_report` | Project a run into JSON, HTML, and CSV | Immutable artifact paths and manifest |
| `ci_timeline` | Project a run into a filtered HTML timeline | Immutable artifact path and manifest |

## Recommended evidence-run workflow

```text
1. ci_status
2. ci_track action="add" entity_type="drug" name="ExampleDrug" aliases=["examplemab"]
3. ci_refresh entities=["ExampleDrug"]
4. Inspect every source status and limitation returned by ci_refresh.
5. ci_report run_id="<run_id>"
6. ci_timeline run_id="<run_id>" months_back=12
7. Confirm both artifact manifests contain the same run_records_sha256.
```

Use the returned run ID rather than repeating collection. Legacy `ci_report focus=...` and
`ci_timeline entities=[...]` remain available and create exactly one new run before rendering.

## Coverage semantics

- `complete`: the bounded provider request completed; zero records is a trustworthy observation.
- `partial`: usable records exist, but pagination, truncation, or another constituent request was incomplete.
- `failed`: the provider or parser did not produce trustworthy records; this is not a zero-result finding.
- `not_configured`: required provider configuration is absent; this is not a zero-result finding.
- `not_applicable`: the source does not apply to the identity, such as drug regulatory records for a company.

Inspect the exact query, source URL, retrieval time, cache state, record count, and limitations in
the source ledger. A larger `total_available` means the bounded run did not inspect every match.

## Identity and source rules

- Track a drug and its manufacturer as separate entities. Tenant or operator context must never be
  inferred from a company name.
- Company aliases are company identities, not inferred products. Add a separate drug entity when
  regulatory collection is needed.
- Provider failure, blocked coverage, and malformed responses are inconclusive.
- Web search requires Serper, Tavily, or Exa; `OPENFDA_API_KEY` and `NCBI_API_KEY` are optional.
- Do not place credentials, confidential strategy, private source content, or unnecessary personal
  data in query terms.

## Local output

`OPEN_PHARMA_CI_DATA_DIR` defaults to
`~/.open-pharma-plugins/competitive-intelligence` and contains:

- `watchlist.json` — validated persistent identities;
- `cache/` — schema-v2 credential-free provider responses;
- `runs/<run_id>/` — immutable `records.json` and its hash-bearing manifest;
- `reports/<run_id>/<artifact_id>/` — non-overwriting JSON, HTML, CSV, and artifact manifests.

Directories and files are private where the operating system supports POSIX permissions, but local
permissions are not encryption, tenant isolation, backup policy, or enterprise authorization.
