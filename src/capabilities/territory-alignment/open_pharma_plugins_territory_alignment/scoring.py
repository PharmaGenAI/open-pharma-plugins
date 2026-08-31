"""Multi-objective scoring and normalisation for territory alignment."""

from __future__ import annotations

from .geo import haversine, travel_minutes
from .models import (
    HCP,
    AssignmentResult,
    Constraint,
    ObjectiveScores,
    ObjectiveWeights,
    RawMetrics,
    Rep,
)
from .workload import hcp_weekly_hours


def score_alignment(
    assignments: list[AssignmentResult],
    hcps: list[HCP],
    reps: list[Rep],
    weights: ObjectiveWeights,
    constraints: list[Constraint] | None = None,
    baseline: RawMetrics | None = None,
    disruption_excluded: set[str] | None = None,
) -> ObjectiveScores:
    """Score an alignment across four normalised objectives."""
    hcp_map = {h.hcp_id: h for h in hcps}
    rep_map = {r.rep_id: r for r in reps}

    raw = _compute_raw(assignments, hcp_map, rep_map, constraints or [], disruption_excluded or set())

    if baseline:
        wb = _normalise(raw.workload_gini, baseline.workload_gini)
        te = _normalise(raw.avg_travel_min, baseline.avg_travel_min)
        di = _normalise(raw.pct_reassigned, baseline.pct_reassigned)
        co = _normalise(100.0 - raw.pct_priority_covered, 100.0 - baseline.pct_priority_covered)
    else:
        wb = min(raw.workload_gini, 1.0)
        te = min(raw.avg_travel_min / 60.0, 1.0) if raw.avg_travel_min > 0 else 0.0
        di = min(raw.pct_reassigned / 100.0, 1.0)
        co = min((100.0 - raw.pct_priority_covered) / 100.0, 1.0)

    composite = (
        weights.workload_balance * wb + weights.travel_efficiency * te + weights.disruption * di + weights.coverage * co
    )

    return ObjectiveScores(
        workload_balance=round(wb, 4),
        travel_efficiency=round(te, 4),
        disruption=round(di, 4),
        coverage=round(co, 4),
        composite=round(composite, 4),
        raw=raw,
    )


def _compute_raw(
    assignments: list[AssignmentResult],
    hcp_map: dict[str, HCP],
    rep_map: dict[str, Rep],
    constraints: list[Constraint],
    disruption_excluded: set[str],
) -> RawMetrics:
    rep_workloads: dict[str, float] = {r: 0.0 for r in rep_map}
    rep_travel: dict[str, list[float]] = {r: [] for r in rep_map}
    reassigned = 0
    priority_total = 0
    priority_covered = 0

    for a in assignments:
        hcp = hcp_map.get(a.hcp_id)
        rep = rep_map.get(a.primary_rep)
        if not hcp or not rep:
            continue

        rep_workloads[a.primary_rep] = rep_workloads.get(a.primary_rep, 0.0) + hcp_weekly_hours(hcp, constraints)

        if hcp.lat is not None and hcp.lng is not None:
            dist = haversine(rep.base_lat, rep.base_lng, hcp.lat, hcp.lng)
            rep_travel.setdefault(a.primary_rep, []).append(travel_minutes(dist))

        if a.is_changed and a.hcp_id not in disruption_excluded:
            reassigned += 1

        if hcp.segment == "high":
            priority_total += 1
            priority_covered += 1

    all_hcps_high = sum(1 for h in hcp_map.values() if h.segment == "high")
    if all_hcps_high > priority_total:
        priority_total = all_hcps_high

    workloads = list(rep_workloads.values())
    gini = _gini(workloads) if workloads else 0.0

    all_travel = [t for ts in rep_travel.values() for t in ts]
    avg_travel = sum(all_travel) / max(len(all_travel), 1)
    max_travel = max(all_travel) if all_travel else 0.0

    disruption_denominator = sum(1 for assignment in assignments if assignment.hcp_id not in disruption_excluded)
    pct_reassigned = (reassigned / max(disruption_denominator, 1)) * 100.0
    pct_covered = 100.0 if priority_total == 0 else (priority_covered / priority_total) * 100.0

    return RawMetrics(
        workload_gini=round(gini, 4),
        avg_travel_min=round(avg_travel, 1),
        max_travel_min=round(max_travel, 1),
        pct_reassigned=round(pct_reassigned, 1),
        pct_priority_covered=round(pct_covered, 1),
    )


def _gini(values: list[float]) -> float:
    """Gini coefficient (0 = perfect equality, 1 = max inequality)."""
    if not values or all(v == 0 for v in values):
        return 0.0
    n = len(values)
    sorted_v = sorted(values)
    total = sum(sorted_v)
    weighted_sum = sum((i + 1) * v for i, v in enumerate(sorted_v))
    return round((2.0 * weighted_sum) / (n * total) - (n + 1.0) / n, 4)


def _normalise(value: float, baseline: float) -> float:
    """Normalise a metric relative to baseline. Returns 0-1 range."""
    if baseline <= 0:
        return 0.0 if value <= 0 else 1.0
    return min(max(value / baseline, 0.0), 2.0) / 2.0
