"""ci_scan_regulatory — scan openFDA and DailyMed with explicit coverage."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from .. import _regulatory
from ..models import RegulatorySearchRequest


class ScanRegulatoryArgs(BaseModel):
    drug_name: str = Field(description="Drug brand or generic name to search")
    date_from: str | None = Field(default=None, description="Start date (ISO, e.g. '2025-01-01')")
    date_to: str | None = Field(default=None, description="End date (ISO, e.g. '2026-08-22')")
    include_label_history: bool = Field(
        default=True,
        description="Include DailyMed SPL label version history",
    )
    max_results: int = Field(default=20, ge=1, le=50, description="Max events to return")


TOOL: dict[str, Any] = {
    "name": "ci_scan_regulatory",
    "description": (
        "Scan openFDA for approvals, supplements, and submission status changes, "
        "with optional DailyMed label-version history. Returns chronological events "
        "and explicit coverage for each requested source."
    ),
    "args": ScanRegulatoryArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    request = RegulatorySearchRequest.model_validate(arguments)
    result = _regulatory.scan_regulatory(request)
    events = []
    for event in result.events:
        record = event.model_dump(mode="json")
        record["type"] = record["event_type"]
        events.append(record)
    events.sort(key=lambda item: item.get("date") or "", reverse=True)
    type_summary: dict[str, int] = {}
    for event in events:
        event_type = event["event_type"]
        type_summary[event_type] = type_summary.get(event_type, 0) + 1
    ledger = [result.openfda.model_dump(mode="json", exclude={"records"})]
    if result.dailymed is not None:
        ledger.append(result.dailymed.model_dump(mode="json", exclude={"records"}))
    payload: dict[str, Any] = {
        "drug_name": request.drug_name,
        "total_events": len(events),
        "type_summary": type_summary,
        "events": events,
        "coverage": result.coverage.value,
        "source_ledger": ledger,
        "limitations": result.limitations,
    }
    if result.label_history:
        payload["label_history"] = [entry.model_dump(mode="json") for entry in result.label_history]
        payload["label_versions"] = len(result.label_history)
    return [{"type": "text", "text": json.dumps(payload, indent=2)}]
