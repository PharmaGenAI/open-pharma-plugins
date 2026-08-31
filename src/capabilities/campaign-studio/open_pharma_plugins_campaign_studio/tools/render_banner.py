"""render_banner - produce an inspected, self-contained SVG banner."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class RenderBannerArgs(BaseModel):
    campaign_brief_id: str = Field(description="Links to the campaign")
    dimensions: str | None = Field(default=None, description="Compatibility WxH override; must equal the brief")


TOOL: dict[str, Any] = {
    "name": "render_banner",
    "description": "Render an inspected, self-contained SVG banner at the exact brief-controlled dimensions.",
    "args": RenderBannerArgs,
}


def _geometry(profile: str, width: int, height: int, lines: dict[str, list[str]]) -> dict[str, Any]:
    if profile == "horizontal":
        legal_leading = 5.7
        isi_y = 39
        return {
            "spine_width": 92,
            "spine_height": height,
            "rule_height": height,
            "endpoint_y": 14,
            "logo_x": 10,
            "logo_y": 31,
            "logo_width": 72,
            "logo_height": 28,
            "claim_x": 112,
            "headline_y": 22,
            "headline_size": 11,
            "headline_leading": 14,
            "subheadline_y": 22 + len(lines["headline"]) * 14 + 2,
            "subheadline_size": 6.8,
            "subheadline_leading": 8,
            "safety_x": 355,
            "safety_y": 13,
            "safety_size": 6.8,
            "safety_leading": 7.5,
            "legal_x": 355,
            "isi_y": isi_y,
            "pi_y": isi_y + len(lines["legal-isi"]) * legal_leading + 2,
            "legal_size": 5.4,
            "legal_leading": legal_leading,
            "cta_x": 112,
            "cta_y": 58,
            "cta_width": 132,
            "cta_height": 23,
            "cta_size": 9,
        }
    if profile == "rectangle":
        legal_leading = 8
        isi_y = 126
        return {
            "spine_width": 66,
            "spine_height": 100,
            "rule_height": 100,
            "endpoint_y": 22,
            "logo_x": 7,
            "logo_y": 35,
            "logo_width": 52,
            "logo_height": 30,
            "claim_x": 84,
            "headline_y": 32,
            "headline_size": 13,
            "headline_leading": 16,
            "subheadline_y": 32 + len(lines["headline"]) * 16 + 4,
            "subheadline_size": 8,
            "subheadline_leading": 10,
            "safety_x": 84,
            "safety_y": 82 + len(lines.get("sub_headline", [])) * 20,
            "safety_size": 8,
            "safety_leading": 10,
            "legal_x": 18,
            "isi_y": isi_y,
            "pi_y": isi_y + len(lines["legal-isi"]) * legal_leading + 4,
            "legal_size": 7,
            "legal_leading": legal_leading,
            "cta_x": width - 132,
            "cta_y": height - 42,
            "cta_width": 114,
            "cta_height": 26,
            "cta_size": 9,
        }
    legal_leading = 9.2
    isi_y = 345
    return {
        "spine_width": width,
        "spine_height": 88,
        "rule_height": 4,
        "endpoint_y": 84,
        "logo_x": 22,
        "logo_y": 25,
        "logo_width": width - 44,
        "logo_height": 40,
        "claim_x": 16,
        "headline_y": 124,
        "headline_size": 14,
        "headline_leading": 18,
        "subheadline_y": 124 + len(lines["headline"]) * 18 + 4,
        "subheadline_size": 8,
        "subheadline_leading": 11,
        "safety_x": 16,
        "safety_y": 230,
        "safety_size": 8.3,
        "safety_leading": 11,
        "legal_x": 16,
        "isi_y": isi_y,
        "pi_y": isi_y + len(lines["legal-isi"]) * legal_leading + 7,
        "legal_size": 7,
        "legal_leading": legal_leading,
        "cta_x": 16,
        "cta_y": height - 52,
        "cta_width": width - 32,
        "cta_height": 32,
        "cta_size": 9,
    }


def _build_banner_candidate(context: dict[str, Any], override: object = None) -> tuple[bytes, str, int, int, str]:
    """Build deterministic banner bytes in memory without touching campaign storage."""
    from .._render_validation import (
        RenderContractError,
        banner_layout,
        expected_roles,
        render_jinja,
        resolve_banner_dimensions,
    )

    if not isinstance(context["copy"].get("safety"), dict):
        raise RenderContractError("missing_banner_safety", "Banner copy must contain approved safety text.")
    canonical, width, height, profile = resolve_banner_dimensions(context["brief"], override)
    roles = expected_roles(context, "banner")
    layout = banner_layout(profile, width, height, roles, context["typography"])
    template_path = Path(str(files("open_pharma_plugins_campaign_studio") / "templates" / "banner.svg.j2"))
    source = template_path.read_text(encoding="utf-8")
    geometry = _geometry(profile, width, height, layout["lines"])
    svg = render_jinja(
        source,
        {
            "brand": str(context["brief"].get("brand", "")),
            "profile": profile,
            "width": width,
            "height": height,
            "palette": context["palette"],
            "typography": context["typography"],
            "logo_data_uri": context["logo"].data_uri,
            "headline_lines": layout["lines"]["headline"],
            "headline_lengths": layout["text_lengths"]["headline"],
            "subheadline_lines": layout["lines"].get("sub_headline", []),
            "subheadline_lengths": layout["text_lengths"].get("sub_headline", []),
            "safety_lines": layout["lines"]["safety"],
            "safety_lengths": layout["text_lengths"]["safety"],
            "isi_lines": layout["lines"]["legal-isi"],
            "isi_lengths": layout["text_lengths"]["legal-isi"],
            "pi_lines": layout["lines"]["legal-pi_ref"],
            "pi_lengths": layout["text_lengths"]["legal-pi_ref"],
            "cta_length": layout["text_lengths"]["cta"][0],
            "headline_full": roles["headline"],
            "subheadline_full": roles.get("sub_headline", ""),
            "safety_full": roles["safety"],
            "isi_full": roles["legal-isi"],
            "pi_full": roles["legal-pi_ref"],
            "cta": roles["cta"],
            **geometry,
        },
        custom=False,
    )
    return svg.encode("utf-8"), canonical, width, height, profile


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    from .._campaign_store import save_output
    from .._render_validation import (
        RenderContractError,
        error_response,
        inspect_svg,
        load_render_context,
        prohibited_errors,
    )

    campaign_brief_id = arguments.get("campaign_brief_id")
    try:
        context = load_render_context(campaign_brief_id, "banner")
        payload, canonical, width, height, profile = _build_banner_candidate(context, arguments.get("dimensions"))
        inspection = inspect_svg(payload, context, (width, height))
        errors = [*inspection["errors"], *prohibited_errors(inspection["visible_text"], context["brief"])]
        if errors:
            raise RenderContractError("rendered_banner_invalid", "; ".join(sorted(set(errors))))
        path = save_output(campaign_brief_id, "banner.svg", payload.decode("utf-8"))
        result = {
            "campaign_brief_id": campaign_brief_id,
            "channel": "banner",
            "file_path": str(path),
            "format": "svg",
            "editable": True,
            "dimensions": canonical,
            "profile": profile,
        }
    except Exception as exc:
        result = error_response(exc)
    return [{"type": "text", "text": json.dumps(result, ensure_ascii=False, sort_keys=True)}]
