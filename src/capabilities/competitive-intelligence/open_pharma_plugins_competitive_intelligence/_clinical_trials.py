"""ClinicalTrials.gov API-v2 provider with explicit source evidence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlencode

from pydantic import ValidationError

from ._cache import cache_lookup, cache_store
from ._transport import HttpRequest, HttpTransport, TransportError, UrllibTransport
from .models import (
    CacheProvenance,
    CacheStatus,
    CoverageStatus,
    SourceError,
    SourceName,
    SourceRequestEvidence,
    SourceResult,
    Trial,
    TrialArm,
    TrialDetail,
    TrialDetailRequest,
    TrialIntervention,
    TrialResultsSummary,
    TrialSearchRequest,
    aggregate_cache_status,
)

_BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
_FIELDS = "|".join(
    [
        "NCTId",
        "BriefTitle",
        "OverallStatus",
        "Phase",
        "LeadSponsorName",
        "CollaboratorName",
        "Condition",
        "InterventionName",
        "InterventionType",
        "EnrollmentCount",
        "StartDate",
        "PrimaryCompletionDate",
        "CompletionDate",
        "StudyType",
        "PrimaryOutcomeMeasure",
        "HasResults",
    ]
)

DEFAULT_TRANSPORT: HttpTransport = UrllibTransport()


def build_search_url(request: TrialSearchRequest, *, page_token: str | None = None) -> str:
    params: dict[str, str] = {
        "query.term": request.query,
        "fields": _FIELDS,
        "pageSize": str(request.max_results),
        "format": "json",
        "countTotal": "true",
    }
    if request.phase:
        params["filter.advanced"] = f"AREA[Phase]{request.phase}"
    if request.status:
        params["filter.overallStatus"] = request.status
    if page_token:
        params["pageToken"] = page_token
    return f"{_BASE_URL}?{urlencode(params)}"


def search_trials(
    request: TrialSearchRequest,
    *,
    transport: HttpTransport | None = None,
    now: datetime | None = None,
) -> SourceResult:
    transport = transport or DEFAULT_TRANSPORT
    retrieved_at = _utc(now)
    records: list[dict[str, Any]] = []
    requests: list[SourceRequestEvidence] = []
    cache_states: list[CacheStatus] = []
    limitations: list[str] = []
    total_available: int | None = None
    page_token: str | None = None

    while len(records) < request.max_results:
        url = build_search_url(request, page_token=page_token)
        cache_params = _search_cache_params(request, page_token)
        lookup = cache_lookup("clinicaltrials:search", cache_params)
        cache_states.append(lookup.status)
        try:
            payload = lookup.payload
            if payload is None:
                response = transport.request(HttpRequest(method="GET", url=url))
                payload = _json_mapping(response.body)
                cache_store("clinicaltrials:search", cache_params, payload)
            page_records, next_token, page_total, dropped = _parse_search_page(payload)
        except TransportError as error:
            return _search_failure(
                request=request,
                url=url,
                retrieved_at=retrieved_at,
                records=records,
                requests=requests,
                cache_states=cache_states,
                total_available=total_available,
                code="pagination_failed" if records else error.code,
                message="later ClinicalTrials.gov page failed" if records else str(error),
            )
        except (ValueError, ValidationError, json.JSONDecodeError, UnicodeDecodeError):
            return _search_failure(
                request=request,
                url=url,
                retrieved_at=retrieved_at,
                records=records,
                requests=requests,
                cache_states=cache_states,
                total_available=total_available,
                code="schema_mismatch",
                message="ClinicalTrials.gov response shape was invalid",
            )

        if total_available is None:
            if page_total is None:
                return _search_failure(
                    request=request,
                    url=url,
                    retrieved_at=retrieved_at,
                    records=records,
                    requests=requests,
                    cache_states=cache_states,
                    total_available=None,
                    code="schema_mismatch",
                    message="ClinicalTrials.gov total count was missing",
                )
            total_available = page_total

        if dropped and not page_records:
            return _search_failure(
                request=request,
                url=url,
                retrieved_at=retrieved_at,
                records=records,
                requests=requests,
                cache_states=cache_states,
                total_available=total_available,
                code="schema_mismatch",
                message="ClinicalTrials.gov page contained no usable records",
            )

        request_status = CoverageStatus.PARTIAL if dropped else CoverageStatus.COMPLETE
        request_error = (
            SourceError(code="schema_mismatch", message="some ClinicalTrials.gov records were invalid")
            if dropped
            else None
        )
        requests.append(
            SourceRequestEvidence(
                query=request.query,
                source_url=url,
                retrieved_at=retrieved_at,
                cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
                status=request_status,
                record_count=len(page_records),
                error=request_error,
            )
        )
        if dropped:
            limitations.append(f"Dropped {dropped} malformed ClinicalTrials.gov record(s).")
        remaining = request.max_results - len(records)
        records.extend(page_records[:remaining])
        page_token = next_token
        if not page_token:
            break

    truncated = total_available is not None and total_available > len(records)
    if truncated:
        limitations.append(
            f"Returned {len(records)} of {total_available} matching trials because max_results bounded collection."
        )
    status = (
        CoverageStatus.PARTIAL
        if truncated or any(r.status == CoverageStatus.PARTIAL for r in requests)
        else CoverageStatus.COMPLETE
    )
    error = (
        SourceError(code="truncated", message="ClinicalTrials.gov results were bounded or partially invalid")
        if status == CoverageStatus.PARTIAL
        else None
    )
    return SourceResult(
        source=SourceName.CLINICAL_TRIALS,
        provider="clinicaltrials.gov",
        status=status,
        query=request.query,
        source_url=build_search_url(request),
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=aggregate_cache_status(cache_states)),
        records=records,
        total_available=total_available,
        requests=requests,
        limitations=limitations,
        error=error,
    )


def get_trial_detail(
    request: TrialDetailRequest,
    *,
    transport: HttpTransport | None = None,
    now: datetime | None = None,
) -> SourceResult:
    transport = transport or DEFAULT_TRANSPORT
    retrieved_at = _utc(now)
    url = f"{_BASE_URL}/{request.nct_id}"
    lookup = cache_lookup("clinicaltrials:detail", {"nct_id": request.nct_id})
    try:
        payload = lookup.payload
        if payload is None:
            response = transport.request(HttpRequest(method="GET", url=url))
            payload = _json_mapping(response.body)
            cache_store("clinicaltrials:detail", {"nct_id": request.nct_id}, payload)
        detail = _parse_trial_detail(payload)
    except TransportError as error:
        return _single_failure(
            source_url=url,
            query=request.nct_id,
            retrieved_at=retrieved_at,
            cache_status=lookup.status,
            error=SourceError(code=error.code, message=str(error)),
        )
    except (ValueError, ValidationError, json.JSONDecodeError, UnicodeDecodeError):
        return _single_failure(
            source_url=url,
            query=request.nct_id,
            retrieved_at=retrieved_at,
            cache_status=lookup.status,
            error=SourceError(code="schema_mismatch", message="ClinicalTrials.gov detail shape was invalid"),
        )

    limitation = "The study endpoint exposes current status and milestone dates, not record-version history."
    return SourceResult(
        source=SourceName.CLINICAL_TRIALS,
        provider="clinicaltrials.gov",
        status=CoverageStatus.COMPLETE,
        query=request.nct_id,
        source_url=url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
        records=[detail.model_dump(mode="json")],
        total_available=1,
        requests=[
            SourceRequestEvidence(
                query=request.nct_id,
                source_url=url,
                retrieved_at=retrieved_at,
                cache=CacheProvenance(status=lookup.status, cached_at=lookup.cached_at),
                status=CoverageStatus.COMPLETE,
                record_count=1,
            )
        ],
        limitations=[limitation],
    )


def _parse_search_page(payload: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str | None, int | None, int]:
    studies = payload.get("studies")
    if not isinstance(studies, list):
        raise ValueError("studies must be a list")
    total = payload.get("totalCount")
    if total is not None and (not isinstance(total, int) or total < 0):
        raise ValueError("totalCount must be a non-negative integer")
    next_token = payload.get("nextPageToken")
    if next_token is not None and not isinstance(next_token, str):
        raise ValueError("nextPageToken must be a string")
    records: list[dict[str, Any]] = []
    dropped = 0
    for study in studies:
        try:
            records.append(_parse_trial(study).model_dump(mode="json"))
        except (ValueError, ValidationError, TypeError, AttributeError):
            dropped += 1
    return records, next_token, total, dropped


def _parse_trial(study: Any, *, detail: bool = False) -> Trial:
    if not isinstance(study, Mapping) or not isinstance(study.get("hasResults"), bool):
        raise ValueError("study and hasResults are required")
    protocol = _mapping(study, "protocolSection")
    identification = _mapping(protocol, "identificationModule")
    status_module = _mapping(protocol, "statusModule")
    sponsors = _mapping(protocol, "sponsorCollaboratorsModule")
    design = _mapping(protocol, "designModule")
    conditions = _mapping(protocol, "conditionsModule")
    arms = _optional_mapping(protocol, "armsInterventionsModule")
    outcomes = protocol.get("outcomesModule") or {}
    if not isinstance(outcomes, Mapping):
        raise ValueError("outcomesModule must be a mapping")
    nct_id = _string(identification, "nctId")
    title_key = "officialTitle" if detail and identification.get("officialTitle") else "briefTitle"
    lead_sponsor = _mapping(sponsors, "leadSponsor")
    collaborators = [
        str(item.get("name", ""))
        for item in _list(sponsors, "collaborators", default=[])
        if isinstance(item, Mapping) and item.get("name")
    ]
    interventions = [
        TrialIntervention(
            name=_string(item, "name"),
            intervention_type=_string(item, "type"),
            description=str(item.get("description", "")),
            other_names=[str(name) for name in item.get("otherNames", []) if isinstance(name, str)],
        )
        for item in _list(arms, "interventions", default=[])
        if isinstance(item, Mapping)
    ]
    primary_outcomes = [
        str(item.get("measure", ""))
        for item in _list(outcomes, "primaryOutcomes", default=[])
        if isinstance(item, Mapping) and item.get("measure")
    ]
    phases = [str(value) for value in _list(design, "phases", default=[]) if isinstance(value, str)]
    return Trial(
        nct_id=nct_id,
        title=_string(identification, title_key),
        sponsor=_string(lead_sponsor, "name"),
        collaborators=collaborators,
        phase=", ".join(phases),
        status=_string(status_module, "overallStatus"),
        conditions=[str(value) for value in _list(conditions, "conditions", default=[]) if isinstance(value, str)],
        interventions=interventions,
        enrollment=_optional_int(_mapping(design, "enrollmentInfo").get("count")),
        start_date=_date_value(status_module, "startDateStruct"),
        primary_completion_date=_date_value(status_module, "primaryCompletionDateStruct"),
        estimated_completion_date=_date_value(status_module, "completionDateStruct"),
        study_type=str(design.get("studyType", "INTERVENTIONAL")),
        primary_endpoints=primary_outcomes,
        has_results=study["hasResults"],
        source_url=f"https://clinicaltrials.gov/study/{nct_id}",
    )


def _parse_trial_detail(payload: Mapping[str, Any]) -> TrialDetail:
    trial = _parse_trial(payload, detail=True)
    protocol = _mapping(payload, "protocolSection")
    arms_module = _optional_mapping(protocol, "armsInterventionsModule")
    outcomes = protocol.get("outcomesModule") or {}
    if not isinstance(outcomes, Mapping):
        raise ValueError("outcomesModule must be a mapping")
    arms = [
        TrialArm(
            label=_string(item, "label"),
            type=_string(item, "type"),
            description=str(item.get("description", "")),
            interventions=[str(name) for name in item.get("interventionNames", []) if isinstance(name, str)],
        )
        for item in _list(arms_module, "armGroups", default=[])
        if isinstance(item, Mapping)
    ]
    secondary = [
        str(item.get("measure", ""))
        for item in _list(outcomes, "secondaryOutcomes", default=[])
        if isinstance(item, Mapping) and item.get("measure")
    ]
    status_module = _mapping(protocol, "statusModule")
    milestones = []
    for label, key in (
        ("Study Start", "startDateStruct"),
        ("Primary Completion", "primaryCompletionDateStruct"),
        ("Study Completion", "completionDateStruct"),
    ):
        value = status_module.get(key)
        if isinstance(value, Mapping) and value.get("date"):
            milestones.append({"milestone": label, "date": str(value["date"]), "type": str(value.get("type", ""))})
    results = payload.get("resultsSection")
    results_summary = None
    if results is not None:
        if not isinstance(results, Mapping):
            raise ValueError("resultsSection must be a mapping")
        measures = results.get("outcomeMeasuresModule") or {}
        if not isinstance(measures, Mapping):
            raise ValueError("outcomeMeasuresModule must be a mapping")
        outcome_measures = measures.get("outcomeMeasures", [])
        if not isinstance(outcome_measures, list):
            raise ValueError("outcomeMeasures must be a list")
        results_summary = TrialResultsSummary(
            has_results=True,
            primary_outcomes_count=len(outcome_measures),
            adverse_events_reported=isinstance(results.get("adverseEventsModule"), Mapping),
        )
    references_module = protocol.get("referencesModule") or {}
    if not isinstance(references_module, Mapping):
        raise ValueError("referencesModule must be a mapping")
    publications = [
        str(item.get("pmid"))
        for item in _list(references_module, "references", default=[])
        if isinstance(item, Mapping) and item.get("pmid")
    ]
    eligibility_module = protocol.get("eligibilityModule") or {}
    if not isinstance(eligibility_module, Mapping):
        raise ValueError("eligibilityModule must be a mapping")
    eligibility = str(eligibility_module.get("eligibilityCriteria", "")) or None
    if eligibility and len(eligibility) > 1000:
        eligibility = eligibility[:1000] + "..."
    return TrialDetail(
        trial=trial,
        arms=arms,
        secondary_endpoints=secondary,
        eligibility_criteria=eligibility,
        status_history=[],
        milestone_dates=milestones,
        results_summary=results_summary,
        publications=publications,
    )


def _search_failure(
    *,
    request: TrialSearchRequest,
    url: str,
    retrieved_at: datetime,
    records: list[dict[str, Any]],
    requests: list[SourceRequestEvidence],
    cache_states: list[CacheStatus],
    total_available: int | None,
    code: str,
    message: str,
) -> SourceResult:
    error = SourceError(code=code, message=message)
    requests.append(
        SourceRequestEvidence(
            query=request.query,
            source_url=url,
            retrieved_at=retrieved_at,
            cache=CacheProvenance(status=cache_states[-1]),
            status=CoverageStatus.FAILED,
            record_count=0,
            error=error,
        )
    )
    if records:
        return SourceResult(
            source=SourceName.CLINICAL_TRIALS,
            provider="clinicaltrials.gov",
            status=CoverageStatus.PARTIAL,
            query=request.query,
            source_url=build_search_url(request),
            retrieved_at=retrieved_at,
            cache=CacheProvenance(status=aggregate_cache_status(cache_states)),
            records=records,
            total_available=total_available,
            requests=requests,
            limitations=["ClinicalTrials.gov pagination stopped after a later request failed."],
            error=SourceError(code="pagination_failed", message="later ClinicalTrials.gov page failed"),
        )
    return SourceResult(
        source=SourceName.CLINICAL_TRIALS,
        provider="clinicaltrials.gov",
        status=CoverageStatus.FAILED,
        query=request.query,
        source_url=build_search_url(request),
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=aggregate_cache_status(cache_states)),
        requests=requests,
        limitations=["No trustworthy ClinicalTrials.gov response was obtained."],
        error=error,
    )


def _single_failure(
    *, source_url: str, query: str, retrieved_at: datetime, cache_status: CacheStatus, error: SourceError
) -> SourceResult:
    evidence = SourceRequestEvidence(
        query=query,
        source_url=source_url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=cache_status),
        status=CoverageStatus.FAILED,
        record_count=0,
        error=error,
    )
    return SourceResult(
        source=SourceName.CLINICAL_TRIALS,
        provider="clinicaltrials.gov",
        status=CoverageStatus.FAILED,
        query=query,
        source_url=source_url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=cache_status),
        requests=[evidence],
        limitations=["No trustworthy ClinicalTrials.gov detail response was obtained."],
        error=error,
    )


def _search_cache_params(request: TrialSearchRequest, page_token: str | None) -> dict[str, Any]:
    return {
        "query": request.query,
        "phase": request.phase,
        "status": request.status,
        "page_size": request.max_results,
        "page_token": page_token,
        "count_total": True,
    }


def _json_mapping(body: bytes) -> Mapping[str, Any]:
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("provider response must be a mapping")
    return payload


def _mapping(container: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = container.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a mapping")
    return value


def _optional_mapping(container: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = container.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a mapping")
    return value


def _list(container: Mapping[str, Any], key: str, *, default: list[Any] | None = None) -> list[Any]:
    value = container.get(key, default)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value


def _string(container: Mapping[str, Any], key: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _date_value(status_module: Mapping[str, Any], key: str) -> str | None:
    value = status_module.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a mapping")
    date = value.get("date")
    return str(date) if date else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("enrollment count must be an integer")
    return value


def _utc(value: datetime | None) -> datetime:
    now = value or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)
