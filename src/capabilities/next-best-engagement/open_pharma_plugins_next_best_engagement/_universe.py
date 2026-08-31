"""In-memory universe store for HCP engagement data."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any

from .models import UniverseRow

if TYPE_CHECKING:
    from .models import EngagementPlan


@dataclass
class _SessionState:
    """One lock-protected snapshot of the loaded universe and derived plan."""

    lock: RLock = field(default_factory=RLock)
    universe: list[UniverseRow] = field(default_factory=list)
    rep_info: dict[str, dict[str, Any]] = field(default_factory=dict)
    universe_fingerprint: str | None = None
    load_generation: int = 0
    last_plan: EngagementPlan | None = None


_session_state = _SessionState()


def load_fixture() -> list[UniverseRow]:
    """Load the built-in sample universe CSV."""
    fixture_path = Path(__file__).parent / "fixtures" / "sample_universe.csv"
    return load_csv(str(fixture_path))


def load_csv(file_path: str) -> list[UniverseRow]:
    """Load a CSV file into the universe store."""
    rows: list[UniverseRow] = []
    seen_hcp_ids: set[str] = set()
    with open(file_path, newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            _coerce_row(raw)
            row = UniverseRow.model_validate(raw)
            if row.hcp_id in seen_hcp_ids:
                raise ValueError(f"Duplicate hcp_id: {row.hcp_id}")
            seen_hcp_ids.add(row.hcp_id)
            rows.append(row)

    rep_info = _build_rep_index(rows)
    fingerprint = fingerprint_universe(rows)
    with _session_state.lock:
        _session_state.universe = rows
        _session_state.rep_info = rep_info
        _session_state.universe_fingerprint = fingerprint
        _session_state.load_generation += 1
        _session_state.last_plan = None
    return list(rows)


def _coerce_row(raw: dict[str, Any]) -> None:
    """Convert string CSV values to the types UniverseRow expects."""
    for date_field in ("last_visit_date", "last_email_date", "last_meeting_date"):
        if date_field in raw and not raw[date_field]:
            raw[date_field] = None
    for bool_field in ("consent_email", "consent_phone"):
        if bool_field in raw:
            value = raw[bool_field].strip().lower()
            if not value:
                raw[bool_field] = None
            elif value in ("true", "1", "yes"):
                raw[bool_field] = True
            elif value in ("false", "0", "no"):
                raw[bool_field] = False
            else:
                raise ValueError(f"{bool_field} must be an explicit true or false value")
    for int_field in ("rep_max_visits_per_week", "visits_last_90d", "emails_last_90d"):
        if int_field in raw and raw[int_field]:
            raw[int_field] = int(raw[int_field])
        elif int_field in raw:
            raw[int_field] = 0

    _coerce_extras(raw)


_KNOWN_FIELDS = set(UniverseRow.model_fields.keys())


def _coerce_extras(raw: dict[str, Any]) -> None:
    """Best-effort type coercion for extra (pass-through) columns."""
    for key in raw:
        if key in _KNOWN_FIELDS:
            continue
        val = raw[key]
        if not isinstance(val, str) or not val:
            continue
        low = val.lower()
        if low in ("true", "false"):
            raw[key] = low == "true"
            continue
        try:
            raw[key] = int(val)
        except ValueError:
            try:
                raw[key] = float(val)
            except ValueError:
                pass


def _build_rep_index(universe: list[UniverseRow]) -> dict[str, dict[str, Any]]:
    """Build a rep lookup from the loaded universe.

    If different rows list different capacities for the same rep, the
    maximum is used.
    """
    rep_info: dict[str, dict[str, Any]] = {}
    for row in universe:
        if row.rep_id not in rep_info:
            rep_info[row.rep_id] = {
                "name": row.rep_name or row.rep_id,
                "territories": set(),
                "capacity": row.rep_max_visits_per_week,
            }
        else:
            rep_info[row.rep_id]["capacity"] = max(rep_info[row.rep_id]["capacity"], row.rep_max_visits_per_week)
        rep_info[row.rep_id]["territories"].add(row.territory_id)
    return rep_info


def fingerprint_universe(universe: list[UniverseRow]) -> str:
    payload = [row.model_dump(mode="json") for row in universe]
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def store_plan(plan: EngagementPlan, load_generation: int) -> bool:
    with _session_state.lock:
        if (
            load_generation != _session_state.load_generation
            or plan.universe_generation != _session_state.load_generation
            or plan.universe_fingerprint != _session_state.universe_fingerprint
        ):
            return False
        _session_state.last_plan = plan
        return True


def get_last_plan() -> EngagementPlan | None:
    with _session_state.lock:
        return _session_state.last_plan


def get_stored_plan() -> tuple[EngagementPlan | None, str | None, int]:
    with _session_state.lock:
        return _session_state.last_plan, _session_state.universe_fingerprint, _session_state.load_generation


def get_universe() -> list[UniverseRow]:
    with _session_state.lock:
        return list(_session_state.universe)


def get_rep_info() -> dict[str, dict[str, Any]]:
    with _session_state.lock:
        return dict(_session_state.rep_info)


def get_session_snapshot() -> tuple[list[UniverseRow], dict[str, dict[str, Any]], int]:
    with _session_state.lock:
        return list(_session_state.universe), dict(_session_state.rep_info), _session_state.load_generation


def get_summary() -> dict[str, Any]:
    """Return summary stats of the loaded universe."""
    with _session_state.lock:
        universe = list(_session_state.universe)
        rep_info = dict(_session_state.rep_info)
    if not universe:
        return {"loaded": False, "hcp_count": 0}

    tiers: dict[str, int] = {}
    territories: set[str] = set()
    specialties: set[str] = set()
    for row in universe:
        tiers[row.tier] = tiers.get(row.tier, 0) + 1
        territories.add(row.territory_id)
        specialties.add(row.specialty)

    extra_columns = []
    if universe:
        extra_columns = sorted(universe[0].model_extra.keys())

    return {
        "loaded": True,
        "hcp_count": len(universe),
        "rep_count": len(rep_info),
        "territory_count": len(territories),
        "territories": sorted(territories),
        "specialties": sorted(specialties),
        "tier_distribution": dict(sorted(tiers.items())),
        "extra_columns": extra_columns,
    }
