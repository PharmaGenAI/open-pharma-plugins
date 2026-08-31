# Data Security and Compliance Boundaries

Open Pharma Plugins is local-first software, but several tools send queries to public APIs or configured search providers. You remain responsible for determining whether the data and intended use are permitted in your jurisdiction and organization.

## Local storage

Mutable data defaults under `~/.open-pharma-plugins`:

| Capability | Default data |
|---|---|
| HCP Intelligence | `hcp-intelligence/enrichment_store.json` |
| Field Training | `training-content/` plus `outputs/` |
| Campaign Studio | `campaign-studio/campaigns/` |
| Next-Best-Engagement | `next-best-engagement/` |
| Territory Alignment | `territory-alignment/scenarios/` |
| Competitive Intelligence | `competitive-intelligence/` watchlist, cache, immutable runs, and reports |

Directories use mode `0700` and files use `0600` where supported. These permissions protect against other local users; they do not provide encryption, backup, tenant isolation, endpoint security, or enterprise access control.

Tools that ingest PDF, PPTX, CSV, or CI source files can read paths available to the MCP server process. Run the server with least-privilege filesystem access, review path arguments before approval, and keep unrelated sensitive files outside its readable scope.

## External transmission

PubMed, ClinicalTrials.gov, ORCID, NIH RePORTER, openFDA, DailyMed, and configured web-search providers receive the query terms needed for a request. Do not include credentials, confidential strategy, private source content, or unnecessary personal data in queries. Provider terms and retention policies apply independently.

Raw HCP batch collection does not call an LLM. When the packaged
`open-pharma-plugins-hcp-batch` console runs with `--synthesize`, it sends the selected account
fields and all gathered evidence to the configured OpenRouter-compatible endpoint. Confirm the
endpoint, model, provider logging/retention terms, and organizational approval before synthesizing
operational data. The schema-v2 batch manifest records the effective endpoint, model, reasoning
effort, and request timeout, but never the API key. Generated account JSON, summary CSV, and manifest
can contain personal/public-source data; keep them in approved storage and do not treat local file
permissions as enterprise access control. See [HCP batch processing](hcp_batch.md).

The automatically generated Territory Alignment offline report makes no network requests: its
styles, interaction code, relative SVG territory boundaries, and markers are embedded in the local
HTML file. It is not a street or routing map. Public geographic context is available only through an
explicit `ta_visualize basemap="public"` call. Opening that separately named report loads
integrity-pinned Leaflet assets and CARTO/OpenStreetMap tiles; those providers can observe network,
map-extent, and tile requests. Do not use public mode when territory coordinates must remain inside
an approved environment.

Competitive-intelligence cache keys, evidence URLs, run manifests, and report manifests exclude
credential-like query parameters. API credentials remain in the local process/configuration and
transport request only.

Provider HTTP errors are deliberately sanitized before entering evidence. `ci_status` reports key
presence, not validity. Never print a key while diagnosing a failed source. When removing one, clear
the config file and any overriding environment value, restart the host, confirm status, and create a
new run.

Run and report directories are non-overwriting and hash-bound, but they are not automatically
expired or encrypted. A matching hash proves artifact linkage, not source truth or reviewer approval.

## Privacy and retention

- Use the minimum personal data needed for a defined purpose.
- Confirm HCP identity before persisting a profile and keep source/access-date provenance.
- Honor consent, suppression, employment, and territory rules before acting on plans.
- Set capability data directories to approved encrypted or managed storage when local defaults are unsuitable.
- Define an organizational retention period. The project does not automatically expire operational records; delete the relevant capability directory when data is no longer required.
- Back up only if the backup location has equivalent controls.

## Pharmaceutical content

Campaign Studio and Field Training enforce schema, source, claim, and stale-validation gates, but their output is still a draft. A qualified medical/legal/regulatory reviewer must approve every artifact before distribution or field use. Never use the bundled fictional claims, documents, accounts, HCPs, representatives, or competitors as real operational data.

## Deletion and incident response

Stop the MCP server before deleting local data. Remove the configured capability directory and any exported copies or backups. If a credential may have been exposed, revoke it with the provider, clear relevant caches, and follow your incident-response process; deleting a local file does not revoke a credential or remove provider-side logs.
