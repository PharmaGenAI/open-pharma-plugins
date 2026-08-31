"""Approved claim models."""

from __future__ import annotations

import unicodedata
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NonBlankString = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]
ClaimCategory = Literal["efficacy", "positioning", "moa", "safety", "tolerability", "dosing"]
CANONICAL_CLAIM_CATEGORIES: tuple[ClaimCategory, ...] = (
    "efficacy",
    "positioning",
    "moa",
    "safety",
    "tolerability",
    "dosing",
)
PROMOTIONAL_CLAIM_CATEGORIES = frozenset({"efficacy", "positioning", "moa"})
SAFETY_CLAIM_CATEGORIES = frozenset({"safety", "tolerability"})


def canonical_claim_category(value: object) -> str | None:
    """Return the canonical claim category without repairing unsafe whitespace."""
    if not isinstance(value, str) or value != value.strip():
        return None
    normalized = unicodedata.normalize("NFC", value).casefold()
    if normalized not in CANONICAL_CLAIM_CATEGORIES:
        return None
    return normalized


class ApprovedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: NonBlankString = Field(description="Stable identifier, e.g. 'c-001'")
    text: NonBlankString = Field(description="Verbatim approved wording")
    category: ClaimCategory = Field(description="efficacy | positioning | moa | safety | tolerability | dosing")
    source_document: NonBlankString = Field(description="Origin document name")
    source_reference: NonBlankString = Field(
        description="Precise location, e.g. 'Study 301, Table 2' or 'SmPC Section 5.1'"
    )
    approval_status: NonBlankString = Field(
        description="Approval state; only 'approved' is available for automated use"
    )
    effective_from: date | None = Field(default=None, description="Date the claim became effective, ISO 8601")
    expiry: date | None = Field(default=None, description="Claim approval expiry, ISO 8601")
    jurisdictions: list[NonBlankString] = Field(
        default_factory=list, description="Jurisdictions where the claim is approved"
    )
    indications: list[NonBlankString] = Field(default_factory=list, description="Approved indications")
    audiences: list[NonBlankString] = Field(default_factory=list, description="Permitted audiences")
    channels: list[NonBlankString] = Field(default_factory=list, description="Permitted channels")
    allowed_variants: list[NonBlankString] = Field(default_factory=list, description="Permitted claim variants")
    restrictions: str | None = Field(default=None, description="Usage restrictions, e.g. 'not for use in EU materials'")

    @field_validator("category", mode="before")
    @classmethod
    def category_is_canonical(cls, value: object) -> str:
        canonical = canonical_claim_category(value)
        if canonical is None:
            allowed = ", ".join(CANONICAL_CLAIM_CATEGORIES)
            raise ValueError(f"category must have no surrounding whitespace and be one of: {allowed}")
        return canonical
