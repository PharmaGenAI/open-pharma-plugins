"""Pydantic output models for HCP/HCO intelligence profiles.

Every profile claim carries its own provenance chain so downstream consumers
(commercial engagement, medical affairs, compliance) can trace each assertion
back to a retrievable source.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Provenance primitives
# ---------------------------------------------------------------------------


class SourceType(str, Enum):
    PUBMED = "pubmed"
    CLINICAL_TRIALS = "clinical_trials"
    WEB = "web"
    REGISTRY = "registry"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceCitation(BaseModel):
    """A single retrievable source backing a claim."""

    url: str = Field(description="Permanent URL or identifier (PubMed link, NCT URL, web page)")
    source_type: SourceType = Field(description="Category of the data source")
    title: str | None = Field(default=None, description="Title of the source document or page")
    accessed_date: str = Field(description="ISO-8601 date when the source was retrieved")


class EvidencedClaim(BaseModel):
    """A single assertion paired with the sources that support it."""

    value: str = Field(description="The factual claim or data point")
    sources: list[SourceCitation] = Field(min_length=1, description="One or more sources supporting this claim")
    confidence: Confidence = Field(
        description=(
            "high = corroborated by multiple authoritative sources; "
            "medium = single authoritative source or multiple informal sources; "
            "low = single informal source or inferred"
        )
    )


# ---------------------------------------------------------------------------
# Search result models (returned by the search_* tools)
# ---------------------------------------------------------------------------


class Publication(BaseModel):
    """A single publication record from PubMed or equivalent."""

    pmid: str | None = Field(default=None, description="PubMed ID")
    title: str = Field(description="Publication title")
    authors: list[str] = Field(description="Author names as listed")
    journal: str = Field(description="Journal name")
    year: int = Field(description="Publication year")
    doi: str | None = Field(default=None, description="Digital Object Identifier")
    abstract: str | None = Field(default=None, description="Abstract text (truncated if long)")
    mesh_terms: list[str] = Field(default_factory=list, description="MeSH descriptor terms")
    publication_types: list[str] = Field(default_factory=list, description="e.g. Clinical Trial, Review, Meta-Analysis")
    source_url: str = Field(description="Direct URL to the publication record")


class PublicationSearchResult(BaseModel):
    """Structured result from search_publications."""

    query: str = Field(description="The query that was executed")
    total_count: int = Field(description="Total matching records in the database")
    publications: list[Publication] = Field(description="Retrieved publication records")
    source: Literal["pubmed", "europe_pmc"] = Field(description="Which API was queried")
    searched_at: str = Field(description="ISO-8601 timestamp of the search")


class ClinicalTrial(BaseModel):
    """A single clinical trial record from ClinicalTrials.gov."""

    nct_id: str = Field(description="ClinicalTrials.gov identifier (NCT number)")
    title: str = Field(description="Official study title")
    status: str = Field(description="Overall recruitment status")
    phase: str | None = Field(default=None, description="Trial phase (Phase 1, 2, 3, 4, N/A)")
    conditions: list[str] = Field(description="Conditions or diseases studied")
    interventions: list[str] = Field(description="Drugs, devices, or procedures under study")
    sponsor: str | None = Field(default=None, description="Lead sponsor organization")
    collaborators: list[str] = Field(default_factory=list, description="Collaborating organizations")
    start_date: str | None = Field(default=None, description="Study start date")
    completion_date: str | None = Field(default=None, description="Primary completion date")
    investigator_name: str | None = Field(default=None, description="Listed PI or contact name")
    investigator_role: str | None = Field(
        default=None, description="Role: Principal Investigator, Sub-Investigator, etc."
    )
    source_url: str = Field(description="Direct URL to the trial record")


class ClinicalTrialSearchResult(BaseModel):
    """Structured result from search_clinical_trials."""

    query: str = Field(description="The query that was executed")
    total_count: int = Field(description="Total matching studies")
    trials: list[ClinicalTrial] = Field(description="Retrieved trial records")
    source: Literal["clinicaltrials.gov"] = "clinicaltrials.gov"
    searched_at: str = Field(description="ISO-8601 timestamp of the search")


class Grant(BaseModel):
    """A single research grant record (typically from NIH RePORTER)."""

    grant_number: str | None = Field(default=None, description="Grant number (e.g. R01CA123456)")
    title: str = Field(description="Project title")
    pi_name: str | None = Field(default=None, description="Principal investigator name")
    institution: str | None = Field(default=None, description="Grantee institution")
    award_amount: float | None = Field(default=None, description="Award amount for the fiscal year (USD)")
    start_date: str | None = Field(default=None, description="Project start date")
    end_date: str | None = Field(default=None, description="Project end date")
    agency: str | None = Field(default=None, description="Funding agency (e.g. NCI, NHLBI)")
    is_active: bool | None = Field(default=None, description="Whether the grant is currently active")
    abstract: str | None = Field(default=None, description="Project abstract (truncated)")
    source_url: str = Field(description="Direct URL to the grant record")


class OrcidProfile(BaseModel):
    """Structured profile from ORCID public API."""

    orcid_id: str = Field(description="ORCID identifier (e.g. 0000-0002-1234-5678)")
    given_names: str | None = Field(default=None, description="Given names")
    family_name: str | None = Field(default=None, description="Family name")
    biography: str | None = Field(default=None, description="Self-authored biography (truncated)")
    education: list[dict] = Field(
        default_factory=list,
        description="Education records: institution, degree, department, years",
    )
    employment: list[dict] = Field(
        default_factory=list,
        description="Employment records: institution, role, department, years",
    )
    publication_count: int = Field(default=0, description="Total works in ORCID profile")
    funding_count: int = Field(default=0, description="Total funding entries")
    peer_review_count: int = Field(default=0, description="Total peer review entries")
    profile_url: str = Field(description="Direct URL to the ORCID profile")


class CongressAppearance(BaseModel):
    """A congress/conference appearance found via web search."""

    congress_name: str = Field(description="Name or abbreviation of the congress (e.g. ASCO 2025)")
    role: str | None = Field(
        default=None,
        description="Presentation role if identifiable: keynote, invited lecture, oral, poster, chair, moderator",
    )
    title: str | None = Field(default=None, description="Title of the presentation or session")
    year: int | None = Field(default=None, description="Year of the congress")
    source_url: str = Field(description="URL where this appearance was found")


class WebResult(BaseModel):
    """A single web search result."""

    url: str = Field(description="Page URL")
    title: str = Field(description="Page title")
    snippet: str = Field(description="Relevant excerpt from the page")
    published_date: str | None = Field(default=None, description="Page publication date if known")
    domain: str | None = Field(default=None, description="Source domain (e.g. hospital.org)")


class WebSearchResult(BaseModel):
    """Structured result from search_hcp_web or search_hco_web."""

    query: str = Field(description="The constructed search query")
    results: list[WebResult] = Field(description="Retrieved web results")
    search_backend: str = Field(description="Backend used: exa, tavily, or serper")
    searched_at: str = Field(description="ISO-8601 timestamp of the search")


# ---------------------------------------------------------------------------
# Profile output models
# ---------------------------------------------------------------------------


class HcpProfile(BaseModel):
    """Canonical profile for an individual Healthcare Professional.

    Every field that makes a factual assertion uses EvidencedClaim so the
    provenance of each data point is individually traceable.
    """

    # --- Identity ---
    full_name: str = Field(description="Full name as identified across sources")
    specialty: str = Field(description="Primary medical specialty")
    country: str = Field(description="Primary country of practice")

    # --- Professional standing ---
    current_title: EvidencedClaim | None = Field(
        default=None, description="Current job title (e.g. Head of Department, Senior Consultant)"
    )
    designations: list[EvidencedClaim] = Field(
        default_factory=list, description="Honorifics or designations (Prof., Dr., FRCP, etc.)"
    )
    affiliations: list[EvidencedClaim] = Field(
        default_factory=list,
        description="Current and recent institutional affiliations",
    )

    # --- Education & qualifications ---
    education: list[EvidencedClaim] = Field(
        default_factory=list, description="Degrees and institutions (e.g. MBBS, University of X)"
    )
    qualifications: list[EvidencedClaim] = Field(
        default_factory=list,
        description="Board certifications, fellowships, specialist registrations",
    )

    # --- Professional activities ---
    society_memberships: list[EvidencedClaim] = Field(
        default_factory=list,
        description="Medical society and association memberships",
    )
    professional_roles: list[EvidencedClaim] = Field(
        default_factory=list,
        description="Committee chairs, advisory board seats, guideline panel roles",
    )
    editorial_roles: list[EvidencedClaim] = Field(default_factory=list, description="Journal editorial board positions")

    # --- Research profile ---
    research_interests: list[EvidencedClaim] = Field(
        default_factory=list,
        description="Key research themes derived from publications and trials",
    )
    publication_summary: EvidencedClaim | None = Field(
        default=None,
        description="Summary: total count, h-index if available, top journals",
    )
    key_publications: list[Publication] = Field(
        default_factory=list,
        description="Most relevant recent publications (up to 10)",
    )
    guideline_publications: list[Publication] = Field(
        default_factory=list,
        description="Practice guidelines, consensus statements authored by this HCP",
    )
    clinical_trial_involvement: list[ClinicalTrial] = Field(
        default_factory=list,
        description="Clinical trials where this HCP is listed as investigator",
    )
    active_grants: list[Grant] = Field(
        default_factory=list,
        description="Current and recent research grants (PI or co-PI)",
    )

    # --- KOL signals ---
    congress_activity: list[CongressAppearance] = Field(
        default_factory=list,
        description="Conference speaking roles, poster presentations, session chairs",
    )
    regulatory_advisory_roles: list[EvidencedClaim] = Field(
        default_factory=list,
        description="FDA advisory committee, EMA CHMP/SAWP, WHO expert panel roles",
    )
    orcid_id: str | None = Field(default=None, description="ORCID identifier if found")

    # --- Metadata ---
    profile_completeness: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Estimated completeness: 1.0 = all sections populated with high-confidence "
            "claims; lower values indicate sections with no data or low confidence"
        ),
    )
    disambiguation_notes: str | None = Field(
        default=None,
        description="Notes on how this person was distinguished from others with similar names",
    )
    sources_consulted: list[SourceCitation] = Field(description="All sources accessed during profile construction")
    built_at: str = Field(description="ISO-8601 timestamp when the profile was assembled")


class HcoProfile(BaseModel):
    """Canonical profile for a Healthcare Organization.

    Covers clinical scope, scale, research activity, and institutional
    history with per-claim provenance.
    """

    # --- Identity ---
    name: str = Field(description="Official name of the organization")
    country: str = Field(description="Country")
    organization_type: EvidencedClaim | None = Field(
        default=None,
        description="Type: tertiary hospital, specialty clinic, research institute, etc.",
    )

    # --- Clinical scope ---
    clinical_focus_areas: list[EvidencedClaim] = Field(
        default_factory=list,
        description="Primary therapeutic areas or disease focus",
    )
    specialist_departments: list[EvidencedClaim] = Field(
        default_factory=list,
        description="Named departments, centres of excellence, or specialist units",
    )

    # --- Scale & capacity ---
    bed_capacity: EvidencedClaim | None = Field(default=None, description="Total bed count or ICU/ward breakdown")
    annual_patient_volume: EvidencedClaim | None = Field(
        default=None, description="Annual admissions, outpatient visits, or surgical volume"
    )
    staff_count: EvidencedClaim | None = Field(default=None, description="Medical staff, nursing, or total headcount")
    accreditations: list[EvidencedClaim] = Field(
        default_factory=list,
        description="JCI, NABH, Magnet, or national accreditation status",
    )

    # --- Research & trials ---
    research_focus: list[EvidencedClaim] = Field(
        default_factory=list,
        description="Active research programmes, centres, or partnerships",
    )
    active_clinical_trials: list[ClinicalTrial] = Field(
        default_factory=list,
        description="Clinical trials listing this organization as a site or sponsor",
    )
    institutional_grants: list[Grant] = Field(
        default_factory=list,
        description="Research grants awarded to this organization",
    )

    # --- History & context ---
    founding_year: EvidencedClaim | None = Field(default=None, description="Year the organization was established")
    key_milestones: list[EvidencedClaim] = Field(
        default_factory=list,
        description="Notable events: mergers, expansions, firsts, rankings",
    )
    notable_affiliations: list[EvidencedClaim] = Field(
        default_factory=list,
        description="University partnerships, network memberships, government designations",
    )

    # --- Metadata ---
    profile_completeness: float = Field(
        ge=0.0,
        le=1.0,
        description="Estimated completeness (same semantics as HcpProfile)",
    )
    sources_consulted: list[SourceCitation] = Field(description="All sources accessed during profile construction")
    built_at: str = Field(description="ISO-8601 timestamp when the profile was assembled")
