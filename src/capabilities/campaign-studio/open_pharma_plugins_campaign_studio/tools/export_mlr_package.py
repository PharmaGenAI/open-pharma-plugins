"""export_mlr_package — deterministic content-addressed campaign export."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ExportMlrPackageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_brief_id: str = Field(description="Links to the campaign")
    destination_dir: str | None = Field(
        default=None,
        description="Optional directory receiving an exact archive copy; callers cannot choose a filename",
    )
    reviewer_notes: str | None = Field(default=None, description="Untrusted plain-text context for reviewers")


TOOL: dict[str, Any] = {
    "name": "export_mlr_package",
    "description": (
        "Require complete current campaign evidence, render canonical reviews, and create a deterministic "
        "content-addressed ZIP with a SHA-256 manifest. Optionally copy it to a safe directory."
    ),
    "args": ExportMlrPackageArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    from .._mlr_package import error_payload, export_package

    try:
        parsed = ExportMlrPackageArgs.model_validate(arguments)
        result = export_package(
            parsed.campaign_brief_id,
            destination_dir=parsed.destination_dir,
            reviewer_notes=parsed.reviewer_notes,
        )
    except ValidationError as exc:
        result = {
            "error": {
                "code": "invalid_arguments",
                "message": "Export arguments are invalid.",
                "items": [error["msg"] for error in exc.errors()],
            }
        }
    except Exception as exc:
        result = error_payload(exc)
    return [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]
