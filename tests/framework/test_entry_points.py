"""Console entry points must be import-safe and expose the callable setuptools loads."""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

import mcp_framework

PACKAGES = [
    "open_pharma_plugins_hcp_intelligence",
    "open_pharma_plugins_field_training",
    "open_pharma_plugins_campaign_studio",
    "open_pharma_plugins_next_best_engagement",
    "open_pharma_plugins_territory_alignment",
    "open_pharma_plugins_competitive_intelligence",
]


@pytest.mark.parametrize("package", PACKAGES)
def test_entry_point_module_is_import_safe_and_exposes_main(monkeypatch, package):
    calls: list[str] = []
    monkeypatch.setattr(mcp_framework, "run_main", calls.append)
    sys.modules.pop(f"{package}.__main__", None)

    module = importlib.import_module(f"{package}.__main__")

    assert calls == [], "importing an entry-point module must not start a stdio server"
    module.main()
    assert calls == [package]


def test_batch_cli_import_is_side_effect_free():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import open_pharma_plugins_hcp_intelligence.batch_cli as module; "
                "assert callable(module.main); print('imported')"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "imported\n"
    assert result.stderr == ""
