"""ci_scan_publications — search PubMed for competitor clinical results."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from .._pubmed import search_publications
from ..models import PublicationSearchRequest


class ScanPublicationsArgs(BaseModel):
    query: str = Field(description="Drug name or topic to search PubMed for")
    days_back: int = Field(default=365, ge=1, le=1825, description="Search window in days")
    max_results: int = Field(default=10, ge=1, le=30, description="Max publications to return")


TOOL: dict[str, Any] = {
    "name": "ci_scan_publications",
    "description": (
        "Search PubMed for recent publications about a competitor drug or "
        "therapeutic area. Returns structured publication data with title, "
        "authors, journal, date, abstract excerpt, and explicit source coverage."
    ),
    "args": ScanPublicationsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    request = PublicationSearchRequest.model_validate(arguments)
    result = search_publications(request)
    output = {
        "query": request.query,
        "exact_query": result.query,
        "total_found": result.total_available,
        "returned": len(result.records),
        "publications": result.records,
        "coverage": result.status.value,
        "source_ledger": [entry.model_dump(mode="json") for entry in result.requests],
        "limitations": result.limitations,
    }
    if result.error:
        output["error"] = result.error.model_dump(mode="json")
    return [{"type": "text", "text": json.dumps(output, indent=2)}]
