"""Assignment solver: greedy seed + swap-based local search."""

from __future__ import annotations

from typing import Any

from .geo import haversine, travel_minutes
from .models import (
    HCP,
    AssignmentResult,
    Constraint,
    CurrentAssignment,
    NewHire,
    ObjectiveWeights,
    Override,
    Rep,
    ScenarioInputSnapshot,
    ScenarioLevers,
    ScenarioResult,
    TerritorySummary,
    UnassignedHCP,
)
from .scoring import score_alignment
from .workload import hcp_weekly_hours, hours_per_visit, visits_per_year


def solve(
    hcps: list[HCP],
    reps: list[Rep],
    current_alignment: list[CurrentAssignment],
    constraints: list[Constraint],
    weights: ObjectiveWeights,
    overrides: list[Override] | None = None,
    lock_reps: list[str] | None = None,
    scenario_name: str = "unnamed",
    max_iterations: int = 2000,
    vacancies: list[str] | None = None,
    new_hires: list[NewHire] | None = None,
    data_provenance: dict[str, Any] | None = None,
) -> ScenarioResult:
    """Run greedy assignment + local search optimisation."""
    _validate_problem(
        hcps,
        reps,
        current_alignment,
        overrides or [],
        lock_reps or [],
        max_iterations,
        vacancies or [],
    )
    hcp_map = {h.hcp_id: h for h in hcps}
    rep_map = {r.rep_id: r for r in reps}
    current_map = {a.hcp_id: a.primary_rep for a in current_alignment}
    override_map = {o.hcp_id: o.rep_id for o in (overrides or [])}
    locked = set(lock_reps or [])
    max_per_rep = _get_max_per_rep(constraints)
    account_groups = _get_account_groups(constraints, hcps)

    locked_hcps: set[str] = set()
    for hcp_id, rep_id in current_map.items():
        if rep_id in locked:
            locked_hcps.add(hcp_id)
    for hcp_id in override_map:
        locked_hcps.add(hcp_id)

    assignment: dict[str, str] = {}
    rep_counts: dict[str, int] = {r.rep_id: 0 for r in reps}
    rep_workloads: dict[str, float] = {r.rep_id: 0.0 for r in reps}
    unassigned: list[UnassignedHCP] = []

    for hcp_id, rep_id in override_map.items():
        hcp = hcp_map[hcp_id]
        rep = rep_map[rep_id]
        if not _is_eligible(hcp, rep, constraints):
            raise ValueError(f"override {hcp_id} -> {rep_id} violates product_match")
        proposed_count = rep_counts.get(rep_id, 0) + 1
        proposed_workload = rep_workloads.get(rep_id, 0.0) + hcp_weekly_hours(hcp, constraints)
        if proposed_count > max_per_rep or proposed_workload > rep.max_weekly_hours:
            raise ValueError(f"override {hcp_id} -> {rep_id} exceeds rep capacity")
        assignment[hcp_id] = rep_id
        rep_counts[rep_id] = proposed_count
        rep_workloads[rep_id] = proposed_workload

    for hcp_id in locked_hcps:
        if hcp_id not in assignment and hcp_id in current_map:
            rep_id = current_map[hcp_id]
            if rep_id in rep_map:
                hcp = hcp_map[hcp_id]
                rep = rep_map[rep_id]
                proposed_count = rep_counts.get(rep_id, 0) + 1
                proposed_workload = rep_workloads.get(rep_id, 0.0) + hcp_weekly_hours(hcp, constraints)
                if not _is_eligible(hcp, rep, constraints):
                    raise ValueError(f"locked assignment {hcp_id} -> {rep_id} violates product_match")
                if proposed_count > max_per_rep or proposed_workload > rep.max_weekly_hours:
                    raise ValueError(f"locked assignments for rep {rep_id!r} exceed capacity")
                assignment[hcp_id] = rep_id
                rep_counts[rep_id] = proposed_count
                rep_workloads[rep_id] = proposed_workload

    _assign_account_groups(
        account_groups,
        hcps,
        reps,
        assignment,
        rep_counts,
        rep_workloads,
        constraints,
        current_map,
        max_per_rep,
        rep_map,
        locked_hcps,
        unassigned,
    )
    grouped_hcp_ids = {hcp_id for hcp_ids in account_groups.values() for hcp_id in hcp_ids}
    remaining = [h for h in hcps if h.hcp_id not in assignment and h.hcp_id not in grouped_hcp_ids]
    remaining.sort(key=lambda h: h.annual_potential, reverse=True)

    for hcp in remaining:
        best_rep = _best_eligible_rep(
            hcp,
            reps,
            rep_counts,
            rep_workloads,
            constraints,
            current_map,
            max_per_rep,
            rep_map,
        )
        if best_rep:
            assignment[hcp.hcp_id] = best_rep
            rep_counts[best_rep] = rep_counts.get(best_rep, 0) + 1
            rep_workloads[best_rep] = rep_workloads.get(best_rep, 0.0) + hcp_weekly_hours(hcp, constraints)
        else:
            unassigned.append(
                UnassignedHCP(
                    hcp_id=hcp.hcp_id,
                    reason=_unassign_reason(hcp, reps, rep_counts, constraints, max_per_rep, rep_map),
                )
            )

    baseline_assignment = {
        hcp_id: rep_id for hcp_id, rep_id in current_map.items() if hcp_id in hcp_map and rep_id in rep_map
    }
    baseline_assignments = _build_assignments(
        baseline_assignment,
        hcp_map,
        rep_map,
        current_map,
        constraints,
    )
    baseline_raw = score_alignment(
        baseline_assignments,
        hcps,
        reps,
        weights,
        constraints,
        disruption_excluded=locked_hcps,
    ).raw

    iterations_used = _local_search(
        assignment,
        hcps,
        reps,
        constraints,
        weights,
        current_map,
        locked_hcps,
        max_per_rep,
        hcp_map,
        rep_map,
        max_iterations,
        account_groups,
        baseline_raw,
    )

    assignments = _build_assignments(assignment, hcp_map, rep_map, current_map, constraints)
    objectives = score_alignment(
        assignments,
        hcps,
        reps,
        weights,
        constraints,
        baseline_raw,
        locked_hcps,
    )
    territory = _build_territory_summary(assignments, hcp_map, rep_map, current_map, constraints)

    from datetime import datetime, timezone
    from uuid import uuid4

    from . import __version__

    provenance = data_provenance or {}
    return ScenarioResult(
        scenario_name=scenario_name,
        assignments=assignments,
        territory_summary=territory,
        objectives=objectives,
        unassigned=unassigned,
        weights_used=weights,
        input_snapshot=ScenarioInputSnapshot(
            hcps=hcps,
            reps=reps,
            current_alignment=current_alignment,
            constraints=constraints,
            levers=ScenarioLevers(
                vacancies=vacancies or [],
                new_hires=new_hires or [],
                overrides=overrides or [],
                lock_reps=lock_reps or [],
            ),
        ),
        metadata={
            "schema_version": "1.0",
            "plugin_version": __version__,
            "run_id": uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "solver": "greedy_local_search",
            "iterations": iterations_used,
            "data_source": provenance.get("data_source", "in-memory"),
            "fixture_data": bool(provenance.get("fixture_data", False)),
            "input_fingerprints": dict(provenance.get("input_fingerprints", {})),
            "decision_support_only": True,
        },
    )


def _validate_problem(
    hcps: list[HCP],
    reps: list[Rep],
    current_alignment: list[CurrentAssignment],
    overrides: list[Override],
    lock_reps: list[str],
    max_iterations: int,
    vacancies: list[str],
) -> None:
    if max_iterations < 0 or max_iterations > 10000:
        raise ValueError("max_iterations must be between 0 and 10000")
    hcp_ids = [hcp.hcp_id for hcp in hcps]
    rep_ids = [rep.rep_id for rep in reps]
    if len(hcp_ids) != len(set(hcp_ids)):
        raise ValueError("HCP identifiers must be unique")
    if len(rep_ids) != len(set(rep_ids)):
        raise ValueError("rep identifiers must be unique")
    known_hcps = set(hcp_ids)
    known_reps = set(rep_ids)
    known_current_reps = known_reps | set(vacancies)
    current_hcp_ids = [item.hcp_id for item in current_alignment]
    if len(current_hcp_ids) != len(set(current_hcp_ids)):
        raise ValueError("current alignment HCP identifiers must be unique")
    for current in current_alignment:
        if current.hcp_id not in known_hcps:
            raise ValueError(f"current alignment references unknown HCP {current.hcp_id!r}")
        if current.primary_rep not in known_current_reps:
            raise ValueError(f"current alignment references unknown rep {current.primary_rep!r}")
        if current.secondary_rep and current.secondary_rep not in known_current_reps:
            raise ValueError(f"current alignment references unknown rep {current.secondary_rep!r}")
    override_hcp_ids = [item.hcp_id for item in overrides]
    if len(override_hcp_ids) != len(set(override_hcp_ids)):
        raise ValueError("override HCP identifiers must be unique")
    for override in overrides:
        if override.hcp_id not in known_hcps:
            raise ValueError(f"override references unknown HCP {override.hcp_id!r}")
        if override.rep_id not in known_reps:
            raise ValueError(f"override references unknown rep {override.rep_id!r}")
    unknown_locks = sorted(set(lock_reps) - known_reps)
    if unknown_locks:
        raise ValueError(f"lock_reps references unknown reps: {', '.join(unknown_locks)}")


# -- greedy helpers --


def _best_eligible_rep(
    hcp: HCP,
    reps: list[Rep],
    rep_counts: dict[str, int],
    rep_workloads: dict[str, float],
    constraints: list[Constraint],
    current_map: dict[str, str],
    max_per_rep: int,
    rep_map: dict[str, Rep],
) -> str | None:
    """Find the best eligible rep for an HCP."""
    best_score = float("inf")
    best_rep = None
    hcp_hours = hcp_weekly_hours(hcp, constraints)

    for rep in reps:
        if not _is_eligible(hcp, rep, constraints):
            continue
        if rep_counts.get(rep.rep_id, 0) >= max_per_rep:
            continue
        if rep_workloads.get(rep.rep_id, 0.0) + hcp_hours > rep.max_weekly_hours:
            continue

        travel = 0.0
        if hcp.lat is not None and hcp.lng is not None:
            travel = haversine(rep.base_lat, rep.base_lng, hcp.lat, hcp.lng)

        disruption = 0.0 if current_map.get(hcp.hcp_id) == rep.rep_id else 1.0

        score = travel * 0.5 + disruption * 50.0
        if score < best_score:
            best_score = score
            best_rep = rep.rep_id

    return best_rep


def _is_eligible(hcp: HCP, rep: Rep, constraints: list[Constraint]) -> bool:
    """Check if rep meets hard constraints for this HCP."""
    for c in constraints:
        if c.type == "product_match" and c.value == "required":
            if hcp.product_requirements:
                if not all(p in rep.product_expertise for p in hcp.product_requirements):
                    return False
    return True


def _unassign_reason(
    hcp: HCP,
    reps: list[Rep],
    rep_counts: dict[str, int],
    constraints: list[Constraint],
    max_per_rep: int,
    rep_map: dict[str, Rep],
) -> str:
    eligible = [r for r in reps if _is_eligible(hcp, r, constraints)]
    if not eligible:
        return "no eligible rep has required product expertise"
    hcp_hours = hcp_weekly_hours(hcp, constraints)
    for r in eligible:
        if rep_counts.get(r.rep_id, 0) < max_per_rep:
            rep = rep_map.get(r.rep_id)
            if rep and rep.max_weekly_hours >= hcp_hours:
                return "unknown"
    at_capacity = all(rep_counts.get(r.rep_id, 0) >= max_per_rep for r in eligible)
    if at_capacity:
        return "all eligible reps at capacity"
    return "all eligible reps would exceed weekly hour limit"


def _get_max_per_rep(constraints: list[Constraint]) -> int:
    for c in constraints:
        if c.type == "max_hcps_per_rep" and c.scope == "global":
            try:
                return int(c.value)
            except (ValueError, TypeError):
                pass
    return 200


def _get_account_groups(
    constraints: list[Constraint],
    hcps: list[HCP],
) -> dict[str, list[str]]:
    """Return {account_id: [hcp_ids]} for accounts with a same_primary_rep constraint."""
    constrained_accounts: set[str] = set()
    for c in constraints:
        if c.type == "account_grouping" and c.value == "same_primary_rep":
            parts = c.scope.split(":", 1)
            if len(parts) == 2 and parts[0] == "account":
                constrained_accounts.add(parts[1])

    groups: dict[str, list[str]] = {}
    for h in hcps:
        if h.account_id in constrained_accounts:
            groups.setdefault(h.account_id, []).append(h.hcp_id)
    return groups


def _assign_account_groups(
    account_groups: dict[str, list[str]],
    hcps: list[HCP],
    reps: list[Rep],
    assignment: dict[str, str],
    rep_counts: dict[str, int],
    rep_workloads: dict[str, float],
    constraints: list[Constraint],
    current_map: dict[str, str],
    max_per_rep: int,
    rep_map: dict[str, Rep],
    locked_hcps: set[str],
    unassigned: list[UnassignedHCP],
) -> None:
    """Assign account-grouped HCPs to a single rep per account."""
    hcp_map = {h.hcp_id: h for h in hcps}
    for account_id, hcp_ids in account_groups.items():
        assigned_reps = {assignment[hid] for hid in hcp_ids if hid in assignment}
        if len(assigned_reps) > 1:
            raise ValueError(f"account group {account_id!r} is pinned to multiple reps")
        unassigned_ids = [hid for hid in hcp_ids if hid not in assignment]
        if not unassigned_ids:
            continue

        already_assigned_rep = next(iter(assigned_reps), None)

        group_hcps = [hcp_map[hid] for hid in unassigned_ids if hid in hcp_map]
        if not group_hcps:
            continue

        group_hours = sum(hcp_weekly_hours(h, constraints) for h in group_hcps)

        if already_assigned_rep and already_assigned_rep in rep_map:
            rep = rep_map[already_assigned_rep]
            all_eligible = all(_is_eligible(h, rep, constraints) for h in group_hcps)
            fits_count = rep_counts.get(already_assigned_rep, 0) + len(group_hcps) <= max_per_rep
            fits_hours = rep_workloads.get(already_assigned_rep, 0.0) + group_hours <= rep.max_weekly_hours
            if all_eligible and fits_count and fits_hours:
                for h in group_hcps:
                    assignment[h.hcp_id] = already_assigned_rep
                    rep_counts[already_assigned_rep] = rep_counts.get(already_assigned_rep, 0) + 1
                    rep_workloads[already_assigned_rep] = rep_workloads.get(
                        already_assigned_rep, 0.0
                    ) + hcp_weekly_hours(h, constraints)
                continue
            raise ValueError(f"pinned account group {account_id!r} cannot fit rep {already_assigned_rep!r}")

        best_rep = None
        best_score = float("inf")
        for rep in reps:
            all_eligible = all(_is_eligible(h, rep, constraints) for h in group_hcps)
            if not all_eligible:
                continue
            if rep_counts.get(rep.rep_id, 0) + len(group_hcps) > max_per_rep:
                continue
            if rep_workloads.get(rep.rep_id, 0.0) + group_hours > rep.max_weekly_hours:
                continue

            travel = 0.0
            geo_count = 0
            for h in group_hcps:
                if h.lat is not None and h.lng is not None:
                    travel += haversine(rep.base_lat, rep.base_lng, h.lat, h.lng)
                    geo_count += 1
            avg_travel = travel / max(geo_count, 1)

            disruption = sum(1 for h in group_hcps if current_map.get(h.hcp_id) != rep.rep_id)
            score = avg_travel * 0.5 + disruption * 50.0
            if score < best_score:
                best_score = score
                best_rep = rep.rep_id

        if best_rep:
            for h in group_hcps:
                assignment[h.hcp_id] = best_rep
                rep_counts[best_rep] = rep_counts.get(best_rep, 0) + 1
                rep_workloads[best_rep] = rep_workloads.get(best_rep, 0.0) + hcp_weekly_hours(h, constraints)
        else:
            for h in group_hcps:
                unassigned.append(
                    UnassignedHCP(
                        hcp_id=h.hcp_id,
                        reason=f"account group {account_id!r} cannot fit one eligible rep",
                    )
                )


# -- local search --


def _local_search(
    assignment: dict[str, str],
    hcps: list[HCP],
    reps: list[Rep],
    constraints: list[Constraint],
    weights: ObjectiveWeights,
    current_map: dict[str, str],
    locked_hcps: set[str],
    max_per_rep: int,
    hcp_map: dict[str, HCP],
    rep_map: dict[str, Rep],
    max_iterations: int,
    account_groups: dict[str, list[str]] | None = None,
    baseline: Any | None = None,
) -> int:
    """Improve assignment via pairwise swaps. Returns iterations used."""
    grouped_hcps: set[str] = set()
    for ids in (account_groups or {}).values():
        grouped_hcps.update(ids)

    movable = [
        h for h in hcps if h.hcp_id in assignment and h.hcp_id not in locked_hcps and h.hcp_id not in grouped_hcps
    ]

    current_assignments = _build_assignments(assignment, hcp_map, rep_map, current_map, constraints)
    current_score = score_alignment(
        current_assignments,
        hcps,
        reps,
        weights,
        constraints,
        baseline,
        locked_hcps,
    )
    best_composite = current_score.composite
    rep_workloads: dict[str, float] = {rep.rep_id: 0.0 for rep in reps}
    for hcp_id, rep_id in assignment.items():
        rep_workloads[rep_id] += hcp_weekly_hours(hcp_map[hcp_id], constraints)

    for iteration in range(max_iterations):
        improved = False

        for i, h1 in enumerate(movable):
            for h2 in movable[i + 1 :]:
                r1 = assignment[h1.hcp_id]
                r2 = assignment[h2.hcp_id]
                if r1 == r2:
                    continue

                rep1 = rep_map.get(r1)
                rep2 = rep_map.get(r2)
                if not rep1 or not rep2:
                    continue
                if not _is_eligible(h1, rep2, constraints):
                    continue
                if not _is_eligible(h2, rep1, constraints):
                    continue

                h1_hours = hcp_weekly_hours(h1, constraints)
                h2_hours = hcp_weekly_hours(h2, constraints)
                r1_workload = rep_workloads[r1] - h1_hours + h2_hours
                r2_workload = rep_workloads[r2] - h2_hours + h1_hours
                if r1_workload > rep1.max_weekly_hours or r2_workload > rep2.max_weekly_hours:
                    continue

                assignment[h1.hcp_id] = r2
                assignment[h2.hcp_id] = r1

                trial = _build_assignments(assignment, hcp_map, rep_map, current_map, constraints)
                trial_score = score_alignment(
                    trial,
                    hcps,
                    reps,
                    weights,
                    constraints,
                    baseline,
                    locked_hcps,
                )

                if trial_score.composite < best_composite - 0.0005:
                    best_composite = trial_score.composite
                    rep_workloads[r1] = r1_workload
                    rep_workloads[r2] = r2_workload
                    improved = True
                else:
                    assignment[h1.hcp_id] = r1
                    assignment[h2.hcp_id] = r2

        if not improved:
            return iteration + 1

    return max_iterations


# -- output builders --


def _build_assignments(
    assignment: dict[str, str],
    hcp_map: dict[str, HCP],
    rep_map: dict[str, Rep],
    current_map: dict[str, str],
    constraints: list[Constraint],
) -> list[AssignmentResult]:
    results: list[AssignmentResult] = []
    for hcp_id, rep_id in sorted(assignment.items()):
        hcp = hcp_map.get(hcp_id)
        rep = rep_map.get(rep_id)
        if not hcp or not rep:
            continue

        prev_rep = current_map.get(hcp_id, "")
        is_changed = prev_rep != rep_id and prev_rep != ""

        travel = 0.0
        if hcp.lat is not None and hcp.lng is not None:
            travel = travel_minutes(haversine(rep.base_lat, rep.base_lng, hcp.lat, hcp.lng))

        visits = visits_per_year(hcp, constraints)

        data: dict[str, Any] = {
            "hcp_id": hcp_id,
            "hcp_name": hcp.name,
            "primary_rep": rep_id,
            "previous_rep": prev_rep,
            "is_changed": is_changed,
            "change_reason": "optimiser_reassigned" if is_changed else "",
            "estimated_travel_min": round(travel, 1),
            "estimated_annual_visits": visits,
            "segment": hcp.segment,
            "tier": hcp.tier,
        }
        data.update(hcp.model_extra)
        results.append(AssignmentResult.model_validate(data))

    return results


def _build_territory_summary(
    assignments: list[AssignmentResult],
    hcp_map: dict[str, HCP],
    rep_map: dict[str, Rep],
    current_map: dict[str, str],
    constraints: list[Constraint],
) -> list[TerritorySummary]:
    rep_data: dict[str, dict[str, Any]] = {
        rep_id: {
            "hcp_count": 0,
            "total_potential": 0.0,
            "workload": 0.0,
            "travel": 0.0,
            "seg_high": 0,
            "seg_med": 0,
            "seg_low": 0,
            "kept": 0,
            "new": 0,
        }
        for rep_id in rep_map
    }

    for a in assignments:
        hcp = hcp_map.get(a.hcp_id)
        if not hcp:
            continue
        rd = rep_data.setdefault(
            a.primary_rep,
            {
                "hcp_count": 0,
                "total_potential": 0.0,
                "workload": 0.0,
                "travel": 0.0,
                "seg_high": 0,
                "seg_med": 0,
                "seg_low": 0,
                "kept": 0,
                "new": 0,
            },
        )
        rd["hcp_count"] += 1
        rd["total_potential"] += hcp.annual_potential

        visits = visits_per_year(hcp, constraints)
        rd["workload"] += hours_per_visit(hcp) * visits / 48.0
        rd["travel"] += a.estimated_travel_min / 60.0 * (visits / 48.0)

        if hcp.segment == "high":
            rd["seg_high"] += 1
        elif hcp.segment == "medium":
            rd["seg_med"] += 1
        else:
            rd["seg_low"] += 1

        if current_map.get(a.hcp_id) == a.primary_rep:
            rd["kept"] += 1
        else:
            rd["new"] += 1

    result = []
    for rep_id in sorted(rep_data.keys()):
        rd = rep_data[rep_id]
        rep = rep_map.get(rep_id)
        result.append(
            TerritorySummary(
                rep_id=rep_id,
                rep_name=rep.name if rep else rep_id,
                hcp_count=rd["hcp_count"],
                total_potential=round(rd["total_potential"], 0),
                workload_hours_weekly=round(rd["workload"], 1),
                travel_hours_weekly=round(rd["travel"], 1),
                segment_high=rd["seg_high"],
                segment_medium=rd["seg_med"],
                segment_low=rd["seg_low"],
                relationships_kept=rd["kept"],
                relationships_new=rd["new"],
            )
        )
    return result
