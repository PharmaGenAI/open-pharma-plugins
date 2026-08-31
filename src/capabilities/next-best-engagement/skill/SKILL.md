---
name: open-pharma-plugins-next-best-engagement
description: |
  Generate next-best-engagement plans for pharmaceutical field teams. Use
  when: (1) the user asks to plan HCP engagements, visits, or outreach for
  a territory or rep team; (2) someone says "next best action", "engagement
  plan", "call plan", "which HCPs should I visit", "territory plan",
  "rep allocation", "coverage plan"; (3) the user wants to optimise channel
  mix, rep utilisation, or HCP coverage; (4) the user wants to run a
  what-if scenario on engagement constraints.
---

# Next-Best-Engagement

Recommend which HCPs to engage, through which channel, by their assigned rep,
subject to visit-only capacity, explicit consent, channel-diversity, and
minimum-gap constraints.

## Available MCP tools

| Tool | Purpose | Returns |
|---|---|---|
| `load_universe` | Load HCP engagement data from CSV or built-in fixtures | Summary: HCP count, rep count, territories, tier distribution |
| `recommend_engagements` | Score HCPs, select actions, optimise allocation | Full engagement plan as structured JSON |
| `render_plan` | Export the plan as CSV files or JSON | File paths (CSV) or the plan (JSON) |

## Workflow 1: Generate Engagement Plan

Follow this sequence to produce a complete engagement plan.

```
1. LOAD DATA
   Call load_universe with source="fixture" for demo data, or provide a
   CSV path. The CSV needs four required columns: hcp_id, hcp_name,
   territory_id, rep_id. All other columns are optional with defaults.

2. REVIEW UNIVERSE
   Check the returned summary: HCP count, territories, tier distribution.
   Confirm the data looks correct before proceeding.

3. GENERATE PLAN
   Call recommend_engagements with default constraints, or override:
   - period_days (default 30, maximum 365)
   - tier coverage targets (A=95%, B=80%, C=50%, D=20%)
   - min_gap_days (default 7)
   - no_action_threshold (default 0.15)

4. REVIEW RESULTS
   Examine the returned plan:
   - universe_fingerprint and universe_generation: identify the exact loaded
     universe snapshot used to generate the plan
   - engagements: list of (HCP, one action, assigned rep, priority, score, rationale)
   - unassigned: HCPs that couldn't be assigned, with reasons
   - metrics: total_universe is every input HCP; total_eligible counts HCPs
     that pass consent, minimum-gap, and initial score-threshold checks;
     total_planned counts recommended actions, followed by coverage by tier,
     rep utilisation, and channel mix

5. EXPORT
   Call render_plan with the plan JSON:
   - format="csv" writes two uniquely named artifacts; use the returned
     `files.engagements_csv` and `files.summary_csv` paths
   - format="json" returns the structured plan
   - the same universe snapshot must still be loaded; any successful reload,
     including an identical reload, advances universe_generation and makes
     earlier plan JSON stale

6. ITERATE (optional)
   Adjust constraints and re-run recommend_engagements to compare plans.
```

## Workflow 2: Quick Demo

When the user wants to see the capability in action:

```
1. Call load_universe with source="fixture"
2. Call recommend_engagements with defaults
3. Summarise the metrics to the user:
   - Total HCP universe, eligible HCPs, and planned HCPs
   - Coverage by tier vs targets
   - Channel mix breakdown
   - Rep utilisation spread (in-person visits consume capacity; remote meetings
     and approved emails do not)
4. Show 3-5 example engagements with rationale
```

## Workflow 3: Custom Data from Databricks

When connecting to a data lake or CRM:

```
1. Help the user write a SQL query that produces the required shape:
   SELECT
     hcp_id, hcp_name, territory_id, rep_id,
     tier, specialty, rep_name, rep_max_visits_per_week,
     consent_email, consent_phone,
     last_visit_date, last_email_date, last_meeting_date,
     visits_last_90d, emails_last_90d
   FROM gold.commercial.hcp_engagement_universe
   WHERE territory_id IN (...)

2. Save the query result to CSV
3. Call load_universe with the CSV path
4. Continue with Workflow 1 from step 3
```

## Scoring model

Each HCP is scored 0–1 using five weighted factors:

| Factor | Weight | What it measures |
|---|---|---|
| Recency gap | 0.30 | Days since last touch vs target interval |
| Tier value | 0.30 | A=1.0, B=0.7, C=0.4, D=0.15 |
| Engagement velocity | 0.15 | Cold HCPs (0 touches in 90d) score higher |
| Channel diversity | 0.15 | Rewards recent use of multiple channel types |
| Coverage debt | 0.10 | How far the tier is from its coverage target |

## Action selection rules (priority order)

1. Tier A/B + no visit in 45+ days + phone consent → **in-person visit**
2. Visited within 30 days + email consent → **approved email** (follow-up)
3. Not visited in 30+ days + phone consent → **remote meeting**
4. Email consent available → **approved email** (fallback)
5. Phone consent available → **in-person visit** (fallback)
6. No channels available → **no action**

## Input CSV format

Required columns (4):

| Column | Type | Description |
|---|---|---|
| `hcp_id` | string | Unique HCP identifier |
| `hcp_name` | string | HCP full name |
| `territory_id` | string | Territory assignment |
| `rep_id` | string | Assigned-rep identifier; the planner preserves this ownership |

Optional columns (with defaults):

| Column | Type | Default |
|---|---|---|
| `tier` | A/B/C/D | B |
| `specialty` | string | General |
| `rep_name` | string | (derived from rep_id) |
| `rep_max_visits_per_week` | int | 20; applies only to in-person visits |
| `consent_email` | bool | unknown / no action until explicitly true |
| `consent_phone` | bool | unknown / no action until explicitly true |
| `last_visit_date` | date | (none) |
| `last_email_date` | date | (none) |
| `last_meeting_date` | date | (none) |
| `visits_last_90d` | int | 0 |
| `emails_last_90d` | int | 0 |

Any additional columns (e.g. `kol_flag`, `affiliation`, `prescribing_volume`)
are preserved. During CSV export, text cells beginning with spreadsheet
formula markers are prefixed with an apostrophe to prevent formula execution.

## Configuration and decision boundary

`OPEN_PHARMA_NBE_OUTPUT_DIR` defaults to
`~/.open-pharma-plugins/next-best-engagement`. Each HCP receives at most one
recommended action; the plan never performs outreach. An email or phone/meeting
action requires the corresponding consent field to be explicitly `true`;
missing or false consent is not permission. The planner keeps each HCP with the
assigned `rep_id`, and only in-person visits consume that rep's capacity.
Channel-diversity scoring rewards recent use of multiple channel types but does
not override consent, suppression lists, assigned-rep/territory ownership, or
local policy. Confirm those controls before acting on an exported plan.
