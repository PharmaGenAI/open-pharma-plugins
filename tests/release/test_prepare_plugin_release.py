from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from conftest import REPO_ROOT

SCRIPT = Path(REPO_ROOT) / "scripts" / "prepare_plugin_release.py"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n")


def test_prepare_release_updates_copilot_catalog_with_version_and_tag(tmp_path):
    repo = tmp_path / "repo"
    cap = "search"
    plugin_name = "open-pharma-plugins-search"
    cap_dir = repo / "src/capabilities/search"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo / "scripts/prepare_plugin_release.py")
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src/mcp_framework.py").write_text('__version__ = "1.0.0"\n')
    (repo / "install.sh").write_text("CAP_ITEMS=(search)\nCAP_VERSIONS=(1.0.0)\n")
    _write_json(
        repo / "plugin-versions.json",
        {
            "distribution_version": "1.0.0",
            "tag_format": "open-pharma-plugins-{cap}-v{version}",
            "plugins": {cap: "1.0.0"},
        },
    )
    _write_json(
        repo / ".claude-plugin/marketplace.json",
        {
            "metadata": {"version": "1.0.0"},
            "plugins": [
                {
                    "name": plugin_name,
                    "source": {
                        "source": "git-subdir",
                        "url": "https://example.test/repo.git",
                        "path": "src/capabilities/search",
                        "ref": f"{plugin_name}-v1.0.0",
                    },
                }
            ],
        },
    )
    _write_json(
        repo / ".github/plugin/marketplace.json",
        {
            "name": "open-pharma-plugins",
            "metadata": {"version": "1.0.0"},
            "plugins": [
                {
                    "name": plugin_name,
                    "version": "1.0.0",
                    "source": {
                        "source": "github",
                        "repo": "example/repo",
                        "path": "src/capabilities/search",
                        "ref": f"{plugin_name}-v1.0.0",
                    },
                }
            ],
        },
    )
    args = [
        "--from",
        f"open-pharma-plugins[search] @ git+https://example.test/repo.git@{plugin_name}-v1.0.0",
        plugin_name,
    ]
    _write_json(
        cap_dir / ".claude-plugin/plugin.json",
        {"name": plugin_name, "version": "1.0.0", "mcpServers": {plugin_name: {"command": "uvx", "args": args}}},
    )
    _write_json(cap_dir / ".codex-plugin/plugin.json", {"name": plugin_name, "version": "1.0.0"})
    _write_json(cap_dir / ".mcp.json", {"mcpServers": {plugin_name: {"command": "uvx", "args": args}}})
    package = cap_dir / "open_pharma_plugins_search"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "1.0.0"\n')
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=repo, check=True, capture_output=True)

    result = subprocess.run(
        [
            "python3",
            "scripts/prepare_plugin_release.py",
            cap,
            "1.1.0",
            "--distribution-version",
            "1.0.1",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    copilot_marketplace = json.loads((repo / ".github/plugin/marketplace.json").read_text())
    entry = copilot_marketplace["plugins"][0]
    assert copilot_marketplace["metadata"]["version"] == "1.0.1"
    assert entry["version"] == "1.1.0"
    assert entry["source"] == {
        "source": "github",
        "repo": "PharmaGenAI/open-pharma-plugins",
        "path": "src/capabilities/search",
        "ref": "open-pharma-plugins-search-v1.1.0",
    }
