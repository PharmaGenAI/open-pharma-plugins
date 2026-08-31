"""Output renderers — CSV file export and JSON serialisation."""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from shared.filesystem import ensure_private_dir, exclusive_write_text, remove_files

from .models import EngagementPlan, is_formula_unsafe_text


def render_csv(plan: EngagementPlan, output_dir: str) -> dict[str, str]:
    """Write the plan to a new pair of CSV files. Returns paths of written files."""
    from datetime import datetime, timezone

    engagements_content = _engagements_csv_content(plan)
    summary_content = _summary_csv_content(plan)
    out = ensure_private_dir(Path(output_dir))

    for _ in range(10):
        run_id = f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid4().hex}"
        engagements_path = out / f"engagements_{run_id}.csv"
        summary_path = out / f"plan_summary_{run_id}.csv"
        created_paths: list[Path] = []
        try:
            exclusive_write_text(engagements_path, engagements_content)
            created_paths.append(engagements_path)
            exclusive_write_text(summary_path, summary_content)
            created_paths.append(summary_path)
        except FileExistsError:
            remove_files(created_paths)
        except Exception:
            remove_files(created_paths)
            raise
        else:
            return {
                "engagements_csv": str(engagements_path),
                "summary_csv": str(summary_path),
            }

    raise FileExistsError("Could not allocate unique plan-render output paths.")


def render_json(plan: EngagementPlan) -> str:
    """Serialise the plan to a JSON string."""
    return plan.model_dump_json(indent=2)


_DEFAULT_ENGAGEMENT_COLUMNS = [
    "hcp_id",
    "hcp_name",
    "specialty",
    "tier",
    "territory_id",
    "rep_id",
    "rep_name",
    "action_type",
    "priority",
    "score",
    "suggested_window_start",
    "suggested_window_end",
    "rationale",
]


def _engagements_csv_content(plan: EngagementPlan) -> str:
    buffer = io.StringIO(newline="")
    rows = [_engagement_to_row(e) for e in plan.engagements]
    extra_columns = sorted({key for row in rows for key in row if key not in _DEFAULT_ENGAGEMENT_COLUMNS})
    fieldnames = [*_DEFAULT_ENGAGEMENT_COLUMNS, *extra_columns]

    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _summary_csv_content(plan: EngagementPlan) -> str:
    m = plan.metrics
    rows: list[dict[str, Any]] = []

    rows.append({"section": "overview", "metric": "total_universe", "value": m.total_universe})
    rows.append({"section": "overview", "metric": "total_eligible", "value": m.total_eligible})
    rows.append({"section": "overview", "metric": "total_planned", "value": m.total_planned})
    rows.append({"section": "overview", "metric": "coverage_pct", "value": m.coverage_pct})
    rows.append({"section": "overview", "metric": "no_action_count", "value": m.no_action_count})

    for tier, cov in sorted(m.coverage_by_tier.items()):
        rows.append(
            {
                "section": f"tier_{tier}",
                "metric": "target_pct",
                "value": cov.target_pct,
            }
        )
        rows.append(
            {
                "section": f"tier_{tier}",
                "metric": "actual_pct",
                "value": cov.actual_pct,
            }
        )
        rows.append({"section": f"tier_{tier}", "metric": "total", "value": cov.total})
        rows.append({"section": f"tier_{tier}", "metric": "planned", "value": cov.planned})
        rows.append({"section": f"tier_{tier}", "metric": "gap", "value": cov.gap})

    for ru in m.rep_utilization:
        rows.append(
            {
                "section": f"rep_{ru.rep_id}",
                "metric": "capacity",
                "value": ru.capacity,
            }
        )
        rows.append(
            {
                "section": f"rep_{ru.rep_id}",
                "metric": "assigned",
                "value": ru.assigned,
            }
        )
        rows.append(
            {
                "section": f"rep_{ru.rep_id}",
                "metric": "utilization_pct",
                "value": ru.utilization_pct,
            }
        )

    for channel, count in sorted(m.channel_mix.items()):
        rows.append({"section": "channel_mix", "metric": channel, "value": count})

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=["section", "metric", "value"])
    writer.writeheader()
    writer.writerows({key: _safe_csv_cell(value) for key, value in row.items()} for row in rows)
    return buffer.getvalue()


def _engagement_to_row(e: Any) -> dict[str, Any]:
    """Convert a PlannedEngagement to a flat dict for CSV, including extra columns."""
    data = e.model_dump()
    for key in ("suggested_window_start", "suggested_window_end"):
        val = data.get(key)
        if isinstance(val, date):
            data[key] = val.isoformat()
    return {key: _safe_csv_cell(value) for key, value in data.items()}


def _safe_csv_cell(value: Any) -> Any:
    """Prevent spreadsheet applications from interpreting exported text as a formula."""
    if isinstance(value, str) and is_formula_unsafe_text(value):
        return "'" + value
    return value
