"""DailyMed SPL search and history adapters with explicit evidence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import quote, urlencode

from pydantic import ValidationError

from ._cache import cache_lookup, cache_store
from ._transport import HttpRequest, HttpTransport, TransportError, UrllibTransport
from .models import (
    CacheProvenance,
    CacheStatus,
    CoverageStatus,
    LabelHistoryEntry,
    RegulatorySearchRequest,
    SourceError,
    SourceName,
    SourceRequestEvidence,
    SourceResult,
    aggregate_cache_status,
    aggregate_coverage,
)

_BASE = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
DEFAULT_TRANSPORT: HttpTransport = UrllibTransport()


def search_dailymed(
    request: RegulatorySearchRequest,
    *,
    transport: HttpTransport | None = None,
    now: datetime | None = None,
) -> SourceResult:
    transport = transport or DEFAULT_TRANSPORT
    retrieved_at = _utc(now)
    names = _distinct_names(request)
    records: list[dict[str, Any]] = []
    requests: list[SourceRequestEvidence] = []
    cache_states: list[CacheStatus] = []
    limitations: list[str] = []
    for name in names:
        params = {"drug_name": name, "pagesize": min(request.max_results, 100), "page": 1}
        url = f"{_BASE}/spls.json?{urlencode(params)}"
        lookup = cache_lookup("dailymed:search", params)
        cache_states.append(lookup.status)
        try:
            payload = lookup.payload
            if payload is None:
                response = transport.request(HttpRequest(method="GET", url=url))
                payload = _json_mapping(response.body)
                cache_store("dailymed:search", params, payload)
            parsed = _parse_search(payload)
            records.extend(parsed)
            requests.append(
                SourceRequestEvidence(
                    query=name,
                    source_url=url,
                    retrieved_at=retrieved_at,
                    cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
                    status=CoverageStatus.COMPLETE,
                    record_count=len(parsed),
                )
            )
        except TransportError as error:
            requests.append(_failed_request(name, url, retrieved_at, lookup, error.code, str(error)))
            limitations.append(f"DailyMed search failed for {name}.")
        except (ValueError, ValidationError, json.JSONDecodeError, UnicodeDecodeError):
            requests.append(
                _failed_request(
                    name,
                    url,
                    retrieved_at,
                    lookup,
                    "schema_mismatch",
                    "DailyMed search shape was invalid",
                )
            )
            limitations.append(f"DailyMed search shape was invalid for {name}.")

    status = aggregate_coverage([evidence.status for evidence in requests])
    error = _aggregate_error(status, requests)
    deduped = {str(record["set_id"]): record for record in records if record.get("set_id")}
    return SourceResult(
        source=SourceName.DAILYMED,
        provider="dailymed",
        status=status,
        query=request.drug_name,
        source_url=f"{_BASE}/spls.json?{urlencode({'drug_name': request.drug_name})}",
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=aggregate_cache_status(cache_states)),
        records=list(deduped.values()) if status in {CoverageStatus.COMPLETE, CoverageStatus.PARTIAL} else [],
        total_available=len(deduped) if status in {CoverageStatus.COMPLETE, CoverageStatus.PARTIAL} else None,
        requests=requests,
        limitations=limitations,
        error=error,
    )


def get_dailymed_history(
    set_id: str,
    *,
    transport: HttpTransport | None = None,
    now: datetime | None = None,
) -> SourceResult:
    transport = transport or DEFAULT_TRANSPORT
    retrieved_at = _utc(now)
    encoded_set_id = quote(set_id, safe="")
    url = f"{_BASE}/spls/{encoded_set_id}/history.json"
    lookup = cache_lookup("dailymed:history", {"set_id": set_id})
    try:
        payload = lookup.payload
        if payload is None:
            response = transport.request(HttpRequest(method="GET", url=url))
            payload = _json_mapping(response.body)
            cache_store("dailymed:history", {"set_id": set_id}, payload)
        records = _parse_history(payload, source_url=url)
    except TransportError as error:
        return _history_failure(set_id, url, retrieved_at, lookup, error.code, str(error))
    except (ValueError, ValidationError, json.JSONDecodeError, UnicodeDecodeError):
        return _history_failure(
            set_id,
            url,
            retrieved_at,
            lookup,
            "schema_mismatch",
            "DailyMed history shape was invalid",
        )

    evidence = SourceRequestEvidence(
        query=set_id,
        source_url=url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
        status=CoverageStatus.COMPLETE,
        record_count=len(records),
    )
    return SourceResult(
        source=SourceName.DAILYMED,
        provider="dailymed",
        status=CoverageStatus.COMPLETE,
        query=set_id,
        source_url=url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
        records=records,
        total_available=len(records),
        requests=[evidence],
    )


def _parse_search(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("DailyMed data must be a list")
    records = []
    for item in data:
        if not isinstance(item, Mapping):
            raise ValueError("DailyMed SPL must be a mapping")
        set_id = str(item.get("setid", ""))
        if not set_id:
            raise ValueError("DailyMed SPL requires setid")
        records.append(
            {
                "set_id": set_id,
                "spl_version": str(item.get("spl_version", "")),
                "title": str(item.get("title", "")),
                "published_date": str(item.get("published_date", "")),
                "source_url": f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={quote(set_id)}",
            }
        )
    return records


def _parse_history(payload: Mapping[str, Any], *, source_url: str) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("DailyMed history data must be a mapping")
    history = data.get("history")
    spl = data.get("spl")
    if not isinstance(history, list) or not isinstance(spl, Mapping):
        raise ValueError("DailyMed history and spl containers are required")
    set_id = str(spl.get("setid", ""))
    title = str(spl.get("title", ""))
    if not set_id:
        raise ValueError("DailyMed SPL requires setid")
    return [
        LabelHistoryEntry(
            set_id=set_id,
            spl_version=str(item.get("spl_version", "")),
            published_date=str(item.get("published_date", "")),
            title=title,
            source_url=source_url,
        ).model_dump(mode="json")
        for item in history
        if isinstance(item, Mapping)
    ]


def _failed_request(name, url, retrieved_at, lookup, code, message) -> SourceRequestEvidence:
    return SourceRequestEvidence(
        query=name,
        source_url=url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
        status=CoverageStatus.FAILED,
        record_count=0,
        error=SourceError(code=code, message=message),
    )


def _aggregate_error(status: CoverageStatus, requests: list[SourceRequestEvidence]) -> SourceError | None:
    if status == CoverageStatus.PARTIAL:
        return SourceError(code="partial_coverage", message="one or more DailyMed searches failed")
    if status == CoverageStatus.FAILED:
        return next((request.error for request in requests if request.error is not None), None)
    return None


def _history_failure(set_id, url, retrieved_at, lookup, code, message) -> SourceResult:
    error = SourceError(code=code, message=message)
    evidence = _failed_request(set_id, url, retrieved_at, lookup, code, message)
    return SourceResult(
        source=SourceName.DAILYMED,
        provider="dailymed",
        status=CoverageStatus.FAILED,
        query=set_id,
        source_url=url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
        requests=[evidence],
        limitations=["No trustworthy DailyMed history response was obtained."],
        error=error,
    )


def _distinct_names(request: RegulatorySearchRequest) -> list[str]:
    names = []
    for value in [request.drug_name, *request.aliases]:
        normalized = value.strip()
        if normalized and normalized.casefold() not in {name.casefold() for name in names}:
            names.append(normalized)
    return names


def _json_mapping(body: bytes) -> Mapping[str, Any]:
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("provider response must be a mapping")
    return payload


def _utc(value: datetime | None) -> datetime:
    now = value or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)
