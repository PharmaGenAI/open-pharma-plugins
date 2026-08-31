"""search_orcid — query the ORCID public API for researcher profiles."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchOrcidArgs(BaseModel):
    name: str = Field(
        description="Full name of the researcher (e.g. 'Yvonne Lim')",
    )
    affiliation: str | None = Field(
        default=None,
        description="Known institution to help disambiguate (e.g. 'KK Women's and Children's Hospital')",
    )
    orcid_id: str | None = Field(
        default=None,
        description="If the ORCID ID is already known (e.g. '0000-0002-1234-5678'), fetch directly instead of searching",
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum candidate profiles to return when searching by name",
    )


TOOL: dict[str, Any] = {
    "name": "search_orcid",
    "description": (
        "Search the ORCID registry for a researcher's profile. ORCID provides "
        "author-curated, globally unique researcher identifiers with verified "
        "affiliations, education history, publication counts, and funding. Call "
        "early in the HCP profiling workflow — it disambiguates common names and "
        "fills education/affiliation gaps cheaply. Pass an orcid_id to fetch a "
        "known profile directly, or search by name and optional affiliation."
    ),
    "args": SearchOrcidArgs,
}


_BASE = "https://pub.orcid.org/v3.0"
_HEADERS = {"Accept": "application/json"}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    from datetime import datetime, timezone

    orcid_id = arguments.get("orcid_id")
    if orcid_id:
        profile = _fetch_profile(orcid_id)
        output = {
            "query": orcid_id,
            "total_found": 1 if profile else 0,
            "profiles": [profile] if profile else [],
            "source": "orcid",
            "searched_at": datetime.now(timezone.utc).isoformat(),
        }
        return [{"type": "text", "text": json.dumps(output, indent=2)}]

    name = arguments["name"]
    affiliation = arguments.get("affiliation")
    max_results = arguments.get("max_results", 5)

    query = _build_query(name, affiliation)
    orcid_ids = _search_ids(query, max_results)

    profiles = []
    for oid in orcid_ids:
        p = _fetch_profile(oid)
        if p:
            profiles.append(p)

    output = {
        "query": query,
        "total_found": len(orcid_ids),
        "profiles": profiles,
        "source": "orcid",
        "searched_at": datetime.now(timezone.utc).isoformat(),
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]


def _build_query(name: str, affiliation: str | None) -> str:
    parts = name.strip().split()
    if len(parts) >= 2:
        family = parts[-1]
        given = " ".join(parts[:-1])
        q = f"family-name:{family} AND given-names:{given}"
    else:
        q = f"family-name:{name}"

    if affiliation:
        q += f" AND affiliation-org-name:{affiliation}"
    return q


def _search_ids(query: str, max_results: int) -> list[str]:
    import json
    import urllib.parse
    import urllib.request

    url = f"{_BASE}/search/?q={urllib.parse.quote(query)}&rows={max_results}"
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception:
        return []

    results = data.get("result", []) or []
    ids = []
    for r in results:
        oid = r.get("orcid-identifier", {}).get("path")
        if oid:
            ids.append(oid)
    return ids


def _fetch_profile(orcid_id: str) -> dict | None:
    import json
    import urllib.request

    url = f"{_BASE}/{orcid_id}"
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None

    return _extract_profile(orcid_id, data)


def _extract_profile(orcid_id: str, data: dict) -> dict:
    person = data.get("person", {}) or {}
    activities = data.get("activities-summary", {}) or {}

    name_obj = person.get("name", {}) or {}
    given = (name_obj.get("given-names", {}) or {}).get("value", "")
    family = (name_obj.get("family-name", {}) or {}).get("value", "")

    bio_obj = person.get("biography", {}) or {}
    biography = bio_obj.get("content", "") or ""
    if len(biography) > 500:
        biography = biography[:497] + "..."

    education = _extract_affiliations(activities.get("educations", {}) or {}, "education-summary")
    employment = _extract_affiliations(activities.get("employments", {}) or {}, "employment-summary")

    work_groups = (activities.get("works", {}) or {}).get("group", []) or []
    funding_groups = (activities.get("fundings", {}) or {}).get("group", []) or []
    review_groups = (activities.get("peer-reviews", {}) or {}).get("group", []) or []

    return {
        "orcid_id": orcid_id,
        "given_names": given,
        "family_name": family,
        "biography": biography or None,
        "education": education,
        "employment": employment,
        "publication_count": len(work_groups),
        "funding_count": len(funding_groups),
        "peer_review_count": len(review_groups),
        "profile_url": f"https://orcid.org/{orcid_id}",
    }


def _extract_affiliations(section: dict, summary_key: str) -> list[dict]:
    groups = section.get("affiliation-group", []) or []
    results = []
    for group in groups:
        summaries = group.get("summaries", []) or []
        for entry in summaries:
            summary = entry.get(summary_key, {}) or {}
            org = (summary.get("organization", {}) or {}).get("name", "")
            role = (summary.get("role-title") or "") if summary_key == "employment-summary" else ""
            degree = (summary.get("role-title") or "") if summary_key == "education-summary" else ""
            dept = summary.get("department-name") or ""

            start = summary.get("start-date") or {}
            end = summary.get("end-date") or {}
            start_year = _year_from(start)
            end_year = _year_from(end)

            entry_dict: dict[str, Any] = {"institution": org}
            if summary_key == "education-summary":
                entry_dict["degree"] = degree
            else:
                entry_dict["role"] = role
            entry_dict["department"] = dept or None
            entry_dict["start_year"] = start_year
            entry_dict["end_year"] = end_year
            results.append(entry_dict)
    return results


def _year_from(date_obj: dict | None) -> int | None:
    if not date_obj:
        return None
    year = date_obj.get("year", {})
    if isinstance(year, dict):
        val = year.get("value")
    else:
        val = year
    if val:
        try:
            return int(val)
        except (ValueError, TypeError):
            pass
    return None
