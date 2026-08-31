"""generate_message_architecture — validate and persist a message hierarchy."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class SourceReferenceInput(BaseModel):
    document_id: str = Field(description="Source document identifier")
    document_name: str = Field(description="Original filename")
    page_number: int | None = Field(default=None)
    excerpt: str = Field(description="Verbatim passage from the source")


class MessageTierInput(BaseModel):
    tier: str = Field(description="primary | secondary | supporting")
    message: str = Field(description="The message statement")
    claim_ids: list[str] = Field(min_length=1, description="Approved claim IDs this message draws from")
    audience_stage: str | None = Field(default=None, description="Links to a journey stage")
    rationale: str = Field(description="Why this message matters for the objective")

    @field_validator("claim_ids")
    @classmethod
    def claim_ids_are_unique_and_nonblank(cls, value: list[str]) -> list[str]:
        if any(not claim_id.strip() for claim_id in value):
            raise ValueError("claim_ids must not contain blank values")
        if len(value) != len(set(value)):
            raise ValueError("claim_ids must be unique within a message tier")
        return value


class GenerateMessageArchitectureArgs(BaseModel):
    campaign_brief_id: str = Field(description="Links to the campaign brief")
    messages: list[MessageTierInput] = Field(description="Tiered message hierarchy")
    fair_balance_statement: str = Field(description="Required safety/risk language")
    fair_balance_sources: list[SourceReferenceInput] = Field(description="Sources for the fair balance statement")


TOOL: dict[str, Any] = {
    "name": "generate_message_architecture",
    "description": (
        "Validate and persist a three-tier message architecture (primary, "
        "secondary, supporting). Checks that claim_ids exist in the approved "
        "claims set, at least one primary message is present, and fair balance "
        "is included. The agent generates the messages; this tool validates and saves."
    ),
    "args": GenerateMessageArchitectureArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    from datetime import datetime, timezone

    from .._campaign_store import load_brief, read_campaign_json, save_artifact
    from .._claims import PersistedClaimsError, load_persisted_claims
    from .._workflow_validation import VALID_TIERS, validate_audience_journey, validate_message_data

    campaign_brief_id = arguments["campaign_brief_id"]
    messages = arguments["messages"]
    fair_balance = arguments["fair_balance_statement"]
    fair_balance_sources = arguments.get("fair_balance_sources", [])

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
    journey_stage_names: set[str] | None = None
    available_stages: list[str] = []
    has_staged_message = isinstance(messages, list) and any(
        (message.get("audience_stage") if isinstance(message, dict) else getattr(message, "audience_stage", None))
        is not None
        for message in messages
    )
    if has_staged_message:
        journey_data, journey_read_error, _journey_path = read_campaign_json(campaign_brief_id, "audience-journey.json")
        if journey_read_error:
            return [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "errors": [
                                "audience_stage requires a current valid audience journey. "
                                "Run generate_audience_journey first."
                            ],
                            "available_stages": [],
                            "journey_error": journey_read_error,
                        }
                    ),
                }
            ]
        journey = validate_audience_journey(journey_data, campaign_brief_id, brief, claims_by_id)
        if journey.errors:
            return [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "errors": [
                                "audience_stage requires a current valid audience journey. "
                                "Run generate_audience_journey first."
                            ],
                            "available_stages": [],
                            "journey_error": {
                                "code": "invalid_audience_journey",
                                "message": "The persisted audience journey does not satisfy its workflow contract.",
                                "details": list(journey.errors),
                            },
                        }
                    ),
                }
            ]
        journey_value = journey.value
        if isinstance(journey_value, dict):
            available_stages = [stage["stage"] for stage in journey_value["stages"]]
            journey_stage_names = set(available_stages)

    errors = validate_message_data(
        messages,
        fair_balance,
        fair_balance_sources,
        brief,
        claims_by_id,
        journey_stage_names,
    )
    tier_counts = {
        tier: sum(isinstance(message, dict) and message.get("tier") == tier for message in messages)
        for tier in VALID_TIERS
    }

    if errors:
        response: dict[str, Any] = {"errors": errors}
        if has_staged_message:
            response["available_stages"] = available_stages
        return [{"type": "text", "text": json.dumps(response)}]

    architecture = {
        "campaign_brief_id": campaign_brief_id,
        "brand": brief.get("brand", ""),
        "indication": brief.get("indication", ""),
        "message_tiers": messages if isinstance(messages[0], dict) else [m for m in messages],
        "fair_balance_statement": fair_balance,
        "fair_balance_sources": [
            {
                "document_id": source["document_id"],
                "document_name": claims_by_id[source["document_id"]]["source_document"],
                # Page numbers are not approved-claim provenance.  Never persist
                # model-supplied values as evidence.
                "page_number": None,
                "excerpt": claims_by_id[source["document_id"]]["source_reference"],
            }
            for source in fair_balance_sources
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    save_artifact(campaign_brief_id, "message-architecture.json", architecture)

    result = {
        "campaign_brief_id": campaign_brief_id,
        "total_messages": len(messages),
        "tier_counts": tier_counts,
        "fair_balance_included": True,
    }
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
