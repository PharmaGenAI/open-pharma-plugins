#!/usr/bin/env python3
"""Verify that every marketplace catalog tag exists in the canonical public repository."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_CATALOG = Path(".claude-plugin/marketplace.json")
COPILOT_CATALOG = Path(".github/plugin/marketplace.json")
CANONICAL_REPOSITORY = "PharmaGenAI/open-pharma-plugins"
CANONICAL_HTTPS_URL = f"https://github.com/{CANONICAL_REPOSITORY}.git"
PLUGIN_PREFIX = "open-pharma-plugins-"

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _load_catalog(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        catalog = json.load(handle)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("plugins"), list):
        raise ValueError(f"{path}: plugins must be a list")
    return catalog


def _catalog_refs(path: Path, *, copilot: bool) -> dict[str, str]:
    refs: dict[str, str] = {}
    for plugin in _load_catalog(path)["plugins"]:
        if not isinstance(plugin, dict):
            raise ValueError(f"{path}: plugin entry must be an object")
        name = plugin.get("name")
        if not isinstance(name, str) or not name.startswith(PLUGIN_PREFIX):
            raise ValueError(f"{path}: invalid plugin name {name!r}")
        capability = name.removeprefix(PLUGIN_PREFIX)
        source = plugin.get("source")
        if not isinstance(source, dict):
            raise ValueError(f"{path}: {name} source must be an object")
        expected_path = f"src/capabilities/{capability}"
        if source.get("path") != expected_path:
            raise ValueError(f"{path}: {name} must use path {expected_path!r}")
        if copilot:
            if source.get("source") != "github" or source.get("repo") != CANONICAL_REPOSITORY:
                raise ValueError(f"{path}: {name} must use canonical repository {CANONICAL_REPOSITORY}")
        elif source.get("source") != "git-subdir" or source.get("url") != CANONICAL_HTTPS_URL:
            raise ValueError(f"{path}: canonical repository {CANONICAL_HTTPS_URL} required for {name}")
        ref = source.get("ref")
        if not isinstance(ref, str) or not ref:
            raise ValueError(f"{path}: {name} must pin a non-empty ref")
        if name in refs:
            raise ValueError(f"{path}: duplicate plugin {name}")
        refs[name] = ref
    return refs


def catalog_refs(root: Path = ROOT) -> tuple[str, ...]:
    """Return every mutually agreed catalog tag, rejecting catalog drift first."""
    claude = _catalog_refs(root / CLAUDE_CATALOG, copilot=False)
    copilot = _catalog_refs(root / COPILOT_CATALOG, copilot=True)
    if claude.keys() != copilot.keys():
        raise ValueError(
            "catalog drift: Claude and Copilot marketplaces list different plugins "
            f"(Claude={sorted(claude)}, Copilot={sorted(copilot)})"
        )
    for name, ref in claude.items():
        if copilot[name] != ref:
            raise ValueError(f"catalog drift: {name} Claude ref {ref!r} != Copilot ref {copilot[name]!r}")
    return tuple(claude.values())


def _public_git_environment() -> dict[str, str]:
    """Use only anonymous, noninteractive Git configuration for public ref probes."""
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "LC_ALL": "C",
    }


def _remote_has_tag(tag: str, runner: Runner) -> bool:
    reference = f"refs/tags/{tag}"
    result = runner(
        [
            "git",
            "-c",
            "credential.helper=",
            "-c",
            "core.askPass=",
            "ls-remote",
            "--refs",
            CANONICAL_HTTPS_URL,
            reference,
        ],
        capture_output=True,
        text=True,
        env=_public_git_environment(),
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ValueError(f"unable to query public catalog tag {tag}: {detail}")
    return any(line.rsplit("\t", 1)[-1] == reference for line in result.stdout.splitlines())


def verify(root: Path = ROOT, *, runner: Runner = subprocess.run) -> tuple[str, ...]:
    """Reject catalog drift or every tag that is absent from the public repository."""
    refs = catalog_refs(root)
    missing = [tag for tag in refs if not _remote_has_tag(tag, runner)]
    if missing:
        raise ValueError(f"missing remote catalog tag(s): {', '.join(missing)}")
    return refs


def main() -> int:
    try:
        refs = verify()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"ok: {len(refs)} Claude/Copilot catalog refs exist in {CANONICAL_HTTPS_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
