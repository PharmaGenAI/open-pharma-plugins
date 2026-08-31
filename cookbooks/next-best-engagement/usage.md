# Next-Best-Engagement

Score eligible HCPs, select one consent-compatible action per HCP, keep the
action with that HCP's assigned representative, and export the resulting plan.

## Tools available

| Tool | Purpose |
|---|---|
| `load_universe` | Load a CSV or the bundled demo universe |
| `recommend_engagements` | Score HCPs and generate a constrained engagement plan |
| `render_plan` | Return JSON or write engagement and summary CSV files |

## Install and configure

After the NBE release has been published to PyPI, install its published version:

```bash
pip install "open-pharma-plugins[next-best-engagement]==2.4.0"
```

For a post-publication install from an immutable release tag (only after that
tag exists), use the exact tag rather than a branch:

```bash
pip install "open-pharma-plugins[next-best-engagement] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-next-best-engagement-v1.0.2"
```

CSV exports default to `~/.open-pharma-plugins/next-best-engagement`; set `OPEN_PHARMA_NBE_OUTPUT_DIR` to use another directory. Text cells beginning with spreadsheet formula markers are prefixed with an apostrophe during CSV export.

## Example workflow

```text
load_universe source="fixture"
recommend_engagements period_days=30 min_gap_days=14
  weight_recency_gap=0.30 weight_tier_value=0.30
  tier_a_coverage_pct=0.95 tier_b_coverage_pct=0.80
render_plan format="csv" plan_json="<JSON returned above>"
```

`period_days` accepts values from 1 through 365. Every plan includes the
required `universe_fingerprint` and `universe_generation` snapshot fields.
Its metrics report `total_universe` for every input HCP, `total_eligible` for
HCPs that pass consent, minimum-gap, and initial score-threshold checks, and
`total_planned` for recommended actions. Tier-coverage denominators use that
same eligible population.

`render_plan` fails closed unless the same universe snapshot is still loaded.
Any successful `load_universe` call, even an identical reload, advances
`universe_generation` and invalidates earlier explicit or stored plan JSON;
generate a new plan before exporting it.

For a custom universe, pass a CSV path to `load_universe`. Required columns are
`hcp_id`, `hcp_name`, `territory_id`, and `rep_id`; `rep_id` is the HCP's
assigned-representative ownership and is not reassigned by the planner.
Consent, tier, activity, capacity, and extra CRM fields are optional, but an
email or phone/meeting action is eligible only when its matching consent value
is explicitly `true`.

## Decision boundary

Recommendations are deterministic planning support, not autonomous outreach.
The planner recommends at most one action for each HCP; it does not send an
email, book a meeting, or perform a visit. It preserves each HCP's assigned
`rep_id`; only in-person visits consume that rep's visit capacity, while remote
meetings and approved emails do not. Channel-diversity scoring rewards recent
use of multiple channel types, but does not replace consent or local-policy review.
Confirm explicit consent, suppression lists, assigned-rep and territory
ownership, and local policies before representatives act on a plan. The bundled
universe is fictional demo data.
