"""preflight_campaign_inputs — validate and persist Campaign Studio sources."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from .._inputs import input_failure_result, preflight_inputs


class PreflightCampaignInputsArgs(BaseModel):
    campaign_brief_id: str = Field(description="Existing campaign brief to prepare")
    claims_path: str | None = Field(default=None, description="Explicit approved-claims JSON path")
    brand_kit_path: str | None = Field(default=None, description="Explicit brand-kit directory path")
    demo_mode: bool = Field(default=False, description="Allow bundled fixtures only when explicitly true")


TOOL: dict[str, Any] = {
    "name": "preflight_campaign_inputs",
    "description": (
        "Fail-closed validation of approved claims and brand-kit inputs for an existing campaign brief. "
        "Persists selected inputs and provenance only when the complete input set is ready."
    ),
    "args": PreflightCampaignInputsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    campaign_brief_id = arguments.get("campaign_brief_id") if isinstance(arguments, dict) else None
    try:
        result = preflight_inputs(
            campaign_brief_id=arguments["campaign_brief_id"],
            claims_path=arguments.get("claims_path"),
            brand_kit_path=arguments.get("brand_kit_path"),
            demo_mode=arguments.get("demo_mode", False),
        )
    except Exception as exc:
        result = input_failure_result(campaign_brief_id, [str(exc)])
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
