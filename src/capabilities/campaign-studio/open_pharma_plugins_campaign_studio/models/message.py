"""Message architecture models."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ._common import SourceReference


class MessageTier(BaseModel):
    tier: str = Field(description="primary | secondary | supporting")
    message: str = Field(description="The message statement")
    claim_ids: list[str] = Field(min_length=1, description="Approved claim IDs this message draws from")
    audience_stage: str | None = Field(default=None, description="Links to a journey stage")
    rationale: str = Field(description="Why this message matters for the objective")

    @field_validator("claim_ids")
    @classmethod
    def claim_ids_are_unique_and_nonblank(cls, value: list[str]) -> list[str]:
        if any(not claim_id.strip() for claim_id in value):
            raise ValueError("claim_ids must not contain blank values")
        if len(value) != len(set(value)):
            raise ValueError("claim_ids must be unique within a message tier")
        return value


class MessageArchitecture(BaseModel):
    campaign_brief_id: str = Field(description="Links to the campaign brief")
    brand: str = Field(description="Brand name")
    indication: str = Field(description="Approved indication")
    message_tiers: list[MessageTier] = Field(description="Tiered message hierarchy")
    fair_balance_statement: str = Field(description="Required safety/risk language")
    fair_balance_sources: list[SourceReference] = Field(description="Sources for the fair balance statement")
    generated_at: str = Field(description="ISO 8601 timestamp")
