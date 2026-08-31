"""search_content — search across ingested documents for relevant passages."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchContentArgs(BaseModel):
    query: str = Field(description="Search query — keywords or phrases to find in the ingested documents")
    document_id: str | None = Field(
        default=None,
        description=(
            "Restrict search to a specific document by ID. Required for path-first workflows; "
            "omit only when the user deliberately requests a global search of the persistent store."
        ),
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=30,
        description="Maximum number of passages to return",
    )


TOOL: dict[str, Any] = {
    "name": "search_content",
    "description": (
        "Search across all ingested training documents for passages matching a "
        "query. Returns ranked results with document name, page/slide number, "
        "relevant text excerpt, and relevance score. Use this to find source "
        "material for specific topics when building learning packages, assessments, "
        "or preparing for role-play. In a path-first request, pass document_id and "
        "repeat the search for each submitted document; never use an unscoped search."
    ),
    "args": SearchContentArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from .._content_store import search_pages

    query = arguments["query"]
    document_id = arguments.get("document_id")
    max_results = arguments.get("max_results", 10)

    results = search_pages(query, document_id, max_results)

    output = {
        "query": query,
        "total_results": len(results),
        "results": results,
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]
