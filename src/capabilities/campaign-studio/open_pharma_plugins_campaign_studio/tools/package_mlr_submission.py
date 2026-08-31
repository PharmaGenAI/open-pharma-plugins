"""package_mlr_submission — fail-closed Markdown and HTML MLR review."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class PackageMlrSubmissionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_brief_id: str = Field(description="Links to the campaign")
    reviewer_notes: str | None = Field(default=None, description="Untrusted plain-text context for reviewers")


TOOL: dict[str, Any] = {
    "name": "package_mlr_submission",
    "description": (
        "Fail closed unless the complete campaign and both validation gates are current, then generate "
        "the canonical Markdown and self-contained HTML draft review outputs."
    ),
    "args": PackageMlrSubmissionArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    from .._mlr_package import build_review_plan, error_payload, publish_review

    try:
        parsed = PackageMlrSubmissionArgs.model_validate(arguments)
        result = publish_review(build_review_plan(parsed.campaign_brief_id, parsed.reviewer_notes))
    except ValidationError as exc:
        result = {
            "error": {
                "code": "invalid_arguments",
                "message": "Package arguments are invalid.",
                "items": [error["msg"] for error in exc.errors()],
            }
        }
    except Exception as exc:
        result = error_payload(exc)
    return [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}]
