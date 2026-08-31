#!/usr/bin/env python3
"""Notify the public business site repository about an immutable capability release."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_REPOSITORY = "PharmaGenAI/open-pharma-plugins"
DESTINATION_REPOSITORY = "PharmaGenAI/pharmagenai.github.io"
EVENT_TYPE = "open-pharma-plugins-release"
API_VERSION = "2022-11-28"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
BRANCH_PATTERN = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\Z")
SEMVER_PATTERN = re.compile(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z"
)
TAG_PATTERN = re.compile(
    r"open-pharma-plugins-(?P<cap>[a-z0-9]+(?:-[a-z0-9]+)*)-v"
    r"(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)\Z"
)


def _required(env: dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"missing required environment variable: {key}")
    return value


def _git_rev_parse(root: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git rev-parse failed"
        raise ValueError(f"unable to resolve {ref}: {detail}")
    value = result.stdout.strip()
    if not SHA_PATTERN.fullmatch(value):
        raise ValueError(f"resolved {ref} is not an exact 40-character lowercase commit SHA: {value}")
    return value


def _release_index_at_commit(root: Path, commit: str) -> dict:
    result = subprocess.run(
        ["git", "show", f"{commit}:plugin-versions.json"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git show failed"
        raise ValueError(f"unable to read plugin-versions.json at {commit}: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid plugin-versions.json at {commit}: {exc}") from exc


def resolve_release_commit(
    release_tag: str,
    release_commit: str,
    default_branch: str,
    root: Path = ROOT,
) -> str:
    if not SHA_PATTERN.fullmatch(release_commit):
        raise ValueError(f"invalid RELEASE_COMMIT: {release_commit}")
    if not BRANCH_PATTERN.fullmatch(default_branch) or ".." in default_branch or "@{" in default_branch:
        raise ValueError(f"invalid SOURCE_DEFAULT_BRANCH: {default_branch}")

    tag_commit = _git_rev_parse(root, f"refs/tags/{release_tag}^{{commit}}")
    if tag_commit != release_commit:
        raise ValueError(f"{release_tag} does not match workflow run commit {release_commit}")

    default_ref = f"refs/remotes/origin/{default_branch}"
    default_commit = _git_rev_parse(root, default_ref)
    head_commit = _git_rev_parse(root, "HEAD")
    if head_commit != default_commit:
        raise ValueError(f"trusted checkout HEAD does not match origin/{default_branch}")

    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", tag_commit, default_commit],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if ancestry.returncode == 1:
        raise ValueError(f"{release_tag} is not an ancestor of origin/{default_branch}")
    if ancestry.returncode != 0:
        detail = ancestry.stderr.strip() or ancestry.stdout.strip() or "git merge-base failed"
        raise ValueError(f"unable to validate {release_tag} against origin/{default_branch}: {detail}")
    return tag_commit


def build_dispatch_payload(env: dict[str, str], root: Path = ROOT) -> dict[str, object]:
    release_tag = _required(env, "RELEASE_TAG")
    match = TAG_PATTERN.fullmatch(release_tag)
    if not match:
        raise ValueError(f"invalid RELEASE_TAG: {release_tag}")
    capability = match.group("cap")
    plugin_version = match.group("version")

    release_commit = _required(env, "RELEASE_COMMIT")
    default_branch = _required(env, "SOURCE_DEFAULT_BRANCH")
    release_commit = resolve_release_commit(release_tag, release_commit, default_branch, root=root)
    index = _release_index_at_commit(root, release_commit)
    if index.get("plugins", {}).get(capability) != plugin_version:
        raise ValueError(f"{release_tag} does not match plugin-versions.json")
    distribution_version = str(index.get("distribution_version", ""))
    if not SEMVER_PATTERN.fullmatch(distribution_version):
        raise ValueError(f"invalid distribution_version in plugin-versions.json: {distribution_version}")

    expected_tag = str(index.get("tag_format", "")).format(cap=capability, version=plugin_version)
    if expected_tag != release_tag:
        raise ValueError(f"{release_tag} does not match tag_format {expected_tag}")
    return {
        "event_type": EVENT_TYPE,
        "client_payload": {
            "repository": SOURCE_REPOSITORY,
            "capability": capability,
            "tag": release_tag,
            "commit": release_commit,
            "distribution_version": distribution_version,
            "plugin_version": plugin_version,
        },
    }


def _post_dispatch(token: str, payload: dict[str, object], opener=urllib.request.urlopen) -> None:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{DESTINATION_REPOSITORY}/dispatches",
        data=body,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": API_VERSION,
        },
        method="POST",
    )
    try:
        with opener(request, timeout=30) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if status != 204:
                raise RuntimeError(
                    f"GitHub repository_dispatch returned unexpected status {status} for {DESTINATION_REPOSITORY}"
                )
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace").strip()
        suffix = f": {body_text}" if body_text else ""
        raise RuntimeError(
            f"GitHub repository_dispatch failed with HTTP {exc.code} for {DESTINATION_REPOSITORY}{suffix}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub repository_dispatch failed for {DESTINATION_REPOSITORY}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(
            f"GitHub repository_dispatch timed out after 30 seconds for {DESTINATION_REPOSITORY}"
        ) from exc


def main(
    env: dict[str, str] | None = None,
    *,
    root: Path = ROOT,
    opener=urllib.request.urlopen,
    stdout: io.TextIOBase = sys.stdout,
    stderr: io.TextIOBase = sys.stderr,
) -> int:
    current_env = dict(os.environ if env is None else env)
    token = current_env.get("OPEN_PHARMA_PAGES_DISPATCH_TOKEN", "").strip()
    if not token:
        print(
            "::notice title=Open Pharma Pages dispatch skipped::"
            "OPEN_PHARMA_PAGES_DISPATCH_TOKEN is not configured; skipping website release notification.",
            file=stdout,
        )
        return 0

    try:
        payload = build_dispatch_payload(current_env, root=root)
        _post_dispatch(token, payload, opener=opener)
    except ValueError as exc:
        print(f"error: {exc}", file=stderr)
        return 2
    except RuntimeError as exc:
        print(f"error: {exc}", file=stderr)
        return 1

    print(
        f"ok: notified {DESTINATION_REPOSITORY} about {payload['client_payload']['tag']}",
        file=stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
