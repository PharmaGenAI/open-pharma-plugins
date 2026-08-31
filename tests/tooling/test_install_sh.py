"""Regression checks for the installer's non-interactive helper functions."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from shared.env import CONFIG_FIELDS

ROOT = Path(__file__).resolve().parents[2]


def _bash(script: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "NO_COLOR": "1", **env_overrides}
    return subprocess.run(
        ["bash", "-c", f"source ./install.sh --help >/dev/null; {script}"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_run_cmd_propagates_command_failure():
    result = _bash('OPP_DRY=0; run_cmd false >/dev/null 2>&1; test "$?" -eq 1')
    assert result.returncode == 0, result.stderr


def test_opp_dry_skips_command_execution():
    result = _bash("OPP_DRY=1; run_cmd false >/dev/null 2>&1")
    assert result.returncode == 0, result.stderr


def test_opp_repo_env_override_sets_install_source():
    repo = "https://example.test/open-pharma-plugins.git"
    result = _bash('printf "%s\\n" "$REPO_URL"', OPP_REPO=repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == repo


def test_opp_ref_env_override_selects_requested_tag():
    tag = "open-pharma-plugins-field-training-v1.0.1"
    result = _bash("cap_ref field-training", OPP_REF=tag)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == tag


def test_banner_uses_open_pharma_plugins_wordmark():
    result = _bash("term_cols() { printf 21; }; banner")
    assert result.returncode == 0, result.stderr
    assert "Open Pharma Plugins" in result.stdout


def test_harness_catalog_supports_claude_codex_and_copilot():
    result = _bash('printf "%s\\n" "$ALL_HARNESSES"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["claude", "codex", "copilot"]


def test_install_rejects_harness_outside_supported_catalog():
    result = _bash("confirm() { return 1; }; install_for other open-pharma-plugins-hcp-intelligence")
    assert result.returncode == 1
    assert "unsupported harness 'other' — supported harnesses: claude codex copilot" in result.stdout


def test_config_spec_lists_search_backend_selector_and_keys():
    result = _bash('printf "%s\\n" "${CONFIG_SPEC[@]}"')
    assert result.returncode == 0, result.stderr
    rows = result.stdout.splitlines()
    assert any(row.startswith("OPEN_PHARMA_SEARCH_BACKEND|0|search|auto|") for row in rows)
    assert any(row.startswith("SERPER_API_KEY|1|search||") for row in rows)
    assert any(row.startswith("TAVILY_API_KEY|1|search||") for row in rows)
    assert any(row.startswith("EXA_API_KEY|1|search||") for row in rows)


def test_config_spec_mirrors_shared_catalog():
    result = _bash('printf "%s\\n" "${CONFIG_SPEC[@]}"')
    assert result.returncode == 0, result.stderr
    actual = [row.split("|", 4) for row in result.stdout.splitlines()]
    group_tags = {
        "Web Search": "search",
        "HCP Intelligence": "hcp",
        "Field Training": "training",
        "Campaign Studio": "campaign",
        "Next Best Engagement": "nbe",
        "Territory Alignment": "territory",
        "Competitive Intelligence": "ci",
    }
    expected = [
        [key, str(int(secret)), group_tags[group], default, description]
        for key, secret, group, default, description in CONFIG_FIELDS
    ]
    assert actual == expected


def test_cap_spec_uses_file_url_for_local_checkout(tmp_path):
    checkout = tmp_path / "checkout with spaces"
    checkout.mkdir()
    result = _bash("REPO_URL=$TEST_REPO; cap_spec hcp-intelligence", TEST_REPO=str(checkout))
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"open-pharma-plugins[hcp-intelligence] @ file://{str(checkout).replace(' ', '%20')}"


def _make_local_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout with spaces"
    (checkout / ".claude-plugin").mkdir(parents=True)
    (checkout / ".github/plugin").mkdir(parents=True)
    (checkout / "scripts").mkdir()
    (checkout / "src/capabilities/hcp-intelligence/.claude-plugin").mkdir(parents=True)
    (checkout / "src/capabilities/field-training/.claude-plugin").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n")
    (checkout / "plugin-versions.json").write_text(
        json.dumps({"distribution": "1.0.1", "plugins": {"hcp-intelligence": "1.0.1", "field-training": "1.0.1"}})
        + "\n"
    )
    (checkout / ".claude-plugin/marketplace.json").write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "open-pharma-plugins-hcp-intelligence",
                        "source": {
                            "source": "git-subdir",
                            "url": "https://example.test/repo.git",
                            "path": "src/capabilities/hcp-intelligence",
                            "ref": "open-pharma-plugins-hcp-intelligence-v1.0.1",
                        },
                    },
                    {
                        "name": "open-pharma-plugins-field-training",
                        "source": {
                            "source": "git-subdir",
                            "url": "https://example.test/repo.git",
                            "path": "src/capabilities/field-training",
                            "ref": "open-pharma-plugins-field-training-v1.0.1",
                        },
                    },
                ]
            }
        )
        + "\n"
    )
    (checkout / ".github/plugin/marketplace.json").write_text(
        json.dumps(
            {
                "name": "open-pharma-plugins",
                "plugins": [
                    {
                        "name": "open-pharma-plugins-hcp-intelligence",
                        "version": "1.0.1",
                        "source": {
                            "source": "github",
                            "repo": "example/repo",
                            "path": "src/capabilities/hcp-intelligence",
                            "ref": "open-pharma-plugins-hcp-intelligence-v1.0.1",
                        },
                    },
                    {
                        "name": "open-pharma-plugins-field-training",
                        "version": "1.0.1",
                        "source": {
                            "source": "github",
                            "repo": "example/repo",
                            "path": "src/capabilities/field-training",
                            "ref": "open-pharma-plugins-field-training-v1.0.1",
                        },
                    },
                ],
            }
        )
        + "\n"
    )
    manifest = {
        "mcpServers": {
            "open-pharma-plugins-hcp-intelligence": {
                "command": "uvx",
                "args": [
                    "--from",
                    "open-pharma-plugins[hcp-intelligence] @ git+https://example.test/repo.git@open-pharma-plugins-hcp-intelligence-v1.0.1",
                    "open-pharma-plugins-hcp-intelligence",
                ],
            }
        }
    }
    for path in (
        checkout / "src/capabilities/hcp-intelligence/.claude-plugin/plugin.json",
        checkout / "src/capabilities/hcp-intelligence/.mcp.json",
    ):
        path.write_text(json.dumps(manifest) + "\n")
    (checkout / "src/capabilities/field-training/.claude-plugin/plugin.json").write_text(
        json.dumps(
            {
                "name": "open-pharma-plugins-field-training",
                "version": "1.0.1",
                "skills": ["./skill"],
            }
        )
        + "\n"
    )
    shutil.copy2(ROOT / "scripts/rewrite_plugin_sources.py", checkout / "scripts")
    return checkout


def test_rewrite_plugin_sources_localizes_catalog_and_mcp(tmp_path):
    checkout = _make_local_checkout(tmp_path)
    result = subprocess.run(
        [
            "python3",
            str(checkout / "scripts/rewrite_plugin_sources.py"),
            "--repo",
            str(checkout),
            "--refresh",
            "hcp-intelligence",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    marketplace = json.loads((checkout / ".claude-plugin/marketplace.json").read_text())
    sources = {item["name"]: item["source"] for item in marketplace["plugins"]}
    assert sources["open-pharma-plugins-hcp-intelligence"] == "./src/capabilities/hcp-intelligence"
    assert isinstance(sources["open-pharma-plugins-field-training"], dict)

    copilot_marketplace = json.loads((checkout / ".github/plugin/marketplace.json").read_text())
    copilot_sources = {item["name"]: item["source"] for item in copilot_marketplace["plugins"]}
    assert copilot_sources["open-pharma-plugins-hcp-intelligence"] == "./src/capabilities/hcp-intelligence"
    assert isinstance(copilot_sources["open-pharma-plugins-field-training"], dict)

    for path in (
        checkout / "src/capabilities/hcp-intelligence/.claude-plugin/plugin.json",
        checkout / "src/capabilities/hcp-intelligence/.mcp.json",
    ):
        args = next(iter(json.loads(path.read_text())["mcpServers"].values()))["args"]
        assert args[0] == "--refresh"
        assert f"open-pharma-plugins[hcp-intelligence] @ {checkout.as_uri()}" in args

    result = subprocess.run(
        [
            "python3",
            str(checkout / "scripts/rewrite_plugin_sources.py"),
            "--repo",
            str(checkout),
            "--restore",
            "hcp-intelligence",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    restored = (checkout / "src/capabilities/hcp-intelligence/.mcp.json").read_text()
    assert "@open-pharma-plugins-hcp-intelligence-v1.0.1" in restored
    assert "--refresh" not in restored
    copilot_marketplace = json.loads((checkout / ".github/plugin/marketplace.json").read_text())
    copilot_source = copilot_marketplace["plugins"][0]["source"]
    assert copilot_source == {
        "source": "github",
        "repo": "PharmaGenAI/open-pharma-plugins",
        "path": "src/capabilities/hcp-intelligence",
        "ref": "open-pharma-plugins-hcp-intelligence-v1.0.1",
    }


def test_rewrite_plugin_sources_all_skips_skill_only_manifests(tmp_path):
    checkout = _make_local_checkout(tmp_path)
    skill_manifest = checkout / "src/capabilities/field-training/.claude-plugin/plugin.json"
    original = skill_manifest.read_text()

    result = subprocess.run(
        [
            "python3",
            str(checkout / "scripts/rewrite_plugin_sources.py"),
            "--repo",
            str(checkout),
            "--refresh",
            "all",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert skill_manifest.read_text() == original

    marketplace = json.loads((checkout / ".claude-plugin/marketplace.json").read_text())
    sources = {item["name"]: item["source"] for item in marketplace["plugins"]}
    assert sources["open-pharma-plugins-hcp-intelligence"] == "./src/capabilities/hcp-intelligence"
    assert sources["open-pharma-plugins-field-training"] == "./src/capabilities/field-training"
    copilot_marketplace = json.loads((checkout / ".github/plugin/marketplace.json").read_text())
    copilot_sources = {item["name"]: item["source"] for item in copilot_marketplace["plugins"]}
    assert copilot_sources == {
        "open-pharma-plugins-hcp-intelligence": "./src/capabilities/hcp-intelligence",
        "open-pharma-plugins-field-training": "./src/capabilities/field-training",
    }


def test_local_restore_cli_restores_all_published_refs(tmp_path):
    checkout = _make_local_checkout(tmp_path)
    shutil.copy2(ROOT / "install.sh", checkout)
    localized = subprocess.run(
        [
            "python3",
            str(checkout / "scripts/rewrite_plugin_sources.py"),
            "--repo",
            str(checkout),
            "--refresh",
            "all",
        ],
        capture_output=True,
        text=True,
    )
    assert localized.returncode == 0, localized.stderr

    restored = subprocess.run(
        ["bash", "install.sh", "local", "--restore"],
        cwd=checkout,
        env={**os.environ, "HOME": str(tmp_path / "home"), "NO_COLOR": "1"},
        capture_output=True,
        text=True,
    )
    assert restored.returncode == 0, restored.stderr
    assert "restored published plugin refs" in restored.stdout

    marketplace = json.loads((checkout / ".claude-plugin/marketplace.json").read_text())
    sources = {item["name"]: item["source"] for item in marketplace["plugins"]}
    assert sources["open-pharma-plugins-hcp-intelligence"]["ref"] == "open-pharma-plugins-hcp-intelligence-v1.0.1"
    assert sources["open-pharma-plugins-field-training"]["ref"] == "open-pharma-plugins-field-training-v1.0.1"
    copilot_marketplace = json.loads((checkout / ".github/plugin/marketplace.json").read_text())
    copilot_sources = {item["name"]: item["source"] for item in copilot_marketplace["plugins"]}
    assert copilot_sources["open-pharma-plugins-hcp-intelligence"]["source"] == "github"
    assert copilot_sources["open-pharma-plugins-hcp-intelligence"]["repo"] == "PharmaGenAI/open-pharma-plugins"
    assert copilot_sources["open-pharma-plugins-field-training"]["ref"] == ("open-pharma-plugins-field-training-v1.0.1")
    manifest = (checkout / "src/capabilities/hcp-intelligence/.mcp.json").read_text()
    assert "@open-pharma-plugins-hcp-intelligence-v1.0.1" in manifest
    assert "--refresh" not in manifest


def test_local_checkout_root_comes_from_install_script_not_cwd(tmp_path):
    result = subprocess.run(
        ["bash", "-c", f"source {ROOT / 'install.sh'} --help >/dev/null; local_checkout_root"],
        cwd=tmp_path,
        env={**os.environ, "NO_COLOR": "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(ROOT)


def test_local_install_dry_run_does_not_rewrite_manifests(tmp_path):
    checkout = _make_local_checkout(tmp_path)
    manifest = checkout / "src/capabilities/hcp-intelligence/.mcp.json"
    original = manifest.read_text()
    result = _bash(
        'LOCAL_REPO_ROOT="$TEST_REPO"; REPO_URL="$TEST_REPO"; '
        "confirm() { return 1; }; install_for codex open-pharma-plugins-hcp-intelligence",
        TEST_REPO=str(checkout),
    )
    assert result.returncode == 0, result.stderr
    assert manifest.read_text() == original


@pytest.mark.parametrize("action", ["do_install", "do_update", "do_uninstall"])
def test_missing_harness_stops_before_plugin_selection(action):
    script = rf"""
screen() {{ :; }}
hr() {{ :; }}
pick_count=0
menu_pick() {{
  pick_count=$((pick_count + 1))
  if [ "$pick_count" -eq 1 ]; then PICK_I=0; PICK=claude
  else PICK_I=-1; PICK=''
  fi
}}
have() {{ return 1; }}
choose_caps() {{ printf 'UNEXPECTED capability selection\n'; }}
choose_caps_local() {{ printf 'UNEXPECTED capability selection\n'; }}
choose_caps_update() {{ printf 'UNEXPECTED capability selection\n'; }}
spin() {{ printf 'UNEXPECTED installed-plugin detection\n'; }}
pause() {{ printf 'PAUSED\n'; }}
{action}
"""
    result = _bash(script)
    assert result.returncode == 0, result.stderr
    assert "claude is not available — 'claude' is not installed or not on PATH" in result.stdout
    assert result.stdout.count("PAUSED") == 1
    assert "UNEXPECTED" not in result.stdout


def test_verify_separates_capabilities_and_summarizes_results():
    script = r"""
screen() { :; }
hr() { :; }
spin() { printf -v "$2" 'hcp-intelligence field-training'; }
load_caps() { MP_ITEMS=(hcp-intelligence field-training); MP_SEL=(1 1); }
multi_pick() { MP_STATUS=ok; }
ensure_uv() { return 0; }
uvx_cap() { printf 'checked %s\n' "$1"; [ "$1" != field-training ]; }
pause() { :; }
do_verify
"""
    result = _bash(script)
    assert result.returncode == 1
    for cap in ("hcp-intelligence", "field-training"):
        assert f"── open-pharma-plugins-{cap} " in result.stdout
    assert "checked hcp-intelligence" in result.stdout
    assert "checked field-training" in result.stdout
    assert "Verify summary" in result.stdout
    assert "passed       1" in result.stdout
    assert "failed       1" in result.stdout


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        ("claude", "claude plugin install open-pharma-plugins-hcp-intelligence@open-pharma-plugins"),
        ("codex", "codex plugin add open-pharma-plugins-hcp-intelligence@open-pharma-plugins"),
        ("copilot", "copilot plugin install open-pharma-plugins-hcp-intelligence@open-pharma-plugins"),
    ],
)
def test_local_install_uses_each_harness_native_command(tmp_path, harness, expected):
    checkout = _make_local_checkout(tmp_path)
    result = _bash(
        'LOCAL_REPO_ROOT="$TEST_REPO"; REPO_URL="$TEST_REPO"; '
        f"confirm() {{ return 1; }}; install_for {harness} open-pharma-plugins-hcp-intelligence",
        TEST_REPO=str(checkout),
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    assert str(checkout) in result.stdout


def test_copilot_local_install_reuses_matching_marketplace(tmp_path):
    checkout = _make_local_checkout(tmp_path)
    script = r"""
confirm() { return 0; }
localize_plugin_sources() { :; }
copilot() {
  if [ "$*" = "plugin marketplace list" ]; then
    printf '  • open-pharma-plugins (Local: %s)\n' "$TEST_REPO"
  fi
}
REPO_URL="$TEST_REPO"
install_for copilot open-pharma-plugins-hcp-intelligence
"""
    result = _bash(script, TEST_REPO=str(checkout))
    assert result.returncode == 0, result.stderr
    assert "plugin marketplace add" not in result.stdout
    assert "copilot plugin install open-pharma-plugins-hcp-intelligence@open-pharma-plugins" in result.stdout


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        (
            "claude",
            (
                "claude plugin marketplace update open-pharma-plugins",
                "claude plugin update open-pharma-plugins-hcp-intelligence@open-pharma-plugins",
            ),
        ),
        (
            "codex",
            (
                "codex plugin marketplace upgrade open-pharma-plugins",
                "codex plugin add open-pharma-plugins-hcp-intelligence@open-pharma-plugins",
            ),
        ),
        (
            "copilot",
            (
                "copilot plugin marketplace update open-pharma-plugins",
                "copilot plugin update open-pharma-plugins-hcp-intelligence",
            ),
        ),
    ],
)
def test_update_uses_each_harness_native_refresh_path(harness, expected):
    result = _bash(
        "configured_marketplace_source() { printf remote; }; "
        f"confirm() {{ return 1; }}; update_for {harness} open-pharma-plugins-hcp-intelligence"
    )
    assert result.returncode == 0, result.stderr
    for command in expected:
        assert command in result.stdout


def test_stable_update_rejects_a_local_marketplace(tmp_path):
    result = _bash(
        'configured_marketplace_source() { printf "%s" "$TEST_REPO"; }; update_for codex open-pharma-plugins-hcp-intelligence',
        TEST_REPO=str(tmp_path),
    )
    assert result.returncode == 1
    assert "currently uses the local marketplace" in result.stdout


def test_cap_spec_defaults_to_capability_stable_tag():
    result = _bash("REPO_REF=; cap_spec field-training")
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("@open-pharma-plugins-field-training-v1.1.1")


def test_explicit_ref_overrides_package_and_marketplace():
    result = _bash(
        'REPO_REF=open-pharma-plugins-field-training-v1.0.0; printf "%s\\n" "$(cap_spec field-training)" "$(marketplace_source)"'
    )
    assert result.returncode == 0, result.stderr
    package, marketplace = result.stdout.splitlines()
    assert package.endswith("@open-pharma-plugins-field-training-v1.0.0")
    assert marketplace.endswith(".git#open-pharma-plugins-field-training-v1.0.0")


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        ("claude", "/reload-plugins"),
        ("codex", "start a new task"),
        ("copilot", "restart Copilot"),
    ],
)
def test_post_update_hint_explains_how_to_activate_updated_components(harness, expected):
    result = _bash(f"post_update_hint {harness}")
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout


def test_copilot_detection_recognizes_installed_marketplace_plugins():
    script = r"""
copilot() {
  printf 'Installed plugins:\n  open-pharma-plugins-hcp-intelligence@open-pharma-plugins\n'
}
MP_ITEMS=(hcp-intelligence field-training)
_detect_mask copilot
"""
    result = _bash(script)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "10"


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        ("  • open-pharma-plugins (GitHub: PharmaGenAI/open-pharma-plugins)", "remote"),
        ("  • open-pharma-plugins (Local: /tmp/open-pharma-plugins)", "/tmp/open-pharma-plugins"),
    ],
)
def test_copilot_marketplace_source_distinguishes_remote_and_local(listing, expected):
    result = _bash(
        'copilot() { printf "%s\\n" "$TEST_LISTING"; }; configured_marketplace_source copilot',
        TEST_LISTING=listing,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_copilot_uninstall_uses_native_command_and_removes_empty_marketplace():
    script = r"""
screen() { :; }
hr() { :; }
menu_pick() { PICK_I=0; PICK=copilot; }
copilot() { :; }
load_caps() { MP_ITEMS=(hcp-intelligence); MP_SEL=(0); MP_DIS=(0); MP_DESC=(x); }
spin() { printf -v "$2" 1; }
multi_pick() { MP_SEL=(1); MP_STATUS=ok; }
confirm() { return 1; }
pause() { :; }
box_open() { :; }
box_row() { :; }
box_close() { :; }
do_uninstall
"""
    result = _bash(script)
    assert result.returncode == 0, result.stderr
    assert "copilot plugin uninstall open-pharma-plugins-hcp-intelligence" in result.stdout
    assert "copilot plugin marketplace remove open-pharma-plugins" in result.stdout


def test_missing_uv_is_reported_without_downloading_or_executing_remote_code():
    result = _bash("have() { return 1; }; curl() { printf 'UNEXPECTED CURL'; }; ensure_uv; test \"$?\" -eq 1")
    assert result.returncode == 0, result.stderr
    assert "docs.astral.sh/uv/getting-started/installation" in result.stdout
    assert "UNEXPECTED CURL" not in result.stdout


@pytest.mark.parametrize("width", [28, 36, 52])
def test_capability_rows_never_wrap_at_narrow_terminal_widths(width):
    count_result = _bash('printf "%s\\n" "${#CAP_ITEMS[@]}"')
    cap_count = int(count_result.stdout.strip())
    result = _bash(f"term_cols() {{ printf {width}; }}; load_caps core; _multi_rows 0")
    assert result.returncode == 0, result.stderr
    _ansi_re = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    lines = [_ansi_re.sub("", line) for line in result.stdout.splitlines()]
    assert len(lines) == cap_count
    assert all(len(line) < width for line in lines), result.stdout


def test_installer_version_index_matches_release_index():
    versions = json.loads((ROOT / "plugin-versions.json").read_text())["plugins"]
    script = """
for cap in "${CAP_ITEMS[@]}"; do
  printf '%s=%s\\n' "$cap" "$(cap_version "$cap")"
done
"""
    result = _bash(script)
    assert result.returncode == 0, result.stderr
    from_installer = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert from_installer == versions
