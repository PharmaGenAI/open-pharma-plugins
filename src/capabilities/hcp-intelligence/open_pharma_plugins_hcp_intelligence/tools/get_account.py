"""get_account — simulates a CRM detail view for a single account."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GetAccountArgs(BaseModel):
    account_id: str = Field(description="Account ID (e.g. 'HCP-AU-001', 'HCO-SG-002')")


TOOL: dict[str, Any] = {
    "name": "get_account",
    "description": (
        "Get detailed information for a single HCP or HCO account by ID. Returns "
        "the account fields (name, specialty, country, institution) plus enrichment "
        "status and the full profile JSON if the account has been enriched. Use this "
        "to check an account before or after profiling."
    ),
    "args": GetAccountArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from .._crm_store import get_account_by_id

    account_id = arguments["account_id"]
    record = get_account_by_id(account_id)

    if record is None:
        return [{"type": "text", "text": json.dumps({"error": f"Account '{account_id}' not found"})}]

    output = {
        "id": record["id"],
        "name": record["name"],
        "specialty": record["specialty"],
        "country": record["country"],
        "account_type": record["account_type"],
        "institution": record.get("institution", ""),
        "status": record["status"],
        "last_enriched": record.get("last_enriched"),
        "enrichment": record.get("enrichment"),
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]
