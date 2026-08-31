"""ci_track — manage the competitor watchlist."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TrackArgs(BaseModel):
    action: str = Field(description="Action: 'add', 'remove', or 'list'")
    entity_type: str = Field(default="", description="Entity type: 'drug' or 'company' (required for add)")
    name: str = Field(default="", description="Entity name (required for add/remove)")
    therapeutic_area: str = Field(default="", description="Therapeutic area, e.g. 'oncology'")
    aliases: list[str] = Field(default_factory=list, description="Alternative names or codes")


TOOL: dict[str, Any] = {
    "name": "ci_track",
    "description": (
        "Manage the competitive intelligence watchlist. Add or remove competitor "
        "drugs and companies to track, or list all currently tracked entities. "
        "The watchlist persists across sessions and is used by ci_report to "
        "generate briefings for all tracked competitors."
    ),
    "args": TrackArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from .._watchlist import WatchlistError, add_entity, load_watchlist, remove_entity

    try:
        return _handle(arguments, add_entity, load_watchlist, remove_entity)
    except WatchlistError as error:
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "error": {
                            "code": "invalid_watchlist",
                            "message": str(error),
                        }
                    }
                ),
            }
        ]


def _handle(arguments, add_entity, load_watchlist, remove_entity):
    import json

    action = arguments.get("action", "list").lower()

    if action == "add":
        name = arguments.get("name", "")
        entity_type = arguments.get("entity_type", "")
        if not name or entity_type not in ("drug", "company"):
            return [
                {
                    "type": "text",
                    "text": json.dumps({"error": "action='add' requires name and entity_type ('drug' or 'company')"}),
                }
            ]
        entities = add_entity(
            entity_type=entity_type,
            name=name,
            therapeutic_area=arguments.get("therapeutic_area", ""),
            aliases=arguments.get("aliases"),
        )
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "action": "added",
                        "name": name,
                        "entity_type": entity_type,
                        "watchlist": entities,
                    },
                    indent=2,
                ),
            }
        ]

    if action == "remove":
        name = arguments.get("name", "")
        if not name:
            return [{"type": "text", "text": json.dumps({"error": "action='remove' requires name"})}]
        entities = remove_entity(name)
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "action": "removed",
                        "name": name,
                        "watchlist": entities,
                    },
                    indent=2,
                ),
            }
        ]

    entities = load_watchlist()
    return [
        {
            "type": "text",
            "text": json.dumps(
                {
                    "action": "list",
                    "total": len(entities),
                    "watchlist": entities,
                },
                indent=2,
            ),
        }
    ]
