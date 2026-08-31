"""Tests for the next-best-engagement capability."""

import json
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

import open_pharma_plugins_next_best_engagement as nbe
from open_pharma_plugins_next_best_engagement._optimizer import generate_plan
from open_pharma_plugins_next_best_engagement._scoring import score_hcp
from open_pharma_plugins_next_best_engagement._universe import (
    get_rep_info,
    get_session_snapshot,
    get_summary,
    get_universe,
    load_fixture,
    store_plan,
)
from open_pharma_plugins_next_best_engagement.models import (
    ConstraintConfig,
    EngagementPlan,
    ScoringWeights,
    UniverseRow,
)

# ---------------------------------------------------------------------------
# Package-level sanity
# ---------------------------------------------------------------------------


def test_lists_three_tools():
    names = {t["name"] for t in nbe.list_tools()}
    assert names == {"load_universe", "recommend_engagements", "render_plan"}


def test_version_is_set():
    assert nbe.__version__ == "1.0.2"


# ---------------------------------------------------------------------------
# Universe loading
# ---------------------------------------------------------------------------


def test_load_fixture_returns_80_rows():
    rows = load_fixture()
    assert len(rows) == 80


def test_fixture_summary_has_expected_shape():
    load_fixture()
    s = get_summary()
    assert s["loaded"] is True
    assert s["hcp_count"] == 80
    assert s["rep_count"] == 8
    assert s["territory_count"] == 4
    assert sorted(s["tier_distribution"].keys()) == ["A", "B", "C", "D"]
    assert "kol_flag" in s["extra_columns"]
    assert "affiliation" in s["extra_columns"]


def test_fixture_rep_info():
    load_fixture()
    reps = get_rep_info()
    assert len(reps) == 8
    for info in reps.values():
        assert info["capacity"] == 20
        assert len(info["territories"]) >= 1


def test_extra_columns_preserved():
    load_fixture()
    universe = get_universe()
    for row in universe:
        assert "kol_flag" in row.model_extra
        assert "affiliation" in row.model_extra


@pytest.mark.parametrize("field", ["hcp_id", "hcp_name", "territory_id", "rep_id"])
def test_required_identity_fields_reject_blank_values(field):
    values = {"hcp_id": "H1", "hcp_name": "Dr A", "territory_id": "T1", "rep_id": "R1"}
    values[field] = "   "

    with pytest.raises(ValidationError):
        UniverseRow(**values)


@pytest.mark.parametrize("field", ["rep_max_visits_per_week", "visits_last_90d", "emails_last_90d"])
def test_universe_counts_reject_negative_values(field):
    values = {"hcp_id": "H1", "hcp_name": "Dr A", "territory_id": "T1", "rep_id": "R1", field: -1}

    with pytest.raises(ValidationError):
        UniverseRow(**values)


@pytest.mark.parametrize("field", ["consent_email", "consent_phone"])
def test_consent_requires_an_explicit_boolean(field):
    values = {"hcp_id": "H1", "hcp_name": "Dr A", "territory_id": "T1", "rep_id": "R1", field: 1}

    with pytest.raises(ValidationError):
        UniverseRow(**values)


def test_load_rejects_duplicate_hcp_ids_without_replacing_session(tmp_path):
    nbe.get_handler("load_universe")({"source": "fixture"})
    nbe.get_handler("recommend_engagements")({})
    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text("hcp_id,hcp_name,territory_id,rep_id\nDUP,Dr One,T1,R1\nDUP,Dr Two,T1,R1\n")

    result = nbe.get_handler("load_universe")({"source": str(duplicate)})

    assert "Duplicate hcp_id: DUP" in json.loads(result[0]["text"])["error"]
    assert get_summary()["hcp_count"] == 80
    assert "engagements" in json.loads(nbe.get_handler("render_plan")({"format": "json"})[0]["text"])


def test_load_rejects_future_dated_touch_history(tmp_path):
    future = date.today() + timedelta(days=1)
    source = tmp_path / "future.csv"
    source.write_text(f"hcp_id,hcp_name,territory_id,rep_id,last_email_date\nH1,Dr Future,T1,R1,{future.isoformat()}\n")

    result = nbe.get_handler("load_universe")({"source": str(source)})

    assert "future-dated" in json.loads(result[0]["text"])["error"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action_type", "approved_email"),
        ("priority", "5"),
        ("score", "0.1"),
        ("suggested_window_start", "2026-08-29"),
        ("suggested_window_end", "2026-09-28"),
        ("rationale", "untrusted override"),
    ],
)
def test_load_universe_tool_rejects_planner_owned_extra_columns(tmp_path, field, value):
    source = tmp_path / "reserved-extra.csv"
    source.write_text(f"hcp_id,hcp_name,territory_id,rep_id,{field}\nH1,Dr Reserved,T1,R1,{value}\n")

    result = nbe.get_handler("load_universe")({"source": str(source)})

    error = json.loads(result[0]["text"])["error"]
    assert field in error
    assert "planner-owned output field" in error


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r", "\n"])
def test_load_universe_tool_rejects_formula_unsafe_extra_column_names(tmp_path, prefix):
    import csv

    source = tmp_path / "unsafe-header.csv"
    with source.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["hcp_id", "hcp_name", "territory_id", "rep_id", f"{prefix}unsafe"])
        writer.writerow(["H1", "Dr Unsafe", "T1", "R1", "payload"])

    result = nbe.get_handler("load_universe")({"source": str(source)})

    error = json.loads(result[0]["text"])["error"]
    assert "unsafe extra column name" in error
    assert "spreadsheet formula marker" in error


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_tier_a_scores_higher_than_tier_d():
    today = date(2026, 8, 20)
    config = ConstraintConfig()
    coverage = {"A": 0.0, "B": 0.0, "C": 0.0, "D": 0.0}

    hcp_a = UniverseRow(hcp_id="1", hcp_name="Dr A", territory_id="T1", rep_id="R1", tier="A")
    hcp_d = UniverseRow(hcp_id="2", hcp_name="Dr D", territory_id="T1", rep_id="R1", tier="D")

    score_a, _ = score_hcp(hcp_a, config, coverage, today)
    score_d, _ = score_hcp(hcp_d, config, coverage, today)
    assert score_a > score_d


def test_overdue_hcp_scores_higher_than_recent():
    today = date(2026, 8, 20)
    config = ConstraintConfig()
    coverage = {"B": 0.0}

    overdue = UniverseRow(
        hcp_id="1",
        hcp_name="Overdue",
        territory_id="T1",
        rep_id="R1",
        last_visit_date=date(2026, 6, 1),
    )
    recent = UniverseRow(
        hcp_id="2",
        hcp_name="Recent",
        territory_id="T1",
        rep_id="R1",
        last_visit_date=date(2026, 8, 18),
    )

    score_overdue, _ = score_hcp(overdue, config, coverage, today)
    score_recent, _ = score_hcp(recent, config, coverage, today)
    assert score_overdue > score_recent


def test_never_contacted_gets_max_recency():
    today = date(2026, 8, 20)
    config = ConstraintConfig()
    coverage = {"B": 0.0}

    hcp = UniverseRow(hcp_id="1", hcp_name="Cold", territory_id="T1", rep_id="R1")
    score_val, rationale = score_hcp(hcp, config, coverage, today)
    assert score_val > 0.5
    assert "recency_gap=1.00" in rationale


def test_score_bounded_zero_to_one():
    today = date(2026, 8, 20)
    config = ConstraintConfig()
    coverage = {"A": 0.0, "D": 1.0}

    for tier in ["A", "D"]:
        hcp = UniverseRow(hcp_id="1", hcp_name="X", territory_id="T1", rep_id="R1", tier=tier)
        s, _ = score_hcp(hcp, config, coverage, today)
        assert 0.0 <= s <= 1.0


def test_scoring_weights_are_normalized_before_scoring():
    weights = ScoringWeights(
        recency_gap=3,
        tier_value=3,
        engagement_velocity=1.5,
        channel_diversity=1.5,
        coverage_debt=1,
    )

    assert weights.model_dump() == pytest.approx(
        {
            "recency_gap": 0.3,
            "tier_value": 0.3,
            "engagement_velocity": 0.15,
            "channel_diversity": 0.15,
            "coverage_debt": 0.1,
        }
    )


def test_scoring_weights_normalize_extreme_finite_values():
    weights = ScoringWeights(
        recency_gap=1e308,
        tier_value=1e308,
        engagement_velocity=1e308,
        channel_diversity=1e308,
        coverage_debt=1e308,
    )

    assert weights.model_dump() == pytest.approx(
        {
            "recency_gap": 0.2,
            "tier_value": 0.2,
            "engagement_velocity": 0.2,
            "channel_diversity": 0.2,
            "coverage_debt": 0.2,
        }
    )
    assert sum(weights.model_dump().values()) == pytest.approx(1.0)


def test_scoring_weights_preserve_mixed_positive_extreme_values():
    weights = ScoringWeights(
        recency_gap=1e308,
        tier_value=5e307,
        engagement_velocity=1e200,
        channel_diversity=1e100,
        coverage_debt=1,
    )

    normalized = weights.model_dump()
    assert normalized["recency_gap"] == pytest.approx(2 / 3)
    assert normalized["tier_value"] == pytest.approx(1 / 3)
    assert all(value > 0 for value in normalized.values())
    assert sum(normalized.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "values",
    [
        {"recency_gap": -0.1},
        {"tier_value": float("inf")},
        {"coverage_debt": float("nan")},
        {
            "recency_gap": 0,
            "tier_value": 0,
            "engagement_velocity": 0,
            "channel_diversity": 0,
            "coverage_debt": 0,
        },
    ],
)
def test_scoring_weights_reject_invalid_values(values):
    with pytest.raises(ValidationError):
        ScoringWeights(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("period_days", 0),
        ("period_days", 366),
        ("min_gap_days", -1),
        ("tier_a_coverage_pct", -0.01),
        ("tier_b_coverage_pct", 1.01),
        ("no_action_threshold", -0.01),
        ("no_action_threshold", 1.01),
    ],
)
def test_constraint_config_rejects_invalid_planning_values(field, value):
    with pytest.raises(ValidationError):
        ConstraintConfig(**{field: value})


def test_constraint_config_accepts_one_year_period():
    assert ConstraintConfig(period_days=365).period_days == 365


def test_coverage_debt_reflects_actual_coverage():
    today = date(2026, 8, 20)
    config = ConstraintConfig()

    hcp = UniverseRow(hcp_id="1", hcp_name="X", territory_id="T1", rep_id="R1", tier="A")

    _, rationale_zero = score_hcp(hcp, config, {"A": 0.0}, today)
    _, rationale_full = score_hcp(hcp, config, {"A": 1.0}, today)

    score_zero, _ = score_hcp(hcp, config, {"A": 0.0}, today)
    score_full, _ = score_hcp(hcp, config, {"A": 1.0}, today)
    assert score_zero > score_full


# ---------------------------------------------------------------------------
# Optimizer — min_gap_days (#1)
# ---------------------------------------------------------------------------


def test_min_gap_days_enforced():
    """An HCP contacted yesterday should not be assigned when min_gap_days=7."""
    today = date(2026, 8, 20)
    universe = [
        UniverseRow(
            hcp_id="1",
            hcp_name="Recent",
            territory_id="T1",
            rep_id="R1",
            tier="A",
            last_visit_date=today - timedelta(days=2),
            consent_email=True,
            consent_phone=True,
        ),
    ]
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}
    config = ConstraintConfig(min_gap_days=7)

    plan = generate_plan(universe, rep_info, config, today)
    assert len(plan.engagements) == 0
    assert len(plan.unassigned) == 1
    assert plan.unassigned[0].reason == "too_recent"


def test_min_gap_days_allows_outside_window():
    """An HCP contacted 10 days ago should be assigned when min_gap_days=7."""
    today = date(2026, 8, 20)
    universe = [
        UniverseRow(
            hcp_id="1",
            hcp_name="Ready",
            territory_id="T1",
            rep_id="R1",
            tier="A",
            last_visit_date=today - timedelta(days=10),
            consent_email=True,
            consent_phone=True,
        ),
    ]
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}
    config = ConstraintConfig(min_gap_days=7)

    plan = generate_plan(universe, rep_info, config, today)
    assert len(plan.engagements) == 1


def test_generate_plan_rejects_duplicate_ids_and_touches_after_planning_date():
    today = date(2026, 8, 20)
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}
    duplicate = UniverseRow(
        hcp_id="DUP",
        hcp_name="Duplicate",
        territory_id="T1",
        rep_id="R1",
        consent_email=True,
    )
    future_touch = UniverseRow(
        hcp_id="FUTURE",
        hcp_name="Future",
        territory_id="T1",
        rep_id="R1",
        consent_email=True,
        last_email_date=today + timedelta(days=1),
    )

    with pytest.raises(ValueError, match="Duplicate hcp_id: DUP"):
        generate_plan([duplicate, duplicate], rep_info, ConstraintConfig(), today)
    with pytest.raises(ValueError, match="future-dated"):
        generate_plan([future_touch], rep_info, ConstraintConfig(), today)


# ---------------------------------------------------------------------------
# Optimizer — coverage debt re-scoring (#2)
# ---------------------------------------------------------------------------


def test_coverage_debt_updates_between_phases():
    """After Phase 1 fills Tier A, Phase 2 should not over-prioritise Tier A."""
    today = date(2026, 8, 20)
    universe = []
    # 4 Tier A HCPs — Phase 1 targets 95% → 4 assigned
    for i in range(4):
        universe.append(
            UniverseRow(
                hcp_id=f"A{i}",
                hcp_name=f"Dr A{i}",
                territory_id="T1",
                rep_id="R1",
                tier="A",
                consent_phone=True,
            )
        )
    # 4 Tier C HCPs — Phase 1 targets 50% → 2 assigned, 2 left for Phase 2
    for i in range(4):
        universe.append(
            UniverseRow(
                hcp_id=f"C{i}",
                hcp_name=f"Dr C{i}",
                territory_id="T1",
                rep_id="R1",
                tier="C",
                consent_phone=True,
            )
        )

    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}
    config = ConstraintConfig(tier_a_coverage_pct=0.95, tier_c_coverage_pct=0.50)

    plan = generate_plan(universe, rep_info, config, today)

    # All 8 should be planned (plenty of capacity)
    assert plan.metrics.total_planned == 8
    # Tier A fully covered
    assert plan.metrics.coverage_by_tier["A"].planned == 4
    # Tier C fully covered (2 from Phase 1 + 2 from Phase 2)
    assert plan.metrics.coverage_by_tier["C"].planned == 4


# ---------------------------------------------------------------------------
# Optimizer — action type correctness (#4)
# ---------------------------------------------------------------------------


def test_only_valid_action_types_produced():
    """The optimizer should only produce action types in the model's Literal."""
    load_fixture()
    universe = get_universe()
    rep_info = get_rep_info()
    config = ConstraintConfig()
    today = date(2026, 8, 20)

    plan = generate_plan(universe, rep_info, config, today)

    valid_types = {"in_person_visit", "remote_meeting", "approved_email", "no_action"}
    for e in plan.engagements:
        assert e.action_type in valid_types, f"Unexpected action: {e.action_type}"


# ---------------------------------------------------------------------------
# Optimizer — general correctness
# ---------------------------------------------------------------------------


def test_no_consent_hcp_gets_unassigned():
    today = date(2026, 8, 20)
    universe = [
        UniverseRow(
            hcp_id="1",
            hcp_name="Opted Out",
            territory_id="T1",
            rep_id="R1",
            tier="A",
            consent_email=False,
            consent_phone=False,
        ),
    ]
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}
    config = ConstraintConfig()

    plan = generate_plan(universe, rep_info, config, today)
    assert len(plan.engagements) == 0
    assert len(plan.unassigned) == 1
    assert plan.unassigned[0].reason == "no_consent"


def test_omitted_consent_is_missing_and_does_not_select_an_action():
    today = date(2026, 8, 20)
    hcp = UniverseRow(hcp_id="1", hcp_name="Unknown", territory_id="T1", rep_id="R1", tier="A")
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}

    plan = generate_plan([hcp], rep_info, ConstraintConfig(), today)

    assert plan.engagements == []
    assert plan.unassigned[0].reason == "missing_consent"


def test_zero_capacity_unassigns_all():
    today = date(2026, 8, 20)
    universe = [
        UniverseRow(
            hcp_id="1",
            hcp_name="Dr X",
            territory_id="T1",
            rep_id="R1",
            tier="A",
            consent_phone=True,
        ),
    ]
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 0}}
    config = ConstraintConfig()

    plan = generate_plan(universe, rep_info, config, today)
    assert len(plan.engagements) == 0
    assert plan.unassigned[0].reason == "no_rep_capacity"


def test_single_hcp_produces_valid_plan():
    today = date(2026, 8, 20)
    universe = [
        UniverseRow(
            hcp_id="1",
            hcp_name="Solo",
            territory_id="T1",
            rep_id="R1",
            tier="B",
            consent_phone=True,
        ),
    ]
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}
    config = ConstraintConfig()

    plan = generate_plan(universe, rep_info, config, today)
    assert len(plan.engagements) == 1
    e = plan.engagements[0]
    assert e.hcp_id == "1"
    assert e.rep_id == "R1"
    assert e.priority >= 1
    assert e.priority <= 5


def test_tier_a_gets_higher_priority_than_tier_d():
    today = date(2026, 8, 20)
    universe = [
        UniverseRow(
            hcp_id="A1",
            hcp_name="Dr A",
            territory_id="T1",
            rep_id="R1",
            tier="A",
            consent_phone=True,
        ),
        UniverseRow(
            hcp_id="D1",
            hcp_name="Dr D",
            territory_id="T1",
            rep_id="R1",
            tier="D",
            consent_phone=True,
        ),
    ]
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}
    config = ConstraintConfig()

    plan = generate_plan(universe, rep_info, config, today)
    a_eng = next(e for e in plan.engagements if e.hcp_id == "A1")
    d_eng = next(e for e in plan.engagements if e.hcp_id == "D1")
    assert a_eng.priority <= d_eng.priority


def test_extra_columns_pass_through_to_engagements():
    today = date(2026, 8, 20)
    universe = [
        UniverseRow.model_validate(
            {
                "hcp_id": "1",
                "hcp_name": "Dr X",
                "territory_id": "T1",
                "rep_id": "R1",
                "consent_email": True,
                "kol_flag": True,
                "custom_field": "abc",
            }
        ),
    ]
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}
    config = ConstraintConfig()

    plan = generate_plan(universe, rep_info, config, today)
    e = plan.engagements[0]
    assert e.model_extra.get("kol_flag") is True
    assert e.model_extra.get("custom_field") == "abc"


def test_generate_plan_keeps_canonical_decisions_authoritative_over_unvalidated_extras():
    today = date(2026, 8, 20)
    hcp = UniverseRow.model_construct(
        hcp_id="H1",
        hcp_name="Dr Canonical",
        territory_id="T1",
        rep_id="R1",
        tier="A",
        consent_email=False,
        consent_phone=True,
        action_type="approved_email",
        priority=5,
        score=0.0,
        suggested_window_start=date(2025, 1, 1),
        suggested_window_end=date(2025, 1, 2),
        rationale="untrusted override",
    )
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}

    plan = generate_plan([hcp], rep_info, ConstraintConfig(period_days=30), today)

    engagement = plan.engagements[0]
    assert engagement.action_type == "in_person_visit"
    assert engagement.priority == 1
    assert engagement.score == 0.97
    assert engagement.suggested_window_start == today
    assert engagement.suggested_window_end == today + timedelta(days=30)
    assert engagement.rationale != "untrusted override"
    assert plan.metrics.channel_mix == {"in_person_visit": 1}
    assert plan.metrics.rep_utilization[0].assigned == 1


def test_rep_utilization_reported():
    load_fixture()
    universe = get_universe()
    rep_info = get_rep_info()
    config = ConstraintConfig()
    today = date(2026, 8, 20)

    plan = generate_plan(universe, rep_info, config, today)
    assert len(plan.metrics.rep_utilization) == 8
    for ru in plan.metrics.rep_utilization:
        assert ru.capacity > 0
        assert 0.0 <= ru.utilization_pct <= 1.0


def test_channel_mix_sums_to_total_planned():
    load_fixture()
    universe = get_universe()
    rep_info = get_rep_info()
    config = ConstraintConfig()
    today = date(2026, 8, 20)

    plan = generate_plan(universe, rep_info, config, today)
    assert sum(plan.metrics.channel_mix.values()) == plan.metrics.total_planned


def test_only_in_person_visits_consume_declared_rep_visit_capacity():
    today = date(2026, 8, 20)
    universe = [
        UniverseRow(
            hcp_id="VISIT",
            hcp_name="Visit",
            territory_id="T1",
            rep_id="R1",
            tier="A",
            consent_phone=True,
            consent_email=False,
        ),
        UniverseRow(
            hcp_id="EMAIL",
            hcp_name="Email",
            territory_id="T1",
            rep_id="R1",
            tier="B",
            last_visit_date=today - timedelta(days=10),
            consent_phone=False,
            consent_email=True,
        ),
        UniverseRow(
            hcp_id="REMOTE",
            hcp_name="Remote",
            territory_id="T1",
            rep_id="R1",
            tier="C",
            consent_phone=True,
            consent_email=False,
        ),
    ]
    rep_info = {"R1": {"name": "Rep One", "territories": {"T1"}, "capacity": 1}}

    plan = generate_plan(universe, rep_info, ConstraintConfig(period_days=7, min_gap_days=0), today)

    assert {(e.hcp_id, e.action_type, e.rep_id) for e in plan.engagements} == {
        ("VISIT", "in_person_visit", "R1"),
        ("EMAIL", "approved_email", "R1"),
        ("REMOTE", "remote_meeting", "R1"),
    }
    assert plan.metrics.rep_utilization[0].assigned == 1


def test_in_person_visit_is_not_reassigned_when_declared_rep_has_no_capacity():
    today = date(2026, 8, 20)
    hcp = UniverseRow(
        hcp_id="H1",
        hcp_name="Declared Rep Only",
        territory_id="T1",
        rep_id="R1",
        tier="A",
        consent_phone=True,
        consent_email=False,
    )
    rep_info = {
        "R1": {"name": "Declared", "territories": {"T1"}, "capacity": 0},
        "R2": {"name": "Other", "territories": {"T1"}, "capacity": 20},
    }

    plan = generate_plan([hcp], rep_info, ConstraintConfig(), today)

    assert plan.engagements == []
    assert plan.unassigned[0].reason == "no_rep_capacity"


def test_below_threshold_unassigned():
    """HCPs with scores below no_action_threshold go to unassigned."""
    today = date(2026, 8, 20)
    universe = [
        UniverseRow(
            hcp_id="1",
            hcp_name="Low Value",
            territory_id="T1",
            rep_id="R1",
            tier="D",
            last_visit_date=today - timedelta(days=1),
            visits_last_90d=5,
            emails_last_90d=5,
            consent_email=True,
            consent_phone=False,
        ),
    ]
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}
    config = ConstraintConfig(tier_d_coverage_pct=0, no_action_threshold=0.99, min_gap_days=0)

    plan = generate_plan(universe, rep_info, config, today)
    assert len(plan.engagements) == 0
    assert plan.unassigned[0].reason == "below_threshold"
    assert plan.metrics.total_universe == 1
    assert plan.metrics.total_eligible == 0
    assert plan.metrics.coverage_pct == 0
    assert plan.metrics.coverage_by_tier == {}


def test_below_threshold_is_enforced_during_coverage_pass():
    today = date(2026, 8, 20)
    hcp = UniverseRow(
        hcp_id="LOW-COVERAGE",
        hcp_name="Low Coverage Score",
        territory_id="T1",
        rep_id="R1",
        tier="A",
        consent_email=True,
        consent_phone=False,
        last_visit_date=today - timedelta(days=1),
        visits_last_90d=10,
        emails_last_90d=10,
    )
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}

    plan = generate_plan(
        [hcp],
        rep_info,
        ConstraintConfig(tier_a_coverage_pct=1.0, no_action_threshold=0.99, min_gap_days=0),
        today,
    )

    assert plan.engagements == []
    assert [(row.hcp_id, row.reason) for row in plan.unassigned] == [("LOW-COVERAGE", "below_threshold")]


def test_metrics_distinguish_universe_from_action_eligible_hcps():
    today = date(2026, 8, 20)
    universe = [
        UniverseRow(
            hcp_id="ELIGIBLE",
            hcp_name="Eligible",
            territory_id="T1",
            rep_id="R1",
            tier="A",
            consent_email=True,
            consent_phone=False,
        ),
        UniverseRow(
            hcp_id="OPTED-OUT",
            hcp_name="Opted Out",
            territory_id="T1",
            rep_id="R1",
            tier="A",
            consent_email=False,
            consent_phone=False,
        ),
    ]
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}

    plan = generate_plan(
        universe,
        rep_info,
        ConstraintConfig(tier_a_coverage_pct=1.0, no_action_threshold=0, min_gap_days=0),
        today,
    )

    assert plan.metrics.total_universe == 2
    assert plan.metrics.total_eligible == 1
    assert plan.metrics.total_planned == 1
    assert plan.metrics.coverage_pct == 1.0
    assert plan.metrics.coverage_by_tier["A"].total == 1
    assert plan.metrics.coverage_by_tier["A"].gap == 0


def test_phase_two_eligibility_reasons_take_precedence_over_score_threshold():
    today = date(2026, 8, 20)
    universe = [
        UniverseRow(hcp_id="MISSING", hcp_name="Missing", territory_id="T1", rep_id="R1", tier="D"),
        UniverseRow(
            hcp_id="NO",
            hcp_name="No Consent",
            territory_id="T1",
            rep_id="R1",
            tier="D",
            consent_email=False,
            consent_phone=False,
        ),
        UniverseRow(
            hcp_id="RECENT",
            hcp_name="Recent",
            territory_id="T1",
            rep_id="R1",
            tier="D",
            consent_email=True,
            consent_phone=False,
            last_email_date=today - timedelta(days=1),
        ),
    ]
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 20}}
    config = ConstraintConfig(tier_d_coverage_pct=0, no_action_threshold=0.99, min_gap_days=7)

    plan = generate_plan(universe, rep_info, config, today)

    assert {hcp.hcp_id: hcp.reason for hcp in plan.unassigned} == {
        "MISSING": "missing_consent",
        "NO": "no_consent",
        "RECENT": "too_recent",
    }


def test_phase_two_accepts_one_explicit_channel_when_the_other_is_unknown():
    today = date(2026, 8, 20)
    hcp = UniverseRow(
        hcp_id="EMAIL",
        hcp_name="Email Consent",
        territory_id="T1",
        rep_id="R1",
        tier="D",
        consent_email=True,
    )
    rep_info = {"R1": {"name": "Rep", "territories": {"T1"}, "capacity": 0}}
    config = ConstraintConfig(tier_d_coverage_pct=0, no_action_threshold=0)

    plan = generate_plan([hcp], rep_info, config, today)

    assert [(engagement.action_type, engagement.rep_id) for engagement in plan.engagements] == [
        ("approved_email", "R1")
    ]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def test_load_universe_tool_fixture():
    result = nbe.get_handler("load_universe")({"source": "fixture"})
    assert result[0]["type"] == "text"
    data = json.loads(result[0]["text"])
    assert data["loaded"] is True
    assert data["hcp_count"] == 80


def test_load_universe_tool_missing_file():
    result = nbe.get_handler("load_universe")({"source": "/nonexistent/path.csv"})
    data = json.loads(result[0]["text"])
    assert "error" in data


def test_recommend_without_load_returns_error(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("hcp_id,hcp_name,territory_id,rep_id\n")
    nbe.get_handler("load_universe")({"source": str(empty)})

    result = nbe.get_handler("recommend_engagements")({})
    data = json.loads(result[0]["text"])
    assert "error" in data


def test_recommend_validates_arguments_before_empty_universe(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("hcp_id,hcp_name,territory_id,rep_id\n")
    nbe.get_handler("load_universe")({"source": str(empty)})

    result = nbe.get_handler("recommend_engagements")({"period_days": 0})

    assert "Invalid recommendation arguments" in json.loads(result[0]["text"])["error"]


def test_recommend_after_fixture_returns_plan():
    nbe.get_handler("load_universe")({"source": "fixture"})
    result = nbe.get_handler("recommend_engagements")({})
    data = json.loads(result[0]["text"])
    assert "engagements" in data
    assert "metrics" in data
    assert data["metrics"]["total_universe"] == 80
    assert data["metrics"]["total_eligible"] == 76
    assert data["metrics"]["total_planned"] > 0


def test_render_plan_json():
    nbe.get_handler("load_universe")({"source": "fixture"})
    rec_result = nbe.get_handler("recommend_engagements")({})
    plan_json = rec_result[0]["text"]

    result = nbe.get_handler("render_plan")({"plan_json": plan_json, "format": "json"})
    data = json.loads(result[0]["text"])
    assert "engagements" in data


def test_render_plan_csv(tmp_path):
    import csv

    nbe.get_handler("load_universe")({"source": "fixture"})
    rec_result = nbe.get_handler("recommend_engagements")({})
    plan_json = rec_result[0]["text"]

    result = nbe.get_handler("render_plan")(
        {
            "plan_json": plan_json,
            "format": "csv",
            "output_dir": str(tmp_path),
        }
    )
    data = json.loads(result[0]["text"])
    assert data["status"] == "ok"
    # Filenames now include timestamps (#8)
    eng_path = data["files"]["engagements_csv"]
    assert "engagements_" in eng_path
    assert eng_path.endswith(".csv")
    from pathlib import Path

    lines = Path(eng_path).read_text().strip().split("\n")
    assert len(lines) > 1

    with Path(data["files"]["summary_csv"]).open(newline="") as handle:
        summary = {(row["section"], row["metric"]): row["value"] for row in csv.DictReader(handle)}
    assert summary[("overview", "total_universe")] == "80"
    assert summary[("overview", "total_eligible")] == "76"


def test_render_plan_csv_exports_a_stable_union_of_pass_through_columns(tmp_path):
    """Every engagement extra is exported, even when it is absent from the first row."""
    import csv
    from pathlib import Path

    nbe.get_handler("load_universe")({"source": "fixture"})
    plan = json.loads(nbe.get_handler("recommend_engagements")({})[0]["text"])
    plan["engagements"][0]["first_row_only"] = "one"
    plan["engagements"][1]["later_row_only"] = "two"

    result = nbe.get_handler("render_plan")(
        {"plan_json": json.dumps(plan), "format": "csv", "output_dir": str(tmp_path)}
    )
    path = Path(json.loads(result[0]["text"])["files"]["engagements_csv"])
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["first_row_only"] == "one"
    assert rows[0]["later_row_only"] == ""
    assert rows[1]["first_row_only"] == ""
    assert rows[1]["later_row_only"] == "two"
    assert list(rows[0]).index("first_row_only") < list(rows[0]).index("later_row_only")


def test_csv_export_neutralizes_spreadsheet_formulas():
    from open_pharma_plugins_next_best_engagement._renderer import _safe_csv_cell

    for value in ("=1+1", "+SUM(A1:A2)", "-2+3", "@cmd", "\t=1", "\r=1", "\n=1"):
        assert _safe_csv_cell(value) == "'" + value
    assert _safe_csv_cell("Dr Example") == "Dr Example"
    assert _safe_csv_cell(-3) == -3


def test_render_plan_rejects_formula_unsafe_extra_column_names(tmp_path):
    nbe.get_handler("load_universe")({"source": "fixture"})
    plan = json.loads(nbe.get_handler("recommend_engagements")({})[0]["text"])
    plan["engagements"][0]["=1+1"] = "unsafe header payload"

    result = nbe.get_handler("render_plan")(
        {"plan_json": json.dumps(plan), "format": "csv", "output_dir": str(tmp_path)}
    )

    error = json.loads(result[0]["text"])["error"]
    assert "unsafe extra column name" in error
    assert list(tmp_path.iterdir()) == []


def test_render_plan_default_uses_configured_private_output_dir(tmp_path, monkeypatch):
    from pathlib import Path

    output_dir = tmp_path / "nbe-data"
    monkeypatch.setenv("OPEN_PHARMA_NBE_OUTPUT_DIR", str(output_dir))
    nbe.get_handler("load_universe")({"source": "fixture"})
    plan_json = nbe.get_handler("recommend_engagements")({})[0]["text"]

    result = nbe.get_handler("render_plan")({"plan_json": plan_json, "format": "csv"})
    files = json.loads(result[0]["text"])["files"]

    assert all(Path(path).is_relative_to(output_dir) for path in files.values())
    assert all(Path(path).stat().st_mode & 0o777 == 0o600 for path in files.values())


def test_render_plan_csv_no_overwrite(tmp_path):
    """Two renders should produce distinct files (#8)."""
    nbe.get_handler("load_universe")({"source": "fixture"})
    rec_result = nbe.get_handler("recommend_engagements")({})
    plan_json = rec_result[0]["text"]

    r1 = nbe.get_handler("render_plan")({"plan_json": plan_json, "format": "csv", "output_dir": str(tmp_path)})
    r2 = nbe.get_handler("render_plan")({"plan_json": plan_json, "format": "csv", "output_dir": str(tmp_path)})
    f1 = json.loads(r1[0]["text"])["files"]["engagements_csv"]
    f2 = json.loads(r2[0]["text"])["files"]["engagements_csv"]
    assert f1 != f2


def test_render_plan_ignores_unknown_arguments_but_rejects_unsupported_format():
    schema = next(tool for tool in nbe.list_tools() if tool["name"] == "render_plan")["inputSchema"]

    assert schema["properties"]["format"]["enum"] == ["csv", "json"]
    nbe.get_handler("load_universe")({"source": "fixture"})
    plan_json = nbe.get_handler("recommend_engagements")({})[0]["text"]
    ignored = nbe.get_handler("render_plan")({"format": "json", "plan_json": plan_json, "legacy_argument": "ignored"})
    assert "engagements" in json.loads(ignored[0]["text"])

    rejected = nbe.get_handler("render_plan")({"format": "xlsx", "plan_json": plan_json, "legacy_argument": "ignored"})
    assert "Invalid render arguments" in json.loads(rejected[0]["text"])["error"]


def test_render_csv_removes_only_new_files_when_the_second_write_fails(tmp_path, monkeypatch):
    """A summary-write error must not leave an orphaned engagement CSV or delete prior output."""
    from pathlib import Path

    from open_pharma_plugins_next_best_engagement import _renderer

    nbe.get_handler("load_universe")({"source": "fixture"})
    plan = EngagementPlan.model_validate_json(nbe.get_handler("recommend_engagements")({})[0]["text"])
    existing = tmp_path / "existing.csv"
    existing.write_text("preserve me")
    original_open = Path.open

    class FailAfterSummaryWrite:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, content):
            return self.handle.write(content)

        def flush(self):
            self.handle.flush()
            raise OSError("simulated summary write failure")

        def fileno(self):
            return self.handle.fileno()

    def fail_summary_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path.name.startswith("plan_summary_"):
            return FailAfterSummaryWrite(handle)
        return handle

    monkeypatch.setattr(Path, "open", fail_summary_open)

    with pytest.raises(OSError, match="simulated summary write failure"):
        _renderer.render_csv(plan, str(tmp_path))

    assert existing.read_text() == "preserve me"
    assert list(tmp_path.glob("engagements_*.csv")) == []
    assert list(tmp_path.glob("plan_summary_*.csv")) == []


def test_render_plan_invalid_json():
    result = nbe.get_handler("render_plan")({"plan_json": "not json"})
    data = json.loads(result[0]["text"])
    assert "error" in data


def test_render_plan_uses_stored_plan():
    """render_plan without plan_json uses the last generated plan (#7)."""
    nbe.get_handler("load_universe")({"source": "fixture"})
    nbe.get_handler("recommend_engagements")({})

    result = nbe.get_handler("render_plan")({"format": "json"})
    data = json.loads(result[0]["text"])
    assert "engagements" in data
    assert data["metrics"]["total_planned"] > 0


def test_successful_universe_reload_clears_stored_plan(tmp_path):
    nbe.get_handler("load_universe")({"source": "fixture"})
    nbe.get_handler("recommend_engagements")({})
    replacement = tmp_path / "replacement.csv"
    replacement.write_text("hcp_id,hcp_name,territory_id,rep_id,consent_email\nNEW-1,Dr New,T1,R1,true\n")

    nbe.get_handler("load_universe")({"source": str(replacement)})
    result = nbe.get_handler("render_plan")({"format": "json"})

    assert "No plan available" in json.loads(result[0]["text"])["error"]


def test_identical_reload_invalidates_an_in_flight_plan_snapshot():
    load_fixture()
    universe, rep_info, load_generation = get_session_snapshot()
    in_flight_plan = generate_plan(
        universe,
        rep_info,
        ConstraintConfig(),
        date(2026, 8, 20),
        universe_generation=load_generation,
    )

    load_fixture()
    _, _, reloaded_generation = get_session_snapshot()

    assert reloaded_generation > load_generation
    assert store_plan(in_flight_plan, load_generation) is False
    result = nbe.get_handler("render_plan")({"format": "json"})
    assert "No plan available" in json.loads(result[0]["text"])["error"]


def test_identical_reload_rejects_explicit_plan_from_prior_generation():
    nbe.get_handler("load_universe")({"source": "fixture"})
    old_plan = nbe.get_handler("recommend_engagements")({})[0]["text"]

    nbe.get_handler("load_universe")({"source": "fixture"})
    result = nbe.get_handler("render_plan")({"format": "json", "plan_json": old_plan})

    assert json.loads(result[0]["text"])["error"] == "Plan is stale for the currently loaded universe."


def test_render_explicit_plan_rejects_stale_universe_fingerprint(monkeypatch):
    from open_pharma_plugins_next_best_engagement import _universe as universe_store

    nbe.get_handler("load_universe")({"source": "fixture"})
    plan_json = nbe.get_handler("recommend_engagements")({})[0]["text"]
    monkeypatch.setattr(universe_store._session_state, "universe_fingerprint", "different")

    result = nbe.get_handler("render_plan")({"format": "json", "plan_json": plan_json})

    assert json.loads(result[0]["text"])["error"] == "Plan is stale for the currently loaded universe."


def test_render_explicit_plan_requires_a_loaded_universe(monkeypatch):
    from open_pharma_plugins_next_best_engagement import _universe as universe_store

    nbe.get_handler("load_universe")({"source": "fixture"})
    plan_json = nbe.get_handler("recommend_engagements")({})[0]["text"]
    monkeypatch.setattr(universe_store._session_state, "universe_fingerprint", None)

    result = nbe.get_handler("render_plan")({"format": "json", "plan_json": plan_json})

    assert json.loads(result[0]["text"])["error"] == "No universe loaded. Call load_universe first."


def test_render_stored_plan_rejects_stale_universe_fingerprint(monkeypatch):
    from open_pharma_plugins_next_best_engagement import _universe as universe_store

    nbe.get_handler("load_universe")({"source": "fixture"})
    plan = json.loads(nbe.get_handler("recommend_engagements")({})[0]["text"])
    assert plan["universe_fingerprint"]
    monkeypatch.setattr(universe_store._session_state, "universe_fingerprint", "stale")

    result = nbe.get_handler("render_plan")({"format": "json"})

    assert json.loads(result[0]["text"])["error"] == "Stored plan is stale for the currently loaded universe."


def test_render_plan_no_plan_available():
    """render_plan without plan_json or stored plan returns an error (#7)."""
    import open_pharma_plugins_next_best_engagement._universe as u

    with u._session_state.lock:
        u._session_state.last_plan = None
    result = nbe.get_handler("render_plan")({"format": "json"})
    data = json.loads(result[0]["text"])
    assert "error" in data


# ---------------------------------------------------------------------------
# Full integration: fixture → recommend → validate
# ---------------------------------------------------------------------------


def test_fixture_full_pipeline():
    """End-to-end: load fixtures, generate plan, validate structure and metrics."""
    today = date(2026, 8, 20)
    load_fixture()
    universe = get_universe()
    rep_info = get_rep_info()
    config = ConstraintConfig()

    plan = generate_plan(universe, rep_info, config, today)

    assert plan.period_start == today
    assert plan.period_end == today + timedelta(days=30)

    # All HCPs accounted for
    planned_ids = {e.hcp_id for e in plan.engagements}
    unassigned_ids = {u.hcp_id for u in plan.unassigned}
    all_ids = {h.hcp_id for h in universe}
    assert planned_ids | unassigned_ids == all_ids
    assert not (planned_ids & unassigned_ids)

    # Coverage targets: Tier A should be close to 95%
    tier_a = plan.metrics.coverage_by_tier.get("A")
    assert tier_a is not None
    assert tier_a.actual_pct >= 0.5  # at least half of A should be covered

    # No duplicate assignments
    assert len(planned_ids) == len(plan.engagements)

    # Rationale is non-empty
    for e in plan.engagements:
        assert len(e.rationale) > 10

    # Scores are valid
    for e in plan.engagements:
        assert 0.0 <= e.score <= 1.0

    # EngagementPlan serialises cleanly
    plan_json = plan.model_dump_json()
    roundtrip = EngagementPlan.model_validate_json(plan_json)
    assert roundtrip.metrics.total_planned == plan.metrics.total_planned


# ---------------------------------------------------------------------------
# #5 — Scoring weight overrides via tool args
# ---------------------------------------------------------------------------


def test_weight_override_changes_scores():
    """Overriding tier_value weight to 0 should reduce Tier A's advantage."""
    nbe.get_handler("load_universe")({"source": "fixture"})

    default_result = nbe.get_handler("recommend_engagements")({})
    default_plan = json.loads(default_result[0]["text"])

    override_result = nbe.get_handler("recommend_engagements")({"weight_tier_value": 0.0, "weight_recency_gap": 0.60})
    override_plan = json.loads(override_result[0]["text"])

    # Both should produce valid plans
    assert default_plan["metrics"]["total_planned"] > 0
    assert override_plan["metrics"]["total_planned"] > 0

    # Scores should differ
    default_scores = {e["hcp_id"]: e["score"] for e in default_plan["engagements"]}
    override_scores = {e["hcp_id"]: e["score"] for e in override_plan["engagements"]}
    shared_ids = set(default_scores.keys()) & set(override_scores.keys())
    diffs = [abs(default_scores[i] - override_scores[i]) for i in shared_ids]
    assert any(d > 0.01 for d in diffs), "Weight override should change at least some scores"


def test_recommend_schema_uses_channel_diversity_and_has_no_per_hcp_caps():
    schema = next(tool for tool in nbe.list_tools() if tool["name"] == "recommend_engagements")["inputSchema"]

    assert "weight_channel_diversity" in schema["properties"]
    assert "weight_channel_freshness" not in schema["properties"]
    assert "max_visits_per_hcp" not in schema["properties"]
    assert "max_emails_per_hcp" not in schema["properties"]


def test_recommend_handler_rejects_invalid_planning_arguments():
    nbe.get_handler("load_universe")({"source": "fixture"})

    result = nbe.get_handler("recommend_engagements")({"period_days": 0})

    assert "Invalid recommendation arguments" in json.loads(result[0]["text"])["error"]


def test_recommend_handler_rejects_periods_beyond_one_year():
    nbe.get_handler("load_universe")({"source": "fixture"})

    result = nbe.get_handler("recommend_engagements")({"period_days": 366})

    assert "Invalid recommendation arguments" in json.loads(result[0]["text"])["error"]


# ---------------------------------------------------------------------------
# #9 — Empty CSV keeps header
# ---------------------------------------------------------------------------


def test_empty_plan_csv_has_header(tmp_path):
    """An empty plan should still write a CSV with a header row (#9)."""
    from open_pharma_plugins_next_best_engagement._renderer import render_csv
    from open_pharma_plugins_next_best_engagement.models import PlanMetrics

    empty_plan = EngagementPlan(
        universe_fingerprint="test",
        universe_generation=0,
        period_start=date(2026, 8, 20),
        period_end=date(2026, 9, 19),
        generated_at="2026-08-20",
        engagements=[],
        unassigned=[],
        metrics=PlanMetrics(
            total_universe=0,
            total_eligible=0,
            total_planned=0,
            coverage_pct=0.0,
            coverage_by_tier={},
            rep_utilization=[],
            channel_mix={},
            no_action_count=0,
            no_action_reasons={},
        ),
    )
    paths = render_csv(empty_plan, str(tmp_path))
    from pathlib import Path

    content = Path(paths["engagements_csv"]).read_text()
    assert content.startswith("hcp_id,")
    assert content.strip().count("\n") == 0  # header only, no data rows


# ---------------------------------------------------------------------------
# #10 — Rep capacity uses max across rows
# ---------------------------------------------------------------------------


def test_rep_capacity_uses_max():
    """If two rows list different capacities for the same rep, use the max (#10)."""
    import tempfile

    from open_pharma_plugins_next_best_engagement._universe import load_csv

    csv_content = "hcp_id,hcp_name,territory_id,rep_id,rep_max_visits_per_week\nH1,Dr A,T1,R1,15\nH2,Dr B,T1,R1,25\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        path = f.name

    load_csv(path)
    reps = get_rep_info()
    assert reps["R1"]["capacity"] == 25

    import os

    os.unlink(path)


# ---------------------------------------------------------------------------
# #11 — Extra column auto-coercion
# ---------------------------------------------------------------------------


def test_extra_column_bool_coercion():
    """Extra columns with true/false should be coerced to Python bools (#11)."""
    load_fixture()
    universe = get_universe()
    for row in universe:
        kol = row.model_extra.get("kol_flag")
        assert isinstance(kol, bool), f"kol_flag should be bool, got {type(kol)}: {kol}"


def test_extra_column_string_preserved():
    """Extra columns that aren't bool/int/float stay as strings (#11)."""
    load_fixture()
    universe = get_universe()
    for row in universe:
        aff = row.model_extra.get("affiliation")
        assert isinstance(aff, str), f"affiliation should be str, got {type(aff)}: {aff}"
