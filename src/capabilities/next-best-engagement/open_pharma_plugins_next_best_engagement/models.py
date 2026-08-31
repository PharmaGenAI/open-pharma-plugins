"""Pydantic models for next-best-engagement universe, constraints, and plans."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

_PLANNER_OWNED_OUTPUT_FIELDS = frozenset(
    {
        "action_type",
        "priority",
        "score",
        "suggested_window_start",
        "suggested_window_end",
        "rationale",
    }
)
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def is_formula_unsafe_text(value: str) -> bool:
    return value.startswith(_FORMULA_PREFIXES)


def _reject_formula_unsafe_extra_names(extras: dict[str, Any] | None) -> None:
    unsafe_names = sorted(name for name in (extras or {}) if is_formula_unsafe_text(name))
    if unsafe_names:
        raise ValueError(
            "unsafe extra column name starts with spreadsheet formula marker: "
            + ", ".join(repr(name) for name in unsafe_names)
        )


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


class ScoringWeights(BaseModel):
    """Weights for each scoring factor."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    recency_gap: float = Field(default=0.30, ge=0, description="Weight for recency gap factor")
    tier_value: float = Field(default=0.30, ge=0, description="Weight for tier value factor")
    engagement_velocity: float = Field(default=0.15, ge=0, description="Weight for engagement velocity factor")
    channel_diversity: float = Field(default=0.15, ge=0, description="Weight for channel diversity factor")
    coverage_debt: float = Field(default=0.10, ge=0, description="Weight for coverage debt factor")

    @model_validator(mode="after")
    def normalize(self) -> ScoringWeights:
        fields = tuple(type(self).model_fields)
        scale = max(getattr(self, name) for name in fields)
        if scale <= 0:
            raise ValueError("Scoring weights must have a positive total")
        scaled_total = sum(getattr(self, name) / scale for name in fields)
        for name in fields:
            setattr(self, name, (getattr(self, name) / scale) / scaled_total)
        return self


class PlanningConstraints(BaseModel):
    """Shared planning fields exposed by the optimizer and MCP arguments."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    period_days: int = Field(default=30, ge=1, le=365, description="Planning period in days")
    min_gap_days: int = Field(default=7, ge=0, description="Minimum days between touches to the same HCP")
    tier_a_coverage_pct: float = Field(default=0.95, ge=0, le=1, description="Target coverage for Tier A")
    tier_b_coverage_pct: float = Field(default=0.80, ge=0, le=1, description="Target coverage for Tier B")
    tier_c_coverage_pct: float = Field(default=0.50, ge=0, le=1, description="Target coverage for Tier C")
    tier_d_coverage_pct: float = Field(default=0.20, ge=0, le=1, description="Target coverage for Tier D")
    no_action_threshold: float = Field(
        default=0.15, ge=0, le=1, description="Score below which no_action is recommended"
    )


class ConstraintConfig(PlanningConstraints):
    """Constraint parameters for plan generation. All fields optional with sensible defaults."""

    weights: ScoringWeights = Field(default_factory=ScoringWeights)


class UniverseRow(BaseModel):
    """One row in the HCP engagement universe. Extra columns are preserved as pass-through."""

    model_config = ConfigDict(extra="allow")

    hcp_id: str = Field(min_length=1, description="Unique HCP identifier")
    hcp_name: str = Field(min_length=1, description="HCP full name")
    territory_id: str = Field(min_length=1, description="Territory assignment")
    rep_id: str = Field(min_length=1, description="Assigned rep identifier")

    tier: Literal["A", "B", "C", "D"] = Field(default="B", description="HCP tier from segmentation")
    specialty: str = Field(default="General", description="Medical specialty")
    rep_name: str = Field(default="", description="Rep full name")
    rep_max_visits_per_week: int = Field(default=20, ge=0, description="Rep weekly visit capacity")
    consent_email: StrictBool | None = Field(default=None, description="HCP explicitly consented to email")
    consent_phone: StrictBool | None = Field(default=None, description="HCP explicitly consented to phone/meeting")
    last_visit_date: date | None = Field(default=None, description="Date of last in-person visit")
    last_email_date: date | None = Field(default=None, description="Date of last email")
    last_meeting_date: date | None = Field(default=None, description="Date of last remote meeting")
    visits_last_90d: int = Field(default=0, ge=0, description="Visit count in last 90 days")
    emails_last_90d: int = Field(default=0, ge=0, description="Email count in last 90 days")

    @field_validator("hcp_id", "hcp_name", "territory_id", "rep_id")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def reject_future_touch_history(self) -> UniverseRow:
        today = date.today()
        for field_name in ("last_visit_date", "last_email_date", "last_meeting_date"):
            touch_date = getattr(self, field_name)
            if touch_date is not None and touch_date > today:
                raise ValueError(f"{field_name} is future-dated")
        return self

    @model_validator(mode="after")
    def reject_unsafe_extra_fields(self) -> UniverseRow:
        extras = self.model_extra or {}
        collisions = sorted(_PLANNER_OWNED_OUTPUT_FIELDS.intersection(extras))
        if collisions:
            raise ValueError("extra input field conflicts with planner-owned output field: " + ", ".join(collisions))
        _reject_formula_unsafe_extra_names(extras)
        return self


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class PlannedEngagement(BaseModel):
    """A single recommended engagement action."""

    model_config = ConfigDict(extra="allow")

    hcp_id: str
    hcp_name: str
    specialty: str
    tier: str
    territory_id: str
    rep_id: str
    rep_name: str
    action_type: Literal[
        "in_person_visit",
        "remote_meeting",
        "approved_email",
        "no_action",
    ]
    priority: int = Field(ge=1, le=5, description="1 = highest priority")
    score: float = Field(ge=0.0, le=1.0)
    suggested_window_start: date
    suggested_window_end: date
    rationale: str

    @model_validator(mode="after")
    def reject_unsafe_extra_fields(self) -> PlannedEngagement:
        _reject_formula_unsafe_extra_names(self.model_extra)
        return self


class UnassignedHCP(BaseModel):
    """An eligible HCP that could not be assigned."""

    hcp_id: str
    hcp_name: str
    tier: str
    reason: str


class TierCoverage(BaseModel):
    target_pct: float
    actual_pct: float
    total: int
    planned: int
    gap: int


class RepUtilization(BaseModel):
    rep_id: str
    rep_name: str
    capacity: int
    assigned: int
    utilization_pct: float


class PlanMetrics(BaseModel):
    total_universe: int
    total_eligible: int
    total_planned: int
    coverage_pct: float
    coverage_by_tier: dict[str, TierCoverage]
    rep_utilization: list[RepUtilization]
    channel_mix: dict[str, int]
    no_action_count: int
    no_action_reasons: dict[str, int]


class EngagementPlan(BaseModel):
    universe_fingerprint: str
    universe_generation: int = Field(ge=0)
    period_start: date
    period_end: date
    generated_at: str
    engagements: list[PlannedEngagement]
    unassigned: list[UnassignedHCP]
    metrics: PlanMetrics
