from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("check_release_tag", ROOT / "scripts" / "check_release_tag.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_release_tag_matches_current_metadata(tmp_path):
    index = json.loads((ROOT / "plugin-versions.json").read_text())
    version = index["plugins"]["hcp-intelligence"]
    tag = index["tag_format"].format(cap="hcp-intelligence", version=version)

    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".github/plugin").mkdir(parents=True)
    (tmp_path / "plugin-versions.json").write_text(json.dumps(index))
    (tmp_path / ".claude-plugin/marketplace.json").write_text((ROOT / ".claude-plugin/marketplace.json").read_text())
    (tmp_path / ".github/plugin/marketplace.json").write_text((ROOT / ".github/plugin/marketplace.json").read_text())
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "release@example.test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "release fixture"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "tag", tag], cwd=tmp_path, check=True)

    capability, actual_version = MODULE.verify(tag, tmp_path)
    assert (capability, actual_version) == ("hcp-intelligence", version)


@pytest.mark.parametrize(
    "tag",
    [
        "v1.0.0",
        "open-pharma-plugins-missing-v1.0.0",
        "open-pharma-plugins-hcp-intelligence-v9.0.0",
    ],
)
def test_release_tag_rejects_wrong_shape_or_metadata(tag):
    with pytest.raises(ValueError):
        MODULE.verify(tag)


def test_release_tag_rejects_copilot_marketplace_drift(tmp_path):
    index = json.loads((ROOT / "plugin-versions.json").read_text())
    version = index["plugins"]["hcp-intelligence"]
    tag = index["tag_format"].format(cap="hcp-intelligence", version=version)

    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".github/plugin").mkdir(parents=True)
    (tmp_path / "plugin-versions.json").write_text(json.dumps(index))
    (tmp_path / ".claude-plugin/marketplace.json").write_text((ROOT / ".claude-plugin/marketplace.json").read_text())
    copilot_marketplace = json.loads((ROOT / ".github/plugin/marketplace.json").read_text())
    entry = next(p for p in copilot_marketplace["plugins"] if p["name"].endswith("hcp-intelligence"))
    entry["source"]["ref"] = "open-pharma-plugins-hcp-intelligence-v9.9.9"
    (tmp_path / ".github/plugin/marketplace.json").write_text(json.dumps(copilot_marketplace))

    with pytest.raises(ValueError, match="Copilot marketplace does not pin"):
        MODULE.verify(tag, tmp_path)
