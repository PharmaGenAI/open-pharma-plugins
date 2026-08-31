#!/usr/bin/env python3
"""Compatibility wrapper for the packaged HCP batch CLI."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [
    str(REPO_ROOT / "src"),
    str(REPO_ROOT / "src" / "capabilities" / "hcp-intelligence"),
]

from open_pharma_plugins_hcp_intelligence.batch_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
