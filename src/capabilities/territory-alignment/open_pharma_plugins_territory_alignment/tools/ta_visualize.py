"""ta_visualize — consolidated offline-first territory reports."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class VisualizeArgs(BaseModel):
    scenarios: list[str] = Field(
        min_length=1,
        max_length=2,
        description="1-2 scenario names. Single = territory report; two = comparison report",
    )
    show_movements: bool = Field(
        default=True,
        description="Show reassigned HCP movement indicators in two-scenario reports",
    )
    show_rep_bases: bool = Field(
        default=True,
        description="Show representative home-base markers",
    )
    file_name: str | None = Field(
        default=None,
        description="Output filename stem (auto-generated if omitted)",
    )
    basemap: Literal["offline", "public"] = Field(
        default="offline",
        description=(
            "offline keeps all rendering local; public explicitly loads Leaflet and CARTO/OpenStreetMap resources"
        ),
    )

    @model_validator(mode="after")
    def require_unique_scenarios(self) -> VisualizeArgs:
        if len(self.scenarios) != len(set(self.scenarios)):
            raise ValueError("scenario names must be unique")
        return self


TOOL: dict[str, Any] = {
    "name": "ta_visualize",
    "description": (
        "Generate a consolidated visual HTML report for one or two saved territory scenarios. "
        "Reports combine executive metrics, an interactive territory map, workload and objective "
        "charts, a review queue, changed assignments, and advanced export links. The default "
        "offline mode makes no network requests. Set basemap='public' explicitly to load public "
        "CARTO/OpenStreetMap resources, which can expose map requests to those providers."
    ),
    "args": VisualizeArgs,
}


def _sanitize_filename(name: str) -> str:
    import re

    clean = re.sub(r"[^a-zA-Z0-9_\-]", "_", name).strip("_")
    return clean[:100] if clean else "output"


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from shared.filesystem import atomic_write_text, ensure_private_dir

    from ..data import _sanitize_scenario_name, _scenarios_dir, load_scenario, scenarios_share_input_universe
    from ..reporting import build_report_model, render_report_html, scenario_artifact_paths

    try:
        args = VisualizeArgs.model_validate(arguments)
    except ValueError as exc:
        return [{"type": "text", "text": json.dumps({"error": str(exc)})}]

    scenario_names = args.scenarios
    scenarios: dict[str, dict[str, Any]] = {}
    scenarios_dir = _scenarios_dir()
    for name in scenario_names:
        try:
            scenario = load_scenario(name)
        except ValueError as exc:
            return [{"type": "text", "text": json.dumps({"error": str(exc)})}]
        if scenario is None:
            return [
                {
                    "type": "text",
                    "text": json.dumps({"error": f"Scenario '{name}' not found. Run ta_align first."}),
                }
            ]
        safe_name = _sanitize_scenario_name(name)
        scenario.setdefault("metadata", {}).setdefault(
            "artifacts",
            scenario_artifact_paths(scenarios_dir, safe_name).as_metadata(),
        )
        scenarios[name] = scenario

    if not scenarios_share_input_universe(list(scenarios.values())):
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {"error": "Scenarios use different input universes and cannot be visualized as a comparison."}
                ),
            }
        ]

    model = build_report_model(
        scenarios,
        scenario_names,
        show_movements=args.show_movements and len(scenario_names) == 2,
        show_rep_bases=args.show_rep_bases,
    )
    is_comparison = len(scenario_names) == 2
    if args.file_name:
        file_stem = _sanitize_filename(args.file_name)
    elif is_comparison:
        file_stem = "_vs_".join(_sanitize_filename(name) for name in scenario_names)
        if args.basemap == "public":
            file_stem += "_public"
    else:
        file_stem = _sanitize_filename(scenario_names[0])
        if args.basemap == "public":
            file_stem += "_public"

    out_dir = ensure_private_dir(scenarios_dir.parent)
    html_path = out_dir / f"{file_stem}.html"
    reuse_primary = not is_comparison and args.basemap == "offline" and args.file_name is None and html_path.is_file()
    if not reuse_primary:
        html = render_report_html(model, scenario_names, basemap=args.basemap)
        atomic_write_text(html_path, html)

    first_snapshot = scenarios[scenario_names[0]]["input_snapshot"]
    result = {
        "success": True,
        "html_path": str(html_path),
        "scenarios": scenario_names,
        "comparison": is_comparison,
        "basemap": args.basemap,
        "network_access": args.basemap == "public",
        "hcp_count": len(first_snapshot.get("hcps", [])),
        "rep_count": len(model["color_map"]),
    }
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
