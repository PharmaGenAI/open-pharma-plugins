"""Exercise every capability through the real MCP stdio protocol."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from open_pharma_plugins_campaign_studio._campaign_store import campaign_dir, save_brief

SERVERS = {
    "open_pharma_plugins_hcp_intelligence": ("list_accounts", {}),
    "open_pharma_plugins_field_training": ("list_documents", {}),
    "open_pharma_plugins_campaign_studio": (
        "retrieve_brand_components",
        {"campaign_brief_id": "protocol-smoke"},
    ),
    "open_pharma_plugins_next_best_engagement": ("load_universe", {"source": "fixture"}),
    "open_pharma_plugins_territory_alignment": ("ta_status", {}),
    "open_pharma_plugins_competitive_intelligence": ("ci_status", {}),
}


async def _probe(module: str, tool_name: str, arguments: dict) -> tuple[str, int]:
    params = StdioServerParameters(command=sys.executable, args=["-m", module])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool(tool_name, arguments)
            assert not result.isError
            assert result.content
            return initialized.serverInfo.name, len(tools.tools)


async def _probe_nbe_workflow(output_dir: Path) -> tuple[str, set[str]]:
    module = "open_pharma_plugins_next_best_engagement"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        env={**os.environ, "OPEN_PHARMA_NBE_OUTPUT_DIR": str(output_dir)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()

            loaded = await session.call_tool("load_universe", {"source": "fixture"})
            assert not loaded.isError
            recommended = await session.call_tool("recommend_engagements", {})
            assert not recommended.isError
            rendered = await session.call_tool("render_plan", {"format": "csv"})
            assert not rendered.isError
            assert rendered.content

            payload = json.loads(rendered.content[0].text)
            assert payload["status"] == "ok"
            assert payload["format"] == "csv"
            assert set(payload["files"]) == {"engagements_csv", "summary_csv"}
            root = output_dir.resolve()
            paths = [Path(raw_path).resolve() for raw_path in payload["files"].values()]
            assert len(paths) == len(set(paths))
            for path in paths:
                assert path.suffix == ".csv"
                assert path.is_relative_to(root)
                assert path.is_file()
                assert path.stat().st_size > 0
                if os.name != "nt":
                    assert path.stat().st_mode & 0o077 == 0

            return initialized.serverInfo.name, {tool.name for tool in tools.tools}


async def _probe_ta_workflow(scenarios_dir: Path) -> tuple[str, set[str]]:
    module = "open_pharma_plugins_territory_alignment"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        env={**os.environ, "OPEN_PHARMA_TA_SCENARIOS_DIR": str(scenarios_dir)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()

            aligned = await session.call_tool(
                "ta_align",
                {"scenario_name": "protocol_baseline", "max_iterations": 0},
            )
            assert not aligned.isError
            alignment = json.loads(aligned.content[0].text)
            assert "error" not in alignment
            assert alignment["metadata"]["run_id"]
            artifacts = alignment["metadata"]["artifacts"]
            report = Path(artifacts["primary_report"])
            assert report == scenarios_dir.parent / "protocol_baseline.html"
            assert report.is_file()
            assert report.stat().st_size > 0
            assert report.stat().st_mode & 0o077 == 0
            assert set(artifacts["advanced_exports"]) == {
                "scenario_json",
                "assignments_csv",
                "territory_summary_csv",
            }
            for raw_path in artifacts["advanced_exports"].values():
                path = Path(raw_path)
                assert path.is_file()
                assert path.stat().st_mode & 0o077 == 0
            report_html = report.read_text(encoding="utf-8")
            assert "https://" not in report_html
            assert "http://" not in report_html

            evaluated = await session.call_tool(
                "ta_evaluate",
                {"scenario_name": "protocol_baseline"},
            )
            assert not evaluated.isError
            evaluation = json.loads(evaluated.content[0].text)
            assert evaluation["run_id"] == alignment["metadata"]["run_id"]

            rep_id = alignment["assignments"][0]["primary_rep"]
            clustered = await session.call_tool(
                "ta_cluster",
                {
                    "scenario_name": "protocol_baseline",
                    "rep_id": rep_id,
                    "period": "next_week",
                },
            )
            assert not clustered.isError
            plan = json.loads(clustered.content[0].text)
            assert plan["scenario_name"] == "protocol_baseline"
            assert plan["planning_dates"]

            return initialized.serverInfo.name, {tool.name for tool in tools.tools}


@pytest.mark.parametrize("module", SERVERS)
def test_server_initializes_and_lists_tools(module):
    tool_name, arguments = SERVERS[module]
    server_name, tool_count = anyio.run(_probe, module, tool_name, arguments)
    assert server_name == module
    assert tool_count > 0


async def _malformed_claims_probe(tool_name: str, arguments: dict) -> dict:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "open_pharma_plugins_campaign_studio"],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            assert not result.isError
            assert result.content
            return json.loads(result.content[0].text)


async def _campaign_validation_probe(tool_name: str, arguments: dict):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "open_pharma_plugins_campaign_studio"],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments)


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "generate_channel_copy",
            {
                "campaign_brief_id": "malformed-mcp",
                "channel": "email",
                "copy_json": json.dumps(
                    {
                        "subject": {"text": "Claim", "claim_ids": ["efficacy"]},
                        "preheader": {"text": "Claim", "claim_ids": ["efficacy"]},
                        "headline": {"text": "Claim", "claim_ids": ["efficacy"]},
                        "body": [{"text": "Claim", "claim_ids": ["efficacy"]}],
                        "cta": {"text": "Learn more", "claim_ids": []},
                    }
                ),
            },
        ),
        (
            "generate_audience_journey",
            {
                "campaign_brief_id": "malformed-mcp",
                "journey": [
                    {
                        "stage": stage,
                        "objective": "Educate",
                        "key_messages": ["efficacy"],
                        "channels": ["email"],
                        "content_type": "promotional",
                        "kpi": "opens",
                    }
                    for stage in ("aware", "interested", "acting")
                ],
            },
        ),
        (
            "generate_message_architecture",
            {
                "campaign_brief_id": "malformed-mcp",
                "messages": [
                    {"tier": tier, "message": "Claim", "claim_ids": ["efficacy"], "rationale": "Why"}
                    for tier in ("primary", "secondary", "supporting")
                ],
                "fair_balance_statement": "Claim",
                "fair_balance_sources": [
                    {"document_id": "efficacy", "document_name": "Approved messages", "excerpt": "Section 1"}
                ],
            },
        ),
    ],
)
def test_campaign_mcp_returns_text_json_for_malformed_persisted_claims(
    tmp_path, monkeypatch, tool_name: str, arguments: dict
):
    """Malformed artifacts are normal validation results, never MCP protocol errors."""
    monkeypatch.setenv("OPEN_PHARMA_CAMPAIGN_STORE_DIR", str(tmp_path / "store"))
    save_brief(
        {
            "campaign_brief_id": "malformed-mcp",
            "campaign_name": "Protocol test",
            "brand": "TestDrug",
            "country": "US",
            "policy_jurisdiction": "FDA",
            "indication": "condition",
            "target_segment": "HCP",
            "mode": "promotional",
            "channels": ["email"],
            "call_to_action": "Learn more",
        }
    )
    (campaign_dir("malformed-mcp") / "approved-claims.json").write_text("{", encoding="utf-8")

    result = anyio.run(_malformed_claims_probe, tool_name, arguments)

    assert result["errors"]
    assert "approved claims artifact" in " ".join(result["errors"]).casefold()


_MALFORMED_MCP_BRAND_COMPONENTS = [
    pytest.param([], id="root-list"),
    pytest.param("not-a-mapping", id="root-string"),
    pytest.param(None, id="root-null"),
    pytest.param({"legal": []}, id="legal-list"),
    pytest.param({"legal": "not-a-mapping"}, id="legal-string"),
    pytest.param({"legal": None}, id="legal-null"),
    pytest.param(
        {
            "legal": {
                "isi": [],
                "pi_ref": "See prescribing information.",
                "reporting_statement": "Report adverse events.",
            }
        },
        id="required-value-list",
    ),
    pytest.param(
        {
            "legal": {
                "isi": {"text": "Important safety information."},
                "pi_ref": "See prescribing information.",
                "reporting_statement": "Report adverse events.",
            }
        },
        id="required-value-mapping",
    ),
]


def _seed_mcp_brand_validation(brief_id: str):
    save_brief(
        {
            "campaign_brief_id": brief_id,
            "campaign_name": "Protocol test",
            "brand": "TestDrug",
            "country": "US",
            "policy_jurisdiction": "FDA",
            "indication": "condition",
            "target_segment": "HCP",
            "mode": "promotional",
            "channels": ["email"],
            "call_to_action": "Learn more",
        }
    )
    campaign_path = campaign_dir(brief_id)
    (campaign_path / "approved-claims.json").write_text(
        json.dumps(
            [
                {
                    "claim_id": "efficacy",
                    "text": "TestDrug reduces exacerbations by 20%.",
                    "category": "efficacy",
                    "source_document": "Approved messages",
                    "source_reference": "Section 1",
                    "approval_status": "approved",
                    "effective_from": "2020-01-01",
                    "expiry": None,
                    "jurisdictions": ["US"],
                    "indications": ["condition"],
                    "audiences": ["HCP"],
                    "channels": ["email"],
                    "allowed_variants": [],
                    "restrictions": None,
                }
            ]
        ),
        encoding="utf-8",
    )
    return campaign_path


@pytest.mark.parametrize("manifest", _MALFORMED_MCP_BRAND_COMPONENTS)
def test_campaign_mcp_returns_text_json_for_malformed_brand_components(tmp_path, monkeypatch, manifest: object):
    """The real MCP server must fail malformed manifest persistence before validation writes."""
    monkeypatch.setenv("OPEN_PHARMA_CAMPAIGN_STORE_DIR", str(tmp_path / "store"))
    brief_id = "malformed-brand-mcp"
    campaign_path = _seed_mcp_brand_validation(brief_id)
    (campaign_path / "brand-components.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = anyio.run(
        _malformed_claims_probe,
        "validate_claims_and_fair_balance",
        {"campaign_brief_id": brief_id, "channels": ["email"]},
    )

    assert result["errors"]
    assert "brand-components" in " ".join(result["errors"]).casefold()
    for name in ("policy-checks.json", "claim-map.json", "source-evidence.json"):
        assert not (campaign_path / "validation" / name).exists()


@pytest.mark.parametrize(
    ("artifact_bytes", "case"),
    [
        pytest.param(b"{", "invalid-json", id="invalid-json"),
        pytest.param(b"\xff", "invalid-utf8", id="invalid-utf8"),
    ],
)
def test_campaign_mcp_returns_text_json_for_unreadable_brand_components_without_writes(
    tmp_path, monkeypatch, artifact_bytes: bytes, case: str
):
    """Raw JSON/UTF-8 corruption must remain a normal MCP text result without validation writes."""
    monkeypatch.setenv("OPEN_PHARMA_CAMPAIGN_STORE_DIR", str(tmp_path / "store"))
    brief_id = f"unreadable-brand-mcp-{case}"
    campaign_path = _seed_mcp_brand_validation(brief_id)
    (campaign_path / "brand-components.json").write_bytes(artifact_bytes)

    protocol_result = anyio.run(
        _campaign_validation_probe,
        "validate_claims_and_fair_balance",
        {"campaign_brief_id": brief_id, "channels": ["email"]},
    )

    assert protocol_result.isError is False
    assert len(protocol_result.content) == 1
    assert protocol_result.content[0].type == "text"
    result = json.loads(protocol_result.content[0].text)
    assert result["errors"]
    assert "brand-components" in " ".join(result["errors"]).casefold()
    for name in ("policy-checks.json", "claim-map.json", "source-evidence.json"):
        assert not (campaign_path / "validation" / name).exists()


def test_next_best_engagement_runs_exact_csv_workflow_in_one_stdio_session(tmp_path):
    server_name, tools = anyio.run(_probe_nbe_workflow, tmp_path / "nbe-output")

    assert server_name == "open_pharma_plugins_next_best_engagement"
    assert tools == {"load_universe", "recommend_engagements", "render_plan"}


def test_territory_alignment_runs_snapshot_workflow_in_one_stdio_session(tmp_path):
    server_name, tools = anyio.run(_probe_ta_workflow, tmp_path / "ta-scenarios")

    assert server_name == "open_pharma_plugins_territory_alignment"
    assert tools == {"ta_status", "ta_align", "ta_evaluate", "ta_compare", "ta_cluster", "ta_visualize"}
