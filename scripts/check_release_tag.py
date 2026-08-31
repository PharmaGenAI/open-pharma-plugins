#!/usr/bin/env python3
"""Verify that an immutable capability tag matches the checked-out release metadata."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAG_PATTERN = re.compile(
    r"open-pharma-plugins-(?P<cap>.+)-v"
    r"(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)\Z"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify(tag: str, root: Path = ROOT) -> tuple[str, str]:
    match = TAG_PATTERN.fullmatch(tag)
    if not match:
        raise ValueError(f"invalid capability release tag: {tag}")
    capability = match.group("cap")
    version = match.group("version")

    index = _load(root / "plugin-versions.json")
    if index.get("plugins", {}).get(capability) != version:
        raise ValueError(f"{tag} does not match plugin-versions.json")
    expected = index.get("tag_format", "").format(cap=capability, version=version)
    if expected != tag:
        raise ValueError(f"{tag} does not match tag_format {expected}")

    marketplace = _load(root / ".claude-plugin" / "marketplace.json")
    entry = next(
        (item for item in marketplace.get("plugins", []) if item.get("name") == f"open-pharma-plugins-{capability}"),
        None,
    )
    if not entry or entry.get("source", {}).get("ref") != tag:
        raise ValueError(f"marketplace does not pin {capability} to {tag}")

    copilot_marketplace = _load(root / ".github" / "plugin" / "marketplace.json")
    copilot_entry = next(
        (
            item
            for item in copilot_marketplace.get("plugins", [])
            if item.get("name") == f"open-pharma-plugins-{capability}"
        ),
        None,
    )
    if not copilot_entry or copilot_entry.get("source", {}).get("ref") != tag:
        raise ValueError(f"Copilot marketplace does not pin {capability} to {tag}")
    if copilot_entry.get("version") != version:
        raise ValueError(f"Copilot marketplace does not set {capability} to version {version}")

    tag_commit = subprocess.run(
        ["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if tag_commit.returncode == 0:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if tag_commit.stdout.strip() != head:
            raise ValueError(f"{tag} does not point at the checked-out commit")

    return capability, version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    args = parser.parse_args()
    try:
        capability, version = verify(args.tag)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(f"ok: {args.tag} matches {capability} {version} release metadata")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
