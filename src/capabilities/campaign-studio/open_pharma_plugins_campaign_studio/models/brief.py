"""Campaign brief models."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class CampaignBrief(BaseModel):
    campaign_brief_id: str = Field(description="Unique reusable identifier")
    campaign_name: str = Field(description="Short campaign name")

    # Jurisdiction & mode
    country: str = Field(description="ISO 3166-1 alpha-2 country code")
    policy_jurisdiction: str = Field(description="Regulatory jurisdiction: FDA | EMA | MHRA | HSA | PMDA | TGA")
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
        description="Path to approved claims JSON; required unless demo_mode is true",
    )
    demo_mode: bool = Field(default=False, description="Allow bundled fixtures only for explicit demonstration use")
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
        description="Path to brand kit directory; required unless demo_mode is true",
    )

    # Localisation
    language: str = Field(default="en")
    localisation_notes: str | None = Field(default=None)

    # Compliance
    required_safety_content: list[str] = Field(
        default_factory=list,
        description="ISI, boxed warning, PI reference",
    )
    required_legal_content: list[str] = Field(
        default_factory=list,
        description="Copyright, disclaimer, reporting statement",
    )

    # Delivery
    delivery_constraints: str | None = Field(default=None)
    approval_workflow: str = Field(
        default="mlr_standard",
        description="mlr_standard | mlr_expedited | medical_only",
    )

    generated_at: str = Field(description="ISO 8601 timestamp")

    @field_validator("call_to_action_url")
    @classmethod
    def call_to_action_url_is_https(cls, value: str) -> str:
        if not _valid_https_url(value):
            raise ValueError("must be an HTTPS URL with a valid hostname")
        return value


def _valid_https_url(value: str) -> bool:
    if not value or any(char.isspace() or ord(char) < 32 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or port is None
        and ":" in parsed.netloc.rsplit("@", 1)[-1]
        and not parsed.netloc.endswith("]")
    ):
        return False
    host = parsed.hostname
    return bool(
        host
        and len(host) <= 253
        and not host.startswith(".")
        and not host.endswith(".")
        and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in host.split("."))
    )
