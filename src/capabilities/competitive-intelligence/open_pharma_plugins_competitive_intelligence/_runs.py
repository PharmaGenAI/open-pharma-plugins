"""One-pass collection and immutable persistence for CI evidence runs."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from shared.filesystem import atomic_write_text, contained_path, validate_component

from . import _clinical_trials, _pubmed, _regulatory, _web_search
from ._watchlist import runs_dir
from .models import (
    ArtifactRecord,
    CacheProvenance,
    CIRun,
    CIRunManifest,
    CIRunPayload,
    CoverageStatus,
    EntitySnapshot,
    NewsSearchRequest,
    PublicationSearchRequest,
    RefreshRequest,
    RegulatorySearchRequest,
    RegulatorySearchResult,
    RunCoverageSummary,
    SourceName,
    SourceResult,
    TrackedEntity,
    TrialSearchRequest,
)

_RECORDS_FILE = "records.json"
_MANIFEST_FILE = "manifest.json"
_MAX_ID_ATTEMPTS = 3


class RunIntegrityError(ValueError):
    """A persisted run failed path, schema, or hash validation."""


@dataclass(frozen=True)
class ProviderBundle:
    trials: Callable[[TrialSearchRequest, datetime], SourceResult]
    regulatory: Callable[[RegulatorySearchRequest, datetime], RegulatorySearchResult]
    news: Callable[[NewsSearchRequest, datetime], SourceResult]
    publications: Callable[[PublicationSearchRequest, datetime], SourceResult]


DEFAULT_PROVIDERS = ProviderBundle(
    trials=lambda request, now: _clinical_trials.search_trials(request, now=now),
    regulatory=lambda request, now: _regulatory.scan_regulatory(request, now=now),
    news=lambda request, now: _web_search.search_news(request, now=now),
    publications=lambda request, now: _pubmed.search_publications(request, now=now),
)


def create_run(
    request: RefreshRequest,
    *,
    providers: ProviderBundle | None = None,
    now: datetime | None = None,
) -> CIRun:
    """Collect every requested source once, then atomically expose the immutable run."""
    effective_now = _utc(now)
    provider_bundle = providers or DEFAULT_PROVIDERS
    snapshots = [_collect_entity(entity, request, provider_bundle, effective_now) for entity in request.entities]
    limitations = [
        f"{snapshot.entity.name}/{source_name}: {limitation}"
        for snapshot in snapshots
        for source_name, result in snapshot.sources.items()
        for limitation in result.limitations
    ]
    return _persist(request, snapshots, limitations, effective_now)


def load_run(run_id: str) -> CIRun:
    """Load a run only after validating its contained paths, schema, size, and hash."""
    try:
        validate_component(run_id, label="run id")
        run_path = contained_path(runs_dir(), run_id)
        manifest_path = contained_path(run_path, _MANIFEST_FILE)
    except (TypeError, ValueError):
        raise RunIntegrityError("invalid run id") from None

    try:
        manifest = CIRunManifest.model_validate(json.loads(manifest_path.read_text()))
    except (OSError, ValueError, TypeError, json.JSONDecodeError, ValidationError):
        raise RunIntegrityError("run manifest is missing or invalid") from None
    if manifest.run_id != run_id:
        raise RunIntegrityError("manifest run id mismatch")
    if manifest.records.relative_path != _RECORDS_FILE:
        raise RunIntegrityError("manifest records path is invalid")
    if manifest.records.media_type != "application/json":
        raise RunIntegrityError("manifest records media type is invalid")

    try:
        records_path = contained_path(run_path, manifest.records.relative_path)
        records_bytes = records_path.read_bytes()
    except (OSError, TypeError, ValueError):
        raise RunIntegrityError("run records are missing or invalid") from None
    records_sha256 = hashlib.sha256(records_bytes).hexdigest()
    if records_sha256 != manifest.records.sha256:
        raise RunIntegrityError("records hash mismatch")
    if len(records_bytes) != manifest.records.byte_size:
        raise RunIntegrityError("records size mismatch")

    try:
        payload = CIRunPayload.model_validate(json.loads(records_bytes))
    except (ValueError, TypeError, json.JSONDecodeError, ValidationError):
        raise RunIntegrityError("run records schema is invalid") from None
    if payload.run_id != run_id:
        raise RunIntegrityError("records run id mismatch")
    return CIRun(
        **payload.model_dump(),
        records_path=str(records_path),
        records_sha256=records_sha256,
        manifest_path=str(manifest_path),
    )


def _collect_entity(
    entity: TrackedEntity,
    request: RefreshRequest,
    providers: ProviderBundle,
    now: datetime,
) -> EntitySnapshot:
    sources: dict[str, SourceResult] = {}
    query = _combined_query(entity)
    if "trials" in request.include_sections:
        sources[SourceName.CLINICAL_TRIALS.value] = providers.trials(TrialSearchRequest(query=query), now)
    if "regulatory" in request.include_sections:
        if entity.entity_type == "company":
            sources[SourceName.OPENFDA.value] = _not_applicable(
                SourceName.OPENFDA,
                entity.name,
                "https://api.fda.gov/drug/drugsfda.json",
                now,
            )
            sources[SourceName.DAILYMED.value] = _not_applicable(
                SourceName.DAILYMED,
                entity.name,
                "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json",
                now,
            )
        else:
            regulatory = providers.regulatory(
                RegulatorySearchRequest(drug_name=entity.name, aliases=entity.aliases), now
            )
            sources[SourceName.OPENFDA.value] = regulatory.openfda
            if regulatory.dailymed is not None:
                sources[SourceName.DAILYMED.value] = regulatory.dailymed
    if "news" in request.include_sections:
        sources[SourceName.WEB.value] = providers.news(
            NewsSearchRequest(query=query, days_back=request.news_days_back), now
        )
    if "publications" in request.include_sections:
        sources[SourceName.PUBMED.value] = providers.publications(
            PublicationSearchRequest(
                query=query,
                days_back=request.publication_days_back,
            ),
            now,
        )
    return EntitySnapshot(entity=entity, sources=sources)


def _not_applicable(
    source: SourceName,
    query: str,
    source_url: str,
    now: datetime,
) -> SourceResult:
    return SourceResult(
        source=source,
        status=CoverageStatus.NOT_APPLICABLE,
        query=query,
        source_url=source_url,
        retrieved_at=now,
        cache=CacheProvenance(status="disabled"),
        limitations=["Regulatory drug records do not apply to a company identity."],
    )


def _combined_query(entity: TrackedEntity) -> str:
    return " OR ".join(json.dumps(name, ensure_ascii=False) for name in [entity.name, *entity.aliases])


def _persist(
    request: RefreshRequest,
    snapshots: list[EntitySnapshot],
    limitations: list[str],
    now: datetime,
) -> CIRun:
    root = runs_dir()
    for _attempt in range(_MAX_ID_ATTEMPTS):
        run_id = f"ci_{now.strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(4)}"
        final_path = contained_path(root, run_id)
        if final_path.exists():
            continue
        stage_path = Path(tempfile.mkdtemp(prefix=".ci-stage-", dir=root))
        try:
            payload = CIRunPayload(
                run_id=run_id,
                generated_at=now,
                request=request,
                entities=snapshots,
                limitations=limitations,
            )
            records_path = contained_path(stage_path, _RECORDS_FILE)
            records_text = json.dumps(
                payload.model_dump(mode="json"),
                indent=2,
                ensure_ascii=False,
            )
            atomic_write_text(records_path, records_text)
            records_bytes = records_path.read_bytes()
            records_sha256 = hashlib.sha256(records_bytes).hexdigest()
            manifest = CIRunManifest(
                run_id=run_id,
                generated_at=now,
                records=ArtifactRecord(
                    relative_path=_RECORDS_FILE,
                    media_type="application/json",
                    byte_size=len(records_bytes),
                    sha256=records_sha256,
                ),
                coverage=_coverage(snapshots),
            )
            atomic_write_text(
                contained_path(stage_path, _MANIFEST_FILE),
                json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False),
            )
            if final_path.exists():
                continue
            os.rename(stage_path, final_path)
            return CIRun(
                **payload.model_dump(),
                records_path=str(contained_path(final_path, _RECORDS_FILE)),
                records_sha256=records_sha256,
                manifest_path=str(contained_path(final_path, _MANIFEST_FILE)),
            )
        except FileExistsError:
            continue
        finally:
            if stage_path.exists():
                shutil.rmtree(stage_path)
    raise RunIntegrityError("could not allocate a unique run id after three attempts")


def _coverage(snapshots: list[EntitySnapshot]) -> list[RunCoverageSummary]:
    return [
        RunCoverageSummary(
            entity=snapshot.entity.name,
            source=result.source,
            status=result.status,
            record_count=len(result.records),
            limitations=result.limitations,
        )
        for snapshot in snapshots
        for result in snapshot.sources.values()
    ]


def _utc(value: datetime | None) -> datetime:
    now = value or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)
