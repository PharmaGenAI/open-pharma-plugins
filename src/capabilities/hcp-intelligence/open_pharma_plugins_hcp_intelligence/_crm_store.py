"""Shared CRM fixture store used by list_accounts, get_account, update_account.

Reads from fixtures/sample_accounts.csv (the account list) and a JSON sidecar
(enrichment data written by update_account).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_CSV_PATH = _FIXTURES_DIR / "sample_accounts.csv"


def _enrichment_path() -> Path:
    from shared.env import get_env
    from shared.filesystem import contained_path, ensure_private_dir

    root = ensure_private_dir(
        get_env(
            "OPEN_PHARMA_HCP_DATA_DIR",
            str(Path.home() / ".open-pharma-plugins" / "hcp-intelligence"),
        )
    )
    return contained_path(root, "enrichment_store.json")


def load_accounts() -> list[dict[str, str]]:
    with open(_CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    enrichment = _load_enrichment()
    for row in rows:
        row["status"] = enrichment.get(row["id"], {}).get("status", "pending")
    return rows


def get_account_by_id(account_id: str) -> dict | None:
    for row in load_accounts():
        if row["id"] == account_id:
            enrichment = _load_enrichment()
            entry = enrichment.get(account_id, {})
            row["status"] = entry.get("status", "pending")
            row["enrichment"] = entry.get("profile")
            row["last_enriched"] = entry.get("last_enriched")
            return row
    return None


def write_enrichment(account_id: str, profile_json: str, status: str) -> None:
    from datetime import datetime, timezone

    enrichment = _load_enrichment()
    enrichment[account_id] = {
        "status": status,
        "profile": json.loads(profile_json) if profile_json else None,
        "last_enriched": datetime.now(timezone.utc).isoformat(),
    }
    from shared.filesystem import atomic_write_json

    atomic_write_json(_enrichment_path(), enrichment)


def _load_enrichment() -> dict:
    path = _enrichment_path()
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}
