"""ci_scan_news — search for competitor news and press releases."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from .._web_search import search_news
from ..models import NewsSearchRequest


class ScanNewsArgs(BaseModel):
    query: str = Field(description="Drug or company name to search for")
    days_back: int = Field(default=90, ge=1, le=365, description="Search window in days")
    max_results: int = Field(default=10, ge=1, le=30, description="Max results to return")


TOOL: dict[str, Any] = {
    "name": "ci_scan_news",
    "description": (
        "Search the web for recent competitor news, press releases, and "
        "conference presentations. Uses configured search backends "
        "(Serper/Tavily/Exa) and reports explicit source coverage."
    ),
    "args": ScanNewsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    user_request = NewsSearchRequest.model_validate(arguments)
    exact_query = f"{user_request.query} pharmaceutical news OR press release OR FDA OR clinical trial"
    provider_request = user_request.model_copy(update={"query": exact_query})
    result = search_news(provider_request)
    output = {
        "query": user_request.query,
        "exact_query": exact_query,
        "days_back": user_request.days_back,
        "total_results": len(result.records),
        "results": result.records,
        "coverage": result.status.value,
        "source_ledger": [entry.model_dump(mode="json") for entry in result.requests],
        "limitations": result.limitations,
    }
    if result.error:
        output["error"] = result.error.model_dump(mode="json")
    return [{"type": "text", "text": json.dumps(output, indent=2)}]
