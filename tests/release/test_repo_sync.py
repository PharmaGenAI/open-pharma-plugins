"""Guards against silent drift between capability manifests, plugin versions, and marketplace."""

import json
import pathlib

import pytest
from conftest import REPO_ROOT

_ROOT = pathlib.Path(REPO_ROOT)
_CAPS_DIR = _ROOT / "src" / "capabilities"


def _capabilities() -> list[str]:
    return sorted(p.parent.parent.name for p in _CAPS_DIR.glob("*/.claude-plugin/plugin.json"))


def _load(cap: str, rel: str) -> dict:
    return json.loads((_CAPS_DIR / cap / rel).read_text())


def _server_capabilities() -> list[str]:
    return [c for c in _capabilities() if (_CAPS_DIR / c / ".mcp.json").exists()]


@pytest.mark.parametrize("cap", _capabilities())
def test_manifest_name_and_version_agree(cap):
    manifests = {
        "claude": _load(cap, ".claude-plugin/plugin.json"),
        "codex": _load(cap, ".codex-plugin/plugin.json"),
    }
    names = {m["name"] for m in manifests.values()}
    assert len(names) == 1, f"{cap}: plugin name differs across harness manifests: {names}"
    versions = {label: m["version"] for label, m in manifests.items()}
    assert len(set(versions.values())) == 1, f"{cap}: plugin version differs across its harness manifests: {versions}."


@pytest.mark.parametrize("cap", _capabilities())
def test_manifests_bundle_every_component_the_capability_ships(cap):
    manifests = {
        "claude": _load(cap, ".claude-plugin/plugin.json"),
        "codex": _load(cap, ".codex-plugin/plugin.json"),
    }
    for label, manifest in manifests.items():
        assert manifest.get("skills"), f"{cap}: {label} manifest omitted the capability skill"

    has_server = (_CAPS_DIR / cap / ".mcp.json").exists()
    if has_server:
        assert manifests["claude"].get("mcpServers"), f"{cap}: Claude manifest omitted its MCP"
        codex_mcp = manifests["codex"].get("mcpServers")
        assert codex_mcp in ("./.mcp.json", ["./skill"]) or codex_mcp is not None, (
            f"{cap}: Codex manifest must reference the MCP server"
        )


@pytest.mark.parametrize("cap", _server_capabilities())
def test_mcp_launch_spec_agrees(cap):
    def _sole_server_args(spec: dict) -> list:
        servers = spec["mcpServers"]
        assert len(servers) == 1, f"{cap}: expected one mcpServers entry, got {sorted(servers)}"
        return next(iter(servers.values()))["args"]

    claude_args = _sole_server_args(_load(cap, ".claude-plugin/plugin.json"))
    mcp_args = _sole_server_args(_load(cap, ".mcp.json"))
    assert claude_args == mcp_args, f"{cap}: uvx launch args drifted between .claude-plugin and .mcp.json"


def test_marketplace_lists_every_capability():
    import mcp_framework

    market = json.loads((_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    listed = {p["name"] for p in market["plugins"]}
    from_manifests = {_load(cap, ".claude-plugin/plugin.json")["name"] for cap in _capabilities()}
    assert listed == from_manifests, (
        "marketplace.json plugins must match the capability manifests.\n"
        f"  marketplace: {sorted(listed)}\n  manifests:   {sorted(from_manifests)}"
    )
    assert market["metadata"]["version"] == mcp_framework.__version__


def test_copilot_marketplace_matches_release_catalog():
    claude_market = json.loads((_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    copilot_market = json.loads((_ROOT / ".github" / "plugin" / "marketplace.json").read_text())
    versions = json.loads((_ROOT / "plugin-versions.json").read_text())["plugins"]

    assert copilot_market["name"] == claude_market["name"]
    assert copilot_market["owner"] == claude_market["owner"]
    assert copilot_market["metadata"] == claude_market["metadata"]
    assert {p["name"] for p in copilot_market["plugins"]} == {p["name"] for p in claude_market["plugins"]}

    for plugin in copilot_market["plugins"]:
        cap = plugin["name"].removeprefix("open-pharma-plugins-")
        assert plugin["version"] == versions[cap]
        assert plugin["source"] == {
            "source": "github",
            "repo": "PharmaGenAI/open-pharma-plugins",
            "path": f"src/capabilities/{cap}",
            "ref": f"open-pharma-plugins-{cap}-v{versions[cap]}",
        }
