"""Pydantic domain models for territory alignment."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


class HCP(BaseModel):
    """An HCP loaded from hcps.csv. Extra columns are preserved."""

    model_config = ConfigDict(extra="allow")

    hcp_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=500)
    specialty: str = ""
    segment: Literal["high", "medium", "low"] = Field(default="medium", description="high | medium | low")
    tier: int = Field(default=2, ge=1, le=3)
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lng: float | None = Field(default=None, ge=-180.0, le=180.0)
    account_id: str = ""
    consent_email: bool = True
    consent_phone: bool = True
    consent_visit: bool = True
    annual_potential: float = Field(default=0.0, ge=0.0)
    product_requirements: list[str] = Field(default_factory=list)


class Rep(BaseModel):
    rep_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=500)
    base_lat: float = Field(default=0.0, ge=-90.0, le=90.0)
    base_lng: float = Field(default=0.0, ge=-180.0, le=180.0)
    product_expertise: list[str] = Field(default_factory=list)
    max_weekly_hours: float = Field(default=40.0, gt=0.0, le=168.0)
    max_daily_calls: int = Field(default=8, ge=1, le=100)
    available_days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] = Field(
        default_factory=lambda: ["mon", "tue", "wed", "thu", "fri"], min_length=1
    )


class CurrentAssignment(BaseModel):
    hcp_id: str = Field(min_length=1, max_length=200)
    primary_rep: str = Field(min_length=1, max_length=200)
    secondary_rep: str = ""


class Constraint(BaseModel):
    type: Literal["product_match", "account_grouping", "max_hcps_per_rep", "frequency_cap"]
    scope: str = Field(min_length=1, max_length=300)
    value: Any
    description: str = ""

    @model_validator(mode="after")
    def validate_contract(self) -> Constraint:
        if self.type == "product_match":
            if self.scope != "global" or self.value != "required":
                raise ValueError("product_match must use scope='global' and value='required'")
        elif self.type == "account_grouping":
            if not self.scope.startswith("account:") or not self.scope.removeprefix("account:"):
                raise ValueError("account_grouping scope must be account:<id>")
            if self.value != "same_primary_rep":
                raise ValueError("account_grouping value must be 'same_primary_rep'")
        elif self.type == "max_hcps_per_rep":
            if self.scope != "global":
                raise ValueError("max_hcps_per_rep scope must be 'global'")
            value = int(self.value)
            if value <= 0:
                raise ValueError("max_hcps_per_rep must be greater than zero")
            object.__setattr__(self, "value", value)
        elif self.type == "frequency_cap":
            if self.scope not in {"segment:high", "segment:medium", "segment:low"}:
                raise ValueError("frequency_cap scope must identify a supported segment")
            value = float(self.value)
            if value <= 0:
                raise ValueError("frequency_cap must be greater than zero")
            object.__setattr__(self, "value", value)
        return self


# ---------------------------------------------------------------------------
# Objective configuration
# ---------------------------------------------------------------------------


class ObjectiveWeights(BaseModel):
    workload_balance: float = Field(default=0.30, ge=0.0, le=1.0)
    travel_efficiency: float = Field(default=0.25, ge=0.0, le=1.0)
    disruption: float = Field(default=0.25, ge=0.0, le=1.0)
    coverage: float = Field(default=0.20, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def normalize(self) -> ObjectiveWeights:
        total = self.workload_balance + self.travel_efficiency + self.disruption + self.coverage
        if total <= 0:
            raise ValueError("at least one objective weight must be greater than zero")
        if abs(total - 1.0) > 1e-12:
            object.__setattr__(self, "workload_balance", self.workload_balance / total)
            object.__setattr__(self, "travel_efficiency", self.travel_efficiency / total)
            object.__setattr__(self, "disruption", self.disruption / total)
            object.__setattr__(self, "coverage", self.coverage / total)
        return self


# ---------------------------------------------------------------------------
# Scenario levers (ta_align input)
# ---------------------------------------------------------------------------


class Override(BaseModel):
    hcp_id: str = Field(min_length=1, max_length=200, description="HCP to pin to a specific rep")
    rep_id: str = Field(min_length=1, max_length=200, description="Rep to assign")
    reason: str = ""


class NewHire(BaseModel):
    rep_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=500)
    base_lat: float = Field(ge=-90.0, le=90.0)
    base_lng: float = Field(ge=-180.0, le=180.0)
    product_expertise: list[str] = Field(default_factory=list)
    max_weekly_hours: float = Field(default=40.0, gt=0.0, le=168.0)
    max_daily_calls: int = Field(default=8, ge=1, le=100)
    available_days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] = Field(
        default_factory=lambda: ["mon", "tue", "wed", "thu", "fri"], min_length=1
    )


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class AssignmentResult(BaseModel):
    """A single HCP assignment in the output plan."""

    model_config = ConfigDict(extra="allow")

    hcp_id: str
    hcp_name: str
    primary_rep: str
    previous_rep: str
    is_changed: bool
    change_reason: str = ""
    estimated_travel_min: float = 0.0
    estimated_annual_visits: int = 0
    segment: str = ""
    tier: int = 0


class TerritorySummary(BaseModel):
    rep_id: str
    rep_name: str
    hcp_count: int
    total_potential: float
    workload_hours_weekly: float
    travel_hours_weekly: float
    segment_high: int = 0
    segment_medium: int = 0
    segment_low: int = 0
    relationships_kept: int = 0
    relationships_new: int = 0


class RawMetrics(BaseModel):
    workload_gini: float
    avg_travel_min: float
    max_travel_min: float
    pct_reassigned: float
    pct_priority_covered: float


class ScenarioLevers(BaseModel):
    vacancies: list[str] = Field(default_factory=list)
    new_hires: list[NewHire] = Field(default_factory=list)
    overrides: list[Override] = Field(default_factory=list)
    lock_reps: list[str] = Field(default_factory=list)


class ScenarioInputSnapshot(BaseModel):
    hcps: list[HCP]
    reps: list[Rep]
    current_alignment: list[CurrentAssignment]
    constraints: list[Constraint]
    levers: ScenarioLevers = Field(default_factory=ScenarioLevers)


class ObjectiveScores(BaseModel):
    workload_balance: float
    travel_efficiency: float
    disruption: float
    coverage: float
    composite: float
    raw: RawMetrics


class UnassignedHCP(BaseModel):
    hcp_id: str
    reason: str


class ScenarioResult(BaseModel):
    scenario_name: str
    assignments: list[AssignmentResult]
    territory_summary: list[TerritorySummary]
    objectives: ObjectiveScores
    unassigned: list[UnassignedHCP]
    weights_used: ObjectiveWeights
    input_snapshot: ScenarioInputSnapshot
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Operational mode output
# ---------------------------------------------------------------------------


class VisitCluster(BaseModel):
    cluster_id: str
    hcp_ids: list[str]
    centroid_lat: float | None = None
    centroid_lng: float | None = None
    estimated_route_km: float = 0.0
    estimated_travel_min: float = 0.0
    hcp_count: int = 0
    suggested_day: str = ""
    suggested_date: str = ""
    travel_warning: str = ""


class VisitStop(BaseModel):
    hcp_id: str
    hcp_name: str
    visit_order: int
    cluster_id: str
    lat: float | None = None
    lng: float | None = None
    travel_km_from_previous: float = 0.0
    appointment_date: str = ""
    appointment_time: str = ""


class RemoteAlternative(BaseModel):
    hcp_id: str
    hcp_name: str
    distance_km: float
    reason: str


class VisitPlan(BaseModel):
    scenario_name: str
    rep_id: str
    rep_name: str
    period: str
    planning_dates: list[str]
    clusters: list[VisitCluster]
    visit_sequence: list[VisitStop]
    remote_alternatives: list[RemoteAlternative]
    total_route_km: float
    total_hcps: int
    remote_count: int
    excluded_no_visit_consent: list[str] = Field(default_factory=list)
    unplanned_hcp_ids: list[str] = Field(default_factory=list)
