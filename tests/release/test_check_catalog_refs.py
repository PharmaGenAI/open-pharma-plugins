from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("check_catalog_refs", ROOT / "scripts" / "check_catalog_refs.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _catalog_root(tmp_path: Path) -> Path:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".github/plugin").mkdir(parents=True)
    (tmp_path / ".claude-plugin/marketplace.json").write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "open-pharma-plugins-search",
                        "source": {
                            "source": "git-subdir",
                            "url": "https://github.com/PharmaGenAI/open-pharma-plugins.git",
                            "path": "src/capabilities/search",
                            "ref": "open-pharma-plugins-search-v1.0.1",
                        },
                    },
                    {
                        "name": "open-pharma-plugins-training",
                        "source": {
                            "source": "git-subdir",
                            "url": "https://github.com/PharmaGenAI/open-pharma-plugins.git",
                            "path": "src/capabilities/training",
                            "ref": "open-pharma-plugins-training-v1.0.2",
                        },
                    },
                ]
            }
        )
        + "\n"
    )
    (tmp_path / ".github/plugin/marketplace.json").write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "open-pharma-plugins-search",
                        "source": {
                            "source": "github",
                            "repo": "PharmaGenAI/open-pharma-plugins",
                            "path": "src/capabilities/search",
                            "ref": "open-pharma-plugins-search-v1.0.1",
                        },
                    },
                    {
                        "name": "open-pharma-plugins-training",
                        "source": {
                            "source": "github",
                            "repo": "PharmaGenAI/open-pharma-plugins",
                            "path": "src/capabilities/training",
                            "ref": "open-pharma-plugins-training-v1.0.2",
                        },
                    },
                ]
            }
        )
        + "\n"
    )
    return tmp_path


def test_verify_accepts_every_catalog_ref_found_at_the_public_remote(tmp_path):
    root = _catalog_root(tmp_path)
    checked: list[str] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        tag = command[-1].removeprefix("refs/tags/")
        checked.append(tag)
        return subprocess.CompletedProcess(command, 0, f"deadbeef\trefs/tags/{tag}\n", "")

    assert MODULE.verify(root, runner=runner) == (
        "open-pharma-plugins-search-v1.0.1",
        "open-pharma-plugins-training-v1.0.2",
    )
    assert checked == [
        "open-pharma-plugins-search-v1.0.1",
        "open-pharma-plugins-training-v1.0.2",
    ]


def test_verify_reports_all_catalog_refs_missing_from_the_public_remote(tmp_path):
    root = _catalog_root(tmp_path)

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(ValueError, match="missing remote catalog tag.*search.*training"):
        MODULE.verify(root, runner=runner)


def test_verify_rejects_claude_and_copilot_catalog_ref_drift(tmp_path):
    root = _catalog_root(tmp_path)
    catalog_path = root / ".github/plugin/marketplace.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["plugins"][1]["source"]["ref"] = "open-pharma-plugins-training-v9.9.9"
    catalog_path.write_text(json.dumps(catalog) + "\n")

    with pytest.raises(ValueError, match="catalog drift.*training"):
        MODULE.verify(root, runner=lambda *_args, **_kwargs: pytest.fail("remote check must not run"))


def test_verify_rejects_claude_catalog_repository_drift(tmp_path):
    root = _catalog_root(tmp_path)
    catalog_path = root / ".claude-plugin/marketplace.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["plugins"][1]["source"]["url"] = "https://github.com/example/redirected-catalog.git"
    catalog_path.write_text(json.dumps(catalog) + "\n")

    with pytest.raises(ValueError, match="canonical repository.*training"):
        MODULE.verify(root, runner=lambda *_args, **_kwargs: pytest.fail("remote check must not run"))


def test_verify_uses_a_credential_free_noninteractive_public_git_probe(tmp_path):
    root = _catalog_root(tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        tag = command[-1].removeprefix("refs/tags/")
        return subprocess.CompletedProcess(command, 0, f"deadbeef\trefs/tags/{tag}\n", "")

    MODULE.verify(root, runner=runner)

    assert len(calls) == 2
    for command, kwargs in calls:
        assert command[:8] == [
            "git",
            "-c",
            "credential.helper=",
            "-c",
            "core.askPass=",
            "ls-remote",
            "--refs",
            "https://github.com/PharmaGenAI/open-pharma-plugins.git",
        ]
        assert command[-1].startswith("refs/tags/open-pharma-plugins-")
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert "GIT_ASKPASS" not in env
        assert "GITHUB_TOKEN" not in env
        assert "GH_TOKEN" not in env
