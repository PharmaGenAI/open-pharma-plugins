"""Stable CSV projection for HCP Intelligence batch artifacts."""

from __future__ import annotations

import codecs
import csv
import hashlib
import io
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from open_pharma_plugins_hcp_intelligence.models import HcoProfile, HcpProfile
from shared.filesystem import atomic_write_bytes

CSV_SCHEMA_VERSION = 1
SUMMARY_FILENAME = "batch_summary.csv"
SUMMARY_COLUMNS = (
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

_NUMERIC_COLUMNS = {
    "profile_completeness",
    "key_publication_count",
    "clinical_trial_count",
    "active_grant_count",
    "congress_activity_count",
    "source_count",
}


def _join_unique(values: Iterable[str]) -> str:
    return " | ".join(dict.fromkeys(value for value in values if value))


def _safe_text(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def _result_values(value: object) -> Iterable[str]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return (str(item) for item in value if item is not None)
    return ()


def _blank_row(account: Mapping[str, str], result: Mapping[str, Any]) -> dict[str, str | int | float]:
    account_id = account["id"]
    return {
        "account_id": account_id,
        "account_type": account["account_type"],
        "input_name": account["name"],
        "input_specialty": account.get("specialty", ""),
        "input_country": account["country"],
        "input_institution": account.get("institution", ""),
        "status": result.get("status", ""),
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
        "tools_failed": _join_unique(_result_values(result.get("tools_failed", []))),
        "error": result.get("error", ""),
        "json_file": f"{account_id}.json",
    }


def _populate_hcp(row: dict[str, str | int | float], profile: HcpProfile) -> None:
    row.update(
        {
            "profile_validated": "true",
            "profile_completeness": profile.profile_completeness,
            "profile_name": profile.full_name,
            "profile_specialty": profile.specialty,
            "profile_country": profile.country,
            "current_title": profile.current_title.value if profile.current_title else "",
            "affiliations": _join_unique(claim.value for claim in profile.affiliations),
            "qualifications": _join_unique(claim.value for claim in profile.qualifications),
            "research_or_clinical_focus": _join_unique(claim.value for claim in profile.research_interests),
            "professional_roles": _join_unique(claim.value for claim in profile.professional_roles),
            "key_publication_count": len(profile.key_publications),
            "clinical_trial_count": len(profile.clinical_trial_involvement),
            "active_grant_count": len(profile.active_grants),
            "congress_activity_count": len(profile.congress_activity),
        }
    )


def _populate_hco(row: dict[str, str | int | float], profile: HcoProfile) -> None:
    row.update(
        {
            "profile_validated": "true",
            "profile_completeness": profile.profile_completeness,
            "profile_name": profile.name,
            "profile_country": profile.country,
            "organization_type": profile.organization_type.value if profile.organization_type else "",
            "affiliations": _join_unique(claim.value for claim in profile.notable_affiliations),
            "qualifications": _join_unique(claim.value for claim in profile.accreditations),
            "research_or_clinical_focus": _join_unique(
                claim.value for claim in (*profile.clinical_focus_areas, *profile.research_focus)
            ),
            "key_publication_count": 0,
            "clinical_trial_count": len(profile.active_clinical_trials),
            "active_grant_count": len(profile.institutional_grants),
            "congress_activity_count": 0,
        }
    )


def build_summary_rows(
    accounts: Sequence[Mapping[str, str]],
    results: Sequence[Mapping[str, Any]],
    artifacts: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, str | int | float]]:
    """Build rows from artifacts already ownership-validated by the batch engine."""
    results_by_id = {result["account_id"]: result for result in results}
    rows: list[dict[str, str | int | float]] = []
    for account in accounts:
        account_id = account["id"]
        result = results_by_id.get(account_id, {})
        row = _blank_row(account, result)
        artifact = artifacts.get(account_id, {})
        raw_profile = artifact.get("synthesized_profile")
        if raw_profile is not None:
            try:
                if account["account_type"] == "HCP":
                    profile = HcpProfile.model_validate(raw_profile)
                    _populate_hcp(row, profile)
                    sources = profile.sources_consulted
                else:
                    hco_profile = HcoProfile.model_validate(raw_profile)
                    _populate_hco(row, hco_profile)
                    sources = hco_profile.sources_consulted
            except ValidationError:
                pass
            else:
                source_urls = list(dict.fromkeys(source.url for source in sources if source.url))
                row["source_count"] = len(source_urls)
                row["source_urls"] = _join_unique(source_urls)

        rows.append(
            {
                column: row[column] if column in _NUMERIC_COLUMNS else _safe_text(row[column])
                for column in SUMMARY_COLUMNS
            }
        )
    return rows


def write_summary_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Atomically write the stable UTF-8 BOM CSV and return exact-byte metadata."""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=SUMMARY_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    payload = codecs.BOM_UTF8 + buffer.getvalue().encode("utf-8")
    written = atomic_write_bytes(path, payload)
    return {
        "status": "completed",
        "path": str(written.resolve()),
        "schema_version": CSV_SCHEMA_VERSION,
        "row_count": len(rows),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
