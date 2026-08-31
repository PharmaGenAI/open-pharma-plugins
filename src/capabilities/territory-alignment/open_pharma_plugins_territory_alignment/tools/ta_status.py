"""ta_status — data overview and scenario inventory."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class StatusArgs(BaseModel):
    pass


TOOL: dict[str, Any] = {
    "name": "ta_status",
    "description": (
        "Show the current state of loaded territory data: HCP and rep counts, "
        "segment distribution, geocoding coverage, unassigned HCPs, and a list "
        "of saved scenarios with timestamps. Call this first to confirm data is "
        "loaded before running ta_align or ta_cluster."
    ),
    "args": StatusArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from ..data import get_summary, is_loaded, load_all

    if not is_loaded():
        try:
            load_all()
        except ValueError as exc:
            return [{"type": "text", "text": json.dumps({"error": str(exc), "loaded": False})}]

    return [{"type": "text", "text": json.dumps(get_summary(), indent=2)}]
