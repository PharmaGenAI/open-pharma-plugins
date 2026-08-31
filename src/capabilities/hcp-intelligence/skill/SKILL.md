---
name: open-pharma-plugins-hcp-intelligence
description: |
  Build structured profiles for Healthcare Professionals (HCP) and Healthcare
  Organizations (HCO) from public sources. Use when: (1) the user provides an
  HCP or HCO name and asks for a profile, background, or intelligence summary;
  (2) someone says "profile this doctor", "look up this hospital", "HCP360",
  "build a KOL profile", "find publications for Dr X", "what clinical trials
  is this investigator involved in"; (3) the user provides a list of accounts
  with name/specialty/country and asks for enrichment; (4) the task involves
  identifying research interests, affiliations, publication history, or
  clinical trial involvement for commercial or medical engagement planning.
---

# HCP Intelligence

Build canonical, evidence-backed profiles for individual Healthcare Professionals (HCP)
and Healthcare Organizations (HCO) using public data sources.

## Available MCP tools

| Tool | Data source | Returns | KOL signal |
|---|---|---|---|
| `list_accounts` | Bundled demo CRM | Filtered account summaries | Enrichment queue |
| `get_account` | Bundled demo CRM + private enrichment store | Account and saved profile | Review context |
| `update_account` | Private enrichment store | Saved status and profile | Workflow state |
| `search_orcid` | ORCID public API | Verified education, employment, publication/funding counts | Disambiguation, career trajectory |
| `search_publications` | PubMed (NCBI E-utilities) | Publications with PMID, authors, MeSH terms, abstract | Research output, topic expertise |
| `search_clinical_trials` | ClinicalTrials.gov v2 API | Trials with NCT ID, status, phase, investigators, sponsors | Trial involvement, sponsor ties |
| `search_guidelines` | PubMed + web (FDA/EMA) | Guideline publications + regulatory advisory roles | Guideline authorship, regulatory influence |
| `search_congresses` | Web (society congress sites) | Conference presentations, speaking roles | KOL tier (keynote > poster) |
| `search_grants` | NIH RePORTER + web fallback | Grant records with amounts, dates, co-PIs | Active funding, career stage |
| `search_hcp_web` | Exa / Tavily / Serper | Web pages about an individual HCP | Biographical, institutional |
| `search_hco_web` | Exa / Tavily / Serper | Web pages about a healthcare organization | Org profile |

## Workflow

Follow this sequence to build a profile. The agent orchestrates the search tools and
synthesizes the final profile — there is no `build_profile` tool; the structured output
is the agent's final response.

### HCP profile workflow

```
1. IDENTIFY & DISAMBIGUATE
   Call search_orcid with the name + affiliation.
   ORCID is cheap (one request, no key) and returns verified education,
   employment history, and publication/funding counts. If it finds a match,
   you have a disambiguated identity and can skip guesswork.
   If no ORCID match, call search_hcp_web with name + specialty + country
   to identify the target from institutional pages and directories.
   Note any disambiguation reasoning in the profile.

2. GATHER PUBLICATIONS
   Call search_publications with the HCP's name and affiliation.
   If the name is common, add the specialty or institution to narrow results.
   Extract: research themes, top journals, co-author networks, h-index if visible.

3. GATHER GUIDELINES & REGULATORY ROLES
   Call search_guidelines with scope "both".
   Guideline authorship and FDA/EMA advisory roles are top-tier KOL signals.
   If the HCP has guideline publications, flag them separately from regular papers.

4. GATHER CLINICAL TRIALS
   Call search_clinical_trials with the HCP's name.
   Filter by country if needed. Note their role (PI vs Sub-I).

5. GATHER CONGRESS ACTIVITY
   Call search_congresses with the name + specialty or therapeutic_area.
   Classify roles: keynote/invited lecture (tier 1), symposium speaker (tier 2),
   oral presentation (tier 3), poster (tier 4), moderator/chair (leadership).

6. GATHER GRANTS (if research-active)
   Call search_grants with the HCP's name + institution.
   Active grants signal current research activity. Grant amounts and
   mechanisms (R01, K-award, P-center) indicate career stage and scale.

7. DEEP WEB SEARCH (if gaps remain)
   Call search_hcp_web with targeted queries:
     - "<name> <institution> biography"
     - "<name> society membership OR committee OR advisory board"
     - "<name> education OR qualification OR fellowship"
   Extract: titles, designations, society roles, education history.

8. SYNTHESIZE
   Assemble the HcpProfile following the output schema (see references/output-schema.md).
   Every factual claim MUST carry at least one SourceCitation.
   Assign confidence: high (multiple authoritative sources), medium (single authoritative
   or multiple informal), low (single informal or inferred).
   Set profile_completeness based on how many sections have data.

9. OUTPUT
   Return the profile as a single JSON object conforming to the HcpProfile schema.
   Wrap in a ```json code fence for readability.
```

### HCO profile workflow

```
1. IDENTIFY
   Call search_hco_web with the organization name + country.
   Locate the official website, Wikipedia page, or ministry-of-health listing.

2. GATHER CLINICAL TRIALS
   Call search_clinical_trials with the organization name as sponsor or site.

3. GATHER GRANTS (for research-active institutions)
   Call search_grants with the organization name as institution.
   Reveals active research programmes, funding scale, and therapeutic focus areas.

4. DEEP WEB SEARCH (if gaps remain)
   Call search_hco_web with targeted queries:
     - "<name> departments OR centres of excellence"
     - "<name> bed capacity OR annual report"
     - "<name> accreditation OR ranking"
     - "<name> history OR founded"

5. SYNTHESIZE
   Assemble the HcoProfile following the output schema.
   Same provenance and confidence rules as HCP.

6. OUTPUT
   Return the profile as a single JSON object conforming to the HcoProfile schema.
```

### Batch workflow

Use the packaged batch console for a user-curated CSV. Do not replace this flow with direct MCP calls
or an in-chat JSON array when the user supplies input and output paths.

1. Require both paths. Reject control characters, expand `~`, resolve both paths to absolute paths,
   and quote each resolved path as one shell argument. Never place a path in command substitution or
   `eval`.
2. Preserve each selection filter the user supplied: pass `--ids` with every supplied ID as its own
   quoted argument, `--country` with the supplied value, and `--account-type` with the supplied value.
   Include only supplied filters; never invent a filter. The examples below show all three filters so
   their propagation is explicit.
3. Always run this no-provider-call preflight first:

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

4. Inspect the resolved paths, selected IDs, selected count, HCP/HCO split, provider/model settings,
   and planned JSON, `batch_summary.csv`, and `batch_manifest.json` artifacts. A successful dry run
   makes no external calls and does not create the output directory.

#### Exact selection gate

Before provider calls, compare the dry-run selected IDs, selected count, and HCP/HCO split with the
user-requested scope. The dry-run and execution must have argument-for-argument identical paths and
filter values. Any omitted or changed filter, extra or missing ID, count mismatch, or HCP/HCO split
mismatch must stop before provider calls; correct the command and perform a new dry run. The automatic
proceed rule for at most 10 accounts applies only after this exact selection equality is established.

5. If the original request explicitly says to run or process and the exactly matched selection is at
   most 10 accounts, execute after the successful dry run. If the user supplied only paths, obtain
   confirmation first. More than 10 accounts always require explicit approval for provider calls;
   clear advance approval in the original request satisfies this gate, so do not ask twice.
6. Construct the approved synthesis run by copying the validated dry-run command, removing only
   `--dry-run`, and adding `--synthesize`, `--resume`, and `--concurrency 3`. Do not retype or alter the
   paths or filters:

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

7. Monitor until the process exits. Report its exit status, completed/partial/failed/skipped counts,
   the absolute output-directory path, and the absolute paths to `batch_summary.csv` and
   `batch_manifest.json`. Treat JSON as the canonical evidence; the CSV is a review projection.
   Require human review of identity, sources, and partial/failed records before operational use.

Never silently fall back to `main`, another tag, an unpinned package, a local checkout, or a different
revision if `uvx` installation fails. Report the exact pinned-source failure and stop. Raw collection
does not require an LLM key; omit `--synthesize` only when the user explicitly requests raw evidence.
Synthesis requires `OPENROUTER_API_KEY` and sends the selected account fields and gathered evidence to
the configured `OPENROUTER_BASE_URL`. Confirm that provider use and data transmission are approved.
Extraction and synthesis default to DeepSeek V4 Flash
(`deepseek/deepseek-v4-flash-0731`), `high` reasoning effort, a 120-second request timeout, strict
structured output, and zero SDK retries.

The input header is `id,name,specialty,country,account_type,institution`; IDs must be unique and safe
as file names, and `account_type` must be `HCP` or `HCO`. A non-empty output directory is accepted only
with `--resume`. Resume preserves unrelated files, reuses valid account JSON, reprocesses unusable
artifacts, and regenerates the summary CSV and schema-v2 manifest; it does not preserve manual edits to
generated batch artifacts. See `docs/en/hcp_batch.md` for the stable 27-column CSV contract and exit
codes. User-curated inputs do not write to the demo enrichment store unless `--write-back` is explicit.

### Checkout development branch

Only when the user deliberately chose an Open Pharma Plugins repository checkout, use the source-tree
wrapper for development testing. Preserve supplied filters exactly as in the installed branch; the
examples below assume the same three filters were supplied.

```bash
uv run --all-extras python scripts/batch_enrich.py \
  --input-file src/capabilities/hcp-intelligence/open_pharma_plugins_hcp_intelligence/fixtures/sample_accounts.csv \
  --output-dir data/hcp-intelligence \
  --ids "HCP-SG-001" "HCP-SG-002" \
  --country "Singapore" \
  --account-type "HCP" \
  --dry-run
```

After the exact selection gate and required approval, copy that validated command, remove only
`--dry-run`, and add the approved execution flags:

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

The repository script is not present in an installed marketplace plugin cache. Never present this
checkout command as the installed-plugin workflow, and never use it as a fallback after a pinned
installed command fails.

## Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SERPER_API_KEY` | For web search | — | Serper API key (preferred for web search) |
| `TAVILY_API_KEY` | For web search | — | Tavily API key (alternative) |
| `EXA_API_KEY` | For web search | — | Exa API key (alternative) |
| `OPENROUTER_API_KEY` | Only for `--synthesize` | — | OpenRouter API key for batch profile synthesis |
| `OPENROUTER_BASE_URL` | No | `https://openrouter.ai/api/v1` | OpenRouter-compatible synthesis endpoint |
| `NCBI_API_KEY` | No | — | NCBI E-utilities API key (raises rate limit from 3 to 10 req/s) |
| `OPEN_PHARMA_HCP_DATA_DIR` | No | `~/.open-pharma-plugins/hcp-intelligence` | Mutable enrichment store |

PubMed and ClinicalTrials.gov APIs are free and do not require keys for basic use.
An NCBI API key is optional but recommended for batch processing.

Web search requires at least one of SERPER, TAVILY, or EXA keys. Set
`OPEN_PHARMA_SEARCH_BACKEND` to `auto`, `serper`, `tavily`, or `exa`.
OpenRouter is separate from search and is contacted only when the batch console runs with
`--synthesize`.

## Provenance rules

These rules are non-negotiable for compliance:

1. **Every claim needs a source.** Do not include a fact in the profile unless at least
   one `SourceCitation` backs it. If you cannot find a source, omit the claim.
2. **Prefer authoritative sources.** Peer-reviewed publications and official registries
   are `high` confidence. Hospital websites and professional directories are `medium`.
   News articles and social media are `low`.
3. **Cross-reference names.** If the HCP has a common name, require at least two
   independent sources agreeing on specialty + institution before accepting a claim.
4. **No fabrication.** If a search returns no results, report the gap honestly via
   `profile_completeness` — do not fill sections with plausible but unsourced data.
5. **Date everything.** Every `SourceCitation` must include `accessed_date`.
6. **Protect personal data.** Confirm identity before persisting an enrichment, collect only
   necessary public information, and follow applicable consent, access, and retention rules.

## Composing with other capabilities

- Use the host application's file tools to prepare account CSVs before calling these MCP tools.
- Follow source URLs returned by `search_hcp_web` or `search_hco_web` only when the host can
  retrieve them, and preserve the original URL in the final evidence record.

## Output schema

See [references/output-schema.md](references/output-schema.md) for the complete
field-by-field schema with examples.
