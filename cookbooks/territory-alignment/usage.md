# Territory Alignment

Assign HCPs to representatives and plan visit clusters while balancing workload, travel,
continuity, and priority coverage.

## Tools

| Tool | Purpose |
|---|---|
| `ta_status` | Show loaded data and saved scenarios |
| `ta_align` | Generate a named alignment scenario |
| `ta_evaluate` | Evaluate one saved scenario |
| `ta_compare` | Compare two to four scenarios |
| `ta_visualize` | Write an interactive HTML map |
| `ta_cluster` | Build visit clusters and route sequences |

For exact schemas, check the installed Skill or MCP tool list.

## Install

Register the marketplace once, then install the Skill and MCP server for your agent host. If the
marketplace is already registered, skip its `add` command and refresh it before installing a newly
released capability.

The marketplace catalog pins this capability to its current immutable release tag.

### Claude Code

```bash
claude plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
claude plugin install open-pharma-plugins-territory-alignment@open-pharma-plugins
```

### Codex

```bash
codex plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
codex plugin marketplace upgrade open-pharma-plugins
codex plugin add open-pharma-plugins-territory-alignment@open-pharma-plugins
```

### GitHub Copilot CLI

```bash
copilot plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
copilot plugin install open-pharma-plugins-territory-alignment@open-pharma-plugins
```

### Python MCP server only

The Python distribution installs the server without the companion Skill:

```bash
python -m pip install "open-pharma-plugins[territory-alignment]"
```

The guided installer, local-checkout setup, and rollback instructions are in the shared
[installation guide](../../docs/en/installation.md).

## Configure

Set `OPEN_PHARMA_TA_DATA_DIR` to a directory containing `hcps.csv`, `reps.csv`,
`current_alignment.csv`, and `constraints.csv`. When it is unset, the plugin uses bundled fictional
fixtures. Scenarios default to `~/.open-pharma-plugins/territory-alignment/scenarios`; set
`OPEN_PHARMA_TA_SCENARIOS_DIR` to move them.

## Recommended workflow

1. Check the loaded inputs and existing scenarios before planning a change.
2. Create an immutable baseline, then create separately named scenarios for vacancies, new hires,
   manual assignments, or changed objective weights.
3. Evaluate each scenario and compare two to four scenarios built from the same source fingerprints.
4. Render a map when location review is appropriate, then build representative-level visit clusters
   from the selected scenario snapshot.
5. Review employment rules, consent, account ownership, accessibility, travel constraints, and
   manager overrides before adopting an alignment.

## Example requests

```text
Using the approved territory data, compare the current baseline with a scenario where R004 is
vacant. Highlight workload, travel, continuity, and priority-coverage trade-offs.

@new-hire-plan.csv
Model the proposed hires and show which HCP assignments change. Keep the named strategic accounts
with their current representatives and create an interactive comparison map.

Plan next week's consented visits for R001 from the selected alignment. Preserve fixed
appointments, flag routes above 120 travel minutes, and suggest remote alternatives for long trips.
```

## Outputs and safeguards

Each scenario JSON includes a versioned input snapshot, source fingerprints, plugin version, run
ID, UTC creation time, levers, and solver settings. Scenario names are immutable; use a new name for
every rerun. Scenario JSON and formula-safe CSVs are written as one transaction with private
permissions where POSIX permissions are available.

Evaluation, clustering, and visualization use the named scenario snapshot rather than mutable
runtime CSVs. Comparisons require matching source fingerprints. Visit planning returns concrete
dates, preserves fixed appointment order, excludes HCPs without visit consent, reports unplanned
records, and warns when route estimates exceed the configured daily limit.

Opening a map loads integrity-pinned Leaflet assets and CARTO/OpenStreetMap tiles from the public
network. Use an approved offline map stack when coordinates must not reach those providers.

Results are planning recommendations, not autonomous assignments or scheduling. Do not treat the
built-in fixtures as real HCP or employee records.

## Advanced usage

The equivalent direct tool sequence is:

```text
ta_status
ta_align scenario_name="baseline"
ta_align scenario_name="vacancy" vacancies=["R004"]
ta_evaluate scenario_name="vacancy"
ta_compare scenarios=["baseline","vacancy"] focus="workload"
ta_visualize scenarios=["baseline","vacancy"] show_movements=true
ta_cluster scenario_name="vacancy" rep_id="R001" period="next_week"
  remote_threshold_min=45
```

Legacy version 1.0 scenarios that lack `input_snapshot` are listed as invalid. Retain any needed
exports, then regenerate those scenarios under new names before comparison or operational planning.
