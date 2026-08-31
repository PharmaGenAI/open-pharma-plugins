"""Fail-closed Campaign Studio input-resolution contracts."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from open_pharma_plugins_campaign_studio._campaign_store import (
    load_artifact,
    load_brief,
    save_brief,
    store_root_path,
)
from open_pharma_plugins_campaign_studio._inputs import (
    current_brand_manifest,
    preflight_inputs,
    resolve_and_persist_brand_kit,
    resolve_and_persist_claims,
)
from open_pharma_plugins_campaign_studio._renderer import load_brand_kit
from open_pharma_plugins_campaign_studio.tools.preflight_campaign_inputs import handle as preflight
from open_pharma_plugins_campaign_studio.tools.retrieve_approved_claims import handle as retrieve_claims
from open_pharma_plugins_campaign_studio.tools.retrieve_brand_components import handle as retrieve_brand

_INPUT_RESULT_KEYS = {
    "campaign_brief_id",
    "ready",
    "demo_mode",
    "claims",
    "claims_count",
    "total_claims",
    "applicable_claim_count",
    "excluded_claims_count",
    "excluded_claim_count",
    "brand_files_count",
    "categories",
    "exclusions",
    "claims_path",
    "brand_kit_path",
    "brand_manifest",
    "provenance_path",
    "hashes",
    "warnings",
    "errors",
    "active_inputs",
    "candidate_inputs",
    "logo_path",
    "product_image_path",
    "palette",
    "typography",
    "legal",
    "files",
}


def _brief(campaign_brief_id: str, **overrides: object) -> dict:
    return {
        "campaign_brief_id": campaign_brief_id,
        "campaign_name": "Input contract test",
        "brand": "TestDrug",
        "country": "US",
        "policy_jurisdiction": "FDA",
        "indication": "test indication",
        "target_segment": "HCP",
        "channels": ["email"],
        "approved_claims_path": None,
        "brand_kit_path": None,
        "demo_mode": False,
        **overrides,
    }


def _create_brief_arguments(campaign_brief_id: str, **overrides: object) -> dict:
    return {
        "campaign_brief_id": campaign_brief_id,
        "campaign_name": "Writer validation contract",
        "country": "US",
        "policy_jurisdiction": "FDA",
        "mode": "promotional",
        "brand": "TestDrug",
        "indication": "oncology",
        "target_segment": "HCP",
        "behavioral_objective": "Review the approved evidence.",
        "desired_kpi": ["reach", "engagement"],
        "call_to_action": "Learn more",
        "call_to_action_url": "https://example.test/learn",
        "channels": ["email", "banner"],
        "demo_mode": True,
        **overrides,
    }


def _claim(claim_id: str = "claim-1", **overrides: object) -> dict:
    return {
        "claim_id": claim_id,
        "text": "TestDrug improved the primary endpoint.",
        "category": "efficacy",
        "source_document": "Approved messages",
        "source_reference": "Table 1",
        "approval_status": "approved",
        "effective_from": "2026-01-01",
        "expiry": None,
        "jurisdictions": ["FDA"],
        "indications": ["test indication"],
        "audiences": ["HCP"],
        "channels": ["email"],
        "allowed_variants": [],
        "restrictions": None,
        **overrides,
    }


def _brand_kit(path: Path) -> Path:
    path.mkdir()
    (path / "palette.json").write_text(
        json.dumps(
            {
                "primary": "#123456",
                "secondary": "#234567",
                "accent": "#345678",
                "text": "#111111",
                "text_light": "#ffffff",
                "background": "#ffffff",
                "background_alt": "#f0f0f0",
                "safety_highlight": "#cc0000",
                "success": "#008800",
            }
        )
    )
    (path / "typography.json").write_text(
        json.dumps(
            {
                "heading_family": "Arial",
                "body_family": "Arial",
                "heading_weight": "bold",
                "body_weight": "normal",
                "sizes": {"h1": "28px", "h2": "22px", "h3": "18px", "body": "14px", "small": "11px", "legal": "9px"},
            }
        )
    )
    (path / "legal.json").write_text(
        json.dumps(
            {
                "isi": "Important safety information.",
                "pi_ref": "Prescribing information.",
                "copyright": "Copyright TestDrug.",
                "reporting_statement": "Report adverse events.",
                "disclaimer": "TestDrug trademark.",
                "jurisdictions": {"FDA": {"required_elements": ["isi"], "fair_balance_required": True}},
            }
        )
    )
    (path / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" />')
    (path / "product.png").write_bytes(b"product-image")
    return path


@pytest.fixture(autouse=True)
def campaign_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_PHARMA_CAMPAIGN_STORE_DIR", str(tmp_path / "store"))


def test_explicit_missing_paths_return_errors_without_fixture_artifacts(tmp_path: Path):
    """Removing explicit-path validation must fail this strict no-fallback contract."""
    brief_id = "missing-paths"
    save_brief(_brief(brief_id))

    result = preflight_inputs(
        campaign_brief_id=brief_id,
        claims_path=str(tmp_path / "missing-claims.json"),
        brand_kit_path=str(tmp_path / "missing-brand-kit"),
        demo_mode=True,
    )

    assert result["ready"] is False
    assert result["errors"] == [
        f"Claims path does not exist or is not a file: {tmp_path / 'missing-claims.json'}",
        f"Brand kit path does not exist or is not a directory: {tmp_path / 'missing-brand-kit'}",
    ]
    assert load_artifact(brief_id, "approved-claims.json") is None
    assert load_artifact(brief_id, "brand-components.json") is None
    assert {
        "ready",
        "demo_mode",
        "applicable_claim_count",
        "excluded_claim_count",
        "brand_manifest",
        "provenance_path",
        "hashes",
        "errors",
        "warnings",
    }.issubset(result)


def test_omitted_paths_require_explicit_demo_mode():
    """Removing the demo gate must fail this fixture-fallback contract."""
    brief_id = "no-fixture-fallback"
    save_brief(_brief(brief_id))

    result = preflight_inputs(brief_id, claims_path=None, brand_kit_path=None, demo_mode=False)

    assert result["ready"] is False
    assert result["errors"] == [
        "No claims path is configured. Supply claims_path or enable demo_mode=true.",
        "No brand kit path is configured. Supply brand_kit_path or enable demo_mode=true.",
    ]
    assert load_artifact(brief_id, "approved-claims.json") is None
    assert load_artifact(brief_id, "brand-components.json") is None


def test_claim_preflight_excludes_invalid_duplicate_expired_and_restricted_records(tmp_path: Path):
    """Removing any validity filter must expose an unusable claim to automation."""
    brief_id = "claim-exclusions"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(
        json.dumps(
            [
                _claim("valid"),
                _claim("claim-1", text="Duplicate ID"),
                _claim("claim-1", text="Another duplicate ID"),
                _claim("expired", expiry="2020-01-01"),
                _claim("restricted", restrictions="Manual review only"),
                {"claim_id": "malformed"},
            ]
        )
    )
    kit_path = _brand_kit(tmp_path / "brand-kit")
    save_brief(_brief(brief_id))

    result = preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)

    assert result["ready"] is True
    assert result["claims_count"] == 1
    assert result["exclusions"] == [
        {"claim_id": "claim-1", "reason": "duplicate_claim_id"},
        {"claim_id": "claim-1", "reason": "duplicate_claim_id"},
        {"claim_id": "expired", "reason": "expired"},
        {"claim_id": "restricted", "reason": "restricted"},
        {"claim_id": "malformed", "reason": "invalid_schema"},
    ]
    persisted = load_artifact(brief_id, "approved-claims.json")
    assert [claim["claim_id"] for claim in persisted] == ["valid"]


def test_valid_custom_kit_is_persisted_and_becomes_current_renderer_manifest(tmp_path: Path):
    """Ignoring a valid explicit kit must fail the renderer-source contract."""
    brief_id = "custom-brand-kit"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim()]))
    kit_path = _brand_kit(tmp_path / "custom-kit")
    save_brief(_brief(brief_id))

    result = preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)

    assert result["ready"] is True
    manifest = current_brand_manifest(brief_id)
    assert manifest["brand_kit_path"] == str(kit_path)
    assert manifest["palette"]["primary"] == "#123456"
    assert manifest["files"]["logo.svg"]["sha256"]
    assert manifest["files"]["product.png"]["size"] == len(b"product-image")
    provenance = load_artifact(brief_id, "input-provenance.json")
    assert provenance["claims"]["path"] == str(claims_path)
    assert provenance["brand_kit"]["path"] == str(kit_path)


def test_claims_are_filtered_by_brief_applicability_dates_and_all_duplicate_occurrences(tmp_path: Path):
    """Removing a governance filter must not make an unusable claim available."""
    brief_id = "governance"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(
        json.dumps(
            [
                _claim("usable"),
                _claim("future", effective_from="2999-01-01"),
                _claim("wrong-country", jurisdictions=["GB"]),
                _claim("wrong-indication", indications=["other indication"]),
                _claim("wrong-audience", audiences=["patient"]),
                _claim("wrong-channel", channels=["banner"]),
                _claim("duplicate"),
                _claim("duplicate", text="A second duplicate claim."),
            ]
        )
    )
    kit_path = _brand_kit(tmp_path / "brand-kit")
    save_brief(_brief(brief_id))

    result = preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)

    assert result["ready"] is True
    assert [claim["claim_id"] for claim in load_artifact(brief_id, "approved-claims.json")] == ["usable"]
    assert result["exclusions"] == [
        {"claim_id": "future", "reason": "not_yet_effective"},
        {"claim_id": "wrong-country", "reason": "jurisdiction_inapplicable"},
        {"claim_id": "wrong-indication", "reason": "indication_inapplicable"},
        {"claim_id": "wrong-audience", "reason": "audience_inapplicable"},
        {"claim_id": "wrong-channel", "reason": "channel_inapplicable"},
        {"claim_id": "duplicate", "reason": "duplicate_claim_id"},
        {"claim_id": "duplicate", "reason": "duplicate_claim_id"},
    ]


def test_empty_or_all_excluded_claims_fail_without_input_artifacts(tmp_path: Path):
    """A preflight with no usable automated claims must not become ready."""
    kit_path = _brand_kit(tmp_path / "brand-kit")
    for brief_id, claims in (("empty", []), ("all-excluded", [_claim("draft", approval_status="draft")])):
        claims_path = tmp_path / f"{brief_id}.json"
        claims_path.write_text(json.dumps(claims))
        save_brief(_brief(brief_id))

        result = preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)

        assert result["ready"] is False
        assert result["applicable_claim_count"] == 0
        assert result["errors"]
        assert load_artifact(brief_id, "approved-claims.json") is None
        assert load_artifact(brief_id, "brand-components.json") is None


def test_optional_claim_governance_fields_default_to_unrestricted(tmp_path: Path):
    """Treating omitted optional governance fields as schema errors is incorrect."""
    brief_id = "optional-governance"
    claims_path = tmp_path / "claims.json"
    claim = _claim()
    for name in (
        "effective_from",
        "expiry",
        "jurisdictions",
        "indications",
        "audiences",
        "channels",
        "allowed_variants",
    ):
        claim.pop(name)
    claims_path.write_text(json.dumps([claim]))
    kit_path = _brand_kit(tmp_path / "brand-kit")
    save_brief(_brief(brief_id))

    result = preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)

    assert result["ready"] is True
    assert load_artifact(brief_id, "approved-claims.json")[0]["allowed_variants"] == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda kit: (kit / "logo.svg").unlink(), "missing required file"),
        (lambda kit: (kit / "palette.json").write_text(json.dumps({"primary": "not-a-colour"})), "invalid palette"),
        (lambda kit: (kit / "logo.svg").write_text("<svg><script>alert(1)</script></svg>"), "unsafe SVG"),
    ],
)
def test_invalid_brand_kit_schema_or_svg_writes_no_artifacts(tmp_path: Path, mutate, message: str):
    """Weak brand validation must not persist an unsafe or incomplete kit."""
    brief_id = f"invalid-brand-{message}"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim()]))
    kit_path = _brand_kit(tmp_path / "brand-kit")
    mutate(kit_path)
    save_brief(_brief(brief_id))

    result = preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)

    assert result["ready"] is False
    assert message in result["errors"][0]
    assert load_artifact(brief_id, "approved-claims.json") is None
    assert load_artifact(brief_id, "brand-components.json") is None


def test_brand_kit_symlink_escape_is_rejected(tmp_path: Path):
    """A symlinked asset must not let a kit read or hash files outside its directory."""
    brief_id = "brand-symlink"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim()]))
    kit_path = _brand_kit(tmp_path / "brand-kit")
    outside_logo = tmp_path / "outside.svg"
    outside_logo.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (kit_path / "logo.svg").unlink()
    (kit_path / "logo.svg").symlink_to(outside_logo)
    save_brief(_brief(brief_id))

    result = preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)

    assert result["ready"] is False
    assert "escapes the kit directory" in result["errors"][0]


@pytest.mark.parametrize("configured_on_brief", [False, True])
def test_claims_symlink_is_rejected_before_preflight_persists_provenance(
    tmp_path: Path, configured_on_brief: bool
) -> None:
    """A successful preflight must not persist a lexical source that status later rejects."""
    brief_id = f"claims-symlink-{configured_on_brief}"
    real_claims = tmp_path / "real-claims.json"
    real_claims.write_text(json.dumps([_claim()]))
    linked_claims = tmp_path / "linked-claims.json"
    linked_claims.symlink_to(real_claims)
    kit_path = _brand_kit(tmp_path / "brand-kit")
    save_brief(_brief(brief_id, approved_claims_path=str(linked_claims) if configured_on_brief else None))

    result = preflight_inputs(
        brief_id,
        None if configured_on_brief else str(linked_claims),
        str(kit_path),
        demo_mode=False,
    )

    assert result["ready"] is False
    assert "symlink" in result["errors"][0].casefold()
    assert load_artifact(brief_id, "approved-claims.json") is None
    assert load_artifact(brief_id, "input-provenance.json") is None


def test_partial_custom_refresh_preserves_source_derived_demo_status(tmp_path: Path):
    """A non-demo partial source refresh cannot clear existing fixture provenance."""
    from open_pharma_plugins_campaign_studio._inputs import resolve_and_persist_brand_kit

    brief_id = "mixed-demo"
    kit_path = _brand_kit(tmp_path / "custom-kit")
    save_brief(_brief(brief_id, indication="oncology"))
    initial = preflight_inputs(brief_id, None, str(kit_path), demo_mode=True)

    refreshed = resolve_and_persist_brand_kit(brief_id, str(kit_path), demo_mode=False)

    assert initial["demo_mode"] is True
    assert refreshed["demo_mode"] is True
    assert load_brief(brief_id)["demo_mode"] is True
    assert load_artifact(brief_id, "input-provenance.json")["claims"]["is_demo_fixture"] is True


def test_renderer_uses_persisted_brand_manifest_and_never_fixture_fallback(tmp_path: Path):
    """Reading mutable paths or a fixture instead of the inspected manifest is unsafe."""
    brief_id = "renderer-manifest"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim()]))
    kit_path = _brand_kit(tmp_path / "brand-kit")
    save_brief(_brief(brief_id))
    assert preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)["ready"] is True
    (kit_path / "legal.json").write_text(json.dumps({"disclaimer": "MUTATED"}))

    assert load_brand_kit(brief_id)["legal"]["disclaimer"] == "TestDrug trademark."
    save_brief(_brief("no-manifest"))
    with pytest.raises(ValueError, match="brand-components.json"):
        load_brand_kit("no-manifest")


def test_category_filter_persists_the_selected_authoritative_claim_set(tmp_path: Path):
    """Persisting unfiltered claims after advertising a category filter is unsafe."""
    brief_id = "claim-selection"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim("efficacy"), _claim("safety", category="safety")]))
    save_brief(_brief(brief_id))

    result = json.loads(
        retrieve_claims(
            {"campaign_brief_id": brief_id, "source": str(claims_path), "categories": ["safety"], "demo_mode": False}
        )[0]["text"]
    )

    assert result["ready"] is True
    assert [claim["claim_id"] for claim in load_artifact(brief_id, "approved-claims.json")] == ["safety"]

    zero = json.loads(
        retrieve_claims(
            {"campaign_brief_id": brief_id, "source": str(claims_path), "categories": ["dosing"], "demo_mode": False}
        )[0]["text"]
    )
    assert zero["ready"] is False
    assert zero["errors"] == ["No applicable approved claims remain after category filtering."]


def test_mcp_create_brief_validates_contract_and_preflights_demo_inputs(tmp_path: Path):
    """MCP must return structured brief errors and persist a preflight-ready demo brief."""
    store = tmp_path / "mcp-store"

    async def exercise() -> tuple[dict, dict, dict]:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "open_pharma_plugins_campaign_studio"],
            env={**os.environ, "OPEN_PHARMA_CAMPAIGN_STORE_DIR": str(store)},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                invalid = await session.call_tool(
                    "create_campaign_brief",
                    {
                        "campaign_name": "Invalid",
                        "country": "USA",
                        "policy_jurisdiction": "FDA",
                        "mode": "promotional",
                        "brand": "TestDrug",
                        "indication": "oncology",
                        "target_segment": "HCP",
                        "behavioral_objective": "Learn",
                        "desired_kpi": [],
                        "call_to_action": "Learn more",
                        "call_to_action_url": "http://example.test",
                        "channels": [],
                        "language": "fr",
                    },
                )
                valid = await session.call_tool(
                    "create_campaign_brief",
                    {
                        "campaign_name": "MCP Demo",
                        "country": "us",
                        "policy_jurisdiction": "FDA",
                        "mode": "promotional",
                        "brand": "TestDrug",
                        "indication": "oncology",
                        "target_segment": "HCP",
                        "behavioral_objective": "Learn",
                        "desired_kpi": ["reach"],
                        "call_to_action": "Learn more",
                        "call_to_action_url": "https://example.test/learn",
                        "channels": ["email"],
                        "demo_mode": True,
                    },
                )
                valid_data = json.loads(valid.content[0].text)
                preflight = await session.call_tool(
                    "preflight_campaign_inputs",
                    {"campaign_brief_id": valid_data["campaign_brief_id"], "demo_mode": True},
                )
                return json.loads(invalid.content[0].text), valid_data, json.loads(preflight.content[0].text)

    invalid, valid, preflight = anyio.run(exercise)

    assert invalid["errors"] == [
        "Invalid country 'USA'. Must be a two-letter ISO 3166-1 alpha-2 code.",
        "desired_kpi must contain at least one value.",
        "channels must contain at least one value.",
        "call_to_action_url must be an HTTPS URL with a valid hostname.",
        "Unsupported language 'fr'. Campaign Studio supports only 'en'.",
    ]
    assert preflight["ready"] is True
    assert preflight["demo_mode"] is True
    assert preflight["applicable_claim_count"] > 0
    assert all(
        key in preflight for key in ("claims", "brand_manifest", "provenance_path", "hashes", "warnings", "errors")
    )
    persisted_brief = json.loads((store / "campaigns" / valid["campaign_brief_id"] / "campaign-brief.json").read_text())
    assert persisted_brief["country"] == "US"
    assert persisted_brief["call_to_action_url"] == "https://example.test/learn"


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("channels", ["email", "email"], "Campaign brief channels must be unique."),
        ("channels", ["email", ""], "Campaign brief channels contain an unsupported value."),
        ("desired_kpi", ["reach", "reach"], "Campaign brief desired_kpi values must be unique."),
        ("desired_kpi", ["reach", " "], "Campaign brief desired_kpi must be a list of nonblank strings."),
    ],
)
def test_create_brief_rejects_writer_status_semantic_drift_without_creating(
    field: str, value: list[str], expected_error: str
):
    """Bypassing the shared brief validator must create an artifact status immediately rejects."""
    from open_pharma_plugins_campaign_studio.tools.create_campaign_brief import handle as create_brief

    campaign_brief_id = f"reject-new-{field}-{len(value)}"
    response = json.loads(create_brief(_create_brief_arguments(campaign_brief_id, **{field: value}))[0]["text"])

    assert expected_error in response["errors"]
    assert not (store_root_path() / "campaigns" / campaign_brief_id).exists()


def test_create_brief_rejects_invalid_update_without_overwriting_and_accepts_unique_values():
    """Candidate validation belongs before both brief and index writes on updates."""
    from open_pharma_plugins_campaign_studio.tools.create_campaign_brief import handle as create_brief

    campaign_brief_id = "writer-update"
    created = json.loads(create_brief(_create_brief_arguments(campaign_brief_id))[0]["text"])
    assert created["channels"] == ["email", "banner"]
    campaign_path = store_root_path() / "campaigns" / campaign_brief_id / "campaign-brief.json"
    index_path = store_root_path() / "_index.json"
    before = {"brief": campaign_path.read_bytes(), "index": index_path.read_bytes()}

    invalid_updates = (
        {"channels": ["email", "email"]},
        {"channels": ["email", ""]},
        {"desired_kpi": ["reach", "reach"]},
        {"desired_kpi": ["reach", " "]},
    )
    for update in invalid_updates:
        response = json.loads(create_brief(_create_brief_arguments(campaign_brief_id, **update))[0]["text"])
        assert response["errors"]
        assert campaign_path.read_bytes() == before["brief"]
        assert index_path.read_bytes() == before["index"]

    persisted = load_brief(campaign_brief_id)
    assert persisted["channels"] == ["email", "banner"]
    assert persisted["desired_kpi"] == ["reach", "engagement"]


def test_mcp_create_brief_uses_shared_collection_semantics_without_create_or_overwrite(tmp_path: Path):
    """Real MCP validation must converge with direct writer and read-only status semantics."""
    store = tmp_path / "mcp-writer-store"
    invalid_cases = (
        ("mcp-duplicate-channels", {"channels": ["email", "email"]}),
        ("mcp-blank-channel", {"channels": ["email", ""]}),
        ("mcp-duplicate-kpis", {"desired_kpi": ["reach", "reach"]}),
        ("mcp-blank-kpi", {"desired_kpi": ["reach", " "]}),
    )

    async def exercise() -> tuple[list[dict], dict, dict]:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "open_pharma_plugins_campaign_studio"],
            env={**os.environ, "OPEN_PHARMA_CAMPAIGN_STORE_DIR": str(store)},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                invalid_results = []
                for campaign_brief_id, override in invalid_cases:
                    response = await session.call_tool(
                        "create_campaign_brief", _create_brief_arguments(campaign_brief_id, **override)
                    )
                    assert response.isError is False
                    invalid_results.append(json.loads(response.content[0].text))

                valid_arguments = _create_brief_arguments("mcp-valid-writer")
                valid_response = await session.call_tool("create_campaign_brief", valid_arguments)
                valid_result = json.loads(valid_response.content[0].text)
                valid_path = store / "campaigns" / "mcp-valid-writer" / "campaign-brief.json"
                before_update = valid_path.read_bytes()
                invalid_update = await session.call_tool(
                    "create_campaign_brief",
                    _create_brief_arguments("mcp-valid-writer", desired_kpi=["reach", "reach"]),
                )
                assert valid_path.read_bytes() == before_update
                return invalid_results, valid_result, json.loads(invalid_update.content[0].text)

    invalid_results, valid_result, invalid_update = anyio.run(exercise)

    assert all(result["errors"] for result in invalid_results)
    assert invalid_update["errors"] == ["Campaign brief desired_kpi values must be unique."]
    for campaign_brief_id, _override in invalid_cases:
        assert not (store / "campaigns" / campaign_brief_id).exists()
    assert valid_result["campaign_brief_id"] == "mcp-valid-writer"
    persisted = json.loads((store / "campaigns" / "mcp-valid-writer" / "campaign-brief.json").read_text())
    assert persisted["channels"] == ["email", "banner"]
    assert persisted["desired_kpi"] == ["reach", "engagement"]


def test_preflight_rolls_back_every_input_target_for_fresh_and_existing_campaigns(tmp_path: Path, monkeypatch):
    """A failed activation cannot expose claims from a different input set."""
    import open_pharma_plugins_campaign_studio._inputs as inputs

    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim("new-claim")]))
    kit_path = _brand_kit(tmp_path / "brand-kit")
    original_save = inputs.save_artifact

    for brief_id, existing in (("fresh-rollback", False), ("existing-rollback", True)):
        save_brief(_brief(brief_id))
        if existing:
            original_save(brief_id, "approved-claims.json", [{"claim_id": "old"}])
            original_save(brief_id, "brand-components.json", {"legal": {"disclaimer": "old"}})
            original_save(brief_id, "input-provenance.json", {"claims": {"path": "old"}})
        before = {
            name: load_artifact(brief_id, name)
            for name in (
                "campaign-brief.json",
                "approved-claims.json",
                "brand-components.json",
                "input-provenance.json",
            )
        }

        def fail_brand(campaign_id: str, filename: str, data: dict):
            if filename == "brand-components.json":
                raise OSError("injected brand write failure")
            return original_save(campaign_id, filename, data)

        monkeypatch.setattr(inputs, "save_artifact", fail_brand)
        result = preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)
        monkeypatch.setattr(inputs, "save_artifact", original_save)

        assert result["ready"] is False
        assert result["errors"] == ["injected brand write failure"]
        after = {
            name: load_artifact(brief_id, name)
            for name in (
                "campaign-brief.json",
                "approved-claims.json",
                "brand-components.json",
                "input-provenance.json",
            )
        }
        assert after == before


@pytest.mark.parametrize("operation", ["claims", "brand"])
def test_corrupt_provenance_blocks_partial_refresh_without_changing_input_set(tmp_path: Path, operation: str):
    """A corrupt legacy provenance file must make partial activation atomically fail."""
    from open_pharma_plugins_campaign_studio._campaign_store import campaign_dir

    brief_id = f"corrupt-provenance-{operation}"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim("first")]))
    replacement_claims = tmp_path / "replacement-claims.json"
    replacement_claims.write_text(json.dumps([_claim("replacement")]))
    kit_path = _brand_kit(tmp_path / "brand-kit")
    replacement_kit = _brand_kit(tmp_path / "replacement-kit")
    save_brief(_brief(brief_id))
    assert preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)["ready"] is True

    directory = campaign_dir(brief_id)
    provenance_path = directory / "input-provenance.json"
    provenance_path.write_bytes(b"{")
    targets = (
        "campaign-brief.json",
        "approved-claims.json",
        "brand-components.json",
        "input-provenance.json",
    )
    before = {name: (directory / name).read_bytes() for name in targets}

    if operation == "claims":
        result = resolve_and_persist_claims(brief_id, str(replacement_claims), demo_mode=False)
    else:
        result = resolve_and_persist_brand_kit(brief_id, str(replacement_kit), demo_mode=False)

    assert result["ready"] is False
    assert result["errors"]
    assert {name: (directory / name).read_bytes() for name in targets} == before


def test_relative_sources_preserve_lexical_provenance_and_bind_resolved_brand_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Relative user paths remain display provenance while renderer seals use their preflight location."""
    from open_pharma_plugins_campaign_studio._renderer import validation_input_fingerprint

    brief_id = "relative-sources"
    submitted_from = tmp_path / "submitted-from"
    submitted_from.mkdir()
    claims_path = submitted_from / "sources" / "claims.json"
    claims_path.parent.mkdir()
    claims_path.write_text(json.dumps([_claim()]))
    kit_path = _brand_kit(submitted_from / "sources" / "kit")
    lexical_claims = "sources/claims.json"
    lexical_kit = "sources/kit"
    save_brief(_brief(brief_id, approved_claims_path=lexical_claims, brand_kit_path=lexical_kit))

    monkeypatch.chdir(submitted_from)
    result = preflight_inputs(brief_id, None, None, demo_mode=False)
    assert result["ready"] is True
    manifest = load_artifact(brief_id, "brand-components.json")
    provenance = load_artifact(brief_id, "input-provenance.json")
    assert manifest["brand_kit_path"] == lexical_kit
    assert manifest["resolved_brand_kit_path"] == str(kit_path.resolve())
    assert manifest["files"]["legal.json"]["path"] == "sources/kit/legal.json"
    assert manifest["files"]["legal.json"]["resolved_path"] == str((kit_path / "legal.json").resolve())
    assert provenance["claims"]["path"] == lexical_claims
    assert provenance["claims"]["resolved_path"] == str(claims_path.resolve())
    assert provenance["brand_kit"]["path"] == lexical_kit
    assert provenance["brand_kit"]["resolved_path"] == str(kit_path.resolve())

    submitted_fingerprint = validation_input_fingerprint(brief_id, ["email"])

    resumed_from = tmp_path / "resumed-from"
    resumed_from.mkdir()
    monkeypatch.chdir(resumed_from)
    assert validation_input_fingerprint(brief_id, ["email"]) == submitted_fingerprint
    assert load_brand_kit(brief_id)["legal"]["disclaimer"] == "TestDrug trademark."


def test_preflight_keeps_channel_restricted_claim_when_any_brief_channel_matches(tmp_path: Path):
    """Preflight is campaign-level; citation-level channel enforcement is Task 2."""
    brief_id = "channel-intersection"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim(channels=["email"])]))
    save_brief(_brief(brief_id, channels=["email", "banner"]))

    result = preflight_inputs(brief_id, str(claims_path), str(_brand_kit(tmp_path / "brand-kit")), demo_mode=False)

    assert result["ready"] is True
    assert result["applicable_claim_count"] == 1


def test_unknown_restricted_audience_fails_closed_and_known_taxonomy_is_explicit(tmp_path: Path):
    """Audience restriction handling cannot guess unknown target segments."""
    kit_path = _brand_kit(tmp_path / "brand-kit")
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim(audiences=["HCP"])]))
    save_brief(_brief("unknown-audience", target_segment="clinical influencers"))
    save_brief(_brief("known-audience", target_segment="oncologists"))

    unknown = preflight_inputs("unknown-audience", str(claims_path), str(kit_path), demo_mode=False)
    known = preflight_inputs("known-audience", str(claims_path), str(kit_path), demo_mode=False)

    assert unknown["ready"] is False
    assert unknown["exclusions"] == [{"claim_id": "claim-1", "reason": "audience_inapplicable"}]
    assert known["ready"] is True


def test_exact_custom_audience_allowlist_match_is_applicable(tmp_path: Path) -> None:
    """Structured allowlists may use an exact audience outside the convenience taxonomy."""
    brief_id = "exact-custom-audience"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim(audiences=["Clinical Influencers"])]))
    save_brief(_brief(brief_id, target_segment="clinical influencers"))

    result = preflight_inputs(
        brief_id,
        str(claims_path),
        str(_brand_kit(tmp_path / "brand-kit")),
        demo_mode=False,
    )

    assert result["ready"] is True
    assert result["applicable_claim_count"] == 1


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda kit: (kit / "typography.json").write_text(
                json.dumps({**json.loads((kit / "typography.json").read_text()), "heading_family": "Arial; color:red"})
            ),
            "invalid typography",
        ),
        (
            lambda kit: (kit / "logo.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><style>@import url(https://evil.test/a.css)</style></svg>'
            ),
            "unsafe SVG",
        ),
        (
            lambda kit: (kit / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>'),
            "unsafe SVG",
        ),
        (
            lambda kit: (kit / "logo.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><animate attributeName="x"/></svg>'
            ),
            "unsafe SVG",
        ),
        (
            lambda kit: (kit / "logo.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="alert(1)"/></svg>'
            ),
            "unsafe SVG",
        ),
        (
            lambda kit: (kit / "logo.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill: url(https://evil.test/a)"/></svg>'
            ),
            "unsafe SVG",
        ),
    ],
)
def test_brand_rejects_unsafe_typography_and_active_svg(tmp_path: Path, mutate, message: str):
    """CSS injection and active SVG constructs are not inert brand data."""
    brief_id = "unsafe-brand"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim()]))
    kit_path = _brand_kit(tmp_path / "brand-kit")
    mutate(kit_path)
    save_brief(_brief(brief_id))

    result = preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)

    assert result["ready"] is False
    assert message in result["errors"][0]


def test_raw_duplicate_and_blank_core_claim_values_are_excluded(tmp_path: Path):
    """Duplicate raw IDs dominate schema errors and core strings cannot be blank."""
    brief_id = "raw-duplicates"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim("dup"), {"claim_id": " dup ", "text": ""}, _claim("blank", text=" ")]))
    save_brief(_brief(brief_id))

    result = preflight_inputs(brief_id, str(claims_path), str(_brand_kit(tmp_path / "brand-kit")), demo_mode=False)

    assert result["ready"] is False
    assert result["exclusions"] == [
        {"claim_id": "dup", "reason": "duplicate_claim_id"},
        {"claim_id": "dup", "reason": "duplicate_claim_id"},
        {"claim_id": "blank", "reason": "invalid_schema"},
    ]


def test_fixture_variants_are_empty_or_substantive_claim_specific_strings():
    """Fixture alternates must not be placeholder tokens such as 'full'."""
    fixture = (
        Path(__file__).parents[3]
        / "src/capabilities/campaign-studio/open_pharma_plugins_campaign_studio/fixtures/sample_approved_claims.json"
    )
    for claim in json.loads(fixture.read_text()):
        for variant in claim["allowed_variants"]:
            assert variant.strip()
            assert variant != "full"
            assert variant != claim["text"]


@pytest.mark.parametrize(
    "url",
    [
        "https://",
        "https:// user@example.test",
        "https://user@example.test",
        "https://example.test:bad",
        "https://exa mple.test",
        "http://example.test",
    ],
)
def test_call_to_action_url_is_required_and_strictly_https(url: str):
    """URL prefixes alone are not enough for a renderer-facing link contract."""
    from open_pharma_plugins_campaign_studio.tools.create_campaign_brief import handle as create_brief

    args = {
        "campaign_name": "URL",
        "country": "US",
        "policy_jurisdiction": "FDA",
        "mode": "promotional",
        "brand": "TestDrug",
        "indication": "test indication",
        "target_segment": "HCP",
        "behavioral_objective": "Learn",
        "desired_kpi": ["reach"],
        "call_to_action": "Learn more",
        "call_to_action_url": url,
        "channels": ["email"],
    }
    data = json.loads(create_brief(args)[0]["text"])
    assert data["errors"] == ["call_to_action_url must be an HTTPS URL with a valid hostname."]


@pytest.mark.parametrize(
    "svg",
    [
        '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(evil.svg#paint)"/></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(&#104;ttps://evil.test/paint)"/></svg>',
        r'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="u\72l(https://evil.test/paint)"/></svg>',
        '<?xml-stylesheet href="https://evil.test/style.css"?><svg xmlns="http://www.w3.org/2000/svg"/>',
    ],
)
def test_svg_rejects_external_or_processing_instruction_bypasses(tmp_path: Path, svg: str):
    """Only local fragment paint URLs are inert enough for a persisted brand SVG."""
    brief_id = "svg-bypass"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim()]), encoding="utf-8")
    kit_path = _brand_kit(tmp_path / "brand-kit")
    (kit_path / "logo.svg").write_text(svg, encoding="utf-8")
    save_brief(_brief(brief_id))

    result = preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)

    assert result["ready"] is False
    assert "unsafe SVG" in result["errors"][0]


def test_svg_allows_only_local_fragment_paint_urls_and_utf8_input_text(tmp_path: Path):
    """Rejecting every paint URL would incorrectly reject an inert local definition."""
    brief_id = "utf8-local-paint"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(
        json.dumps([_claim(text="Café evidence improved the endpoint.")], ensure_ascii=False), encoding="utf-8"
    )
    kit_path = _brand_kit(tmp_path / "brand-kit")
    (kit_path / "legal.json").write_text(
        json.dumps(
            {
                **json.loads((kit_path / "legal.json").read_text(encoding="utf-8")),
                "disclaimer": "Información aprobada.",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (kit_path / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="paint"/></defs><rect fill="url(#paint)"/></svg>',
        encoding="utf-8",
    )
    save_brief(_brief(brief_id))

    result = preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)

    assert result["ready"] is True
    assert result["claims"][0]["text"] == "Café evidence improved the endpoint."
    assert result["brand_manifest"]["legal"]["disclaimer"] == "Información aprobada."


def test_failure_result_keeps_active_inputs_separate_from_failed_candidate_and_is_safe_for_traversal(tmp_path: Path):
    """A failing candidate must not erase or impersonate an already activated input set."""
    brief_id = "active-inputs"
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps([_claim("active")]), encoding="utf-8")
    kit_path = _brand_kit(tmp_path / "brand-kit")
    save_brief(_brief(brief_id))
    assert preflight_inputs(brief_id, str(claims_path), str(kit_path), demo_mode=False)["ready"] is True
    rejected_path = tmp_path / "rejected.json"
    rejected_path.write_text(json.dumps([_claim("draft", approval_status="draft")]), encoding="utf-8")

    failed = preflight_inputs(brief_id, str(rejected_path), str(kit_path), demo_mode=False)
    traversal = json.loads(retrieve_claims({"campaign_brief_id": "../escape"})[0]["text"])
    brand_traversal = json.loads(retrieve_brand({"campaign_brief_id": "../escape"})[0]["text"])
    from open_pharma_plugins_campaign_studio.tools.preflight_campaign_inputs import handle as preflight

    preflight_traversal = json.loads(preflight({"campaign_brief_id": "../escape"})[0]["text"])

    assert failed["ready"] is False
    assert failed["claims_path"] == str(claims_path)
    assert failed["active_inputs"]["claims_path"] == str(claims_path)
    assert failed["candidate_inputs"]["claims_path"] == str(rejected_path)
    assert failed["candidate_inputs"]["applicable_claim_count"] == 0
    for result in (traversal, brand_traversal, preflight_traversal):
        assert result["ready"] is False
        assert result["campaign_brief_id"] == "../escape"
        assert result["claims"] == []
        assert result["active_inputs"] == {}
        assert result["candidate_inputs"] == {}
        assert result["errors"]


def test_post_resolution_failure_discloses_demo_if_either_selected_source_is_demo(tmp_path: Path):
    """Demo disclosure is source provenance, not a successful-persistence flag."""
    brief_id = "mixed-demo-failure"
    invalid_claims = tmp_path / "invalid-claims.json"
    invalid_claims.write_text("not JSON", encoding="utf-8")
    save_brief(_brief(brief_id))

    result = preflight_inputs(brief_id, str(invalid_claims), None, demo_mode=True)

    assert result["ready"] is False
    assert result["demo_mode"] is True
    assert result["candidate_inputs"]["claims_path"] == str(invalid_claims)
    assert result["candidate_inputs"]["brand_kit_path"]


def _persisted_input_state(campaign_brief_id: str) -> dict[str, object]:
    return {
        name: load_artifact(campaign_brief_id, name)
        for name in (
            "campaign-brief.json",
            "approved-claims.json",
            "brand-components.json",
            "input-provenance.json",
        )
    }


def _active_custom_inputs(tmp_path: Path, campaign_brief_id: str) -> tuple[Path, Path]:
    claims_path = tmp_path / f"{campaign_brief_id}-claims.json"
    claims_path.write_text(json.dumps([_claim("active")]), encoding="utf-8")
    kit_path = _brand_kit(tmp_path / f"{campaign_brief_id}-brand-kit")
    save_brief(_brief(campaign_brief_id))
    assert preflight_inputs(campaign_brief_id, str(claims_path), str(kit_path), demo_mode=False)["ready"] is True
    return claims_path, kit_path


@pytest.mark.parametrize("source_kind", ["claims", "brand"])
@pytest.mark.parametrize("source_selection", ["explicit", "configured"])
@pytest.mark.parametrize("through_handler", [False, True])
def test_bundled_resolver_failure_discloses_candidate_demo_without_writes(
    monkeypatch: pytest.MonkeyPatch,
    source_kind: str,
    source_selection: str,
    through_handler: bool,
):
    """A known bundled candidate remains disclosed when its resolver raises."""
    import open_pharma_plugins_campaign_studio._inputs as inputs

    brief_id = f"bundled-{source_kind}-{source_selection}-{'handler' if through_handler else 'operation'}"
    fixture_root = Path(inputs.__file__).parent / "fixtures"
    fixture_path = (
        fixture_root / "sample_approved_claims.json" if source_kind == "claims" else fixture_root / "brand_kit"
    )
    configured_key = "approved_claims_path" if source_kind == "claims" else "brand_kit_path"
    brief_overrides = {configured_key: str(fixture_path)} if source_selection == "configured" else {}
    save_brief(_brief(brief_id, **brief_overrides))
    before = _persisted_input_state(brief_id)
    error = f"injected {source_kind} resolver failure"

    def fail_resolution(*_args):
        raise OSError(error)

    if source_kind == "claims":
        monkeypatch.setattr(inputs, "_resolve_claims_source", fail_resolution)
        explicit_source = str(fixture_path) if source_selection == "explicit" else None
        arguments = {"campaign_brief_id": brief_id, "source": explicit_source, "demo_mode": True}
        result = (
            json.loads(retrieve_claims(arguments)[0]["text"])
            if through_handler
            else resolve_and_persist_claims(brief_id, explicit_source, True)
        )
        candidate_path_key = "claims_path"
    else:
        monkeypatch.setattr(inputs, "_resolve_brand_kit_source", fail_resolution)
        explicit_source = str(fixture_path) if source_selection == "explicit" else None
        arguments = {"campaign_brief_id": brief_id, "brand_kit_path": explicit_source, "demo_mode": True}
        result = (
            json.loads(retrieve_brand(arguments)[0]["text"])
            if through_handler
            else resolve_and_persist_brand_kit(brief_id, explicit_source, True)
        )
        candidate_path_key = "brand_kit_path"

    assert len(result) == 27
    assert set(result) == _INPUT_RESULT_KEYS
    assert result["ready"] is False
    assert result["demo_mode"] is True
    assert result["candidate_inputs"]["demo_mode"] is True
    assert result["candidate_inputs"][candidate_path_key] == str(fixture_path)
    assert result["errors"] == [error]
    assert _persisted_input_state(brief_id) == before


@pytest.mark.parametrize("claims_selection", ["explicit", "configured"])
@pytest.mark.parametrize("brand_selection", ["explicit", "configured"])
@pytest.mark.parametrize("through_handler", [False, True])
def test_preflight_first_resolver_failure_preserves_both_bundled_candidate_hints(
    monkeypatch: pytest.MonkeyPatch,
    claims_selection: str,
    brand_selection: str,
    through_handler: bool,
):
    """Both hints must be computed before the first preflight resolver can fail."""
    import open_pharma_plugins_campaign_studio._inputs as inputs

    brief_id = f"preflight-first-{claims_selection}-{brand_selection}-{'handler' if through_handler else 'operation'}"
    fixture_root = Path(inputs.__file__).parent / "fixtures"
    fixture_claims = fixture_root / "sample_approved_claims.json"
    fixture_kit = fixture_root / "brand_kit"
    brief_overrides = {}
    if claims_selection == "configured":
        brief_overrides["approved_claims_path"] = str(fixture_claims)
    if brand_selection == "configured":
        brief_overrides["brand_kit_path"] = str(fixture_kit)
    save_brief(_brief(brief_id, **brief_overrides))
    before = _persisted_input_state(brief_id)

    def fail_claims_resolution(*_args):
        raise OSError("injected first resolver failure")

    def reject_brand_resolution(*_args):
        raise AssertionError("brand resolver must not run after the claims resolver fails")

    monkeypatch.setattr(inputs, "_resolve_claims_source", fail_claims_resolution)
    monkeypatch.setattr(inputs, "_resolve_brand_kit_source", reject_brand_resolution)
    arguments = {
        "campaign_brief_id": brief_id,
        "claims_path": str(fixture_claims) if claims_selection == "explicit" else None,
        "brand_kit_path": str(fixture_kit) if brand_selection == "explicit" else None,
        "demo_mode": True,
    }
    result = json.loads(preflight(arguments)[0]["text"]) if through_handler else preflight_inputs(**arguments)

    assert len(result) == 27
    assert set(result) == _INPUT_RESULT_KEYS
    assert result["ready"] is False
    assert result["demo_mode"] is True
    assert result["candidate_inputs"]["demo_mode"] is True
    assert result["candidate_inputs"]["claims_path"] == str(fixture_claims)
    assert result["candidate_inputs"]["brand_kit_path"] == str(fixture_kit)
    assert result["errors"] == ["injected first resolver failure"]
    assert _persisted_input_state(brief_id) == before


def test_bundled_hint_classification_is_lexical_component_bounded_and_side_effect_free(
    monkeypatch: pytest.MonkeyPatch,
):
    """Hint classification normalises lexically and never invokes strict filesystem inspection."""
    import open_pharma_plugins_campaign_studio._inputs as inputs

    fixture_root = Path(inputs.__file__).parent / "fixtures"
    contained = os.path.relpath(fixture_root / "brand_kit" / ".." / "sample_approved_claims.json")
    lookalike = str(Path(f"{fixture_root}-lookalike") / "sample_approved_claims.json")

    def reject_filesystem_access(*_args, **_kwargs):
        raise AssertionError("source hints must be lexical")

    monkeypatch.setattr(Path, "resolve", reject_filesystem_access)
    monkeypatch.setattr(Path, "open", reject_filesystem_access)
    monkeypatch.setattr(inputs, "sha256_file", reject_filesystem_access)
    monkeypatch.setattr(inputs, "_is_bundled_fixture", reject_filesystem_access)

    assert inputs._claims_source_hint(contained, None, True) == (contained, True)
    assert inputs._claims_source_hint(lookalike, None, True) == (lookalike, False)


def test_unexpected_preflight_failure_after_source_resolution_preserves_context_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Moving the exception boundary outside resolution must lose candidate provenance and fail this test."""
    import open_pharma_plugins_campaign_studio._inputs as inputs

    brief_id = "unexpected-preflight"
    active_claims, active_kit = _active_custom_inputs(tmp_path, brief_id)
    fixture_kit = Path(str(inputs.files("open_pharma_plugins_campaign_studio") / "fixtures" / "brand_kit"))
    before = _persisted_input_state(brief_id)
    resolved = {"claims": False, "brand": False}
    original_claims_resolver = inputs._resolve_claims_source
    original_brand_resolver = inputs._resolve_brand_kit_source

    def resolve_claims(*args):
        result = original_claims_resolver(*args)
        resolved["claims"] = True
        return result

    def resolve_brand(*args):
        result = original_brand_resolver(*args)
        resolved["brand"] = True
        return result

    def fail_after_resolution(*_args):
        assert resolved == {"claims": True, "brand": True}
        raise RuntimeError("injected unexpected preflight failure")

    monkeypatch.setattr(inputs, "_resolve_claims_source", resolve_claims)
    monkeypatch.setattr(inputs, "_resolve_brand_kit_source", resolve_brand)
    monkeypatch.setattr(inputs, "_validated_claims", fail_after_resolution)

    result = preflight_inputs(brief_id, str(active_claims), str(fixture_kit), demo_mode=True)

    assert result["ready"] is False
    assert result["demo_mode"] is True
    assert result["candidate_inputs"]["claims_path"] == str(active_claims)
    assert result["candidate_inputs"]["brand_kit_path"] == str(fixture_kit)
    assert result["active_inputs"]["claims_path"] == str(active_claims)
    assert result["active_inputs"]["brand_kit_path"] == str(active_kit)
    assert result["errors"] == ["injected unexpected preflight failure"]
    assert _persisted_input_state(brief_id) == before


def test_unexpected_claims_failure_after_source_resolution_preserves_context_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A claims parse crash must retain its resolved demo candidate without changing active inputs."""
    import open_pharma_plugins_campaign_studio._inputs as inputs

    brief_id = "unexpected-claims"
    active_claims, active_kit = _active_custom_inputs(tmp_path, brief_id)
    fixture_claims = Path(
        str(inputs.files("open_pharma_plugins_campaign_studio") / "fixtures" / "sample_approved_claims.json")
    )
    before = _persisted_input_state(brief_id)
    resolved = False
    original_resolver = inputs._resolve_claims_source

    def resolve_claims(*args):
        nonlocal resolved
        result = original_resolver(*args)
        resolved = True
        return result

    def fail_after_resolution(*_args):
        assert resolved is True
        raise RuntimeError("injected unexpected claims failure")

    monkeypatch.setattr(inputs, "_resolve_claims_source", resolve_claims)
    monkeypatch.setattr(inputs, "_validated_claims", fail_after_resolution)

    result = resolve_and_persist_claims(brief_id, str(fixture_claims), demo_mode=True)

    assert result["ready"] is False
    assert result["demo_mode"] is True
    assert result["candidate_inputs"]["claims_path"] == str(fixture_claims)
    assert result["active_inputs"]["claims_path"] == str(active_claims)
    assert result["active_inputs"]["brand_kit_path"] == str(active_kit)
    assert result["errors"] == ["injected unexpected claims failure"]
    assert _persisted_input_state(brief_id) == before


def test_unexpected_brand_failure_after_source_resolution_preserves_context_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A brand manifest crash must retain its resolved demo candidate without changing active inputs."""
    import open_pharma_plugins_campaign_studio._inputs as inputs

    brief_id = "unexpected-brand"
    active_claims, active_kit = _active_custom_inputs(tmp_path, brief_id)
    fixture_kit = Path(str(inputs.files("open_pharma_plugins_campaign_studio") / "fixtures" / "brand_kit"))
    before = _persisted_input_state(brief_id)
    resolved = False
    original_resolver = inputs._resolve_brand_kit_source

    def resolve_brand(*args):
        nonlocal resolved
        result = original_resolver(*args)
        resolved = True
        return result

    def fail_after_resolution(*_args):
        assert resolved is True
        raise RuntimeError("injected unexpected brand failure")

    monkeypatch.setattr(inputs, "_resolve_brand_kit_source", resolve_brand)
    monkeypatch.setattr(inputs, "_brand_manifest", fail_after_resolution)

    result = resolve_and_persist_brand_kit(brief_id, str(fixture_kit), demo_mode=True)

    assert result["ready"] is False
    assert result["demo_mode"] is True
    assert result["candidate_inputs"]["brand_kit_path"] == str(fixture_kit)
    assert result["active_inputs"]["claims_path"] == str(active_claims)
    assert result["active_inputs"]["brand_kit_path"] == str(active_kit)
    assert result["errors"] == ["injected unexpected brand failure"]
    assert _persisted_input_state(brief_id) == before


@pytest.mark.parametrize("through_handler", [False, True])
def test_preflight_brand_resolver_exception_preserves_prior_claims_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, through_handler: bool
):
    """The first successful resolver must remain visible when the second resolver raises."""
    import open_pharma_plugins_campaign_studio._inputs as inputs

    brief_id = f"resolver-preflight-{'handler' if through_handler else 'operation'}"
    active_claims, active_kit = _active_custom_inputs(tmp_path, brief_id)
    kit_path = _brand_kit(tmp_path / f"{brief_id}-kit")
    fixture_claims = str(
        inputs.files("open_pharma_plugins_campaign_studio") / "fixtures" / "sample_approved_claims.json"
    )

    def fail_brand_resolution(*_args):
        raise OSError("injected brand resolver failure")

    monkeypatch.setattr(inputs, "_resolve_brand_kit_source", fail_brand_resolution)
    arguments = {
        "campaign_brief_id": brief_id,
        "claims_path": fixture_claims,
        "brand_kit_path": str(kit_path),
        "demo_mode": True,
    }
    result = json.loads(preflight(arguments)[0]["text"]) if through_handler else preflight_inputs(**arguments)

    assert set(result) == _INPUT_RESULT_KEYS
    assert result["ready"] is False
    assert result["demo_mode"] is True
    assert result["candidate_inputs"]["claims_path"] == fixture_claims
    assert result["candidate_inputs"]["brand_kit_path"] == str(kit_path)
    assert result["active_inputs"]["claims_path"] == str(active_claims)
    assert result["active_inputs"]["brand_kit_path"] == str(active_kit)
    assert result["errors"] == ["injected brand resolver failure"]


@pytest.mark.parametrize("through_handler", [False, True])
def test_claims_resolver_exception_is_total_and_retains_safe_source_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, through_handler: bool
):
    """Claims operations, rather than only MCP fallbacks, own resolver totality."""
    import open_pharma_plugins_campaign_studio._inputs as inputs

    brief_id = f"resolver-claims-{'handler' if through_handler else 'operation'}"
    source = tmp_path / "attempted-claims.json"
    active_claims, active_kit = _active_custom_inputs(tmp_path, brief_id)

    def fail_claims_resolution(*_args):
        raise OSError("injected claims resolver failure")

    monkeypatch.setattr(inputs, "_resolve_claims_source", fail_claims_resolution)
    arguments = {"campaign_brief_id": brief_id, "source": str(source), "demo_mode": False}
    result = (
        json.loads(retrieve_claims(arguments)[0]["text"])
        if through_handler
        else resolve_and_persist_claims(brief_id, str(source), False)
    )

    assert set(result) == _INPUT_RESULT_KEYS
    assert result["ready"] is False
    assert result["candidate_inputs"]["claims_path"] == str(source)
    assert result["active_inputs"]["claims_path"] == str(active_claims)
    assert result["active_inputs"]["brand_kit_path"] == str(active_kit)
    assert result["errors"] == ["injected claims resolver failure"]


@pytest.mark.parametrize("through_handler", [False, True])
def test_brand_resolver_exception_is_total_and_retains_configured_or_demo_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, through_handler: bool
):
    """Brand operations retain a safely derivable configured path or omitted demo fixture."""
    import open_pharma_plugins_campaign_studio._inputs as inputs

    brief_id = f"resolver-brand-{'handler' if through_handler else 'operation'}"
    configured_kit = tmp_path / "configured-brand-kit"
    active_claims, active_kit = _active_custom_inputs(tmp_path, brief_id)
    if through_handler:
        save_brief(_brief(brief_id, approved_claims_path=str(active_claims)))
        demo_mode = True
        expected_path = str(inputs.files("open_pharma_plugins_campaign_studio") / "fixtures" / "brand_kit")
    else:
        save_brief(_brief(brief_id, approved_claims_path=str(active_claims), brand_kit_path=str(configured_kit)))
        demo_mode = False
        expected_path = str(configured_kit)

    def fail_brand_resolution(*_args):
        raise OSError("injected brand resolver failure")

    monkeypatch.setattr(inputs, "_resolve_brand_kit_source", fail_brand_resolution)
    arguments = {"campaign_brief_id": brief_id, "brand_kit_path": None, "demo_mode": demo_mode}
    result = (
        json.loads(retrieve_brand(arguments)[0]["text"])
        if through_handler
        else resolve_and_persist_brand_kit(brief_id, None, demo_mode)
    )

    assert set(result) == _INPUT_RESULT_KEYS
    assert result["ready"] is False
    assert result["demo_mode"] is through_handler
    assert result["candidate_inputs"]["demo_mode"] is through_handler
    assert result["candidate_inputs"]["brand_kit_path"] == expected_path
    assert result["active_inputs"]["claims_path"] == str(active_claims)
    assert result["active_inputs"]["brand_kit_path"] == str(active_kit)
    assert result["errors"] == ["injected brand resolver failure"]


@pytest.mark.parametrize("through_handler", [False, True])
def test_brief_load_exception_is_total_for_all_input_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, through_handler: bool
):
    """Brief I/O failures must not escape and explicit source hints require no brief access."""
    import open_pharma_plugins_campaign_studio._inputs as inputs

    brief_id = f"brief-load-{'handler' if through_handler else 'operation'}"
    claims_path = tmp_path / "attempted-claims.json"
    kit_path = tmp_path / "attempted-brand-kit"
    active_claims, active_kit = _active_custom_inputs(tmp_path, brief_id)

    def fail_brief_load(*_args):
        raise OSError("injected brief load failure")

    monkeypatch.setattr(inputs, "load_brief", fail_brief_load)
    if through_handler:
        results = [
            json.loads(
                preflight(
                    {
                        "campaign_brief_id": brief_id,
                        "claims_path": str(claims_path),
                        "brand_kit_path": str(kit_path),
                    }
                )[0]["text"]
            ),
            json.loads(retrieve_claims({"campaign_brief_id": brief_id, "source": str(claims_path)})[0]["text"]),
            json.loads(retrieve_brand({"campaign_brief_id": brief_id, "brand_kit_path": str(kit_path)})[0]["text"]),
        ]
    else:
        results = [
            preflight_inputs(brief_id, str(claims_path), str(kit_path), False),
            resolve_and_persist_claims(brief_id, str(claims_path), False),
            resolve_and_persist_brand_kit(brief_id, str(kit_path), False),
        ]

    for result in results:
        assert set(result) == _INPUT_RESULT_KEYS
        assert result["ready"] is False
        assert result["errors"] == ["injected brief load failure"]
    assert results[0]["candidate_inputs"]["claims_path"] == str(claims_path)
    assert results[0]["candidate_inputs"]["brand_kit_path"] == str(kit_path)
    assert results[1]["candidate_inputs"]["claims_path"] == str(claims_path)
    assert results[2]["candidate_inputs"]["brand_kit_path"] == str(kit_path)
    for result in results:
        assert result["active_inputs"]["claims_path"] == str(active_claims)
        assert result["active_inputs"]["brand_kit_path"] == str(active_kit)


def test_unsafe_campaign_id_failure_retains_explicit_hint_without_diagnostic_store_access(tmp_path: Path):
    """Building failure diagnostics must not reopen a traversal-invalid campaign directory."""
    source = tmp_path / "attempted-claims.json"

    result = resolve_and_persist_claims("../unsafe-campaign", str(source), False)

    assert set(result) == _INPUT_RESULT_KEYS
    assert result["ready"] is False
    assert result["candidate_inputs"]["claims_path"] == str(source)
    assert result["active_inputs"] == {}


def test_every_input_operation_and_handler_uses_one_exact_result_contract(tmp_path: Path):
    """Hand-built success or failure dictionaries must not drift between the three input operations."""
    claims_path = tmp_path / "contract-claims.json"
    claims_path.write_text(json.dumps([_claim()]), encoding="utf-8")
    kit_path = _brand_kit(tmp_path / "contract-brand-kit")
    missing_claims = tmp_path / "missing-claims.json"
    missing_kit = tmp_path / "missing-brand-kit"

    for brief_id in (
        "contract-preflight-success",
        "contract-preflight-failure",
        "contract-claims-success",
        "contract-claims-failure",
        "contract-brand-success",
        "contract-brand-failure",
        "contract-handler-preflight-success",
        "contract-handler-preflight-failure",
        "contract-handler-claims-success",
        "contract-handler-claims-failure",
        "contract-handler-brand-success",
        "contract-handler-brand-failure",
    ):
        save_brief(_brief(brief_id))

    operation_results = [
        preflight_inputs("contract-preflight-success", str(claims_path), str(kit_path), False),
        preflight_inputs("contract-preflight-failure", str(missing_claims), str(kit_path), False),
        resolve_and_persist_claims("contract-claims-success", str(claims_path), False),
        resolve_and_persist_claims("contract-claims-failure", str(missing_claims), False),
        resolve_and_persist_brand_kit("contract-brand-success", str(kit_path), False),
        resolve_and_persist_brand_kit("contract-brand-failure", str(missing_kit), False),
    ]
    handler_results = [
        json.loads(
            preflight(
                {
                    "campaign_brief_id": "contract-handler-preflight-success",
                    "claims_path": str(claims_path),
                    "brand_kit_path": str(kit_path),
                }
            )[0]["text"]
        ),
        json.loads(
            preflight(
                {
                    "campaign_brief_id": "contract-handler-preflight-failure",
                    "claims_path": str(missing_claims),
                    "brand_kit_path": str(kit_path),
                }
            )[0]["text"]
        ),
        json.loads(
            retrieve_claims({"campaign_brief_id": "contract-handler-claims-success", "source": str(claims_path)})[0][
                "text"
            ]
        ),
        json.loads(
            retrieve_claims({"campaign_brief_id": "contract-handler-claims-failure", "source": str(missing_claims)})[0][
                "text"
            ]
        ),
        json.loads(
            retrieve_brand({"campaign_brief_id": "contract-handler-brand-success", "brand_kit_path": str(kit_path)})[0][
                "text"
            ]
        ),
        json.loads(
            retrieve_brand({"campaign_brief_id": "contract-handler-brand-failure", "brand_kit_path": str(missing_kit)})[
                0
            ]["text"]
        ),
    ]

    for result in operation_results + handler_results:
        assert set(result) == _INPUT_RESULT_KEYS

    claims_success = operation_results[2]
    assert claims_success["active_inputs"]["claims_path"] == str(claims_path)
    assert claims_success["active_inputs"]["brand_kit_path"] is None
    assert claims_success["active_inputs"]["claims_count"] == 1
    assert claims_success["active_inputs"]["hashes"]["claims"]
    assert claims_success["candidate_inputs"]["claims_path"] == str(claims_path)
    assert claims_success["candidate_inputs"]["claims_count"] == 1
    assert claims_success["candidate_inputs"]["hashes"]["claims"]
    brand_success = operation_results[4]
    assert brand_success["active_inputs"]["brand_kit_path"] == str(kit_path)
    assert brand_success["active_inputs"]["claims_path"] is None
    assert brand_success["active_inputs"]["brand_files_count"] == 5
    assert brand_success["active_inputs"]["hashes"]["brand_files"]
    assert brand_success["candidate_inputs"]["brand_kit_path"] == str(kit_path)
    assert brand_success["candidate_inputs"]["brand_files_count"] == 5
    assert brand_success["candidate_inputs"]["hashes"]["brand_files"]
