"""ci_trial_detail — full trial record with arms, endpoints, and results."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from .. import _clinical_trials
from ..models import TrialDetailRequest


class TrialDetailArgs(BaseModel):
    nct_id: str = Field(description="ClinicalTrials.gov identifier, e.g. 'NCT04567890'")


TOOL: dict[str, Any] = {
    "name": "ci_trial_detail",
    "description": (
        "Fetch the full record for a single clinical trial including arms, "
        "endpoints, eligibility criteria, current status, milestone dates, and "
        "posted results. Use after ci_scan_trials to drill into a specific trial."
    ),
    "args": TrialDetailArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    request = TrialDetailRequest.model_validate(arguments)
    result = _clinical_trials.get_trial_detail(request)
    ledger = result.model_dump(mode="json", exclude={"records"})
    if not result.records:
        payload: dict[str, Any] = {
            "error": result.error.message if result.error else "ClinicalTrials.gov detail failed",
            "coverage": result.status.value,
            "source_ledger": [ledger],
            "limitations": result.limitations,
        }
        return [{"type": "text", "text": json.dumps(payload, indent=2)}]

    detail = dict(result.records[0])
    trial = dict(detail["trial"])
    interventions = trial.get("interventions", [])
    trial["intervention_details"] = interventions
    trial["interventions"] = [item.get("name", "") for item in interventions]
    detail["trial"] = trial
    detail.update(
        {
            "coverage": result.status.value,
            "source_ledger": [ledger],
            "limitations": result.limitations,
        }
    )
    return [{"type": "text", "text": json.dumps(detail, indent=2)}]
