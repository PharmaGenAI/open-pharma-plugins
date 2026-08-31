"""Adversarial Campaign Studio compliance contracts."""

from __future__ import annotations

import json
import shutil
from importlib.resources import files
from pathlib import Path

import pytest

from open_pharma_plugins_campaign_studio import _claim_engine
from open_pharma_plugins_campaign_studio._campaign_store import (
    campaign_dir,
    load_artifact,
    load_validation_artifact,
    save_artifact,
    save_brief,
)
from open_pharma_plugins_campaign_studio._claims import PersistedClaimsError, load_persisted_claims
from open_pharma_plugins_campaign_studio.models.claims import ApprovedClaim
from open_pharma_plugins_campaign_studio.tools.generate_audience_journey import handle as journey
from open_pharma_plugins_campaign_studio.tools.generate_channel_copy import handle as channel_copy
from open_pharma_plugins_campaign_studio.tools.generate_message_architecture import handle as architecture
from open_pharma_plugins_campaign_studio.tools.preflight_campaign_inputs import handle as preflight
from open_pharma_plugins_campaign_studio.tools.render_poster import handle as render_poster
from open_pharma_plugins_campaign_studio.tools.validate_claims_and_fair_balance import TOOL as COMPLIANCE_TOOL
from open_pharma_plugins_campaign_studio.tools.validate_claims_and_fair_balance import handle as compliance


@pytest.fixture(autouse=True)
def campaign_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_PHARMA_CAMPAIGN_STORE_DIR", str(tmp_path / "store"))


def _claim(claim_id: str, text: str, category: str = "efficacy", **overrides: object) -> dict:
    return {
        "claim_id": claim_id,
        "text": text,
        "category": category,
        "source_document": "Approved messages",
        "source_reference": "Section 1",
        "approval_status": "approved",
        "effective_from": "2020-01-01",
        "expiry": None,
        "jurisdictions": ["US"],
        "indications": ["condition"],
        "audiences": ["HCP"],
        "channels": ["email", "banner"],
        "allowed_variants": [],
        "restrictions": None,
        **overrides,
    }


def _brief(brief_id: str = "compliance", **overrides: object) -> dict:
    return {
        "campaign_brief_id": brief_id,
        "campaign_name": "Compliance test",
        "brand": "TestDrug",
        "country": "US",
        "policy_jurisdiction": "FDA",
        "indication": "condition",
        "target_segment": "HCP",
        "mode": "promotional",
        "channels": ["email", "banner"],
        "call_to_action": "Learn more",
        "generated_at": "2026-08-28T00:00:00+00:00",
        **overrides,
    }


def _seed(brief_id: str = "compliance") -> list[dict]:
    save_brief(_brief(brief_id))
    claims = [
        _claim("efficacy", "TestDrug reduces exacerbations by 20%.", "efficacy"),
        _claim("safety", "TestDrug may cause nausea.", "safety"),
    ]
    save_artifact(brief_id, "approved-claims.json", claims)
    save_artifact(
        brief_id,
        "brand-components.json",
        {
            "legal": {
                "isi": "Important safety information.",
                "pi_ref": "See prescribing information.",
                "reporting_statement": "Report adverse events.",
            }
        },
    )
    return claims


def _result(blocks: list[dict]) -> dict:
    return json.loads(blocks[0]["text"])


def _fixture_brand_kit_path() -> str:
    return str(files("open_pharma_plugins_campaign_studio") / "fixtures" / "brand_kit")


@pytest.mark.parametrize(
    ("spelling", "canonical"),
    [
        ("EFFICACY", "efficacy"),
        ("Positioning", "positioning"),
        ("MOA", "moa"),
        ("SAFETY", "safety"),
        ("Tolerability", "tolerability"),
        ("DOSING", "dosing"),
    ],
)
def test_approved_claim_serializes_one_canonical_category_taxonomy(spelling: str, canonical: str):
    """Accepted case variants must never survive into persisted claim JSON."""
    claim = ApprovedClaim.model_validate(_claim("claim", "Claim text.", spelling))
    assert claim.model_dump(mode="json")["category"] == canonical


@pytest.mark.parametrize("category", ["efficacy ", "unknown"])
def test_preflight_rejects_noncanonical_claim_categories_without_writes(tmp_path: Path, category: str):
    """Invalid categories must fail at the public preflight boundary before activation."""
    brief_id = "category-preflight-" + category.strip().replace(" ", "-")
    claims_path = tmp_path / f"{brief_id}.json"
    claims_path.write_text(json.dumps([_claim("claim", "Claim text.", category, channels=["banner"])]))
    save_brief(_brief(brief_id, channels=["banner"]))

    result = _result(
        preflight(
            {
                "campaign_brief_id": brief_id,
                "claims_path": str(claims_path),
                "brand_kit_path": _fixture_brand_kit_path(),
                "demo_mode": True,
            }
        )
    )

    assert result["ready"] is False
    assert result["errors"]
    assert result["exclusions"] == [{"claim_id": "claim", "reason": "invalid_schema"}]
    assert load_artifact(brief_id, "approved-claims.json") is None
    assert load_artifact(brief_id, "brand-components.json") is None
    assert load_artifact(brief_id, "input-provenance.json") is None


def test_validation_tool_describes_exact_approval_and_diagnostic_only_fuzzy_matching():
    description = COMPLIANCE_TOOL["description"].casefold()

    assert "exact normalized canonical wording" in description
    assert "allowed variant" in description
    assert "fuzzy" in description and "diagnostic only" in description


@pytest.mark.parametrize("category", ["efficacy ", "unknown"])
def test_shared_loader_rejects_noncanonical_persisted_claim_categories(category: str):
    """Every downstream claim consumer shares the same closed-category loader."""
    brief_id = "category-loader-" + category.strip().replace(" ", "-")
    _seed(brief_id)
    save_artifact(brief_id, "approved-claims.json", [_claim("efficacy", "Claim", category)])

    with pytest.raises(PersistedClaimsError, match="category"):
        load_persisted_claims(brief_id)


@pytest.mark.parametrize("category", ["efficacy ", "unknown"])
@pytest.mark.parametrize("tool", ["copy", "journey", "architecture", "validate"])
def test_claim_tools_fail_structured_without_writes_for_noncanonical_categories(category: str, tool: str):
    """Generation and validation must not turn invalid category data into downstream artifacts."""
    brief_id = f"category-{tool}-{category.strip()}"
    _seed(brief_id)
    save_artifact(brief_id, "approved-claims.json", [_claim("efficacy", "Claim", category)])

    if tool == "validate":
        content = compliance({"campaign_brief_id": brief_id, "channels": ["email"]})
        output_name = None
    else:
        content, output_name = _generation_call(tool, brief_id)
    result = _result(content)

    assert result["errors"]
    assert "category" in " ".join(result["errors"]).casefold()
    if output_name is not None:
        assert not (campaign_dir(brief_id) / output_name).exists()
    for name in ("policy-checks.json", "claim-map.json", "source-evidence.json"):
        assert load_validation_artifact(brief_id, name) is None


def test_canonicalized_efficacy_still_requires_banner_safety_and_fair_balance(tmp_path: Path):
    """Case normalization cannot weaken the two independent promotional banner controls."""
    brief_id = "canonical-efficacy-banner"
    efficacy_text = "TestDrug reduces exacerbations by 20%."
    claims_path = tmp_path / "canonical-claims.json"
    claims_path.write_text(json.dumps([_claim("efficacy", efficacy_text, "EFFICACY", channels=["banner"])]))
    save_brief(_brief(brief_id, channels=["banner"]))
    prepared = _result(
        preflight(
            {
                "campaign_brief_id": brief_id,
                "claims_path": str(claims_path),
                "brand_kit_path": _fixture_brand_kit_path(),
                "demo_mode": True,
            }
        )
    )
    assert prepared["ready"] is True
    assert load_artifact(brief_id, "approved-claims.json")[0]["category"] == "efficacy"

    banner_copy = {
        "headline": {"text": efficacy_text, "claim_ids": ["efficacy"]},
        "cta": {"text": "Learn more", "claim_ids": []},
    }
    generated = _result(
        channel_copy({"campaign_brief_id": brief_id, "channel": "banner", "copy_json": json.dumps(banner_copy)})
    )
    assert any("safety" in error.casefold() for error in generated["errors"])
    assert load_artifact(brief_id, "copy-banner.json") is None

    save_artifact(
        brief_id,
        "copy-banner.json",
        {"campaign_brief_id": brief_id, "channel": "banner", "copy": banner_copy},
    )
    report = _result(compliance({"campaign_brief_id": brief_id, "channels": ["banner"]}))
    failed_checks = {
        check["check_name"]
        for check in report["channel_results"]["banner"]["policy_checks"]
        if check["result"] == "fail"
    }
    assert report["overall_pass"] is False
    assert {"banner_safety", "fair_balance"} <= failed_checks


def test_claim_wording_only_approves_exact_text_or_explicit_variant():
    """Changing exact-match approval back to fuzzy matching must fail this test."""
    claim = _claim(
        "efficacy",
        "TestDrug does not increase mortality by 20%.",
        allowed_variants=["TestDrug shows no 20% increase in mortality."],
    )

    assert _claim_engine.validate_claim_wording("TestDrug increases mortality by 20%.", claim)["status"] == "rejected"
    assert (
        _claim_engine.validate_claim_wording("TestDrug does not increase mortality by 10%.", claim)["status"]
        == "rejected"
    )
    assert (
        _claim_engine.validate_claim_wording("TestDrug does not increase mortality by approximately 20%.", claim)[
            "status"
        ]
        == "needs_review"
    )
    assert (
        _claim_engine.validate_claim_wording("TestDrug shows no 20% increase in mortality.", claim)["status"]
        == "approved"
    )


def test_claim_applicability_is_rechecked_for_each_cited_channel():
    """Removing runtime governance checks must expose stale or channel-limited claims."""
    brief = _brief()
    assert "expired" in _claim_engine.claim_applicability_errors(
        _claim("old", "Old", expiry="2020-01-01"), brief, "email"
    )
    assert "channel_inapplicable" in _claim_engine.claim_applicability_errors(
        _claim("email-only", "Email only", channels=["email"]), brief, "banner"
    )
    assert (
        _claim_engine.claim_applicability_errors(
            _claim("unrestricted", "Unrestricted", jurisdictions=[], indications=[], audiences=[], channels=[]),
            brief,
            "banner",
        )
        == []
    )


def test_each_channel_must_independently_pass_fair_balance_and_required_legal():
    """Aggregating channel checks must not let a balanced email mask an unsafe banner."""
    brief_id = "independent-channels"
    _seed(brief_id)
    legal_blocks = [
        {"text": "Important safety information.", "claim_ids": []},
        {"text": "See prescribing information.", "claim_ids": []},
        {"text": "Report adverse events.", "claim_ids": []},
    ]
    save_artifact(
        brief_id,
        "copy-email.json",
        {
            "campaign_brief_id": brief_id,
            "channel": "email",
            "generated_at": "2026-08-28T00:00:00+00:00",
            "copy": {
                "subject": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
                "preheader": {"text": "TestDrug may cause nausea.", "claim_ids": ["safety"]},
                "headline": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
                "body": legal_blocks,
                "cta": {"text": "Learn more", "claim_ids": []},
            },
        },
    )
    save_artifact(
        brief_id,
        "copy-banner.json",
        {
            "campaign_brief_id": brief_id,
            "channel": "banner",
            "generated_at": "2026-08-28T00:00:00+00:00",
            "copy": {
                "headline": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
                "cta": {"text": "Learn more", "claim_ids": []},
            },
        },
    )

    report = _result(compliance({"campaign_brief_id": brief_id}))

    assert report["overall_pass"] is False
    assert report["channel_results"]["email"]["overall_pass"] is True
    assert report["channel_results"]["banner"]["overall_pass"] is False
    assert any(
        check["check_name"] == "fair_balance" and check["result"] == "fail"
        for check in report["channel_results"]["banner"]["policy_checks"]
    )
    assert report["policy_version"]
    assert report["policy_hash"]


def test_missing_copy_and_missing_claims_fail_their_own_channel():
    """A missing channel artifact or citation cannot be treated as a passing empty channel."""
    brief_id = "missing-channel"
    _seed(brief_id)
    save_artifact(
        brief_id,
        "copy-email.json",
        {
            "campaign_brief_id": brief_id,
            "channel": "email",
            "generated_at": "2026-08-28T00:00:00+00:00",
            "copy": {
                "subject": {"text": "Uncited promotional text", "claim_ids": []},
                "preheader": {"text": "", "claim_ids": []},
                "headline": {"text": "", "claim_ids": []},
                "body": [],
                "cta": {"text": "Learn more", "claim_ids": []},
            },
        },
    )

    report = _result(compliance({"campaign_brief_id": brief_id}))

    assert report["overall_pass"] is False
    assert report["channel_results"]["email"]["overall_pass"] is False
    assert report["channel_results"]["banner"]["overall_pass"] is False


@pytest.mark.parametrize("stages", [[], ["aware", "aware", "interested"], ["aware", "interested"]])
def test_journey_requires_three_to_six_unique_nonempty_stages(stages: list[str]):
    """Relaxing journey cardinality or uniqueness must fail this structure contract."""
    brief_id = f"journey-{'-'.join(stages) or 'empty'}"
    _seed(brief_id)
    payload = [
        {
            "stage": stage,
            "objective": "Educate",
            "key_messages": ["efficacy"],
            "channels": ["email"],
            "content_type": "promotional",
            "kpi": "opens",
        }
        for stage in stages
    ]

    result = _result(journey({"campaign_brief_id": brief_id, "journey": payload}))

    assert result["errors"]


def test_journey_requires_approved_claim_references():
    """Allowing an empty stage claim list must fail this grounding contract."""
    brief_id = "journey-missing-claim"
    _seed(brief_id)
    stages = [
        {
            "stage": stage,
            "objective": "Educate",
            "key_messages": [],
            "channels": ["email"],
            "content_type": "promotional",
            "kpi": "opens",
        }
        for stage in ("aware", "interested", "acting")
    ]
    assert _result(journey({"campaign_brief_id": brief_id, "journey": stages}))["errors"]


def test_message_tier_ranges_and_fair_balance_sources_must_be_grounded():
    """Changing tier policy or accepting ungrounded fair-balance sources must fail."""
    brief_id = "message-structure"
    _seed(brief_id)
    messages = [
        {
            "tier": "primary",
            "message": "TestDrug reduces exacerbations by 20%.",
            "claim_ids": ["efficacy"],
            "rationale": "Why",
        },
        {
            "tier": "primary",
            "message": "TestDrug reduces exacerbations by 20%.",
            "claim_ids": ["efficacy"],
            "rationale": "Why",
        },
        {"tier": "supporting", "message": "TestDrug may cause nausea.", "claim_ids": ["safety"], "rationale": "Why"},
    ]
    result = _result(
        architecture(
            {
                "campaign_brief_id": brief_id,
                "messages": messages,
                "fair_balance_statement": "TestDrug may cause nausea.",
                "fair_balance_sources": [{"document_id": "unknown", "document_name": "Unknown", "excerpt": "Unknown"}],
            }
        )
    )
    assert result["errors"]


def test_promotional_banner_requires_a_safety_copy_block():
    """Removing the banner safety block requirement must fail this validation contract."""
    brief_id = "banner-safety"
    _seed(brief_id)
    unsafe = _result(
        channel_copy(
            {
                "campaign_brief_id": brief_id,
                "channel": "banner",
                "copy_json": json.dumps(
                    {
                        "headline": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
                        "cta": {"text": "Learn more", "claim_ids": []},
                    }
                ),
            }
        )
    )
    assert any("safety" in error for error in unsafe["errors"])

    safe = _result(
        channel_copy(
            {
                "campaign_brief_id": brief_id,
                "channel": "banner",
                "copy_json": json.dumps(
                    {
                        "headline": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
                        "safety": {"text": "TestDrug may cause nausea.", "claim_ids": ["safety"]},
                        "cta": {"text": "Learn more", "claim_ids": []},
                    }
                ),
            }
        )
    )
    assert "errors" not in safe


@pytest.mark.parametrize(
    ("canonical", "altered"),
    [
        ("TestDrug response is \u226420%.", "TestDrug response is \u226520%."),
        ("TestDrug changes score by -5%.", "TestDrug changes score by 5%."),
        ("A response is unlikely.", "A response is likely."),
    ],
)
def test_wording_rejects_comparator_sign_and_likelihood_polarity_drift(canonical: str, altered: str):
    """Removing semantic polarity detection must fail these adversarial probes."""
    assert _claim_engine.validate_claim_wording(altered, _claim("claim", canonical))["status"] == "rejected"


def test_wording_accepts_canonically_equivalent_unicode():
    """Removing Unicode canonical normalisation must fail this exact-wording contract."""
    claim = _claim("claim", "Caf\u00e9 response is 20%.")
    assert _claim_engine.validate_claim_wording("Cafe\u0301 response is 20%.", claim)["status"] == "approved"


@pytest.mark.parametrize(
    ("channel", "copy"),
    [
        (
            "email",
            {
                "subject": {"text": " ", "claim_ids": []},
                "preheader": {"text": " ", "claim_ids": []},
                "headline": {"text": " ", "claim_ids": []},
                "body": [],
                "cta": {"text": "Learn more", "claim_ids": []},
            },
        ),
        ("banner", {"headline": {"text": " ", "claim_ids": []}, "cta": {"text": "Learn more", "claim_ids": []}}),
        (
            "poster",
            {"headline": {"text": " ", "claim_ids": []}, "body": [], "cta": {"text": "Learn more", "claim_ids": []}},
        ),
    ],
)
def test_generation_rejects_whitespace_and_empty_required_channel_content(channel: str, copy: dict):
    """Weak persisted-copy constraints must fail every channel's generation contract."""
    brief_id = f"empty-{channel}"
    _seed(brief_id)
    brief = _brief(brief_id, channels=[channel])
    save_brief(brief)
    result = _result(channel_copy({"campaign_brief_id": brief_id, "channel": channel, "copy_json": json.dumps(copy)}))
    assert "errors" in result


def test_validation_marks_hand_persisted_wrong_envelope_or_copy_schema_invalid():
    """Skipping persisted-envelope validation must fail this malformed artifact contract."""
    brief_id = "persisted-invalid"
    _seed(brief_id)
    save_artifact(
        brief_id,
        "copy-email.json",
        {"campaign_brief_id": "other", "channel": "banner", "copy": {"subject": {"text": " ", "claim_ids": []}}},
    )
    report = _result(compliance({"campaign_brief_id": brief_id, "channels": ["email"]}))
    checks = report["channel_results"]["email"]["policy_checks"]
    assert any(check["check_name"] == "invalid_copy" and check["result"] == "fail" for check in checks)


def test_poster_footnotes_are_checked_for_prohibited_language_in_non_promotional_mode():
    """Omitting textual footnotes from policy checks must fail this every-mode contract."""
    brief_id = "poster-footnote"
    _seed(brief_id)
    save_brief(_brief(brief_id, channels=["poster"], mode="non_promotional"))
    save_artifact(
        brief_id,
        "copy-poster.json",
        {
            "campaign_brief_id": brief_id,
            "channel": "poster",
            "generated_at": "2026-08-28T00:00:00+00:00",
            "copy": {
                "headline": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
                "body": [{"text": "TestDrug may cause nausea.", "claim_ids": ["safety"]}],
                "cta": {"text": "Learn more", "claim_ids": []},
                "footnotes": ["A miracle outcome."],
            },
        },
    )
    report = _result(compliance({"campaign_brief_id": brief_id}))
    assert any(
        check["check_name"] == "prohibited_language" for check in report["channel_results"]["poster"]["policy_checks"]
    )


@pytest.mark.parametrize(
    "source",
    [
        {"document_id": "efficacy", "document_name": "Approved messages", "excerpt": "Section 1"},
        {"document_id": "safety", "document_name": "Forged", "excerpt": "Section 1"},
    ],
)
def test_fair_balance_requires_current_safety_claim_exact_text_and_verified_provenance(source: dict):
    """Accepting efficacy sources or forged provenance must fail source-grounding."""
    brief_id = "fair-balance-proof"
    _seed(brief_id)
    messages = [
        {
            "tier": "primary",
            "message": "TestDrug reduces exacerbations by 20%.",
            "claim_ids": ["efficacy"],
            "rationale": "Why",
        },
        {"tier": "secondary", "message": "TestDrug may cause nausea.", "claim_ids": ["safety"], "rationale": "Why"},
        {"tier": "supporting", "message": "TestDrug may cause nausea.", "claim_ids": ["safety"], "rationale": "Why"},
    ]
    result = _result(
        architecture(
            {
                "campaign_brief_id": brief_id,
                "messages": messages,
                "fair_balance_statement": "TestDrug may not cause nausea.",
                "fair_balance_sources": [source],
            }
        )
    )
    assert result["errors"]


def test_validation_rejects_malformed_or_duplicate_claim_artifacts_without_writes():
    """Trusting malformed claim persistence must fail closed without a validation seal."""
    brief_id = "bad-claims"
    _seed(brief_id)
    save_artifact(brief_id, "approved-claims.json", [{"claim_id": "one"}, {"claim_id": "one"}])
    result = _result(compliance({"campaign_brief_id": brief_id, "channels": ["email"]}))
    assert result["errors"]
    assert load_validation_artifact(brief_id, "policy-checks.json") is None
    assert load_validation_artifact(brief_id, "claim-map.json") is None
    assert load_validation_artifact(brief_id, "source-evidence.json") is None


def test_journey_rechecks_claim_applicability_for_every_stage_channel():
    """Skipping per-stage channel applicability must fail this journey contract."""
    brief_id = "journey-applicability"
    _seed(brief_id)
    claims = _seed(brief_id)
    claims[0]["channels"] = ["email"]
    save_artifact(brief_id, "approved-claims.json", claims)
    stages = [
        {
            "stage": stage,
            "objective": "Educate",
            "key_messages": ["efficacy"],
            "channels": ["banner"],
            "content_type": "promotional",
            "kpi": "opens",
        }
        for stage in ("aware", "interested", "acting")
    ]
    assert _result(journey({"campaign_brief_id": brief_id, "journey": stages}))["errors"]


def test_validation_rejects_duplicate_or_unknown_requested_channels():
    """Overwriting duplicate channel results must fail this closed-taxonomy contract."""
    brief_id = "duplicate-channels"
    _seed(brief_id)
    result = _result(compliance({"campaign_brief_id": brief_id, "channels": ["email", "email", "unknown"]}))
    assert result["errors"]


def test_source_evidence_keeps_each_cited_claim_when_documents_match():
    """Deduplicating source rows by document must fail claim-level provenance."""
    brief_id = "evidence-rows"
    _seed(brief_id)
    save_artifact(
        brief_id,
        "copy-email.json",
        {
            "campaign_brief_id": brief_id,
            "channel": "email",
            "generated_at": "2026-08-28T00:00:00+00:00",
            "copy": {
                "subject": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
                "preheader": {"text": "TestDrug may cause nausea.", "claim_ids": ["safety"]},
                "headline": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
                "body": [
                    {"text": "Important safety information.", "claim_ids": []},
                    {"text": "See prescribing information.", "claim_ids": []},
                    {"text": "Report adverse events.", "claim_ids": []},
                ],
                "cta": {"text": "Learn more", "claim_ids": []},
            },
        },
    )
    _result(compliance({"campaign_brief_id": brief_id, "channels": ["email"]}))
    assert len(load_validation_artifact(brief_id, "source-evidence.json")) == 2


def test_fair_balance_rejects_expired_safety_source_even_with_exact_copy():
    """Removing runtime fair-balance claim applicability must fail this stale-source case."""
    brief_id = "fair-balance-expired"
    claims = _seed(brief_id)
    claims[1]["expiry"] = "2020-01-01"
    save_artifact(brief_id, "approved-claims.json", claims)
    messages = [
        {"tier": "primary", "message": claims[0]["text"], "claim_ids": ["efficacy"], "rationale": "Why"},
        {"tier": "secondary", "message": claims[1]["text"], "claim_ids": ["safety"], "rationale": "Why"},
        {"tier": "supporting", "message": claims[1]["text"], "claim_ids": ["safety"], "rationale": "Why"},
    ]
    result = _result(
        architecture(
            {
                "campaign_brief_id": brief_id,
                "messages": messages,
                "fair_balance_statement": claims[1]["text"],
                "fair_balance_sources": [
                    {"document_id": "safety", "document_name": "Approved messages", "excerpt": "Section 1"}
                ],
            }
        )
    )
    assert result["errors"]


def test_generation_accepts_complete_email_banner_and_poster_contracts():
    """Normal schema-valid generation for each closed channel remains supported."""
    brief_id = "normal-channels"
    _seed(brief_id)
    claims = _seed(brief_id)
    for claim in claims:
        claim["channels"] = ["email", "banner", "poster"]
    save_artifact(brief_id, "approved-claims.json", claims)
    save_brief(_brief(brief_id, channels=["email", "banner", "poster"]))
    payloads = {
        "email": {
            "subject": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
            "preheader": {"text": "TestDrug may cause nausea.", "claim_ids": ["safety"]},
            "headline": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
            "body": [{"text": "TestDrug may cause nausea.", "claim_ids": ["safety"]}],
            "cta": {"text": "Learn more", "claim_ids": []},
        },
        "banner": {
            "headline": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
            "safety": {"text": "TestDrug may cause nausea.", "claim_ids": ["safety"]},
            "cta": {"text": "Learn more", "claim_ids": []},
        },
        "poster": {
            "headline": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
            "body": [{"text": "TestDrug may cause nausea.", "claim_ids": ["safety"]}],
            "cta": {"text": "Learn more", "claim_ids": []},
            "footnotes": ["Important safety information."],
        },
    }
    for channel, payload in payloads.items():
        assert "errors" not in _result(
            channel_copy({"campaign_brief_id": brief_id, "channel": channel, "copy_json": json.dumps(payload)})
        )


def test_validation_rejects_duplicate_requested_channel_without_overwrite():
    """A duplicate valid channel must not be silently overwritten in the report."""
    brief_id = "duplicate-only"
    _seed(brief_id)
    assert "errors" in _result(compliance({"campaign_brief_id": brief_id, "channels": ["email", "email"]}))


@pytest.mark.parametrize(
    "footnote", ["Important safety information.", {"text": "TestDrug may cause nausea.", "claim_ids": ["safety"]}]
)
def test_poster_footnotes_generate_validate_and_render_without_type_errors(footnote: str | dict, tmp_path: Path):
    """Raw CopyBlock footnotes must never reach ReportLab as dictionaries."""
    brief_id = "poster-render-" + ("legal" if isinstance(footnote, str) else "claim")
    claims = [
        _claim("efficacy", "TestDrug reduces exacerbations by 20%.", "efficacy"),
        _claim("safety", "TestDrug may cause nausea.", "safety"),
    ]
    for claim in claims:
        claim["channels"] = ["poster"]
    claims_path = tmp_path / f"{brief_id}-claims.json"
    claims_path.write_text(json.dumps(claims), encoding="utf-8")
    brand_kit = tmp_path / f"{brief_id}-brand-kit"
    shutil.copytree(_fixture_brand_kit_path(), brand_kit)
    brand_kit_path = str(brand_kit)
    save_brief(
        _brief(
            brief_id,
            channels=["poster"],
            lifecycle_stage="growth",
            behavioral_objective="Review approved evidence.",
            educational_objective=None,
            desired_kpi=["engagement"],
            approved_claims_path=str(claims_path),
            brand_kit_path=brand_kit_path,
            demo_mode=False,
            call_to_action_url="https://example.test/learn",
            asset_dimensions=None,
            language="en",
            localisation_notes=None,
            required_safety_content=[],
            required_legal_content=[],
            delivery_constraints=None,
            approval_workflow="mlr_standard",
        )
    )
    preflight_result = _result(
        preflight(
            {
                "campaign_brief_id": brief_id,
                "claims_path": str(claims_path),
                "brand_kit_path": brand_kit_path,
                "demo_mode": False,
            }
        )
    )
    assert preflight_result["ready"] is True, preflight_result
    legal = json.loads((brand_kit / "legal.json").read_text(encoding="utf-8"))
    resolved_footnote = legal["isi"] if isinstance(footnote, str) else footnote
    footnotes = [resolved_footnote, legal["isi"], legal["pi_ref"], legal["reporting_statement"]]
    result = _result(
        channel_copy(
            {
                "campaign_brief_id": brief_id,
                "channel": "poster",
                "copy_json": json.dumps(
                    {
                        "headline": {"text": claims[0]["text"], "claim_ids": ["efficacy"]},
                        "body": [{"text": claims[1]["text"], "claim_ids": ["safety"]}],
                        "cta": {"text": "Learn more", "claim_ids": []},
                        "footnotes": footnotes,
                    }
                ),
            }
        )
    )
    assert "errors" not in result
    assert _result(compliance({"campaign_brief_id": brief_id}))["overall_pass"] is True
    from open_pharma_plugins_campaign_studio._renderer import validation_gate_state, validation_input_payload

    payload = validation_input_payload(brief_id, ["poster"])
    assert payload["errors"] == [], "\n".join(item["message"] for item in payload["errors"])
    gate = validation_gate_state(brief_id)
    assert gate["status"] == "current", gate
    rendered = _result(render_poster({"campaign_brief_id": brief_id}))
    assert "error" not in rendered


@pytest.mark.parametrize("unsafe", ["\x00", "\u200b", "\u202e", "\ufeff", "\u2066"])
def test_copy_text_rejects_nonvisible_control_and_format_characters(unsafe: str):
    """All CopyBlock and legal-string text must share a visible-text safety rule."""
    from pydantic import ValidationError

    from open_pharma_plugins_campaign_studio.models.copy import CopyBlock, PosterCopy

    with pytest.raises(ValidationError):
        CopyBlock.model_validate({"text": unsafe, "claim_ids": []})
    with pytest.raises(ValidationError):
        PosterCopy.model_validate(
            {
                "headline": {"text": "Visible", "claim_ids": []},
                "body": [{"text": "Visible", "claim_ids": []}],
                "cta": {"text": "Visible", "claim_ids": []},
                "footnotes": [unsafe],
            }
        )
    assert CopyBlock.model_validate({"text": "Caf\u00e9 - 20%", "claim_ids": []}).text == "Caf\u00e9 - 20%"


def test_copy_block_rejects_duplicate_claim_ids_and_fair_balance_cannot_be_inflated():
    """Duplicate citations cannot alter a block's safety-to-efficacy contribution."""
    from pydantic import ValidationError

    from open_pharma_plugins_campaign_studio._claim_engine import check_fair_balance
    from open_pharma_plugins_campaign_studio.models.copy import CopyBlock

    with pytest.raises(ValidationError):
        CopyBlock.model_validate({"text": "Visible", "claim_ids": ["efficacy", "efficacy"]})
    claims = [
        _claim("efficacy-a", "Efficacy A", "efficacy"),
        _claim("efficacy-b", "Efficacy B", "efficacy"),
        _claim("safety", "Safety", "safety"),
    ]
    fair_balance = check_fair_balance(
        [{"text": "Mixed", "claim_ids": ["efficacy-a", "efficacy-b", "safety"]}], claims, 0.3
    )
    assert fair_balance["result"] == "pass"


def test_defensive_banner_validation_requires_dedicated_exact_applicable_safety_block():
    """Safety elsewhere in a persisted efficacy banner cannot replace banner.safety."""
    brief_id = "persisted-banner-safety"
    claims = _seed(brief_id)
    save_brief(_brief(brief_id, channels=["banner"]))
    save_artifact(
        brief_id,
        "copy-banner.json",
        {
            "campaign_brief_id": brief_id,
            "channel": "banner",
            "copy": {
                "headline": {"text": claims[0]["text"], "claim_ids": ["efficacy"]},
                "sub_headline": {"text": claims[1]["text"], "claim_ids": ["safety"]},
                "cta": {"text": "Learn more", "claim_ids": []},
            },
        },
    )
    report = _result(compliance({"campaign_brief_id": brief_id}))
    assert any(check["check_name"] == "banner_safety" for check in report["channel_results"]["banner"]["policy_checks"])


def test_fair_balance_requires_a_safety_claim_usable_by_a_brief_channel_and_derives_page_number():
    """Poster-only safety and caller-controlled page provenance cannot be persisted."""
    brief_id = "fair-balance-channel-page"
    claims = _seed(brief_id)
    claims[1]["channels"] = ["poster"]
    save_artifact(brief_id, "approved-claims.json", claims)
    save_brief(_brief(brief_id, channels=["email"]))
    messages = [
        {"tier": "primary", "message": claims[0]["text"], "claim_ids": ["efficacy"], "rationale": "Why"},
        {"tier": "secondary", "message": claims[1]["text"], "claim_ids": ["safety"], "rationale": "Why"},
        {"tier": "supporting", "message": claims[1]["text"], "claim_ids": ["safety"], "rationale": "Why"},
    ]
    rejected = _result(
        architecture(
            {
                "campaign_brief_id": brief_id,
                "messages": messages,
                "fair_balance_statement": claims[1]["text"],
                "fair_balance_sources": [
                    {
                        "document_id": "safety",
                        "document_name": "Approved messages",
                        "excerpt": "Section 1",
                        "page_number": -1,
                    }
                ],
            }
        )
    )
    assert rejected["errors"]
    claims[1]["channels"] = ["email"]
    save_artifact(brief_id, "approved-claims.json", claims)
    accepted = _result(
        architecture(
            {
                "campaign_brief_id": brief_id,
                "messages": messages,
                "fair_balance_statement": claims[1]["text"],
                "fair_balance_sources": [
                    {
                        "document_id": "safety",
                        "document_name": "Approved messages",
                        "excerpt": "Section 1",
                        "page_number": -1,
                    }
                ],
            }
        )
    )
    assert "errors" not in accepted
    persisted = load_artifact(brief_id, "message-architecture.json")
    assert persisted["fair_balance_sources"][0]["page_number"] is None


@pytest.mark.parametrize(
    ("canonical", "altered"),
    [
        ("Response is at least 20%.", "Response is at most 20%."),
        ("Response is under 20%.", "Response is over 20%."),
    ],
)
def test_wording_rejects_multiword_comparator_polarity_drift(canonical: str, altered: str):
    """Phrase polarity must be compared before ordinary word-token diagnostics."""
    assert _claim_engine.validate_claim_wording(altered, _claim("claim", canonical))["status"] == "rejected"


@pytest.mark.parametrize(
    ("canonical", "altered"),
    [
        ("Response is above 20 kg.", "Response is below 20 kg."),
        ("Response is over 20 kg.", "Response is under 20 kg."),
        ("Response is more than 20 kg.", "Response is fewer than 20 kg."),
        ("Response is up to 20 kg.", "Response is more than 20 kg."),
        ("Weight changed by 20 kg.", "Weight changed by 20 lb."),
        ("Treatment lasted 10 weeks.", "Treatment lasted 10 hours."),
        ("Response is ≤20%.", "Response is ≥20%."),
        ("Weight changed by -5%.", "Weight changed by 5%."),
        ("Dose was 20.5-30.2 mg.", "Dose was 20.5-30.2 mcg."),
    ],
)
def test_wording_rejects_comparator_and_arbitrary_unit_drift(canonical: str, altered: str):
    """Directional phrases and number-adjacent units are governed before fuzzy diagnostics."""
    assert _claim_engine.validate_claim_wording(altered, _claim("claim", canonical))["status"] == "rejected"


@pytest.mark.parametrize(
    ("canonical", "statement", "expected_status"),
    [
        pytest.param("Response was <=20%.", "Response was <20%.", "rejected", id="ascii-inclusive-strict"),
        pytest.param("Response was ≤20%.", "Response was <20%.", "rejected", id="unicode-inclusive-strict"),
        pytest.param("Response was >=20%.", "Response was >20%.", "rejected", id="greater-inclusive-strict"),
        pytest.param("Response was ≥20%.", "Response was >20%.", "rejected", id="unicode-greater-inclusive"),
        pytest.param("Response was <20%.", "Response was >20%.", "rejected", id="strict-direction"),
        pytest.param("Response was =20%.", "Response was !=20%.", "rejected", id="ascii-equality-inequality"),
        pytest.param("Response was =20%.", "Response was ≠20%.", "rejected", id="unicode-inequality"),
        pytest.param("Change was +/-5%.", "Change was 5%.", "rejected", id="ascii-plus-minus"),
        pytest.param("Change was ±5%.", "Change was 5%.", "rejected", id="unicode-plus-minus"),
        pytest.param("Change was +5%.", "Change was -5%.", "rejected", id="unary-sign"),
        pytest.param("Temperature was 20°C.", "Temperature was 20°F.", "rejected", id="adjacent-temperature"),
        pytest.param("Temperature was 20 °C.", "Temperature was 20 °F.", "rejected", id="spaced-temperature"),
        pytest.param("Dose was 20 µg.", "Dose was 20 mg.", "rejected", id="symbol-prefixed-unit"),
        pytest.param("Dose was 20 mg/kg.", "Dose was 20 mg/L.", "rejected", id="compound-dose-unit"),
        pytest.param("Clearance was 20 mL/min.", "Clearance was 20 mL/hour.", "rejected", id="compound-rate-unit"),
        pytest.param("Dose was 20.5-30.2 mg.", "Dose was 20.5-30.3 mg.", "rejected", id="decimal-range"),
        pytest.param("Dose was -5 to -1 mg.", "Dose was -5 to 1 mg.", "rejected", id="signed-range"),
        pytest.param(
            "Dose was 10 mg then 20 mL/min.",
            "Dose was 20 mL/min then 10 mg.",
            "rejected",
            id="multiple-quantity-order",
        ),
        pytest.param("Dose was 20 mg.", "Dose was 20 mg!", "needs_review", id="sentence-punctuation"),
        pytest.param("Response was ≤20%.", "Response was <=20%.", "needs_review", id="unicode-operator-alias"),
        pytest.param("Response was ≥20%.", "Response was >=20%.", "needs_review", id="greater-operator-alias"),
        pytest.param("Response was ≠20%.", "Response was !=20%.", "needs_review", id="inequality-alias"),
        pytest.param("Change was ±5%.", "Change was +/-5%.", "needs_review", id="plus-minus-alias"),
    ],
)
def test_claim_wording_quantitative_signature_matrix(canonical: str, statement: str, expected_status: str):
    """The public matcher preserves quantity semantics without fuzzy approval."""
    result = _claim_engine.validate_claim_wording(statement, _claim("claim", canonical))
    assert result["status"] == expected_status


@pytest.mark.parametrize(
    ("canonical", "statement", "expected_status"),
    [
        (
            "Dose was ≤20 mg/kg at ±5°C for 2-4 weeks.",
            "Dose was <20 mg/kg at 5°F for 2-4 weeks.",
            "rejected",
        ),
        ("Dose was 20 mg.", "Dose was 20 mg!", "needs_review"),
    ],
)
def test_generated_copy_validation_uses_complete_quantitative_signature(
    canonical: str, statement: str, expected_status: str
):
    """Generated copy must reach the same quantity decision at the persisted validation seam."""
    brief_id = "quantity-tool-" + expected_status
    claims = _seed(brief_id)
    claims[0]["text"] = canonical
    save_artifact(brief_id, "approved-claims.json", claims)
    generated = _result(
        channel_copy(
            {
                "campaign_brief_id": brief_id,
                "channel": "email",
                "copy_json": json.dumps(
                    {
                        "subject": {"text": statement, "claim_ids": ["efficacy"]},
                        "preheader": {"text": claims[1]["text"], "claim_ids": ["safety"]},
                        "headline": {"text": canonical, "claim_ids": ["efficacy"]},
                        "body": [
                            {"text": "Important safety information.", "claim_ids": []},
                            {"text": "See prescribing information.", "claim_ids": []},
                            {"text": "Report adverse events.", "claim_ids": []},
                        ],
                        "cta": {"text": "Learn more", "claim_ids": []},
                    }
                ),
            }
        )
    )
    assert "errors" not in generated

    report = _result(compliance({"campaign_brief_id": brief_id, "channels": ["email"]}))
    altered_result = next(
        result for result in report["channel_results"]["email"]["claims_checked"] if result["statement"] == statement
    )
    assert report["overall_pass"] is False
    assert altered_result["status"] == expected_status


@pytest.mark.parametrize(
    ("canonical", "statement", "expected_status"),
    [
        pytest.param("Dose was 20 mg.", "Dose was 20 Mg.", "rejected", id="milli-mega-gram"),
        pytest.param("Concentration was 20 mM.", "Concentration was 20 mm.", "rejected", id="molar-length"),
        pytest.param("Energy was 20 mJ.", "Energy was 20 MJ.", "rejected", id="milli-mega-joule"),
        pytest.param(
            "Clearance was 20 mL/min.",
            "Clearance was 20 ML/min.",
            "rejected",
            id="milli-mega-litre-rate",
        ),
        pytest.param("Dose was .5 mg/kg.", "Dose was .6 mg/kg.", "rejected", id="leading-decimal"),
        pytest.param("Exposure was 20 mg·h/L.", "Exposure was 20 mg·h/mL.", "rejected", id="middle-dot"),
        pytest.param("Dose was 20 mg per kg.", "Dose was 20 mg/kg.", "needs_review", id="per-alias"),
        pytest.param("Dose was 20 mg kg−1.", "Dose was 20 mg/kg.", "needs_review", id="inverse-unit"),
        pytest.param("Dose was 20 µg.", "Dose was 20 μg.", "approved", id="micro-alias"),
        pytest.param("Dose was .5–1 mg.", "Dose was 0.5-1.0 mg.", "needs_review", id="range-dash-alias"),
        pytest.param(
            "Dose was 20 mg, administered daily.",
            "Dose was 20 mg; administered daily.",
            "needs_review",
            id="sentence-punctuation",
        ),
        pytest.param(
            "20 patients were eligible.",
            "20 people were eligible.",
            "needs_review",
            id="prose-is-not-a-unit",
        ),
        pytest.param(
            "20 eligible patients enrolled.",
            "20 qualified people enrolled.",
            "needs_review",
            id="adjective-is-not-a-unit",
        ),
        pytest.param(
            "20 patients were eligible.",
            "99 people were eligible.",
            "rejected",
            id="standalone-number-drift",
        ),
        pytest.param(
            "At least 10 mg and at most 20 mg were administered.",
            "At most 10 mg and at least 20 mg were administered.",
            "rejected",
            id="multiple-comparator-order",
        ),
        pytest.param(
            "At least 20 mg was administered.",
            "≥20 mg was administered.",
            "needs_review",
            id="phrase-symbol-comparator-alias",
        ),
    ],
)
def test_round_five_bounded_case_sensitive_quantitative_matrix(canonical: str, statement: str, expected_status: str):
    """Changing SI case or bounded quantity semantics must fail before exact/fuzzy wording decisions."""
    result = _claim_engine.validate_claim_wording(statement, _claim("claim", canonical))
    assert result["status"] == expected_status


@pytest.mark.parametrize(
    ("canonical", "statement", "expected_status"),
    [
        pytest.param(
            "Leftover 20 mg doses were discarded.",
            "Remaining 20 mg doses were discarded.",
            "needs_review",
            id="leftover-is-prose",
        ),
        pytest.param(
            "Turnover 20% was recorded in the cohort.",
            "Attrition 20% was recorded in the cohort.",
            "needs_review",
            id="turnover-is-prose",
        ),
        pytest.param(
            "Moreover 20% of patients responded.",
            "Additionally 20% of patients responded.",
            "needs_review",
            id="moreover-is-prose",
        ),
        pytest.param(
            "Thunder 20 mg was the internal scenario label.",
            "Storm 20 mg was the internal scenario label.",
            "needs_review",
            id="thunder-is-prose",
        ),
        pytest.param(
            "Understand 20 mg as the planned dose.",
            "Interpret 20 mg as the planned dose.",
            "needs_review",
            id="understand-is-prose",
        ),
        pytest.param(
            "Aboveboard 20 mg documentation was retained.",
            "Transparent 20 mg documentation was retained.",
            "needs_review",
            id="aboveboard-is-prose",
        ),
        pytest.param(
            "Caf\u00e9over 20 mg was the internal scenario label.",
            "Scenario 20 mg was the internal label.",
            "needs_review",
            id="unicode-word-boundary",
        ),
        pytest.param("Response was over 20%.", "Response was under 20%.", "rejected", id="standalone-over"),
        pytest.param("Response was under 20%.", "Response was over 20%.", "rejected", id="standalone-under"),
        pytest.param("Response was above 20%.", "Response was below 20%.", "rejected", id="standalone-above"),
        pytest.param("Response was below 20%.", "Response was above 20%.", "rejected", id="standalone-below"),
        pytest.param("Response was >20%.", "Response was <20%.", "rejected", id="symbol-operators"),
    ],
)
def test_round_six_comparator_aliases_have_unicode_lexical_boundaries(
    canonical: str, statement: str, expected_status: str
):
    """Word aliases are standalone lexemes; symbol and standalone direction changes remain semantic."""
    result = _claim_engine.validate_claim_wording(statement, _claim("claim", canonical))
    assert result["status"] == expected_status


@pytest.mark.parametrize(
    ("canonical", "statement", "expected_status"),
    [
        pytest.param(
            "x\u0301over 20 mg was the internal scenario label.",
            "Scenario 20 mg was the internal label.",
            "needs_review",
            id="decomposed-combining-mark",
        ),
        pytest.param(
            "\u0915\u094dover 20 mg was the internal scenario label.",
            "Scenario 20 mg was the internal label.",
            "needs_review",
            id="indic-mark",
        ),
        pytest.param(
            "foo\u200dover 20 mg was the internal scenario label.",
            "Scenario 20 mg was the internal label.",
            "needs_review",
            id="zero-width-joiner",
        ),
        pytest.param(
            "carry-over 20 mg was the internal scenario label.",
            "Scenario 20 mg was the internal label.",
            "needs_review",
            id="hyphenated-compound",
        ),
        pytest.param(
            "Carry\u2014over 20 mg was the internal scenario label.",
            "Scenario 20 mg was the internal label.",
            "needs_review",
            id="dash-compound",
        ),
        pytest.param(
            "Carry'over 20 mg was the internal scenario label.",
            "Scenario 20 mg was the internal label.",
            "needs_review",
            id="apostrophe-compound",
        ),
        pytest.param(
            "(over 20 mg) was the internal scenario label.",
            "(20 mg) was the internal scenario label.",
            "needs_review",
            id="arbitrary-punctuation-prefix",
        ),
        pytest.param("over 20 mg was used.", "under 20 mg was used.", "rejected", id="start-of-text"),
        pytest.param(
            "Response was\u2003above 20%.",
            "Response was\u2003below 20%.",
            "rejected",
            id="unicode-whitespace",
        ),
        pytest.param("Response was over 20%.", "Response was under 20%.", "rejected", id="ascii-whitespace"),
        pytest.param("(>20 mg) was used.", "(<20 mg) was used.", "rejected", id="symbol-after-punctuation"),
        pytest.param(">20 mg was used.", "<20 mg was used.", "rejected", id="symbol-at-start"),
    ],
)
def test_round_seven_word_comparators_require_start_or_unicode_whitespace(
    canonical: str, statement: str, expected_status: str
):
    """Word comparators start text or follow whitespace; symbol comparators retain their grammar."""
    result = _claim_engine.validate_claim_wording(statement, _claim("claim", canonical))
    assert result["status"] == expected_status


_ROUND_EIGHT_FORBIDDEN_COMPARATOR_PREFIXES = [
    pytest.param("(", id="open-parenthesis"),
    pytest.param(".", id="full-stop"),
    pytest.param("/", id="slash"),
    pytest.param("'", id="apostrophe"),
    pytest.param("-", id="ascii-hyphen"),
    pytest.param("\u2014", id="em-dash"),
    pytest.param("x", id="ascii-letter"),
    pytest.param("word", id="ascii-letters"),
    pytest.param("\u00e9", id="precomposed-latin-letter"),
    pytest.param("\u754c", id="cjk-letter"),
    pytest.param("x\u0301", id="decomposed-combining-mark"),
    pytest.param("\u0915\u094d", id="indic-letter-mark"),
    pytest.param("foo\u200d", id="zero-width-joiner"),
    pytest.param("foo\u200c", id="zero-width-non-joiner"),
    pytest.param("foo\u2060", id="word-joiner"),
    pytest.param("foo_", id="connector-punctuation"),
]

_ROUND_EIGHT_NEGATED_COMPARATOR_PHRASES = [
    pytest.param("no less than", id="no-less-than"),
    pytest.param("no more than", id="no-more-than"),
    pytest.param("not less than", id="not-less-than"),
    pytest.param("not more than", id="not-more-than"),
]


@pytest.mark.parametrize("prefix", _ROUND_EIGHT_FORBIDDEN_COMPARATOR_PREFIXES)
@pytest.mark.parametrize("phrase", _ROUND_EIGHT_NEGATED_COMPARATOR_PHRASES)
def test_round_eight_boundary_rejected_negated_comparator_cannot_rematch_suffix(prefix: str, phrase: str):
    """A rejected negated phrase must not fall through to its less/more suffix."""
    canonical = "Control 20 mg was the internal scenario label."
    statement = f"{prefix}{phrase} 20 mg was the internal scenario label."

    result = _claim_engine.validate_claim_wording(statement, _claim("claim", canonical))

    assert result["status"] == "needs_review"


_ROUND_EIGHT_UNICODE_WORD_PREFIXES = [
    pytest.param("Caf\u00e9", id="precomposed-latin"),
    pytest.param("Cafe\u0301", id="decomposed-latin"),
    pytest.param("\u754c", id="cjk"),
    pytest.param("\u0915\u094d", id="devanagari-mark"),
    pytest.param("\u0ba8\u0bbf", id="tamil-mark"),
    pytest.param("foo\u200d", id="zero-width-joiner"),
    pytest.param("foo\u200c", id="zero-width-non-joiner"),
    pytest.param("foo\u2060", id="word-joiner"),
    pytest.param("foo\u200b", id="zero-width-space"),
    pytest.param("A1", id="alphanumeric"),
    pytest.param("123", id="numeric"),
    pytest.param("foo_", id="underscore"),
    pytest.param("foo\u203f", id="undertie"),
    pytest.param("\u0394", id="greek"),
    pytest.param("\u0416", id="cyrillic"),
    pytest.param("\u0627\u0644\u0639\u0631\u0628\u064a\u0629", id="arabic"),
]

_ROUND_EIGHT_UNICODE_WORD_SUFFIXES = [
    pytest.param("\u00e9", id="latin-letter"),
    pytest.param("\u754c", id="cjk-letter"),
    pytest.param("\u0394", id="greek-letter"),
    pytest.param("\u0338", id="combining-overlay"),
    pytest.param("\u094d", id="indic-mark"),
    pytest.param("1", id="number"),
    pytest.param("_", id="underscore"),
    pytest.param("\u203f", id="undertie"),
    pytest.param("\u200dfoo", id="zero-width-joiner"),
    pytest.param("\u200cfoo", id="zero-width-non-joiner"),
    pytest.param("\u2060foo", id="word-joiner"),
    pytest.param("\u200bfoo", id="zero-width-space"),
]

_ROUND_EIGHT_POLARITY_WORDS = [
    pytest.param("greater", id="greater"),
    pytest.param("less", id="less"),
    pytest.param("increase", id="increase"),
    pytest.param("decrease", id="decrease"),
    pytest.param("likely", id="likely"),
    pytest.param("unlikely", id="unlikely"),
]


@pytest.mark.parametrize("prefix", _ROUND_EIGHT_UNICODE_WORD_PREFIXES)
@pytest.mark.parametrize("polarity", _ROUND_EIGHT_POLARITY_WORDS)
def test_round_eight_unicode_word_prefix_does_not_expose_internal_polarity(prefix: str, polarity: str):
    """Letters, marks, numbers, connectors, and format controls continue one word."""
    canonical = f"{prefix}stable 20 mg was the internal scenario label."
    statement = f"{prefix}{polarity} 20 mg was the internal scenario label."

    result = _claim_engine.validate_claim_wording(statement, _claim("claim", canonical))

    assert result["status"] == "needs_review"


@pytest.mark.parametrize("suffix", _ROUND_EIGHT_UNICODE_WORD_SUFFIXES)
@pytest.mark.parametrize("polarity", _ROUND_EIGHT_POLARITY_WORDS)
def test_round_eight_unicode_word_suffix_does_not_expose_internal_polarity(suffix: str, polarity: str):
    """A polarity substring followed by a Unicode word continuation is not standalone."""
    canonical = f"stable{suffix} 20 mg was the internal scenario label."
    statement = f"{polarity}{suffix} 20 mg was the internal scenario label."

    result = _claim_engine.validate_claim_wording(statement, _claim("claim", canonical))

    assert result["status"] == "needs_review"


@pytest.mark.parametrize("boundary", [pytest.param("", id="start"), pytest.param("\u2003", id="unicode-space")])
@pytest.mark.parametrize(
    ("phrase", "symbol"),
    [
        pytest.param("no less than", ">=", id="no-less-than"),
        pytest.param("no more than", "<=", id="no-more-than"),
        pytest.param("not less than", ">=", id="not-less-than"),
        pytest.param("not more than", "<=", id="not-more-than"),
    ],
)
def test_round_eight_standalone_negated_comparator_matches_symbol_semantics(boundary: str, phrase: str, symbol: str):
    """Supported negated phrases at valid boundaries retain inclusive semantics."""
    canonical = f"{boundary}{phrase} 20 mg was administered."
    statement = f"{boundary}{symbol}20 mg was administered."

    result = _claim_engine.validate_claim_wording(statement, _claim("claim", canonical))

    assert result["status"] == "needs_review"


@pytest.mark.parametrize(
    ("canonical", "statement"),
    [
        pytest.param("No less than 20 mg was administered.", "No more than 20 mg was administered.", id="no-less"),
        pytest.param("No more than 20 mg was administered.", "No less than 20 mg was administered.", id="no-more"),
        pytest.param("Not less than 20 mg was administered.", "Not more than 20 mg was administered.", id="not-less"),
        pytest.param("Not more than 20 mg was administered.", "Not less than 20 mg was administered.", id="not-more"),
        pytest.param(">=20 mg was administered.", "<=20 mg was administered.", id="ascii-inclusive-symbols"),
        pytest.param("\u226520 mg was administered.", "\u226420 mg was administered.", id="unicode-inclusive-symbols"),
        pytest.param(">20 mg was administered.", "<20 mg was administered.", id="strict-symbols"),
    ],
)
def test_round_eight_standalone_comparator_direction_changes_remain_rejected(canonical: str, statement: str):
    """Valid word and symbol comparators continue to reject direction changes."""
    result = _claim_engine.validate_claim_wording(statement, _claim("claim", canonical))

    assert result["status"] == "rejected"


@pytest.mark.parametrize(
    "punctuation", [pytest.param("", id="whitespace"), pytest.param("parentheses", id="punctuation")]
)
@pytest.mark.parametrize(
    ("canonical_polarity", "statement_polarity"),
    [
        pytest.param("less", "greater", id="greater"),
        pytest.param("greater", "less", id="less"),
        pytest.param("decrease", "increase", id="increase"),
        pytest.param("increase", "decrease", id="decrease"),
        pytest.param("unlikely", "likely", id="likely"),
        pytest.param("likely", "unlikely", id="unlikely"),
    ],
)
def test_round_eight_standalone_unicode_tokenizer_polarity_controls_remain_rejected(
    punctuation: str, canonical_polarity: str, statement_polarity: str
):
    """Standalone polarity words remain visible after the Unicode tokenizer change."""
    if punctuation:
        canonical_polarity = f"({canonical_polarity})"
        statement_polarity = f"({statement_polarity})"
    canonical = f"Response was {canonical_polarity} at 20 mg."
    statement = f"Response was {statement_polarity} at 20 mg."

    result = _claim_engine.validate_claim_wording(statement, _claim("claim", canonical))

    assert result["status"] == "rejected"


@pytest.mark.parametrize(
    ("canonical", "statement"),
    [
        ("Dose was 20 mg.", "Dose was 20 Mg."),
        ("Concentration was 20 mM.", "Concentration was 20 mm."),
        ("Energy was 20 mJ.", "Energy was 20 MJ."),
        ("Clearance was 20 mL/min.", "Clearance was 20 ML/min."),
        ("Dose was .5 mg/kg.", "Dose was .6 mg/kg."),
        ("Exposure was 20 mg·h/L.", "Exposure was 20 mg·h/mL."),
        ("20 patients were eligible.", "99 people were eligible."),
    ],
)
def test_round_five_quantity_drift_reaches_persisted_channel_validation(canonical: str, statement: str):
    """Every round-five quantity drift probe must be rejected at the generated-copy compliance seam."""
    brief_id = "round-five-quantity"
    claims = _seed(brief_id)
    claims[0]["text"] = canonical
    save_artifact(brief_id, "approved-claims.json", claims)
    generated = _result(
        channel_copy(
            {
                "campaign_brief_id": brief_id,
                "channel": "banner",
                "copy_json": json.dumps(
                    {
                        "headline": {"text": statement, "claim_ids": ["efficacy"]},
                        "safety": {"text": claims[1]["text"], "claim_ids": ["safety"]},
                        "cta": {"text": "Learn more", "claim_ids": []},
                    }
                ),
            }
        )
    )
    assert "errors" not in generated

    report = _result(compliance({"campaign_brief_id": brief_id, "channels": ["banner"]}))
    altered = next(
        result for result in report["channel_results"]["banner"]["claims_checked"] if result["statement"] == statement
    )
    assert report["overall_pass"] is False
    assert altered["status"] == "rejected"


@pytest.mark.parametrize(
    ("channel", "copy_data"),
    [
        (
            "email",
            {
                "subject": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
                "preheader": {"text": "TestDrug may cause nausea.", "claim_ids": ["safety"]},
                "headline": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
                "body": [{"text": "TestDrug may cause nausea.", "claim_ids": ["safety"]}],
                "cta": {"text": "Learn more", "claim_ids": []},
            },
        ),
        (
            "banner",
            {
                "headline": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
                "safety": {"text": "TestDrug may cause nausea.", "claim_ids": ["safety"]},
                "cta": {"text": "Learn more", "claim_ids": []},
            },
        ),
        (
            "poster",
            {
                "headline": {"text": "TestDrug reduces exacerbations by 20%.", "claim_ids": ["efficacy"]},
                "body": [{"text": "TestDrug may cause nausea.", "claim_ids": ["safety"]}],
                "cta": {"text": "Learn more", "claim_ids": []},
            },
        ),
    ],
)
def test_pre_render_legal_checks_require_available_inputs_not_copy_substrings(channel: str, copy_data: dict):
    """Removing legal prose from copy must not fail while the selected kit has every channel-required input."""
    brief_id = f"legal-availability-{channel}"
    claims = _seed(brief_id)
    for claim in claims:
        claim["channels"] = [channel]
    save_artifact(brief_id, "approved-claims.json", claims)
    save_brief(_brief(brief_id, channels=[channel]))
    generated = _result(
        channel_copy({"campaign_brief_id": brief_id, "channel": channel, "copy_json": json.dumps(copy_data)})
    )
    assert "errors" not in generated

    report = _result(compliance({"campaign_brief_id": brief_id, "channels": [channel]}))
    required_checks = [
        check
        for check in report["channel_results"][channel]["policy_checks"]
        if check["check_name"].startswith("required_")
    ]
    assert required_checks
    assert all(check["result"] == "pass" for check in required_checks)
    assert report["overall_pass"] is True


_MALFORMED_BRAND_COMPONENTS = [
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


@pytest.mark.parametrize("manifest", _MALFORMED_BRAND_COMPONENTS)
def test_round_six_validation_rejects_malformed_brand_components_without_writes(manifest: object):
    """Corrupted manifest shapes must be ordinary JSON errors before any channel validation or writes."""
    brief_id = "malformed-brand-components"
    _seed(brief_id)
    (campaign_dir(brief_id) / "brand-components.json").write_text(json.dumps(manifest), encoding="utf-8")

    try:
        content = compliance({"campaign_brief_id": brief_id, "channels": ["email"]})
    except Exception as exc:  # pragma: no cover - failure assertion protects the direct/MCP contract
        pytest.fail(f"validate_claims_and_fair_balance leaked malformed brand components: {exc}")

    assert content[0]["type"] == "text"
    result = _result(content)
    assert result["errors"]
    assert "brand-components" in " ".join(result["errors"]).casefold()
    for name in ("policy-checks.json", "claim-map.json", "source-evidence.json"):
        assert load_validation_artifact(brief_id, name) is None


@pytest.mark.parametrize(
    ("artifact_bytes", "case"),
    [
        pytest.param(b"{", "invalid-json", id="invalid-json"),
        pytest.param(b"\xff", "invalid-utf8", id="invalid-utf8"),
    ],
)
def test_round_seven_validation_rejects_unreadable_brand_components_without_writes(artifact_bytes: bytes, case: str):
    """Unreadable persisted bytes must return structured text JSON before validation writes."""
    brief_id = f"unreadable-brand-components-{case}"
    _seed(brief_id)
    (campaign_dir(brief_id) / "brand-components.json").write_bytes(artifact_bytes)

    try:
        content = compliance({"campaign_brief_id": brief_id, "channels": ["email"]})
    except Exception as exc:  # pragma: no cover - assertion protects the direct boundary
        pytest.fail(f"validate_claims_and_fair_balance leaked unreadable brand components: {exc}")

    assert len(content) == 1
    assert content[0]["type"] == "text"
    result = json.loads(content[0]["text"])
    assert result["errors"]
    assert "brand-components" in " ".join(result["errors"]).casefold()
    for name in ("policy-checks.json", "claim-map.json", "source-evidence.json"):
        assert load_validation_artifact(brief_id, name) is None


@pytest.mark.parametrize("invalid_value", [None, 7, " ", "\u200b"])
@pytest.mark.parametrize("field", ["isi", "pi_ref"])
def test_banner_required_legal_inputs_must_be_string_nonblank_and_visible(field: str, invalid_value: object):
    """A required banner legal input must fail preflight when absent, mistyped, blank, or non-visible."""
    brief_id = f"invalid-banner-legal-{field}-{type(invalid_value).__name__}"
    claims = _seed(brief_id)
    save_brief(_brief(brief_id, channels=["banner"]))
    manifest = load_artifact(brief_id, "brand-components.json")
    manifest["legal"][field] = invalid_value
    save_artifact(brief_id, "brand-components.json", manifest)
    save_artifact(
        brief_id,
        "copy-banner.json",
        {
            "campaign_brief_id": brief_id,
            "channel": "banner",
            "copy": {
                "headline": {"text": claims[0]["text"], "claim_ids": ["efficacy"]},
                "safety": {"text": claims[1]["text"], "claim_ids": ["safety"]},
                "cta": {"text": "Learn more", "claim_ids": []},
            },
        },
    )

    result = _result(compliance({"campaign_brief_id": brief_id, "channels": ["banner"]}))
    assert result["errors"]
    assert field in " ".join(result["errors"])
    for name in ("policy-checks.json", "claim-map.json", "source-evidence.json"):
        assert load_validation_artifact(brief_id, name) is None


def test_bundled_demo_promotional_banner_generates_and_validates_without_pre_rendered_legal_copy():
    """The ordinary demo banner flow relies on selected-kit legal availability until Task 4 renders it."""
    brief_id = "demo-promotional-banner"
    save_brief(
        _brief(
            brief_id,
            brand="ONCORIX",
            indication="oncology",
            channels=["banner"],
            demo_mode=True,
        )
    )
    prepared = _result(
        preflight(
            {
                "campaign_brief_id": brief_id,
                "claims_path": None,
                "brand_kit_path": None,
                "demo_mode": True,
            }
        )
    )
    assert prepared["ready"] is True
    claims = {claim["claim_id"]: claim for claim in load_artifact(brief_id, "approved-claims.json")}
    generated = _result(
        channel_copy(
            {
                "campaign_brief_id": brief_id,
                "channel": "banner",
                "copy_json": json.dumps(
                    {
                        "headline": {"text": claims["c-004"]["text"], "claim_ids": ["c-004"]},
                        "safety": {"text": claims["c-006"]["text"], "claim_ids": ["c-006"]},
                        "cta": {"text": "Learn more", "claim_ids": []},
                    }
                ),
            }
        )
    )
    assert "errors" not in generated

    report = _result(compliance({"campaign_brief_id": brief_id, "channels": ["banner"]}))
    assert report["overall_pass"] is True
    assert all(
        check["result"] == "pass"
        for check in report["channel_results"]["banner"]["policy_checks"]
        if check["check_name"].startswith("required_")
    )


@pytest.mark.parametrize(
    ("case", "claim_overrides", "message_text", "claim_ids"),
    [
        pytest.param("duplicate", {}, "TestDrug reduces exacerbations by 20%.", ["efficacy", "efficacy"]),
        pytest.param("blank", {}, "TestDrug reduces exacerbations by 20%.", ["efficacy", ""]),
        pytest.param("expired", {"expiry": "2020-01-01"}, "TestDrug reduces exacerbations by 20%.", ["efficacy"]),
        pytest.param(
            "jurisdiction",
            {"jurisdictions": ["EMA"]},
            "TestDrug reduces exacerbations by 20%.",
            ["efficacy"],
        ),
        pytest.param(
            "indication",
            {"indications": ["other"]},
            "TestDrug reduces exacerbations by 20%.",
            ["efficacy"],
        ),
        pytest.param("audience", {"audiences": ["patient"]}, "TestDrug reduces exacerbations by 20%.", ["efficacy"]),
        pytest.param("channel", {"channels": ["poster"]}, "TestDrug reduces exacerbations by 20%.", ["efficacy"]),
        pytest.param("numeric-drift", {}, "TestDrug reduces exacerbations by 99%.", ["efficacy"]),
        pytest.param("multi-id-mismatch", {}, "TestDrug reduces exacerbations by 20%.", ["efficacy", "safety"]),
    ],
)
def test_message_tiers_fail_closed_for_invalid_references_or_wording(
    case: str, claim_overrides: dict, message_text: str, claim_ids: list[str]
):
    """Every tier citation must be unique, current, channel-applicable, and independently ground its wording."""
    brief_id = f"message-round-five-{case}"
    claims = _seed(brief_id)
    claims[0].update(claim_overrides)
    save_artifact(brief_id, "approved-claims.json", claims)
    messages = [
        {"tier": "primary", "message": message_text, "claim_ids": claim_ids, "rationale": "Why"},
        {"tier": "secondary", "message": claims[1]["text"], "claim_ids": ["safety"], "rationale": "Why"},
        {"tier": "supporting", "message": claims[1]["text"], "claim_ids": ["safety"], "rationale": "Why"},
    ]
    result = _result(
        architecture(
            {
                "campaign_brief_id": brief_id,
                "messages": messages,
                "fair_balance_statement": claims[1]["text"],
                "fair_balance_sources": [
                    {"document_id": "safety", "document_name": "Approved messages", "excerpt": "Section 1"}
                ],
            }
        )
    )
    assert result["errors"]
    assert load_artifact(brief_id, "message-architecture.json") is None


def test_message_tier_multi_id_is_allowed_only_when_each_claim_grounds_the_same_approved_wording():
    """Two distinct applicable claims may share one tier only when both approve that exact wording."""
    brief_id = "message-round-five-shared-wording"
    claims = _seed(brief_id)
    claims.append(
        _claim(
            "positioning",
            "A separate approved positioning statement.",
            "positioning",
            allowed_variants=[claims[0]["text"]],
        )
    )
    save_artifact(brief_id, "approved-claims.json", claims)
    messages = [
        {
            "tier": "primary",
            "message": claims[0]["text"],
            "claim_ids": ["efficacy", "positioning"],
            "rationale": "Why",
        },
        {"tier": "secondary", "message": claims[1]["text"], "claim_ids": ["safety"], "rationale": "Why"},
        {"tier": "supporting", "message": claims[1]["text"], "claim_ids": ["safety"], "rationale": "Why"},
    ]
    result = _result(
        architecture(
            {
                "campaign_brief_id": brief_id,
                "messages": messages,
                "fair_balance_statement": claims[1]["text"],
                "fair_balance_sources": [
                    {"document_id": "safety", "document_name": "Approved messages", "excerpt": "Section 1"}
                ],
            }
        )
    )
    assert "errors" not in result
    persisted = load_artifact(brief_id, "message-architecture.json")
    assert persisted["message_tiers"][0]["claim_ids"] == ["efficacy", "positioning"]


_MALFORMED_CLAIMS = [
    pytest.param(b"{", id="invalid-json"),
    pytest.param({"claim_id": "not-a-list"}, id="dict-root"),
    pytest.param([None], id="null-item"),
    pytest.param([{"claim_id": "missing-required-fields"}], id="schema-invalid"),
    pytest.param([_claim("duplicate", "First"), _claim("duplicate", "Second")], id="duplicate-id"),
    pytest.param([], id="empty-list"),
]


def _write_malformed_claims(brief_id: str, claims: bytes | list[object] | dict[str, object]) -> None:
    """Persist deliberately malformed data exactly as a stale artifact can contain it."""
    if isinstance(claims, bytes):
        (campaign_dir(brief_id) / "approved-claims.json").write_bytes(claims)
    else:
        save_artifact(brief_id, "approved-claims.json", claims)  # type: ignore[arg-type]


def _generation_call(tool: str, brief_id: str) -> tuple[list[dict], str]:
    copy_payload = {
        "subject": {"text": "Claim", "claim_ids": ["efficacy"]},
        "preheader": {"text": "Claim", "claim_ids": ["efficacy"]},
        "headline": {"text": "Claim", "claim_ids": ["efficacy"]},
        "body": [{"text": "Claim", "claim_ids": ["efficacy"]}],
        "cta": {"text": "Learn more", "claim_ids": []},
    }
    stages = [
        {
            "stage": stage,
            "objective": "Educate",
            "key_messages": ["efficacy"],
            "channels": ["email"],
            "content_type": "promotional",
            "kpi": "opens",
        }
        for stage in ("aware", "interested", "acting")
    ]
    messages = [
        {"tier": tier, "message": "Claim", "claim_ids": ["efficacy"], "rationale": "Why"}
        for tier in ("primary", "secondary", "supporting")
    ]
    if tool == "copy":
        return (
            channel_copy({"campaign_brief_id": brief_id, "channel": "email", "copy_json": json.dumps(copy_payload)}),
            "copy-email.json",
        )
    if tool == "journey":
        return journey({"campaign_brief_id": brief_id, "journey": stages}), "audience-journey.json"
    return (
        architecture(
            {
                "campaign_brief_id": brief_id,
                "messages": messages,
                "fair_balance_statement": "Claim",
                "fair_balance_sources": [
                    {"document_id": "efficacy", "document_name": "Approved messages", "excerpt": "Section 1"}
                ],
            }
        ),
        "message-architecture.json",
    )


@pytest.mark.parametrize("malformed_claims", _MALFORMED_CLAIMS)
@pytest.mark.parametrize("tool", ["copy", "journey", "architecture"])
def test_generation_tools_fail_closed_on_every_malformed_persisted_claim_root(
    malformed_claims: bytes | list[object] | dict[str, object], tool: str
):
    """All claim consumers must return a JSON artifact error and create no downstream artifact."""
    brief_id = f"malformed-{tool}"
    _seed(brief_id)
    _write_malformed_claims(brief_id, malformed_claims)

    try:
        content, output_name = _generation_call(tool, brief_id)
    except Exception as exc:  # pragma: no cover - failure assertion protects direct-MCP behavior
        pytest.fail(f"{tool} leaked malformed persisted claims: {exc}")

    result = _result(content)
    assert result["errors"]
    assert "approved claims artifact" in " ".join(result["errors"]).casefold()
    assert not (campaign_dir(brief_id) / output_name).exists()
