"""Semantic, read-only workflow checks for Campaign Studio status."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from open_pharma_plugins_campaign_studio._campaign_store import (
    load_artifact,
    save_artifact,
    save_brief,
    store_root_path,
)
from open_pharma_plugins_campaign_studio._inputs import preflight_inputs
from open_pharma_plugins_campaign_studio.tools.generate_audience_journey import handle as generate_journey
from open_pharma_plugins_campaign_studio.tools.generate_channel_copy import handle as generate_copy
from open_pharma_plugins_campaign_studio.tools.generate_message_architecture import handle as generate_messages
from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle as get_status


@pytest.fixture(autouse=True)
def campaign_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_PHARMA_CAMPAIGN_STORE_DIR", str(tmp_path / "store"))


def _result(blocks: list[dict]) -> dict:
    return json.loads(blocks[0]["text"])


def _brief(campaign_brief_id: str, claims_path: Path, kit_path: Path) -> dict:
    """A complete persisted brief, not the deliberately sparse tool-test fixture."""
    return {
        "campaign_brief_id": campaign_brief_id,
        "campaign_name": "Semantic status campaign",
        "country": "US",
        "policy_jurisdiction": "FDA",
        "mode": "promotional",
        "brand": "ONCORIX",
        "indication": "oncology",
        "lifecycle_stage": "growth",
        "target_segment": "HCP",
        "behavioral_objective": "Review the approved ONCORIX evidence.",
        "educational_objective": None,
        "desired_kpi": ["open_rate"],
        "approved_claims_path": str(claims_path),
        "demo_mode": False,
        "call_to_action": "Learn more",
        "call_to_action_url": "https://example.test/oncoriX",
        "channels": ["email"],
        "asset_dimensions": None,
        "brand_kit_path": str(kit_path),
        "language": "en",
        "localisation_notes": None,
        "required_safety_content": [],
        "required_legal_content": [],
        "delivery_constraints": None,
        "approval_workflow": "mlr_standard",
        "generated_at": "2026-08-28T00:00:00+00:00",
    }


def _stage(name: str, claim_id: str) -> dict:
    return {
        "stage": name,
        "objective": f"Move the HCP to {name}.",
        "key_messages": [claim_id],
        "channels": ["email"],
        "content_type": "promotional",
        "kpi": f"{name}_engagement",
    }


def _message_arguments(campaign_brief_id: str, first_stage: str | None) -> dict:
    claims = {claim["claim_id"]: claim for claim in load_artifact(campaign_brief_id, "approved-claims.json")}
    return {
        "campaign_brief_id": campaign_brief_id,
        "messages": [
            {
                "tier": "primary",
                "message": claims["c-001"]["text"],
                "claim_ids": ["c-001"],
                "audience_stage": first_stage,
                "rationale": "Lead with the primary endpoint after validating the current journey.",
            },
            {
                "tier": "secondary",
                "message": claims["c-002"]["text"],
                "claim_ids": ["c-002"],
                "audience_stage": None if first_stage is None else "interested",
                "rationale": "Add response evidence.",
            },
            {
                "tier": "supporting",
                "message": claims["c-003"]["text"],
                "claim_ids": ["c-003"],
                "audience_stage": None if first_stage is None else "acting",
                "rationale": "Add progression-free survival evidence.",
            },
        ],
        "fair_balance_statement": claims["c-006"]["text"],
        "fair_balance_sources": [
            {
                "document_id": "c-006",
                "document_name": claims["c-006"]["source_document"],
                "excerpt": claims["c-006"]["source_reference"],
            }
        ],
    }


def _seed_complete_workflow(tmp_path: Path, campaign_brief_id: str) -> tuple[Path, Path]:
    """Persist a real writer-produced workflow so each mutation has one cause."""
    fixture_root = Path(str(files("open_pharma_plugins_campaign_studio") / "fixtures"))
    claims_path = tmp_path / f"{campaign_brief_id}-claims.json"
    claims_path.write_bytes((fixture_root / "sample_approved_claims.json").read_bytes())
    kit_path = tmp_path / f"{campaign_brief_id}-brand-kit"
    shutil.copytree(fixture_root / "brand_kit", kit_path)
    save_brief(_brief(campaign_brief_id, claims_path, kit_path))
    assert preflight_inputs(campaign_brief_id, str(claims_path), str(kit_path), False)["ready"] is True

    journey_result = _result(
        generate_journey(
            {
                "campaign_brief_id": campaign_brief_id,
                "journey": [_stage("aware", "c-001"), _stage("interested", "c-002"), _stage("acting", "c-003")],
            }
        )
    )
    assert "errors" not in journey_result

    claims = {claim["claim_id"]: claim for claim in load_artifact(campaign_brief_id, "approved-claims.json")}
    messages_result = _result(
        generate_messages(
            {
                "campaign_brief_id": campaign_brief_id,
                "messages": [
                    {
                        "tier": "primary",
                        "message": claims["c-001"]["text"],
                        "claim_ids": ["c-001"],
                        "audience_stage": "aware",
                        "rationale": "Lead with the primary endpoint.",
                    },
                    {
                        "tier": "secondary",
                        "message": claims["c-002"]["text"],
                        "claim_ids": ["c-002"],
                        "audience_stage": "interested",
                        "rationale": "Add response evidence.",
                    },
                    {
                        "tier": "supporting",
                        "message": claims["c-003"]["text"],
                        "claim_ids": ["c-003"],
                        "audience_stage": "acting",
                        "rationale": "Add progression-free survival evidence.",
                    },
                ],
                "fair_balance_statement": claims["c-006"]["text"],
                "fair_balance_sources": [
                    {
                        "document_id": "c-006",
                        "document_name": claims["c-006"]["source_document"],
                        "excerpt": claims["c-006"]["source_reference"],
                    }
                ],
            }
        )
    )
    assert "errors" not in messages_result

    copy_result = _result(
        generate_copy(
            {
                "campaign_brief_id": campaign_brief_id,
                "channel": "email",
                "copy_json": json.dumps(
                    {
                        "subject": {"text": claims["c-001"]["text"], "claim_ids": ["c-001"]},
                        "preheader": {"text": claims["c-002"]["text"], "claim_ids": ["c-002"]},
                        "headline": {"text": claims["c-003"]["text"], "claim_ids": ["c-003"]},
                        "body": [{"text": claims["c-004"]["text"], "claim_ids": ["c-004"]}],
                        "cta": {"text": "Learn more", "claim_ids": []},
                    }
                ),
            }
        )
    )
    assert "errors" not in copy_result
    return claims_path, kit_path


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


Mutator = Callable[[str, Path], None]


def _artifact_mutator(filename: str, mutate: Callable[[object], object]) -> Mutator:
    def apply(campaign_brief_id: str, _kit_path: Path) -> None:
        save_artifact(campaign_brief_id, filename, mutate(load_artifact(campaign_brief_id, filename)))

    return apply


def _replace_list(value: object) -> object:
    assert isinstance(value, list)
    return [{"claim_id": "c-001"}]


def _journey_channel(value: object) -> object:
    assert isinstance(value, dict)
    value["stages"][0]["channels"] = ["banner"]
    return value


def _journey_claim(value: object) -> object:
    assert isinstance(value, dict)
    value["stages"][0]["key_messages"] = ["unknown"]
    return value


def _message_tier(value: object) -> object:
    assert isinstance(value, dict)
    value["message_tiers"][0]["tier"] = "invalid"
    return value


def _message_balance(value: object) -> object:
    assert isinstance(value, dict)
    value["fair_balance_statement"] = "Ungrounded safety language."
    return value


def _copy_channel(value: object) -> object:
    assert isinstance(value, dict)
    value["channel"] = "banner"
    return value


def _copy_claim(value: object) -> object:
    assert isinstance(value, dict)
    value["copy"]["headline"]["claim_ids"] = ["unknown"]
    return value


def _copy_cta(value: object) -> object:
    assert isinstance(value, dict)
    value["copy"]["cta"]["text"] = "Different CTA"
    return value


def _brand_missing_legal(_campaign_brief_id: str, kit_path: Path) -> None:
    (kit_path / "legal.json").unlink()


def _provenance_mismatch(campaign_brief_id: str, kit_path: Path) -> None:
    provenance = load_artifact(campaign_brief_id, "input-provenance.json")
    provenance["brand_kit"]["resolved_path"] = str(kit_path / "elsewhere")
    save_artifact(campaign_brief_id, "input-provenance.json", provenance)


_CASES: tuple[tuple[str, Mutator, str, str, dict[str, str | None]], ...] = (
    (
        "invalid-brief-country",
        _artifact_mutator("campaign-brief.json", lambda value: {**value, "country": "USA"}),
        "campaign-brief.json",
        "brief",
        {"tool": "create_campaign_brief", "channel": None},
    ),
    (
        "minimal-claims",
        _artifact_mutator("approved-claims.json", _replace_list),
        "approved-claims.json",
        "preflight_inputs",
        {"tool": "preflight_campaign_inputs", "channel": None},
    ),
    (
        "missing-brand-file",
        _brand_missing_legal,
        "brand-components.json",
        "preflight_inputs",
        {"tool": "preflight_campaign_inputs", "channel": None},
    ),
    (
        "mismatched-provenance",
        _provenance_mismatch,
        "input-provenance.json",
        "preflight_inputs",
        {"tool": "preflight_campaign_inputs", "channel": None},
    ),
    (
        "empty-journey",
        _artifact_mutator("audience-journey.json", lambda value: {**value, "stages": []}),
        "audience-journey.json",
        "audience_journey",
        {"tool": "generate_audience_journey", "channel": None},
    ),
    (
        "journey-wrong-id",
        _artifact_mutator("audience-journey.json", lambda value: {**value, "campaign_brief_id": "other"}),
        "audience-journey.json",
        "audience_journey",
        {"tool": "generate_audience_journey", "channel": None},
    ),
    (
        "journey-wrong-channel",
        _artifact_mutator("audience-journey.json", _journey_channel),
        "audience-journey.json",
        "audience_journey",
        {"tool": "generate_audience_journey", "channel": None},
    ),
    (
        "journey-unknown-claim",
        _artifact_mutator("audience-journey.json", _journey_claim),
        "audience-journey.json",
        "audience_journey",
        {"tool": "generate_audience_journey", "channel": None},
    ),
    (
        "empty-message-architecture",
        _artifact_mutator("message-architecture.json", lambda value: {**value, "message_tiers": []}),
        "message-architecture.json",
        "message_architecture",
        {"tool": "generate_message_architecture", "channel": None},
    ),
    (
        "message-wrong-tier",
        _artifact_mutator("message-architecture.json", _message_tier),
        "message-architecture.json",
        "message_architecture",
        {"tool": "generate_message_architecture", "channel": None},
    ),
    (
        "message-bad-fair-balance",
        _artifact_mutator("message-architecture.json", _message_balance),
        "message-architecture.json",
        "message_architecture",
        {"tool": "generate_message_architecture", "channel": None},
    ),
    (
        "empty-email-copy",
        _artifact_mutator("copy-email.json", lambda value: {**value, "copy": {}}),
        "copy-email.json",
        "copy:email",
        {"tool": "generate_channel_copy", "channel": "email"},
    ),
    (
        "copy-wrong-id",
        _artifact_mutator("copy-email.json", lambda value: {**value, "campaign_brief_id": "other"}),
        "copy-email.json",
        "copy:email",
        {"tool": "generate_channel_copy", "channel": "email"},
    ),
    (
        "copy-wrong-channel",
        _artifact_mutator("copy-email.json", _copy_channel),
        "copy-email.json",
        "copy:email",
        {"tool": "generate_channel_copy", "channel": "email"},
    ),
    (
        "copy-unknown-claim",
        _artifact_mutator("copy-email.json", _copy_claim),
        "copy-email.json",
        "copy:email",
        {"tool": "generate_channel_copy", "channel": "email"},
    ),
    (
        "copy-wrong-cta",
        _artifact_mutator("copy-email.json", _copy_cta),
        "copy-email.json",
        "copy:email",
        {"tool": "generate_channel_copy", "channel": "email"},
    ),
)


def test_status_validates_every_persisted_workflow_contract_without_writes(tmp_path: Path):
    """Status is a semantic reader: every independently corrupt artifact reopens its own workflow step."""
    cases: list[tuple[str, str, str, dict[str, str | None]]] = []
    for name, mutate, artifact, missing_step, next_step in _CASES:
        campaign_brief_id = f"semantic-{name}"
        source_dir = tmp_path / campaign_brief_id
        source_dir.mkdir()
        _claims_path, kit_path = _seed_complete_workflow(source_dir, campaign_brief_id)
        mutate(campaign_brief_id, kit_path)
        snapshot = _snapshot(tmp_path)
        direct = _result(get_status({"campaign_brief_id": campaign_brief_id}))
        assert direct["artifact_diagnostics"][artifact]["status"] == "invalid"
        assert missing_step in direct["missing_steps"]
        assert direct["next_step"] == next_step
        assert _snapshot(tmp_path) == snapshot
        cases.append((campaign_brief_id, artifact, missing_step, next_step))

    mcp_snapshot = _snapshot(tmp_path)

    async def exercise() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "open_pharma_plugins_campaign_studio"],
            env={**os.environ, "OPEN_PHARMA_CAMPAIGN_STORE_DIR": str(store_root_path())},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for campaign_brief_id, artifact, missing_step, next_step in cases:
                    response = await session.call_tool("get_campaign_status", {"campaign_brief_id": campaign_brief_id})
                    assert response.isError is False
                    status = json.loads(response.content[0].text)
                    assert status["artifact_diagnostics"][artifact]["status"] == "invalid"
                    assert missing_step in status["missing_steps"]
                    assert status["next_step"] == next_step
                    assert _snapshot(tmp_path) == mcp_snapshot

    anyio.run(exercise)


def test_message_writer_requires_current_journey_for_staged_messages_direct_and_mcp(tmp_path: Path):
    """Removing the writer's journey check must permit stale stage links to be persisted again."""
    cases: dict[str, dict] = {}
    for suffix in ("unknown-stage", "missing-journey", "unreadable-journey", "wrong-journey-id"):
        campaign_brief_id = f"writer-{suffix}"
        source_dir = tmp_path / campaign_brief_id
        source_dir.mkdir()
        _seed_complete_workflow(source_dir, campaign_brief_id)
        cases[suffix] = {
            "campaign_brief_id": campaign_brief_id,
            "arguments": _message_arguments(campaign_brief_id, "unaware" if suffix == "unknown-stage" else "aware"),
        }

    store = store_root_path()
    missing_dir = store / "campaigns" / cases["missing-journey"]["campaign_brief_id"]
    (missing_dir / "audience-journey.json").unlink()
    (missing_dir / "message-architecture.json").unlink()
    unreadable_dir = store / "campaigns" / cases["unreadable-journey"]["campaign_brief_id"]
    (unreadable_dir / "audience-journey.json").write_text("{", encoding="utf-8")
    wrong_id = cases["wrong-journey-id"]["campaign_brief_id"]
    wrong_journey = load_artifact(wrong_id, "audience-journey.json")
    wrong_journey["campaign_brief_id"] = "another-campaign"
    save_artifact(wrong_id, "audience-journey.json", wrong_journey)

    expected_available = {
        "unknown-stage": ["aware", "interested", "acting"],
        "missing-journey": [],
        "unreadable-journey": [],
        "wrong-journey-id": [],
    }
    architecture_paths = {
        suffix: store / "campaigns" / case["campaign_brief_id"] / "message-architecture.json"
        for suffix, case in cases.items()
    }
    before = {suffix: path.read_bytes() if path.exists() else None for suffix, path in architecture_paths.items()}

    for suffix, case in cases.items():
        response = _result(generate_messages(case["arguments"]))
        assert response["available_stages"] == expected_available[suffix]
        assert response["errors"]
        if suffix == "unknown-stage":
            assert response["errors"] == ["Message 1: audience_stage 'unaware' is not present in the audience journey."]
        else:
            assert response["errors"][0] == (
                "audience_stage requires a current valid audience journey. Run generate_audience_journey first."
            )
        path = architecture_paths[suffix]
        assert (path.read_bytes() if path.exists() else None) == before[suffix]

    async def exercise() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "open_pharma_plugins_campaign_studio"],
            env={**os.environ, "OPEN_PHARMA_CAMPAIGN_STORE_DIR": str(store)},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for suffix, case in cases.items():
                    response = await session.call_tool("generate_message_architecture", case["arguments"])
                    assert response.isError is False
                    data = json.loads(response.content[0].text)
                    assert data["available_stages"] == expected_available[suffix]
                    assert data["errors"]

    anyio.run(exercise)
    for suffix, path in architecture_paths.items():
        assert (path.read_bytes() if path.exists() else None) == before[suffix]


def test_message_writer_accepts_current_stage_and_preserves_null_stage_compatibility_direct_and_mcp(tmp_path: Path):
    """Rejecting every absent journey would break documented stage-optional workflows."""
    valid_id = "writer-valid-stage"
    valid_dir = tmp_path / valid_id
    valid_dir.mkdir()
    _seed_complete_workflow(valid_dir, valid_id)
    valid_arguments = _message_arguments(valid_id, "aware")

    null_id = "writer-null-stages"
    null_dir = tmp_path / null_id
    null_dir.mkdir()
    _seed_complete_workflow(null_dir, null_id)
    null_campaign_dir = store_root_path() / "campaigns" / null_id
    (null_campaign_dir / "audience-journey.json").unlink()
    (null_campaign_dir / "message-architecture.json").unlink()
    null_arguments = _message_arguments(null_id, None)

    assert "errors" not in _result(generate_messages(valid_arguments))
    assert (
        _result(get_status({"campaign_brief_id": valid_id}))["artifact_diagnostics"]["message-architecture.json"][
            "status"
        ]
        == "current"
    )
    assert "errors" not in _result(generate_messages(null_arguments))
    null_status = _result(get_status({"campaign_brief_id": null_id}))
    assert null_status["artifact_diagnostics"]["message-architecture.json"]["status"] == "current"
    assert null_status["next_step"] == {"tool": "generate_audience_journey", "channel": None}

    async def exercise() -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "open_pharma_plugins_campaign_studio"],
            env={**os.environ, "OPEN_PHARMA_CAMPAIGN_STORE_DIR": str(store_root_path())},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for arguments in (valid_arguments, null_arguments):
                    response = await session.call_tool("generate_message_architecture", arguments)
                    assert response.isError is False
                    assert "errors" not in json.loads(response.content[0].text)
                status_response = await session.call_tool("get_campaign_status", {"campaign_brief_id": null_id})
                null_status = json.loads(status_response.content[0].text)
                assert null_status["artifact_diagnostics"]["message-architecture.json"]["status"] == "current"
                assert null_status["next_step"] == {"tool": "generate_audience_journey", "channel": None}

    anyio.run(exercise)
