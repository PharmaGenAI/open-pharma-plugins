from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("dispatch_site_release", ROOT / "scripts" / "dispatch_site_release.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _release_env(*, tag: str | None = None, **overrides: str) -> dict[str, str]:
    index = json.loads((ROOT / "plugin-versions.json").read_text())
    plugin_version = index["plugins"]["hcp-intelligence"]
    env = {
        "OPEN_PHARMA_PAGES_DISPATCH_TOKEN": "test-token",
        "RELEASE_TAG": tag or index["tag_format"].format(cap="hcp-intelligence", version=plugin_version),
        "SOURCE_DEFAULT_BRANCH": "main",
    }
    env.update(overrides)
    return env


def _init_release_repo(tmp_path: Path) -> tuple[dict, str, str]:
    index = json.loads((ROOT / "plugin-versions.json").read_text())
    plugin_version = index["plugins"]["hcp-intelligence"]
    tag = index["tag_format"].format(cap="hcp-intelligence", version=plugin_version)

    (tmp_path / "plugin-versions.json").write_text(json.dumps(index), encoding="utf-8")
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "release@example.test"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("release fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "plugin-versions.json", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "release fixture"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "tag", "-a", tag, "-m", "annotated release"], cwd=tmp_path, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", commit],
        cwd=tmp_path,
        check=True,
    )
    return index, tag, commit


def test_resolve_release_commit_dereferences_annotated_tag_on_default_branch(tmp_path):
    _index, tag, commit = _init_release_repo(tmp_path)
    tag_object = subprocess.run(
        ["git", "rev-parse", f"refs/tags/{tag}"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    resolved = MODULE.resolve_release_commit(tag, commit, "main", root=tmp_path)

    assert resolved == commit
    assert resolved != tag_object


def test_build_dispatch_payload_uses_exact_event_and_tag_commit(tmp_path):
    index, tag, commit = _init_release_repo(tmp_path)
    payload = MODULE.build_dispatch_payload(_release_env(tag=tag, RELEASE_COMMIT=commit), root=tmp_path)

    assert payload == {
        "event_type": "open-pharma-plugins-release",
        "client_payload": {
            "repository": "PharmaGenAI/open-pharma-plugins",
            "capability": "hcp-intelligence",
            "tag": tag,
            "commit": commit,
            "distribution_version": index["distribution_version"],
            "plugin_version": index["plugins"]["hcp-intelligence"],
        },
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("RELEASE_TAG", "hcp-intelligence-v1.0.0"),
        ("RELEASE_COMMIT", "not-a-commit"),
        ("SOURCE_DEFAULT_BRANCH", "../main"),
    ],
)
def test_build_dispatch_payload_rejects_invalid_release_identity(tmp_path, field, value):
    _init_release_repo(tmp_path)
    with pytest.raises(ValueError):
        MODULE.build_dispatch_payload(_release_env(**{field: value}), root=tmp_path)


def test_resolve_release_commit_rejects_missing_tag(tmp_path):
    _index, tag, _commit = _init_release_repo(tmp_path)
    subprocess.run(["git", "tag", "-d", tag], cwd=tmp_path, check=True, capture_output=True)

    with pytest.raises(ValueError, match="unable to resolve refs/tags/"):
        MODULE.resolve_release_commit(tag, "0" * 40, "main", root=tmp_path)


def test_resolve_release_commit_rejects_event_commit_mismatch(tmp_path):
    _index, tag, commit = _init_release_repo(tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("second commit\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "head moved"], cwd=tmp_path, check=True, capture_output=True)
    moved = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()

    with pytest.raises(ValueError, match="does not match workflow run commit"):
        MODULE.resolve_release_commit(tag, moved, "main", root=tmp_path)

    assert moved != commit


def test_resolve_release_commit_rejects_tag_outside_origin_default_branch(tmp_path):
    _index, tag, commit = _init_release_repo(tmp_path)
    subprocess.run(["git", "checkout", "--orphan", "trusted-main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "rm", "-rf", "."], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "trusted.txt").write_text("trusted\n", encoding="utf-8")
    subprocess.run(["git", "add", "trusted.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "unrelated main"], cwd=tmp_path, check=True, capture_output=True)
    unrelated = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", unrelated], cwd=tmp_path, check=True)

    with pytest.raises(ValueError, match="is not an ancestor of origin/main"):
        MODULE.resolve_release_commit(tag, commit, "main", root=tmp_path)


def test_build_dispatch_payload_reads_release_index_from_tagged_commit(tmp_path):
    index, tag, commit = _init_release_repo(tmp_path)
    changed = dict(index)
    changed["distribution_version"] = "99.0.0"
    (tmp_path / "plugin-versions.json").write_text(json.dumps(changed), encoding="utf-8")

    payload = MODULE.build_dispatch_payload(_release_env(tag=tag, RELEASE_COMMIT=commit), root=tmp_path)

    assert payload["client_payload"]["distribution_version"] == index["distribution_version"]


def test_main_is_a_notice_only_when_dispatch_secret_is_absent():
    stdout = io.StringIO()
    stderr = io.StringIO()
    status = MODULE.main(
        _release_env(OPEN_PHARMA_PAGES_DISPATCH_TOKEN=""),
        stdout=stdout,
        stderr=stderr,
    )

    assert status == 0
    assert "dispatch skipped" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_main_posts_the_dispatch_to_the_public_site_repository(tmp_path):
    _index, tag, commit = _init_release_repo(tmp_path)
    seen: dict[str, object] = {}

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def opener(request, *, timeout):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        return Response()

    stdout = io.StringIO()
    status = MODULE.main(
        _release_env(tag=tag, RELEASE_COMMIT=commit),
        root=tmp_path,
        opener=opener,
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert status == 0
    assert seen["url"] == "https://api.github.com/repos/PharmaGenAI/pharmagenai.github.io/dispatches"
    assert seen["method"] == "POST"
    assert seen["body"]["event_type"] == "open-pharma-plugins-release"
    assert (
        seen["body"]["client_payload"]
        == MODULE.build_dispatch_payload(_release_env(tag=tag, RELEASE_COMMIT=commit), root=tmp_path)["client_payload"]
    )
    assert seen["body"]["client_payload"]["commit"] == commit
    assert seen["headers"]["Accept"] == "application/vnd.github+json"
    assert seen["headers"]["Content-type"] == "application/json"
    assert seen["headers"]["X-github-api-version"] == "2022-11-28"
    assert seen["timeout"] == 30
    assert stdout.getvalue().strip().startswith("ok: notified PharmaGenAI/pharmagenai.github.io about ")


def test_main_fails_closed_before_api_call_when_tag_commit_validation_fails(tmp_path):
    _index, tag, commit = _init_release_repo(tmp_path)
    subprocess.run(["git", "tag", "-d", tag], cwd=tmp_path, check=True, capture_output=True)
    opened = False

    def opener(_request, *, timeout):
        nonlocal opened
        opened = True
        raise AssertionError("opener should not be called")

    stdout = io.StringIO()
    stderr = io.StringIO()
    status = MODULE.main(
        _release_env(tag=tag, RELEASE_COMMIT=commit),
        root=tmp_path,
        opener=opener,
        stdout=stdout,
        stderr=stderr,
    )

    assert status == 2
    assert not opened
    assert stdout.getvalue() == ""
    assert "unable to resolve refs/tags/" in stderr.getvalue()


def test_main_fails_visibly_when_configured_dispatch_is_rejected(tmp_path):
    _index, tag, commit = _init_release_repo(tmp_path)

    def opener(request, *, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            422,
            "unprocessable",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"Validation Failed"}'),
        )

    stdout = io.StringIO()
    stderr = io.StringIO()
    status = MODULE.main(
        _release_env(tag=tag, RELEASE_COMMIT=commit),
        root=tmp_path,
        opener=opener,
        stdout=stdout,
        stderr=stderr,
    )

    assert status == 1
    assert stdout.getvalue() == ""
    assert "HTTP 422" in stderr.getvalue()
    assert "Validation Failed" in stderr.getvalue()


def test_main_fails_visibly_when_configured_dispatch_times_out(tmp_path):
    _index, tag, commit = _init_release_repo(tmp_path)

    def opener(_request, *, timeout):
        assert timeout == 30
        raise TimeoutError("timed out")

    stdout = io.StringIO()
    stderr = io.StringIO()
    status = MODULE.main(
        _release_env(tag=tag, RELEASE_COMMIT=commit),
        root=tmp_path,
        opener=opener,
        stdout=stdout,
        stderr=stderr,
    )

    assert status == 1
    assert stdout.getvalue() == ""
    assert "timed out after 30 seconds" in stderr.getvalue()
