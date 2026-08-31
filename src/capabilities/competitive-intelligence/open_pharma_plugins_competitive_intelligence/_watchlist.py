"""Validated persistent watchlist for tracked competitor entities."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from shared.filesystem import atomic_write_json, contained_path, ensure_private_dir

from .models import TrackedEntity


class WatchlistError(ValueError):
    """The persisted watchlist cannot be trusted or validated."""


def _data_dir() -> Path:
    from shared.env import get_env

    return ensure_private_dir(
        get_env(
            "OPEN_PHARMA_CI_DATA_DIR",
            str(Path.home() / ".open-pharma-plugins" / "competitive-intelligence"),
        )
    )


def _watchlist_path() -> Path:
    return contained_path(_data_dir(), "watchlist.json")


def reports_dir() -> Path:
    return ensure_private_dir(contained_path(_data_dir(), "reports"))


def runs_dir() -> Path:
    return ensure_private_dir(contained_path(_data_dir(), "runs"))


def load_tracked_entities() -> list[TrackedEntity]:
    path = _watchlist_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict) or not isinstance(payload.get("entities"), list):
            raise ValueError("watchlist root must contain an entities list")
        return [TrackedEntity.model_validate(entity) for entity in payload["entities"]]
    except (OSError, ValueError, TypeError, json.JSONDecodeError, ValidationError):
        raise WatchlistError("watchlist.json is malformed or contains an invalid entity") from None


def load_watchlist() -> list[dict[str, Any]]:
    return [entity.model_dump(mode="json", exclude_none=True) for entity in load_tracked_entities()]


def save_watchlist(entities: list[dict[str, Any] | TrackedEntity]) -> None:
    try:
        validated = [TrackedEntity.model_validate(entity) for entity in entities]
    except (TypeError, ValueError, ValidationError):
        raise WatchlistError("refusing to persist an invalid watchlist") from None
    atomic_write_json(
        _watchlist_path(),
        {"entities": [entity.model_dump(mode="json", exclude_none=True) for entity in validated]},
    )


def add_entity(
    entity_type: str,
    name: str,
    therapeutic_area: str = "",
    aliases: list[str] | None = None,
) -> list[dict[str, Any]]:
    entities = load_tracked_entities()
    for index, entity in enumerate(entities):
        if entity.name.casefold() == name.strip().casefold() and entity.entity_type == entity_type:
            entities[index] = TrackedEntity(
                entity_type=entity.entity_type,
                name=entity.name,
                therapeutic_area=therapeutic_area or entity.therapeutic_area,
                aliases=[*entity.aliases, *(aliases or [])],
                added_at=entity.added_at,
            )
            save_watchlist(entities)
            return [item.model_dump(mode="json", exclude_none=True) for item in entities]

    entities.append(
        TrackedEntity(
            entity_type=entity_type,
            name=name,
            therapeutic_area=therapeutic_area,
            aliases=aliases or [],
            added_at=datetime.now(timezone.utc),
        )
    )
    save_watchlist(entities)
    return [item.model_dump(mode="json", exclude_none=True) for item in entities]


def remove_entity(name: str) -> list[dict[str, Any]]:
    entities = [entity for entity in load_tracked_entities() if entity.name.casefold() != name.strip().casefold()]
    save_watchlist(entities)
    return [item.model_dump(mode="json", exclude_none=True) for item in entities]


def get_search_names(entity: dict[str, Any] | TrackedEntity) -> list[str]:
    """Return the validated primary name and case-insensitively unique aliases."""
    tracked = TrackedEntity.model_validate(entity)
    return [tracked.name, *tracked.aliases]
