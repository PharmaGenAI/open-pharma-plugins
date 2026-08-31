"""Claim validation and policy check models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ClaimValidationResult(BaseModel):
    claim_id: str | None = Field(default=None, description="Matched approved claim ID, if any")
    statement: str = Field(description="The promotional statement as written in the copy")
    status: str = Field(description="approved | needs_review | rejected | not_found")
    matched_claim_text: str | None = Field(default=None, description="Verbatim approved claim it was matched to")
    similarity_score: float | None = Field(default=None, description="0.0-1.0 similarity to nearest approved claim")
    deviation: str | None = Field(
        default=None,
        description="How the copy deviates from approved language",
    )


class PolicyCheck(BaseModel):
    check_name: str = Field(description="e.g. fair_balance, isi_present, pi_link, prohibited_language")
    result: str = Field(description="pass | warn | fail")
    detail: str = Field(description="Explanation of the check outcome")


class ChannelComplianceResult(BaseModel):
    channel: str = Field(description="Channel independently checked")
    copy_exists: bool = Field(description="Whether channel copy was available")
    claims_checked: list[ClaimValidationResult] = Field(default_factory=list)
    policy_checks: list[PolicyCheck] = Field(default_factory=list)
    overall_pass: bool = Field(description="True only if this channel has no failures")


class ClaimValidationReport(BaseModel):
    campaign_brief_id: str = Field(description="Links to the campaign brief")
    channels_validated: list[str] = Field(description="Which channels were validated")
    claims_checked: list[ClaimValidationResult] = Field(description="Per-statement validation results")
    policy_checks: list[PolicyCheck] = Field(description="Jurisdiction-specific policy checks")
    channel_results: dict[str, ChannelComplianceResult] = Field(
        default_factory=dict,
        description="Independent compliance result for every requested channel",
    )
    policy_version: str = Field(default="unversioned", description="Illustrative policy bundle version")
    policy_hash: str = Field(default="", description="SHA-256 of the policy bundle used")
    overall_pass: bool = Field(description="True only if zero failures across all checks")
    generated_at: str = Field(description="ISO 8601 timestamp")
