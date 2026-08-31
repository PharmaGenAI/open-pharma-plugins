"""search_hcp_web — web search tailored for HCP profiling."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchHcpWebArgs(BaseModel):
    name: str = Field(description="Full name of the healthcare professional")
    specialty: str | None = Field(default=None, description="Medical specialty (e.g. 'Cardiology')")
    country: str | None = Field(default=None, description="Country of practice")
    institution: str | None = Field(default=None, description="Known institution or affiliation")
    query_focus: str | None = Field(
        default=None,
        description=(
            "Optional focus to append to the search query, e.g. "
            "'biography', 'society membership', 'education qualifications', "
            "'advisory board committee'"
        ),
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=20,
        description="Maximum web results to return",
    )


TOOL: dict[str, Any] = {
    "name": "search_hcp_web",
    "description": (
        "Search the web for information about a specific Healthcare Professional. "
        "Constructs a targeted query from the HCP's name, specialty, country, and "
        "institution to find institutional profiles, society memberships, conference "
        "appearances, and biographical information. Returns URLs, titles, and snippets. "
        "Use query_focus to steer toward specific profile sections (e.g. 'education', "
        "'advisory board')."
    ),
    "args": SearchHcpWebArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    from datetime import datetime, timezone

    query_parts = [f'"{arguments["name"]}"']
    if arguments.get("specialty"):
        query_parts.append(arguments["specialty"])
    if arguments.get("country"):
        query_parts.append(arguments["country"])
    if arguments.get("institution"):
        query_parts.append(arguments["institution"])
    if arguments.get("query_focus"):
        query_parts.append(arguments["query_focus"])
    else:
        query_parts.append("doctor OR physician OR professor OR consultant")

    query = " ".join(query_parts)
    max_results = arguments.get("max_results", 10)

    results = _web_search(query, max_results)

    output = {
        "query": query,
        "results": results,
        "search_backend": _backend_name(),
        "searched_at": datetime.now(timezone.utc).isoformat(),
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]


def _backend_name() -> str:
    from shared.env import get_env

    backend = get_env("OPEN_PHARMA_SEARCH_BACKEND", "auto").strip().lower()
    if backend != "auto":
        return backend
    if get_env("SERPER_API_KEY", ""):
        return "serper"
    if get_env("TAVILY_API_KEY", ""):
        return "tavily"
    if get_env("EXA_API_KEY", ""):
        return "exa"
    return "none"


def _web_search(query: str, max_results: int) -> list[dict]:
    backend = _backend_name()
    if backend == "serper":
        return _serper_search(query, max_results)
    if backend == "tavily":
        return _tavily_search(query, max_results)
    if backend == "exa":
        return _exa_search(query, max_results)
    raise RuntimeError("No web search backend configured. Set SERPER_API_KEY, TAVILY_API_KEY, or EXA_API_KEY.")


def _serper_search(query: str, max_results: int) -> list[dict]:
    import json
    import urllib.request

    from shared.env import get_env

    body = json.dumps({"q": query, "num": max_results}).encode()
    req = urllib.request.Request(
        "https://google.serper.dev/search",
        data=body,
        headers={
            "X-API-KEY": get_env("SERPER_API_KEY", ""),
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    return [
        {
            "url": r.get("link", ""),
            "title": r.get("title", ""),
            "snippet": r.get("snippet", ""),
            "published_date": r.get("date"),
            "domain": r.get("link", "").split("/")[2] if "/" in r.get("link", "") else None,
        }
        for r in data.get("organic", [])
    ]


def _tavily_search(query: str, max_results: int) -> list[dict]:
    import json
    import urllib.request

    from shared.env import get_env

    body = json.dumps(
        {
            "api_key": get_env("TAVILY_API_KEY", ""),
            "query": query,
            "max_results": max_results,
            "search_depth": "advanced",
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    return [
        {
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "snippet": r.get("content", ""),
            "published_date": r.get("published_date"),
            "domain": r.get("url", "").split("/")[2] if "/" in r.get("url", "") else None,
        }
        for r in data.get("results", [])
    ]


def _exa_search(query: str, max_results: int) -> list[dict]:
    import json
    import urllib.request

    from shared.env import get_env

    body = json.dumps(
        {
            "query": query,
            "numResults": max_results,
            "useAutoprompt": True,
            "contents": {"text": {"maxCharacters": 500}},
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.exa.ai/search",
        data=body,
        headers={
            "x-api-key": get_env("EXA_API_KEY", ""),
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    return [
        {
            "url": r.get("url", ""),
            "title": r.get("title", ""),
            "snippet": r.get("text", ""),
            "published_date": r.get("publishedDate"),
            "domain": r.get("url", "").split("/")[2] if "/" in r.get("url", "") else None,
        }
        for r in data.get("results", [])
    ]
