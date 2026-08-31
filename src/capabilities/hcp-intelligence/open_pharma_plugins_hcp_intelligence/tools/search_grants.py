"""search_grants — query NIH RePORTER for research grant funding, with web fallback."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchGrantsArgs(BaseModel):
    pi_name: str = Field(description="Full name of the principal investigator (e.g. 'Yvonne Lim')")
    institution: str | None = Field(
        default=None,
        description="Institution or organization name to narrow results",
    )
    keywords: str | None = Field(
        default=None,
        description="Research topic keywords (e.g. 'immunotherapy melanoma')",
    )
    country: str | None = Field(
        default=None,
        description="Country of the PI (used for web fallback when NIH RePORTER returns no results)",
    )
    active_only: bool = Field(
        default=False,
        description="If true, only return currently active grants",
    )
    max_results: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum grants to return",
    )


TOOL: dict[str, Any] = {
    "name": "search_grants",
    "description": (
        "Search for research grant funding awarded to a principal investigator. "
        "Queries NIH RePORTER (covers NIH, NSF, and other US federal agencies) and "
        "falls back to web search for non-US funding bodies. Returns grant number, "
        "title, PI, institution, award amount, dates, activity status, and agency. "
        "Active funding signals an active researcher; amounts indicate research "
        "program scale; co-PIs reveal collaboration networks."
    ),
    "args": SearchGrantsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    from datetime import datetime, timezone

    pi_name = arguments["pi_name"]
    institution = arguments.get("institution")
    keywords = arguments.get("keywords")
    country = arguments.get("country")
    active_only = arguments.get("active_only", False)
    max_results = arguments.get("max_results", 20)

    grants, total_count, source = _search_nih_reporter(pi_name, institution, keywords, active_only, max_results)

    web_fallback_results = None
    if total_count == 0 and country and country.lower() not in ("us", "usa", "united states"):
        web_fallback_results = _web_fallback(pi_name, institution, country)
        source = "web_fallback"

    query_desc = f"PI: {pi_name}"
    if institution:
        query_desc += f", institution: {institution}"
    if keywords:
        query_desc += f", keywords: {keywords}"
    if active_only:
        query_desc += " (active only)"

    output: dict[str, Any] = {
        "query": query_desc,
        "total_count": total_count,
        "grants": grants,
        "source": source,
        "searched_at": datetime.now(timezone.utc).isoformat(),
    }
    if web_fallback_results is not None:
        output["web_fallback_results"] = web_fallback_results

    return [{"type": "text", "text": json.dumps(output, indent=2)}]


def _search_nih_reporter(
    pi_name: str,
    institution: str | None,
    keywords: str | None,
    active_only: bool,
    max_results: int,
) -> tuple[list[dict], int, str]:
    import json
    import urllib.request

    criteria: dict[str, Any] = {
        "pi_names": [{"any_name": pi_name}],
    }
    if institution:
        criteria["org_names"] = [institution]
    if active_only:
        criteria["is_active"] = True
    if keywords:
        criteria["advanced_text_search"] = {
            "operator": "and",
            "search_field": "terms",
            "search_text": keywords,
        }

    body = json.dumps(
        {
            "criteria": criteria,
            "offset": 0,
            "limit": max_results,
            "sort_field": "project_start_date",
            "sort_order": "desc",
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.reporter.nih.gov/v2/projects/search",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception:
        return [], 0, "nih_reporter"

    total_count = data.get("meta", {}).get("total", 0)
    results = data.get("results", [])

    grants = []
    for r in results:
        pis = r.get("principal_investigators", [])
        pi_full_name = pis[0].get("full_name", "") if pis else ""

        org = r.get("organization", {})
        org_name = org.get("org_name", "")

        abstract = r.get("abstract_text") or ""
        if len(abstract) > 500:
            abstract = abstract[:497] + "..."

        app_id = r.get("appl_id", "")

        grants.append(
            {
                "project_number": r.get("project_num", ""),
                "title": r.get("project_title", ""),
                "abstract": abstract,
                "pi_name": pi_full_name,
                "institution": org_name,
                "award_amount": r.get("award_amount"),
                "start_date": r.get("project_start_date"),
                "end_date": r.get("project_end_date"),
                "agency_code": r.get("agency_code", ""),
                "is_active": r.get("is_active", False),
                "source_url": f"https://reporter.nih.gov/project-details/{app_id}" if app_id else "",
            }
        )

    return grants, total_count, "nih_reporter"


def _web_fallback(pi_name: str, institution: str | None, country: str | None) -> list[dict]:
    from .search_hcp_web import _web_search

    parts = [f'"{pi_name}"', "(research grant OR funding OR funded by)"]
    if institution:
        parts.append(institution)
    if country:
        parts.append(country)
    query = " ".join(parts)

    return _web_search(query, 10)
