# Data Format Reference

All data files are CSV. Multi-value fields use `;` as separator.

## hcps.csv

| Column | Type | Required | Description |
|---|---|---|---|
| hcp_id | str | yes | Unique HCP identifier |
| name | str | yes | HCP full name |
| specialty | str | no | Medical specialty |
| segment | str | no | high, medium, or low (default: medium) |
| tier | int | no | 1, 2, or 3 (default: 2) |
| lat | float | no | Latitude (enables travel optimisation) |
| lng | float | no | Longitude |
| account_id | str | no | Account affiliation for grouping |
| consent_email | bool | no | Email consent (default: true) |
| consent_phone | bool | no | Phone consent (default: true) |
| consent_visit | bool | no | Visit consent (default: true) |
| annual_potential | float | no | Revenue potential (default: 0) |
| product_requirements | str | no | Required products, semicolon-separated |

Extra HCP columns are accepted and retained in the immutable scenario input
snapshot. Assignment and summary CSV exports use the documented stable columns.

## reps.csv

| Column | Type | Required | Description |
|---|---|---|---|
| rep_id | str | yes | Unique rep identifier |
| name | str | yes | Rep full name |
| base_lat | float | no | Home-base latitude |
| base_lng | float | no | Home-base longitude |
| product_expertise | str | no | Products covered, semicolon-separated |
| max_weekly_hours | float | no | Weekly hour capacity (default: 40) |
| max_daily_calls | int | no | Max calls per day (default: 8) |
| available_days | str | no | Available days, semicolon-separated (default: mon-fri) |

## current_alignment.csv

| Column | Type | Required | Description |
|---|---|---|---|
| hcp_id | str | yes | HCP identifier |
| primary_rep | str | yes | Currently assigned primary rep |
| secondary_rep | str | no | Secondary rep (if any) |

## constraints.csv

| Column | Type | Required | Description |
|---|---|---|---|
| type | str | yes | Constraint type |
| scope | str | yes | Where it applies |
| value | str | yes | Constraint value |
| description | str | no | Human-readable explanation |

Supported constraint types:

- `product_match` — scope: global, value: required
- `account_grouping` — scope: account:<id>, value: same_primary_rep
- `max_hcps_per_rep` — scope: global, value: integer
- `frequency_cap` — scope: segment:<name>, value: max weeks between visits

Identifiers must be unique. Alignment rows and account constraints must refer
to HCPs, reps, and accounts present in the same input set. Latitude, longitude,
segments, tiers, capacities, and available-day values are schema-validated at
load time; invalid or incomplete configured directories fail closed.
