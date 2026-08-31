"""load_universe — load HCP engagement data from CSV or built-in fixtures."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LoadUniverseArgs(BaseModel):
    source: str = Field(
        default="fixture",
        description=("Data source: 'fixture' to load built-in sample data, or an absolute path to a CSV file."),
    )


TOOL: dict[str, Any] = {
    "name": "load_universe",
    "description": (
        "Load the HCP engagement universe from a CSV file or built-in fixture "
        "data. The CSV must have columns: hcp_id, hcp_name, territory_id, "
        "rep_id. Optional columns (tier, specialty, consent_email, "
        "consent_phone, last_visit_date, etc.) enrich scoring. Any extra "
        "columns are preserved and passed through to the output plan. "
        "Call this before recommend_engagements."
    ),
    "args": LoadUniverseArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    import os

    from .._universe import get_summary, load_csv, load_fixture

    source = arguments.get("source", "fixture")

    try:
        if source == "fixture":
            load_fixture()
        else:
            if not os.path.isfile(source):
                return [{"type": "text", "text": json.dumps({"error": f"File not found: {source}"})}]
            load_csv(source)
    except (OSError, TypeError, ValueError) as exc:
        return [{"type": "text", "text": json.dumps({"error": f"Invalid universe: {exc}"})}]

    summary = get_summary()
    return [{"type": "text", "text": json.dumps(summary, indent=2)}]
