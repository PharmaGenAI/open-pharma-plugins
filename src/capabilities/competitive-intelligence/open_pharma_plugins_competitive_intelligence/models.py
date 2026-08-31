"""Pydantic domain models for competitive intelligence."""

from __future__ import annotations

import re
from datetime import date as Date
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from shared.filesystem import sanitize_url


class CoverageStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"
    NOT_APPLICABLE = "not_applicable"


class CacheStatus(str, Enum):
    HIT = "hit"
    MISS = "miss"
    DISABLED = "disabled"
    MIXED = "mixed"


class SourceName(str, Enum):
    CLINICAL_TRIALS = "clinicaltrials_gov"
    OPENFDA = "openfda"
    DAILYMED = "dailymed"
    PUBMED = "pubmed"
    WEB = "web_search"
    DOCUMENT = "document"


class DatePrecision(str, Enum):
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    UNKNOWN = "unknown"


class CacheProvenance(BaseModel):
    status: CacheStatus
    cached_at: datetime | None = None
    schema_version: int = 2


class SourceError(BaseModel):
    code: str
    message: str


class SourceRequestEvidence(BaseModel):
    query: str
    source_url: str
    retrieved_at: datetime
    cache: CacheProvenance
    status: CoverageStatus
    record_count: int = Field(ge=0)
    error: SourceError | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> "SourceRequestEvidence":
        _validate_https_url(self.source_url)
        if self.status in {CoverageStatus.FAILED, CoverageStatus.NOT_CONFIGURED} and self.error is None:
            raise ValueError("failed and not_configured request evidence requires an error")
        if self.status in {CoverageStatus.COMPLETE, CoverageStatus.NOT_APPLICABLE} and self.error is not None:
            raise ValueError("complete and not_applicable request evidence cannot contain an error")
        if (
            self.status
            in {
                CoverageStatus.FAILED,
                CoverageStatus.NOT_CONFIGURED,
                CoverageStatus.NOT_APPLICABLE,
            }
            and self.record_count
        ):
            raise ValueError(f"{self.status.value} request evidence cannot contain records")
        if self.status == CoverageStatus.PARTIAL and self.record_count == 0:
            raise ValueError("partial request evidence requires at least one usable record")
        return self


class SourceResult(BaseModel):
    source: SourceName
    provider: str | None = None
    status: CoverageStatus
    query: str
    source_url: str
    retrieved_at: datetime
    cache: CacheProvenance
    records: list[dict[str, Any]] = Field(default_factory=list)
    total_available: int | None = Field(default=None, ge=0)
    requests: list[SourceRequestEvidence] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    error: SourceError | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> "SourceResult":
        if self.source == SourceName.DOCUMENT:
            if not re.fullmatch(r"urn:sha256:[0-9a-f]{64}", self.source_url):
                raise ValueError("document evidence requires a lowercase SHA-256 URN")
        else:
            _validate_https_url(self.source_url)

        if self.status in {CoverageStatus.FAILED, CoverageStatus.NOT_CONFIGURED} and self.error is None:
            raise ValueError("failed and not_configured source results requires an error")
        if self.status in {CoverageStatus.COMPLETE, CoverageStatus.NOT_APPLICABLE} and self.error is not None:
            raise ValueError("complete and not_applicable source results cannot contain an error")
        non_usable = {
            CoverageStatus.FAILED,
            CoverageStatus.NOT_CONFIGURED,
            CoverageStatus.NOT_APPLICABLE,
        }
        if self.status in non_usable and self.records:
            raise ValueError(f"{self.status.value} source results cannot contain records")
        if self.status in non_usable and self.total_available is not None:
            raise ValueError(f"{self.status.value} source results cannot contain a total")
        if self.total_available is not None and self.total_available < len(self.records):
            raise ValueError("total_available cannot be smaller than the returned record count")

        allowed_non_usable_requests = {
            CoverageStatus.FAILED: {CoverageStatus.FAILED, CoverageStatus.NOT_CONFIGURED},
            CoverageStatus.NOT_CONFIGURED: {CoverageStatus.NOT_CONFIGURED},
            CoverageStatus.NOT_APPLICABLE: {CoverageStatus.NOT_APPLICABLE},
        }
        if self.status in allowed_non_usable_requests and any(
            request.status not in allowed_non_usable_requests[self.status] for request in self.requests
        ):
            raise ValueError(f"{self.status.value} source results cannot contain usable requests")
        if self.status == CoverageStatus.COMPLETE and any(
            request.status != CoverageStatus.COMPLETE for request in self.requests
        ):
            raise ValueError("complete source results require complete constituent requests")
        if self.status == CoverageStatus.PARTIAL:
            has_success = any(
                request.status == CoverageStatus.COMPLETE
                or (request.status == CoverageStatus.PARTIAL and request.record_count > 0)
                for request in self.requests
            )
            has_failure_or_truncation = any(
                request.status in {CoverageStatus.PARTIAL, CoverageStatus.FAILED} for request in self.requests
            ) or (self.total_available is not None and self.total_available > len(self.records))
            if not has_success or not has_failure_or_truncation:
                raise ValueError("partial source results require successful and failed or truncated requests")
        return self


def aggregate_coverage(statuses: list[CoverageStatus]) -> CoverageStatus:
    """Aggregate source coverage without turning missing coverage into zero results."""
    applicable = [status for status in statuses if status != CoverageStatus.NOT_APPLICABLE]
    if not applicable:
        return CoverageStatus.NOT_APPLICABLE
    if all(status == CoverageStatus.COMPLETE for status in applicable):
        return CoverageStatus.COMPLETE
    if any(status == CoverageStatus.PARTIAL for status in applicable):
        return CoverageStatus.PARTIAL
    if CoverageStatus.COMPLETE in applicable:
        return CoverageStatus.PARTIAL
    if all(status == CoverageStatus.NOT_CONFIGURED for status in applicable):
        return CoverageStatus.NOT_CONFIGURED
    if CoverageStatus.FAILED in applicable:
        return CoverageStatus.FAILED
    return CoverageStatus.NOT_CONFIGURED


def aggregate_cache_status(statuses: list[CacheStatus]) -> CacheStatus:
    """Return a single cache state while retaining mixed provenance explicitly."""
    if not statuses:
        return CacheStatus.DISABLED
    first = statuses[0]
    return first if all(status == first for status in statuses) else CacheStatus.MIXED


def _validate_https_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("source URL must be credential-free HTTPS")
    if sanitize_url(value) != value:
        raise ValueError("source URL must not contain credential query parameters")


class TrialSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    phase: Literal["PHASE1", "PHASE2", "PHASE3", "PHASE4"] | None = None
    status: str | None = None
    max_results: int = Field(default=20, ge=1, le=50)

    @field_validator("phase", mode="before")
    @classmethod
    def normalize_phase(cls, value: str | None) -> str | None:
        return {"1": "PHASE1", "2": "PHASE2", "3": "PHASE3", "4": "PHASE4"}.get(value, value)


class TrialDetailRequest(BaseModel):
    nct_id: str = Field(pattern=r"^NCT\d{8}$")

    @field_validator("nct_id", mode="before")
    @classmethod
    def normalize_nct_id(cls, value: str) -> str:
        return value.strip().upper()


class TrialArm(BaseModel):
    label: str
    type: str
    description: str = ""
    interventions: list[str] = Field(default_factory=list)


class TrialIntervention(BaseModel):
    name: str = Field(min_length=1)
    intervention_type: str = Field(min_length=1)
    description: str = ""
    other_names: list[str] = Field(default_factory=list)


class TrialResultsSummary(BaseModel):
    has_results: bool
    primary_outcomes_count: int = 0
    adverse_events_reported: bool = False


class Trial(BaseModel):
    nct_id: str
    title: str
    sponsor: str
    collaborators: list[str] = Field(default_factory=list)
    phase: str
    status: str
    conditions: list[str] = Field(default_factory=list)
    interventions: list[TrialIntervention] = Field(default_factory=list)
    mechanism: str | None = None
    enrollment: int | None = None
    start_date: str | None = None
    primary_completion_date: str | None = None
    estimated_completion_date: str | None = None
    study_type: str = "INTERVENTIONAL"
    primary_endpoints: list[str] = Field(default_factory=list)
    has_results: bool = False
    source_url: str


class TrialDetail(BaseModel):
    trial: Trial
    arms: list[TrialArm] = Field(default_factory=list)
    secondary_endpoints: list[str] = Field(default_factory=list)
    eligibility_criteria: str | None = None
    status_history: list[dict[str, str]] = Field(default_factory=list)
    milestone_dates: list[dict[str, str]] = Field(default_factory=list)
    results_summary: TrialResultsSummary | None = None
    publications: list[str] = Field(default_factory=list)


class RegulatorySearchRequest(BaseModel):
    drug_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    date_from: Date | None = None
    date_to: Date | None = None
    include_label_history: bool = True
    max_results: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def validate_dates(self) -> "RegulatorySearchRequest":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class RegulatoryEvent(BaseModel):
    date: Date | None = None
    event_type: Literal["approval", "supplement", "label_change", "other"]
    application_number: str = ""
    submission: str = ""
    status: str = ""
    brand_name: str = ""
    generic_name: str = ""
    sponsor: str = ""
    manufacturer_names: list[str] = Field(default_factory=list)
    description: str = ""
    source_url: str


class LabelHistoryEntry(BaseModel):
    set_id: str
    spl_version: str
    published_date: str
    title: str
    source_url: str


class RegulatorySearchResult(BaseModel):
    drug_name: str
    coverage: CoverageStatus
    openfda: SourceResult
    dailymed: SourceResult | None = None
    events: list[RegulatoryEvent] = Field(default_factory=list)
    label_history: list[LabelHistoryEntry] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class PublicationSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    days_back: int = Field(default=365, ge=1, le=1825)
    max_results: int = Field(default=10, ge=1, le=30)


class Publication(BaseModel):
    pmid: str
    title: str
    authors: list[str] = Field(default_factory=list)
    journal: str = ""
    pub_date: str = ""
    abstract_excerpt: str = ""
    pub_types: list[str] = Field(default_factory=list)
    source_url: str


class SearchBackend(str, Enum):
    AUTO = "auto"
    SERPER = "serper"
    TAVILY = "tavily"
    EXA = "exa"


class NewsSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    days_back: int = Field(default=90, ge=1, le=365)
    max_results: int = Field(default=10, ge=1, le=30)


class NewsItem(BaseModel):
    title: str
    url: str
    snippet: str = ""
    source: str = ""
    published_date: str | None = None

    @field_validator("url")
    @classmethod
    def validate_outbound_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("news URL must be credential-free HTTP or HTTPS")
        if sanitize_url(value) != value:
            raise ValueError("news URL must not contain credential query parameters")
        return value


SectionName = Literal["trials", "regulatory", "news", "publications"]
_SECTION_ORDER = ("trials", "regulatory", "news", "publications")


class TrackedEntity(BaseModel):
    entity_type: Literal["drug", "company"]
    name: str = Field(min_length=1)
    therapeutic_area: str = ""
    aliases: list[str] = Field(default_factory=list)
    added_at: datetime | None = None

    @field_validator("name", "therapeutic_area", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def normalize_aliases(self) -> "TrackedEntity":
        seen = {self.name.casefold()}
        aliases = []
        for raw in self.aliases:
            alias = raw.strip()
            if alias and alias.casefold() not in seen:
                seen.add(alias.casefold())
                aliases.append(alias)
        self.aliases = aliases
        if self.added_at is not None and self.added_at.tzinfo is None:
            raise ValueError("added_at must be timezone-aware")
        return self


class RefreshRequest(BaseModel):
    entities: list[TrackedEntity] = Field(min_length=1)
    include_sections: list[SectionName] = Field(default_factory=lambda: list(_SECTION_ORDER))
    news_days_back: int = Field(default=90, ge=1, le=365)
    publication_days_back: int = Field(default=365, ge=1, le=1825)

    @field_validator("include_sections", mode="before")
    @classmethod
    def normalize_sections(cls, value: Any) -> list[Any]:
        values = list(value)
        unique = list(dict.fromkeys(values))
        return [section for section in _SECTION_ORDER if section in unique] + [
            section for section in unique if section not in _SECTION_ORDER
        ]

    @field_validator("include_sections")
    @classmethod
    def require_section(cls, value: list[SectionName]) -> list[SectionName]:
        if not value:
            raise ValueError("include_sections requires at least one section")
        return value


class EntitySnapshot(BaseModel):
    entity: TrackedEntity
    sources: dict[str, SourceResult]


class ArtifactRecord(BaseModel):
    relative_path: str
    media_type: str
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RunCoverageSummary(BaseModel):
    entity: str
    source: SourceName
    status: CoverageStatus
    record_count: int = Field(ge=0)
    limitations: list[str] = Field(default_factory=list)


class CIRunPayload(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    generated_at: datetime
    request: RefreshRequest
    entities: list[EntitySnapshot]
    limitations: list[str] = Field(default_factory=list)


class CIRunManifest(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    generated_at: datetime
    records: ArtifactRecord
    coverage: list[RunCoverageSummary] = Field(default_factory=list)


class CIRun(CIRunPayload):
    records_path: str
    records_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_path: str


class ArtifactManifest(BaseModel):
    schema_version: Literal[1] = 1
    artifact_id: str
    run_id: str
    run_records_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_at: datetime
    display_stem: str
    artifacts: list[ArtifactRecord]


class ReportArtifactResult(BaseModel):
    output_dir: str
    json_path: str
    html_path: str
    csv_files: list[str] = Field(default_factory=list)
    manifest_path: str
    manifest: ArtifactManifest


class TimelineArtifactResult(BaseModel):
    output_dir: str
    html_path: str
    manifest_path: str
    manifest: ArtifactManifest
    included_events: int = Field(ge=0)
    excluded_old_events: int = Field(ge=0)
    excluded_undated_events: int = Field(ge=0)


class LabelEvent(BaseModel):
    drug_name: str
    generic_name: str | None = None
    application_number: str | None = None
    event_type: str
    date: str
    description: str
    indication: str | None = None
    sections_changed: list[str] = Field(default_factory=list)
    sponsor: str | None = None


class CIEvent(BaseModel):
    event_type: str
    date: str | None = None
    competitor: str
    product: str | None = None
    therapeutic_area: str | None = None
    description: str
    implication: str | None = None
    source: str | None = None
    source_page: int | None = None
    confidence: str = "medium"


class LandscapeEntry(BaseModel):
    competitor: str
    product: str
    mechanism: str | None = None
    phase: str
    status: str
    indications: list[str] = Field(default_factory=list)
    key_trials: list[str] = Field(default_factory=list)
    expected_milestones: list[dict[str, str]] = Field(default_factory=list)
    differentiators: list[str] = Field(default_factory=list)
    approval_date: str | None = None


class Landscape(BaseModel):
    therapeutic_area: str
    generated_at: str
    entries: list[LandscapeEntry] = Field(default_factory=list)
    summary: str = ""
    data_sources: list[str] = Field(default_factory=list)
