"""ta_evaluate — detailed scoring breakdown for a saved scenario."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EvaluateArgs(BaseModel):
    scenario_name: str = Field(description="Name of a saved scenario to evaluate")


TOOL: dict[str, Any] = {
    "name": "ta_evaluate",
    "description": (
        "Evaluate a saved territory alignment scenario in detail. Returns "
        "objective scores, per-rep territory breakdown (HCP count, potential, "
        "workload, travel, segment coverage), relationship continuity stats, "
        "and workload distribution analysis. Call ta_align first to create a scenario."
    ),
    "args": EvaluateArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from ..data import load_scenario
    from ..models import HCP

    try:
        args = EvaluateArgs.model_validate(arguments)
    except ValueError as exc:
        return [{"type": "text", "text": json.dumps({"error": str(exc)})}]
    name = args.scenario_name
    try:
        scenario = load_scenario(name)
    except ValueError as exc:
        return [{"type": "text", "text": json.dumps({"error": str(exc)})}]

    if scenario is None:
        return [
            {
                "type": "text",
                "text": json.dumps({"error": f"Scenario '{name}' not found. Run ta_align first or check ta_status."}),
            }
        ]

    objectives = scenario.get("objectives", {})
    territories = scenario.get("territory_summary", [])
    assignments = scenario.get("assignments", [])
    unassigned = scenario.get("unassigned", [])
    saved_hcps = [HCP.model_validate(item) for item in scenario["input_snapshot"]["hcps"]]

    workloads = [t["workload_hours_weekly"] for t in territories]
    travel_hours = [t["travel_hours_weekly"] for t in territories]

    kept = sum(t.get("relationships_kept", 0) for t in territories)
    new = sum(t.get("relationships_new", 0) for t in territories)
    total_rel = kept + new

    evaluation = {
        "scenario_name": name,
        "run_id": scenario.get("metadata", {}).get("run_id", ""),
        "objectives": objectives,
        "territory_details": territories,
        "coverage_analysis": _coverage_analysis(assignments, unassigned, saved_hcps),
        "relationship_continuity": {
            "kept": kept,
            "new": new,
            "pct_kept": round(kept / max(total_rel, 1) * 100, 1),
        },
        "workload_distribution": {
            "min": round(min(workloads), 1) if workloads else 0,
            "max": round(max(workloads), 1) if workloads else 0,
            "median": round(sorted(workloads)[len(workloads) // 2], 1) if workloads else 0,
            "std_dev": round(_std(workloads), 2) if workloads else 0,
        },
        "travel_distribution": {
            "min_hours": round(min(travel_hours), 2) if travel_hours else 0,
            "max_hours": round(max(travel_hours), 2) if travel_hours else 0,
            "median_hours": round(sorted(travel_hours)[len(travel_hours) // 2], 2) if travel_hours else 0,
        },
        "unassigned": unassigned,
    }

    return [{"type": "text", "text": json.dumps(evaluation, indent=2)}]


def _coverage_analysis(
    assignments: list[dict],
    unassigned: list[dict],
    all_hcps: list[Any],
) -> dict[str, Any]:
    from ..models import HCP

    segment_totals: dict[str, int] = {}
    for h in all_hcps:
        seg = h.segment if isinstance(h, HCP) else "unknown"
        segment_totals[seg] = segment_totals.get(seg, 0) + 1

    segment_covered: dict[str, int] = {}
    for a in assignments:
        seg = a.get("segment", "unknown")
        segment_covered[seg] = segment_covered.get(seg, 0) + 1

    result: dict[str, Any] = {}
    for seg in sorted(segment_totals.keys()):
        total = segment_totals[seg]
        covered = segment_covered.get(seg, 0)
        result[seg] = {
            "total": total,
            "covered": covered,
            "unassigned": total - covered,
            "pct": round(covered / max(total, 1) * 100, 1),
        }
    return result


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return variance**0.5
