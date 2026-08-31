"""ta_compare — side-by-side scenario comparison."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class CompareArgs(BaseModel):
    scenarios: list[str] = Field(
        min_length=2,
        max_length=4,
        description="Names of 2-4 saved scenarios to compare",
    )
    focus: str = Field(
        default="",
        description="Objective to highlight: workload, travel, disruption, or coverage",
    )

    @model_validator(mode="after")
    def require_unique_scenarios(self) -> CompareArgs:
        if len(self.scenarios) != len(set(self.scenarios)):
            raise ValueError("scenario names must be unique")
        return self


TOOL: dict[str, Any] = {
    "name": "ta_compare",
    "description": (
        "Compare 2-4 saved territory alignment scenarios side by side. "
        "Returns an objectives matrix, Pareto analysis, per-HCP movement "
        "summary, and a plain-language trade-off narrative. Use after "
        "generating multiple scenarios with ta_align."
    ),
    "args": CompareArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from ..data import load_scenario, scenarios_share_input_universe

    try:
        args = CompareArgs.model_validate(arguments)
    except ValueError as exc:
        return [{"type": "text", "text": json.dumps({"error": str(exc)})}]
    names = args.scenarios
    focus = args.focus

    scenarios: dict[str, dict[str, Any]] = {}
    for name in names:
        try:
            data = load_scenario(name)
        except ValueError as exc:
            return [{"type": "text", "text": json.dumps({"error": str(exc)})}]
        if data is None:
            return [
                {"type": "text", "text": json.dumps({"error": f"Scenario '{name}' not found. Run ta_align first."})}
            ]
        scenarios[name] = data

    if not scenarios_share_input_universe(list(scenarios.values())):
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {"error": "Scenarios use different input universes and cannot be compared reliably."}
                ),
            }
        ]

    comparison_table = _build_comparison_table(scenarios)
    pareto = _pareto_analysis(scenarios)
    movements = _movement_summary(scenarios)
    narrative = _build_narrative(scenarios, comparison_table, pareto, focus)

    result = {
        "scenario_runs": {name: scenario.get("metadata", {}).get("run_id", "") for name, scenario in scenarios.items()},
        "comparison_table": comparison_table,
        "pareto": pareto,
        "movement_summary": movements,
        "trade_off_narrative": narrative,
    }
    return [{"type": "text", "text": json.dumps(result, indent=2)}]


def _build_comparison_table(
    scenarios: dict[str, dict[str, Any]],
) -> dict[str, dict[str, float]]:
    table: dict[str, dict[str, float]] = {}
    for name, data in scenarios.items():
        obj = data.get("objectives", {})
        table[name] = {
            "workload_balance": obj.get("workload_balance", 0),
            "travel_efficiency": obj.get("travel_efficiency", 0),
            "disruption": obj.get("disruption", 0),
            "coverage": obj.get("coverage", 0),
            "composite": obj.get("composite", 0),
        }
    return table


def _pareto_analysis(
    scenarios: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    names = list(scenarios.keys())
    objectives_keys = ["workload_balance", "travel_efficiency", "disruption", "coverage"]

    scores: dict[str, list[float]] = {}
    for name, data in scenarios.items():
        obj = data.get("objectives", {})
        scores[name] = [obj.get(k, 0) for k in objectives_keys]

    dominated: set[str] = set()
    for i, n1 in enumerate(names):
        for n2 in names[i + 1 :]:
            s1, s2 = scores[n1], scores[n2]
            if all(a <= b for a, b in zip(s1, s2)) and any(a < b for a, b in zip(s1, s2)):
                dominated.add(n2)
            elif all(a <= b for a, b in zip(s2, s1)) and any(a < b for a, b in zip(s2, s1)):
                dominated.add(n1)

    optimal = [n for n in names if n not in dominated]

    return {
        "pareto_optimal": optimal,
        "dominated": list(dominated),
    }


def _movement_summary(
    scenarios: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    names = list(scenarios.keys())
    if len(names) < 2:
        return {}

    rep_maps: dict[str, dict[str, str]] = {}
    for name, data in scenarios.items():
        rep_maps[name] = {a["hcp_id"]: a["primary_rep"] for a in data.get("assignments", [])}

    pairwise: dict[str, dict[str, int]] = {}
    for i, n1 in enumerate(names):
        for n2 in names[i + 1 :]:
            m1, m2 = rep_maps[n1], rep_maps[n2]
            common = set(m1.keys()) & set(m2.keys())
            moved = sum(1 for h in common if m1[h] != m2[h])
            key = f"{n1}_vs_{n2}"
            pairwise[key] = {
                "moved": moved,
                "total": len(common),
                "pct_moved": round(moved / max(len(common), 1) * 100, 1),
            }

    return pairwise


def _build_narrative(
    scenarios: dict[str, dict[str, Any]],
    table: dict[str, dict[str, float]],
    pareto: dict[str, Any],
    focus: str,
) -> str:
    names = list(table.keys())
    if len(names) < 2:
        return "Need at least 2 scenarios to compare."

    parts: list[str] = []

    optimal = pareto.get("pareto_optimal", [])
    if optimal:
        parts.append(f"Pareto-optimal scenario(s): {', '.join(optimal)}.")

    dominated = pareto.get("dominated", [])
    if dominated:
        parts.append(f"Dominated (worse on all objectives): {', '.join(dominated)}.")

    best_composite = min(names, key=lambda n: table[n]["composite"])
    parts.append(f"Lowest composite score: {best_composite} ({table[best_composite]['composite']:.3f}).")

    if focus and focus in ("workload", "travel", "disruption", "coverage"):
        key_map = {
            "workload": "workload_balance",
            "travel": "travel_efficiency",
            "disruption": "disruption",
            "coverage": "coverage",
        }
        fk = key_map[focus]
        best = min(names, key=lambda n: table[n][fk])
        worst = max(names, key=lambda n: table[n][fk])
        parts.append(
            f"Focus '{focus}': best = {best} ({table[best][fk]:.3f}), worst = {worst} ({table[worst][fk]:.3f})."
        )

    return " ".join(parts)
