"""render_mlr_review — render the canonical interactive MLR review."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class RenderMlrReviewArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_brief_id: str = Field(description="Links to the campaign")
    reviewer_notes: str | None = Field(default=None, description="Untrusted plain-text context for reviewers")


TOOL: dict[str, Any] = {
    "name": "render_mlr_review",
    "description": (
        "Require complete current campaign evidence and produce canonical Markdown plus a self-contained, "
        "interactive HTML draft review with channel tabs, validation evidence, provenance, and hashes."
    ),
    "args": RenderMlrReviewArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    from .._mlr_package import build_review_plan, error_payload, publish_review

    try:
        parsed = RenderMlrReviewArgs.model_validate(arguments)
        result = publish_review(build_review_plan(parsed.campaign_brief_id, parsed.reviewer_notes))
    except ValidationError as exc:
        result = {
            "error": {
                "code": "invalid_arguments",
                "message": "Review arguments are invalid.",
                "items": [error["msg"] for error in exc.errors()],
            }
        }
    except Exception as exc:
        result = error_payload(exc)
    return [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]
