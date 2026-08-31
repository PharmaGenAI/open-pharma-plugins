"""render_email - produce an inspected, self-contained campaign email."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class RenderEmailArgs(BaseModel):
    campaign_brief_id: str = Field(description="Links to the campaign")
    template: str | None = Field(default=None, description="Custom template path; default template when omitted")


TOOL: dict[str, Any] = {
    "name": "render_email",
    "description": (
        "Render an inspected, responsive, self-contained HTML email from current validated copy and sealed brand assets."
    ),
    "args": RenderEmailArgs,
}


def _build_email_candidate(
    context: dict[str, Any], template: object = None, *, expected_provenance: dict[str, Any] | None = None
) -> tuple[bytes, dict[str, Any]]:
    """Build the deterministic inspected candidate input without touching campaign storage."""
    from .._render_validation import RenderContractError, load_email_template, render_jinja
    from ..models.brief import _valid_https_url

    brief = context["brief"]
    copy = context["copy"]
    cta_url = brief.get("call_to_action_url")
    if not isinstance(cta_url, str) or not _valid_https_url(cta_url):
        raise RenderContractError("invalid_cta_url", "The campaign brief must contain one approved HTTPS CTA URL.")
    source, provenance = load_email_template(template)
    if expected_provenance is not None and provenance != expected_provenance:
        raise RenderContractError(
            "email_template_changed",
            "The recorded email template is missing, malformed, unsafe, or changed after rendering.",
        )
    legal = context["legal"]
    template_context = {
        "language": "en",
        "brand": str(brief.get("brand", "")),
        "subject": copy["subject"]["text"],
        "preheader": copy["preheader"]["text"],
        "headline": copy["headline"]["text"],
        "body_paragraphs": [block["text"] for block in copy["body"]],
        "cta": copy["cta"]["text"],
        "cta_url": cta_url,
        "logo_data_uri": context["logo"].data_uri,
        "legal_isi": legal.get("isi", ""),
        "remaining_legal": [{"name": name, "value": value} for name, value in legal.items() if name != "isi"],
        "palette": context["palette"],
        "typography": context["typography"],
    }
    html = render_jinja(source, template_context, custom=provenance["kind"] == "custom")
    return html.encode("utf-8"), provenance


def _snapshot_existing(path: Path) -> bytes | None:
    try:
        state = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f"Could not inspect prior email state: {path.name}.") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
        raise RuntimeError(f"Prior email state is not a regular file: {path.name}.")
    return path.read_bytes()


def _restore_snapshot(path: Path, payload: bytes | None) -> None:
    from shared.filesystem import atomic_write_bytes

    try:
        state = path.lstat()
    except FileNotFoundError:
        state = None
    if state is not None:
        if stat.S_ISDIR(state.st_mode):
            raise RuntimeError(f"Cannot restore email transaction over a directory: {path.name}.")
        path.unlink()
    if payload is not None:
        atomic_write_bytes(path, payload)


def _persist_email_pair(campaign_brief_id: str, payload: bytes, provenance: dict[str, Any]) -> Path:
    """Persist email bytes and provenance as one rollback-safe logical transaction."""
    from .. import _campaign_store as store

    campaign = store.campaign_dir(campaign_brief_id)
    output = store.outputs_dir(campaign_brief_id) / "email.html"
    provenance_path = campaign / "render-provenance-email.json"
    prior_output = _snapshot_existing(output)
    prior_provenance = _snapshot_existing(provenance_path)
    envelope = {"campaign_brief_id": campaign_brief_id, "channel": "email", "template": provenance}
    try:
        store.save_artifact(campaign_brief_id, "render-provenance-email.json", envelope)
        return store.save_output(campaign_brief_id, "email.html", payload.decode("utf-8"))
    except Exception:
        try:
            _restore_snapshot(output, prior_output)
            _restore_snapshot(provenance_path, prior_provenance)
        except Exception as rollback_error:
            raise RuntimeError(
                "Email output/provenance transaction could not be rolled back safely."
            ) from rollback_error
        raise


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    from .._render_validation import (
        RenderContractError,
        error_response,
        inspect_html,
        load_render_context,
        prohibited_errors,
    )

    campaign_brief_id = arguments.get("campaign_brief_id")
    try:
        context = load_render_context(campaign_brief_id, "email")
        brief = context["brief"]
        payload, provenance = _build_email_candidate(context, arguments.get("template"))
        inspection = inspect_html(payload, context, custom=provenance["kind"] == "custom")
        errors = [*inspection["errors"], *prohibited_errors(inspection["content_text"], brief)]
        if errors:
            raise RenderContractError("rendered_email_invalid", "; ".join(sorted(set(errors))))
        path = _persist_email_pair(campaign_brief_id, payload, provenance)
        result = {
            "campaign_brief_id": campaign_brief_id,
            "channel": "email",
            "file_path": str(path),
            "format": "html",
            "editable": True,
            "template": provenance,
        }
    except Exception as exc:
        result = error_response(exc)
    return [{"type": "text", "text": json.dumps(result, ensure_ascii=False, sort_keys=True)}]
