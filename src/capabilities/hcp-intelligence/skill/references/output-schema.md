# HCP Intelligence — Output Schema Reference

This document defines the exact JSON structure the agent must produce. The schemas are
implemented as Pydantic models in `open_pharma_plugins_hcp_intelligence/models.py`.

## Provenance primitives

### SourceCitation

Every source referenced in a profile.

```json
{
  "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
  "source_type": "pubmed",
  "title": "Advances in paediatric immunology: a review",
  "accessed_date": "2026-08-19"
}
```

| Field | Type | Required | Values |
|---|---|---|---|
| `url` | string | yes | Permanent URL or identifier |
| `source_type` | enum | yes | `pubmed`, `clinical_trials`, `web`, `registry` |
| `title` | string | no | Title of the source page or document |
| `accessed_date` | string | yes | ISO-8601 date |

### EvidencedClaim

A single factual assertion with its evidence chain.

```json
{
  "value": "Fellow of the Royal College of Paediatrics and Child Health (FRCPCH)",
  "sources": [
    {
      "url": "https://www.rcpch.ac.uk/members/dr-yvonne-lim",
      "source_type": "registry",
      "title": "RCPCH Member Directory",
      "accessed_date": "2026-08-19"
    }
  ],
  "confidence": "high"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `value` | string | yes | The factual claim |
| `sources` | SourceCitation[] | yes (min 1) | Supporting sources |
| `confidence` | enum | yes | `high`, `medium`, or `low` |

**Confidence definitions:**
- **high** — Corroborated by 2+ authoritative sources (peer-reviewed, official registry, institutional page)
- **medium** — Single authoritative source, or 2+ informal sources agreeing
- **low** — Single informal source (news, social media) or reasonably inferred

## HcpProfile

Complete output schema for an individual Healthcare Professional.

```json
{
  "full_name": "Yvonne Lim",
  "specialty": "Paediatric Medicine",
  "country": "Singapore",

  "current_title": {
    "value": "Senior Consultant, Department of Paediatrics",
    "sources": [{"url": "...", "source_type": "web", "accessed_date": "2026-08-19"}],
    "confidence": "high"
  },

  "designations": [
    {"value": "MBBS", "sources": [...], "confidence": "high"},
    {"value": "MRCPCH", "sources": [...], "confidence": "high"}
  ],

  "affiliations": [
    {"value": "KK Women's and Children's Hospital", "sources": [...], "confidence": "high"}
  ],

  "education": [
    {"value": "MBBS, National University of Singapore", "sources": [...], "confidence": "medium"}
  ],

  "qualifications": [
    {"value": "Specialist Accreditation in Paediatric Medicine, MOH Singapore", "sources": [...], "confidence": "high"}
  ],

  "society_memberships": [
    {"value": "Singapore Paediatric Society", "sources": [...], "confidence": "medium"}
  ],

  "professional_roles": [
    {"value": "Member, National Immunisation Advisory Committee", "sources": [...], "confidence": "medium"}
  ],

  "editorial_roles": [],

  "research_interests": [
    {"value": "Paediatric infectious diseases", "sources": [...], "confidence": "high"},
    {"value": "Childhood vaccination programmes", "sources": [...], "confidence": "medium"}
  ],

  "publication_summary": {
    "value": "42 publications in PubMed; top journals include Lancet Infectious Diseases, Pediatrics",
    "sources": [{"url": "https://pubmed.ncbi.nlm.nih.gov/?term=...", "source_type": "pubmed", "accessed_date": "2026-08-19"}],
    "confidence": "high"
  },

  "key_publications": [
    {
      "pmid": "12345678",
      "title": "Efficacy of rotavirus vaccination in Southeast Asian children",
      "authors": ["Lim Y", "Tan A", "Wong B"],
      "journal": "Lancet Infectious Diseases",
      "year": 2024,
      "doi": "10.1016/S1473-3099(24)00123-4",
      "abstract": "Background: ...",
      "mesh_terms": ["Rotavirus Vaccines", "Child"],
      "publication_types": ["Clinical Trial"],
      "source_url": "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    }
  ],

  "clinical_trial_involvement": [
    {
      "nct_id": "NCT05123456",
      "title": "Phase 3 Study of Pentavalent Rotavirus Vaccine in Infants",
      "status": "Completed",
      "phase": "Phase 3",
      "conditions": ["Rotavirus Infections"],
      "interventions": ["Biological: Pentavalent Rotavirus Vaccine"],
      "sponsor": "Example Pharma Inc.",
      "collaborators": ["KK Women's and Children's Hospital"],
      "start_date": "2022-03",
      "completion_date": "2024-06",
      "investigator_name": "Yvonne Lim",
      "investigator_role": "Principal Investigator",
      "source_url": "https://clinicaltrials.gov/study/NCT05123456"
    }
  ],

  "profile_completeness": 0.85,
  "disambiguation_notes": "Distinguished from Yvonne Lim (ophthalmology, Malaysia) by specialty and KKH affiliation.",
  "sources_consulted": [
    {"url": "https://pubmed.ncbi.nlm.nih.gov/?term=...", "source_type": "pubmed", "accessed_date": "2026-08-19"},
    {"url": "https://clinicaltrials.gov/search?term=...", "source_type": "clinical_trials", "accessed_date": "2026-08-19"},
    {"url": "https://www.kkh.com.sg/doctors/...", "source_type": "web", "accessed_date": "2026-08-19"}
  ],
  "built_at": "2026-08-19T14:30:00Z"
}
```

### Field reference

| Section | Field | Type | Description |
|---|---|---|---|
| Identity | `full_name` | string | Full name as identified across sources |
| | `specialty` | string | Primary medical specialty |
| | `country` | string | Primary country of practice |
| Professional | `current_title` | EvidencedClaim? | Current job title |
| | `designations` | EvidencedClaim[] | Honorifics, post-nominals (Prof., FRCP, etc.) |
| | `affiliations` | EvidencedClaim[] | Current and recent institutions |
| Education | `education` | EvidencedClaim[] | Degrees and granting institutions |
| | `qualifications` | EvidencedClaim[] | Board certs, fellowships, registrations |
| Activities | `society_memberships` | EvidencedClaim[] | Medical societies |
| | `professional_roles` | EvidencedClaim[] | Committee, advisory, guideline roles |
| | `editorial_roles` | EvidencedClaim[] | Journal editorial positions |
| Research | `research_interests` | EvidencedClaim[] | Key themes from publications/trials |
| | `publication_summary` | EvidencedClaim? | Count, h-index, top journals |
| | `key_publications` | Publication[] | Up to 10 most relevant papers |
| | `clinical_trial_involvement` | ClinicalTrial[] | Trials as investigator |
| Metadata | `profile_completeness` | float [0,1] | Estimated completeness |
| | `disambiguation_notes` | string? | How the person was identified |
| | `sources_consulted` | SourceCitation[] | All sources accessed |
| | `built_at` | string | ISO-8601 build timestamp |

## HcoProfile

Complete output schema for a Healthcare Organization.

```json
{
  "name": "Singapore General Hospital",
  "country": "Singapore",

  "organization_type": {
    "value": "Tertiary acute care hospital and national referral centre",
    "sources": [...],
    "confidence": "high"
  },

  "clinical_focus_areas": [
    {"value": "Oncology", "sources": [...], "confidence": "high"},
    {"value": "Cardiology and cardiac surgery", "sources": [...], "confidence": "high"},
    {"value": "Transplant medicine", "sources": [...], "confidence": "high"}
  ],

  "specialist_departments": [
    {"value": "National Cancer Centre Singapore (co-located)", "sources": [...], "confidence": "high"},
    {"value": "National Heart Centre Singapore (co-located)", "sources": [...], "confidence": "high"},
    {"value": "Department of Haematology", "sources": [...], "confidence": "medium"}
  ],

  "bed_capacity": {
    "value": "1,785 beds",
    "sources": [...],
    "confidence": "high"
  },

  "annual_patient_volume": {
    "value": "Approximately 1 million outpatient visits per year",
    "sources": [...],
    "confidence": "medium"
  },

  "staff_count": null,

  "accreditations": [
    {"value": "Joint Commission International (JCI) accredited", "sources": [...], "confidence": "high"}
  ],

  "research_focus": [
    {"value": "Translational cancer research via NCCS partnership", "sources": [...], "confidence": "high"}
  ],

  "active_clinical_trials": [],

  "founding_year": {
    "value": "1821",
    "sources": [...],
    "confidence": "high"
  },

  "key_milestones": [
    {"value": "Relocated to Outram campus in 1981", "sources": [...], "confidence": "medium"},
    {"value": "Part of SingHealth cluster since 2000", "sources": [...], "confidence": "high"}
  ],

  "notable_affiliations": [
    {"value": "Duke-NUS Medical School (teaching hospital)", "sources": [...], "confidence": "high"},
    {"value": "SingHealth regional health system", "sources": [...], "confidence": "high"}
  ],

  "profile_completeness": 0.80,
  "sources_consulted": [...],
  "built_at": "2026-08-19T14:45:00Z"
}
```

### Field reference

| Section | Field | Type | Description |
|---|---|---|---|
| Identity | `name` | string | Official organization name |
| | `country` | string | Country |
| | `organization_type` | EvidencedClaim? | Hospital, clinic, research institute, etc. |
| Clinical | `clinical_focus_areas` | EvidencedClaim[] | Primary therapeutic areas |
| | `specialist_departments` | EvidencedClaim[] | Named departments, COEs, units |
| Scale | `bed_capacity` | EvidencedClaim? | Bed count |
| | `annual_patient_volume` | EvidencedClaim? | Admissions or visits |
| | `staff_count` | EvidencedClaim? | Staff headcount |
| | `accreditations` | EvidencedClaim[] | JCI, national accreditation |
| Research | `research_focus` | EvidencedClaim[] | Research programmes |
| | `active_clinical_trials` | ClinicalTrial[] | Trials as site/sponsor |
| History | `founding_year` | EvidencedClaim? | Year established |
| | `key_milestones` | EvidencedClaim[] | Notable events |
| | `notable_affiliations` | EvidencedClaim[] | University ties, networks |
| Metadata | `profile_completeness` | float [0,1] | Estimated completeness |
| | `sources_consulted` | SourceCitation[] | All sources accessed |
| | `built_at` | string | ISO-8601 build timestamp |
