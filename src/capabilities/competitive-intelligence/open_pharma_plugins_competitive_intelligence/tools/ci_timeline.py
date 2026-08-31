"""ci_timeline — project a run into a filtered, DOM-safe HTML timeline."""

from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timezone
from html import escape
from typing import Any

from pydantic import BaseModel, Field, model_validator

from shared.filesystem import json_for_html_script

from .._artifacts import (
    create_artifact_dir,
    sanitize_display_stem,
    write_artifact,
    write_manifest,
)
from .._runs import RunIntegrityError, create_run, load_run
from .._watchlist import WatchlistError, load_tracked_entities
from ..models import (
    ArtifactManifest,
    CIRun,
    RefreshRequest,
    SourceName,
    TimelineArtifactResult,
    TrackedEntity,
    aggregate_coverage,
)


class TimelineArgs(BaseModel):
    run_id: str | None = None
    entities: list[str] | None = Field(
        default=None,
        description="Tracked drug/company names, or omit for the full watchlist",
    )
    months_back: int = Field(default=12, ge=1, le=60)
    file_name: str | None = Field(default=None, description="Display filename stem")

    @model_validator(mode="after")
    def validate_collection_selector(self) -> "TimelineArgs":
        if self.run_id and self.entities:
            raise ValueError("run_id cannot be combined with entities")
        return self


TOOL: dict[str, Any] = {
    "name": "ci_timeline",
    "description": (
        "Generate a calendar-filtered, DOM-safe HTML timeline from an existing "
        "evidence run or one new watchlist collection pass."
    ),
    "args": TimelineArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    args = TimelineArgs.model_validate(arguments)
    extra_limitations: list[str] = []
    if args.run_id:
        try:
            run = load_run(args.run_id)
        except RunIntegrityError as error:
            return _error("invalid_run", str(error))
    else:
        try:
            watchlist = load_tracked_entities()
        except WatchlistError as error:
            return _error("invalid_watchlist", str(error))
        selected = watchlist
        if args.entities:
            by_name = {entity.name.casefold(): entity for entity in watchlist}
            selected = []
            seen = set()
            for name in args.entities:
                normalized = name.strip().casefold()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                entity = by_name.get(normalized)
                if entity is None:
                    entity = TrackedEntity(entity_type="drug", name=name)
                    extra_limitations.append(
                        f"identity_assumed_drug: unmatched legacy entity {name!r} was transient and was not tracked."
                    )
                selected.append(entity)
        if not selected:
            return _error(
                "no_entities",
                "Use ci_track to add an explicit drug or company, or pass entities.",
            )
        run = create_run(
            RefreshRequest(
                entities=selected,
                include_sections=["trials", "regulatory", "publications"],
            )
        )

    try:
        result = write_timeline_artifact(
            run,
            months_back=args.months_back,
            display_stem=args.file_name or "timeline",
            additional_limitations=extra_limitations,
        )
    except (OSError, ValueError) as error:
        return _error("artifact_write_failed", str(error))
    coverage = [
        {
            "entity": snapshot.entity.name,
            "source": result.source.value,
            "status": result.status.value,
            "record_count": len(result.records),
            "limitations": result.limitations,
        }
        for snapshot in run.entities
        for result in snapshot.sources.values()
        if result.source != SourceName.WEB
    ]
    coverage_status = aggregate_coverage(
        [
            result.status
            for snapshot in run.entities
            for result in snapshot.sources.values()
            if result.source != SourceName.WEB
        ]
    )
    output = {
        "success": True,
        "artifact_status": "complete",
        "coverage_status": coverage_status.value,
        "run_id": run.run_id,
        "run_records_sha256": run.records_sha256,
        "manifest_path": result.manifest_path,
        "html_path": result.html_path,
        "included_events": result.included_events,
        "excluded_old_events": result.excluded_old_events,
        "excluded_undated_events": result.excluded_undated_events,
        "total_events": result.included_events,
        "entities": [snapshot.entity.name for snapshot in run.entities],
        "coverage": coverage,
        "limitations": [*run.limitations, *extra_limitations],
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]


def write_timeline_artifact(
    run: CIRun,
    *,
    months_back: int,
    display_stem: str = "timeline",
    now: datetime | None = None,
    additional_limitations: list[str] | None = None,
) -> TimelineArtifactResult:
    generated_at = _utc(now)
    cutoff = calendar_month_cutoff(generated_at, months_back)
    all_events = events_from_run(run)
    included = []
    excluded_old = 0
    excluded_undated = 0
    for event in all_events:
        normalized, precision = normalize_event_date(event.get("raw_date"))
        if normalized is None:
            excluded_undated += 1
            continue
        if normalized < cutoff:
            excluded_old += 1
            continue
        included.append(
            {
                **event,
                "date": normalized.isoformat(),
                "date_precision": precision,
            }
        )
    included.sort(key=lambda event: (event["date"], event["entity"], event["id"]), reverse=True)

    stem = sanitize_display_stem(display_stem, default="timeline")
    output_dir = create_artifact_dir(run.run_id, now=generated_at)
    html_name = f"{stem}.html"
    coverage = [
        {
            "entity": snapshot.entity.name,
            "source": result.source.value,
            "status": result.status.value,
            "record_count": len(result.records),
            "limitations": result.limitations,
        }
        for snapshot in run.entities
        for result in snapshot.sources.values()
    ]
    html_record = write_artifact(
        output_dir,
        html_name,
        render_timeline_html(
            included,
            [snapshot.entity.name for snapshot in run.entities],
            coverage,
            [*run.limitations, *(additional_limitations or [])],
            excluded_old=excluded_old,
            excluded_undated=excluded_undated,
        ),
        media_type="text/html; charset=utf-8",
    )
    manifest = ArtifactManifest(
        artifact_id=output_dir.name,
        run_id=run.run_id,
        run_records_sha256=run.records_sha256,
        generated_at=generated_at,
        display_stem=stem,
        artifacts=[html_record],
    )
    manifest_path = write_manifest(output_dir, manifest)
    return TimelineArtifactResult(
        output_dir=str(output_dir),
        html_path=str(output_dir / html_name),
        manifest_path=str(manifest_path),
        manifest=manifest,
        included_events=len(included),
        excluded_old_events=excluded_old,
        excluded_undated_events=excluded_undated,
    )


def events_from_run(run: CIRun) -> list[dict[str, Any]]:
    events = []
    seen = set()
    for snapshot in run.entities:
        entity = snapshot.entity.name
        trials = snapshot.sources.get(SourceName.CLINICAL_TRIALS.value)
        if trials:
            for record in trials.records:
                for field, label in (
                    ("start_date", "Study start"),
                    ("primary_completion_date", "Primary completion"),
                    ("estimated_completion_date", "Study completion"),
                ):
                    if field in record:
                        _append_event(
                            events,
                            seen,
                            source=trials.source.value,
                            entity=entity,
                            event_type="trial",
                            raw_date=record.get(field),
                            title=f"{label}: {record.get('title', '')}",
                            detail=f"{record.get('phase', '')} · {record.get('status', '')} · {record.get('sponsor', '')}",
                            identifier=f"{record.get('nct_id', '')}:{field}",
                            source_url=record.get("source_url") or trials.source_url,
                        )
        openfda = snapshot.sources.get(SourceName.OPENFDA.value)
        if openfda:
            for record in openfda.records:
                _append_event(
                    events,
                    seen,
                    source=openfda.source.value,
                    entity=entity,
                    event_type=str(record.get("event_type") or "other"),
                    raw_date=record.get("date"),
                    title=f"{str(record.get('event_type') or 'Regulatory event').replace('_', ' ').title()}: {record.get('application_number', '')}",
                    detail=str(record.get("description", "")),
                    identifier=f"{record.get('application_number', '')}:{record.get('submission', '')}",
                    source_url=record.get("source_url") or openfda.source_url,
                )
        dailymed = snapshot.sources.get(SourceName.DAILYMED.value)
        if dailymed:
            for record in dailymed.records:
                _append_event(
                    events,
                    seen,
                    source=dailymed.source.value,
                    entity=entity,
                    event_type="label_change",
                    raw_date=record.get("published_date"),
                    title=f"Label version {record.get('spl_version', '')}: {record.get('title', '')}",
                    detail="DailyMed label history",
                    identifier=f"{record.get('set_id', '')}:{record.get('spl_version', '')}",
                    source_url=record.get("source_url") or dailymed.source_url,
                )
        pubmed = snapshot.sources.get(SourceName.PUBMED.value)
        if pubmed:
            for record in pubmed.records:
                _append_event(
                    events,
                    seen,
                    source=pubmed.source.value,
                    entity=entity,
                    event_type="publication",
                    raw_date=record.get("pub_date"),
                    title=str(record.get("title", "")),
                    detail=f"{record.get('journal', '')} · PMID {record.get('pmid', '')}",
                    identifier=str(record.get("pmid", "")),
                    source_url=record.get("source_url") or pubmed.source_url,
                )
    return events


def _append_event(
    events,
    seen,
    *,
    source,
    entity,
    event_type,
    raw_date,
    title,
    detail,
    identifier,
    source_url,
):
    key = (source, entity, event_type, str(raw_date or ""), identifier)
    if key in seen:
        return
    seen.add(key)
    events.append(
        {
            "raw_date": raw_date,
            "entity": entity,
            "type": event_type,
            "title": title,
            "detail": detail,
            "id": identifier,
            "source": source,
            "source_url": source_url,
        }
    )


def normalize_event_date(value: Any) -> tuple[date | None, str]:
    if isinstance(value, datetime):
        return value.date(), "day"
    if isinstance(value, date):
        return value, "day"
    if not isinstance(value, str):
        return None, "unknown"
    text = value.strip()
    for parse_value, pattern, precision in (
        (text, "%Y-%m-%d", "day"),
        (f"{text}-01", "%Y-%m-%d", "month"),
        (f"{text}-01-01", "%Y-%m-%d", "year"),
        (text, "%b %d, %Y", "day"),
        (text, "%B %d, %Y", "day"),
    ):
        try:
            return datetime.strptime(parse_value, pattern).date(), precision
        except ValueError:
            continue
    return None, "unknown"


def calendar_month_cutoff(now: datetime, months_back: int) -> date:
    effective = _utc(now)
    month_index = effective.year * 12 + (effective.month - 1) - months_back
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(effective.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def render_timeline_html(
    events: list[dict[str, Any]],
    entity_names: list[str],
    coverage: list[dict[str, Any]],
    limitations: list[str],
    *,
    excluded_old: int,
    excluded_undated: int,
) -> str:
    title = f"CI Timeline: {', '.join(entity_names[:3])}"
    data = json_for_html_script(
        {
            "events": events,
            "coverage": coverage,
            "limitations": limitations,
            "excluded_old": excluded_old,
            "excluded_undated": excluded_undated,
        }
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>body{{font-family:system-ui;background:#f7f9fc;color:#172033;margin:0}}header{{background:#203a5f;color:white;padding:1.5rem}}main{{max-width:900px;margin:2rem auto;padding:0 1rem}}button{{margin:.2rem;padding:.4rem .8rem}}.card{{background:white;border:1px solid #dce3ed;border-left:5px solid #758399;padding:1rem;margin:1rem 0}}.kind-trial{{border-left-color:#2879bd}}.kind-approval{{border-left-color:#27834b}}.kind-label_change{{border-left-color:#b18200}}.kind-publication{{border-left-color:#7b47a6}}.meta{{color:#667085;font-size:.85rem}}.detail{{white-space:pre-wrap}}.coverage{{background:white;border:1px solid #dce3ed;padding:.7rem;margin:.5rem 0}}</style>
</head><body><header><h1>{escape(title)}</h1><p>{len(events)} included events</p></header>
<main><section><h2>Coverage and limitations</h2><div id="coverage"></div><ul id="limitations"></ul></section>
<section><h2>Filters</h2><div id="filters"></div><p id="count"></p><div id="timeline"></div></section></main>
<script id="timeline-data" type="application/json">{data}</script>
<script>
const DATA = JSON.parse(document.getElementById('timeline-data').textContent);
const KIND_CLASSES = {{trial:'kind-trial',approval:'kind-approval',label_change:'kind-label_change',publication:'kind-publication'}};
const active = new Set(DATA.events.map(event => event.type));
function node(tag, className, text) {{
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== undefined) value.textContent = text;
  return value;
}}
function renderCoverage() {{
  const rows = DATA.coverage.map(row => node('div','coverage',`${{row.entity}} · ${{row.source}}: ${{row.status}} (${{row.record_count}} returned)`));
  document.getElementById('coverage').replaceChildren(...rows);
  const items = [...DATA.limitations, `Excluded old events: ${{DATA.excluded_old}}`, `Excluded undated events: ${{DATA.excluded_undated}}`]
    .map(value => node('li','',value));
  document.getElementById('limitations').replaceChildren(...items);
}}
function render() {{
  const filtered = DATA.events.filter(event => active.has(event.type));
  document.getElementById('count').textContent = `Showing ${{filtered.length}} of ${{DATA.events.length}} events`;
  const cards = filtered.map(event => {{
    const card = node('article','card ' + (KIND_CLASSES[event.type] || ''),undefined);
    card.dataset.type = event.type;
    card.append(node('div','meta',`${{event.date}} · ${{event.date_precision}} · ${{event.source}}`));
    card.append(node('h3','',event.title));
    card.append(node('div','meta',`${{event.entity}} · ${{event.id}}`));
    card.append(node('p','detail',event.detail));
    return card;
  }});
  document.getElementById('timeline').replaceChildren(...cards);
}}
function initFilters() {{
  const buttons = [...active].sort().map(type => {{
    const button = node('button','',type.replaceAll('_',' '));
    button.dataset.type = type;
    button.addEventListener('click', () => {{
      if (active.has(type)) active.delete(type); else active.add(type);
      render();
    }});
    return button;
  }});
  document.getElementById('filters').replaceChildren(...buttons);
}}
renderCoverage(); initFilters(); render();
</script></body></html>"""


def _error(code: str, message: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": json.dumps({"error": {"code": code, "message": message}}, indent=2),
        }
    ]


def _utc(value: datetime | None) -> datetime:
    now = value or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)
