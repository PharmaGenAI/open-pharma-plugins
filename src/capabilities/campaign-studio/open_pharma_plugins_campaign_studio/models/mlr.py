"""MLR review package model."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ._common import SourceReference
from .brief import CampaignBrief
from .validation import ClaimValidationReport


class RenderedAsset(BaseModel):
    channel: str = Field(description="email | banner | poster")
    file_path: str = Field(description="Path to the rendered file")
    format: str = Field(description="html | svg | pdf")
    editable: bool = Field(description="Whether the output is an editable source file")


class MlrPackage(BaseModel):
    campaign_brief_id: str = Field(description="Links to the campaign brief")
    brief: CampaignBrief
    rendered_assets: list[RenderedAsset] = Field(description="All rendered channel assets")
    claim_validation: ClaimValidationReport
    source_evidence: list[SourceReference] = Field(
        description="Deduplicated list of every source cited across all assets"
    )
    mlr_summary: str = Field(description="Markdown summary for reviewer cover sheet")
    reviewer_notes: str | None = Field(default=None)
    generated_at: str = Field(description="ISO 8601 timestamp")
