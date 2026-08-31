from __future__ import annotations

from pathlib import Path

import pytest

from open_pharma_plugins_territory_alignment.reporting import (
    build_report_model,
    convex_hull,
    render_report_html,
    scenario_artifact_paths,
)


def _scenario(name: str = "baseline") -> dict:
    artifacts = scenario_artifact_paths(Path("/private/territory/scenarios"), name)
    return {
        "scenario_name": name,
        "assignments": [
            {
                "hcp_id": "H1",
                "hcp_name": "Dr One",
                "primary_rep": "R1",
                "previous_rep": "R1",
                "is_changed": False,
                "change_reason": "",
                "estimated_travel_min": 12.5,
                "estimated_annual_visits": 24,
                "segment": "high",
                "tier": 1,
            },
            {
                "hcp_id": "H2",
                "hcp_name": "Dr Two",
                "primary_rep": "R1",
                "previous_rep": "R2",
                "is_changed": True,
                "change_reason": "workload balance",
                "estimated_travel_min": 18.0,
                "estimated_annual_visits": 12,
                "segment": "medium",
                "tier": 2,
            },
            {
                "hcp_id": "H3",
                "hcp_name": "Dr Three",
                "primary_rep": "R2",
                "previous_rep": "R2",
                "is_changed": False,
                "change_reason": "",
                "estimated_travel_min": 8.0,
                "estimated_annual_visits": 6,
                "segment": "low",
                "tier": 3,
            },
        ],
        "territory_summary": [
            {
                "rep_id": "R1",
                "rep_name": "Rep One",
                "hcp_count": 2,
                "total_potential": 300.0,
                "workload_hours_weekly": 32.0,
                "travel_hours_weekly": 7.5,
                "segment_high": 1,
                "segment_medium": 1,
                "segment_low": 0,
                "relationships_kept": 1,
                "relationships_new": 1,
            },
            {
                "rep_id": "R2",
                "rep_name": "Rep Two",
                "hcp_count": 1,
                "total_potential": 100.0,
                "workload_hours_weekly": 10.0,
                "travel_hours_weekly": 2.0,
                "segment_high": 0,
                "segment_medium": 0,
                "segment_low": 1,
                "relationships_kept": 1,
                "relationships_new": 0,
            },
        ],
        "objectives": {
            "workload_balance": 0.2,
            "travel_efficiency": 0.3,
            "disruption": 0.1,
            "coverage": 0.0,
            "composite": 0.17,
            "raw": {
                "workload_gini": 0.2,
                "avg_travel_min": 12.8,
                "max_travel_min": 18.0,
                "pct_reassigned": 33.3,
                "pct_priority_covered": 100.0,
            },
        },
        "unassigned": [],
        "weights_used": {
            "workload_balance": 0.3,
            "travel_efficiency": 0.25,
            "disruption": 0.25,
            "coverage": 0.2,
        },
        "input_snapshot": {
            "hcps": [
                {
                    "hcp_id": "H1",
                    "name": "Dr One",
                    "segment": "high",
                    "tier": 1,
                    "lat": 40.70,
                    "lng": -74.02,
                    "consent_visit": True,
                },
                {
                    "hcp_id": "H2",
                    "name": "Dr Two",
                    "segment": "medium",
                    "tier": 2,
                    "lat": 40.75,
                    "lng": -73.98,
                    "consent_visit": True,
                },
                {
                    "hcp_id": "H3",
                    "name": "Dr Three",
                    "segment": "low",
                    "tier": 3,
                    "lat": 40.80,
                    "lng": -73.92,
                    "consent_visit": False,
                },
            ],
            "reps": [
                {
                    "rep_id": "R1",
                    "name": "Rep One",
                    "base_lat": 40.72,
                    "base_lng": -74.00,
                    "max_weekly_hours": 40.0,
                },
                {
                    "rep_id": "R2",
                    "name": "Rep Two",
                    "base_lat": 40.79,
                    "base_lng": -73.94,
                    "max_weekly_hours": 40.0,
                },
            ],
            "current_alignment": [
                {"hcp_id": "H1", "primary_rep": "R1"},
                {"hcp_id": "H2", "primary_rep": "R2"},
                {"hcp_id": "H3", "primary_rep": "R2"},
            ],
            "constraints": [],
            "levers": {"vacancies": [], "new_hires": [], "overrides": [], "lock_reps": []},
        },
        "metadata": {
            "schema_version": "1.0",
            "plugin_version": "1.1.0",
            "run_id": "run-123",
            "created_at": "2026-08-30T10:00:00Z",
            "fixture_data": True,
            "input_fingerprints": {"hcps.csv": "abc123"},
            "artifacts": artifacts.as_metadata(),
        },
    }


@pytest.fixture
def report_model() -> dict:
    scenario = _scenario()
    return build_report_model(
        {"baseline": scenario},
        ["baseline"],
        show_movements=False,
        show_rep_bases=True,
    )


def test_scenario_artifact_paths_preserve_raw_contract(tmp_path):
    paths = scenario_artifact_paths(tmp_path / "scenarios", "baseline")

    assert paths.scenario_json == tmp_path / "scenarios" / "baseline.json"
    assert paths.assignments_csv.name == "baseline_assignments.csv"
    assert paths.territory_summary_csv.name == "baseline_territory_summary.csv"
    assert paths.primary_report == tmp_path / "baseline.html"
    assert paths.as_metadata() == {
        "primary_report": str(tmp_path / "baseline.html"),
        "advanced_exports": {
            "scenario_json": str(tmp_path / "scenarios" / "baseline.json"),
            "assignments_csv": str(tmp_path / "scenarios" / "baseline_assignments.csv"),
            "territory_summary_csv": str(tmp_path / "scenarios" / "baseline_territory_summary.csv"),
        },
    }


def test_convex_hull_discards_interior_points_and_is_deterministic():
    points = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.5, 0.5)]

    assert convex_hull(points) == [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


def test_report_model_contains_decision_sections_and_boundaries(report_model):
    assert set(report_model) >= {
        "scenario_layers",
        "rep_bases",
        "territories",
        "movements",
        "kpis",
        "territory_rows",
        "review_items",
        "advanced_exports",
        "provenance",
    }
    assert report_model["territories"]["baseline"]
    assert report_model["rep_bases"]
    assert report_model["kpis"]["baseline"]["assigned_hcps"] == 3
    assert any(item["kind"] == "consent" for item in report_model["review_items"]["baseline"])


def test_offline_report_is_consolidated_accessible_and_network_free(report_model):
    html = render_report_html(report_model, ["baseline"], basemap="offline")

    assert "https://" not in html and "http://" not in html
    assert 'src="//' not in html and 'href="//' not in html
    for marker in (
        'id="territory-map"',
        'id="rep-filter"',
        'id="segment-filter"',
        'id="zoom-in"',
        'id="zoom-out"',
        'id="reset-view"',
        'id="workload-chart"',
        'id="objective-chart"',
        'id="review-queue"',
        'id="advanced-exports"',
    ):
        assert marker in html
    assert 'aria-label="Zoom in"' in html
    assert "@media (prefers-reduced-motion: reduce)" in html
    assert "h1 { overflow-wrap:anywhere;" in html
    assert '<link rel="icon" href="data:,' in html
    assert "Offline relative territory view" in html
    assert "Advanced exports" in html


def test_report_escapes_markup_and_script_terminators(report_model):
    hostile = "</script><img src=x onerror=alert(1)>"
    report_model["scenario_layers"]["baseline"]["markers"][0]["hcp_name"] = hostile

    html = render_report_html(report_model, ["baseline"], basemap="offline")

    assert hostile not in html
    assert "</script><img" not in html
    assert "\\u003c/script\\u003e" in html
    assert "function escapeHtml" in html


def test_public_report_includes_pinned_assets_and_persistent_warning(report_model):
    html = render_report_html(report_model, ["baseline"], basemap="public")

    assert "Network map enabled" in html
    assert "may disclose map extent or" in html
    assert "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" in html
    assert "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" in html
    assert "https://{s}.basemaps.cartocdn.com" in html


def test_report_model_builds_movement_between_scenarios():
    baseline = _scenario("baseline")
    alternative = _scenario("alternative")
    alternative["assignments"][0]["primary_rep"] = "R2"

    model = build_report_model(
        {"baseline": baseline, "alternative": alternative},
        ["baseline", "alternative"],
        show_movements=True,
        show_rep_bases=True,
    )

    assert model["movements"] == [
        {
            "hcp_id": "H1",
            "hcp_name": "Dr One",
            "x": pytest.approx(model["scenario_layers"]["baseline"]["markers"][0]["x"]),
            "y": pytest.approx(model["scenario_layers"]["baseline"]["markers"][0]["y"]),
            "old_rep_id": "R1",
            "new_rep_id": "R2",
        }
    ]
