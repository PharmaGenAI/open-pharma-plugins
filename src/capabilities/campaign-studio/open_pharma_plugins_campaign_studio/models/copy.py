"""Channel copy models — typed per channel for validation."""

from __future__ import annotations

from typing import Annotated
from unicodedata import category

from pydantic import BaseModel, ConfigDict, Field, field_validator

NonBlankCopyText = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]


def validate_visible_text(value: str) -> str:
    """Accept readable copy only; controls and format characters are unsafe in rendered text."""
    if any(category(char).startswith("C") for char in value):
        raise ValueError("text must not contain Unicode control or format characters")
    if not any(category(char)[0] in {"L", "N", "P", "S"} for char in value):
        raise ValueError("text must contain at least one visible graphic character")
    return value


class StrictCopyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CopyBlock(StrictCopyModel):
    text: NonBlankCopyText = Field(description="Copy text")
    claim_ids: list[str] = Field(
        default_factory=list,
        description="Approved claim IDs that ground this text",
    )

    @field_validator("text")
    @classmethod
    def text_is_visible(cls, value: str) -> str:
        return validate_visible_text(value)

    @field_validator("claim_ids")
    @classmethod
    def claim_ids_are_nonblank(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("claim_ids must not contain blank values")
        if len(value) != len(set(value)):
            raise ValueError("claim_ids must be unique within a CopyBlock")
        return value


class EmailCopy(StrictCopyModel):
    subject: CopyBlock = Field(description="Email subject line")
    preheader: CopyBlock = Field(description="Preview text")
    headline: CopyBlock = Field(description="Hero headline")
    body: list[CopyBlock] = Field(min_length=1, description="Body paragraphs")
    cta: CopyBlock = Field(description="Call-to-action button text")


class BannerCopy(StrictCopyModel):
    headline: CopyBlock = Field(description="Banner headline, max 8 words")
    sub_headline: CopyBlock | None = Field(default=None, description="Optional sub-headline")
    safety: CopyBlock | None = Field(
        default=None,
        description="Safety/risk copy; required by the copy tool for promotional efficacy banners",
    )
    cta: CopyBlock = Field(description="CTA text, max 3 words")


class PosterCopy(StrictCopyModel):
    headline: CopyBlock = Field(description="Poster headline")
    subhead: CopyBlock | None = Field(default=None)
    body: list[CopyBlock] = Field(min_length=1, description="Body paragraphs")
    bullet_points: list[CopyBlock] | None = Field(default=None, min_length=1)
    cta: CopyBlock = Field(description="Call-to-action text")
    footnotes: list[CopyBlock | NonBlankCopyText] | None = Field(
        default=None,
        min_length=1,
        description="Grounded copy blocks or verbatim approved legal text",
    )

    @field_validator("footnotes")
    @classmethod
    def footnotes_are_visible(cls, value: list[CopyBlock | str] | None) -> list[CopyBlock | str] | None:
        if value is not None:
            for footnote in value:
                if isinstance(footnote, str):
                    validate_visible_text(footnote)
        return value


class ChannelCopy(StrictCopyModel):
    campaign_brief_id: str = Field(description="Links to the campaign brief")
    channel: str = Field(description="email | banner | poster")
    copy_json: str = Field(description="JSON string of EmailCopy, BannerCopy, or PosterCopy")
    generated_at: str = Field(description="ISO 8601 timestamp")


class PersistedChannelCopy(StrictCopyModel):
    """On-disk copy envelope checked again before compliance is sealed."""

    campaign_brief_id: NonBlankCopyText
    channel: NonBlankCopyText
    copy_data: dict = Field(alias="copy")
    generated_at: NonBlankCopyText | None = None
