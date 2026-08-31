"""ci_report — project immutable evidence runs into safe briefing artifacts."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from html import escape
from typing import Any, Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

from shared.filesystem import sanitize_url

from .._artifacts import (
    create_artifact_dir,
    safe_csv_cell,
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
    ReportArtifactResult,
    SectionName,
    SourceName,
    TrackedEntity,
    aggregate_coverage,
)

_SECTION_ORDER: tuple[SectionName, ...] = ("trials", "regulatory", "news", "publications")


class ReportArgs(BaseModel):
    run_id: str | None = None
    focus: str | None = Field(
        default=None,
        description="Specific drug/company to report on, or omit for full watchlist",
    )
    include_sections: list[SectionName] = Field(default_factory=lambda: list(_SECTION_ORDER))
    file_name: str | None = Field(default=None, description="Display filename stem")

    @model_validator(mode="after")
    def validate_collection_selector(self) -> "ReportArgs":
        if self.run_id and self.focus:
            raise ValueError("run_id cannot be combined with focus")
        return self


TOOL: dict[str, Any] = {
    "name": "ci_report",
    "description": (
        "Generate immutable JSON, HTML, and CSV briefing artifacts from an existing "
        "evidence run or from one new watchlist collection pass."
    ),
    "args": ReportArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    args = ReportArgs.model_validate(arguments)
    extra_limitations: list[str] = []
    if args.run_id:
        try:
            run = load_run(args.run_id)
        except RunIntegrityError as error:
            return _error("invalid_run", str(error))
    else:
        try:
            entities = load_tracked_entities()
        except WatchlistError as error:
            return _error("invalid_watchlist", str(error))
        if args.focus:
            match = next(
                (entity for entity in entities if entity.name.casefold() == args.focus.casefold()),
                None,
            )
            if match is None:
                match = TrackedEntity(entity_type="drug", name=args.focus)
                extra_limitations.append(
                    "identity_assumed_drug: unmatched legacy focus was treated as a transient drug and was not tracked."
                )
            entities = [match]
        if not entities:
            return _error(
                "no_entities",
                "Use ci_track to add an explicit drug or company, or pass focus.",
            )
        run = create_run(RefreshRequest(entities=entities, include_sections=args.include_sections))

    try:
        result = write_report_artifacts(
            run,
            include_sections=args.include_sections,
            display_stem=args.file_name or "briefing",
            additional_limitations=extra_limitations,
        )
    except (OSError, ValueError) as error:
        return _error("artifact_write_failed", str(error))
    coverage = _coverage_rows(run, args.include_sections)
    coverage_status = aggregate_coverage([row["status"] for row in coverage])
    output = {
        "success": True,
        "artifact_status": "complete",
        "coverage_status": coverage_status.value,
        "run_id": run.run_id,
        "run_records_sha256": run.records_sha256,
        "manifest_path": result.manifest_path,
        "json_path": result.json_path,
        "html_path": result.html_path,
        "csv_files": result.csv_files,
        "entities_covered": [snapshot.entity.name for snapshot in run.entities],
        "sections": args.include_sections,
        "coverage": [{**row, "status": row["status"].value} for row in coverage],
        "limitations": [*run.limitations, *extra_limitations],
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]


def write_report_artifacts(
    run: CIRun,
    *,
    include_sections: Sequence[SectionName] | None = None,
    display_stem: str = "briefing",
    now: datetime | None = None,
    additional_limitations: Sequence[str] = (),
) -> ReportArtifactResult:
    generated_at = _utc(now)
    sections = _normalize_sections(include_sections or run.request.include_sections)
    stem = sanitize_display_stem(display_stem, default="briefing")
    output_dir = create_artifact_dir(run.run_id, now=generated_at)
    report = _project_report(run, sections, generated_at, additional_limitations)
    artifacts = []

    json_name = f"{stem}.json"
    artifacts.append(
        write_artifact(
            output_dir,
            json_name,
            json.dumps(report, indent=2, ensure_ascii=False),
            media_type="application/json",
        )
    )
    html_name = f"{stem}.html"
    artifacts.append(
        write_artifact(
            output_dir,
            html_name,
            _render_html(report),
            media_type="text/html; charset=utf-8",
        )
    )

    csv_paths = []
    for section in sections:
        rows = _csv_rows(run, section)
        if not rows:
            continue
        csv_name = f"{stem}_{section}.csv"
        csv_bytes = _csv_bytes(rows)
        artifacts.append(
            write_artifact(
                output_dir,
                csv_name,
                csv_bytes,
                media_type="text/csv; charset=utf-8",
            )
        )
        csv_paths.append(str(output_dir / csv_name))

    manifest = ArtifactManifest(
        artifact_id=output_dir.name,
        run_id=run.run_id,
        run_records_sha256=run.records_sha256,
        generated_at=generated_at,
        display_stem=stem,
        artifacts=artifacts,
    )
    manifest_path = write_manifest(output_dir, manifest)
    return ReportArtifactResult(
        output_dir=str(output_dir),
        json_path=str(output_dir / json_name),
        html_path=str(output_dir / html_name),
        csv_files=csv_paths,
        manifest_path=str(manifest_path),
        manifest=manifest,
    )


def _project_report(
    run: CIRun,
    sections: Sequence[SectionName],
    generated_at: datetime,
    additional_limitations: Sequence[str],
) -> dict[str, Any]:
    projected: dict[str, Any] = {
        "schema_version": 1,
        "title": "Competitive Intelligence Briefing",
        "generated_at": generated_at.isoformat(),
        "run_id": run.run_id,
        "run_records_sha256": run.records_sha256,
        "entities": [snapshot.entity.name for snapshot in run.entities],
        "requested_sections": list(sections),
        "sections": {},
        "coverage": [],
        "limitations": [*run.limitations, *additional_limitations],
    }
    for snapshot in run.entities:
        entity_data: dict[str, Any] = {
            "entity": snapshot.entity.name,
            "entity_type": snapshot.entity.entity_type,
        }
        for section in sections:
            sources = _sources_for_section(snapshot.sources, section)
            if not sources:
                projected["limitations"].append(
                    f"{snapshot.entity.name}/{section}: section was not present in the selected run."
                )
                continue
            records = [record for result in sources for record in result.records]
            status = aggregate_coverage([result.status for result in sources])
            section_data = {
                "coverage": status.value,
                "records": records,
                "returned": len(records),
                "total_available": sum(result.total_available or 0 for result in sources),
                "source_ledger": [
                    evidence.model_dump(mode="json") for result in sources for evidence in result.requests
                ],
                "limitations": [limitation for result in sources for limitation in result.limitations],
            }
            if section == "trials":
                section_data.update({"trials": records, "total_found": sources[0].total_available})
            elif section == "regulatory":
                section_data.update({"events": sources[0].records, "total_events": len(records)})
                if len(sources) > 1:
                    section_data["label_history"] = sources[1].records
            elif section == "news":
                section_data.update({"results": records, "total_results": len(records)})
            else:
                section_data.update({"publications": records, "total_found": sources[0].total_available})
            entity_data[section] = section_data
            projected["coverage"].extend(
                {
                    "entity": snapshot.entity.name,
                    "source": result.source.value,
                    "status": result.status.value,
                    "record_count": len(result.records),
                    "query": result.query,
                    "retrieved_at": result.retrieved_at.isoformat(),
                    "cache": result.cache.status.value,
                    "limitations": result.limitations,
                }
                for result in sources
            )
        projected["sections"][snapshot.entity.name] = entity_data
    return projected


def _sources_for_section(sources, section: SectionName):
    keys = {
        "trials": [SourceName.CLINICAL_TRIALS.value],
        "regulatory": [SourceName.OPENFDA.value, SourceName.DAILYMED.value],
        "news": [SourceName.WEB.value],
        "publications": [SourceName.PUBMED.value],
    }[section]
    return [sources[key] for key in keys if key in sources]


def _coverage_rows(run: CIRun, sections: Sequence[SectionName]) -> list[dict[str, Any]]:
    return [
        {
            "entity": snapshot.entity.name,
            "source": result.source.value,
            "status": result.status,
            "record_count": len(result.records),
            "limitations": result.limitations,
        }
        for snapshot in run.entities
        for section in sections
        for result in _sources_for_section(snapshot.sources, section)
    ]


def _csv_rows(run: CIRun, section: SectionName) -> list[dict[str, Any]]:
    rows = []
    for snapshot in run.entities:
        for result in _sources_for_section(snapshot.sources, section):
            for record in result.records:
                rows.append(
                    {
                        "entity": snapshot.entity.name,
                        "source": result.source.value,
                        **record,
                    }
                )
    return rows


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                key: safe_csv_cell(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)
                for key, value in row.items()
            }
        )
    return buffer.getvalue().encode("utf-8-sig")


def _render_html(report: dict[str, Any]) -> str:
    parts = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'\">",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{escape(report['title'])}</title>",
        "<style>body{font-family:system-ui;margin:2rem;color:#172033}main{max-width:1100px;margin:auto}table{width:100%;border-collapse:collapse;margin:1rem 0}th,td{border:1px solid #dce3ed;padding:.5rem;text-align:left;vertical-align:top}th{background:#f2f5f9}.coverage{padding:.75rem;border-left:4px solid #466b9f;background:#f7f9fc;margin:.5rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#174f91}</style>",
        "</head><body><main>",
        f"<h1>{escape(report['title'])}</h1>",
        f"<p>Run <code>{escape(report['run_id'])}</code> · Generated {escape(report['generated_at'])}</p>",
        "<h2>Source coverage</h2>",
    ]
    for row in report["coverage"]:
        parts.append(
            "<div class='coverage'>"
            f"<strong>{escape(row['entity'])} · {escape(row['source'])}</strong>: "
            f"{escape(row['status'])} · {row['record_count']} returned · "
            f"cache {escape(row['cache'])}"
            "</div>"
        )
    for entity_name, entity_data in report["sections"].items():
        parts.append(f"<h2>{escape(entity_name)}</h2>")
        for section in report["requested_sections"]:
            section_data = entity_data.get(section)
            if not section_data:
                continue
            parts.append(f"<h3>{escape(section.title())} · {escape(section_data['coverage'])}</h3>")
            parts.append("<table><thead><tr><th>Source record</th></tr></thead><tbody>")
            for record in section_data["records"]:
                link = _record_link(record)
                provenance = (
                    f"<p><a href='{escape(link, quote=True)}' rel='noopener noreferrer'>Source</a></p>" if link else ""
                )
                parts.append(
                    "<tr><td>"
                    f"<pre>{escape(json.dumps(record, indent=2, ensure_ascii=False))}</pre>"
                    f"{provenance}</td></tr>"
                )
            parts.append("</tbody></table>")
    if report["limitations"]:
        parts.append("<h2>Limitations</h2><ul>")
        parts.extend(f"<li>{escape(value)}</li>" for value in report["limitations"])
        parts.append("</ul>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


def _record_link(record: dict[str, Any]) -> str | None:
    for key in ("source_url", "url"):
        value = record.get(key)
        if not isinstance(value, str):
            continue
        parsed = urlsplit(value)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.netloc
            and not parsed.username
            and not parsed.password
            and sanitize_url(value) == value
        ):
            return value
    return None


def _normalize_sections(values: Sequence[SectionName]) -> list[SectionName]:
    return [section for section in _SECTION_ORDER if section in values]


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
