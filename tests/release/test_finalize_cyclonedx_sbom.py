from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

from conftest import REPO_ROOT

SCRIPT = Path(REPO_ROOT) / "scripts" / "finalize_cyclonedx_sbom.py"
SEED = "open-pharma-plugins-campaign-studio-v1.1.0"


def _run(path: Path, seed: str = SEED) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path), "--seed", seed],
        capture_output=True,
        text=True,
    )


def test_finalizer_adds_a_deterministic_cyclonedx_serial_number(tmp_path):
    path = tmp_path / "bom.json"
    payload = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "metadata": {"component": {"name": "open-pharma-plugins"}},
    }
    path.write_text(json.dumps(payload))

    result = _run(path)

    assert result.returncode == 0, result.stderr
    finalized = json.loads(path.read_text())
    assert finalized["serialNumber"] == f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'open-pharma-plugins/{SEED}')}"
    assert finalized["metadata"] == payload["metadata"]
    first_output = path.read_bytes()

    path.write_text(json.dumps(dict(reversed(list(payload.items())))))
    assert _run(path).returncode == 0
    assert path.read_bytes() == first_output


def test_finalizer_rejects_malformed_or_incomplete_cyclonedx_documents(tmp_path):
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{")
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps({"bomFormat": "CycloneDX"}))
    wrong_format = tmp_path / "spdx.json"
    wrong_format.write_text(json.dumps({"bomFormat": "SPDX", "specVersion": "1.6"}))

    for path in (malformed, incomplete, wrong_format):
        result = _run(path)
        assert result.returncode != 0
        assert path.read_text() in {
            "{",
            json.dumps({"bomFormat": "CycloneDX"}),
            json.dumps({"bomFormat": "SPDX", "specVersion": "1.6"}),
        }
