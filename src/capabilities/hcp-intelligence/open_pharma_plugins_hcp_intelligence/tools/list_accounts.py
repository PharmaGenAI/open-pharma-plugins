"""list_accounts — simulates a CRM list view over sample account data."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ListAccountsArgs(BaseModel):
    country: str | None = Field(default=None, description="Filter by country (e.g. 'Australia', 'Singapore')")
    account_type: str | None = Field(default=None, description="Filter by account type: 'HCP' or 'HCO'")
    specialty: str | None = Field(default=None, description="Filter by specialty (substring match, e.g. 'Oncology')")
    status: str | None = Field(
        default=None, description="Filter by enrichment status: 'pending', 'enriched', or 'failed'"
    )


TOOL: dict[str, Any] = {
    "name": "list_accounts",
    "description": (
        "List HCP and HCO accounts from the sample CRM data. Returns a table of "
        "accounts with id, name, specialty, country, account_type, institution, and "
        "enrichment status. Supports optional filters by country, account_type, "
        "specialty, and status. Use this to see which accounts need profiling."
    ),
    "args": ListAccountsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from .._crm_store import load_accounts

    rows = load_accounts()

    if arguments.get("country"):
        val = arguments["country"].lower()
        rows = [r for r in rows if r["country"].lower() == val]
    if arguments.get("account_type"):
        val = arguments["account_type"].upper()
        rows = [r for r in rows if r["account_type"].upper() == val]
    if arguments.get("specialty"):
        val = arguments["specialty"].lower()
        rows = [r for r in rows if val in r["specialty"].lower()]
    if arguments.get("status"):
        val = arguments["status"].lower()
        rows = [r for r in rows if r["status"].lower() == val]

    output = {
        "total": len(rows),
        "accounts": [
            {
                "id": r["id"],
                "name": r["name"],
                "specialty": r["specialty"],
                "country": r["country"],
                "account_type": r["account_type"],
                "institution": r.get("institution", ""),
                "status": r["status"],
            }
            for r in rows
        ],
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]
