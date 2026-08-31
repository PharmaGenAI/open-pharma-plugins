"""ci_status — configuration and data source status."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class StatusArgs(BaseModel):
    pass


TOOL: dict[str, Any] = {
    "name": "ci_status",
    "description": (
        "Show the current configuration and data source status for "
        "competitive intelligence: API key availability, cache stats, "
        "watchlist summary, and optional dependency status."
    ),
    "args": StatusArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from shared.env import get_env

    from .._cache import cache_stats
    from .._watchlist import WatchlistError, load_watchlist

    try:
        watchlist = load_watchlist()
    except WatchlistError as error:
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "error": {
                            "code": "invalid_watchlist",
                            "message": str(error),
                        }
                    }
                ),
            }
        ]

    has_pdfplumber = False
    try:
        import pypdfium2  # noqa: F401

        has_pdfplumber = True
    except ImportError:
        pass

    has_docx = False
    try:
        import docx  # noqa: F401

        has_docx = True
    except ImportError:
        pass

    output = {
        "config": {
            "OPENFDA_API_KEY": "set" if get_env("OPENFDA_API_KEY", "") else "not set",
            "NCBI_API_KEY": "set" if get_env("NCBI_API_KEY", "") else "not set",
            "SERPER_API_KEY": "set" if get_env("SERPER_API_KEY", "") else "not set",
            "TAVILY_API_KEY": "set" if get_env("TAVILY_API_KEY", "") else "not set",
            "EXA_API_KEY": "set" if get_env("EXA_API_KEY", "") else "not set",
            "CI_CACHE_TTL_HOURS": get_env("CI_CACHE_TTL_HOURS", "24"),
        },
        "data_sources": {
            "clinicaltrials_gov": {"url": "https://clinicaltrials.gov/api/v2", "auth": "none"},
            "openfda": {
                "url": "https://api.fda.gov/drug",
                "auth": "optional API key",
                "rate_limit": "240/min; 1,000/day per IP without key or 120,000/day per key",
            },
            "dailymed": {"url": "https://dailymed.nlm.nih.gov/dailymed/services/v2", "auth": "none"},
            "pubmed": {
                "url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
                "auth": "optional NCBI key",
                "rate_limit": "10/s with key, 3/s without",
            },
            "web_search": {
                "backends": "serper, tavily, exa",
                "available": bool(
                    get_env("SERPER_API_KEY", "") or get_env("TAVILY_API_KEY", "") or get_env("EXA_API_KEY", "")
                ),
            },
        },
        "cache": cache_stats(),
        "watchlist": {
            "total_entities": len(watchlist),
            "drugs": sum(1 for e in watchlist if e.get("entity_type") == "drug"),
            "companies": sum(1 for e in watchlist if e.get("entity_type") == "company"),
        },
        "optional_deps": {
            "pypdfium2": has_pdfplumber,
            "python_docx": has_docx,
        },
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]
