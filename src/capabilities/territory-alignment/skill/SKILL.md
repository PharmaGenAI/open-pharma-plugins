---
name: open-pharma-plugins-territory-alignment
description: |
  Territory alignment: assign HCPs to reps (strategic mode) and plan visit
  routes (operational mode). Use when: (1) the user asks to assign, balance,
  or realign territories; (2) someone says "territory alignment", "territory
  planning", "rep assignment", "workload balance", "headcount scenario",
  "vacancy model", "visit clusters", "route planning"; (3) the user wants to
  compare alternative territory configurations; (4) the user wants to plan
  weekly visits for a specific rep.
---

# Territory Alignment

Assign HCPs to representatives (strategic) and plan visit routes (operational),
balancing workload, travel, relationship continuity, and priority coverage.

## Available MCP tools

| Tool | Mode | Purpose | Returns |
|---|---|---|---|
| `ta_status` | Both | Data overview + scenario inventory | HCP/rep counts, segments, geocoding, saved scenarios |
| `ta_align` | Strategic | Generate named alignment scenario | Consolidated offline report + assignments, objectives, advanced exports |
| `ta_evaluate` | Strategic | Detailed scoring breakdown | Per-rep detail, coverage, continuity, distributions |
| `ta_compare` | Strategic | Side-by-side 2-4 scenarios | Objectives matrix, Pareto analysis, movements, narrative |
| `ta_visualize` | Both | Consolidated visual report | Offline map, KPIs, charts, review queue, movements, advanced exports |
| `ta_cluster` | Operational | Visit clusters + routing | Clusters, visit sequence, remote alternatives |

## Strategic workflow

```
1. ta_status — confirm data is loaded, review HCP/rep counts
2. ta_align scenario_name="baseline" — generate alignment + primary offline HTML report
3. ta_evaluate scenario_name="baseline" — review detailed metrics
4. Discuss with the user: what to explore?
   - Vacancy?    → ta_align with vacancies=["R004"]
   - Rebalance?  → ta_align with adjusted weights
   - New hire?   → ta_align with new_hires=[...]
   - Pin an HCP? → ta_align with overrides=[...]
5. ta_compare scenarios=["baseline", "alternative"] — show trade-offs
6. ta_visualize scenarios=["baseline", "alternative"] — offline comparison report
7. Iterate until the user is satisfied
```

Every successful `ta_align` automatically creates one consolidated offline HTML report at the
territory-alignment store root. It combines KPIs, an interactive relative territory view, workload
and objective charts, a review queue, changed assignments, and provenance. The existing JSON and CSV
files remain unchanged advanced exports under `scenarios/`.

The default report is self-contained and makes no network requests. To request street-map context,
call `ta_visualize scenarios=[...] basemap="public"`; opening that separately named report loads
integrity-pinned Leaflet assets and CARTO/OpenStreetMap tiles and may disclose map requests or
coordinates to those providers.

## Operational workflow

```
1. ta_status — confirm alignment exists
2. ta_cluster scenario_name="baseline" rep_id="R001" period="next_week"
3. Present daily plan with clusters and route
4. Review excluded_no_visit_consent, unplanned_hcp_ids, and travel warnings
5. Highlight consented remote alternatives
6. Iterate: adjust threshold or add in-period appointments
```

`ta_cluster` always plans from the named scenario's saved input snapshot. Fixed
appointments must belong to that scenario and fall on a rep-available date in
the requested period. Treat route time as an estimate, and confirm appointment,
travel, daily-call, consent, and employment constraints before execution.

## Scenario levers

Six levers compose freely in ta_align:

1. **Objective weights** — shift priorities between balance, travel, stability, coverage
2. **Vacancies** — model a rep leaving, HCPs redistributed
3. **New hires** — add a rep at a given location
4. **Overrides** — pin specific HCP-to-rep assignments
5. **Lock reps** — freeze specific reps' territories
6. **Combined** — all levers in one call for realistic planning

See `references/workflows.md` for worked examples.

## Data format

Four CSV files in the data directory. See `references/data_format.md` for
column definitions and conventions.

## Objective function

See `references/objectives.md` for how each objective is measured and scored.

## Connection to Next-Best-Engagement

Territory Alignment → assignments CSV → NBE's load_universe →
engagement recommendations within those territories.

## Configuration and decision boundary

The four input CSVs default to the packaged fixtures. Set `OPEN_PHARMA_TA_DATA_DIR` to an absolute,
user-visible directory for operational inputs. Scenarios default to
`~/.open-pharma-plugins/territory-alignment/scenarios` and can be moved with
`OPEN_PHARMA_TA_SCENARIOS_DIR`. Scenario names are immutable: use a new name for
each run. The primary report is written to the parent of the configured scenario
directory; the canonical JSON and assignment/territory CSVs retain their existing
paths. Comparisons require matching saved input fingerprints. Results are planning
recommendations; confirm manager overrides, consent, employment rules, and
operational feasibility.
