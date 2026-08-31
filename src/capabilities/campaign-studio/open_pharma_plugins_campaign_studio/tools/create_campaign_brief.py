"""create_campaign_brief — persist interview data as a campaign brief."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .. import _workflow_validation
from ..models.brief import _valid_https_url


class CreateCampaignBriefArgs(BaseModel):
    # Identity
    campaign_brief_id: str | None = Field(
        default=None,
        description="Resume an existing brief; omit to create new",
    )
    campaign_name: str = Field(description="Short campaign name")

    # Jurisdiction & mode
    country: str = Field(description="ISO 3166-1 alpha-2 country code")
    policy_jurisdiction: str = Field(description="FDA | EMA | MHRA | HSA | PMDA | TGA")
    mode: str = Field(description="promotional | non_promotional | disease_awareness")

    # Product
    brand: str = Field(description="Brand name")
    indication: str = Field(description="Approved indication")
    lifecycle_stage: str = Field(
        default="growth",
        description="pre_launch | launch | growth | mature | LOE",
    )

    # Audience & objectives
    target_segment: str = Field(description="e.g. oncologists, PCPs, nurses, patients")
    behavioral_objective: str = Field(description="What the audience should DO after exposure")
    educational_objective: str | None = Field(default=None)
    desired_kpi: list[str] = Field(description="e.g. ['open_rate>25%', 'HCP_reach_500']")

    # Content sources
    approved_claims_path: str | None = Field(
        default=None,
        description="Path to claims JSON; required by preflight unless demo_mode is true",
    )
    demo_mode: bool = Field(default=False, description="Allow bundled fixtures only when explicitly true")
    call_to_action: str = Field(description="Primary CTA text")
    call_to_action_url: str = Field(description="HTTPS URL for the primary CTA")

    # Channels
    channels: list[str] = Field(description="['email', 'banner', 'poster']")
    asset_dimensions: dict | None = Field(
        default=None,
        description='{"banner": "728x90", "poster": "A4"}',
    )

    # Brand
    brand_kit_path: str | None = Field(
        default=None,
        description="Path to brand kit directory; required by preflight unless demo_mode is true",
    )

    # Localisation
    language: str = Field(default="en")
    localisation_notes: str | None = Field(default=None)

    # Compliance
    required_safety_content: list[str] | None = Field(
        default=None,
        description="ISI, boxed warning, PI reference",
    )
    required_legal_content: list[str] | None = Field(
        default=None,
        description="Copyright, disclaimer, reporting statement",
    )

    # Delivery
    delivery_constraints: str | None = Field(default=None)
    approval_workflow: str = Field(
        default="mlr_standard",
        description="mlr_standard | mlr_expedited | medical_only",
    )


TOOL: dict[str, Any] = {
    "name": "create_campaign_brief",
    "description": (
        "Create or update a campaign brief from interview data. Validates all "
        "fields, generates a reusable campaign_brief_id, persists the brief to "
        "the campaign store, and returns the brief with any assumptions noted. "
        "The Skill drives the interview conversation — this tool validates and "
        "saves the collected data."
    ),
    "args": CreateCampaignBriefArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    from datetime import datetime, timezone

    from .._campaign_store import generate_campaign_id, load_brief, save_brief

    errors: list[str] = []
    assumptions: list[str] = []

    mode = arguments["mode"]
    if mode not in _workflow_validation.VALID_MODES:
        errors.append(f"Invalid mode '{mode}'. Must be one of: {', '.join(sorted(_workflow_validation.VALID_MODES))}")

    jurisdiction = arguments["policy_jurisdiction"]
    if jurisdiction not in _workflow_validation.VALID_JURISDICTIONS:
        errors.append(
            "Invalid jurisdiction "
            f"'{jurisdiction}'. Must be one of: {', '.join(sorted(_workflow_validation.VALID_JURISDICTIONS))}"
        )

    lifecycle = arguments.get("lifecycle_stage", "growth")
    if lifecycle not in _workflow_validation.VALID_LIFECYCLES:
        errors.append(
            "Invalid lifecycle_stage "
            f"'{lifecycle}'. Must be one of: {', '.join(sorted(_workflow_validation.VALID_LIFECYCLES))}"
        )

    channels = arguments["channels"]
    invalid_channels = {
        channel
        for channel in channels
        if isinstance(channel, str) and channel.strip() and channel not in _workflow_validation.VALID_CHANNELS
    }
    if invalid_channels:
        errors.append(
            f"Invalid channels: {', '.join(sorted(invalid_channels))}. "
            f"Must be from: {', '.join(sorted(_workflow_validation.VALID_CHANNELS))}"
        )

    workflow = arguments.get("approval_workflow", "mlr_standard")
    if workflow not in _workflow_validation.VALID_WORKFLOWS:
        errors.append(
            "Invalid approval_workflow "
            f"'{workflow}'. Must be one of: {', '.join(sorted(_workflow_validation.VALID_WORKFLOWS))}"
        )

    country = arguments["country"].upper()
    if len(country) != 2 or not country.isascii() or not country.isalpha():
        errors.append(f"Invalid country '{arguments['country']}'. Must be a two-letter ISO 3166-1 alpha-2 code.")

    if not arguments.get("desired_kpi"):
        errors.append("desired_kpi must contain at least one value.")
    if not channels:
        errors.append("channels must contain at least one value.")

    call_to_action_url = arguments.get("call_to_action_url")
    if not _valid_https_url(call_to_action_url):
        errors.append("call_to_action_url must be an HTTPS URL with a valid hostname.")

    language = arguments.get("language", "en")
    if language != "en":
        errors.append(f"Unsupported language '{language}'. Campaign Studio supports only 'en'.")

    if errors:
        return [
            {
                "type": "text",
                "text": json.dumps({"errors": errors}),
            }
        ]

    # Resolve or generate campaign_brief_id
    campaign_brief_id = arguments.get("campaign_brief_id")
    if campaign_brief_id:
        existing = load_brief(campaign_brief_id)
        if existing:
            assumptions.append(f"Updating existing brief '{campaign_brief_id}'")
    else:
        campaign_brief_id = generate_campaign_id(arguments["campaign_name"], arguments["brand"])

    # Handle defaults and assumptions
    safety = arguments.get("required_safety_content")
    if not safety:
        safety = []
        assumptions.append("No safety content specified — will use default ISI from brand kit")

    legal = arguments.get("required_legal_content")
    if not legal:
        legal = []
        assumptions.append("No legal content specified — will use default from brand kit")

    if not arguments.get("approved_claims_path") and not arguments.get("demo_mode", False):
        assumptions.append("No claims path specified — preflight requires a path unless demo_mode=true")

    if not arguments.get("brand_kit_path") and not arguments.get("demo_mode", False):
        assumptions.append("No brand kit path specified — preflight requires a path unless demo_mode=true")

    brief = {
        "campaign_brief_id": campaign_brief_id,
        "campaign_name": arguments["campaign_name"],
        "country": country,
        "policy_jurisdiction": jurisdiction,
        "mode": mode,
        "brand": arguments["brand"],
        "indication": arguments["indication"],
        "lifecycle_stage": lifecycle,
        "target_segment": arguments["target_segment"],
        "behavioral_objective": arguments["behavioral_objective"],
        "educational_objective": arguments.get("educational_objective"),
        "desired_kpi": arguments["desired_kpi"],
        "approved_claims_path": arguments.get("approved_claims_path"),
        "demo_mode": arguments.get("demo_mode", False),
        "call_to_action": arguments["call_to_action"],
        "call_to_action_url": call_to_action_url,
        "channels": channels,
        "asset_dimensions": arguments.get("asset_dimensions"),
        "brand_kit_path": arguments.get("brand_kit_path"),
        "language": arguments.get("language", "en"),
        "localisation_notes": arguments.get("localisation_notes"),
        "required_safety_content": safety,
        "required_legal_content": legal,
        "delivery_constraints": arguments.get("delivery_constraints"),
        "approval_workflow": workflow,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    candidate = _workflow_validation.validate_campaign_brief(brief, campaign_brief_id)
    if candidate.errors:
        return [{"type": "text", "text": json.dumps({"errors": list(candidate.errors)})}]

    save_brief(brief)

    result = {
        "campaign_brief_id": campaign_brief_id,
        "campaign_name": brief["campaign_name"],
        "brand": brief["brand"],
        "mode": brief["mode"],
        "channels": brief["channels"],
        "brief_path": str(__import__("pathlib").Path(_brief_path(campaign_brief_id))),
        "assumptions": assumptions,
    }
    return [{"type": "text", "text": json.dumps(result, indent=2)}]


def _brief_path(campaign_brief_id: str) -> str:
    from .._campaign_store import campaign_dir

    return str(campaign_dir(campaign_brief_id) / "campaign-brief.json")
