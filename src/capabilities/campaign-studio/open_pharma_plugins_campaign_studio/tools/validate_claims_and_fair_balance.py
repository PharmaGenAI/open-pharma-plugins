"""validate_claims_and_fair_balance — compliance gate before rendering."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field, ValidationError

_VALID_CHANNELS = {"email", "banner", "poster"}


class ValidateClaimsArgs(BaseModel):
    campaign_brief_id: str = Field(description="Links to the campaign brief")
    channels: list[str] | None = Field(
        default=None,
        description="Validate specific channels; all if omitted",
    )


TOOL: dict[str, Any] = {
    "name": "validate_claims_and_fair_balance",
    "description": (
        "Run the compliance gate on generated channel copy. Reads copy and "
        "approved claims from the campaign directory and performs three checks: "
        "(1) claim grounding — require exact normalized canonical wording or an explicit "
        "allowed variant; fuzzy similarity is diagnostic only and never grants approval; "
        "(2) fair balance — ratio of safety to efficacy content; "
        "(3) policy compliance — jurisdiction-required elements, prohibited "
        "language patterns. Persists claim-map.json, policy-checks.json, and "
        "source-evidence.json. ALL checks must pass before rendering."
    ),
    "args": ValidateClaimsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    from datetime import datetime, timezone

    from .._campaign_store import load_artifact, load_brief, save_validation_artifact
    from .._claim_engine import (
        banner_safety_errors,
        check_fair_balance,
        check_prohibited_language,
        check_required_elements,
        claim_applicability_errors,
        is_claim_citation_exempt,
        load_policy_rules,
        policy_metadata,
        validate_claim_wording,
    )
    from .._claims import PersistedClaimsError, load_persisted_claims
    from .._renderer import validation_input_fingerprint

    campaign_brief_id = arguments["campaign_brief_id"]
    requested_channels = arguments.get("channels")

    brief = load_brief(campaign_brief_id)
    if not brief:
        return [
            {
                "type": "text",
                "text": json.dumps({"error": f"Campaign brief '{campaign_brief_id}' not found."}),
            }
        ]

    try:
        claims_data = load_persisted_claims(campaign_brief_id)
    except PersistedClaimsError as exc:
        return [{"type": "text", "text": json.dumps({"errors": exc.errors})}]

    channels = brief.get("channels", []) if requested_channels is None else requested_channels
    if not isinstance(channels, list) or any(not isinstance(channel, str) for channel in channels):
        return [{"type": "text", "text": json.dumps({"errors": ["Channels must be a list of channel names."]})}]
    if not channels:
        return [
            {"type": "text", "text": json.dumps({"errors": ["At least one channel must be selected for validation."]})}
        ]
    duplicate_channels = sorted({channel for channel in channels if channels.count(channel) > 1})
    if duplicate_channels:
        return [{"type": "text", "text": json.dumps({"errors": [f"Duplicate channels: {duplicate_channels}"]})}]
    unsupported_channels = sorted(set(channels) - _VALID_CHANNELS)
    if unsupported_channels:
        return [{"type": "text", "text": json.dumps({"errors": [f"Unsupported channels: {unsupported_channels}"]})}]
    brief_channels = brief.get("channels", [])
    if not isinstance(brief_channels, list) or len(brief_channels) != len(set(brief_channels)):
        return [{"type": "text", "text": json.dumps({"errors": ["Brief channels must be unique."]})}]
    invalid_channels = sorted(set(channels) - set(brief_channels))
    if invalid_channels:
        return [{"type": "text", "text": json.dumps({"errors": [f"Channels not in brief: {invalid_channels}"]})}]
    jurisdiction = brief.get("policy_jurisdiction", "FDA")
    mode = brief.get("mode", "promotional")

    rules = load_policy_rules(jurisdiction)
    required_by_channel = {
        channel: rules.get("channel_requirements", {})
        .get(channel, {})
        .get("required_elements", rules.get("required_elements", []))
        for channel in channels
    }
    required_legal_elements = list(
        dict.fromkeys(element for channel in channels for element in required_by_channel[channel])
    )

    try:
        brand_manifest = load_artifact(campaign_brief_id, "brand-components.json")
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return [
            {
                "type": "text",
                "text": json.dumps({"errors": [f"Invalid brand-components.json: could not read artifact: {exc}"]}),
            }
        ]
    if brand_manifest is None:
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "errors": [
                            "No valid brand-components.json found; its root must be a JSON object. "
                            "Run preflight_campaign_inputs first."
                        ]
                    }
                ),
            }
        ]
    if not isinstance(brand_manifest, Mapping):
        return [
            {
                "type": "text",
                "text": json.dumps({"errors": ["Invalid brand-components.json: root must be a JSON object."]}),
            }
        ]
    brand_kit = brand_manifest.get("legal")
    if not isinstance(brand_kit, Mapping):
        return [
            {
                "type": "text",
                "text": json.dumps({"errors": ["Invalid brand-components.json: 'legal' must be a JSON object."]}),
            }
        ]
    brand_kit = dict(brand_kit)
    required_legal_errors = [
        check["detail"]
        for check in check_required_elements(brand_kit, required_legal_elements)
        if check.get("result") == "fail"
    ]
    if required_legal_errors:
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {"errors": [f"Invalid brand-components.json: {detail}" for detail in required_legal_errors]}
                ),
            }
        ]

    all_claim_results: list[dict] = []
    all_policy_checks: list[dict] = []
    channel_results: dict[str, dict] = {}
    claim_by_id = {claim.get("claim_id"): claim for claim in claims_data}
    used_claim_ids: set[str] = set()
    metadata = policy_metadata()

    for channel in channels:
        channel_claim_results: list[dict] = []
        channel_policy_checks: list[dict] = []
        try:
            copy_artifact = load_artifact(campaign_brief_id, f"copy-{channel}.json")
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            copy_artifact = None
            invalid_copy_detail = f"Could not read copy artifact: {exc}"
        else:
            invalid_copy_detail = _validate_copy_artifact(copy_artifact, campaign_brief_id, channel)
        if not copy_artifact:
            channel_policy_checks.append(
                {
                    "check_name": "invalid_copy" if invalid_copy_detail else f"copy_{channel}_exists",
                    "result": "fail",
                    "detail": invalid_copy_detail or f"No copy-{channel}.json found. Run generate_channel_copy first.",
                }
            )
            channel_results[channel] = {
                "channel": channel,
                "copy_exists": False,
                "claims_checked": channel_claim_results,
                "policy_checks": channel_policy_checks,
                "overall_pass": False,
            }
            all_policy_checks.extend(channel_policy_checks)
            continue

        if invalid_copy_detail:
            channel_policy_checks.append(
                {"check_name": "invalid_copy", "result": "fail", "detail": invalid_copy_detail}
            )
            channel_results[channel] = {
                "channel": channel,
                "copy_exists": True,
                "claims_checked": channel_claim_results,
                "policy_checks": channel_policy_checks,
                "overall_pass": False,
            }
            all_policy_checks.extend(channel_policy_checks)
            continue

        copy_data = copy_artifact.get("copy", {})
        blocks = _extract_all_blocks(copy_data, channel)
        all_text_parts = [str(block.get("text", "")) for _, block in blocks]
        channel_text = " ".join(all_text_parts)

        if channel == "poster":
            for index, footnote in enumerate(copy_data.get("footnotes") or []):
                if isinstance(footnote, str) and not is_claim_citation_exempt("footnote", footnote, brief, brand_kit):
                    channel_policy_checks.append(
                        {
                            "check_name": "invalid_footnote",
                            "result": "fail",
                            "detail": f"poster.footnotes[{index}] must be verbatim approved legal text.",
                        }
                    )
                elif isinstance(footnote, dict) and not footnote.get("claim_ids"):
                    channel_policy_checks.append(
                        {
                            "check_name": "invalid_footnote",
                            "result": "fail",
                            "detail": f"poster.footnotes[{index}] CopyBlock must cite an approved claim ID.",
                        }
                    )

        if channel == "banner":
            for detail in banner_safety_errors(copy_data, claim_by_id, brief):
                channel_policy_checks.append({"check_name": "banner_safety", "result": "fail", "detail": detail})

        for block_name, block in blocks:
            text = block.get("text", "")
            claim_ids = block.get("claim_ids", [])
            if not claim_ids:
                if mode == "promotional" and text and not is_claim_citation_exempt(block_name, text, brief, brand_kit):
                    channel_policy_checks.append(
                        {
                            "check_name": "missing_claim_citation",
                            "result": "fail",
                            "detail": f"{channel}.{block_name}: promotional copy has no approved claim ID.",
                        }
                    )
                continue

            for claim_id in claim_ids:
                claim = claim_by_id.get(claim_id)
                used_claim_ids.add(claim_id)
                if claim is None:
                    channel_claim_results.append(
                        {
                            "claim_id": claim_id,
                            "declared_claim_id": claim_id,
                            "statement": text,
                            "status": "not_found",
                            "matched_claim_text": None,
                            "similarity_score": 0.0,
                            "deviation": "Declared claim ID is not in the approved claims set",
                        }
                    )
                    continue
                applicability = claim_applicability_errors(claim, brief, channel)
                match_result = validate_claim_wording(text, claim)
                if applicability:
                    match_result["status"] = "rejected"
                    match_result["deviation"] = "Claim is inapplicable: " + ", ".join(applicability)
                match_result["declared_claim_id"] = claim_id
                match_result["statement"] = text
                channel_claim_results.append(match_result)

        if rules.get("fair_balance_required", True):
            channel_policy_checks.append(
                check_fair_balance([block for _, block in blocks], claims_data, rules.get("min_safety_ratio", 0.3))
            )
        channel_policy_checks.extend(check_prohibited_language(channel_text, rules.get("prohibited_patterns", [])))
        if mode in ("non_promotional", "disease_awareness"):
            channel_policy_checks.extend(
                check_prohibited_language(channel_text, rules.get("non_promotional_prohibited", []))
            )
        required = required_by_channel[channel]
        channel_policy_checks.extend(check_required_elements(brand_kit, required))

        channel_pass = all(check.get("result") != "fail" for check in channel_policy_checks) and all(
            result.get("status") == "approved" for result in channel_claim_results
        )
        channel_results[channel] = {
            "channel": channel,
            "copy_exists": True,
            "claims_checked": channel_claim_results,
            "policy_checks": channel_policy_checks,
            "overall_pass": channel_pass,
        }
        all_claim_results.extend(channel_claim_results)
        all_policy_checks.extend(channel_policy_checks)

    overall_pass = all(result["overall_pass"] for result in channel_results.values()) and all(
        claim.get("status") == "approved" for claim in all_claim_results
    )

    # Build source evidence
    source_evidence = []
    for claim in claims_data:
        if claim.get("claim_id") not in used_claim_ids:
            continue
        source_evidence.append(
            {
                "document_id": claim.get("claim_id", ""),
                "document_name": claim.get("source_document", ""),
                "page_number": None,
                "excerpt": claim.get("source_reference", ""),
            }
        )

    # Build claim map
    claim_map: dict[str, list[str]] = {}
    for result in all_claim_results:
        stmt = result.get("statement", "")[:60]
        cid = result.get("declared_claim_id")
        if cid:
            claim_map.setdefault(stmt, []).append(cid)

    # Persist
    report = {
        "campaign_brief_id": campaign_brief_id,
        "channels_validated": channels,
        "claims_checked": all_claim_results,
        "policy_checks": all_policy_checks,
        "channel_results": channel_results,
        "policy_version": metadata["version"],
        "policy_hash": metadata["hash"],
        "overall_pass": overall_pass,
        "input_fingerprint": validation_input_fingerprint(campaign_brief_id, channels),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    save_validation_artifact(campaign_brief_id, "policy-checks.json", report)
    save_validation_artifact(campaign_brief_id, "claim-map.json", claim_map)
    save_validation_artifact(campaign_brief_id, "source-evidence.json", source_evidence)

    summary = {
        "campaign_brief_id": campaign_brief_id,
        "overall_pass": overall_pass,
        "channels_validated": channels,
        "channel_results": channel_results,
        "policy_version": metadata["version"],
        "policy_hash": metadata["hash"],
        "claims_total": len(all_claim_results),
        "claims_approved": sum(1 for c in all_claim_results if c.get("status") == "approved"),
        "claims_needs_review": sum(1 for c in all_claim_results if c.get("status") == "needs_review"),
        "claims_not_found": sum(1 for c in all_claim_results if c.get("status") == "not_found"),
        "policy_pass": sum(1 for c in all_policy_checks if c.get("result") == "pass"),
        "policy_warn": sum(1 for c in all_policy_checks if c.get("result") == "warn"),
        "policy_fail": sum(1 for c in all_policy_checks if c.get("result") == "fail"),
        "failures": [c for c in all_policy_checks if c.get("result") == "fail"]
        + [c for c in all_claim_results if c.get("status") != "approved"],
    }
    return [{"type": "text", "text": json.dumps(summary, indent=2)}]


def _extract_all_blocks(copy_data: dict, channel: str) -> list[tuple[str, dict]]:
    """Extract named copy blocks from channel-specific copy data."""
    blocks: list[tuple[str, dict]] = []

    simple = {
        "email": ["subject", "preheader", "headline", "cta"],
        "banner": ["headline", "sub_headline", "safety", "cta"],
        "poster": ["headline", "subhead", "cta"],
    }
    for field in simple.get(channel, []):
        val = copy_data.get(field)
        if val and isinstance(val, dict):
            blocks.append((field, val))

    list_fields = {
        "email": ["body"],
        "poster": ["body", "bullet_points"],
    }
    for field in list_fields.get(channel, []):
        items = copy_data.get(field)
        if items and isinstance(items, list):
            for i, item in enumerate(items):
                if isinstance(item, dict):
                    blocks.append((f"{field}[{i}]", item))

    if channel == "poster":
        for i, footnote in enumerate(copy_data.get("footnotes") or []):
            if isinstance(footnote, dict):
                blocks.append((f"footnotes[{i}]", footnote))
            elif isinstance(footnote, str):
                blocks.append((f"footnotes[{i}]", {"text": footnote, "claim_ids": []}))

    return blocks


def _extract_flat_blocks(channels: list[str], campaign_brief_id: str) -> list[tuple[str, dict]]:
    """Load and extract blocks from all channel copy files."""
    from .._campaign_store import load_artifact

    blocks: list[tuple[str, dict]] = []
    for channel in channels:
        copy_artifact = load_artifact(campaign_brief_id, f"copy-{channel}.json")
        if copy_artifact:
            blocks.extend(_extract_all_blocks(copy_artifact.get("copy", {}), channel))
    return blocks


def _validate_copy_artifact(copy_artifact: object, campaign_brief_id: str, channel: str) -> str | None:
    """Validate the trusted on-disk envelope and its concrete channel copy model."""
    from ..models.copy import BannerCopy, EmailCopy, PersistedChannelCopy, PosterCopy

    try:
        envelope = PersistedChannelCopy.model_validate(copy_artifact)
    except ValidationError as exc:
        return f"Copy artifact envelope is invalid: {exc}"
    if envelope.campaign_brief_id != campaign_brief_id:
        return "Copy artifact campaign_brief_id does not match the validated campaign."
    if envelope.channel != channel:
        return f"Copy artifact channel '{envelope.channel}' does not match requested channel '{channel}'."
    model = {"email": EmailCopy, "banner": BannerCopy, "poster": PosterCopy}[channel]
    try:
        model.model_validate(envelope.copy_data)
    except ValidationError as exc:
        return f"{channel} copy is invalid: {exc}"
    return None
