"""Tests for the territory-alignment capability."""

import json
import os
from pathlib import Path

import pytest

import open_pharma_plugins_territory_alignment as ta
from open_pharma_plugins_territory_alignment.data import (
    get_constraints,
    get_current_alignment,
    get_hcps,
    get_reps,
    load_all,
)
from open_pharma_plugins_territory_alignment.geo import (
    centroid,
    grid_cluster,
    haversine,
    nearest_neighbor_route,
    travel_minutes,
)
from open_pharma_plugins_territory_alignment.models import (
    HCP,
    ObjectiveWeights,
    Rep,
)
from open_pharma_plugins_territory_alignment.solver import solve


def _write_minimal_data(directory: Path, *, hcp_id: str = "H1", hcp_name: str = "Dr A") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "hcps.csv").write_text(
        f"hcp_id,name,segment,lat,lng,consent_visit\n{hcp_id},{hcp_name},high,1,1,true\n",
        encoding="utf-8",
    )
    (directory / "reps.csv").write_text(
        "rep_id,name,base_lat,base_lng\nR1,Rep A,1,1\n",
        encoding="utf-8",
    )
    (directory / "current_alignment.csv").write_text(
        f"hcp_id,primary_rep\n{hcp_id},R1\n",
        encoding="utf-8",
    )
    (directory / "constraints.csv").write_text("type,scope,value\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Package sanity
# ---------------------------------------------------------------------------


def test_lists_six_tools():
    names = {t["name"] for t in ta.list_tools()}
    assert names == {"ta_status", "ta_align", "ta_evaluate", "ta_compare", "ta_cluster", "ta_visualize"}


def test_version():
    assert ta.__version__ == "1.2.0"


# ---------------------------------------------------------------------------
# Geo utilities
# ---------------------------------------------------------------------------


def test_haversine_known_distance():
    nyc = (40.7128, -74.0060)
    la = (34.0522, -118.2437)
    d = haversine(*nyc, *la)
    assert 3900 < d < 4000


def test_haversine_zero():
    assert haversine(0, 0, 0, 0) == 0.0


def test_travel_minutes():
    assert travel_minutes(40.0) == 60.0
    assert travel_minutes(0) == 0.0


def test_centroid():
    pts = [(40.0, -74.0), (42.0, -72.0)]
    c = centroid(pts)
    assert abs(c[0] - 41.0) < 0.01
    assert abs(c[1] - (-73.0)) < 0.01


def test_grid_cluster_basic():
    points = [(0, 0, "a"), (0.01, 0.01, "b"), (10, 10, "c"), (10.01, 10.01, "d")]
    clusters = grid_cluster(points, target_per_cluster=2)
    assert len(clusters) >= 2
    all_ids = [pid for c in clusters for pid in c]
    assert set(all_ids) == {"a", "b", "c", "d"}


def test_nearest_neighbor_route():
    points = [(0, 0), (1, 0), (2, 0), (3, 0)]
    order, km = nearest_neighbor_route(points)
    assert len(order) == 4
    assert km > 0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def test_default_inputs_use_packaged_fixtures(tmp_path, monkeypatch):
    from open_pharma_plugins_territory_alignment import data

    monkeypatch.delenv("OPEN_PHARMA_TA_DATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    summary = data.load_all()
    assert summary["data_source"] == str(Path(data.__file__).parent / "fixtures")
    assert summary["hcp_count"] == 80
    assert summary["rep_count"] == 8


def test_default_scenarios_use_private_user_storage(tmp_path, monkeypatch):
    from open_pharma_plugins_territory_alignment import data

    monkeypatch.delenv("OPEN_PHARMA_TA_SCENARIOS_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = solve(
        hcps=[HCP(hcp_id="H1", name="Dr A")],
        reps=[Rep(rep_id="R1", name="Rep A")],
        current_alignment=[],
        constraints=[],
        weights=ObjectiveWeights(),
        scenario_name="default-location",
        max_iterations=0,
    ).model_dump(mode="json")
    scenario_path = Path(data.save_scenario("default-location", result))

    assert scenario_path.parent == tmp_path / ".open-pharma-plugins" / "territory-alignment" / "scenarios"
    if os.name != "nt":
        assert scenario_path.parent.stat().st_mode & 0o777 == 0o700
        assert scenario_path.stat().st_mode & 0o777 == 0o600


def test_load_fixtures():
    summary = load_all()
    assert summary["loaded"] is True
    assert summary["hcp_count"] == 80
    assert summary["rep_count"] == 8


def test_load_all_rejects_an_incomplete_configured_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_PHARMA_TA_DATA_DIR", str(tmp_path))
    (tmp_path / "hcps.csv").write_text("hcp_id,name\nH1,Dr A\n", encoding="utf-8")

    with pytest.raises(
        ValueError, match="missing required territory data files: constraints.csv, current_alignment.csv, reps.csv"
    ):
        load_all()


def test_load_all_rejects_duplicate_hcp_identifiers(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_PHARMA_TA_DATA_DIR", str(tmp_path))
    (tmp_path / "hcps.csv").write_text("hcp_id,name\nH1,Dr A\nH1,Dr Duplicate\n", encoding="utf-8")
    (tmp_path / "reps.csv").write_text("rep_id,name\nR1,Rep A\n", encoding="utf-8")
    (tmp_path / "current_alignment.csv").write_text("hcp_id,primary_rep\nH1,R1\n", encoding="utf-8")
    (tmp_path / "constraints.csv").write_text(
        "type,scope,value\nmax_hcps_per_rep,global,10\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate HCP identifiers: H1"):
        load_all()


def test_hcps_have_coordinates():
    load_all()
    hcps = get_hcps()
    geo_count = sum(1 for h in hcps if h.lat is not None)
    assert geo_count == 80


def test_reps_loaded():
    load_all()
    reps = get_reps()
    assert len(reps) == 8
    for r in reps:
        assert r.product_expertise


def test_alignment_loaded():
    load_all()
    alignment = get_current_alignment()
    assert len(alignment) == 80


def test_constraints_loaded():
    load_all()
    constraints = get_constraints()
    assert len(constraints) > 0
    types = {c.type for c in constraints}
    assert "product_match" in types


def test_reps_with_vacancy():
    load_all()
    reps = get_reps(vacancies=["R004"])
    assert all(r.rep_id != "R004" for r in reps)
    assert len(reps) == 7


def test_reps_with_new_hire():
    from open_pharma_plugins_territory_alignment.models import NewHire

    load_all()
    nh = NewHire(rep_id="R009", name="New Rep", base_lat=40.0, base_lng=-74.0)
    reps = get_reps(new_hires=[nh])
    assert len(reps) == 9
    assert any(r.rep_id == "R009" for r in reps)


@pytest.mark.parametrize(
    "factory, expected",
    [
        (lambda: HCP(hcp_id="H1", name="Dr A", lat=91), "less than or equal to 90"),
        (lambda: HCP(hcp_id="H1", name="Dr A", lng=-181), "greater than or equal to -180"),
        (lambda: HCP(hcp_id="H1", name="Dr A", segment="urgent"), "Input should be 'high', 'medium' or 'low'"),
        (lambda: Rep(rep_id="R1", name="Rep A", max_daily_calls=0), "greater than or equal to 1"),
    ],
)
def test_domain_models_reject_invalid_planning_values(factory, expected):
    with pytest.raises(ValueError, match=expected):
        factory()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_gini_equal():
    from open_pharma_plugins_territory_alignment.scoring import _gini

    assert _gini([10, 10, 10, 10]) < 0.01


def test_gini_unequal():
    from open_pharma_plugins_territory_alignment.scoring import _gini

    g = _gini([1, 1, 1, 100])
    assert g > 0.3


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------


def test_solver_produces_assignments():
    load_all()
    result = solve(
        hcps=get_hcps(),
        reps=get_reps(),
        current_alignment=get_current_alignment(),
        constraints=get_constraints(),
        weights=ObjectiveWeights(),
        scenario_name="test_basic",
        max_iterations=10,
    )
    assert len(result.assignments) > 0
    assert result.objectives.composite >= 0


def test_solver_respects_product_match():
    hcps = [
        HCP(hcp_id="H1", name="Dr A", product_requirements=["product_x"]),
    ]
    reps = [
        Rep(rep_id="R1", name="Rep 1", product_expertise=["product_y"]),
    ]
    from open_pharma_plugins_territory_alignment.models import Constraint

    constraints = [Constraint(type="product_match", scope="global", value="required")]

    result = solve(
        hcps=hcps,
        reps=reps,
        current_alignment=[],
        constraints=constraints,
        weights=ObjectiveWeights(),
        scenario_name="product_test",
        max_iterations=0,
    )
    assert len(result.unassigned) == 1
    assert "product expertise" in result.unassigned[0].reason


def test_solver_respects_overrides():
    load_all()
    from open_pharma_plugins_territory_alignment.models import Override

    result = solve(
        hcps=get_hcps(),
        reps=get_reps(),
        current_alignment=get_current_alignment(),
        constraints=get_constraints(),
        weights=ObjectiveWeights(),
        overrides=[Override(hcp_id="H001", rep_id="R005", reason="test pin")],
        scenario_name="override_test",
        max_iterations=0,
    )
    h001 = next(a for a in result.assignments if a.hcp_id == "H001")
    assert h001.primary_rep == "R005"


def test_solver_rejects_override_references_that_do_not_exist():
    from open_pharma_plugins_territory_alignment.models import Override

    with pytest.raises(ValueError, match="override references unknown HCP 'MISSING'"):
        solve(
            hcps=[HCP(hcp_id="H1", name="Dr A")],
            reps=[Rep(rep_id="R1", name="Rep 1")],
            current_alignment=[],
            constraints=[],
            weights=ObjectiveWeights(),
            overrides=[Override(hcp_id="MISSING", rep_id="R1")],
            max_iterations=0,
        )


def test_solver_rejects_a_current_alignment_with_an_unknown_rep():
    from open_pharma_plugins_territory_alignment.models import CurrentAssignment

    with pytest.raises(ValueError, match="current alignment references unknown rep 'MISSING'"):
        solve(
            hcps=[HCP(hcp_id="H1", name="Dr A")],
            reps=[Rep(rep_id="R1", name="Rep 1")],
            current_alignment=[CurrentAssignment(hcp_id="H1", primary_rep="MISSING")],
            constraints=[],
            weights=ObjectiveWeights(),
            max_iterations=0,
        )


def test_solver_rejects_override_that_breaks_a_hard_product_constraint():
    from open_pharma_plugins_territory_alignment.models import Constraint, Override

    with pytest.raises(ValueError, match="override H1 -> R1 violates product_match"):
        solve(
            hcps=[HCP(hcp_id="H1", name="Dr A", product_requirements=["product_a"])],
            reps=[Rep(rep_id="R1", name="Rep 1", product_expertise=["product_b"])],
            current_alignment=[],
            constraints=[Constraint(type="product_match", scope="global", value="required")],
            weights=ObjectiveWeights(),
            overrides=[Override(hcp_id="H1", rep_id="R1")],
            max_iterations=0,
        )


def test_product_match_requires_every_declared_product_requirement():
    from open_pharma_plugins_territory_alignment.models import Constraint

    result = solve(
        hcps=[HCP(hcp_id="H1", name="Dr A", product_requirements=["product_a", "product_b"])],
        reps=[Rep(rep_id="R1", name="Rep 1", product_expertise=["product_a"])],
        current_alignment=[],
        constraints=[Constraint(type="product_match", scope="global", value="required")],
        weights=ObjectiveWeights(),
        max_iterations=0,
    )

    assert result.assignments == []
    assert result.unassigned[0].reason == "no eligible rep has required product expertise"


def test_solver_vacancy():
    load_all()
    result = solve(
        hcps=get_hcps(),
        reps=get_reps(vacancies=["R004"]),
        current_alignment=get_current_alignment(),
        constraints=get_constraints(),
        weights=ObjectiveWeights(),
        scenario_name="vacancy_test",
        max_iterations=10,
        vacancies=["R004"],
    )
    assigned_reps = {a.primary_rep for a in result.assignments}
    assert "R004" not in assigned_reps


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def test_ta_status_tool():
    result = ta.get_handler("ta_status")({})
    data = json.loads(result[0]["text"])
    assert data["loaded"] is True
    assert data["hcp_count"] == 80


def test_ta_status_returns_a_structured_error_for_invalid_configured_data(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_PHARMA_TA_DATA_DIR", str(tmp_path))

    result = json.loads(ta.get_handler("ta_status")({})[0]["text"])

    assert "error" in result
    assert "missing required territory data files" in result["error"]


def test_ta_status_lists_scenarios_by_creation_time_with_run_metadata():
    ta.get_handler("ta_align")({"scenario_name": "zzz_first", "max_iterations": 0})
    ta.get_handler("ta_align")({"scenario_name": "aaa_second", "max_iterations": 0})

    status = json.loads(ta.get_handler("ta_status")({})[0]["text"])

    assert [item["name"] for item in status["scenarios"]] == ["aaa_second", "zzz_first"]
    assert all(item["created_at"].endswith("Z") for item in status["scenarios"])
    assert all(item["run_id"] for item in status["scenarios"])
    assert all(Path(item["report_file"]).is_file() for item in status["scenarios"])
    assert status["fixture_data"] is True


def test_ta_align_tool():
    result = ta.get_handler("ta_align")({"scenario_name": "tool_test", "max_iterations": 5})
    data = json.loads(result[0]["text"])
    assert "assignments" in data
    assert "objectives" in data
    assert data["scenario_name"] == "tool_test"


def test_ta_align_schema_bounds_weights_and_solver_work():
    schema = next(tool["inputSchema"] for tool in ta.list_tools() if tool["name"] == "ta_align")

    for field in ("weight_workload", "weight_travel", "weight_disruption", "weight_coverage"):
        assert schema["properties"][field]["minimum"] == 0
        assert schema["properties"][field]["maximum"] == 1
    assert schema["properties"]["max_iterations"] == {
        "default": 2000,
        "description": "Max local-search iterations",
        "maximum": 10000,
        "minimum": 0,
        "type": "integer",
    }


def test_ta_align_normalizes_partial_weight_overrides():
    result = ta.get_handler("ta_align")(
        {"scenario_name": "normalized_weights", "weight_workload": 0.5, "max_iterations": 0}
    )
    data = json.loads(result[0]["text"])

    assert sum(data["weights_used"].values()) == pytest.approx(1.0)


def test_ta_align_returns_a_structured_error_for_invalid_direct_input():
    result = json.loads(
        ta.get_handler("ta_align")({"scenario_name": "invalid_weight", "weight_workload": 2})[0]["text"]
    )

    assert "error" in result
    assert "less than or equal to 1" in result["error"]


def test_ta_align_returns_a_structured_error_for_invalid_solver_levers():
    result = json.loads(
        ta.get_handler("ta_align")(
            {
                "scenario_name": "invalid_override",
                "overrides": [{"hcp_id": "MISSING", "rep_id": "R001"}],
            }
        )[0]["text"]
    )

    assert "error" in result
    assert "unknown HCP" in result["error"]


def test_ta_align_rejects_an_unknown_vacancy_identifier():
    result = json.loads(
        ta.get_handler("ta_align")({"scenario_name": "invalid_vacancy", "vacancies": ["MISSING"]})[0]["text"]
    )

    assert "error" in result
    assert "unknown vacancy reps: MISSING" in result["error"]


def test_ta_align_persists_a_versioned_reproducible_input_snapshot():
    result = ta.get_handler("ta_align")({"scenario_name": "auditable", "vacancies": ["R004"], "max_iterations": 0})
    data = json.loads(result[0]["text"])

    assert data["metadata"]["schema_version"] == "1.0"
    assert data["metadata"]["plugin_version"] == ta.__version__
    assert data["metadata"]["run_id"]
    assert data["metadata"]["created_at"].endswith("Z")
    assert data["metadata"]["fixture_data"] is True
    assert set(data["metadata"]["input_fingerprints"]) == {
        "constraints.csv",
        "current_alignment.csv",
        "hcps.csv",
        "reps.csv",
    }
    snapshot = data["input_snapshot"]
    assert len(snapshot["hcps"]) == 80
    assert {rep["rep_id"] for rep in snapshot["reps"]} == {f"R{i:03d}" for i in range(1, 9)} - {"R004"}
    assert snapshot["levers"]["vacancies"] == ["R004"]


def test_ta_align_automatically_creates_primary_report_and_preserves_raw_paths(tmp_path, monkeypatch):
    scenarios = tmp_path / "scenarios"
    monkeypatch.setenv("OPEN_PHARMA_TA_SCENARIOS_DIR", str(scenarios))

    result = json.loads(ta.get_handler("ta_align")({"scenario_name": "visual", "max_iterations": 0})[0]["text"])

    artifacts = result["metadata"]["artifacts"]
    expected = {
        "primary_report": tmp_path / "visual.html",
        "scenario_json": scenarios / "visual.json",
        "assignments_csv": scenarios / "visual_assignments.csv",
        "territory_summary_csv": scenarios / "visual_territory_summary.csv",
    }
    assert Path(artifacts["primary_report"]) == expected["primary_report"]
    assert {key: Path(value) for key, value in artifacts["advanced_exports"].items()} == {
        key: value for key, value in expected.items() if key != "primary_report"
    }
    for path in expected.values():
        assert path.is_file()
        assert path.stat().st_size > 0
        assert path.stat().st_mode & 0o777 == 0o600

    html = expected["primary_report"].read_text(encoding="utf-8")
    assert "Offline relative territory view" in html
    assert "https://" not in html
    assert "http://" not in html


def test_ta_align_never_overwrites_an_existing_named_scenario():
    first = json.loads(ta.get_handler("ta_align")({"scenario_name": "immutable", "max_iterations": 0})[0]["text"])
    second = json.loads(ta.get_handler("ta_align")({"scenario_name": "immutable", "max_iterations": 0})[0]["text"])

    assert second == {"error": "Scenario 'immutable' already exists; choose a new scenario_name."}
    evaluated = json.loads(ta.get_handler("ta_evaluate")({"scenario_name": "immutable"})[0]["text"])
    assert evaluated["run_id"] == first["metadata"]["run_id"]


def test_ta_evaluate_tool():
    ta.get_handler("ta_align")({"scenario_name": "eval_test", "max_iterations": 5})
    result = ta.get_handler("ta_evaluate")({"scenario_name": "eval_test"})
    data = json.loads(result[0]["text"])
    assert "objectives" in data
    assert "territory_details" in data
    assert "workload_distribution" in data


def test_ta_evaluate_missing_scenario():
    result = ta.get_handler("ta_evaluate")({"scenario_name": "nonexistent"})
    data = json.loads(result[0]["text"])
    assert "error" in data


def test_ta_evaluate_returns_a_structured_error_for_missing_direct_input():
    result = json.loads(ta.get_handler("ta_evaluate")({})[0]["text"])

    assert "error" in result
    assert "scenario_name" in result["error"]


def test_ta_evaluate_returns_an_actionable_error_for_a_legacy_scenario(tmp_path, monkeypatch):
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "legacy.json").write_text(
        json.dumps({"scenario_name": "legacy", "assignments": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPEN_PHARMA_TA_SCENARIOS_DIR", str(scenarios))

    result = json.loads(ta.get_handler("ta_evaluate")({"scenario_name": "legacy"})[0]["text"])

    assert "error" in result
    assert "invalid or incompatible" in result["error"]
    assert "regenerate" in result["error"]


def test_ta_compare_tool():
    ta.get_handler("ta_align")({"scenario_name": "cmp_a", "max_iterations": 5})
    ta.get_handler("ta_align")({"scenario_name": "cmp_b", "max_iterations": 5, "weight_workload": 0.50})
    result = ta.get_handler("ta_compare")({"scenarios": ["cmp_a", "cmp_b"]})
    data = json.loads(result[0]["text"])
    assert "comparison_table" in data
    assert "pareto" in data
    assert "trade_off_narrative" in data


def test_ta_compare_rejects_duplicate_scenario_names():
    ta.get_handler("ta_align")({"scenario_name": "cmp_unique", "max_iterations": 0})

    result = json.loads(ta.get_handler("ta_compare")({"scenarios": ["cmp_unique", "cmp_unique"]})[0]["text"])

    assert "error" in result
    assert "unique" in result["error"]


def test_ta_compare_rejects_scenarios_from_different_input_universes(tmp_path, monkeypatch):
    scenarios_dir = tmp_path / "scenarios"
    data_a = tmp_path / "data-a"
    data_b = tmp_path / "data-b"
    _write_minimal_data(data_a, hcp_id="H1")
    _write_minimal_data(data_b, hcp_id="H2")
    monkeypatch.setenv("OPEN_PHARMA_TA_SCENARIOS_DIR", str(scenarios_dir))
    monkeypatch.setenv("OPEN_PHARMA_TA_DATA_DIR", str(data_a))
    ta.get_handler("ta_align")({"scenario_name": "source_a", "max_iterations": 0})
    monkeypatch.setenv("OPEN_PHARMA_TA_DATA_DIR", str(data_b))
    ta.get_handler("ta_align")({"scenario_name": "source_b", "max_iterations": 0})

    result = json.loads(ta.get_handler("ta_compare")({"scenarios": ["source_a", "source_b"]})[0]["text"])

    assert "error" in result
    assert "different input universes" in result["error"]


def test_ta_cluster_tool():
    ta.get_handler("ta_align")({"scenario_name": "cluster_base", "max_iterations": 5})
    result = ta.get_handler("ta_cluster")({"scenario_name": "cluster_base", "rep_id": "R001"})
    data = json.loads(result[0]["text"])
    assert "clusters" in data
    assert "visit_sequence" in data
    assert data["total_hcps"] > 0


def test_ta_cluster_missing_rep():
    ta.get_handler("ta_align")({"scenario_name": "missing_rep", "max_iterations": 0})
    result = ta.get_handler("ta_cluster")({"scenario_name": "missing_rep", "rep_id": "NONEXISTENT"})
    data = json.loads(result[0]["text"])
    assert "error" in data


def test_ta_cluster_uses_the_named_scenario_snapshot_for_new_hires():
    ta.get_handler("ta_align")(
        {
            "scenario_name": "new_hire_plan",
            "vacancies": [f"R{i:03d}" for i in range(1, 9)],
            "new_hires": [
                {
                    "rep_id": "R009",
                    "name": "New Rep",
                    "base_lat": 40.75,
                    "base_lng": -73.98,
                    "product_expertise": ["product_a", "product_b"],
                }
            ],
            "max_iterations": 0,
        }
    )

    plan = json.loads(
        ta.get_handler("ta_cluster")({"scenario_name": "new_hire_plan", "rep_id": "R009", "period": "2026-W36"})[0][
            "text"
        ]
    )

    assert plan["scenario_name"] == "new_hire_plan"
    assert plan["rep_id"] == "R009"
    assert plan["total_hcps"] > 0


def test_ta_cluster_route_includes_the_outbound_and_return_base_legs(tmp_path, monkeypatch):
    data_dir = tmp_path / "route-data"
    data_dir.mkdir()
    (data_dir / "hcps.csv").write_text(
        "hcp_id,name,lat,lng,consent_visit\nH1,Dr A,0,1,true\n",
        encoding="utf-8",
    )
    (data_dir / "reps.csv").write_text(
        "rep_id,name,base_lat,base_lng\nR1,Rep A,0,0\n",
        encoding="utf-8",
    )
    (data_dir / "current_alignment.csv").write_text("hcp_id,primary_rep\nH1,R1\n", encoding="utf-8")
    (data_dir / "constraints.csv").write_text("type,scope,value\n", encoding="utf-8")
    monkeypatch.setenv("OPEN_PHARMA_TA_DATA_DIR", str(data_dir))

    ta.get_handler("ta_align")({"scenario_name": "route_legs", "max_iterations": 0})
    plan = json.loads(
        ta.get_handler("ta_cluster")(
            {
                "scenario_name": "route_legs",
                "rep_id": "R1",
                "period": "2026-W36",
                "appointments": [{"hcp_id": "H1", "date": "2026-09-02", "time": "10:30"}],
            }
        )[0]["text"]
    )

    assert plan["clusters"][0]["estimated_route_km"] == pytest.approx(222.4, abs=0.2)
    assert plan["clusters"][0]["estimated_travel_min"] == pytest.approx(333.6, abs=0.3)
    assert plan["clusters"][0]["suggested_date"] == "2026-09-02"
    assert plan["visit_sequence"][0]["appointment_date"] == "2026-09-02"
    assert plan["visit_sequence"][0]["appointment_time"] == "10:30"


def test_ta_cluster_rejects_appointments_that_exceed_daily_call_capacity(tmp_path, monkeypatch):
    data_dir = tmp_path / "daily-cap-data"
    data_dir.mkdir()
    (data_dir / "hcps.csv").write_text(
        "hcp_id,name,lat,lng,consent_visit\nH1,Dr A,0,1,true\nH2,Dr B,10,10,true\n",
        encoding="utf-8",
    )
    (data_dir / "reps.csv").write_text(
        "rep_id,name,base_lat,base_lng,max_daily_calls\nR1,Rep A,0,0,1\n",
        encoding="utf-8",
    )
    (data_dir / "current_alignment.csv").write_text(
        "hcp_id,primary_rep\nH1,R1\nH2,R1\n",
        encoding="utf-8",
    )
    (data_dir / "constraints.csv").write_text("type,scope,value\n", encoding="utf-8")
    monkeypatch.setenv("OPEN_PHARMA_TA_DATA_DIR", str(data_dir))
    ta.get_handler("ta_align")({"scenario_name": "daily_cap", "max_iterations": 0})

    plan = json.loads(
        ta.get_handler("ta_cluster")(
            {
                "scenario_name": "daily_cap",
                "rep_id": "R1",
                "period": "2026-W36",
                "appointments": [
                    {"hcp_id": "H1", "date": "2026-09-02", "time": "09:00"},
                    {"hcp_id": "H2", "date": "2026-09-02", "time": "14:00"},
                ],
            }
        )[0]["text"]
    )

    assert "error" in plan
    assert "max_daily_calls" in plan["error"]


def test_ta_cluster_sequences_fixed_appointments_in_time_order(tmp_path, monkeypatch):
    data_dir = tmp_path / "appointment-order-data"
    data_dir.mkdir()
    (data_dir / "hcps.csv").write_text(
        "hcp_id,name,lat,lng,consent_visit\nH1,Dr Near,0,0.1,true\nH2,Dr Far,0,1,true\n",
        encoding="utf-8",
    )
    (data_dir / "reps.csv").write_text(
        "rep_id,name,base_lat,base_lng,max_daily_calls\nR1,Rep A,0,0,8\n",
        encoding="utf-8",
    )
    (data_dir / "current_alignment.csv").write_text(
        "hcp_id,primary_rep\nH1,R1\nH2,R1\n",
        encoding="utf-8",
    )
    (data_dir / "constraints.csv").write_text("type,scope,value\n", encoding="utf-8")
    monkeypatch.setenv("OPEN_PHARMA_TA_DATA_DIR", str(data_dir))
    ta.get_handler("ta_align")({"scenario_name": "appointment_order", "max_iterations": 0})

    plan = json.loads(
        ta.get_handler("ta_cluster")(
            {
                "scenario_name": "appointment_order",
                "rep_id": "R1",
                "period": "2026-W36",
                "appointments": [
                    {"hcp_id": "H1", "date": "2026-09-02", "time": "14:00"},
                    {"hcp_id": "H2", "date": "2026-09-02", "time": "09:00"},
                ],
            }
        )[0]["text"]
    )

    assert [stop["hcp_id"] for stop in plan["visit_sequence"]] == ["H2", "H1"]


# ---------------------------------------------------------------------------
# Full integration
# ---------------------------------------------------------------------------


def test_solver_respects_lock_reps():
    load_all()
    result = solve(
        hcps=get_hcps(),
        reps=get_reps(),
        current_alignment=get_current_alignment(),
        constraints=get_constraints(),
        weights=ObjectiveWeights(),
        lock_reps=["R001"],
        scenario_name="lock_test",
        max_iterations=10,
    )
    current = {a.hcp_id: a.primary_rep for a in get_current_alignment()}
    for a in result.assignments:
        if current.get(a.hcp_id) == "R001":
            assert a.primary_rep == "R001", f"{a.hcp_id} should stay with R001"


def test_solver_rejects_locked_relationships_that_exceed_hard_capacity():
    from open_pharma_plugins_territory_alignment.models import Constraint, CurrentAssignment

    with pytest.raises(ValueError, match="locked assignments for rep 'R1' exceed capacity"):
        solve(
            hcps=[HCP(hcp_id="H1", name="Dr A"), HCP(hcp_id="H2", name="Dr B")],
            reps=[Rep(rep_id="R1", name="Rep 1")],
            current_alignment=[
                CurrentAssignment(hcp_id="H1", primary_rep="R1"),
                CurrentAssignment(hcp_id="H2", primary_rep="R1"),
            ],
            constraints=[Constraint(type="max_hcps_per_rep", scope="global", value=1)],
            weights=ObjectiveWeights(),
            lock_reps=["R1"],
            max_iterations=0,
        )


def test_solver_respects_account_grouping():
    """HCPs in a constrained account must share the same primary rep."""
    load_all()
    result = solve(
        hcps=get_hcps(),
        reps=get_reps(),
        current_alignment=get_current_alignment(),
        constraints=get_constraints(),
        weights=ObjectiveWeights(),
        scenario_name="account_group_test",
        max_iterations=10,
    )
    assigned = {a.hcp_id: a.primary_rep for a in result.assignments}
    a001_hcps = [h for h in get_hcps() if h.account_id == "A001"]
    a001_reps = {assigned[h.hcp_id] for h in a001_hcps if h.hcp_id in assigned}
    assert len(a001_reps) == 1, f"A001 HCPs should all share one rep, got {a001_reps}"


def test_solver_leaves_an_infeasible_account_group_unassigned_instead_of_splitting_it():
    """An indivisible account must never be weakened into independent HCP assignments."""
    from open_pharma_plugins_territory_alignment.models import Constraint

    hcps = [
        HCP(hcp_id="H1", name="Dr A", account_id="A1"),
        HCP(hcp_id="H2", name="Dr B", account_id="A1"),
    ]
    reps = [Rep(rep_id="R1", name="Rep 1"), Rep(rep_id="R2", name="Rep 2")]
    constraints = [
        Constraint(type="account_grouping", scope="account:A1", value="same_primary_rep"),
        Constraint(type="max_hcps_per_rep", scope="global", value=1),
    ]

    result = solve(
        hcps=hcps,
        reps=reps,
        current_alignment=[],
        constraints=constraints,
        weights=ObjectiveWeights(),
        max_iterations=0,
    )

    assert result.assignments == []
    assert {item.hcp_id for item in result.unassigned} == {"H1", "H2"}
    assert all("account group" in item.reason for item in result.unassigned)


def test_solver_rejects_conflicting_pins_inside_one_account_group():
    from open_pharma_plugins_territory_alignment.models import Constraint, Override

    with pytest.raises(ValueError, match="account group 'A1' is pinned to multiple reps"):
        solve(
            hcps=[
                HCP(hcp_id="H1", name="Dr A", account_id="A1"),
                HCP(hcp_id="H2", name="Dr B", account_id="A1"),
            ],
            reps=[Rep(rep_id="R1", name="Rep 1"), Rep(rep_id="R2", name="Rep 2")],
            current_alignment=[],
            constraints=[Constraint(type="account_grouping", scope="account:A1", value="same_primary_rep")],
            weights=ObjectiveWeights(),
            overrides=[Override(hcp_id="H1", rep_id="R1"), Override(hcp_id="H2", rep_id="R2")],
            max_iterations=0,
        )


def test_solver_rejects_a_pinned_account_group_that_cannot_fit_the_pinned_rep():
    from open_pharma_plugins_territory_alignment.models import Constraint, Override

    with pytest.raises(ValueError, match="pinned account group 'A1' cannot fit rep 'R1'"):
        solve(
            hcps=[
                HCP(hcp_id="H1", name="Dr A", account_id="A1"),
                HCP(hcp_id="H2", name="Dr B", account_id="A1"),
            ],
            reps=[Rep(rep_id="R1", name="Rep 1"), Rep(rep_id="R2", name="Rep 2")],
            current_alignment=[],
            constraints=[
                Constraint(type="account_grouping", scope="account:A1", value="same_primary_rep"),
                Constraint(type="max_hcps_per_rep", scope="global", value=1),
            ],
            weights=ObjectiveWeights(),
            overrides=[Override(hcp_id="H1", rep_id="R1")],
            max_iterations=0,
        )


def test_solver_respects_max_weekly_hours():
    """A rep with low max_weekly_hours should not be overloaded."""
    hcps_list = [HCP(hcp_id=f"H{i}", name=f"Dr {i}", segment="high", annual_potential=90000) for i in range(20)]
    reps_list = [
        Rep(rep_id="R1", name="Rep 1", max_weekly_hours=5.0),
        Rep(rep_id="R2", name="Rep 2", max_weekly_hours=40.0),
    ]
    result = solve(
        hcps=hcps_list,
        reps=reps_list,
        current_alignment=[],
        constraints=[],
        weights=ObjectiveWeights(),
        scenario_name="hours_test",
        max_iterations=0,
    )
    r1_count = sum(1 for a in result.assignments if a.primary_rep == "R1")
    assert r1_count <= 10, f"R1 (5h cap) got {r1_count} high-segment HCPs"


def test_solver_local_search_never_swaps_a_rep_over_weekly_capacity():
    hcps = [
        HCP(hcp_id="LOW", name="Low", segment="low", lat=0, lng=0.5, annual_potential=100),
        HCP(hcp_id="HIGH", name="High", segment="high", lat=0, lng=0, annual_potential=50),
    ]
    reps = [
        Rep(rep_id="R1", name="Rep 1", base_lat=0, base_lng=0, max_weekly_hours=0.2),
        Rep(rep_id="R2", name="Rep 2", base_lat=0, base_lng=0.5, max_weekly_hours=40),
    ]
    from open_pharma_plugins_territory_alignment.models import CurrentAssignment

    result = solve(
        hcps=hcps,
        reps=reps,
        current_alignment=[
            CurrentAssignment(hcp_id="LOW", primary_rep="R1"),
            CurrentAssignment(hcp_id="HIGH", primary_rep="R2"),
        ],
        constraints=[],
        weights=ObjectiveWeights(workload_balance=0, travel_efficiency=1, disruption=0, coverage=0),
        max_iterations=5,
    )

    assigned = {item.hcp_id: item.primary_rep for item in result.assignments}
    assert assigned == {"HIGH": "R2", "LOW": "R1"}


def test_workload_balance_counts_reps_with_zero_assignments():
    from open_pharma_plugins_territory_alignment.models import CurrentAssignment

    result = solve(
        hcps=[HCP(hcp_id="H1", name="Dr A", segment="high")],
        reps=[Rep(rep_id="R1", name="Rep 1"), Rep(rep_id="R2", name="Rep 2")],
        current_alignment=[CurrentAssignment(hcp_id="H1", primary_rep="R1")],
        constraints=[],
        weights=ObjectiveWeights(),
        max_iterations=0,
    )

    assert result.objectives.raw.workload_gini == 0.5
    assert {item.rep_id: item.hcp_count for item in result.territory_summary} == {"R1": 1, "R2": 0}


def test_frequency_cap_constraint_controls_visit_frequency_and_workload():
    from open_pharma_plugins_territory_alignment.models import Constraint

    result = solve(
        hcps=[HCP(hcp_id="H1", name="Dr A", segment="high")],
        reps=[Rep(rep_id="R1", name="Rep 1")],
        current_alignment=[],
        constraints=[Constraint(type="frequency_cap", scope="segment:high", value=4)],
        weights=ObjectiveWeights(),
        max_iterations=0,
    )

    assert result.assignments[0].estimated_annual_visits == 12
    assert result.territory_summary[0].workload_hours_weekly == 0.2


def test_coverage_is_complete_when_the_input_has_no_priority_hcps():
    result = solve(
        hcps=[HCP(hcp_id="H1", name="Dr A", segment="low")],
        reps=[Rep(rep_id="R1", name="Rep 1")],
        current_alignment=[],
        constraints=[],
        weights=ObjectiveWeights(),
        max_iterations=0,
    )

    assert result.objectives.raw.pct_priority_covered == 100
    assert result.objectives.coverage == 0


def test_objectives_are_normalized_against_the_current_alignment_baseline():
    from open_pharma_plugins_territory_alignment.models import CurrentAssignment

    result = solve(
        hcps=[HCP(hcp_id="H1", name="Dr A", lat=0, lng=1)],
        reps=[Rep(rep_id="R1", name="Rep 1", base_lat=0, base_lng=0)],
        current_alignment=[CurrentAssignment(hcp_id="H1", primary_rep="R1")],
        constraints=[],
        weights=ObjectiveWeights(),
        max_iterations=0,
    )

    assert result.objectives.raw.avg_travel_min > 120
    assert result.objectives.travel_efficiency == 0.5


def test_manual_overrides_are_excluded_from_the_disruption_denominator():
    from open_pharma_plugins_territory_alignment.models import CurrentAssignment, Override

    result = solve(
        hcps=[HCP(hcp_id="H1", name="Dr A"), HCP(hcp_id="H2", name="Dr B")],
        reps=[Rep(rep_id="R1", name="Rep 1"), Rep(rep_id="R2", name="Rep 2")],
        current_alignment=[
            CurrentAssignment(hcp_id="H1", primary_rep="R1"),
            CurrentAssignment(hcp_id="H2", primary_rep="R1"),
        ],
        constraints=[],
        weights=ObjectiveWeights(),
        overrides=[Override(hcp_id="H1", rep_id="R2", reason="manager decision")],
        max_iterations=0,
    )

    assert result.objectives.raw.pct_reassigned == 0


def test_two_opt_improve():
    from open_pharma_plugins_territory_alignment.geo import two_opt_improve

    points = [(0, 0), (1, 1), (2, 0), (3, 1)]
    route = [0, 2, 1, 3]
    improved, dist = two_opt_improve(points, route)
    assert len(improved) == 4
    assert dist > 0


def test_scenario_name_sanitization():
    from open_pharma_plugins_territory_alignment.data import _sanitize_scenario_name

    assert _sanitize_scenario_name("baseline") == "baseline"
    assert _sanitize_scenario_name("my-scenario_v2") == "my-scenario_v2"
    assert ".." not in _sanitize_scenario_name("../../etc/passwd")
    assert "/" not in _sanitize_scenario_name("path/traversal")


def test_scenario_files_are_private_in_configured_directory(tmp_path, monkeypatch):
    scenarios = tmp_path / "scenarios"
    monkeypatch.setenv("OPEN_PHARMA_TA_SCENARIOS_DIR", str(scenarios))
    result = json.loads(ta.get_handler("ta_align")({"scenario_name": "private", "max_iterations": 0})[0]["text"])
    assert "error" not in result
    path = scenarios / "private.json"

    assert path.is_relative_to(scenarios)
    assert path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "private.html").stat().st_mode & 0o777 == 0o600
    assert scenarios.stat().st_mode & 0o777 == 0o700


def test_scenario_csv_exports_neutralize_spreadsheet_formulas(tmp_path, monkeypatch):
    import csv

    data_dir = tmp_path / "formula-data"
    _write_minimal_data(data_dir, hcp_name="=2+2")
    (data_dir / "reps.csv").write_text(
        "rep_id,name,base_lat,base_lng\nR1,+cmd,1,1\n",
        encoding="utf-8",
    )
    scenarios = tmp_path / "scenarios"
    monkeypatch.setenv("OPEN_PHARMA_TA_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPEN_PHARMA_TA_SCENARIOS_DIR", str(scenarios))

    result = json.loads(ta.get_handler("ta_align")({"scenario_name": "formula_safe", "max_iterations": 0})[0]["text"])
    assert "error" not in result
    with (scenarios / "formula_safe_assignments.csv").open(newline="", encoding="utf-8") as handle:
        assignment = next(csv.DictReader(handle))
    with (scenarios / "formula_safe_territory_summary.csv").open(newline="", encoding="utf-8") as handle:
        territory = next(csv.DictReader(handle))

    assert assignment["hcp_name"] == "'=2+2"
    assert territory["rep_name"] == "'+cmd"


def test_scenario_save_cleans_up_every_partial_artifact_on_failure(tmp_path, monkeypatch):
    import open_pharma_plugins_territory_alignment.data as territory_data

    result = solve(
        hcps=[HCP(hcp_id="H1", name="Dr A")],
        reps=[Rep(rep_id="R1", name="Rep A")],
        current_alignment=[],
        constraints=[],
        weights=ObjectiveWeights(),
        max_iterations=0,
    ).model_dump(mode="json")
    scenarios = tmp_path / "scenarios"
    monkeypatch.setenv("OPEN_PHARMA_TA_SCENARIOS_DIR", str(scenarios))
    original_write = territory_data.exclusive_write_text
    call_count = 0

    def fail_on_fourth_write(path, content, *, encoding="utf-8"):
        nonlocal call_count
        call_count += 1
        if call_count == 4:
            raise OSError("simulated write failure")
        return original_write(path, content, encoding=encoding)

    monkeypatch.setattr(territory_data, "exclusive_write_text", fail_on_fourth_write)

    with pytest.raises(OSError, match="simulated write failure"):
        territory_data.save_scenario("partial", result)

    assert list(scenarios.iterdir()) == []
    assert not (tmp_path / "partial.html").exists()


def test_ta_evaluate_coverage_analysis():
    ta.get_handler("ta_align")({"scenario_name": "eval_coverage", "max_iterations": 5})
    result = ta.get_handler("ta_evaluate")({"scenario_name": "eval_coverage"})
    data = json.loads(result[0]["text"])
    coverage = data["coverage_analysis"]
    assert sum(seg["total"] for seg in coverage.values()) == 80
    assert all(seg["total"] >= seg["covered"] for seg in coverage.values())


def test_ta_evaluate_uses_the_saved_universe_after_runtime_data_changes(tmp_path, monkeypatch):
    ta.get_handler("ta_align")({"scenario_name": "snapshot_eval", "max_iterations": 0})

    data_dir = tmp_path / "replacement-data"
    data_dir.mkdir()
    (data_dir / "hcps.csv").write_text("hcp_id,name,segment\nOTHER,Other Doctor,low\n", encoding="utf-8")
    (data_dir / "reps.csv").write_text("rep_id,name\nOTHER_REP,Other Rep\n", encoding="utf-8")
    (data_dir / "current_alignment.csv").write_text(
        "hcp_id,primary_rep\nOTHER,OTHER_REP\n",
        encoding="utf-8",
    )
    (data_dir / "constraints.csv").write_text("type,scope,value\n", encoding="utf-8")
    monkeypatch.setenv("OPEN_PHARMA_TA_DATA_DIR", str(data_dir))
    load_all()

    evaluation = json.loads(ta.get_handler("ta_evaluate")({"scenario_name": "snapshot_eval"})[0]["text"])

    assert sum(segment["total"] for segment in evaluation["coverage_analysis"].values()) == 80


def test_ta_cluster_respects_available_days():
    ta.get_handler("ta_align")({"scenario_name": "zzz_days_test", "max_iterations": 5})
    result = ta.get_handler("ta_cluster")({"scenario_name": "zzz_days_test", "rep_id": "R004"})
    data = json.loads(result[0]["text"])
    for cluster in data["clusters"]:
        assert cluster["suggested_day"] in ["mon", "tue", "wed", "thu"]


def test_ta_visualize_defaults_to_existing_offline_primary_report():
    ta.get_handler("ta_align")({"scenario_name": "viz_single", "max_iterations": 5})
    result = ta.get_handler("ta_visualize")({"scenarios": ["viz_single"]})
    data = json.loads(result[0]["text"])
    assert data["success"] is True
    assert data["html_path"].endswith(".html")
    assert data["comparison"] is False
    assert data["basemap"] == "offline"
    assert data["network_access"] is False
    html = Path(data["html_path"]).read_text(encoding="utf-8")
    assert "https://" not in html
    assert "http://" not in html
    assert "Offline relative territory view" in html


def test_ta_visualize_public_mode_is_explicit_and_warns():
    ta.get_handler("ta_align")({"scenario_name": "viz_public", "max_iterations": 0})

    result = json.loads(ta.get_handler("ta_visualize")({"scenarios": ["viz_public"], "basemap": "public"})[0]["text"])

    assert result["success"] is True
    assert result["html_path"].endswith("viz_public_public.html")
    assert result["basemap"] == "public"
    assert result["network_access"] is True
    html = Path(result["html_path"]).read_text(encoding="utf-8")
    assert "Network map enabled" in html
    assert "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" in html
    assert "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" in html


def test_ta_visualize_comparison():
    ta.get_handler("ta_align")({"scenario_name": "viz_a", "max_iterations": 5})
    ta.get_handler("ta_align")({"scenario_name": "viz_b", "max_iterations": 5, "weight_workload": 0.50})
    result = ta.get_handler("ta_visualize")({"scenarios": ["viz_a", "viz_b"]})
    data = json.loads(result[0]["text"])
    assert data["success"] is True
    assert data["comparison"] is True
    assert data["basemap"] == "offline"
    assert data["network_access"] is False
    html = Path(data["html_path"]).read_text(encoding="utf-8")
    assert "viz_a vs viz_b" in html
    assert "Show movements" in html
    assert "https://" not in html


def test_ta_visualize_uses_saved_snapshot_after_runtime_data_changes(tmp_path, monkeypatch):
    scenarios_dir = tmp_path / "scenarios"
    original_data = tmp_path / "original"
    replacement_data = tmp_path / "replacement"
    _write_minimal_data(original_data, hcp_id="H1", hcp_name="Original Doctor")
    _write_minimal_data(replacement_data, hcp_id="H2", hcp_name="Replacement Doctor")
    monkeypatch.setenv("OPEN_PHARMA_TA_SCENARIOS_DIR", str(scenarios_dir))
    monkeypatch.setenv("OPEN_PHARMA_TA_DATA_DIR", str(original_data))
    ta.get_handler("ta_align")({"scenario_name": "snapshot_map", "max_iterations": 0})

    monkeypatch.setenv("OPEN_PHARMA_TA_DATA_DIR", str(replacement_data))
    load_all()
    result = json.loads(ta.get_handler("ta_visualize")({"scenarios": ["snapshot_map"]})[0]["text"])
    html = Path(result["html_path"]).read_text(encoding="utf-8")

    assert '"hcp_id": "H1"' in html
    assert '"hcp_id": "H2"' not in html


def test_ta_visualize_missing_scenario():
    result = ta.get_handler("ta_visualize")({"scenarios": ["nonexistent"]})
    data = json.loads(result[0]["text"])
    assert "error" in data


def test_full_strategic_workflow():
    """End-to-end: load → align → evaluate → compare."""
    load_all()
    hcps = get_hcps()
    reps = get_reps()
    alignment = get_current_alignment()
    constraints = get_constraints()
    weights = ObjectiveWeights()

    result = solve(
        hcps=hcps,
        reps=reps,
        current_alignment=alignment,
        constraints=constraints,
        weights=weights,
        scenario_name="integration",
        max_iterations=50,
    )

    assert len(result.assignments) + len(result.unassigned) == len(hcps)
    assert 0 <= result.objectives.composite <= 2.0
    assert len(result.territory_summary) > 0

    for ts in result.territory_summary:
        assert ts.hcp_count > 0
        assert ts.total_potential > 0

    assigned_reps = {a.primary_rep for a in result.assignments}
    for r in assigned_reps:
        assert any(ts.rep_id == r for ts in result.territory_summary)
