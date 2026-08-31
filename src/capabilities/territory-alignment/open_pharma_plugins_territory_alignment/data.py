"""Data loading, scenario persistence, and fixture fallback."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

from shared.filesystem import ensure_private_dir, exclusive_write_text, remove_files

from .models import HCP, Constraint, CurrentAssignment, NewHire, Rep, ScenarioResult


def _data_dir() -> Path:
    from shared.env import get_env

    custom = get_env("OPEN_PHARMA_TA_DATA_DIR", "")
    if custom:
        return Path(custom)
    return Path(__file__).parent / "fixtures"


def _scenarios_dir() -> Path:
    from shared.env import get_env

    custom = get_env("OPEN_PHARMA_TA_SCENARIOS_DIR", "")
    if custom:
        d = Path(custom)
    else:
        d = Path.home() / ".open-pharma-plugins" / "territory-alignment" / "scenarios"
    return ensure_private_dir(d)


# -- state --

_hcps: list[HCP] = []
_reps: list[Rep] = []
_current_alignment: list[CurrentAssignment] = []
_constraints: list[Constraint] = []
_loaded: bool = False
_loaded_source: Path | None = None


def is_loaded() -> bool:
    return _loaded and _loaded_source == _data_dir().resolve()


def load_all() -> dict[str, Any]:
    """Load all CSV files from the data directory."""
    global _hcps, _reps, _current_alignment, _constraints, _loaded, _loaded_source

    data_dir = _data_dir()
    required = ("hcps.csv", "reps.csv", "current_alignment.csv", "constraints.csv")
    missing = sorted(name for name in required if not (data_dir / name).is_file())
    if missing:
        _loaded = False
        raise ValueError(f"missing required territory data files: {', '.join(missing)}")
    _hcps = _load_hcps(data_dir / "hcps.csv")
    _reps = _load_reps(data_dir / "reps.csv")
    _current_alignment = _load_alignment(data_dir / "current_alignment.csv")
    _constraints = _load_constraints(data_dir / "constraints.csv")
    _validate_loaded_data(_hcps, _reps, _current_alignment, _constraints)
    _loaded = True
    _loaded_source = data_dir.resolve()

    return get_summary()


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _validate_loaded_data(
    hcps: list[HCP],
    reps: list[Rep],
    alignment: list[CurrentAssignment],
    constraints: list[Constraint],
) -> None:
    if not hcps:
        raise ValueError("hcps.csv must contain at least one HCP")
    if not reps:
        raise ValueError("reps.csv must contain at least one rep")
    duplicate_hcps = _duplicates([hcp.hcp_id for hcp in hcps])
    if duplicate_hcps:
        raise ValueError(f"duplicate HCP identifiers: {', '.join(duplicate_hcps)}")
    duplicate_reps = _duplicates([rep.rep_id for rep in reps])
    if duplicate_reps:
        raise ValueError(f"duplicate rep identifiers: {', '.join(duplicate_reps)}")
    duplicate_alignment = _duplicates([item.hcp_id for item in alignment])
    if duplicate_alignment:
        raise ValueError(f"duplicate current-alignment HCP identifiers: {', '.join(duplicate_alignment)}")

    hcp_ids = {hcp.hcp_id for hcp in hcps}
    rep_ids = {rep.rep_id for rep in reps}
    for item in alignment:
        if item.hcp_id not in hcp_ids:
            raise ValueError(f"current alignment references unknown HCP {item.hcp_id!r}")
        if item.primary_rep not in rep_ids:
            raise ValueError(f"current alignment references unknown primary rep {item.primary_rep!r}")
        if item.secondary_rep and item.secondary_rep not in rep_ids:
            raise ValueError(f"current alignment references unknown secondary rep {item.secondary_rep!r}")

    account_ids = {hcp.account_id for hcp in hcps if hcp.account_id}
    for constraint in constraints:
        if constraint.type == "account_grouping":
            account_id = constraint.scope.split(":", 1)[1]
            if account_id not in account_ids:
                raise ValueError(f"account_grouping references unknown account {account_id!r}")


def get_hcps() -> list[HCP]:
    return list(_hcps)


def get_reps(
    vacancies: list[str] | None = None,
    new_hires: list[NewHire] | None = None,
) -> list[Rep]:
    """Return reps with vacancies removed and new hires added."""
    vacancy_set = set(vacancies or [])
    result = [r for r in _reps if r.rep_id not in vacancy_set]
    for nh in new_hires or []:
        result.append(
            Rep(
                rep_id=nh.rep_id,
                name=nh.name,
                base_lat=nh.base_lat,
                base_lng=nh.base_lng,
                product_expertise=nh.product_expertise,
                max_weekly_hours=nh.max_weekly_hours,
                max_daily_calls=nh.max_daily_calls,
                available_days=nh.available_days,
            )
        )
    return result


def get_current_alignment() -> list[CurrentAssignment]:
    return list(_current_alignment)


def get_constraints() -> list[Constraint]:
    return list(_constraints)


def get_summary() -> dict[str, Any]:
    if not _loaded:
        return {"loaded": False}

    segments: dict[str, int] = {}
    geo_count = 0
    for h in _hcps:
        segments[h.segment] = segments.get(h.segment, 0) + 1
        if h.lat is not None and h.lng is not None:
            geo_count += 1

    assigned_hcps = {a.hcp_id for a in _current_alignment}
    unassigned = [h.hcp_id for h in _hcps if h.hcp_id not in assigned_hcps]

    return {
        "loaded": True,
        "data_source": str(_data_dir()),
        "hcp_count": len(_hcps),
        "rep_count": len(_reps),
        "alignment_count": len(_current_alignment),
        "constraint_count": len(_constraints),
        "segments": segments,
        "geocoded_pct": round(geo_count / max(len(_hcps), 1) * 100, 1),
        "unassigned_hcps": unassigned,
        "scenarios": list_scenarios(),
        **get_data_provenance(),
    }


def get_data_provenance() -> dict[str, Any]:
    data_dir = _data_dir()
    names = ("constraints.csv", "current_alignment.csv", "hcps.csv", "reps.csv")
    fingerprints = {
        name: hashlib.sha256((data_dir / name).read_bytes()).hexdigest()
        for name in names
        if (data_dir / name).is_file()
    }
    fixture_dir = Path(__file__).parent / "fixtures"
    return {
        "data_source": str(data_dir),
        "fixture_data": data_dir.resolve() == fixture_dir.resolve(),
        "input_fingerprints": fingerprints,
    }


def scenarios_share_input_universe(scenarios: list[dict[str, Any]]) -> bool:
    """Return whether scenarios were derived from the same immutable source inputs."""
    identities: list[str] = []
    for scenario in scenarios:
        fingerprints = scenario.get("metadata", {}).get("input_fingerprints", {})
        if fingerprints:
            source_input = {"input_fingerprints": fingerprints}
        else:
            snapshot = scenario["input_snapshot"]
            source_input = {key: snapshot[key] for key in ("hcps", "reps", "current_alignment", "constraints")}
        encoded = json.dumps(source_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        identities.append(hashlib.sha256(encoded.encode("utf-8")).hexdigest())
    return len(set(identities)) <= 1


# -- scenarios --


def _sanitize_scenario_name(name: str) -> str:
    import re

    clean = re.sub(r"[^a-zA-Z0-9_\-]", "_", name).strip("_")[:80]
    if not clean or not any(character.isalnum() for character in clean):
        raise ValueError("scenario name must contain at least one alphanumeric character")
    if clean != name:
        clean = f"{clean}-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]}"
    return clean


def save_scenario(name: str, result: dict[str, Any]) -> str:
    from .reporting import build_report_model, render_report_html, scenario_artifact_paths

    safe = _sanitize_scenario_name(name)
    d = _scenarios_dir()
    paths = scenario_artifact_paths(d, safe)
    result.setdefault("metadata", {})["artifacts"] = paths.as_metadata()
    validated = ScenarioResult.model_validate(result).model_dump(mode="json")
    scenario_name = validated["scenario_name"]
    report_model = build_report_model(
        {scenario_name: validated},
        [scenario_name],
        show_movements=False,
        show_rep_bases=True,
    )
    report_html = render_report_html(report_model, [scenario_name], basemap="offline")
    created: list[Path] = []
    try:
        exclusive_write_text(paths.scenario_json, json.dumps(validated, indent=2, ensure_ascii=False))
        created.append(paths.scenario_json)
        created.extend(_write_scenario_csvs(d, safe, validated))
        exclusive_write_text(paths.primary_report, report_html)
        created.append(paths.primary_report)
    except Exception:
        remove_files(created)
        raise
    return str(paths.scenario_json)


def _write_scenario_csvs(directory: Path, name: str, result: dict[str, Any]) -> list[Path]:
    created: list[Path] = []
    try:
        assignments = result.get("assignments", [])
        if assignments:
            assign_cols = [
                "hcp_id",
                "hcp_name",
                "primary_rep",
                "previous_rep",
                "is_changed",
                "change_reason",
                "estimated_travel_min",
                "estimated_annual_visits",
                "segment",
                "tier",
            ]
            a_path = directory / f"{name}_assignments.csv"
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=assign_cols, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(_formula_safe_row(row) for row in assignments)
            exclusive_write_text(a_path, buffer.getvalue())
            created.append(a_path)

        territories = result.get("territory_summary", [])
        if territories:
            territory_cols = [
                "rep_id",
                "rep_name",
                "hcp_count",
                "total_potential",
                "workload_hours_weekly",
                "travel_hours_weekly",
                "segment_high",
                "segment_medium",
                "segment_low",
                "relationships_kept",
                "relationships_new",
            ]
            t_path = directory / f"{name}_territory_summary.csv"
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=territory_cols, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(_formula_safe_row(row) for row in territories)
            exclusive_write_text(t_path, buffer.getvalue())
            created.append(t_path)
        return created
    except Exception:
        remove_files(created)
        raise


def _formula_safe_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _formula_safe_cell(value) for key, value in row.items()}


def _formula_safe_cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value


def load_scenario(name: str) -> dict[str, Any] | None:
    path = _scenarios_dir() / f"{_sanitize_scenario_name(name)}.json"
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return ScenarioResult.model_validate(raw).model_dump(mode="json")
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"Scenario {name!r} is invalid or incompatible; retain needed exports and regenerate it."
            ) from exc
    return None


def list_scenarios() -> list[dict[str, Any]]:
    from .reporting import scenario_artifact_paths

    result: list[dict[str, Any]] = []
    for f in sorted(_scenarios_dir().glob("*.json")):
        try:
            scenario = ScenarioResult.model_validate_json(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            result.append(
                {
                    "name": f.stem,
                    "file": str(f),
                    "created_at": "",
                    "run_id": "",
                    "status": "invalid",
                    "error": str(exc),
                }
            )
            continue
        metadata = scenario.metadata
        report_path = scenario_artifact_paths(f.parent, f.stem).primary_report
        result.append(
            {
                "name": scenario.scenario_name,
                "file": str(f),
                "report_file": str(report_path) if report_path.is_file() else "",
                "created_at": metadata.get("created_at", ""),
                "run_id": metadata.get("run_id", ""),
                "plugin_version": metadata.get("plugin_version", ""),
                "status": "valid",
            }
        )
    return sorted(result, key=lambda item: (item["created_at"], item["name"]), reverse=True)


# -- CSV loaders --


def _load_hcps(path: Path) -> list[HCP]:
    if not path.exists():
        return []
    rows: list[HCP] = []
    with open(path, newline="") as f:
        for raw in csv.DictReader(f):
            _coerce_hcp(raw)
            rows.append(HCP.model_validate(raw))
    return rows


def _coerce_hcp(raw: dict[str, Any]) -> None:
    for field in ("lat", "lng", "annual_potential"):
        if field in raw:
            val = raw[field]
            if not val:
                raw[field] = None if field in ("lat", "lng") else 0.0
            else:
                raw[field] = float(val)
    for field in ("consent_email", "consent_phone", "consent_visit"):
        if field in raw:
            raw[field] = raw[field].lower() in ("true", "1", "yes")
    if "tier" in raw and raw["tier"]:
        raw["tier"] = int(raw["tier"])
    if "product_requirements" in raw:
        val = raw["product_requirements"]
        raw["product_requirements"] = [v.strip() for v in val.split(";")] if val else []
    _coerce_extras(raw, _HCP_KNOWN_FIELDS)


_HCP_KNOWN_FIELDS = set(HCP.model_fields.keys())


def _load_reps(path: Path) -> list[Rep]:
    if not path.exists():
        return []
    rows: list[Rep] = []
    with open(path, newline="") as f:
        for raw in csv.DictReader(f):
            _coerce_rep(raw)
            rows.append(Rep.model_validate(raw))
    return rows


def _coerce_rep(raw: dict[str, Any]) -> None:
    for field in ("base_lat", "base_lng", "max_weekly_hours"):
        if field in raw and raw[field]:
            raw[field] = float(raw[field])
    if "max_daily_calls" in raw and raw["max_daily_calls"]:
        raw["max_daily_calls"] = int(raw["max_daily_calls"])
    for list_field in ("product_expertise", "available_days"):
        if list_field in raw:
            val = raw[list_field]
            raw[list_field] = [v.strip() for v in val.split(";")] if val else []


def _load_alignment(path: Path) -> list[CurrentAssignment]:
    if not path.exists():
        return []
    rows: list[CurrentAssignment] = []
    with open(path, newline="") as f:
        for raw in csv.DictReader(f):
            rows.append(CurrentAssignment.model_validate(raw))
    return rows


def _load_constraints(path: Path) -> list[Constraint]:
    if not path.exists():
        return []
    rows: list[Constraint] = []
    with open(path, newline="") as f:
        for raw in csv.DictReader(f):
            rows.append(Constraint.model_validate(raw))
    return rows


def _coerce_extras(raw: dict[str, Any], known: set[str]) -> None:
    for key in raw:
        if key in known:
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
