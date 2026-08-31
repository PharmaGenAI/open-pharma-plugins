"""Credential-safe Serper, Tavily, and Exa news-search adapters."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from pydantic import ValidationError

from ._cache import cache_lookup, cache_store
from ._transport import HttpRequest, HttpTransport, TransportError, UrllibTransport
from .models import (
    CacheProvenance,
    CoverageStatus,
    NewsItem,
    NewsSearchRequest,
    SearchBackend,
    SourceError,
    SourceName,
    SourceRequestEvidence,
    SourceResult,
)

SERPER_ENDPOINT = "https://google.serper.dev/search"
TAVILY_ENDPOINT = "https://api.tavily.com/search"
EXA_ENDPOINT = "https://api.exa.ai/search"
DEFAULT_TRANSPORT: HttpTransport = UrllibTransport()

_BACKEND_CONFIG = {
    SearchBackend.SERPER: ("SERPER_API_KEY", SERPER_ENDPOINT),
    SearchBackend.TAVILY: ("TAVILY_API_KEY", TAVILY_ENDPOINT),
    SearchBackend.EXA: ("EXA_API_KEY", EXA_ENDPOINT),
}


def search_news(
    request: NewsSearchRequest,
    *,
    transport: HttpTransport | None = None,
    now: datetime | None = None,
) -> SourceResult:
    """Search one configured backend and preserve failure/coverage semantics."""
    from shared.env import get_env

    retrieved_at = _utc(now)
    backend, api_key = _select_backend(get_env)
    if backend is None:
        configured = (get_env("OPEN_PHARMA_SEARCH_BACKEND", "auto") or "auto").lower()
        selected = configured if configured != SearchBackend.AUTO.value else "web_search"
        endpoint = _BACKEND_CONFIG.get(_backend_or_none(configured), ("", SERPER_ENDPOINT))[1]
        error = SourceError(
            code="not_configured",
            message=f"{selected} search backend is not configured",
        )
        evidence = SourceRequestEvidence(
            query=request.query,
            source_url=endpoint,
            retrieved_at=retrieved_at,
            cache=CacheProvenance(status="disabled"),
            status=CoverageStatus.NOT_CONFIGURED,
            record_count=0,
            error=error,
        )
        return SourceResult(
            source=SourceName.WEB,
            provider=None if selected == "web_search" else selected,
            status=CoverageStatus.NOT_CONFIGURED,
            query=request.query,
            source_url=endpoint,
            retrieved_at=retrieved_at,
            cache=CacheProvenance(status="disabled"),
            requests=[evidence],
            limitations=["No configured web-search provider was available."],
            error=error,
        )

    endpoint = _BACKEND_CONFIG[backend][1]
    cache_params = {
        "backend": backend.value,
        "query": request.query,
        "days_back": request.days_back,
        "max_results": request.max_results,
    }
    lookup = cache_lookup("web_search", cache_params)
    try:
        payload = lookup.payload
        if payload is None:
            wire_request = _build_request(backend, api_key, request, retrieved_at)
            response = (transport or DEFAULT_TRANSPORT).request(wire_request)
            payload = _json_mapping(response.body)
            records, dropped, total = _parse_results(backend, payload, request.max_results)
            cache_store("web_search", cache_params, payload)
        else:
            if not isinstance(payload, Mapping):
                raise ValueError("cached web-search payload must be a mapping")
            records, dropped, total = _parse_results(backend, payload, request.max_results)
    except TransportError as error:
        return _failure(
            request,
            backend,
            endpoint,
            retrieved_at,
            lookup.status,
            error.code,
            str(error),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError, TypeError):
        return _failure(
            request,
            backend,
            endpoint,
            retrieved_at,
            lookup.status,
            "schema_mismatch",
            f"{backend.value} response shape was invalid",
        )

    status = CoverageStatus.PARTIAL if dropped else CoverageStatus.COMPLETE
    error = SourceError(code="schema_mismatch", message="some web-search results had invalid URLs") if dropped else None
    evidence = SourceRequestEvidence(
        query=request.query,
        source_url=endpoint,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
        status=status,
        record_count=len(records),
        error=error,
    )
    limitations = _window_limitations(backend, request.days_back)
    if dropped:
        limitations.append(f"Dropped {dropped} result(s) with invalid or incomplete URLs.")
    return SourceResult(
        source=SourceName.WEB,
        provider=backend.value,
        status=status,
        query=request.query,
        source_url=endpoint,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
        records=records,
        total_available=total,
        requests=[evidence],
        limitations=limitations,
        error=error,
    )


def _select_backend(get_env: Callable[[str, str], str]) -> tuple[SearchBackend | None, str]:
    value = (get_env("OPEN_PHARMA_SEARCH_BACKEND", "auto") or "auto").lower()
    requested = _backend_or_none(value)
    if value != SearchBackend.AUTO.value:
        if requested is None:
            return None, ""
        key_name = _BACKEND_CONFIG[requested][0]
        key = get_env(key_name, "")
        return (requested, key) if key else (None, "")
    for backend in (SearchBackend.SERPER, SearchBackend.TAVILY, SearchBackend.EXA):
        key = get_env(_BACKEND_CONFIG[backend][0], "")
        if key:
            return backend, key
    return None, ""


def _backend_or_none(value: str) -> SearchBackend | None:
    try:
        backend = SearchBackend(value)
    except ValueError:
        return None
    return backend if backend != SearchBackend.AUTO else None


def _build_request(
    backend: SearchBackend,
    api_key: str,
    request: NewsSearchRequest,
    now: datetime,
) -> HttpRequest:
    endpoint = _BACKEND_CONFIG[backend][1]
    if backend == SearchBackend.SERPER:
        body: dict[str, Any] = {"q": request.query, "num": request.max_results}
        period = _serper_period(request.days_back)
        if period:
            body["tbs"] = period
        return HttpRequest(
            method="POST",
            url=endpoint,
            headers={"X-API-KEY": api_key},
            json_body=body,
        )
    if backend == SearchBackend.TAVILY:
        body = {
            "api_key": api_key,
            "query": request.query,
            "max_results": request.max_results,
            "include_answer": False,
            "time_range": _tavily_period(request.days_back),
        }
        return HttpRequest(method="POST", url=endpoint, json_body=body)
    start = (now - timedelta(days=request.days_back)).strftime("%Y-%m-%dT00:00:00Z")
    return HttpRequest(
        method="POST",
        url=endpoint,
        headers={"x-api-key": api_key},
        json_body={
            "query": request.query,
            "numResults": request.max_results,
            "type": "neural",
            "startPublishedDate": start,
            "contents": {"text": {"maxCharacters": 300}},
        },
    )


def _parse_results(
    backend: SearchBackend,
    payload: Mapping[str, Any],
    max_results: int,
) -> tuple[list[dict[str, Any]], int, int]:
    key = "organic" if backend == SearchBackend.SERPER else "results"
    items = payload.get(key, [])
    if not isinstance(items, list):
        raise ValueError(f"{key} must be a list")
    records: list[dict[str, Any]] = []
    dropped = 0
    for raw in items[:max_results]:
        if not isinstance(raw, Mapping):
            dropped += 1
            continue
        try:
            records.append(_parse_item(backend, raw).model_dump(mode="json"))
        except ValidationError:
            dropped += 1
    if dropped and not records:
        raise ValueError("web-search response contained no usable records")
    return records, dropped, min(len(items), max_results)


def _parse_item(backend: SearchBackend, item: Mapping[str, Any]) -> NewsItem:
    url = str(item.get("link" if backend == SearchBackend.SERPER else "url", ""))
    if backend == SearchBackend.TAVILY:
        snippet = str(item.get("content", ""))[:300]
        published = item.get("published_date")
    elif backend == SearchBackend.EXA:
        snippet = str(item.get("text", ""))[:300]
        published = item.get("publishedDate")
    else:
        snippet = str(item.get("snippet", ""))[:300]
        published = item.get("date")
    return NewsItem(
        title=str(item.get("title", "")),
        url=url,
        snippet=snippet,
        source=str(item.get("source") or urlsplit(url).netloc),
        published_date=str(published) if published else None,
    )


def _failure(
    request: NewsSearchRequest,
    backend: SearchBackend,
    endpoint: str,
    retrieved_at: datetime,
    cache_status,
    code: str,
    message: str,
) -> SourceResult:
    error = SourceError(code=code, message=message)
    evidence = SourceRequestEvidence(
        query=request.query,
        source_url=endpoint,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=cache_status),
        status=CoverageStatus.FAILED,
        record_count=0,
        error=error,
    )
    return SourceResult(
        source=SourceName.WEB,
        provider=backend.value,
        status=CoverageStatus.FAILED,
        query=request.query,
        source_url=endpoint,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=cache_status),
        requests=[evidence],
        limitations=["No trustworthy web-search response was obtained."],
        error=error,
    )


def _json_mapping(body: bytes) -> Mapping[str, Any]:
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("provider response must be a mapping")
    return payload


def _serper_period(days_back: int) -> str | None:
    if days_back <= 1:
        return "qdr:d"
    if days_back <= 7:
        return "qdr:w"
    if days_back <= 30:
        return "qdr:m"
    return "qdr:y" if days_back <= 365 else None


def _tavily_period(days_back: int) -> str:
    if days_back <= 1:
        return "day"
    if days_back <= 7:
        return "week"
    if days_back <= 30:
        return "month"
    return "year"


def _window_limitations(backend: SearchBackend, days_back: int) -> list[str]:
    if backend == SearchBackend.EXA:
        return []
    bucket = _serper_period(days_back) if backend == SearchBackend.SERPER else _tavily_period(days_back)
    return [f"{backend.value} applies coarse recency bucket {bucket!r} to the requested {days_back}-day window."]


def _utc(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
