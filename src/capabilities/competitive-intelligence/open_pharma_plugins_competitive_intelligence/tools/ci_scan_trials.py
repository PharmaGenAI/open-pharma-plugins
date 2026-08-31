"""ci_scan_trials — scan ClinicalTrials.gov for competitor trials."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from .. import _clinical_trials
from ..models import TrialSearchRequest


class ScanTrialsArgs(BaseModel):
    query: str = Field(description="Drug name, company name, or NCT ID to search for")
    phase: str | None = Field(default=None, description="Filter by phase: '1', '2', '3', '4'")
    status: str | None = Field(
        default=None,
        description="Filter by status: RECRUITING, COMPLETED, ACTIVE_NOT_RECRUITING, etc.",
    )
    max_results: int = Field(default=20, ge=1, le=50, description="Max trials to return")


TOOL: dict[str, Any] = {
    "name": "ci_scan_trials",
    "description": (
        "Scan ClinicalTrials.gov for clinical trials matching a competitor drug "
        "or company. Returns a structured pipeline summary with trial phase, "
        "status, sponsor, enrollment, expected completion dates, and source coverage."
    ),
    "args": ScanTrialsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    request = TrialSearchRequest.model_validate(arguments)
    result = _clinical_trials.search_trials(request)
    trials = [_legacy_trial(record) for record in result.records]
    phase_summary: dict[str, int] = {}
    status_summary: dict[str, int] = {}
    for trial in trials:
        phase = trial["phase"] or "Not specified"
        phase_summary[phase] = phase_summary.get(phase, 0) + 1
        status = trial["status"]
        status_summary[status] = status_summary.get(status, 0) + 1

    output: dict[str, Any] = {
        "query": request.query,
        "total_found": result.total_available if result.total_available is not None else 0,
        "returned": len(trials),
        "phase_summary": phase_summary,
        "status_summary": status_summary,
        "trials": trials,
        "coverage": result.status.value,
        "source_ledger": [_source_ledger_entry(result)],
        "limitations": result.limitations,
    }
    if result.error is not None:
        output["error"] = result.error.message
    return [{"type": "text", "text": json.dumps(output, indent=2)}]


def _legacy_trial(record: dict[str, Any]) -> dict[str, Any]:
    interventions = record.get("interventions", [])
    return {
        "nct_id": record.get("nct_id", ""),
        "title": record.get("title", ""),
        "phase": record.get("phase", ""),
        "status": record.get("status", ""),
        "sponsor": record.get("sponsor", ""),
        "collaborators": record.get("collaborators", []),
        "conditions": record.get("conditions", [])[:3],
        "interventions": [item.get("name", "") for item in interventions[:3]],
        "intervention_details": interventions[:3],
        "enrollment": record.get("enrollment"),
        "start_date": record.get("start_date") or "",
        "primary_completion_date": record.get("primary_completion_date") or "",
        "completion_date": record.get("estimated_completion_date") or "",
        "results_posted": bool(record.get("has_results")),
        "source_url": record.get("source_url", ""),
    }


def _source_ledger_entry(result) -> dict[str, Any]:
    return result.model_dump(mode="json", exclude={"records"})
