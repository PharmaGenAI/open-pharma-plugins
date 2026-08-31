"""Shared types used across campaign-studio models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SourceReference(BaseModel):
    document_id: str = Field(description="Identifier of the approved source document")
    document_name: str = Field(description="Original filename")
    page_number: int | None = Field(default=None, description="1-indexed page or slide number, if applicable")
    excerpt: str = Field(description="Verbatim passage from the source")
