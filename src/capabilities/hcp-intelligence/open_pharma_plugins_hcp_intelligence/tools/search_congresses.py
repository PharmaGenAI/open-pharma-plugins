"""search_congresses — web search for conference speaking roles and KOL signals."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

_CONGRESS_MAP: dict[str, list[str]] = {
    "oncology": ["ASCO", "ESMO", "AACR", "ASH"],
    "hematology": ["ASH", "EHA", "ASCO"],
    "cardiology": ["AHA", "ESC", "ACC", "HRS"],
    "endocrinology": ["ADA", "EASD", "ENDO"],
    "diabetes": ["ADA", "EASD", "ENDO"],
    "neurology": ["AAN", "EAN", "AES"],
    "respiratory": ["ATS", "ERS", "CHEST"],
    "pulmonology": ["ATS", "ERS", "CHEST"],
    "immunology": ["ACR", "EULAR", "AAI"],
    "rheumatology": ["ACR", "EULAR", "AAI"],
    "infectious disease": ["IDWeek", "ECCMID", "CROI"],
    "paediatrics": ["AAP", "ESPID", "EPA"],
    "pediatrics": ["AAP", "ESPID", "EPA"],
    "gastroenterology": ["DDW", "UEG", "AASLD"],
    "hepatology": ["AASLD", "EASL", "DDW"],
    "nephrology": ["ASN", "ERA", "WCN"],
    "dermatology": ["AAD", "EADV", "SID"],
    "ophthalmology": ["AAO", "ARVO", "EURETINA"],
    "urology": ["AUA", "EAU"],
    "psychiatry": ["APA", "EPA", "WPA"],
    "surgery": ["ACS", "EAES", "SAGES"],
}


def _resolve_congresses(
    specialty: str | None,
    therapeutic_area: str | None,
    explicit: list[str] | None,
) -> list[str]:
    if explicit:
        return explicit

    for key_source in (therapeutic_area, specialty):
        if not key_source:
            continue
        needle = key_source.strip().lower()
        for key, congresses in _CONGRESS_MAP.items():
            if key in needle or needle in key:
                return congresses

    if specialty:
        return [specialty.strip()]
    return ["medical congress"]


class SearchCongressesArgs(BaseModel):
    name: str = Field(description="Full name of the healthcare professional")
    specialty: str | None = Field(
        default=None,
        description="Medical specialty (used to select relevant congresses if congress_names is not provided)",
    )
    therapeutic_area: str | None = Field(
        default=None,
        description=("Therapeutic area to narrow congress search (e.g. 'oncology', 'cardiology', 'diabetes')"),
    )
    country: str | None = Field(default=None, description="Country of practice")
    congress_names: list[str] | None = Field(
        default=None,
        description=(
            "Specific congress names to search (e.g. ['ASCO', 'ESMO']). "
            "If not provided, the tool selects congresses based on specialty "
            "or therapeutic_area."
        ),
    )
    max_results: int = Field(
        default=15,
        ge=1,
        le=30,
        description="Maximum web results to return",
    )


TOOL: dict[str, Any] = {
    "name": "search_congresses",
    "description": (
        "Search for an HCP's conference speaking roles, poster presentations, "
        "and advisory board visibility at major medical congresses. Results "
        "signal KOL status: invited keynote, symposium speaker, oral "
        "presentation, poster presenter, session chair, or moderator. "
        "Provide specialty or therapeutic_area to auto-select relevant "
        "congresses, or pass explicit congress_names."
    ),
    "args": SearchCongressesArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    from datetime import datetime, timezone

    from .search_hcp_web import _backend_name, _web_search

    congresses = _resolve_congresses(
        arguments.get("specialty"),
        arguments.get("therapeutic_area"),
        arguments.get("congress_names"),
    )

    congress_clause = " OR ".join(congresses)
    role_clause = (
        "speaker OR lecture OR presentation OR poster OR symposium "
        "OR chair OR moderator OR plenary OR keynote OR abstract"
    )

    query_parts = [
        f'"{arguments["name"]}"',
        f"({congress_clause})",
        f"({role_clause})",
    ]
    if arguments.get("country"):
        query_parts.append(arguments["country"])

    query = " ".join(query_parts)
    max_results = arguments.get("max_results", 15)

    results = _web_search(query, max_results)

    output = {
        "query": query,
        "results": results,
        "congresses_searched": congresses,
        "search_backend": _backend_name(),
        "searched_at": datetime.now(timezone.utc).isoformat(),
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]
