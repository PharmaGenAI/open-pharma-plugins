"""Shared pytest fixtures for open-pharma-plugins tests."""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

_SRC = os.path.join(REPO_ROOT, "src")
sys.path.insert(0, _SRC)
_CAPABILITIES = os.path.join(_SRC, "capabilities")


def _discover_servers() -> dict:
    servers = {}
    for cap in sorted(os.listdir(_CAPABILITIES)):
        cap_dir = os.path.join(_CAPABILITIES, cap)
        if not os.path.isdir(cap_dir):
            continue
        for sub in sorted(os.listdir(cap_dir)):
            if os.path.isfile(os.path.join(cap_dir, sub, "__init__.py")):
                sys.path.insert(0, cap_dir)
                servers[sub] = os.path.join(cap_dir, sub)
    return servers


_SERVER_DIRS = _discover_servers()


@pytest.fixture(scope="session")
def repo_root() -> str:
    return REPO_ROOT


@pytest.fixture(autouse=True)
def isolate_mutable_runtime_data(tmp_path, monkeypatch):
    """No unit test may write capability state into the checkout or the user's home."""
    base = tmp_path / "runtime-data"
    monkeypatch.delenv("OPEN_PHARMA_CONFIG", raising=False)
    monkeypatch.setenv("OPEN_PHARMA_CONFIG_DIR", str(base / "shared-config"))
    monkeypatch.setattr("shared.env._config_cache", None)
    monkeypatch.setenv("OPEN_PHARMA_HCP_DATA_DIR", str(base / "hcp-intelligence"))
    monkeypatch.setenv("OPEN_PHARMA_TRAINING_CONTENT_DIR", str(base / "training-content"))
    monkeypatch.setenv("OPEN_PHARMA_CAMPAIGN_STORE_DIR", str(base / "campaign-studio"))
    monkeypatch.setenv("OPEN_PHARMA_TA_SCENARIOS_DIR", str(base / "territory-alignment" / "scenarios"))
    monkeypatch.setenv("OPEN_PHARMA_CI_DATA_DIR", str(base / "competitive-intelligence"))
    monkeypatch.setenv("OPEN_PHARMA_NBE_OUTPUT_DIR", str(base / "next-best-engagement"))


@pytest.fixture
def fixtures(repo_root: str):
    from helpers.ci_http import FixtureFiles

    return FixtureFiles(Path(repo_root) / "tests" / "fixtures" / "competitive_intelligence")
