# Territory Alignment

Assign HCPs to representatives and plan visit clusters while balancing workload, travel, continuity, and priority coverage.

## Tools available

| Tool | Purpose |
|---|---|
| `ta_status` | Show loaded data and saved scenarios |
| `ta_align` | Generate a named alignment scenario |
| `ta_evaluate` | Evaluate one saved scenario |
| `ta_compare` | Compare two to four scenarios |
| `ta_visualize` | Write an interactive HTML map |
| `ta_cluster` | Build visit clusters and route sequences |

## Install and configure

```bash
pip install "open-pharma-plugins[territory-alignment]"
```

`OPEN_PHARMA_TA_DATA_DIR` selects the four input CSVs; built-in fictional fixtures are used when unset. Scenarios default to `~/.open-pharma-plugins/territory-alignment/scenarios` and can be moved with `OPEN_PHARMA_TA_SCENARIOS_DIR`.

## Example workflow

```text
ta_status
ta_align scenario_name="baseline"
ta_align scenario_name="vacancy" vacancies=["R004"]
ta_evaluate scenario_name="vacancy"
ta_compare scenarios=["baseline","vacancy"] focus="workload"
ta_visualize scenarios=["baseline","vacancy"] show_movements=true
ta_cluster scenario_name="vacancy" rep_id="R001" period="next_week" remote_threshold_min=45
```

Each scenario JSON includes a versioned input snapshot, source fingerprints,
plugin version, run ID, UTC creation time, levers, and solver settings. Names
are immutable; reruns must use a new name. Scenario JSON and formula-safe CSVs
are written as one transaction to the scenarios directory; map HTML is written
to its parent. Files are private to the current user where POSIX permissions
are available.

Evaluation, clustering, and visualization use the named scenario snapshot
rather than mutable runtime CSVs. Comparisons require the same source input
fingerprints. `ta_cluster` returns concrete planning dates, preserves fixed
appointment order, excludes HCPs without visit consent, identifies unplanned
records, and warns when route estimates exceed the configured daily limit.

Opening a map loads integrity-pinned Leaflet assets and CARTO/OpenStreetMap
tiles from the public network; use an approved offline map stack when
coordinates must not reach those providers.

Version 1.1 scenarios use the new snapshot schema. Scenario files from 1.0
that do not contain `input_snapshot` are listed as invalid; retain any needed
exports, then regenerate them under new scenario names before comparison or
operational planning.

## Decision boundary

Results are planning recommendations. Confirm employment rules, consent, account ownership, accessibility, travel constraints, and manager overrides before operational use. Do not treat the built-in fixtures as real HCP or employee records.
