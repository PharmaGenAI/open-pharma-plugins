"""Presentation model and self-contained visual reports for territory scenarios."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Literal

from shared.filesystem import json_for_html_script

_VIEWBOX_WIDTH = 1000.0
_VIEWBOX_HEIGHT = 620.0
_VIEWBOX_PADDING = 64.0

REP_COLORS = [
    "#2563eb",
    "#059669",
    "#d97706",
    "#7c3aed",
    "#dc2626",
    "#0891b2",
    "#92400e",
    "#475569",
    "#db2777",
    "#65a30d",
    "#ea580c",
    "#4f46e5",
    "#0f766e",
    "#ca8a04",
    "#6d28d9",
    "#4d7c0f",
]


@dataclass(frozen=True)
class ScenarioArtifactPaths:
    """Stable paths for one scenario's primary and advanced artifacts."""

    primary_report: Path
    scenario_json: Path
    assignments_csv: Path
    territory_summary_csv: Path

    def as_metadata(self) -> dict[str, Any]:
        return {
            "primary_report": str(self.primary_report),
            "advanced_exports": {
                "scenario_json": str(self.scenario_json),
                "assignments_csv": str(self.assignments_csv),
                "territory_summary_csv": str(self.territory_summary_csv),
            },
        }


def scenario_artifact_paths(scenarios_dir: Path, safe_name: str) -> ScenarioArtifactPaths:
    """Return paths without creating directories or files."""
    return ScenarioArtifactPaths(
        primary_report=scenarios_dir.parent / f"{safe_name}.html",
        scenario_json=scenarios_dir / f"{safe_name}.json",
        assignments_csv=scenarios_dir / f"{safe_name}_assignments.csv",
        territory_summary_csv=scenarios_dir / f"{safe_name}_territory_summary.csv",
    )


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Return a deterministic monotonic-chain convex hull."""
    unique = sorted(set(points))
    if len(unique) <= 1:
        return unique

    def cross(
        origin: tuple[float, float],
        first: tuple[float, float],
        second: tuple[float, float],
    ) -> float:
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (second[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _coordinates(scenarios: dict[str, dict[str, Any]]) -> list[tuple[float, float]]:
    coordinates: list[tuple[float, float]] = []
    for scenario in scenarios.values():
        snapshot = scenario.get("input_snapshot", {})
        for hcp in snapshot.get("hcps", []):
            if hcp.get("lat") is not None and hcp.get("lng") is not None:
                coordinates.append((float(hcp["lat"]), float(hcp["lng"])))
        for rep in snapshot.get("reps", []):
            lat = rep.get("base_lat")
            lng = rep.get("base_lng")
            if lat is not None and lng is not None and (float(lat) != 0.0 or float(lng) != 0.0):
                coordinates.append((float(lat), float(lng)))
    return coordinates


def _projector(coordinates: list[tuple[float, float]]):
    if not coordinates:
        return lambda _lat, _lng: (_VIEWBOX_WIDTH / 2, _VIEWBOX_HEIGHT / 2)

    latitudes = [point[0] for point in coordinates]
    longitudes = [point[1] for point in coordinates]
    mean_latitude = sum(latitudes) / len(latitudes)
    longitude_factor = max(math.cos(math.radians(mean_latitude)), 0.15)
    projected_x = [value * longitude_factor for value in longitudes]
    min_x, max_x = min(projected_x), max(projected_x)
    min_lat, max_lat = min(latitudes), max(latitudes)
    x_span = max(max_x - min_x, 1e-9)
    y_span = max(max_lat - min_lat, 1e-9)
    available_width = _VIEWBOX_WIDTH - 2 * _VIEWBOX_PADDING
    available_height = _VIEWBOX_HEIGHT - 2 * _VIEWBOX_PADDING
    scale = min(available_width / x_span, available_height / y_span)
    content_width = x_span * scale
    content_height = y_span * scale
    x_offset = (_VIEWBOX_WIDTH - content_width) / 2
    y_offset = (_VIEWBOX_HEIGHT - content_height) / 2

    def project(lat: float, lng: float) -> tuple[float, float]:
        x = x_offset + (lng * longitude_factor - min_x) * scale
        y = y_offset + (max_lat - lat) * scale
        return round(x, 2), round(y, 2)

    return project


def _artifact_links(scenario: dict[str, Any]) -> dict[str, dict[str, str]]:
    metadata = scenario.get("metadata", {})
    artifacts = metadata.get("artifacts", {})
    advanced = artifacts.get("advanced_exports", {})
    result: dict[str, dict[str, str]] = {}
    for key, raw_path in advanced.items():
        path = Path(raw_path)
        result[key] = {
            "name": path.name,
            "path": str(path),
            "href": f"scenarios/{path.name}",
        }
    return result


def _territory_envelope(markers: list[dict[str, Any]]) -> dict[str, Any]:
    lookup = {(float(marker["x"]), float(marker["y"])): marker for marker in markers}
    hull = convex_hull(list(lookup))
    points = [
        {
            "x": point[0],
            "y": point[1],
            "lat": lookup[point]["lat"],
            "lng": lookup[point]["lng"],
        }
        for point in hull
    ]
    kind = "polygon" if len(points) >= 3 else "line" if len(points) == 2 else "point"
    return {"kind": kind, "points": points}


def build_report_model(
    scenarios: dict[str, dict[str, Any]],
    names: list[str],
    *,
    show_movements: bool,
    show_rep_bases: bool,
) -> dict[str, Any]:
    """Build a JSON-serializable presentation model from saved snapshots."""
    if not names or any(name not in scenarios for name in names):
        raise ValueError("all report scenario names must exist in scenarios")

    project = _projector(_coordinates(scenarios))
    first_snapshot = scenarios[names[0]].get("input_snapshot", {})
    hcp_map = {item["hcp_id"]: item for item in first_snapshot.get("hcps", [])}
    reps_by_id: dict[str, dict[str, Any]] = {}
    for name in names:
        for rep in scenarios[name].get("input_snapshot", {}).get("reps", []):
            reps_by_id[rep["rep_id"]] = rep

    assigned_rep_ids = {
        assignment["primary_rep"] for name in names for assignment in scenarios[name].get("assignments", [])
    }
    all_rep_ids = sorted(assigned_rep_ids | set(reps_by_id))
    color_map = {rep_id: REP_COLORS[index % len(REP_COLORS)] for index, rep_id in enumerate(all_rep_ids)}

    scenario_layers: dict[str, Any] = {}
    territories: dict[str, list[dict[str, Any]]] = {}
    kpis: dict[str, dict[str, Any]] = {}
    territory_rows: dict[str, list[dict[str, Any]]] = {}
    review_items: dict[str, list[dict[str, str]]] = {}
    advanced_exports: dict[str, dict[str, dict[str, str]]] = {}
    provenance: dict[str, dict[str, Any]] = {}

    for name in names:
        scenario = scenarios[name]
        snapshot = scenario.get("input_snapshot", {})
        scenario_hcp_map = {item["hcp_id"]: item for item in snapshot.get("hcps", [])}
        markers: list[dict[str, Any]] = []
        by_rep: dict[str, list[dict[str, Any]]] = {}
        missing_coordinates: list[str] = []
        for assignment in scenario.get("assignments", []):
            hcp = scenario_hcp_map.get(assignment["hcp_id"], {})
            lat, lng = hcp.get("lat"), hcp.get("lng")
            if lat is None or lng is None:
                missing_coordinates.append(assignment["hcp_id"])
                continue
            x, y = project(float(lat), float(lng))
            marker = {
                "x": x,
                "y": y,
                "lat": float(lat),
                "lng": float(lng),
                "hcp_id": assignment["hcp_id"],
                "hcp_name": assignment.get("hcp_name", hcp.get("name", "")),
                "rep_id": assignment["primary_rep"],
                "rep_name": reps_by_id.get(assignment["primary_rep"], {}).get("name", ""),
                "color": color_map.get(assignment["primary_rep"], "#64748b"),
                "segment": assignment.get("segment", hcp.get("segment", "medium")),
                "tier": assignment.get("tier", hcp.get("tier", 0)),
                "travel_min": assignment.get("estimated_travel_min", 0),
                "is_changed": assignment.get("is_changed", False),
                "previous_rep": assignment.get("previous_rep", ""),
                "change_reason": assignment.get("change_reason", ""),
            }
            markers.append(marker)
            by_rep.setdefault(marker["rep_id"], []).append(marker)

        unassigned_markers: list[dict[str, Any]] = []
        for item in scenario.get("unassigned", []):
            hcp = scenario_hcp_map.get(item["hcp_id"], {})
            lat, lng = hcp.get("lat"), hcp.get("lng")
            if lat is None or lng is None:
                continue
            x, y = project(float(lat), float(lng))
            unassigned_markers.append(
                {
                    "x": x,
                    "y": y,
                    "lat": float(lat),
                    "lng": float(lng),
                    "hcp_id": item["hcp_id"],
                    "hcp_name": hcp.get("name", ""),
                    "reason": item.get("reason", ""),
                }
            )

        scenario_layers[name] = {
            "markers": markers,
            "unassigned": unassigned_markers,
            "objectives": scenario.get("objectives", {}),
            "assignment_count": len(scenario.get("assignments", [])),
        }
        territories[name] = [
            {
                "rep_id": rep_id,
                "rep_name": reps_by_id.get(rep_id, {}).get("name", ""),
                "color": color_map.get(rep_id, "#64748b"),
                **_territory_envelope(rep_markers),
            }
            for rep_id, rep_markers in sorted(by_rep.items())
        ]

        raw = scenario.get("objectives", {}).get("raw", {})
        kpis[name] = {
            "assigned_hcps": len(scenario.get("assignments", [])),
            "unassigned_hcps": len(scenario.get("unassigned", [])),
            "reassigned_pct": raw.get("pct_reassigned", 0),
            "workload_gini": raw.get("workload_gini", 0),
            "avg_travel_min": raw.get("avg_travel_min", 0),
            "priority_coverage_pct": raw.get("pct_priority_covered", 0),
            "composite": scenario.get("objectives", {}).get("composite", 0),
        }

        scenario_reps = {rep["rep_id"]: rep for rep in snapshot.get("reps", [])}
        rows: list[dict[str, Any]] = []
        for territory in scenario.get("territory_summary", []):
            capacity = float(scenario_reps.get(territory["rep_id"], {}).get("max_weekly_hours", 40.0))
            workload = float(territory.get("workload_hours_weekly", 0))
            rows.append(
                {
                    **territory,
                    "capacity_hours": capacity,
                    "utilization_pct": round(workload / capacity * 100, 1) if capacity else 0,
                    "over_capacity": workload > capacity,
                    "color": color_map.get(territory["rep_id"], "#64748b"),
                }
            )
        territory_rows[name] = rows

        items: list[dict[str, str]] = []
        for item in scenario.get("unassigned", []):
            items.append(
                {
                    "kind": "unassigned",
                    "severity": "high",
                    "label": f"{item['hcp_id']} is unassigned: {item.get('reason', 'review required')}",
                }
            )
        if missing_coordinates:
            items.append(
                {
                    "kind": "coordinates",
                    "severity": "medium",
                    "label": f"{len(missing_coordinates)} assigned HCP(s) are not shown because coordinates are missing.",
                }
            )
        no_visit_consent = [hcp["hcp_id"] for hcp in snapshot.get("hcps", []) if hcp.get("consent_visit") is False]
        if no_visit_consent:
            items.append(
                {
                    "kind": "consent",
                    "severity": "medium",
                    "label": f"{len(no_visit_consent)} HCP(s) lack visit consent; confirm channel eligibility before execution.",
                }
            )
        for row in rows:
            if row["over_capacity"]:
                items.append(
                    {
                        "kind": "capacity",
                        "severity": "high",
                        "label": f"{row['rep_id']} exceeds weekly capacity ({row['workload_hours_weekly']:.1f}h / {row['capacity_hours']:.1f}h).",
                    }
                )
        if not items:
            items.append(
                {
                    "kind": "review",
                    "severity": "low",
                    "label": "No automated exception was detected; manager and operational review is still required.",
                }
            )
        review_items[name] = items
        advanced_exports[name] = _artifact_links(scenario)
        metadata = scenario.get("metadata", {})
        provenance[name] = {
            "run_id": metadata.get("run_id", ""),
            "plugin_version": metadata.get("plugin_version", ""),
            "created_at": metadata.get("created_at", ""),
            "fixture_data": metadata.get("fixture_data", False),
            "schema_version": metadata.get("schema_version", ""),
            "input_fingerprints": metadata.get("input_fingerprints", {}),
        }

    rep_bases: list[dict[str, Any]] = []
    if show_rep_bases:
        for rep_id, rep in sorted(reps_by_id.items()):
            lat, lng = rep.get("base_lat"), rep.get("base_lng")
            if lat is None or lng is None or (float(lat) == 0.0 and float(lng) == 0.0):
                continue
            x, y = project(float(lat), float(lng))
            rep_bases.append(
                {
                    "x": x,
                    "y": y,
                    "lat": float(lat),
                    "lng": float(lng),
                    "rep_id": rep_id,
                    "rep_name": rep.get("name", ""),
                    "color": color_map.get(rep_id, "#64748b"),
                }
            )

    movements: list[dict[str, Any]] = []
    if show_movements and len(names) == 2:
        before = {item["hcp_id"]: item["primary_rep"] for item in scenarios[names[0]].get("assignments", [])}
        after = {item["hcp_id"]: item["primary_rep"] for item in scenarios[names[1]].get("assignments", [])}
        marker_map = {item["hcp_id"]: item for item in scenario_layers[names[0]]["markers"]}
        for hcp_id in sorted(set(before) & set(after)):
            if before[hcp_id] == after[hcp_id] or hcp_id not in marker_map:
                continue
            marker = marker_map[hcp_id]
            movements.append(
                {
                    "hcp_id": hcp_id,
                    "hcp_name": hcp_map.get(hcp_id, {}).get("name", marker.get("hcp_name", "")),
                    "x": marker["x"],
                    "y": marker["y"],
                    "old_rep_id": before[hcp_id],
                    "new_rep_id": after[hcp_id],
                }
            )

    return {
        "names": names,
        "scenario_layers": scenario_layers,
        "rep_bases": rep_bases,
        "territories": territories,
        "movements": movements,
        "kpis": kpis,
        "territory_rows": territory_rows,
        "review_items": review_items,
        "advanced_exports": advanced_exports,
        "provenance": provenance,
        "color_map": color_map,
        "viewbox": {"width": _VIEWBOX_WIDTH, "height": _VIEWBOX_HEIGHT},
    }


def render_report_html(
    model: dict[str, Any],
    names: list[str],
    *,
    basemap: Literal["offline", "public"] = "offline",
) -> str:
    """Render a consolidated report without consulting mutable runtime state."""
    if basemap not in {"offline", "public"}:
        raise ValueError("basemap must be 'offline' or 'public'")
    if not names:
        raise ValueError("at least one scenario is required")

    title = " vs ".join(names)
    public_assets = ""
    public_script = ""
    network_warning = ""
    map_markup = (
        '<svg id="territory-map" role="img" aria-label="Interactive relative territory map" '
        'viewBox="0 0 1000 620" tabindex="0"><g id="map-viewport"></g></svg>'
    )
    if basemap == "public":
        public_assets = """
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
  integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
"""
        network_warning = """
<div class="network-warning" role="alert"><strong>Network map enabled.</strong>
Opening this report requests public CARTO/OpenStreetMap resources and may disclose map extent or
coordinates to those providers.</div>
"""
        map_markup = '<div id="territory-map" role="img" aria-label="Interactive public territory map"></div>'
        public_script = """
function clearPublicLayers() { publicLayers.forEach(function(layer){ publicMap.removeLayer(layer); }); publicLayers=[]; }
function renderPublicMap() {
  if (!publicMap) {
    publicMap = L.map("territory-map");
    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png", {attribution:"&copy; OpenStreetMap &copy; CARTO",maxZoom:18}).addTo(publicMap);
  }
  clearPublicLayers();
  const points=[];
  (REPORT_DATA.territories[currentScenario] || []).forEach(function(territory){
    if (selectedRep()!=="all" && territory.rep_id!==selectedRep()) return;
    if (territory.points.length >= 3) { const layer=L.polygon(territory.points.map(function(p){return [p.lat,p.lng];}),{color:territory.color,fillOpacity:.1,dashArray:"8 6"}).addTo(publicMap); publicLayers.push(layer); }
  });
  const layer=REPORT_DATA.scenario_layers[currentScenario];
  layer.markers.filter(markerVisible).forEach(function(marker){ const circle=L.circleMarker([marker.lat,marker.lng],{radius:marker.segment==="high"?9:marker.segment==="medium"?7:5,color:marker.is_changed?"#111827":"#fff",weight:marker.is_changed?3:2,fillColor:marker.color,fillOpacity:.9}).bindPopup(markerTooltip(marker)).addTo(publicMap); publicLayers.push(circle); points.push([marker.lat,marker.lng]); });
  (REPORT_DATA.rep_bases||[]).forEach(function(rep){ if(selectedRep()!=="all"&&rep.rep_id!==selectedRep())return; const marker=L.circleMarker([rep.lat,rep.lng],{radius:10,color:"#111827",weight:2,fillColor:rep.color,fillOpacity:1}).bindPopup("<strong>Rep base</strong><br>"+escapeHtml(rep.rep_id)+" · "+escapeHtml(rep.rep_name)).addTo(publicMap); publicLayers.push(marker); points.push([rep.lat,rep.lng]); });
  if(points.length) publicMap.fitBounds(points,{padding:[32,32],maxZoom:13});
}
"""

    tokens = {
        "TITLE": escape(title),
        "PUBLIC_ASSETS": public_assets,
        "PUBLIC_SCRIPT": public_script,
        "NETWORK_WARNING": network_warning,
        "MAP_MARKUP": map_markup,
        "REPORT_DATA": json_for_html_script(model),
        "SCENARIO_NAMES": json_for_html_script(names),
        "BASEMAP": json_for_html_script(basemap),
    }
    return re.sub(r"@@([A-Z_]+)@@", lambda match: tokens[match.group(1)], _REPORT_TEMPLATE)


_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>Territory Alignment — @@TITLE@@</title>
@@PUBLIC_ASSETS@@
<style>
:root { --ink:#172033; --muted:#65738b; --line:#dbe3ed; --surface:#fff; --canvas:#f4f7fb; --navy:#102a43; --blue:#2563eb; --good:#047857; --warn:#b45309; --bad:#b91c1c; }
* { box-sizing:border-box; }
body { margin:0; color:var(--ink); background:var(--canvas); font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; line-height:1.45; }
button,select,input { font:inherit; }
button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible,#territory-map:focus-visible { outline:3px solid #93c5fd; outline-offset:2px; }
.shell { max-width:1480px; margin:0 auto; padding:28px; }
.hero { display:flex; justify-content:space-between; gap:24px; align-items:flex-start; padding:28px 30px; color:#fff; border-radius:22px; background:linear-gradient(135deg,#102a43,#1d4ed8); box-shadow:0 18px 48px rgba(15,42,67,.18); }
.eyebrow { margin:0 0 6px; text-transform:uppercase; letter-spacing:.12em; font-size:.72rem; font-weight:800; opacity:.78; }
h1 { overflow-wrap:anywhere; margin:0; font-size:clamp(1.65rem,3vw,2.55rem); line-height:1.12; }
.hero-note { max-width:560px; margin:10px 0 0; opacity:.86; }
.badges { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:8px; }
.badge { padding:7px 11px; border:1px solid rgba(255,255,255,.3); border-radius:999px; background:rgba(255,255,255,.12); font-size:.78rem; font-weight:750; white-space:nowrap; }
.network-warning { margin-top:16px; padding:13px 16px; border:1px solid #fdba74; border-radius:12px; background:#fff7ed; color:#9a3412; }
.kpi-grid { display:grid; grid-template-columns:repeat(7,minmax(132px,1fr)); gap:12px; margin:18px 0; }
.kpi { min-height:106px; padding:16px; border:1px solid var(--line); border-radius:16px; background:var(--surface); box-shadow:0 4px 16px rgba(15,42,67,.05); }
.kpi-label { color:var(--muted); font-size:.74rem; font-weight:800; text-transform:uppercase; letter-spacing:.06em; }
.kpi-value { display:block; margin-top:8px; font-size:1.62rem; font-weight:850; color:var(--navy); }
.kpi-direction { display:block; margin-top:3px; color:var(--muted); font-size:.72rem; }
.grid { display:grid; grid-template-columns:minmax(0,1.75fr) minmax(320px,.75fr); gap:18px; }
.card { border:1px solid var(--line); border-radius:18px; background:var(--surface); box-shadow:0 4px 18px rgba(15,42,67,.055); overflow:hidden; }
.card-head { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:17px 20px; border-bottom:1px solid var(--line); }
.card-head h2,.section-title { margin:0; color:var(--navy); font-size:1.03rem; }
.map-card { min-height:720px; }
.controls { display:flex; flex-wrap:wrap; gap:8px; align-items:center; padding:12px 16px; border-bottom:1px solid var(--line); background:#f8fafc; }
.control { display:flex; align-items:center; gap:6px; color:var(--muted); font-size:.78rem; font-weight:700; }
.control select { max-width:180px; padding:7px 28px 7px 9px; border:1px solid #cbd5e1; border-radius:9px; color:var(--ink); background:#fff; }
.icon-btn { min-width:35px; height:35px; border:1px solid #cbd5e1; border-radius:9px; background:#fff; color:var(--navy); font-weight:850; cursor:pointer; }
.map-wrap { position:relative; height:590px; overflow:hidden; background:radial-gradient(circle at 20% 20%,#f8fbff,#e8eef7); }
#territory-map { width:100%; height:100%; display:block; touch-action:none; }
.map-note { position:absolute; left:14px; bottom:14px; z-index:500; max-width:360px; padding:8px 11px; border:1px solid rgba(148,163,184,.5); border-radius:10px; background:rgba(255,255,255,.93); color:var(--muted); font-size:.72rem; }
.tooltip { position:absolute; z-index:1000; display:none; max-width:280px; padding:10px 12px; border-radius:10px; background:#0f172a; color:#fff; font-size:.76rem; pointer-events:none; box-shadow:0 8px 30px rgba(15,23,42,.28); }
.legend { display:flex; flex-wrap:wrap; gap:8px 14px; padding:12px 18px; border-top:1px solid var(--line); }
.legend-item { display:inline-flex; gap:6px; align-items:center; font-size:.76rem; color:var(--muted); }
.legend-dot { width:11px; height:11px; border-radius:50%; border:1px solid rgba(15,23,42,.3); }
.side-stack { display:grid; gap:18px; align-content:start; }
.chart { padding:16px 18px 19px; }
.bar-row { display:grid; grid-template-columns:72px minmax(0,1fr) 62px; gap:9px; align-items:center; margin:11px 0; font-size:.76rem; }
.bar-track { position:relative; height:12px; border-radius:99px; background:#e8eef5; overflow:hidden; }
.bar-fill { height:100%; border-radius:inherit; }
.bar-value { color:var(--muted); text-align:right; font-variant-numeric:tabular-nums; }
.review-list { list-style:none; margin:0; padding:12px 18px 18px; display:grid; gap:9px; }
.review-item { padding:11px 12px; border-left:4px solid #94a3b8; border-radius:8px; background:#f8fafc; font-size:.8rem; }
.review-item.high { border-color:var(--bad); background:#fef2f2; }
.review-item.medium { border-color:var(--warn); background:#fffbeb; }
.review-item.low { border-color:var(--good); background:#ecfdf5; }
.lower-grid { display:grid; grid-template-columns:1.15fr .85fr; gap:18px; margin-top:18px; }
.table-wrap { overflow:auto; max-height:460px; }
table { width:100%; border-collapse:collapse; font-size:.78rem; }
th,td { padding:10px 12px; border-bottom:1px solid #e7edf4; text-align:left; white-space:nowrap; }
th { position:sticky; top:0; z-index:1; background:#f8fafc; color:var(--muted); font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; }
.status-pill { display:inline-flex; padding:3px 7px; border-radius:999px; background:#e2e8f0; font-size:.68rem; font-weight:800; }
.status-pill.changed { background:#fef3c7; color:#92400e; }
.advanced { margin-top:18px; }
.advanced summary { cursor:pointer; padding:18px 20px; color:var(--navy); font-weight:850; }
.advanced-body { padding:0 20px 20px; color:var(--muted); font-size:.82rem; }
.export-list { display:grid; gap:8px; padding-left:18px; }
.export-list a { color:var(--blue); font-weight:700; }
.fingerprints { overflow-wrap:anywhere; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.7rem; }
.footer { margin:18px 2px 0; color:var(--muted); font-size:.75rem; text-align:center; }
@media (max-width:1100px) { .kpi-grid{grid-template-columns:repeat(4,1fr)} .grid,.lower-grid{grid-template-columns:1fr} .map-card{min-height:650px} }
@media (max-width:650px) { .shell{padding:12px} .hero{padding:22px;display:block;border-radius:16px} .badges{justify-content:flex-start;margin-top:16px} .kpi-grid{grid-template-columns:repeat(2,1fr)} .map-wrap{height:480px} .controls{align-items:flex-end} .control{display:grid} }
@media (prefers-reduced-motion: reduce) { *,*::before,*::after { scroll-behavior:auto!important; transition:none!important; animation:none!important; } }
@media print { body{background:#fff} .shell{max-width:none;padding:0} .hero,.card,.kpi{box-shadow:none} .controls{display:none} .map-wrap{height:520px} }
</style>
</head>
<body>
<main class="shell">
  <header class="hero">
    <div>
      <p class="eyebrow">Territory alignment decision report</p>
      <h1>@@TITLE@@</h1>
      <p class="hero-note">A consolidated planning view of assignment coverage, workload, travel, change, and geographic shape. Confirm manager overrides, consent, employment rules, and operational feasibility before use.</p>
    </div>
    <div class="badges" id="provenance-badges" aria-label="Report provenance"></div>
  </header>
  @@NETWORK_WARNING@@
  <section class="kpi-grid" id="kpi-grid" aria-label="Executive metrics"></section>
  <section class="grid">
    <article class="card map-card">
      <div class="card-head"><h2>Territory view</h2><span class="status-pill" id="map-mode"></span></div>
      <div class="controls" aria-label="Map controls">
        <label class="control">Scenario <select id="scenario-filter"></select></label>
        <label class="control">Representative <select id="rep-filter"><option value="all">All representatives</option></select></label>
        <label class="control">Segment <select id="segment-filter"><option value="all">All segments</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label>
        <label class="control"><input id="movement-filter" type="checkbox" checked> Show movements</label>
        <button class="icon-btn" id="zoom-in" type="button" aria-label="Zoom in">+</button>
        <button class="icon-btn" id="zoom-out" type="button" aria-label="Zoom out">−</button>
        <button class="icon-btn" id="reset-view" type="button" aria-label="Reset map view">↺</button>
      </div>
      <div class="map-wrap" id="map-wrap">
        @@MAP_MARKUP@@
        <div class="tooltip" id="map-tooltip" role="status"></div>
        <div class="map-note" id="map-note"></div>
      </div>
      <div class="legend" id="map-legend" aria-label="Representative legend"></div>
    </article>
    <aside class="side-stack">
      <section class="card"><div class="card-head"><h2>Weekly workload</h2></div><div class="chart" id="workload-chart"></div></section>
      <section class="card"><div class="card-head"><h2>Optimization trade-offs</h2></div><div class="chart" id="objective-chart"></div></section>
      <section class="card"><div class="card-head"><h2>Review queue</h2></div><ul class="review-list" id="review-queue"></ul></section>
    </aside>
  </section>
  <section class="lower-grid">
    <article class="card"><div class="card-head"><h2>Territory summary</h2></div><div class="table-wrap"><table><thead><tr><th>Rep</th><th>HCPs</th><th>Workload</th><th>Capacity</th><th>Travel</th><th>High / Med / Low</th></tr></thead><tbody id="territory-table"></tbody></table></div></article>
    <article class="card"><div class="card-head"><h2>Changed assignments</h2><input id="change-search" type="search" aria-label="Search changed assignments" placeholder="Search HCP or rep"></div><div class="table-wrap"><table><thead><tr><th>HCP</th><th>Previous</th><th>New</th><th>Reason</th></tr></thead><tbody id="change-table"></tbody></table></div></article>
  </section>
  <details class="card advanced" id="advanced-exports"><summary>Advanced exports and provenance</summary><div class="advanced-body"><p>The HTML report is the primary human-facing artifact. These unchanged raw files support audit and downstream processing.</p><ul class="export-list" id="export-list"></ul><div class="fingerprints" id="fingerprints"></div></div></details>
  <p class="footer">Generated locally from the immutable saved scenario snapshot. This report is a planning aid, not an approved field deployment.</p>
</main>
<script>
const REPORT_DATA = @@REPORT_DATA@@;
const SCENARIO_NAMES = @@SCENARIO_NAMES@@;
const BASEMAP = @@BASEMAP@@;
let currentScenario = SCENARIO_NAMES[0];
let zoom = 1;
let panX = 0;
let panY = 0;
let dragging = false;
let lastPointer = null;
let publicMap = null;
let publicLayers = [];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, function(character) {
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character];
  });
}
function byId(id) { return document.getElementById(id); }
function svgElement(name, attributes) {
  const element = document.createElementNS("http:" + "//www.w3.org/2000/svg", name);
  Object.entries(attributes || {}).forEach(function(entry) { element.setAttribute(entry[0], String(entry[1])); });
  return element;
}
function selectedRep() { return byId("rep-filter").value; }
function selectedSegment() { return byId("segment-filter").value; }
function markerVisible(marker) {
  return (selectedRep() === "all" || marker.rep_id === selectedRep()) && (selectedSegment() === "all" || marker.segment === selectedSegment());
}
function showTooltip(event, html) {
  const tooltip = byId("map-tooltip");
  tooltip.innerHTML = html;
  tooltip.style.display = "block";
  const wrap = byId("map-wrap").getBoundingClientRect();
  tooltip.style.left = Math.min(event.clientX - wrap.left + 12, wrap.width - 292) + "px";
  tooltip.style.top = Math.max(event.clientY - wrap.top - 12, 8) + "px";
}
function hideTooltip() { byId("map-tooltip").style.display = "none"; }
function markerTooltip(marker) {
  return "<strong>" + escapeHtml(marker.hcp_name) + "</strong><br>" + escapeHtml(marker.hcp_id) + " · " + escapeHtml(marker.segment) + "<br>Rep " + escapeHtml(marker.rep_id) + " · " + Number(marker.travel_min || 0).toFixed(1) + " min" + (marker.is_changed ? "<br>Changed from " + escapeHtml(marker.previous_rep) : "");
}
function applyTransform() {
  const viewport = byId("map-viewport");
  if (viewport) viewport.setAttribute("transform", "translate(" + panX + " " + panY + ") scale(" + zoom + ")");
}
function renderOfflineMap() {
  const viewport = byId("map-viewport");
  viewport.replaceChildren();
  const territories = REPORT_DATA.territories[currentScenario] || [];
  territories.forEach(function(territory) {
    if (selectedRep() !== "all" && territory.rep_id !== selectedRep()) return;
    if (territory.kind === "polygon") {
      const polygon = svgElement("polygon", {points:territory.points.map(function(p){return p.x+","+p.y;}).join(" "), fill:territory.color, "fill-opacity":"0.1", stroke:territory.color, "stroke-width":"3", "stroke-dasharray":"9 6", "vector-effect":"non-scaling-stroke"});
      viewport.appendChild(polygon);
    } else if (territory.kind === "line") {
      viewport.appendChild(svgElement("line", {x1:territory.points[0].x,y1:territory.points[0].y,x2:territory.points[1].x,y2:territory.points[1].y,stroke:territory.color,"stroke-width":"10","stroke-opacity":".25","stroke-linecap":"round","vector-effect":"non-scaling-stroke"}));
    } else if (territory.points.length) {
      viewport.appendChild(svgElement("circle", {cx:territory.points[0].x,cy:territory.points[0].y,r:"24",fill:territory.color,"fill-opacity":".1",stroke:territory.color,"stroke-width":"2","stroke-dasharray":"6 5","vector-effect":"non-scaling-stroke"}));
    }
  });
  (REPORT_DATA.rep_bases || []).forEach(function(rep) {
    if (selectedRep() !== "all" && rep.rep_id !== selectedRep()) return;
    const diamond = svgElement("polygon", {points:(rep.x)+","+(rep.y-11)+" "+(rep.x+11)+","+rep.y+" "+rep.x+","+(rep.y+11)+" "+(rep.x-11)+","+rep.y,fill:rep.color,stroke:"#fff","stroke-width":"3","vector-effect":"non-scaling-stroke",tabindex:"0","aria-label":"Rep base "+rep.rep_id});
    diamond.addEventListener("pointermove", function(event){showTooltip(event,"<strong>Rep base</strong><br>"+escapeHtml(rep.rep_id)+" · "+escapeHtml(rep.rep_name));});
    diamond.addEventListener("pointerleave", hideTooltip);
    viewport.appendChild(diamond);
  });
  const layer = REPORT_DATA.scenario_layers[currentScenario];
  layer.markers.filter(markerVisible).forEach(function(marker) {
    const radius = marker.segment === "high" ? 9 : marker.segment === "medium" ? 7 : 5.5;
    const circle = svgElement("circle", {cx:marker.x,cy:marker.y,r:radius,fill:marker.color,stroke:marker.is_changed?"#111827":"#fff","stroke-width":marker.is_changed?"3":"2","vector-effect":"non-scaling-stroke",tabindex:"0","aria-label":marker.hcp_name+", assigned to "+marker.rep_id});
    circle.addEventListener("pointermove", function(event){showTooltip(event,markerTooltip(marker));});
    circle.addEventListener("pointerleave", hideTooltip);
    circle.addEventListener("focus", function(){ circle.setAttribute("stroke-width","5"); });
    circle.addEventListener("blur", function(){ circle.setAttribute("stroke-width",marker.is_changed?"3":"2"); });
    viewport.appendChild(circle);
  });
  (layer.unassigned || []).forEach(function(marker) {
    viewport.appendChild(svgElement("path", {d:"M "+(marker.x-8)+" "+(marker.y-8)+" L "+(marker.x+8)+" "+(marker.y+8)+" M "+(marker.x+8)+" "+(marker.y-8)+" L "+(marker.x-8)+" "+(marker.y+8),stroke:"#b91c1c","stroke-width":"4","vector-effect":"non-scaling-stroke"}));
  });
  if (byId("movement-filter").checked) {
    (REPORT_DATA.movements || []).forEach(function(movement) {
      const marker = layer.markers.find(function(item){return item.hcp_id===movement.hcp_id;});
      if (!marker || !markerVisible(marker)) return;
      viewport.appendChild(svgElement("circle", {cx:movement.x,cy:movement.y,r:"15",fill:"none",stroke:"#111827","stroke-width":"2","stroke-dasharray":"3 3","vector-effect":"non-scaling-stroke"}));
    });
  }
  applyTransform();
}
@@PUBLIC_SCRIPT@@
function renderKpis() {
  const values=REPORT_DATA.kpis[currentScenario]||{};
  const metrics=[
    ["Assigned HCPs",values.assigned_hcps,"coverage count"],
    ["Unassigned",values.unassigned_hcps,"lower is better"],
    ["Reassigned",Number(values.reassigned_pct||0).toFixed(1)+"%","lower preserves continuity"],
    ["Workload Gini",Number(values.workload_gini||0).toFixed(3),"lower is more balanced"],
    ["Avg travel",Number(values.avg_travel_min||0).toFixed(1)+" min","lower is better"],
    ["Priority coverage",Number(values.priority_coverage_pct||0).toFixed(1)+"%","higher is better"],
    ["Composite",Number(values.composite||0).toFixed(3),"lower is better"]
  ];
  byId("kpi-grid").innerHTML=metrics.map(function(metric){return '<article class="kpi"><span class="kpi-label">'+escapeHtml(metric[0])+'</span><strong class="kpi-value">'+escapeHtml(metric[1])+'</strong><span class="kpi-direction">'+escapeHtml(metric[2])+'</span></article>';}).join("");
}
function renderBadges() {
  const p=REPORT_DATA.provenance[currentScenario]||{};
  byId("provenance-badges").innerHTML=[p.fixture_data?"Fictional fixture data":"Configured data","Plugin "+(p.plugin_version||"unknown"),"Run "+(p.run_id||"unknown")].map(function(value){return '<span class="badge">'+escapeHtml(value)+'</span>';}).join("");
}
function renderLegend() {
  byId("map-legend").innerHTML=Object.entries(REPORT_DATA.color_map).map(function(entry){return '<span class="legend-item"><span class="legend-dot" style="background:'+escapeHtml(entry[1])+'"></span>'+escapeHtml(entry[0])+'</span>';}).join("")+ '<span class="legend-item">◆ Rep base</span><span class="legend-item">Outlined HCP = changed</span>';
}
function renderWorkload() {
  const rows=REPORT_DATA.territory_rows[currentScenario]||[];
  byId("workload-chart").innerHTML=rows.map(function(row){const width=Math.min(Number(row.utilization_pct||0),125)/1.25;return '<div class="bar-row"><strong>'+escapeHtml(row.rep_id)+'</strong><div class="bar-track" title="'+escapeHtml(row.utilization_pct)+'% of weekly capacity"><div class="bar-fill" style="width:'+width+'%;background:'+escapeHtml(row.color)+'"></div></div><span class="bar-value">'+Number(row.workload_hours_weekly||0).toFixed(1)+'h</span></div>';}).join("") || '<p>No territory workload rows.</p>';
}
function renderObjectives() {
  const objective=REPORT_DATA.scenario_layers[currentScenario].objectives||{};
  const labels=[["Workload",objective.workload_balance],["Travel",objective.travel_efficiency],["Disruption",objective.disruption],["Coverage",objective.coverage],["Composite",objective.composite]];
  byId("objective-chart").innerHTML='<p style="margin-top:0;color:var(--muted);font-size:.75rem">Lower scores are better.</p>'+labels.map(function(item){const value=Number(item[1]||0);return '<div class="bar-row"><strong>'+escapeHtml(item[0])+'</strong><div class="bar-track"><div class="bar-fill" style="width:'+Math.min(value*100,100)+'%;background:#2563eb"></div></div><span class="bar-value">'+value.toFixed(3)+'</span></div>';}).join("");
}
function renderReview() {
  const items=REPORT_DATA.review_items[currentScenario]||[];
  byId("review-queue").innerHTML=items.map(function(item){return '<li class="review-item '+escapeHtml(item.severity)+'"><strong>'+escapeHtml(item.kind)+'</strong><br>'+escapeHtml(item.label)+'</li>';}).join("");
}
function renderTables() {
  const rows=REPORT_DATA.territory_rows[currentScenario]||[];
  byId("territory-table").innerHTML=rows.map(function(row){return '<tr><td><strong>'+escapeHtml(row.rep_id)+'</strong><br>'+escapeHtml(row.rep_name)+'</td><td>'+escapeHtml(row.hcp_count)+'</td><td>'+Number(row.workload_hours_weekly||0).toFixed(1)+'h</td><td>'+Number(row.capacity_hours||0).toFixed(1)+'h</td><td>'+Number(row.travel_hours_weekly||0).toFixed(1)+'h</td><td>'+escapeHtml(row.segment_high)+' / '+escapeHtml(row.segment_medium)+' / '+escapeHtml(row.segment_low)+'</td></tr>';}).join("");
  renderChangeTable();
}
function renderChangeTable() {
  const query=byId("change-search").value.trim().toLowerCase();
  const rows=(REPORT_DATA.scenario_layers[currentScenario].markers||[]).filter(function(marker){if(!marker.is_changed)return false;return !query||[marker.hcp_id,marker.hcp_name,marker.previous_rep,marker.rep_id,marker.change_reason].join(" ").toLowerCase().includes(query);});
  byId("change-table").innerHTML=rows.map(function(marker){return '<tr><td><strong>'+escapeHtml(marker.hcp_name)+'</strong><br>'+escapeHtml(marker.hcp_id)+'</td><td>'+escapeHtml(marker.previous_rep||"—")+'</td><td><span class="status-pill changed">'+escapeHtml(marker.rep_id)+'</span></td><td>'+escapeHtml(marker.change_reason||"Optimization")+'</td></tr>';}).join("") || '<tr><td colspan="4">No changed assignments match.</td></tr>';
}
function renderAdvanced() {
  const exports=REPORT_DATA.advanced_exports[currentScenario]||{};
  byId("export-list").innerHTML=Object.entries(exports).map(function(entry){return '<li><a href="'+escapeHtml(entry[1].href)+'">'+escapeHtml(entry[1].name)+'</a> — '+escapeHtml(entry[0].replaceAll("_"," "))+'</li>';}).join("") || '<li>Advanced export paths are unavailable for this legacy scenario; the saved snapshot remains the report source.</li>';
  const p=REPORT_DATA.provenance[currentScenario]||{};
  const fingerprints=Object.entries(p.input_fingerprints||{}).map(function(entry){return '<div>'+escapeHtml(entry[0])+': '+escapeHtml(entry[1])+'</div>';}).join("");
  byId("fingerprints").innerHTML='<p><strong>Created:</strong> '+escapeHtml(p.created_at||"unknown")+' · <strong>Schema:</strong> '+escapeHtml(p.schema_version||"unknown")+'</p>'+fingerprints;
}
function refreshRepOptions() {
  const selected=byId("rep-filter").value||"all";
  const reps=Array.from(new Set((REPORT_DATA.scenario_layers[currentScenario].markers||[]).map(function(marker){return marker.rep_id;}))).sort();
  byId("rep-filter").innerHTML='<option value="all">All representatives</option>'+reps.map(function(rep){return '<option value="'+escapeHtml(rep)+'">'+escapeHtml(rep)+'</option>';}).join("");
  byId("rep-filter").value=reps.includes(selected)?selected:"all";
}
function renderMap() { if(BASEMAP==="public")renderPublicMap();else renderOfflineMap(); }
function renderAll() {
  refreshRepOptions(); renderBadges(); renderKpis(); renderLegend(); renderWorkload(); renderObjectives(); renderReview(); renderTables(); renderAdvanced(); renderMap();
  byId("map-mode").textContent=BASEMAP==="public"?"Public basemap":"Offline relative territory view";
  byId("map-note").textContent=BASEMAP==="public"?"Public map tiles are enabled by explicit request.":"Offline relative territory view — boundaries show assignment shape, not routing or administrative borders.";
}
function changeScenario(value) { currentScenario=value; zoom=1;panX=0;panY=0;renderAll(); }

SCENARIO_NAMES.forEach(function(name){const option=document.createElement("option");option.value=name;option.textContent=name;byId("scenario-filter").appendChild(option);});
byId("scenario-filter").addEventListener("change",function(event){changeScenario(event.target.value);});
byId("rep-filter").addEventListener("change",renderMap);
byId("segment-filter").addEventListener("change",renderMap);
byId("movement-filter").addEventListener("change",renderMap);
byId("change-search").addEventListener("input",renderChangeTable);
byId("zoom-in").addEventListener("click",function(){if(BASEMAP==="public")publicMap.zoomIn();else{zoom=Math.min(zoom*1.25,5);applyTransform();}});
byId("zoom-out").addEventListener("click",function(){if(BASEMAP==="public")publicMap.zoomOut();else{zoom=Math.max(zoom/1.25,.7);applyTransform();}});
byId("reset-view").addEventListener("click",function(){zoom=1;panX=0;panY=0;if(BASEMAP==="public")renderPublicMap();else applyTransform();});
if(BASEMAP==="offline") {
  const map=byId("territory-map");
  map.addEventListener("pointerdown",function(event){dragging=true;lastPointer=[event.clientX,event.clientY];map.setPointerCapture(event.pointerId);});
  map.addEventListener("pointermove",function(event){if(!dragging)return;panX+=(event.clientX-lastPointer[0])/zoom;panY+=(event.clientY-lastPointer[1])/zoom;lastPointer=[event.clientX,event.clientY];applyTransform();});
  map.addEventListener("pointerup",function(){dragging=false;lastPointer=null;});
  map.addEventListener("wheel",function(event){event.preventDefault();zoom=Math.max(.7,Math.min(5,zoom*(event.deltaY<0?1.12:.89)));applyTransform();},{passive:false});
  map.addEventListener("keydown",function(event){const step=22/zoom;if(event.key==="ArrowLeft")panX+=step;else if(event.key==="ArrowRight")panX-=step;else if(event.key==="ArrowUp")panY+=step;else if(event.key==="ArrowDown")panY-=step;else if(event.key==="+")zoom=Math.min(5,zoom*1.2);else if(event.key==="-")zoom=Math.max(.7,zoom/1.2);else if(event.key==="0"){zoom=1;panX=0;panY=0;}else return;event.preventDefault();applyTransform();});
}
renderAll();
</script>
</body>
</html>
"""
