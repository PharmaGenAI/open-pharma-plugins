"""Campaign Studio validation seals and non-mutating resumability contracts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import date
from importlib.resources import files
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

import open_pharma_plugins_campaign_studio as campaign_studio
from open_pharma_plugins_campaign_studio._campaign_store import (
    load_artifact,
    load_validation_artifact,
    save_artifact,
    save_brief,
    save_output,
    save_validation_artifact,
)
from open_pharma_plugins_campaign_studio._renderer import check_validation_gate, validation_input_fingerprint
from open_pharma_plugins_campaign_studio.tools.render_email import handle as render_email
from open_pharma_plugins_campaign_studio.tools.validate_claims_and_fair_balance import handle as validate_claims


@pytest.fixture(autouse=True)
def campaign_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_PHARMA_CAMPAIGN_STORE_DIR", str(tmp_path / "store"))


def _brief(campaign_brief_id: str = "status-test", **overrides: object) -> dict:
    return {
        "campaign_brief_id": campaign_brief_id,
        "campaign_name": "Status test",
        "country": "US",
        "policy_jurisdiction": "FDA",
        "mode": "promotional",
        "brand": "ONCORIX",
        "indication": "oncology",
        "target_segment": "HCP",
        "lifecycle_stage": "growth",
        "behavioral_objective": "Review approved evidence.",
        "educational_objective": None,
        "desired_kpi": ["open_rate"],
        "approved_claims_path": None,
        "channels": ["email", "banner"],
        "call_to_action": "Learn more",
        "call_to_action_url": "https://example.test/learn",
        "asset_dimensions": None,
        "brand_kit_path": None,
        "demo_mode": False,
        "language": "en",
        "localisation_notes": None,
        "required_safety_content": [],
        "required_legal_content": [],
        "delivery_constraints": None,
        "approval_workflow": "mlr_standard",
        "generated_at": "2026-08-28T00:00:00+00:00",
        **overrides,
    }


def _digest(path: Path) -> dict:
    payload = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}


def _seed(
    brief_id: str = "status-test",
    *,
    channels: list[str] | None = None,
    demo_mode: bool = False,
    complete_workflow: bool = False,
) -> Path:
    """Build true Task 1/2 artifacts so status tests exercise semantic completion."""
    from open_pharma_plugins_campaign_studio._inputs import preflight_inputs

    fixture_root = Path(str(files("open_pharma_plugins_campaign_studio") / "fixtures"))
    root = Path(os.environ["OPEN_PHARMA_CAMPAIGN_STORE_DIR"]).parent
    if demo_mode:
        claims_path = fixture_root / "sample_approved_claims.json"
        kit = fixture_root / "brand_kit"
    else:
        claims_path = root / "claims" / f"{brief_id}.json"
        claims_path.parent.mkdir(parents=True, exist_ok=True)
        claims_path.write_bytes((fixture_root / "sample_approved_claims.json").read_bytes())
        kit = root / "brand-kits" / brief_id
        shutil.copytree(fixture_root / "brand_kit", kit)
    brief = _brief(
        brief_id,
        channels=channels or ["email", "banner"],
        approved_claims_path=str(claims_path),
        brand_kit_path=str(kit),
        demo_mode=demo_mode,
    )
    save_brief(brief)
    assert preflight_inputs(brief_id, str(claims_path), str(kit), demo_mode)["ready"] is True
    claims = {claim["claim_id"]: claim for claim in load_artifact(brief_id, "approved-claims.json")}
    for channel in brief["channels"]:
        if channel == "email":
            copy = {
                "subject": {"text": claims["c-001"]["text"], "claim_ids": ["c-001"]},
                "preheader": {"text": claims["c-006"]["text"], "claim_ids": ["c-006"]},
                "headline": {"text": claims["c-002"]["text"], "claim_ids": ["c-002"]},
                "body": [{"text": claims["c-007"]["text"], "claim_ids": ["c-007"]}],
                "cta": {"text": "Learn more", "claim_ids": []},
            }
        elif channel == "banner":
            copy = {
                "headline": {"text": claims["c-001"]["text"], "claim_ids": ["c-001"]},
                "safety": {"text": claims["c-006"]["text"], "claim_ids": ["c-006"]},
                "cta": {"text": "Learn more", "claim_ids": []},
            }
        else:
            copy = {
                "headline": {"text": claims["c-001"]["text"], "claim_ids": ["c-001"]},
                "body": [{"text": claims["c-006"]["text"], "claim_ids": ["c-006"]}],
                "cta": {"text": "Learn more", "claim_ids": []},
            }
        save_artifact(
            brief_id,
            f"copy-{channel}.json",
            {
                "campaign_brief_id": brief_id,
                "channel": channel,
                "copy": copy,
                "generated_at": "2026-08-28T00:00:00+00:00",
            },
        )
    if complete_workflow:
        _seed_workflow(brief_id, claims)
    return kit / "legal.json"


def _seed_workflow(brief_id: str, claims: dict[str, dict]) -> None:
    _save_journey(brief_id)
    _save_message_architecture(brief_id, claims)


def _save_journey(brief_id: str) -> None:
    stages = []
    for stage, claim_id in (("aware", "c-001"), ("interested", "c-002"), ("acting", "c-003")):
        stages.append(
            {
                "stage": stage,
                "objective": f"Move HCPs to {stage}.",
                "key_messages": [claim_id],
                "channels": ["email"],
                "content_type": "promotional",
                "kpi": f"{stage}_engagement",
            }
        )
    save_artifact(
        brief_id,
        "audience-journey.json",
        {
            "campaign_brief_id": brief_id,
            "target_segment": "HCP",
            "stages": stages,
            "generated_at": "2026-08-28T00:00:00+00:00",
        },
    )


def _save_message_architecture(brief_id: str, claims: dict[str, dict]) -> None:
    tiers = []
    for tier, claim_id, stage in (
        ("primary", "c-001", "aware"),
        ("secondary", "c-002", "interested"),
        ("supporting", "c-003", "acting"),
    ):
        tiers.append(
            {
                "tier": tier,
                "message": claims[claim_id]["text"],
                "claim_ids": [claim_id],
                "audience_stage": stage,
                "rationale": f"Use {tier} evidence.",
            }
        )
    save_artifact(
        brief_id,
        "message-architecture.json",
        {
            "campaign_brief_id": brief_id,
            "brand": "ONCORIX",
            "indication": "oncology",
            "message_tiers": tiers,
            "fair_balance_statement": claims["c-006"]["text"],
            "fair_balance_sources": [
                {
                    "document_id": "c-006",
                    "document_name": claims["c-006"]["source_document"],
                    "page_number": None,
                    "excerpt": claims["c-006"]["source_reference"],
                }
            ],
            "generated_at": "2026-08-28T00:00:00+00:00",
        },
    )


def _passing_pre_render_report(brief_id: str, channels: list[str]) -> None:
    report = _canonical_pre_render_report(brief_id)
    assert report["channels_validated"] == channels


def _canonical_pre_render_report(brief_id: str) -> dict:
    result = json.loads(validate_claims({"campaign_brief_id": brief_id})[0]["text"])
    assert result["overall_pass"] is True, result
    report = load_validation_artifact(brief_id, "policy-checks.json")
    assert isinstance(report, dict)
    return report


@pytest.mark.parametrize("mutation", ["mutated", "deleted"])
def test_render_gate_rejects_changed_live_claims_source_after_validation(mutation: str):
    """A current persisted claims copy cannot authorize rendering after its selected source changes."""
    from open_pharma_plugins_campaign_studio._renderer import validation_gate_state

    brief_id = f"live-claims-{mutation}"
    _seed(brief_id, channels=["email"])
    _canonical_pre_render_report(brief_id)
    source = Path(load_artifact(brief_id, "campaign-brief.json")["approved_claims_path"])
    if mutation == "mutated":
        claims = json.loads(source.read_text(encoding="utf-8"))
        claims[0]["text"] += " tampered"
        source.write_text(json.dumps(claims), encoding="utf-8")
    else:
        source.unlink()

    gate = validation_gate_state(brief_id, "email")
    rendered = json.loads(render_email({"campaign_brief_id": brief_id})[0]["text"])

    assert gate["status"] == "stale"
    assert gate["code"] == "validation_input_invalid"
    assert rendered["error"]["code"] == "pre_render_validation_not_current"


def test_render_gate_rechecks_claim_expiry_after_validation(monkeypatch: pytest.MonkeyPatch):
    """A date rollover must revoke a seal even when every persisted byte is unchanged."""
    import open_pharma_plugins_campaign_studio._claim_engine as claim_engine
    from open_pharma_plugins_campaign_studio._inputs import preflight_inputs
    from open_pharma_plugins_campaign_studio._renderer import validation_gate_state

    brief_id = "claim-expiry-rollover"
    _seed(brief_id, channels=["email"])
    brief = load_artifact(brief_id, "campaign-brief.json")
    source = Path(brief["approved_claims_path"])
    claims = json.loads(source.read_text(encoding="utf-8"))
    claims[0]["expiry"] = "2026-08-31"
    source.write_text(json.dumps(claims), encoding="utf-8")
    assert preflight_inputs(brief_id, str(source), brief["brand_kit_path"], False)["ready"] is True
    _canonical_pre_render_report(brief_id)

    class Tomorrow(date):
        @classmethod
        def today(cls) -> date:
            return cls(2026, 9, 1)

    monkeypatch.setattr(claim_engine, "date", Tomorrow)

    gate = validation_gate_state(brief_id, "email")
    rendered = json.loads(render_email({"campaign_brief_id": brief_id})[0]["text"])

    assert gate["status"] == "stale"
    assert gate["code"] == "validation_input_invalid"
    assert rendered["error"]["code"] == "pre_render_validation_not_current"


def test_render_gate_rejects_brand_source_drift_even_when_validation_seals_it():
    """Validation cannot bless live brand bytes that disagree with the activated manifest/provenance."""
    from open_pharma_plugins_campaign_studio._renderer import validation_gate_state

    brief_id = "brand-source-drift"
    _seed(brief_id, channels=["email"])
    brief = load_artifact(brief_id, "campaign-brief.json")
    palette_path = Path(brief["brand_kit_path"]) / "palette.json"
    palette = json.loads(palette_path.read_text(encoding="utf-8"))
    palette["primary"] = "#123456"
    palette_path.write_text(json.dumps(palette), encoding="utf-8")
    _canonical_pre_render_report(brief_id)

    gate = validation_gate_state(brief_id, "email")
    rendered = json.loads(render_email({"campaign_brief_id": brief_id})[0]["text"])

    assert gate["status"] == "stale"
    assert gate["code"] == "validation_input_invalid"
    assert rendered["error"]["code"] == "pre_render_validation_not_current"


@pytest.mark.parametrize(
    "mutation",
    ["missing_field", "extra_field", "malformed_claims", "inconsistent_claim", "shallow_forgery"],
)
def test_render_gate_rejects_noncanonical_passing_validation_reports(mutation: str):
    """Only the complete canonical report can authorize rendering, regardless of a matching fingerprint."""
    from open_pharma_plugins_campaign_studio._renderer import validation_gate_state

    brief_id = f"forged-report-{mutation}"
    _seed(brief_id, channels=["email"])
    report = _canonical_pre_render_report(brief_id)
    if mutation == "missing_field":
        report.pop("policy_hash")
    elif mutation == "extra_field":
        report["undeclared"] = True
    elif mutation == "malformed_claims":
        report["claims_checked"] = {}
    elif mutation == "inconsistent_claim":
        report["channel_results"]["email"]["claims_checked"][0]["statement"] = "forged statement"
    else:
        report = {
            "campaign_brief_id": brief_id,
            "channels_validated": ["email"],
            "overall_pass": True,
            "policy_checks": [],
            "input_fingerprint": validation_input_fingerprint(brief_id, ["email"]),
        }
    save_validation_artifact(brief_id, "policy-checks.json", report)

    gate = validation_gate_state(brief_id, "email")
    rendered = json.loads(render_email({"campaign_brief_id": brief_id})[0]["text"])

    assert gate["status"] == "failed"
    assert gate["code"] == "malformed_validation_report"
    assert rendered["error"]["code"] == "pre_render_validation_not_current"


def test_forged_matching_fingerprint_cannot_authorize_unapproved_paraphrase():
    """A fresh fingerprint cannot promote an altered statement that exact claim governance rejects."""
    from open_pharma_plugins_campaign_studio._renderer import validation_gate_state

    brief_id = "forged-unapproved-paraphrase"
    _seed(brief_id, channels=["email"])
    copy = load_artifact(brief_id, "copy-email.json")
    copy["copy"]["headline"]["text"] = "ONCORIX improved survival."
    save_artifact(brief_id, "copy-email.json", copy)
    save_validation_artifact(
        brief_id,
        "policy-checks.json",
        {
            "campaign_brief_id": brief_id,
            "channels_validated": ["email"],
            "overall_pass": True,
            "policy_checks": [],
            "input_fingerprint": validation_input_fingerprint(brief_id, ["email"]),
        },
    )

    gate = validation_gate_state(brief_id, "email")
    rendered = json.loads(render_email({"campaign_brief_id": brief_id})[0]["text"])

    assert gate["status"] == "failed"
    assert gate["code"] == "malformed_validation_report"
    assert rendered["error"]["code"] == "pre_render_validation_not_current"


def _passing_rendered_report(brief_id: str, outputs: list[Path]) -> None:
    template_path = Path(str(files("open_pharma_plugins_campaign_studio") / "templates" / "email.html.j2"))
    template_payload = template_path.read_bytes()
    save_artifact(
        brief_id,
        "render-provenance-email.json",
        {
            "campaign_brief_id": brief_id,
            "channel": "email",
            "template": {
                "kind": "default",
                "path": str(template_path.resolve()),
                "sha256": hashlib.sha256(template_payload).hexdigest(),
                "size": len(template_payload),
            },
        },
    )
    save_validation_artifact(
        brief_id,
        "rendered-assets.json",
        {
            "campaign_brief_id": brief_id,
            "overall_pass": True,
            "pre_render_input_fingerprint": validation_input_fingerprint(brief_id, ["email", "banner"]),
            "template_sources": [],
            "outputs": [
                {"path": str(path), "sha256": _digest(path)["sha256"], "size": _digest(path)["size"]}
                for path in outputs
            ],
        },
    )


@pytest.mark.parametrize("mutable", ["brand", "policy", "template", "copy"])
def test_pre_render_gate_rejects_every_mutable_validation_input(mutable: str):
    """Dropping any live seal input would let a changed campaign render unchecked."""
    from open_pharma_plugins_campaign_studio._renderer import validation_input_payload

    brief_id = f"freshness-{mutable}"
    legal_path = _seed(brief_id)
    channels = ["email", "banner"]
    _passing_pre_render_report(brief_id, channels)
    assert check_validation_gate(brief_id) is None
    baseline = validation_input_payload(brief_id, channels)

    if mutable == "brand":
        path = legal_path
        replacement = b'{"isi":"Changed legal language."}'
    elif mutable == "policy":
        path = Path(str(files("open_pharma_plugins_campaign_studio") / "policy" / "rules.json"))
        replacement = path.read_bytes() + b"\n"
    elif mutable == "template":
        path = Path(str(files("open_pharma_plugins_campaign_studio") / "templates" / "email.html.j2"))
        replacement = path.read_bytes() + b"\n<!-- changed -->\n"
    else:
        original_copy = load_artifact(brief_id, "copy-email.json")
        save_artifact(brief_id, "copy-email.json", {"channel": "email", "copy": {"headline": "changed"}})
        try:
            assert validation_input_payload(brief_id, channels) != baseline
            assert (
                check_validation_gate(brief_id)
                == "Campaign inputs changed after validation. Re-run validation before rendering or packaging."
            )
        finally:
            save_artifact(brief_id, "copy-email.json", original_copy)
        return

    original = path.read_bytes()
    try:
        path.write_bytes(replacement)
        assert validation_input_payload(brief_id, channels) != baseline
        assert (
            check_validation_gate(brief_id)
            == "Campaign inputs changed after validation. Re-run validation before rendering or packaging."
        )
    finally:
        path.write_bytes(original)


def test_rendered_gate_requires_current_pre_render_seal_and_output_bytes():
    """A changed output or pre-render input must invalidate a rendered-assets report."""
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate

    brief_id = "rendered-gate"
    _seed(brief_id)
    _passing_pre_render_report(brief_id, ["email", "banner"])
    email = save_output(brief_id, "email.html", "<html>draft</html>")
    banner = save_output(brief_id, "banner.svg", "<svg>draft</svg>")
    _passing_rendered_report(brief_id, [email, banner])
    assert rendered_validation_gate(brief_id) is None

    original = email.read_bytes()
    try:
        email.write_bytes(b"<html>changed</html>")
        assert "changed after rendered validation" in (rendered_validation_gate(brief_id) or "")
    finally:
        email.write_bytes(original)

    malformed = {"overall_pass": True, "outputs": "not-a-list"}
    save_validation_artifact(brief_id, "rendered-assets.json", malformed)
    assert "malformed" in (rendered_validation_gate(brief_id) or "").casefold()


def test_validation_payload_seals_server_version_and_missing_live_brand_files():
    """Omitting version or treating a deleted selected brand asset as absent defeats a seal."""
    from open_pharma_plugins_campaign_studio import __version__
    from open_pharma_plugins_campaign_studio._renderer import validation_input_payload

    brief_id = "missing-brand-file"
    legal_path = _seed(brief_id)
    channels = ["email", "banner"]
    _passing_pre_render_report(brief_id, channels)
    payload = validation_input_payload(brief_id, channels)
    assert payload["campaign_studio_version"] == __version__
    original = legal_path.read_bytes()
    try:
        legal_path.unlink()
        live = validation_input_payload(brief_id, channels)
        state = live["selected_brand_files"]["legal.json"]
        assert {key: state[key] for key in ("path", "exists", "sha256", "size")} == {
            "path": str(legal_path),
            "exists": False,
            "sha256": None,
            "size": None,
        }
        assert state["error"]
        assert live != payload
        assert (
            check_validation_gate(brief_id)
            == "Campaign inputs changed after validation. Re-run validation before rendering or packaging."
        )
    finally:
        legal_path.write_bytes(original)


def test_status_is_non_creating_and_recommends_the_first_missing_workflow_step():
    """Status must not turn unknown or unsafe IDs into writable campaign directories."""
    from open_pharma_plugins_campaign_studio._campaign_store import existing_campaign_path, store_root_path
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle

    root_before = store_root_path().exists()
    unknown = json.loads(handle({"campaign_brief_id": "does-not-exist"})[0]["text"])
    assert unknown["error"]["code"] == "campaign_not_found"
    assert store_root_path().exists() is root_before
    assert existing_campaign_path("does-not-exist") is None
    unsafe = json.loads(handle({"campaign_brief_id": "../escape"})[0]["text"])
    assert unsafe["error"]["code"] == "unsafe_campaign_brief_id"

    brief_id = "workflow-status"
    save_brief(_brief(brief_id, channels=["email"]))
    first = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
    assert first["next_step"] == {"tool": "preflight_campaign_inputs", "channel": None}
    _seed(brief_id, channels=["email"], demo_mode=True)
    status = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
    assert status["demo_mode"] is True
    assert status["demo_provenance_disclosure"]
    assert status["next_step"] == {"tool": "generate_audience_journey", "channel": None}
    assert status["pre_render_validation"]["status"] == "missing"
    assert all(Path(path).is_absolute() for path in status["artifact_paths"].values())

    claims = {claim["claim_id"]: claim for claim in load_artifact(brief_id, "approved-claims.json")}
    _save_journey(brief_id)
    status = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
    assert status["next_step"] == {"tool": "generate_message_architecture", "channel": None}
    _save_message_architecture(brief_id, claims)
    status = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
    assert status["next_step"] == {"tool": "validate_claims_and_fair_balance", "channel": None}


def test_status_is_discovered_with_a_strict_read_only_schema_and_current_paths():
    """Removing the MCP registration or read-only status contract must be observable to clients."""
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import TOOL, handle

    spec = next(tool for tool in campaign_studio.list_tools() if tool["name"] == "get_campaign_status")
    assert spec["inputSchema"]["properties"] == {
        "campaign_brief_id": {"description": "Campaign brief ID", "type": "string"}
    }
    assert spec["inputSchema"]["required"] == ["campaign_brief_id"]
    assert TOOL["name"] == "get_campaign_status"

    brief_id = "status-current"
    _seed(brief_id)
    _passing_pre_render_report(brief_id, ["email", "banner"])
    email = save_output(brief_id, "email.html", "<html>draft</html>")
    banner = save_output(brief_id, "banner.svg", "<svg>draft</svg>")
    _passing_rendered_report(brief_id, [email, banner])
    response = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
    assert response["pre_render_validation"]["status"] == "current"
    assert response["rendered_validation"]["status"] == "current"
    assert response["rendered_paths"] == sorted([str(email.resolve()), str(banner.resolve())])


def test_status_recommends_each_remaining_channel_in_renderer_workflow_order():
    """A resumable campaign must name the first concrete renderer, not alphabetical set order."""
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle

    brief_id = "workflow-render-order"
    _seed(brief_id, channels=["email", "banner"], complete_workflow=True)
    _passing_pre_render_report(brief_id, ["email", "banner"])

    before_render = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
    assert before_render["next_step"] == {"tool": "render_email", "channel": "email"}

    email = save_output(brief_id, "email.html", "email")
    after_email = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
    assert after_email["next_step"] == {"tool": "render_banner", "channel": "banner"}

    banner = save_output(brief_id, "banner.svg", "banner")
    after_outputs = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
    assert after_outputs["next_step"] == {"tool": "validate_rendered_assets", "channel": None}

    _passing_rendered_report(brief_id, [email, banner])
    ready_for_review = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
    assert ready_for_review["next_step"] == {"tool": "render_mlr_review", "channel": None}


def test_status_and_rendered_gate_reject_symlinked_campaign_and_outputs(tmp_path: Path):
    """Following a campaign or outputs symlink would expose data outside the configured store."""
    from open_pharma_plugins_campaign_studio._campaign_store import store_root_path
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle

    _seed("real-campaign")
    root = store_root_path()
    outside_campaign = tmp_path / "outside-campaign"
    outside_campaign.mkdir()
    (root / "campaigns" / "symlinked-campaign").symlink_to(outside_campaign, target_is_directory=True)
    campaign_status = json.loads(handle({"campaign_brief_id": "symlinked-campaign"})[0]["text"])
    assert campaign_status["error"]["code"] == "unsafe_campaign_path"
    assert not any(outside_campaign.iterdir())

    brief_id = "symlinked-outputs"
    _seed(brief_id)
    _passing_pre_render_report(brief_id, ["email", "banner"])
    email = save_output(brief_id, "email.html", "e")
    banner = save_output(brief_id, "banner.svg", "b")
    _passing_rendered_report(brief_id, [email, banner])
    outputs = root / "campaigns" / brief_id / "outputs"
    outside_outputs = tmp_path / "outside-outputs"
    outside_outputs.mkdir()
    (outside_outputs / "email.html").write_text("e")
    (outside_outputs / "banner.svg").write_text("b")
    shutil.rmtree(outputs)
    outputs.symlink_to(outside_outputs, target_is_directory=True)
    gate = rendered_validation_gate_state(brief_id)
    assert gate["status"] == "failed"
    assert gate["code"] == "unsafe_outputs_directory"
    output_status = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
    assert output_status["rendered_validation"]["status"] == "failed"
    assert output_status["rendered_paths"] == []


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("palette", {"primary": "#abcdef"}),
        ("legal", {"isi": "Persisted legal text changed."}),
    ],
)
def test_validation_payload_seals_all_persisted_renderer_brand_semantics(field: str, replacement: dict):
    """Ignoring persisted palette or legal values lets renderer behavior change under a passing seal."""
    from open_pharma_plugins_campaign_studio._renderer import validation_input_payload

    brief_id = f"brand-semantic-{field}"
    _seed(brief_id)
    manifest = load_artifact(brief_id, "brand-components.json")
    manifest["palette"] = {"primary": "#123456"}
    save_artifact(brief_id, "brand-components.json", manifest)
    baseline = validation_input_payload(brief_id, ["email", "banner"])
    manifest = load_artifact(brief_id, "brand-components.json")
    manifest[field] = replacement
    save_artifact(brief_id, "brand-components.json", manifest)
    changed = validation_input_payload(brief_id, ["email", "banner"])
    assert changed["brand_components"][field] == replacement
    assert changed != baseline


def test_validation_payload_rejects_same_byte_brand_file_symlink_swap():
    """Following a same-byte brand-file symlink conceals a mutable source-identity change."""
    from open_pharma_plugins_campaign_studio._renderer import validation_input_payload

    brief_id = "brand-symlink-swap"
    legal_path = _seed(brief_id)
    baseline = validation_input_payload(brief_id, ["email", "banner"])
    replacement = legal_path.with_name("same-byte-legal.json")
    original = legal_path.read_bytes()
    replacement.write_bytes(original)
    try:
        legal_path.unlink()
        legal_path.symlink_to(replacement)
        changed = validation_input_payload(brief_id, ["email", "banner"])
        state = changed["selected_brand_files"]["legal.json"]
        assert state["kind"] == "symlink"
        assert state["error"]
        assert changed != baseline
    finally:
        legal_path.unlink()
        legal_path.write_bytes(original)
        replacement.unlink()


def test_validation_payload_rejects_same_byte_brand_root_symlink_swap(tmp_path: Path):
    """A moved kit directory can preserve a file inode, so root symlinks must be sealed as unsafe."""
    from open_pharma_plugins_campaign_studio._renderer import validation_input_payload

    brief_id = "brand-root-symlink-swap"
    legal_path = _seed(brief_id)
    baseline = validation_input_payload(brief_id, ["email", "banner"])
    kit = legal_path.parent
    moved_kit = tmp_path / "moved-brand-kit"
    try:
        kit.rename(moved_kit)
        kit.symlink_to(moved_kit, target_is_directory=True)
        changed = validation_input_payload(brief_id, ["email", "banner"])
        state = changed["selected_brand_files"]["legal.json"]
        assert state["kind"] == "unsafe_path"
        assert state["error"]
        assert changed != baseline
    finally:
        if kit.is_symlink():
            kit.unlink()
        moved_kit.rename(kit)


@pytest.mark.parametrize("artifact_name", ["approved-claims.json", "brand-components.json", "copy-email.json"])
def test_seals_and_status_are_total_for_corrupt_json_and_mixed_channels(artifact_name: str):
    """Corrupt artifacts and mixed channel lists must produce fail-closed JSON, never storage exceptions."""
    from open_pharma_plugins_campaign_studio._campaign_store import existing_artifact_path
    from open_pharma_plugins_campaign_studio._renderer import validation_gate_state, validation_input_payload
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle

    brief_id = "corrupt-seals"
    _seed(brief_id)
    _passing_pre_render_report(brief_id, ["email", "banner"])
    artifact_path = existing_artifact_path(brief_id, artifact_name)
    assert artifact_path is not None
    original = artifact_path.read_bytes()
    try:
        artifact_path.write_bytes(b"{")
        payload = validation_input_payload(brief_id, ["email", "banner"])
        assert any(error["code"] == "artifact_json_unreadable" for error in payload["errors"])
        gate = validation_gate_state(brief_id)
        assert gate["status"] == "stale"
        assert gate["code"] == "validation_input_invalid"
        status = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
        assert status["pre_render_validation"]["status"] == "stale"
    finally:
        artifact_path.write_bytes(original)

    mixed = validation_input_payload(brief_id, ["email", 1])
    assert any(error["code"] == "invalid_channels" for error in mixed["errors"])


def test_pre_render_status_fails_for_invalid_brief_channels_without_raising():
    """A non-string or unsupported brief channel is a malformed sealed contract, not an iterable accident."""
    from open_pharma_plugins_campaign_studio._renderer import validation_gate_state
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle

    brief_id = "mixed-brief-channels"
    _seed(brief_id)
    brief = load_artifact(brief_id, "campaign-brief.json")
    brief["channels"] = ["email", 3, "unsupported"]
    save_artifact(brief_id, "campaign-brief.json", brief)
    gate = validation_gate_state(brief_id)
    assert gate == {
        "status": "failed",
        "code": "invalid_brief_channels",
        "reason": "Campaign brief channels are malformed.",
    }
    status = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
    assert status["pre_render_validation"]["status"] == "failed"


def _sealed_outputs(brief_id: str) -> tuple[Path, Path]:
    _seed(brief_id)
    _passing_pre_render_report(brief_id, ["email", "banner"])
    email = save_output(brief_id, "email.html", "e")
    banner = save_output(brief_id, "banner.svg", "b")
    _passing_rendered_report(brief_id, [email, banner])
    return email, banner


@pytest.mark.parametrize("mutation", ["partial", "duplicate", "unexpected"])
def test_rendered_gate_marks_changed_declared_output_sets_stale(mutation: str):
    """A report with a partial, duplicate, or unrelated output set cannot be current for the brief."""
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state

    brief_id = f"output-set-{mutation}"
    email, banner = _sealed_outputs(brief_id)
    entries = [
        {"path": str(email.resolve()), "sha256": _digest(email)["sha256"], "size": _digest(email)["size"]},
        {"path": str(banner.resolve()), "sha256": _digest(banner)["sha256"], "size": _digest(banner)["size"]},
    ]
    if mutation == "partial":
        entries = entries[:1]
    elif mutation == "duplicate":
        entries = [entries[0], entries[0]]
    else:
        unrelated = save_output(brief_id, "unrelated.txt", "x")
        entries.append({"path": str(unrelated.resolve()), "sha256": _digest(unrelated)["sha256"], "size": 1})
    save_validation_artifact(
        brief_id,
        "rendered-assets.json",
        {
            "campaign_brief_id": brief_id,
            "overall_pass": True,
            "pre_render_input_fingerprint": validation_input_fingerprint(brief_id, ["email", "banner"]),
            "template_sources": [],
            "outputs": entries,
        },
    )
    gate = rendered_validation_gate_state(brief_id)
    assert gate["status"] == "stale"
    assert gate["code"] == "rendered_output_set_changed"


@pytest.mark.parametrize(
    ("field", "value"),
    [("sha256", "A" * 64), ("sha256", "not-a-hash"), ("size", True), ("size", -1), ("size", 1.0)],
)
def test_rendered_gate_fails_malformed_hash_and_size_declarations(field: str, value: object):
    """Accepting malformed output metadata makes a rendered seal non-verifiable."""
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state

    brief_id = f"bad-output-{field}-{str(value).replace('-', 'minus')}"
    email, banner = _sealed_outputs(brief_id)
    entries = [
        {"path": str(email.resolve()), "sha256": _digest(email)["sha256"], "size": _digest(email)["size"]},
        {"path": str(banner.resolve()), "sha256": _digest(banner)["sha256"], "size": _digest(banner)["size"]},
    ]
    entries[0][field] = value
    save_validation_artifact(
        brief_id,
        "rendered-assets.json",
        {
            "campaign_brief_id": brief_id,
            "overall_pass": True,
            "pre_render_input_fingerprint": validation_input_fingerprint(brief_id, ["email", "banner"]),
            "template_sources": [],
            "outputs": entries,
        },
    )
    gate = rendered_validation_gate_state(brief_id)
    assert gate["status"] == "failed"
    assert gate["code"] == "malformed_rendered_output"


def test_deleted_or_symlinked_rendered_output_is_stale_in_gate_and_status():
    """Deleted or symlink-swapped rendered files are stale output changes, not a passing review state."""
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle

    brief_id = "deleted-output-status"
    email, _banner = _sealed_outputs(brief_id)
    original = email.read_bytes()
    try:
        email.unlink()
        deleted = rendered_validation_gate_state(brief_id)
        assert deleted["status"] == "stale"
        assert deleted["code"] == "rendered_output_missing"
        status = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
        assert status["rendered_validation"]["status"] == "stale"
    finally:
        email.write_bytes(original)

    replacement = email.with_name("same-byte-email.html")
    replacement.write_bytes(original)
    try:
        email.unlink()
        email.symlink_to(replacement)
        symlinked = rendered_validation_gate_state(brief_id)
        assert symlinked["status"] == "stale"
        assert symlinked["code"] == "rendered_output_unsafe"
    finally:
        email.unlink()
        email.write_bytes(original)
        replacement.unlink()


def test_status_uses_semantic_artifact_completion_for_corrupt_claims_and_mismatched_copy():
    """Existence alone must not allow a corrupt workflow artifact to skip its repair tool."""
    from open_pharma_plugins_campaign_studio._campaign_store import existing_artifact_path
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle

    brief_id = "semantic-status"
    _seed(brief_id, complete_workflow=True)
    claims_path = existing_artifact_path(brief_id, "approved-claims.json")
    assert claims_path is not None
    original_claims = claims_path.read_bytes()
    try:
        claims_path.write_bytes(b"{")
        corrupt = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
        assert "preflight_inputs" in corrupt["missing_steps"]
        assert corrupt["next_step"] == {"tool": "preflight_campaign_inputs", "channel": None}
        assert corrupt["artifact_diagnostics"]["approved-claims.json"]["status"] == "invalid"
    finally:
        claims_path.write_bytes(original_claims)

    copy_path = existing_artifact_path(brief_id, "copy-email.json")
    assert copy_path is not None
    original_copy = copy_path.read_bytes()
    try:
        copy_path.write_bytes(b"{")
        corrupt_copy = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
        assert "copy:email" in corrupt_copy["missing_steps"]
        assert corrupt_copy["next_step"] == {"tool": "generate_channel_copy", "channel": "email"}
        assert corrupt_copy["artifact_diagnostics"]["copy-email.json"]["status"] == "invalid"
    finally:
        copy_path.write_bytes(original_copy)

    save_artifact(brief_id, "copy-email.json", {"campaign_brief_id": "other", "channel": "email", "copy": {}})
    mismatched = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
    assert "copy:email" in mismatched["missing_steps"]
    assert mismatched["next_step"] == {"tool": "generate_channel_copy", "channel": "email"}
    assert mismatched["artifact_diagnostics"]["copy-email.json"]["status"] == "invalid"


def test_deeply_nested_json_is_a_structured_direct_and_mcp_status_fault():
    """A parser recursion fault must remain normal status JSON instead of a protocol exception."""
    from open_pharma_plugins_campaign_studio._campaign_store import existing_artifact_path, store_root_path
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle

    brief_id = "deep-status-json"
    _seed(brief_id)
    provenance_path = existing_artifact_path(brief_id, "input-provenance.json")
    assert provenance_path is not None
    provenance_path.write_bytes(b"[" * 10_000 + b"0" + b"]" * 10_000)

    direct = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
    assert direct["artifact_diagnostics"]["input-provenance.json"]["status"] == "invalid"

    async def exercise() -> object:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "open_pharma_plugins_campaign_studio"],
            env={**os.environ, "OPEN_PHARMA_CAMPAIGN_STORE_DIR": str(store_root_path())},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("get_campaign_status", {"campaign_brief_id": brief_id})

    response = anyio.run(exercise)
    assert response.isError is False
    mcp_status = json.loads(response.content[0].text)
    assert mcp_status["artifact_diagnostics"]["input-provenance.json"]["status"] == "invalid"


@pytest.mark.parametrize("resource_directory", ["policy", "templates"])
def test_validation_payload_seals_resource_directory_symlink_identity(resource_directory: str, tmp_path: Path):
    """A directory symlink replacement changes the seal even when every leaf byte is unchanged."""
    from open_pharma_plugins_campaign_studio._renderer import validation_input_payload

    brief_id = f"resource-directory-{resource_directory}"
    _seed(brief_id)
    baseline = validation_input_payload(brief_id, ["email", "banner"])
    package_root = Path(str(files("open_pharma_plugins_campaign_studio")))
    directory = package_root / resource_directory
    moved = tmp_path / f"moved-{resource_directory}"
    try:
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        changed = validation_input_payload(brief_id, ["email", "banner"])
        state = changed["resource_directories"][resource_directory]
        assert state["kind"] == "symlink"
        assert state["error"]
        assert changed != baseline
    finally:
        if directory.is_symlink():
            directory.unlink()
        moved.rename(directory)


def test_unreported_fifo_preserves_safe_rendered_paths_and_fails_rendered_validation():
    """One unsafe output entry must be visible without hiding verified rendered files."""
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle

    brief_id = "fifo-output"
    email, banner = _sealed_outputs(brief_id)
    fifo = email.parent / "unreported.fifo"
    os.mkfifo(fifo)
    try:
        status = json.loads(handle({"campaign_brief_id": brief_id})[0]["text"])
        assert status["rendered_paths"] == sorted([str(email.resolve()), str(banner.resolve())])
        assert status["rendered_path_errors"] == [
            {"code": "unsafe_output_entry", "message": "Campaign outputs contain an unsafe entry: unreported.fifo."}
        ]
        assert status["rendered_validation"]["status"] == "failed"
        assert status["rendered_validation"]["code"] == "unsafe_outputs_directory"
    finally:
        fifo.unlink()
