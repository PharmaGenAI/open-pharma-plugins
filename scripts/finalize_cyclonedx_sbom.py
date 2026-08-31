#!/usr/bin/env python3
"""Add the deterministic CycloneDX serial number required by GitHub SBOM attestations."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from pathlib import Path

NAMESPACE = uuid.NAMESPACE_URL
SEED_PREFIX = "open-pharma-plugins/"


def serial_number(seed: str) -> str:
    if not seed.strip():
        raise ValueError("seed must not be empty")
    return f"urn:uuid:{uuid.uuid5(NAMESPACE, f'{SEED_PREFIX}{seed}')}"


def finalize(path: Path, seed: str) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{path}: CycloneDX document must be an object")
    if document.get("bomFormat") != "CycloneDX":
        raise ValueError(f"{path}: bomFormat must be CycloneDX")
    if not isinstance(document.get("specVersion"), str) or not document["specVersion"]:
        raise ValueError(f"{path}: specVersion must be a non-empty string")

    document["serialNumber"] = serial_number(seed)
    serialized = json.dumps(document, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temporary:
        temporary.write(serialized)
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--seed", required=True)
    args = parser.parse_args()
    try:
        document = finalize(args.path, args.seed)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"ok: finalized CycloneDX {document['specVersion']} serial number")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
