"""retrieve_brand_components — load brand kit assets for rendering."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RetrieveBrandComponentsArgs(BaseModel):
    campaign_brief_id: str = Field(description="Links to the campaign")
    brand_kit_path: str | None = Field(
        default=None,
        description="Explicit brand kit directory; otherwise uses the brief's stored path",
    )
    demo_mode: bool = Field(default=False, description="Allow bundled fixture only when explicitly true")


TOOL: dict[str, Any] = {
    "name": "retrieve_brand_components",
    "description": (
        "Load brand components (logo, product image, colour palette, typography, "
        "and legal content) for a campaign. Returns paths, provenance, and "
        "structured data for use by renderers. The bundled sample kit is "
        "available only with demo_mode=true."
    ),
    "args": RetrieveBrandComponentsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from .._inputs import input_failure_result, resolve_and_persist_brand_kit

    campaign_brief_id = arguments.get("campaign_brief_id") if isinstance(arguments, dict) else None
    try:
        result = resolve_and_persist_brand_kit(
            arguments["campaign_brief_id"], arguments.get("brand_kit_path"), arguments.get("demo_mode", False)
        )
    except Exception as exc:
        result = input_failure_result(campaign_brief_id, [str(exc)])
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
