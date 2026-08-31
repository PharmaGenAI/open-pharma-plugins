# Workflow Examples

## Example 1: Quarterly Rebalance After a Resignation

Rep R004 is leaving. Model the impact and evaluate adding a replacement.

```
Step 1:  ta_align  scenario_name="baseline"
Step 2:  ta_align  scenario_name="without_r004"  vacancies=["R004"]
Step 3:  ta_align  scenario_name="replace_r004"  vacancies=["R004"]
                   new_hires=[{rep_id:"R009", name:"J. Rivera",
                               base_lat:34.05, base_lng:-118.24,
                               product_expertise:["product_a"]}]
Step 4:  ta_compare  scenarios=["baseline", "without_r004", "replace_r004"]
```

Expected insight: "without_r004" shows workload imbalance spike; "replace_r004"
restores balance but with some disruption from reassignment.

## Example 2: Rebalance with KOL Protection

Rebalance workload but protect a key opinion leader relationship.

```
Step 1:  ta_align  scenario_name="baseline"
Step 2:  ta_align  scenario_name="rebalance_protected"
                   overrides=[{hcp_id:"H001", rep_id:"R001", reason:"KOL"}]
                   weight_workload=0.45  weight_disruption=0.15
Step 3:  ta_compare  scenarios=["baseline", "rebalance_protected"]
```

## Example 3: Weekly Visit Planning

Plan next week's visits for Rep R001.

```
Step 1:  ta_status  — confirm alignment exists
Step 2:  ta_cluster  scenario_name="baseline"  rep_id="R001"  period="next_week"
                     remote_threshold_min=60
```

Review: each cluster = one day's visits. HCPs above the travel threshold are
flagged as remote meeting alternatives. Also review `excluded_no_visit_consent`,
`unplanned_hcp_ids`, fixed appointment times, and route warnings before use.

## Example 4: Constraint-Compatible Partial Territory Freeze

Freeze the fixture representatives whose current books already satisfy the hard
product-match rule, and rebalance the remaining territories.

```
Step 1:  ta_align  scenario_name="se_rebalance"
                   lock_reps=["R001","R002","R003","R005"]
```

The fixture's current `R004` and `R006` books contain assignments that violate
`product_match`, so the hardened solver correctly refuses to freeze those books
as-is. Clean the source alignment or leave those reps unlocked for optimization.
