"""update_account — simulates a CRM writeback of enrichment results."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UpdateAccountArgs(BaseModel):
    account_id: str = Field(description="Account ID to update (e.g. 'HCP-AU-001')")
    status: str = Field(
        description="New enrichment status: 'enriched' (profile complete), 'failed' (enrichment unsuccessful), or 'pending' (reset)"
    )
    profile_json: str | None = Field(
        default=None,
        description=(
            "The full HcpProfile or HcoProfile as a JSON string. Required when "
            "status is 'enriched'. This is the structured profile output from the "
            "intelligence workflow."
        ),
    )


TOOL: dict[str, Any] = {
    "name": "update_account",
    "description": (
        "Write enrichment results back to an account, simulating a CRM writeback. "
        "Pass the account ID, the new status ('enriched' or 'failed'), and the "
        "full profile JSON. The profile is stored alongside the account and can be "
        "retrieved later via get_account. Use this after completing the HCP/HCO "
        "profiling workflow to persist the results."
    ),
    "args": UpdateAccountArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from .._crm_store import get_account_by_id, write_enrichment

    account_id = arguments["account_id"]
    status = arguments["status"]
    profile_json = arguments.get("profile_json")

    record = get_account_by_id(account_id)
    if record is None:
        return [{"type": "text", "text": json.dumps({"error": f"Account '{account_id}' not found"})}]

    if status not in ("enriched", "failed", "pending"):
        return [
            {
                "type": "text",
                "text": json.dumps({"error": f"Invalid status '{status}'. Use 'enriched', 'failed', or 'pending'."}),
            }
        ]

    if status == "enriched" and not profile_json:
        return [{"type": "text", "text": json.dumps({"error": "profile_json is required when status is 'enriched'"})}]

    if profile_json:
        try:
            json.loads(profile_json)
        except json.JSONDecodeError as e:
            return [{"type": "text", "text": json.dumps({"error": f"Invalid profile_json: {e}"})}]

    write_enrichment(account_id, profile_json or "", status)

    return [
        {
            "type": "text",
            "text": json.dumps(
                {
                    "success": True,
                    "account_id": account_id,
                    "name": record["name"],
                    "status": status,
                    "message": f"Account {account_id} ({record['name']}) updated to '{status}'.",
                },
                indent=2,
            ),
        }
    ]
