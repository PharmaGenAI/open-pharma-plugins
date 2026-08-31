"""generate_channel_copy — validate and persist structured copy for a channel."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GenerateChannelCopyArgs(BaseModel):
    campaign_brief_id: str = Field(description="Links to the campaign brief")
    channel: str = Field(description="email | banner | poster")
    copy_json: str = Field(
        description=(
            "JSON string of the channel-specific copy. For email: "
            '{"subject": {"text": "...", "claim_ids": []}, "preheader": {...}, '
            '"headline": {...}, "body": [{...}], "cta": {...}}. '
            "For banner: {headline, sub_headline?, safety?, cta}; promotional efficacy "
            "banners require safety. "
            "For poster: {headline, subhead?, body, bullet_points?, cta, footnotes?}."
        )
    )


TOOL: dict[str, Any] = {
    "name": "generate_channel_copy",
    "description": (
        "Validate and persist structured copy for a specific channel. Accepts "
        "a JSON string with channel-specific copy blocks (EmailCopy, BannerCopy, "
        "or PosterCopy). Validates that claim_ids exist in the approved claims "
        "set, enforces character limits per channel, and checks the CTA matches "
        "the brief. The agent generates the copy; this tool validates and saves."
    ),
    "args": GenerateChannelCopyArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    from datetime import datetime, timezone

    from .._campaign_store import load_brief, save_artifact
    from .._claims import PersistedClaimsError, load_persisted_claims
    from .._workflow_validation import VALID_CHANNELS, extract_copy_blocks, validate_copy_data

    campaign_brief_id = arguments["campaign_brief_id"]
    channel = arguments["channel"]
    copy_json_str = arguments["copy_json"]

    if channel not in VALID_CHANNELS:
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {"error": f"Invalid channel '{channel}'. Must be one of: {', '.join(sorted(VALID_CHANNELS))}"}
                ),
            }
        ]

    brief = load_brief(campaign_brief_id)
    if not brief:
        return [
            {
                "type": "text",
                "text": json.dumps({"error": f"Campaign brief '{campaign_brief_id}' not found."}),
            }
        ]

    if channel not in brief.get("channels", []):
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {"error": f"Channel '{channel}' not in brief's channels: {brief.get('channels', [])}"}
                ),
            }
        ]

    try:
        copy_data = json.loads(copy_json_str)
    except json.JSONDecodeError as e:
        return [{"type": "text", "text": json.dumps({"error": f"Invalid JSON: {e}"})}]

    from pydantic import ValidationError

    from ..models.copy import BannerCopy, EmailCopy, PosterCopy

    copy_models = {"email": EmailCopy, "banner": BannerCopy, "poster": PosterCopy}
    try:
        copy_data = copy_models[channel].model_validate(copy_data).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        return [{"type": "text", "text": json.dumps({"errors": [f"Invalid {channel} copy: {exc}"]})}]

    try:
        claims_data = load_persisted_claims(campaign_brief_id)
    except PersistedClaimsError as exc:
        return [{"type": "text", "text": json.dumps({"errors": exc.errors})}]
    claims_by_id = {claim["claim_id"]: claim for claim in claims_data}
    warnings: list[str] = []
    from .._renderer import load_brand_kit

    legal = load_brand_kit(campaign_brief_id).get("legal", {})
    errors = validate_copy_data(copy_data, channel, brief, claims_by_id, legal)
    all_blocks = extract_copy_blocks(copy_data, channel)

    if channel == "banner":
        headline = copy_data.get("headline", {})
        headline_text = headline.get("text", "")
        word_count = len(headline_text.split())
        if word_count > 8:
            warnings.append(f"Banner headline has {word_count} words (recommended max: 8).")

        cta = copy_data.get("cta", {})
        cta_text = cta.get("text", "")
        cta_words = len(cta_text.split())
        if cta_words > 3:
            warnings.append(f"Banner CTA has {cta_words} words (recommended max: 3).")

    if errors:
        return [
            {
                "type": "text",
                "text": json.dumps({"errors": errors, "warnings": warnings}),
            }
        ]

    artifact = {
        "campaign_brief_id": campaign_brief_id,
        "channel": channel,
        "copy": copy_data,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    save_artifact(campaign_brief_id, f"copy-{channel}.json", artifact)

    result = {
        "campaign_brief_id": campaign_brief_id,
        "channel": channel,
        "total_blocks": len(all_blocks),
        "warnings": warnings,
    }
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
