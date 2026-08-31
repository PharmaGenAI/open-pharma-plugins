"""validate_rendered_assets - inspect actual campaign outputs and seal their bytes."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ValidateRenderedAssetsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_brief_id: str = Field(description="Campaign brief ID whose actual outputs must be validated")


TOOL: dict[str, Any] = {
    "name": "validate_rendered_assets",
    "description": (
        "Inspect every actual rendered channel file, enforce copy/legal/link/dimension/asset contracts, "
        "and persist a fingerprint- and SHA-256-bound rendered-assets report."
    ),
    "args": ValidateRenderedAssetsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    from .._render_validation import validate_rendered_campaign

    result = validate_rendered_campaign(arguments.get("campaign_brief_id"))
    return [{"type": "text", "text": json.dumps(result, ensure_ascii=False, sort_keys=True)}]
