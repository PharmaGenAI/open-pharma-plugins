"""retrieve_approved_claims — load and filter approved claims for a campaign."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RetrieveApprovedClaimsArgs(BaseModel):
    campaign_brief_id: str = Field(description="Links claims to the campaign")
    source: str | None = Field(
        default=None,
        description="Explicit path to claims JSON; otherwise uses the brief's stored path",
    )
    demo_mode: bool = Field(default=False, description="Allow bundled fixture only when explicitly true")
    categories: list[str] | None = Field(
        default=None,
        description="Filter: efficacy | positioning | moa | safety | tolerability | dosing",
    )


TOOL: dict[str, Any] = {
    "name": "retrieve_approved_claims",
    "description": (
        "Load the approved claims set for a campaign. Reads from a JSON file "
        "containing ApprovedClaim objects. Optionally filter by category. "
        "Persists the claims to the campaign directory for downstream tools. "
        "If no source is specified, uses the path stored in the campaign brief. "
        "The bundled fixture is available only with demo_mode=true."
    ),
    "args": RetrieveApprovedClaimsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from .._inputs import input_failure_result, resolve_and_persist_claims

    campaign_brief_id = arguments.get("campaign_brief_id") if isinstance(arguments, dict) else None
    try:
        result = resolve_and_persist_claims(
            arguments["campaign_brief_id"],
            arguments.get("source"),
            arguments.get("demo_mode", False),
            arguments.get("categories"),
        )
    except Exception as exc:
        result = input_failure_result(campaign_brief_id, [str(exc)])
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
