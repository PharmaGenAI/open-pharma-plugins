"""generate_audience_journey — validate and persist an audience journey."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class JourneyStageInput(BaseModel):
    stage: str = Field(description="unaware | aware | interested | convinced | acting | advocating")
    objective: str = Field(description="What should happen at this stage")
    key_messages: list[str] = Field(description="claim_id references")
    channels: list[str] = Field(description="Channels serving this stage")
    content_type: str = Field(description="educational | promotional | reminder")
    kpi: str = Field(description="Measurable outcome for this stage")


class GenerateAudienceJourneyArgs(BaseModel):
    campaign_brief_id: str = Field(description="Links to the campaign brief")
    journey: list[JourneyStageInput] = Field(description="Ordered list of journey stages")


TOOL: dict[str, Any] = {
    "name": "generate_audience_journey",
    "description": (
        "Validate and persist an audience journey for a campaign. Checks that "
        "every channel referenced exists in the brief's channel list, and "
        "every claim_id in key_messages exists in the approved claims set. "
        "The agent generates the journey content; this tool validates and saves."
    ),
    "args": GenerateAudienceJourneyArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    from datetime import datetime, timezone

    from .._campaign_store import load_brief, save_artifact
    from .._claims import PersistedClaimsError, load_persisted_claims
    from .._workflow_validation import validate_journey_stages

    campaign_brief_id = arguments["campaign_brief_id"]
    stages = arguments["journey"]

    brief = load_brief(campaign_brief_id)
    if not brief:
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {"error": f"Campaign brief '{campaign_brief_id}' not found. Run create_campaign_brief first."}
                ),
            }
        ]

    try:
        claims_data = load_persisted_claims(campaign_brief_id)
    except PersistedClaimsError as exc:
        return [{"type": "text", "text": json.dumps({"errors": exc.errors})}]
    claims_by_id = {claim["claim_id"]: claim for claim in claims_data}
    errors = validate_journey_stages(stages, brief, claims_by_id)
    warnings: list[str] = []

    if errors:
        return [
            {
                "type": "text",
                "text": json.dumps({"errors": errors, "warnings": warnings}),
            }
        ]

    journey = {
        "campaign_brief_id": campaign_brief_id,
        "target_segment": brief.get("target_segment", ""),
        "stages": stages,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    save_artifact(campaign_brief_id, "audience-journey.json", journey)

    result = {
        "campaign_brief_id": campaign_brief_id,
        "total_stages": len(stages),
        "stages": [s.get("stage", "") if isinstance(s, dict) else s for s in stages],
        "warnings": warnings,
    }
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
