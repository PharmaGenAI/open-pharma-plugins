"""Release contract for checked-in Field Training HTML examples."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_field_training_html_examples_match_the_renderer():
    result = subprocess.run(
        [sys.executable, "scripts/generate_field_training_html_examples.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
