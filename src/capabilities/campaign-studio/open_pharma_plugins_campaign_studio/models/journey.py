"""Audience journey models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class JourneyStage(BaseModel):
    stage: str = Field(description="unaware | aware | interested | convinced | acting | advocating")
    objective: str = Field(description="What should happen at this stage")
    key_messages: list[str] = Field(description="claim_id references")
    channels: list[str] = Field(description="Which channels serve this stage")
    content_type: str = Field(description="educational | promotional | reminder")
    kpi: str = Field(description="Measurable outcome for this stage")


class AudienceJourney(BaseModel):
    campaign_brief_id: str = Field(description="Links to the campaign brief")
    target_segment: str = Field(description="Target audience segment")
    stages: list[JourneyStage] = Field(description="Journey stages in order")
    generated_at: str = Field(description="ISO 8601 timestamp")
