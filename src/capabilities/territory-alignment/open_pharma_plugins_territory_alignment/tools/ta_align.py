"""ta_align — generate or adjust territory alignment (strategic mode)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class OverrideArg(BaseModel):
    hcp_id: str = Field(description="HCP to pin to a specific rep")
    rep_id: str = Field(description="Rep to assign")
    reason: str = Field(default="", description="Why this override exists")


class NewHireArg(BaseModel):
    rep_id: str = Field(description="New rep identifier")
    name: str = Field(description="New rep name")
    base_lat: float = Field(ge=-90.0, le=90.0, description="Home-base latitude")
    base_lng: float = Field(ge=-180.0, le=180.0, description="Home-base longitude")
    product_expertise: list[str] = Field(
        default_factory=list, description="Products the new rep covers (semicolon-separated)"
    )
    max_weekly_hours: float = Field(default=40.0, gt=0.0, le=168.0, description="Weekly hour capacity")
    max_daily_calls: int = Field(default=8, ge=1, le=100, description="Maximum calls per available day")
    available_days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] = Field(
        default_factory=lambda: ["mon", "tue", "wed", "thu", "fri"],
        min_length=1,
        description="Available weekday names",
    )


class AlignArgs(BaseModel):
    scenario_name: str = Field(
        min_length=1,
        max_length=200,
        description="Label for this scenario, e.g. 'baseline' or 'minus_r004'",
    )
    weight_workload: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Override workload balance weight (default 0.30)"
    )
    weight_travel: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Override travel efficiency weight (default 0.25)"
    )
    weight_disruption: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Override disruption weight (default 0.25)"
    )
    weight_coverage: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Override coverage weight (default 0.20)"
    )
    vacancies: list[str] = Field(
        default_factory=list,
        description="Rep IDs to model as vacant; their HCPs are redistributed",
    )
    new_hires: list[NewHireArg] = Field(default_factory=list, description="New reps to add for this scenario")
    overrides: list[OverrideArg] = Field(
        default_factory=list, description="Manual HCP-to-rep pins the solver must respect"
    )
    lock_reps: list[str] = Field(
        default_factory=list,
        description="Rep IDs whose current HCPs must not be reassigned",
    )
    max_iterations: int = Field(default=2000, ge=0, le=10000, description="Max local-search iterations")

    @model_validator(mode="after")
    def require_unique_levers(self) -> AlignArgs:
        collections = {
            "vacancies": self.vacancies,
            "new_hires": [item.rep_id for item in self.new_hires],
            "overrides": [item.hcp_id for item in self.overrides],
            "lock_reps": self.lock_reps,
        }
        for label, values in collections.items():
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must not contain duplicate identifiers")
        return self


TOOL: dict[str, Any] = {
    "name": "ta_align",
    "description": (
        "Generate a territory alignment scenario. Assigns HCPs to reps under "
        "multi-objective optimisation balancing workload, travel, disruption, "
        "and coverage. Supports vacancies, new hires, manual overrides, and "
        "locked reps. Results are saved as a named scenario for later "
        "evaluation or comparison. Call ta_status first to confirm data is loaded."
    ),
    "args": AlignArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from ..data import (
        get_constraints,
        get_current_alignment,
        get_data_provenance,
        get_hcps,
        get_reps,
        is_loaded,
        load_all,
        save_scenario,
    )
    from ..models import NewHire, ObjectiveWeights, Override
    from ..solver import solve

    try:
        args = AlignArgs.model_validate(arguments).model_dump(mode="json")
    except ValueError as exc:
        return [{"type": "text", "text": json.dumps({"error": str(exc)})}]

    if not is_loaded():
        try:
            load_all()
        except ValueError as exc:
            return [{"type": "text", "text": json.dumps({"error": str(exc)})}]

    scenario_name = args["scenario_name"]

    defaults = ObjectiveWeights()
    w = ObjectiveWeights(
        workload_balance=args["weight_workload"] if args["weight_workload"] is not None else defaults.workload_balance,
        travel_efficiency=args["weight_travel"] if args["weight_travel"] is not None else defaults.travel_efficiency,
        disruption=args["weight_disruption"] if args["weight_disruption"] is not None else defaults.disruption,
        coverage=args["weight_coverage"] if args["weight_coverage"] is not None else defaults.coverage,
    )

    vacancies = args["vacancies"]
    new_hires_raw = args["new_hires"]
    new_hires = (
        [
            NewHire(
                rep_id=nh["rep_id"],
                name=nh["name"],
                base_lat=nh["base_lat"],
                base_lng=nh["base_lng"],
                product_expertise=nh.get("product_expertise", []),
                max_weekly_hours=nh.get("max_weekly_hours", 40.0),
                max_daily_calls=nh.get("max_daily_calls", 8),
                available_days=nh.get("available_days", ["mon", "tue", "wed", "thu", "fri"]),
            )
            for nh in new_hires_raw
        ]
        if new_hires_raw
        else []
    )

    overrides_raw = args["overrides"]
    overrides = (
        [Override(hcp_id=o["hcp_id"], rep_id=o["rep_id"], reason=o.get("reason", "")) for o in overrides_raw]
        if overrides_raw
        else []
    )

    lock_reps = args["lock_reps"]

    hcps = get_hcps()
    base_reps = get_reps()
    unknown_vacancies = sorted(set(vacancies) - {rep.rep_id for rep in base_reps})
    if unknown_vacancies:
        return [
            {
                "type": "text",
                "text": json.dumps({"error": f"unknown vacancy reps: {', '.join(unknown_vacancies)}"}),
            }
        ]
    reps = get_reps(vacancies=vacancies, new_hires=new_hires)
    alignment = get_current_alignment()
    constraints = get_constraints()

    try:
        result = solve(
            hcps=hcps,
            reps=reps,
            current_alignment=alignment,
            constraints=constraints,
            weights=w,
            overrides=overrides,
            lock_reps=lock_reps,
            scenario_name=scenario_name,
            max_iterations=args["max_iterations"],
            vacancies=vacancies,
            new_hires=new_hires,
            data_provenance=get_data_provenance(),
        )
    except ValueError as exc:
        return [{"type": "text", "text": json.dumps({"error": str(exc)})}]

    result_dict = json.loads(result.model_dump_json())
    try:
        save_scenario(scenario_name, result_dict)
    except FileExistsError:
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {"error": f"Scenario '{scenario_name}' already exists; choose a new scenario_name."}
                ),
            }
        ]
    except (OSError, ValueError) as exc:
        return [
            {
                "type": "text",
                "text": json.dumps({"error": f"Failed to save scenario artifacts: {exc}"}),
            }
        ]

    return [{"type": "text", "text": json.dumps(result_dict, indent=2)}]
