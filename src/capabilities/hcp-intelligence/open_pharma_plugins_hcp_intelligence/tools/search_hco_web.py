"""search_hco_web — web search tailored for HCO profiling."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchHcoWebArgs(BaseModel):
    name: str = Field(description="Name of the healthcare organization")
    country: str | None = Field(default=None, description="Country")
    organization_type: str | None = Field(
        default=None,
        description="Type: hospital, clinic, research institute, medical school, etc.",
    )
    query_focus: str | None = Field(
        default=None,
        description=(
            "Optional focus to append to the search query, e.g. "
            "'departments centres of excellence', 'bed capacity annual report', "
            "'accreditation ranking', 'history founded'"
        ),
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=20,
        description="Maximum web results to return",
    )


TOOL: dict[str, Any] = {
    "name": "search_hco_web",
    "description": (
        "Search the web for information about a Healthcare Organization (hospital, "
        "clinic, research centre, medical school). Constructs a targeted query to find "
        "the organization's official site, Wikipedia entry, accreditation status, "
        "departments, bed capacity, and institutional history. Use query_focus to "
        "steer toward specific profile sections."
    ),
    "args": SearchHcoWebArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    from datetime import datetime, timezone

    query_parts = [f'"{arguments["name"]}"']
    if arguments.get("country"):
        query_parts.append(arguments["country"])
    if arguments.get("organization_type"):
        query_parts.append(arguments["organization_type"])
    if arguments.get("query_focus"):
        query_parts.append(arguments["query_focus"])
    else:
        query_parts.append("hospital OR medical centre OR healthcare")

    query = " ".join(query_parts)
    max_results = arguments.get("max_results", 10)

    from .search_hcp_web import _backend_name, _web_search

    results = _web_search(query, max_results)

    output = {
        "query": query,
        "results": results,
        "search_backend": _backend_name(),
        "searched_at": datetime.now(timezone.utc).isoformat(),
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]
