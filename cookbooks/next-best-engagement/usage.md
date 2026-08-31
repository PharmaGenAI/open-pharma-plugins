# Next-Best Engagement

Score eligible HCPs, select one consent-compatible action per HCP, keep the action with the HCP's
assigned representative, and export a reviewable engagement plan.

## Tools

| Tool | Purpose |
|---|---|
| `load_universe` | Load a CSV or the bundled demo universe |
| `recommend_engagements` | Score HCPs and generate a constrained engagement plan |
| `render_plan` | Return JSON or write engagement and summary CSV files |

For exact schemas, check the installed Skill or MCP tool list.

## Install

Register the marketplace once, then install the Skill and MCP server for your agent host. If the
marketplace is already registered, skip its `add` command and refresh it before installing a newly
released capability.

The marketplace catalog pins this capability to its current immutable release tag.

### Claude Code

```bash
claude plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
claude plugin install open-pharma-plugins-next-best-engagement@open-pharma-plugins
```

### Codex

```bash
codex plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
codex plugin marketplace upgrade open-pharma-plugins
codex plugin add open-pharma-plugins-next-best-engagement@open-pharma-plugins
```

### GitHub Copilot CLI

```bash
copilot plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
copilot plugin install open-pharma-plugins-next-best-engagement@open-pharma-plugins
```

### Python MCP server only

The Python distribution installs the server without the companion Skill:

```bash
python -m pip install "open-pharma-plugins[next-best-engagement]"
```

The guided installer, local-checkout setup, and rollback instructions are in the shared
[installation guide](../../docs/en/installation.md).

## Configure

CSV exports default to `~/.open-pharma-plugins/next-best-engagement`. Set
`OPEN_PHARMA_NBE_OUTPUT_DIR` to use another directory. Next-Best Engagement does not require an
external provider key.

## Recommended workflow

1. Load the bundled fictional universe for a demonstration, or provide an absolute path to an
   approved CSV universe.
2. Define the planning period and any scoring or tier-coverage priorities. The planner first applies
   consent, minimum-gap, and score-threshold eligibility rules.
3. Generate one plan from the currently loaded universe and review eligibility, coverage, channel,
   representative, and capacity outcomes.
4. Export the plan before loading another universe. Every successful load advances the universe
   generation and invalidates earlier plan JSON.
5. Confirm consent, suppression lists, ownership, and local policies before representatives act.

## Example requests

```text
@hcp-universe.csv
Build a 30-day engagement plan for the field team. Prioritize Tier A coverage, respect channel
consent and a 14-day minimum gap, and keep every HCP with the assigned representative.

Using the fictional demo universe, show which eligible HCPs should receive an approved email,
remote meeting, or in-person visit next month. Explain coverage gaps and capacity constraints.

@hcp-universe.csv
Compare the eligible population with the recommended population, summarize Tier A and Tier B
coverage, and export reviewable CSV files. Do not contact any HCP.
```

## Outputs and safeguards

Every plan includes the required `universe_fingerprint` and `universe_generation` snapshot fields.
Metrics distinguish `total_universe`, `total_eligible`, and `total_planned`; tier-coverage
denominators use the same eligible population. `render_plan` fails closed unless the same universe
snapshot is still loaded.

CSV exports prefix text cells that begin with spreadsheet formula markers. The planner recommends
at most one action per HCP and preserves the assigned `rep_id`. Only in-person visits consume the
representative's visit capacity; remote meetings and approved emails do not.

Channel-diversity scoring rewards recent use of multiple channel types, but it does not replace
consent or local-policy review.

Recommendations are deterministic planning support, not autonomous outreach. The tools do not send
email, book meetings, or perform visits. The bundled universe is fictional demo data.

## Advanced usage

The equivalent direct tool sequence is:

```text
load_universe source="fixture"
recommend_engagements period_days=30 min_gap_days=14
  weight_recency_gap=0.30 weight_tier_value=0.30
  tier_a_coverage_pct=0.95 tier_b_coverage_pct=0.80
render_plan format="csv" plan_json="<JSON returned above>"
```

For a custom universe, pass its absolute CSV path to `load_universe`. Required columns are
`hcp_id`, `hcp_name`, `territory_id`, and `rep_id`. Consent, tier, activity, capacity, and extra CRM
fields are optional, but an email, phone, or meeting action is eligible only when the matching
consent value is explicitly `true`. `period_days` accepts values from 1 through 365.
