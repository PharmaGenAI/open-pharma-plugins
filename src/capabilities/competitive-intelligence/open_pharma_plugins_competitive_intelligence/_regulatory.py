"""One-pass regulatory collection across openFDA and DailyMed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from . import _dailymed, _fda
from .models import (
    CacheProvenance,
    CoverageStatus,
    LabelHistoryEntry,
    RegulatoryEvent,
    RegulatorySearchRequest,
    RegulatorySearchResult,
    SourceError,
    SourceName,
    SourceResult,
    aggregate_cache_status,
    aggregate_coverage,
)


@dataclass(frozen=True)
class RegulatoryProviders:
    openfda: Callable[[RegulatorySearchRequest, datetime], SourceResult]
    dailymed_search: Callable[[RegulatorySearchRequest, datetime], SourceResult]
    dailymed_history: Callable[[str, datetime], SourceResult]


DEFAULT_PROVIDERS = RegulatoryProviders(
    openfda=lambda request, now: _fda.search_openfda(request, now=now),
    dailymed_search=lambda request, now: _dailymed.search_dailymed(request, now=now),
    dailymed_history=lambda set_id, now: _dailymed.get_dailymed_history(set_id, now=now),
)


def scan_regulatory(
    request: RegulatorySearchRequest,
    *,
    providers: RegulatoryProviders | None = None,
    now: datetime | None = None,
) -> RegulatorySearchResult:
    providers = providers or DEFAULT_PROVIDERS
    collected_at = _utc(now)
    openfda = providers.openfda(request, collected_at)
    dailymed = None
    if request.include_label_history:
        search = providers.dailymed_search(request, collected_at)
        histories = []
        if search.status in {CoverageStatus.COMPLETE, CoverageStatus.PARTIAL}:
            seen_set_ids: set[str] = set()
            for record in search.records:
                set_id = str(record.get("set_id", ""))
                if set_id and set_id not in seen_set_ids:
                    seen_set_ids.add(set_id)
                    histories.append(providers.dailymed_history(set_id, collected_at))
        dailymed = _combine_dailymed(request, search, histories)

    events = _dedupe_events(openfda)
    label_history = _dedupe_history(dailymed)
    sources = [openfda] + ([dailymed] if dailymed is not None else [])
    coverage = aggregate_coverage([source.status for source in sources])
    limitations = [f"{source.source.value}: {limitation}" for source in sources for limitation in source.limitations]
    return RegulatorySearchResult(
        drug_name=request.drug_name,
        coverage=coverage,
        openfda=openfda,
        dailymed=dailymed,
        events=events,
        label_history=label_history,
        limitations=limitations,
    )


def _combine_dailymed(
    request: RegulatorySearchRequest,
    search: SourceResult,
    histories: list[SourceResult],
) -> SourceResult:
    if search.status in {CoverageStatus.FAILED, CoverageStatus.NOT_CONFIGURED}:
        return search
    sources = [search, *histories]
    status = aggregate_coverage([source.status for source in sources])
    records = [record for history in histories for record in history.records]
    requests = [evidence for source in sources for evidence in source.requests]
    limitations = [limitation for source in sources for limitation in source.limitations]
    error = None
    if status == CoverageStatus.PARTIAL:
        error = SourceError(code="partial_coverage", message="one or more DailyMed requests failed")
    elif status == CoverageStatus.FAILED:
        error = next((source.error for source in sources if source.error is not None), None)
    return SourceResult(
        source=SourceName.DAILYMED,
        provider="dailymed",
        status=status,
        query=request.drug_name,
        source_url=search.source_url,
        retrieved_at=search.retrieved_at,
        cache=CacheProvenance(status=aggregate_cache_status([source.cache.status for source in sources])),
        records=records,
        total_available=len(records) if status in {CoverageStatus.COMPLETE, CoverageStatus.PARTIAL} else None,
        requests=requests,
        limitations=limitations,
        error=error,
    )


def _dedupe_events(source: SourceResult) -> list[RegulatoryEvent]:
    seen: set[tuple[str, str, str]] = set()
    events = []
    for record in source.records:
        event = RegulatoryEvent.model_validate(record)
        key = (event.application_number, event.submission, event.date.isoformat() if event.date else "")
        if key not in seen:
            seen.add(key)
            events.append(event)
    return events


def _dedupe_history(source: SourceResult | None) -> list[LabelHistoryEntry]:
    if source is None:
        return []
    seen: set[tuple[str, str, str]] = set()
    entries = []
    for record in source.records:
        entry = LabelHistoryEntry.model_validate(record)
        key = (entry.set_id, entry.spl_version, entry.published_date)
        if key not in seen:
            seen.add(key)
            entries.append(entry)
    return entries


def _utc(value: datetime | None) -> datetime:
    now = value or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)
