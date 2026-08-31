"""openFDA Drugs@FDA adapter with explicit evidence and identity fields."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlencode

from pydantic import ValidationError

from shared.filesystem import sanitize_url

from ._cache import cache_lookup, cache_store
from ._transport import HttpRequest, HttpTransport, TransportError, UrllibTransport
from .models import (
    CacheProvenance,
    CoverageStatus,
    RegulatoryEvent,
    RegulatorySearchRequest,
    SourceError,
    SourceName,
    SourceRequestEvidence,
    SourceResult,
)

_ENDPOINT = "https://api.fda.gov/drug/drugsfda.json"
_SAFE_QUERY_CHARS = '():[]"'
DEFAULT_TRANSPORT: HttpTransport = UrllibTransport()


def build_openfda_url(request: RegulatorySearchRequest, *, api_key: str) -> str:
    params = {"search": _build_query(request), "limit": str(request.max_results)}
    if api_key:
        params["api_key"] = api_key
    return f"{_ENDPOINT}?{urlencode(params, safe=_SAFE_QUERY_CHARS)}"


def search_openfda(
    request: RegulatorySearchRequest,
    *,
    transport: HttpTransport | None = None,
    now: datetime | None = None,
) -> SourceResult:
    from shared.env import get_env

    transport = transport or DEFAULT_TRANSPORT
    retrieved_at = _utc(now)
    api_key = get_env("OPENFDA_API_KEY", "")
    wire_url = build_openfda_url(request, api_key=api_key)
    evidence_url = sanitize_url(wire_url)
    cache_params = {
        "search": _build_query(request),
        "limit": request.max_results,
    }
    lookup = cache_lookup("openfda:drugsfda", cache_params)
    try:
        payload = lookup.payload
        if payload is None:
            response = transport.request(HttpRequest(method="GET", url=wire_url))
            payload = _json_mapping(response.body)
            cache_store("openfda:drugsfda", cache_params, payload)
        records, dropped = _parse_response(payload, evidence_url)
    except TransportError as error:
        return _failure(
            request=request,
            source_url=evidence_url,
            retrieved_at=retrieved_at,
            cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
            error=SourceError(code=error.code, message=str(error)),
        )
    except (ValueError, ValidationError, json.JSONDecodeError, UnicodeDecodeError):
        return _failure(
            request=request,
            source_url=evidence_url,
            retrieved_at=retrieved_at,
            cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
            error=SourceError(code="schema_mismatch", message="openFDA response shape was invalid"),
        )

    if dropped and not records:
        return _failure(
            request=request,
            source_url=evidence_url,
            retrieved_at=retrieved_at,
            cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
            error=SourceError(code="schema_mismatch", message="openFDA returned no valid records"),
        )
    status = CoverageStatus.PARTIAL if dropped else CoverageStatus.COMPLETE
    error = SourceError(code="schema_mismatch", message="some openFDA records were invalid") if dropped else None
    limitation = [f"Dropped {dropped} malformed openFDA record(s)."] if dropped else []
    request_evidence = SourceRequestEvidence(
        query=_build_query(request),
        source_url=evidence_url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
        status=status,
        record_count=len(records),
        error=error,
    )
    return SourceResult(
        source=SourceName.OPENFDA,
        provider="openfda",
        status=status,
        query=_build_query(request),
        source_url=evidence_url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
        records=records,
        requests=[request_evidence],
        limitations=limitation,
        error=error,
    )


def _build_query(request: RegulatorySearchRequest) -> str:
    names = []
    for value in [request.drug_name, *request.aliases]:
        clean = value.replace('"', "").replace("\\", "").strip()
        if clean and clean.casefold() not in {name.casefold() for name in names}:
            names.append(clean)
    name_groups = [f'(openfda.brand_name:"{name}" OR openfda.generic_name:"{name}")' for name in names]
    query = f"({' OR '.join(name_groups)})" if len(name_groups) > 1 else name_groups[0]
    if request.date_from or request.date_to:
        start = request.date_from.strftime("%Y%m%d") if request.date_from else "00010101"
        end = request.date_to.strftime("%Y%m%d") if request.date_to else "99991231"
        query += f" AND submissions.submission_status_date:[{start} TO {end}]"
    return query


def _parse_response(payload: Mapping[str, Any], source_url: str) -> tuple[list[dict[str, Any]], int]:
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("results must be a list")
    records = []
    dropped = 0
    for result in results:
        try:
            if not isinstance(result, Mapping):
                raise ValueError("result must be a mapping")
            openfda = result.get("openfda") or {}
            if not isinstance(openfda, Mapping):
                raise ValueError("openfda must be a mapping")
            submissions = result.get("submissions") or []
            if not isinstance(submissions, list):
                raise ValueError("submissions must be a list")
            for submission in submissions:
                if not isinstance(submission, Mapping):
                    raise ValueError("submission must be a mapping")
                event = RegulatoryEvent(
                    date=_submission_date(submission.get("submission_status_date")),
                    event_type=_classify_submission(str(submission.get("submission_type", ""))),
                    application_number=str(result.get("application_number", "")),
                    submission=(f"{submission.get('submission_type', '')}-{submission.get('submission_number', '')}"),
                    status=str(submission.get("submission_status", "")),
                    brand_name=_first(openfda.get("brand_name")),
                    generic_name=_first(openfda.get("generic_name")),
                    sponsor=str(result.get("sponsor_name", "")),
                    manufacturer_names=_strings(openfda.get("manufacturer_name")),
                    description=_submission_description(submission),
                    source_url=source_url,
                )
                records.append(event.model_dump(mode="json"))
        except (ValueError, ValidationError, TypeError):
            dropped += 1
    return records, dropped


def _classify_submission(submission_type: str) -> str:
    normalized = submission_type.upper()
    if normalized == "ORIG":
        return "approval"
    if normalized == "SUPPL":
        return "supplement"
    if normalized in {"EFFSUPL", "EFFICACY SUPPL"}:
        return "label_change"
    return "other"


def _submission_description(submission: Mapping[str, Any]) -> str:
    parts = []
    classification = str(submission.get("submission_class_code_description", ""))
    submission_type = str(submission.get("submission_type", ""))
    status = str(submission.get("submission_status", ""))
    if classification:
        parts.append(classification)
    elif submission_type:
        parts.append(submission_type)
    if status:
        parts.append(f"({status})")
    priority = str(submission.get("review_priority", ""))
    if priority:
        parts.append(f"[{priority}]")
    return " ".join(parts)


def _submission_date(value: Any) -> str | None:
    text = str(value or "")
    if len(text) != 8 or not text.isdigit():
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _first(value: Any) -> str:
    values = _strings(value)
    return values[0] if values else ""


def _failure(
    *,
    request: RegulatorySearchRequest,
    source_url: str,
    retrieved_at: datetime,
    cache: CacheProvenance,
    error: SourceError,
) -> SourceResult:
    evidence = SourceRequestEvidence(
        query=_build_query(request),
        source_url=source_url,
        retrieved_at=retrieved_at,
        cache=cache,
        status=CoverageStatus.FAILED,
        record_count=0,
        error=error,
    )
    return SourceResult(
        source=SourceName.OPENFDA,
        provider="openfda",
        status=CoverageStatus.FAILED,
        query=_build_query(request),
        source_url=source_url,
        retrieved_at=retrieved_at,
        cache=cache,
        requests=[evidence],
        limitations=["No trustworthy openFDA response was obtained."],
        error=error,
    )


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
