# Objective Function Reference

The alignment optimises a weighted sum of four objectives, all normalised to
0-1 (lower is better).

## Workload Balance

**Metric:** Gini coefficient of weekly workload hours across reps.

**Computation:** Each HCP contributes weekly hours based on segment and the
configured `frequency_cap` (maximum weeks between visits):

- high: 1.0 hr/visit x 24 visits/yr / 48 weeks = 0.50 hr/week
- medium: 0.75 hr x 12 visits / 48 = 0.19 hr/week
- low: 0.50 hr x 6 visits / 48 = 0.06 hr/week

These examples use the default 2/4/8-week caps. The Gini calculation includes
active reps with zero assignments. Gini = 0 means perfect equality; 0.3+
indicates significant imbalance.

## Travel Efficiency

**Metric:** Average one-way travel minutes from rep base to assigned HCPs.

**Computation:** Haversine distance x assumed 40 km/h urban speed. Normalised
against initial alignment baseline.

**Without lat/lng:** This objective scores 0 (neutral) — optimisation focuses
on the other three objectives.

## Disruption

**Metric:** Fraction of HCPs whose primary rep changed vs current alignment.

**Computation:** Count of reassigned HCPs / assigned, changeable HCPs. HCPs
fixed by locked reps or explicit overrides are excluded from both numerator
and denominator.

## Coverage

**Metric:** Fraction of high-segment HCPs not assigned (inverted: lower = better coverage).

**Computation:** (1 - high-segment covered / total high-segment). A value of
0 means every high-segment HCP has a primary rep.

If the input contains no high-segment HCPs, coverage is complete rather than
penalized.

## Baseline normalization

The raw metrics above are persisted under `objectives.raw`. Objective costs are
then normalized against the saved current-alignment baseline. For a positive
baseline metric, `min(current / baseline, 2) / 2` makes an unchanged value 0.5,
an improvement lower than 0.5, and a value at least twice the baseline 1.0. If
the baseline is zero, an unchanged zero costs 0 and any regression costs 1.
Coverage normalizes the uncovered percentage. Lower is always better.

## Composite Score

`composite = w_workload x workload + w_travel x travel + w_disruption x disruption + w_coverage x coverage`

Default weights: 0.30, 0.25, 0.25, 0.20.
