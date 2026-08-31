"""recommend_engagements — score, filter, and optimise the engagement plan."""

from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationError

from ..models import PlanningConstraints


class RecommendEngagementsArgs(PlanningConstraints):
    weight_recency_gap: float | None = Field(
        default=None, ge=0, description="Override recency gap scoring weight (default 0.30)"
    )
    weight_tier_value: float | None = Field(
        default=None, ge=0, description="Override tier value scoring weight (default 0.30)"
    )
    weight_engagement_velocity: float | None = Field(
        default=None, ge=0, description="Override engagement velocity scoring weight (default 0.15)"
    )
    weight_channel_diversity: float | None = Field(
        default=None, ge=0, description="Override channel diversity scoring weight (default 0.15)"
    )
    weight_coverage_debt: float | None = Field(
        default=None, ge=0, description="Override coverage debt scoring weight (default 0.10)"
    )


TOOL: dict[str, Any] = {
    "name": "recommend_engagements",
    "description": (
        "Generate the next-best-engagement plan for the loaded HCP universe. "
        "Scores each HCP using a weighted linear model (recency gap, tier "
        "value, engagement velocity, channel diversity, coverage debt), "
        "selects actions via a rule cascade, and allocates to reps using a "
        "two-phase greedy optimiser that satisfies coverage targets first, "
        "then fills remaining capacity by score. Returns a structured "
        "EngagementPlan with planned engagements, unassigned HCPs, and "
        "metrics. Call load_universe first."
    ),
    "args": RecommendEngagementsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from .._optimizer import generate_plan
    from .._universe import get_session_snapshot, store_plan
    from ..models import ConstraintConfig, ScoringWeights

    try:
        validated = RecommendEngagementsArgs.model_validate(arguments).model_dump(exclude_none=True)
        weight_overrides = {k.removeprefix("weight_"): v for k, v in validated.items() if k.startswith("weight_")}
        constraint_fields = {k: v for k, v in validated.items() if not k.startswith("weight_")}
        if weight_overrides:
            constraint_fields["weights"] = ScoringWeights(**{**ScoringWeights().model_dump(), **weight_overrides})
        config = ConstraintConfig(**constraint_fields)
    except ValidationError as exc:
        return [
            {
                "type": "text",
                "text": json.dumps({"error": f"Invalid recommendation arguments: {exc}"}),
            }
        ]

    universe, rep_info, load_generation = get_session_snapshot()
    if not universe:
        return [{"type": "text", "text": json.dumps({"error": "No universe loaded. Call load_universe first."})}]

    plan = generate_plan(universe, rep_info, config, universe_generation=load_generation)
    if not store_plan(plan, load_generation):
        return [
            {
                "type": "text",
                "text": json.dumps({"error": "Universe changed while the recommendation plan was generated. Retry."}),
            }
        ]

    result = json.loads(plan.model_dump_json())
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
