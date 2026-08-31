#!/usr/bin/env python3
"""Install a wheel in an empty venv and smoke-test every console/MCP server."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import venv
from collections.abc import Sequence
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

CONSOLES = {
    "open-pharma-plugins-hcp-intelligence": ("list_accounts", {}),
    "open-pharma-plugins-field-training": ("list_documents", {}),
    "open-pharma-plugins-campaign-studio": (
        "retrieve_brand_components",
        {"campaign_brief_id": "wheel-smoke"},
    ),
    "open-pharma-plugins-next-best-engagement": ("load_universe", {"source": "fixture"}),
    "open-pharma-plugins-territory-alignment": ("ta_status", {}),
    "open-pharma-plugins-competitive-intelligence": ("ci_status", {}),
}
BATCH_CONSOLE = "open-pharma-plugins-hcp-batch"
EXPECTED_TOOLS = {
    "open-pharma-plugins-competitive-intelligence": {
        "ci_status",
        "ci_track",
        "ci_scan_trials",
        "ci_trial_detail",
        "ci_scan_regulatory",
        "ci_scan_news",
        "ci_scan_publications",
        "ci_extract_events",
        "ci_landscape",
        "ci_report",
        "ci_timeline",
        "ci_refresh",
    },
    "open-pharma-plugins-next-best-engagement": {
        "load_universe",
        "recommend_engagements",
        "render_plan",
    },
    "open-pharma-plugins-territory-alignment": {
        "ta_status",
        "ta_align",
        "ta_evaluate",
        "ta_compare",
        "ta_cluster",
        "ta_visualize",
    },
}


def _bin(venv_dir: Path, name: str) -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv_dir / directory / f"{name}{suffix}"


def _create_venv(venv_dir: Path) -> None:
    # uv's standalone macOS Python links libpython relative to its original executable. Copying that
    # executable into a venv loses the sibling library; POSIX symlinks preserve the relationship.
    venv.EnvBuilder(with_pip=True, clear=True, symlinks=os.name != "nt").create(venv_dir)


def _validate_tools(console_name: str, tool_names: set[str]) -> None:
    expected = EXPECTED_TOOLS.get(console_name)
    if expected is None:
        if not tool_names:
            raise RuntimeError(f"{console_name} advertised no tools")
        return

    missing = sorted(expected - tool_names)
    extra = sorted(tool_names - expected)
    if missing or extra:
        raise RuntimeError(f"{console_name} tool contract mismatch: missing={missing}, extra={extra}")


async def _probe(executable: Path, tool_name: str, arguments: dict, data_root: Path) -> None:
    env = {
        **os.environ,
        "OPEN_PHARMA_HCP_DATA_DIR": str(data_root / "hcp-intelligence"),
        "OPEN_PHARMA_TRAINING_CONTENT_DIR": str(data_root / "training-content"),
        "OPEN_PHARMA_CAMPAIGN_STORE_DIR": str(data_root / "campaign-studio"),
        "OPEN_PHARMA_NBE_OUTPUT_DIR": str(data_root / "next-best-engagement"),
        "OPEN_PHARMA_TA_SCENARIOS_DIR": str(data_root / "territory-alignment" / "scenarios"),
        "OPEN_PHARMA_CI_DATA_DIR": str(data_root / "competitive-intelligence"),
    }
    params = StdioServerParameters(command=str(executable), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            if initialized.serverInfo.name.replace("_", "-") != executable.name:
                raise RuntimeError(f"unexpected server name from {executable.name}: {initialized.serverInfo.name}")
            _validate_tools(executable.stem, {tool.name for tool in tools.tools})

            if executable.stem == "open-pharma-plugins-next-best-engagement":
                await _probe_nbe_workflow(session, data_root / "next-best-engagement")
                return
            if executable.stem == "open-pharma-plugins-territory-alignment":
                await _probe_ta_workflow(session)
                return

            result = await session.call_tool(tool_name, arguments)
            if result.isError or not result.content:
                raise RuntimeError(f"{executable.name} failed its {tool_name} tool call")


async def _probe_nbe_workflow(session: ClientSession, output_dir: Path) -> None:
    """Exercise NBE's required ordered workflow in the already-open stdio session."""
    load_result = await session.call_tool("load_universe", {"source": "fixture"})
    if load_result.isError or not load_result.content:
        raise RuntimeError("NBE load_universe failed")

    recommend_result = await session.call_tool("recommend_engagements", {})
    if recommend_result.isError or not recommend_result.content:
        raise RuntimeError("NBE recommend_engagements failed")

    render_result = await session.call_tool("render_plan", {"format": "csv"})
    _validate_nbe_csv_output(render_result, output_dir)


async def _probe_ta_workflow(session: ClientSession) -> None:
    """Exercise TA scenario persistence and planning through the installed wheel."""
    align_result = await session.call_tool(
        "ta_align",
        {"scenario_name": "wheel-smoke", "max_iterations": 0},
    )
    if align_result.isError or not align_result.content:
        raise RuntimeError("TA ta_align failed")
    alignment = json.loads(align_result.content[0].text)
    if alignment.get("error") or not alignment.get("metadata", {}).get("run_id"):
        raise RuntimeError(f"TA ta_align returned invalid output: {alignment!r}")
    _validate_ta_artifacts(alignment)

    evaluate_result = await session.call_tool(
        "ta_evaluate",
        {"scenario_name": "wheel-smoke"},
    )
    if evaluate_result.isError or not evaluate_result.content:
        raise RuntimeError("TA ta_evaluate failed")
    evaluation = json.loads(evaluate_result.content[0].text)
    if evaluation.get("run_id") != alignment["metadata"]["run_id"]:
        raise RuntimeError("TA ta_evaluate did not use the saved scenario run")

    rep_id = alignment["assignments"][0]["primary_rep"]
    cluster_result = await session.call_tool(
        "ta_cluster",
        {"scenario_name": "wheel-smoke", "rep_id": rep_id, "period": "next_week"},
    )
    if cluster_result.isError or not cluster_result.content:
        raise RuntimeError("TA ta_cluster failed")
    plan = json.loads(cluster_result.content[0].text)
    if plan.get("scenario_name") != "wheel-smoke" or not plan.get("planning_dates"):
        raise RuntimeError(f"TA ta_cluster returned invalid output: {plan!r}")


def _validate_ta_artifacts(alignment: dict) -> None:
    artifacts = alignment.get("metadata", {}).get("artifacts", {})
    report = Path(artifacts.get("primary_report", ""))
    advanced = artifacts.get("advanced_exports", {})
    if set(advanced) != {"scenario_json", "assignments_csv", "territory_summary_csv"}:
        raise RuntimeError(f"TA advanced artifact contract mismatch: {advanced!r}")
    paths = [report, *(Path(value) for value in advanced.values())]
    for path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"TA artifact is missing or empty: {path}")
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise RuntimeError(f"TA artifact is not private: {path}")
    if report.suffix != ".html":
        raise RuntimeError(f"TA primary report is not HTML: {report}")
    html = report.read_text(encoding="utf-8")
    if "https://" in html or "http://" in html:
        raise RuntimeError("TA automatic report unexpectedly contains an external URL")


def _validate_nbe_csv_output(result, output_dir: Path) -> None:
    """Validate CSV artifacts returned by the installed NBE server."""
    if result.isError or not result.content:
        raise RuntimeError("NBE render_plan failed")
    try:
        payload = json.loads(result.content[0].text)
    except (AttributeError, IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError("NBE render_plan returned invalid CSV output") from exc

    if payload.get("status") != "ok" or payload.get("format") != "csv":
        raise RuntimeError(f"NBE render_plan did not report CSV success: {payload!r}")
    files = payload.get("files")
    if not isinstance(files, dict) or set(files) != {"engagements_csv", "summary_csv"}:
        raise RuntimeError(f"NBE render_plan returned unexpected CSV paths: {files!r}")

    root = output_dir.resolve()
    paths = {name: Path(raw_path).resolve() for name, raw_path in files.items()}
    if len(set(paths.values())) != len(paths):
        raise RuntimeError(f"NBE CSV artifacts must be distinct: {paths!r}")
    for name, path in paths.items():
        if path.suffix != ".csv":
            raise RuntimeError(f"NBE {name} is not a .csv artifact: {path}")
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise RuntimeError(f"NBE {name} escaped isolated output directory: {path}") from exc
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"NBE {name} is missing or empty: {path}")
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise RuntimeError(f"NBE {name} is not private: {path}")


def _probe_batch(executable: Path | Sequence[str], data_root: Path) -> None:
    command = [str(executable)] if isinstance(executable, Path) else list(executable)
    if not command:
        raise ValueError("batch command cannot be empty")

    help_result = subprocess.run(
        [*command, "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    if not help_result.stdout.strip():
        raise RuntimeError(f"{Path(command[-1]).name} returned empty help")

    output_dir = data_root / "hcp-batch-dry-run"
    result = subprocess.run(
        [
            *command,
            "--ids",
            "HCP-SG-001",
            "--output-dir",
            str(output_dir),
            "--dry-run",
            "--synthesize",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Would process 1 account(s)" in result.stdout, "dry-run did not select exactly one account"
    assert "Reasoning effort: high" in result.stdout, "dry-run did not report Reasoning effort: high"
    assert not output_dir.exists(), "dry-run created its output directory"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    if not wheel.is_file():
        parser.error(f"wheel not found: {wheel}")

    with tempfile.TemporaryDirectory(prefix="open-pharma-wheel-") as tmp:
        venv_dir = Path(tmp) / "venv"
        _create_venv(venv_dir)
        python = _bin(venv_dir, "python")
        subprocess.run(
            [str(python), "-m", "pip", "install", f"{wheel}[all]"],
            check=True,
        )
        data_root = Path(tmp) / "runtime-data"
        for console, (tool_name, arguments) in CONSOLES.items():
            executable = _bin(venv_dir, console)
            version = subprocess.run(
                [str(executable), "--version"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if not version:
                raise RuntimeError(f"{console} returned an empty version")
            anyio.run(_probe, executable, tool_name, arguments, data_root)
            print(f"ok {console} {version}")

        batch_executable = _bin(venv_dir, BATCH_CONSOLE)
        _probe_batch(batch_executable, data_root)
        print(f"ok {BATCH_CONSOLE} help and dry-run")


if __name__ == "__main__":
    main()
