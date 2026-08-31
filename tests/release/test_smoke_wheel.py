from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("smoke_wheel", ROOT / "scripts" / "smoke_wheel.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

CI_TOOLS = {
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
}
NBE_TOOLS = {"load_universe", "recommend_engagements", "render_plan"}


def _nbe_csv_result(files: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        isError=False,
        content=[SimpleNamespace(text=json.dumps({"status": "ok", "format": "csv", "files": files}))],
    )


def _write_private_artifact(path: Path, content: str) -> None:
    path.write_text(content)
    if os.name != "nt":
        path.chmod(0o600)


def test_empty_venv_preserves_posix_python_library_layout(monkeypatch, tmp_path):
    captured = {}

    class Builder:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def create(self, path):
            captured["path"] = path

    monkeypatch.setattr(MODULE.venv, "EnvBuilder", Builder)
    MODULE._create_venv(tmp_path / "venv")

    assert captured["with_pip"] is True
    assert captured["clear"] is True
    assert captured["symlinks"] is (os.name != "nt")


def test_competitive_intelligence_wheel_accepts_exact_tool_contract():
    MODULE._validate_tools("open-pharma-plugins-competitive-intelligence", CI_TOOLS)


def test_competitive_intelligence_wheel_rejects_missing_tool():
    with pytest.raises(RuntimeError, match="missing=.*ci_refresh"):
        MODULE._validate_tools(
            "open-pharma-plugins-competitive-intelligence",
            CI_TOOLS - {"ci_refresh"},
        )


def test_competitive_intelligence_wheel_rejects_extra_tool():
    with pytest.raises(RuntimeError, match="extra=.*ci_deprecated"):
        MODULE._validate_tools(
            "open-pharma-plugins-competitive-intelligence",
            CI_TOOLS | {"ci_deprecated"},
        )


def test_next_best_engagement_wheel_rejects_missing_workflow_tool():
    """Catch a wheel that cannot complete the required NBE workflow."""
    with pytest.raises(RuntimeError, match="missing=.*render_plan"):
        MODULE._validate_tools(
            "open-pharma-plugins-next-best-engagement",
            NBE_TOOLS - {"render_plan"},
        )


def test_next_best_engagement_wheel_rejects_extra_tool():
    """The installed NBE contract must remain exactly its three workflow tools."""
    with pytest.raises(RuntimeError, match="extra=.*deprecated_action"):
        MODULE._validate_tools(
            "open-pharma-plugins-next-best-engagement",
            NBE_TOOLS | {"deprecated_action"},
        )


def test_next_best_engagement_wheel_rejects_duplicate_csv_artifact_paths(tmp_path):
    """Catch a renderer response that aliases the two required CSV artifacts."""
    output_dir = tmp_path / "nbe-output"
    output_dir.mkdir()
    artifact = output_dir / "engagements.csv"
    _write_private_artifact(artifact, "hcp_id\nHCP-001\n")

    with pytest.raises(RuntimeError, match="distinct"):
        MODULE._validate_nbe_csv_output(
            _nbe_csv_result({"engagements_csv": str(artifact), "summary_csv": str(artifact)}),
            output_dir,
        )


def test_next_best_engagement_wheel_rejects_non_csv_artifact_path(tmp_path):
    """Catch a renderer response that labels a non-CSV file as an export."""
    output_dir = tmp_path / "nbe-output"
    output_dir.mkdir()
    engagements = output_dir / "engagements.csv"
    summary = output_dir / "plan_summary.txt"
    _write_private_artifact(engagements, "hcp_id\nHCP-001\n")
    _write_private_artifact(summary, "section,metric,value\noverview,total_planned,1\n")

    with pytest.raises(RuntimeError, match=r"\.csv"):
        MODULE._validate_nbe_csv_output(
            _nbe_csv_result({"engagements_csv": str(engagements), "summary_csv": str(summary)}),
            output_dir,
        )


def _installed_batch_executable() -> Path:
    executable = shutil.which("open-pharma-plugins-hcp-batch")
    assert executable, "the development environment must install the HCP batch console"
    return Path(executable)


def _write_python_process(path: Path, body: str) -> list[str]:
    path.write_text(body)
    return [sys.executable, str(path)]


def test_batch_probe_runs_the_real_console_without_creating_dry_run_output(tmp_path):
    output_dir = tmp_path / "hcp-batch-dry-run"

    MODULE._probe_batch(_installed_batch_executable(), tmp_path)

    assert not output_dir.exists()


def test_batch_probe_rejects_empty_help_from_a_real_process(tmp_path):
    command = _write_python_process(
        tmp_path / "empty_help.py",
        "import sys\n"
        "if sys.argv[1:] != ['--help']:\n"
        "    print('Would process 1 account(s)')\n"
        "    print('Reasoning effort: high')\n",
    )

    with pytest.raises(RuntimeError, match="empty help"):
        MODULE._probe_batch(command, tmp_path)


def test_batch_probe_requires_high_reasoning_from_actual_dry_run_stdout(tmp_path):
    command = _write_python_process(
        tmp_path / "missing_reasoning.py",
        "import sys\n"
        "if sys.argv[1:] == ['--help']:\n"
        "    print('usage: hcp-batch')\n"
        "else:\n"
        "    print('Would process 1 account(s)')\n",
    )

    with pytest.raises(AssertionError, match="Reasoning effort: high"):
        MODULE._probe_batch(command, tmp_path)
