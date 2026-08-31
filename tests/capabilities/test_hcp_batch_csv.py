from __future__ import annotations

import codecs
import json
import os
import stat
from pathlib import Path

import pytest

from open_pharma_plugins_hcp_intelligence import batch_csv
from shared.filesystem import atomic_write_json

EXPECTED_COLUMNS = (
    "account_id",
    "account_type",
    "input_name",
    "input_specialty",
    "input_country",
    "input_institution",
    "status",
    "profile_validated",
    "profile_completeness",
    "profile_name",
    "profile_specialty",
    "profile_country",
    "current_title",
    "organization_type",
    "affiliations",
    "qualifications",
    "research_or_clinical_focus",
    "professional_roles",
    "key_publication_count",
    "clinical_trial_count",
    "active_grant_count",
    "congress_activity_count",
    "source_count",
    "source_urls",
    "tools_failed",
    "error",
    "json_file",
)


def _source(url: str) -> dict[str, str]:
    return {
        "url": url,
        "source_type": "web",
        "title": "Public source",
        "accessed_date": "2026-08-27",
    }


def _claim(value: str, url: str) -> dict[str, object]:
    return {"value": value, "sources": [_source(url)], "confidence": "high"}


def _write_artifact(output_dir: Path, account_id: str, artifact: object) -> None:
    atomic_write_json(output_dir / f"{account_id}.json", artifact)


def test_summary_projection_preserves_order_maps_valid_profiles_and_blanks_unvalidated(tmp_path):
    output_dir = tmp_path / "output"
    accounts = [
        {
            "id": "HCP-001",
            "name": "=Dr Élodie",
            "specialty": "+Oncology",
            "country": "-France",
            "account_type": "HCP",
            "institution": "@Hôpital Central",
        },
        {
            "id": "HCO-001",
            "name": "東京医療センター",
            "specialty": "Cancer care",
            "country": "Japan",
            "account_type": "HCO",
            "institution": "Tokyo",
        },
        {
            "id": "RAW-001",
            "name": "Raw Account",
            "specialty": "Cardiology",
            "country": "Singapore",
            "account_type": "HCP",
            "institution": "General Hospital",
        },
        {
            "id": "INVALID-001",
            "name": "Invalid Profile",
            "specialty": "Neurology",
            "country": "Canada",
            "account_type": "HCP",
            "institution": "Example Clinic",
        },
    ]
    results = [
        {
            "account_id": "INVALID-001",
            "status": "skipped",
            "profile_validated": True,
            "tools_failed": [],
            "error": "+profile validation failed",
        },
        {
            "account_id": "RAW-001",
            "status": "failed",
            "profile_validated": False,
            "tools_failed": ["-search_grants", "@search_web", "-search_grants"],
            "error": "=provider error",
        },
        {
            "account_id": "HCO-001",
            "status": "partial",
            "profile_validated": False,
            "tools_failed": ["search_grants"],
        },
        {
            "account_id": "HCP-001",
            "status": "completed",
            "profile_validated": False,
            "tools_failed": ["+search_web", "search_orcid", "+search_web"],
        },
    ]
    _write_artifact(
        output_dir,
        "HCP-001",
        {
            "synthesized_profile": {
                "full_name": "=Prof Élodie Dupont",
                "specialty": "+Medical Oncology",
                "country": "-France",
                "current_title": _claim("@Department Chair", "https://example.test/title"),
                "affiliations": [
                    _claim("=Cancer Centre", "https://example.test/affiliation-1"),
                    _claim("=Cancer Centre", "https://example.test/affiliation-2"),
                    _claim("General Hospital", "https://example.test/affiliation-3"),
                ],
                "qualifications": [
                    _claim("+FRCP", "https://example.test/qualification"),
                ],
                "research_interests": [
                    _claim("-Immuno-oncology", "https://example.test/research"),
                ],
                "professional_roles": [
                    _claim("@Guideline panel", "https://example.test/role"),
                ],
                "key_publications": [
                    {
                        "pmid": "1",
                        "title": "Publication one",
                        "authors": ["Élodie Dupont"],
                        "journal": "Journal A",
                        "year": 2025,
                        "source_url": "https://example.test/publication-1",
                    },
                    {
                        "pmid": "2",
                        "title": "Publication two",
                        "authors": ["Élodie Dupont"],
                        "journal": "Journal B",
                        "year": 2026,
                        "source_url": "https://example.test/publication-2",
                    },
                ],
                "clinical_trial_involvement": [
                    {
                        "nct_id": "NCT00000001",
                        "title": "Trial one",
                        "status": "RECRUITING",
                        "conditions": ["Cancer"],
                        "interventions": ["Drug A"],
                        "source_url": "https://example.test/trial-1",
                    }
                ],
                "active_grants": [
                    {
                        "grant_number": "R01-1",
                        "title": "Grant one",
                        "source_url": "https://example.test/grant-1",
                    }
                ],
                "congress_activity": [
                    {
                        "congress_name": "ASCO 2025",
                        "source_url": "https://example.test/congress-1",
                    },
                    {
                        "congress_name": "ESMO 2026",
                        "source_url": "https://example.test/congress-2",
                    },
                ],
                "profile_completeness": 0.85,
                "sources_consulted": [
                    _source("=https://example.test/source-a"),
                    _source("https://example.test/source-b"),
                    _source("=https://example.test/source-a"),
                ],
                "built_at": "2026-08-27T00:00:00Z",
            }
        },
    )
    _write_artifact(
        output_dir,
        "HCO-001",
        {
            "synthesized_profile": {
                "name": "東京医療センター",
                "country": "Japan",
                "organization_type": _claim("Tertiary hospital", "https://example.test/type"),
                "clinical_focus_areas": [
                    _claim("Oncology", "https://example.test/clinical-1"),
                    _claim("Shared focus", "https://example.test/clinical-2"),
                ],
                "research_focus": [
                    _claim("Shared focus", "https://example.test/research-1"),
                    _claim("Precision medicine", "https://example.test/research-2"),
                ],
                "accreditations": [
                    _claim("JCI", "https://example.test/accreditation"),
                ],
                "notable_affiliations": [
                    _claim("University of Tokyo", "https://example.test/affiliation"),
                ],
                "active_clinical_trials": [
                    {
                        "nct_id": "NCT00000002",
                        "title": "Trial two",
                        "status": "ACTIVE_NOT_RECRUITING",
                        "conditions": ["Cancer"],
                        "interventions": ["Drug B"],
                        "source_url": "https://example.test/trial-2",
                    },
                    {
                        "nct_id": "NCT00000003",
                        "title": "Trial three",
                        "status": "RECRUITING",
                        "conditions": ["Cancer"],
                        "interventions": ["Drug C"],
                        "source_url": "https://example.test/trial-3",
                    },
                ],
                "institutional_grants": [
                    {
                        "grant_number": "HCO-1",
                        "title": "Institutional grant",
                        "source_url": "https://example.test/hco-grant",
                    }
                ],
                "profile_completeness": 1.0,
                "sources_consulted": [
                    _source("https://example.test/hco-a"),
                    _source("https://example.test/hco-b"),
                ],
                "built_at": "2026-08-27T00:00:00Z",
            }
        },
    )
    _write_artifact(output_dir, "RAW-001", {"search_results": {"search_hcp_web": {"results": []}}})
    _write_artifact(
        output_dir,
        "INVALID-001",
        {
            "synthesized_profile": {
                "full_name": "Should not leak",
                "specialty": "Should not leak",
                "country": "Should not leak",
                "profile_completeness": 2.0,
                "sources_consulted": [_source("https://should-not-leak.test")],
                "built_at": "2026-08-27T00:00:00Z",
            }
        },
    )

    artifacts = {
        account["id"]: json.loads((output_dir / f"{account['id']}.json").read_text(encoding="utf-8"))
        for account in accounts
        if (output_dir / f"{account['id']}.json").is_file()
    }
    rows = batch_csv.build_summary_rows(accounts, results, artifacts)

    assert batch_csv.SUMMARY_COLUMNS == EXPECTED_COLUMNS
    assert rows == [
        {
            "account_id": "HCP-001",
            "account_type": "HCP",
            "input_name": "'=Dr Élodie",
            "input_specialty": "'+Oncology",
            "input_country": "'-France",
            "input_institution": "'@Hôpital Central",
            "status": "completed",
            "profile_validated": "true",
            "profile_completeness": 0.85,
            "profile_name": "'=Prof Élodie Dupont",
            "profile_specialty": "'+Medical Oncology",
            "profile_country": "'-France",
            "current_title": "'@Department Chair",
            "organization_type": "",
            "affiliations": "'=Cancer Centre | General Hospital",
            "qualifications": "'+FRCP",
            "research_or_clinical_focus": "'-Immuno-oncology",
            "professional_roles": "'@Guideline panel",
            "key_publication_count": 2,
            "clinical_trial_count": 1,
            "active_grant_count": 1,
            "congress_activity_count": 2,
            "source_count": 2,
            "source_urls": "'=https://example.test/source-a | https://example.test/source-b",
            "tools_failed": "'+search_web | search_orcid",
            "error": "",
            "json_file": "HCP-001.json",
        },
        {
            "account_id": "HCO-001",
            "account_type": "HCO",
            "input_name": "東京医療センター",
            "input_specialty": "Cancer care",
            "input_country": "Japan",
            "input_institution": "Tokyo",
            "status": "partial",
            "profile_validated": "true",
            "profile_completeness": 1.0,
            "profile_name": "東京医療センター",
            "profile_specialty": "",
            "profile_country": "Japan",
            "current_title": "",
            "organization_type": "Tertiary hospital",
            "affiliations": "University of Tokyo",
            "qualifications": "JCI",
            "research_or_clinical_focus": "Oncology | Shared focus | Precision medicine",
            "professional_roles": "",
            "key_publication_count": 0,
            "clinical_trial_count": 2,
            "active_grant_count": 1,
            "congress_activity_count": 0,
            "source_count": 2,
            "source_urls": "https://example.test/hco-a | https://example.test/hco-b",
            "tools_failed": "search_grants",
            "error": "",
            "json_file": "HCO-001.json",
        },
        {
            "account_id": "RAW-001",
            "account_type": "HCP",
            "input_name": "Raw Account",
            "input_specialty": "Cardiology",
            "input_country": "Singapore",
            "input_institution": "General Hospital",
            "status": "failed",
            "profile_validated": "false",
            "profile_completeness": "",
            "profile_name": "",
            "profile_specialty": "",
            "profile_country": "",
            "current_title": "",
            "organization_type": "",
            "affiliations": "",
            "qualifications": "",
            "research_or_clinical_focus": "",
            "professional_roles": "",
            "key_publication_count": "",
            "clinical_trial_count": "",
            "active_grant_count": "",
            "congress_activity_count": "",
            "source_count": "",
            "source_urls": "",
            "tools_failed": "'-search_grants | @search_web",
            "error": "'=provider error",
            "json_file": "RAW-001.json",
        },
        {
            "account_id": "INVALID-001",
            "account_type": "HCP",
            "input_name": "Invalid Profile",
            "input_specialty": "Neurology",
            "input_country": "Canada",
            "input_institution": "Example Clinic",
            "status": "skipped",
            "profile_validated": "false",
            "profile_completeness": "",
            "profile_name": "",
            "profile_specialty": "",
            "profile_country": "",
            "current_title": "",
            "organization_type": "",
            "affiliations": "",
            "qualifications": "",
            "research_or_clinical_focus": "",
            "professional_roles": "",
            "key_publication_count": "",
            "clinical_trial_count": "",
            "active_grant_count": "",
            "congress_activity_count": "",
            "source_count": "",
            "source_urls": "",
            "tools_failed": "",
            "error": "'+profile validation failed",
            "json_file": "INVALID-001.json",
        },
    ]


def test_summary_writer_emits_exact_bom_csv_hash_and_private_modes(tmp_path):
    path = tmp_path / "private" / "batch_summary.csv"
    row = {
        "account_id": "HCP-東京",
        "account_type": "HCP",
        "input_name": "'=山田, 太郎",
        "input_specialty": "'+腫瘍学",
        "input_country": "'-日本",
        "input_institution": "'@病院",
        "status": "completed",
        "profile_validated": "true",
        "profile_completeness": 0.75,
        "profile_name": "Élodie",
        "profile_specialty": "Oncologie",
        "profile_country": "France",
        "current_title": "'=Chair",
        "organization_type": "",
        "affiliations": "Centre, Paris",
        "qualifications": "'+FRCP",
        "research_or_clinical_focus": "'-Immunologie",
        "professional_roles": "'@Board",
        "key_publication_count": 2,
        "clinical_trial_count": 1,
        "active_grant_count": 0,
        "congress_activity_count": 3,
        "source_count": 2,
        "source_urls": "'=https://one | @https://two",
        "tools_failed": "'+search_web | -search_grants",
        "error": "'=erreur",
        "json_file": "HCP-東京.json",
    }
    expected = codecs.BOM_UTF8 + (
        "account_id,account_type,input_name,input_specialty,input_country,input_institution,status,"
        "profile_validated,profile_completeness,profile_name,profile_specialty,profile_country,"
        "current_title,organization_type,affiliations,qualifications,research_or_clinical_focus,"
        "professional_roles,key_publication_count,clinical_trial_count,active_grant_count,"
        "congress_activity_count,source_count,source_urls,tools_failed,error,json_file\r\n"
        "HCP-東京,HCP,\"'=山田, 太郎\",'+腫瘍学,'-日本,'@病院,completed,true,0.75,Élodie,"
        "Oncologie,France,'=Chair,,\"Centre, Paris\",'+FRCP,'-Immunologie,'@Board,2,1,0,3,2,"
        "'=https://one | @https://two,'+search_web | -search_grants,'=erreur,HCP-東京.json\r\n"
    ).encode("utf-8")

    metadata = batch_csv.write_summary_csv(path, [row])

    assert path.read_bytes() == expected
    assert metadata == {
        "status": "completed",
        "path": str(path.resolve()),
        "schema_version": 1,
        "row_count": 1,
        "sha256": "4548651f61cb2971d5082670a7ecebc4fca8b4b9a1d906658c1be6689f64000d",
    }
    assert list(path.parent.glob(f".{path.name}.*")) == []
    if os.name != "nt":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_summary_writer_removes_temporary_sibling_and_preserves_destination_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "private" / "batch_summary.csv"
    batch_csv.write_summary_csv(path, [])
    original = path.read_bytes()

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("shared.filesystem.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        batch_csv.write_summary_csv(path, [])

    assert path.read_bytes() == original
    assert list(path.parent.glob(f".{path.name}.*")) == []
