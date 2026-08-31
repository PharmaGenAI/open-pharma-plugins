"""Tests for the campaign-studio capability."""

import json

import open_pharma_plugins_campaign_studio as cs


def _create_brief(name: str, *, channels: list[str], mode: str = "promotional") -> str:
    result = cs.get_handler("create_campaign_brief")(
        {
            "campaign_name": name,
            "country": "US",
            "policy_jurisdiction": "FDA",
            "mode": mode,
            "brand": "ONCORIX",
            "indication": "oncology",
            "target_segment": "oncologists",
            "behavioral_objective": "awareness",
            "desired_kpi": ["reach"],
            "call_to_action": "Learn more",
            "call_to_action_url": "https://example.test/learn",
            "channels": channels,
            "demo_mode": True,
        }
    )
    campaign_brief_id = json.loads(result[0]["text"])["campaign_brief_id"]
    preflight = cs.get_handler("preflight_campaign_inputs")({"campaign_brief_id": campaign_brief_id, "demo_mode": True})
    assert json.loads(preflight[0]["text"])["ready"] is True
    return campaign_brief_id


# ---------------------------------------------------------------------------
# Package sanity
# ---------------------------------------------------------------------------


def test_lists_sixteen_tools():
    names = {t["name"] for t in cs.list_tools()}
    assert names == {
        "create_campaign_brief",
        "export_mlr_package",
        "generate_audience_journey",
        "generate_channel_copy",
        "generate_message_architecture",
        "get_campaign_status",
        "package_mlr_submission",
        "preflight_campaign_inputs",
        "render_banner",
        "render_email",
        "render_mlr_review",
        "render_poster",
        "retrieve_approved_claims",
        "retrieve_brand_components",
        "validate_claims_and_fair_balance",
        "validate_rendered_assets",
    }


def test_version():
    assert cs.__version__ == "1.1.0"


# ---------------------------------------------------------------------------
# All handlers exist and are callable
# ---------------------------------------------------------------------------


def test_create_campaign_brief_handler():
    assert callable(cs.get_handler("create_campaign_brief"))


def test_generate_audience_journey_handler():
    assert callable(cs.get_handler("generate_audience_journey"))


def test_generate_channel_copy_handler():
    assert callable(cs.get_handler("generate_channel_copy"))


def test_generate_message_architecture_handler():
    assert callable(cs.get_handler("generate_message_architecture"))


def test_package_mlr_submission_handler():
    assert callable(cs.get_handler("package_mlr_submission"))


def test_render_banner_handler():
    assert callable(cs.get_handler("render_banner"))


def test_render_email_handler():
    assert callable(cs.get_handler("render_email"))


def test_render_poster_handler():
    assert callable(cs.get_handler("render_poster"))


def test_retrieve_approved_claims_handler():
    assert callable(cs.get_handler("retrieve_approved_claims"))


def test_retrieve_brand_components_handler():
    assert callable(cs.get_handler("retrieve_brand_components"))


def test_validate_claims_handler():
    assert callable(cs.get_handler("validate_claims_and_fair_balance"))


def test_validate_rendered_assets_handler():
    assert callable(cs.get_handler("validate_rendered_assets"))


# ---------------------------------------------------------------------------
# Offline tool calls — retrieve fixtures
# ---------------------------------------------------------------------------


def test_create_campaign_brief():
    result = cs.get_handler("create_campaign_brief")(
        {
            "campaign_name": "Test Campaign",
            "country": "US",
            "policy_jurisdiction": "FDA",
            "mode": "promotional",
            "brand": "TestDrug",
            "indication": "oncology",
            "target_segment": "community oncologists",
            "behavioral_objective": "Drive awareness",
            "desired_kpi": ["reach"],
            "call_to_action": "Learn more",
            "call_to_action_url": "https://example.test/learn",
            "channels": ["email"],
            "demo_mode": True,
        }
    )
    data = json.loads(result[0]["text"])
    assert "campaign_brief_id" in data


def test_retrieve_approved_claims():
    brief = cs.get_handler("create_campaign_brief")(
        {
            "campaign_name": "Claims Test",
            "country": "US",
            "policy_jurisdiction": "FDA",
            "mode": "promotional",
            "brand": "TestDrug",
            "indication": "oncology",
            "target_segment": "oncologists",
            "behavioral_objective": "awareness",
            "desired_kpi": ["reach"],
            "call_to_action": "Learn more",
            "call_to_action_url": "https://example.test/learn",
            "channels": ["email"],
            "demo_mode": True,
        }
    )
    brief_id = json.loads(brief[0]["text"])["campaign_brief_id"]
    result = cs.get_handler("retrieve_approved_claims")({"campaign_brief_id": brief_id, "demo_mode": True})
    data = json.loads(result[0]["text"])
    assert isinstance(data, dict)


def test_retrieve_brand_components():
    brief = cs.get_handler("create_campaign_brief")(
        {
            "campaign_name": "Brand Test",
            "country": "US",
            "policy_jurisdiction": "FDA",
            "mode": "promotional",
            "brand": "TestDrug",
            "indication": "oncology",
            "target_segment": "oncologists",
            "behavioral_objective": "awareness",
            "desired_kpi": ["reach"],
            "call_to_action": "Learn more",
            "call_to_action_url": "https://example.test/learn",
            "channels": ["email"],
            "demo_mode": True,
        }
    )
    brief_id = json.loads(brief[0]["text"])["campaign_brief_id"]
    result = cs.get_handler("retrieve_brand_components")({"campaign_brief_id": brief_id, "demo_mode": True})
    data = json.loads(result[0]["text"])
    assert isinstance(data, dict)


def test_promotional_copy_without_claim_citations_is_rejected():
    brief_id = _create_brief("Unsupported copy", channels=["banner"])
    cs.get_handler("retrieve_approved_claims")({"campaign_brief_id": brief_id, "demo_mode": True})

    result = cs.get_handler("generate_channel_copy")(
        {
            "campaign_brief_id": brief_id,
            "channel": "banner",
            "copy_json": json.dumps(
                {
                    "headline": {"text": "ONCORIX triples survival versus every competitor", "claim_ids": []},
                    "cta": {"text": "Learn more", "claim_ids": []},
                }
            ),
        }
    )
    data = json.loads(result[0]["text"])

    assert "errors" in data
    assert any("approved claim" in error for error in data["errors"])


def test_validation_defensively_fails_uncited_promotional_copy():
    from open_pharma_plugins_campaign_studio._campaign_store import save_artifact

    brief_id = _create_brief("Bypassed copy", channels=["banner"])
    cs.get_handler("retrieve_approved_claims")({"campaign_brief_id": brief_id, "demo_mode": True})
    save_artifact(
        brief_id,
        "copy-banner.json",
        {
            "campaign_brief_id": brief_id,
            "channel": "banner",
            "copy": {
                "headline": {"text": "ONCORIX triples survival versus every competitor", "claim_ids": []},
                "cta": {"text": "Learn more", "claim_ids": []},
            },
        },
    )

    result = cs.get_handler("validate_claims_and_fair_balance")({"campaign_brief_id": brief_id})
    data = json.loads(result[0]["text"])

    assert data["overall_pass"] is False
    assert any(item["check_name"] == "missing_claim_citation" for item in data["failures"])


def test_banner_escapes_untrusted_svg_markup():
    from pathlib import Path

    brief_id = _create_brief("SVG escaping", channels=["banner"])
    copy_result = cs.get_handler("generate_channel_copy")(
        {
            "campaign_brief_id": brief_id,
            "channel": "banner",
            "copy_json": json.dumps(
                {
                    "headline": {
                        "text": "Among responders, 68% maintained response at 12 months.",
                        "claim_ids": ["c-004"],
                    },
                    "safety": {
                        "text": "The most common adverse events (>=15%) with ONCORIX were fatigue (28.1%), rash (19.4%), and diarrhoea (16.7%).",
                        "claim_ids": ["c-007"],
                    },
                    "cta": {"text": "Learn more", "claim_ids": []},
                }
            ),
        }
    )
    assert "errors" not in json.loads(copy_result[0]["text"])
    validation = cs.get_handler("validate_claims_and_fair_balance")({"campaign_brief_id": brief_id})
    assert json.loads(validation[0]["text"])["overall_pass"] is True

    result = cs.get_handler("render_banner")({"campaign_brief_id": brief_id})
    path = Path(json.loads(result[0]["text"])["file_path"])
    svg = path.read_text()

    assert ">=15%" not in svg
    assert "&gt;=15%" in svg


def test_copy_change_invalidates_previous_validation():
    from open_pharma_plugins_campaign_studio._campaign_store import load_artifact, save_artifact
    from open_pharma_plugins_campaign_studio._renderer import check_validation_gate

    brief_id = _create_brief("Stale validation", channels=["banner"])
    copy_result = cs.get_handler("generate_channel_copy")(
        {
            "campaign_brief_id": brief_id,
            "channel": "banner",
            "copy_json": json.dumps(
                {
                    "headline": {
                        "text": "Among responders, 68% maintained response at 12 months.",
                        "claim_ids": ["c-004"],
                    },
                    "safety": {
                        "text": "Treatment discontinuation due to adverse events occurred in 8.4% of patients receiving ONCORIX.",
                        "claim_ids": ["c-010"],
                    },
                    "cta": {"text": "Learn more", "claim_ids": []},
                }
            ),
        }
    )
    assert "errors" not in json.loads(copy_result[0]["text"])
    validation = cs.get_handler("validate_claims_and_fair_balance")({"campaign_brief_id": brief_id})
    assert json.loads(validation[0]["text"])["overall_pass"] is True
    assert check_validation_gate(brief_id, "banner") is None

    copy = load_artifact(brief_id, "copy-banner.json")
    copy["copy"]["headline"]["text"] = "Changed after validation"
    save_artifact(brief_id, "copy-banner.json", copy)

    assert "changed" in check_validation_gate(brief_id, "banner").lower()
