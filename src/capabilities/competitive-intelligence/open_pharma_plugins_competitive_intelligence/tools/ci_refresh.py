"""ci_refresh — collect one immutable evidence run for tracked entities."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from .._runs import RunIntegrityError, create_run
from .._watchlist import WatchlistError, load_tracked_entities
from ..models import RefreshRequest, SectionName, aggregate_coverage


class RefreshArgs(BaseModel):
    entities: list[str] | None = Field(
        default=None,
        description="Optional tracked entity names; omit to refresh the full watchlist",
    )
    include_sections: list[SectionName] = Field(
        default_factory=lambda: ["trials", "regulatory", "news", "publications"]
    )
    news_days_back: int = Field(default=90, ge=1, le=365)
    publication_days_back: int = Field(default=365, ge=1, le=1825)


TOOL: dict[str, Any] = {
    "name": "ci_refresh",
    "description": (
        "Collect one immutable, evidence-bearing Competitive Intelligence run "
        "for selected tracked entities or the full watchlist."
    ),
    "args": RefreshArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    args = RefreshArgs.model_validate(arguments)
    try:
        watchlist = load_tracked_entities()
    except WatchlistError as error:
        return _error("invalid_watchlist", str(error))

    selected = watchlist
    if args.entities is not None:
        by_name = {entity.name.casefold(): entity for entity in watchlist}
        requested = list(dict.fromkeys(name.strip().casefold() for name in args.entities if name.strip()))
        unknown = [name for name in args.entities if name.strip().casefold() not in by_name]
        if unknown:
            return _error(
                "unknown_entity",
                "Track each entity with ci_track action=add and an explicit drug or company type before refresh.",
                entities=list(dict.fromkeys(unknown)),
            )
        selected = [by_name[name] for name in requested]
    if not selected:
        return _error(
            "empty_watchlist",
            "Track at least one explicit drug or company with ci_track action=add before refresh.",
        )

    request = RefreshRequest(
        entities=selected,
        include_sections=args.include_sections,
        news_days_back=args.news_days_back,
        publication_days_back=args.publication_days_back,
    )
    try:
        run = create_run(request)
    except RunIntegrityError as error:
        return _error("run_persistence_failed", str(error))

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
    status = aggregate_coverage([result.status for snapshot in run.entities for result in snapshot.sources.values()])
    output = {
        "success": True,
        "run_id": run.run_id,
        "manifest_path": run.manifest_path,
        "records_sha256": run.records_sha256,
        "entities": [snapshot.entity.name for snapshot in run.entities],
        "include_sections": run.request.include_sections,
        "coverage_status": status.value,
        "coverage": coverage,
        "limitations": run.limitations,
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]


def _error(code: str, message: str, **details: Any) -> list[dict[str, Any]]:
    payload = {"error": {"code": code, "message": message, **details}}
    return [{"type": "text", "text": json.dumps(payload, indent=2)}]
