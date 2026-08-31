"""get_campaign_status — non-mutating Campaign Studio resumability status."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError


class GetCampaignStatusArgs(BaseModel):
    campaign_brief_id: str = Field(description="Campaign brief ID")


TOOL: dict[str, Any] = {
    "name": "get_campaign_status",
    "description": (
        "Read a Campaign Studio campaign's persisted workflow status without creating files or directories. "
        "Reports semantic workflow completeness, provenance, validation freshness, available rendered outputs, "
        "and the exact next tool to call."
    ),
    "args": GetCampaignStatusArgs,
}

_INPUT_ARTIFACTS = ("approved-claims.json", "brand-components.json", "input-provenance.json")
_OUTPUT_NAMES = {"email": "email.html", "banner": "banner.svg", "poster": "poster.pdf"}
_CHANNEL_WORKFLOW_ORDER = ("email", "banner", "poster")
_SemanticValidator = Callable[[object], object]


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normal JSON even when persisted campaign data is malformed or too deep."""
    try:
        return _handle(arguments)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        ValidationError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        RecursionError,
        OverflowError,
        MemoryError,
    ):
        return _text_response(
            {
                "error": {
                    "code": "campaign_status_unavailable",
                    "message": "Campaign status could not be read safely from persisted data.",
                }
            }
        )


def _handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    from .._campaign_store import existing_campaign_path_result, safe_campaign_path
    from .._workflow_validation import validate_campaign_brief

    campaign_brief_id = arguments["campaign_brief_id"]
    campaign_path, path_error = safe_campaign_path(campaign_brief_id)
    if path_error:
        return _text_response({"error": path_error})
    existing_campaign, existing_error = existing_campaign_path_result(campaign_brief_id)
    if existing_error:
        return _text_response({"error": existing_error})
    if campaign_path is None or existing_campaign is None:
        return _text_response(
            {
                "error": {
                    "code": "campaign_not_found",
                    "message": f"Campaign brief '{campaign_brief_id}' was not found.",
                }
            }
        )

    raw_brief, brief_read_error, brief_path = _read_artifact(campaign_brief_id, "campaign-brief.json")
    if brief_read_error and brief_read_error.get("code") == "artifact_missing":
        return _text_response(
            {
                "error": {
                    "code": "malformed_campaign_brief",
                    "message": "Campaign brief is missing, malformed, or unreadable.",
                    "path": str(brief_path) if brief_path else None,
                }
            }
        )
    brief_diagnostic, brief_value = _semantic_diagnostic(
        raw_brief,
        brief_read_error,
        brief_path,
        "campaign-brief.json",
        lambda value: validate_campaign_brief(value, campaign_brief_id),
    )
    raw_brief_dict = raw_brief if isinstance(raw_brief, dict) else {}
    brief = brief_value if isinstance(brief_value, dict) else None
    channels = _channels(brief) if brief is not None else []

    diagnostics: dict[str, dict[str, Any]] = {"campaign-brief.json": brief_diagnostic}
    values: dict[str, object] = {}
    if brief is None:
        diagnostics.update(_prerequisite_diagnostics(campaign_brief_id, channels, "campaign_brief_invalid"))
    else:
        diagnostics, values = _workflow_artifact_diagnostics(campaign_brief_id, brief, channels, brief_diagnostic)

    artifact_paths = {
        name: diagnostic["path"] for name, diagnostic in diagnostics.items() if isinstance(diagnostic.get("path"), str)
    }
    safe_provenance = values.get("input-provenance.json")
    provenance_diagnostic = diagnostics.get("input-provenance.json", _missing_diagnostic())
    pre_render = _pre_render_status(campaign_brief_id)
    rendered = _rendered_status(campaign_brief_id)
    rendered_paths, rendered_path_errors, output_diagnostics = _output_status(campaign_brief_id, channels)
    inputs_ready = all(diagnostics.get(name, _missing_diagnostic())["status"] == "current" for name in _INPUT_ARTIFACTS)
    if not inputs_ready:
        pre_render = _invalidate_current_validation(pre_render, "workflow_inputs_invalid")
        rendered = _invalidate_current_validation(rendered, "workflow_inputs_invalid")
    review_outputs, package_export = _handoff_status(campaign_brief_id, rendered, rendered_paths)
    completed, missing, next_step = _workflow_status(
        brief_diagnostic,
        channels,
        diagnostics,
        output_diagnostics,
        pre_render,
        rendered,
        review_outputs,
        package_export,
    )

    metadata = {
        key: raw_brief_dict.get(key)
        for key in (
            "campaign_brief_id",
            "campaign_name",
            "brand",
            "country",
            "policy_jurisdiction",
            "indication",
            "target_segment",
            "mode",
            "channels",
        )
    }
    demo_mode = bool(raw_brief_dict.get("demo_mode"))
    if isinstance(safe_provenance, dict):
        demo_mode = demo_mode or any(
            bool(source.get("is_demo_fixture")) for source in safe_provenance.values() if isinstance(source, dict)
        )
    return _text_response(
        {
            "campaign_brief_id": campaign_brief_id,
            "brief": metadata,
            "demo_mode": demo_mode,
            "demo_provenance_disclosure": (
                "This campaign uses bundled fictional demo input(s); it must not be used as production evidence."
                if demo_mode
                else None
            ),
            "provenance": {
                "path": provenance_diagnostic.get("path"),
                "inputs": safe_provenance if isinstance(safe_provenance, dict) else {},
                "errors": _diagnostic_errors(provenance_diagnostic),
            },
            "artifact_paths": artifact_paths,
            "artifact_diagnostics": diagnostics,
            "rendered_paths": rendered_paths,
            "rendered_path_errors": rendered_path_errors,
            "completed_steps": completed,
            "missing_steps": missing,
            "pre_render_validation": pre_render,
            "rendered_validation": rendered,
            "review_outputs": review_outputs,
            "package_export": package_export,
            "next_step": next_step,
        }
    )


def _workflow_artifact_diagnostics(
    campaign_brief_id: str,
    brief: dict[str, Any],
    channels: list[str],
    brief_diagnostic: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, object]]:
    from .._workflow_validation import (
        BrandState,
        validate_audience_journey,
        validate_brand_components,
        validate_channel_copy,
        validate_claims,
        validate_input_provenance,
        validate_message_architecture,
    )

    diagnostics: dict[str, dict[str, Any]] = {"campaign-brief.json": brief_diagnostic}
    values: dict[str, object] = {}
    claims_diagnostic, claims_value = _artifact_diagnostic(
        campaign_brief_id, "approved-claims.json", lambda value: validate_claims(value, brief)
    )
    diagnostics["approved-claims.json"] = claims_diagnostic
    if claims_value is not None:
        values["approved-claims.json"] = claims_value

    brand_diagnostic, brand_value = _artifact_diagnostic(
        campaign_brief_id, "brand-components.json", validate_brand_components
    )
    diagnostics["brand-components.json"] = brand_diagnostic
    if brand_value is not None:
        values["brand-components.json"] = brand_value

    claims = claims_value if isinstance(claims_value, list) else None
    brand = brand_value if isinstance(brand_value, BrandState) else None
    if claims is None or brand is None:
        provenance_diagnostic, provenance_value = _dependent_artifact_diagnostic(
            campaign_brief_id,
            "input-provenance.json",
            "input_dependencies_invalid",
        )
    else:
        provenance_diagnostic, provenance_value = _artifact_diagnostic(
            campaign_brief_id,
            "input-provenance.json",
            lambda value: validate_input_provenance(value, brief, claims, brand),
        )
    diagnostics["input-provenance.json"] = provenance_diagnostic
    if provenance_value is not None:
        values["input-provenance.json"] = provenance_value

    claims_by_id = {claim["claim_id"]: claim for claim in claims} if claims is not None else {}
    if claims is None:
        journey_diagnostic, journey_value = _dependent_artifact_diagnostic(
            campaign_brief_id, "audience-journey.json", "input_dependencies_invalid"
        )
    else:
        journey_diagnostic, journey_value = _artifact_diagnostic(
            campaign_brief_id,
            "audience-journey.json",
            lambda value: validate_audience_journey(value, campaign_brief_id, brief, claims_by_id),
        )
    diagnostics["audience-journey.json"] = journey_diagnostic
    if journey_value is not None:
        values["audience-journey.json"] = journey_value

    journey_stages = _journey_stage_names(journey_value)
    if claims is None:
        architecture_diagnostic, architecture_value = _dependent_artifact_diagnostic(
            campaign_brief_id, "message-architecture.json", "input_dependencies_invalid"
        )
    else:
        architecture_diagnostic, architecture_value = _artifact_diagnostic(
            campaign_brief_id,
            "message-architecture.json",
            lambda value: validate_message_architecture(
                value,
                campaign_brief_id,
                brief,
                claims_by_id,
                journey_stages,
            ),
        )
    diagnostics["message-architecture.json"] = architecture_diagnostic
    if architecture_value is not None:
        values["message-architecture.json"] = architecture_value

    for channel in channels:
        filename = f"copy-{channel}.json"
        if claims is None or brand is None:
            diagnostic, artifact_value = _dependent_artifact_diagnostic(
                campaign_brief_id, filename, "input_dependencies_invalid"
            )
        else:
            diagnostic, artifact_value = _artifact_diagnostic(
                campaign_brief_id,
                filename,
                lambda value, channel=channel: validate_channel_copy(
                    value,
                    campaign_brief_id,
                    channel,
                    brief,
                    claims_by_id,
                    brand,
                ),
            )
        diagnostics[filename] = diagnostic
        if artifact_value is not None:
            values[filename] = artifact_value
    return diagnostics, values


def _prerequisite_diagnostics(campaign_brief_id: str, channels: list[str], code: str) -> dict[str, dict[str, Any]]:
    names = [*_INPUT_ARTIFACTS, "audience-journey.json", "message-architecture.json"]
    names.extend(f"copy-{channel}.json" for channel in channels)
    diagnostics: dict[str, dict[str, Any]] = {}
    for filename in names:
        diagnostic, _value = _dependent_artifact_diagnostic(campaign_brief_id, filename, code)
        diagnostics[filename] = diagnostic
    return diagnostics


def _text_response(value: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "text", "text": json.dumps(value, ensure_ascii=False, sort_keys=True)}]


def _read_artifact(
    campaign_brief_id: str, filename: str, *, section: str | None = None
) -> tuple[object | None, dict[str, str] | None, object | None]:
    from .._campaign_store import read_campaign_json

    return read_campaign_json(campaign_brief_id, filename, section=section)


def _artifact_diagnostic(
    campaign_brief_id: str, filename: str, validator: _SemanticValidator
) -> tuple[dict[str, Any], object | None]:
    value, read_error, path = _read_artifact(campaign_brief_id, filename)
    return _semantic_diagnostic(value, read_error, path, filename, validator)


def _dependent_artifact_diagnostic(
    campaign_brief_id: str, filename: str, prerequisite_code: str
) -> tuple[dict[str, Any], object | None]:
    _value, read_error, path = _read_artifact(campaign_brief_id, filename)
    if read_error:
        return _read_error_diagnostic(read_error, path), None
    return (
        {
            "status": "invalid",
            "path": str(path) if path is not None else None,
            "error": {
                "code": prerequisite_code,
                "message": f"Persisted artifact cannot be trusted until prerequisite campaign inputs are valid: {filename}.",
            },
        },
        None,
    )


def _semantic_diagnostic(
    value: object | None,
    read_error: dict[str, str] | None,
    path: object | None,
    filename: str,
    validator: _SemanticValidator,
) -> tuple[dict[str, Any], object | None]:
    if read_error:
        return _read_error_diagnostic(read_error, path), None
    try:
        result = validator(value)
        errors = getattr(result, "errors", ())
        result_value = getattr(result, "value", None)
    except (OSError, ValueError, TypeError, ValidationError, RecursionError, OverflowError, MemoryError) as exc:
        errors = (f"Artifact semantics could not be read safely: {exc}",)
        result_value = None
    if errors:
        details = [str(error) for error in errors]
        return (
            {
                "status": "invalid",
                "path": str(path) if path is not None else None,
                "error": {
                    "code": "invalid_artifact_semantics",
                    "message": f"Persisted artifact does not satisfy its workflow contract: {filename}.",
                    "details": details,
                },
            },
            None,
        )
    return {"status": "current", "path": str(path) if path is not None else None, "error": None}, result_value


def _read_error_diagnostic(error: dict[str, str], path: object | None) -> dict[str, Any]:
    return {
        "status": "missing" if error.get("code") == "artifact_missing" else "invalid",
        "path": str(path) if path is not None else None,
        "error": error,
    }


def _missing_diagnostic() -> dict[str, Any]:
    return {"status": "missing", "path": None, "error": None}


def _diagnostic_errors(diagnostic: dict[str, Any]) -> list[dict[str, Any]]:
    error = diagnostic.get("error")
    return [error] if isinstance(error, dict) else []


def _channels(brief: dict[str, Any] | None) -> list[str]:
    if not isinstance(brief, dict):
        return []
    raw_channels = brief.get("channels")
    if not isinstance(raw_channels, list):
        return []
    selected = {channel for channel in raw_channels if isinstance(channel, str) and channel in _OUTPUT_NAMES}
    return [channel for channel in _CHANNEL_WORKFLOW_ORDER if channel in selected]


def _journey_stage_names(value: object | None) -> set[str] | None:
    if not isinstance(value, dict) or not isinstance(value.get("stages"), list):
        return None
    names = {
        stage.get("stage")
        for stage in value["stages"]
        if isinstance(stage, dict) and isinstance(stage.get("stage"), str)
    }
    return names if names else None


def _pre_render_status(campaign_brief_id: str) -> dict[str, Any]:
    from .._campaign_store import existing_artifact_path
    from .._renderer import validation_gate_state

    try:
        state = dict(validation_gate_state(campaign_brief_id))
        path = existing_artifact_path(campaign_brief_id, "policy-checks.json", section="validation")
        state["path"] = str(path) if path is not None else None
        return state
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError, MemoryError):
        return {
            "status": "failed",
            "code": "validation_status_unavailable",
            "reason": "Pre-render validation status could not be read safely.",
            "path": None,
        }


def _rendered_status(campaign_brief_id: str) -> dict[str, Any]:
    from .._campaign_store import existing_artifact_path
    from .._renderer import rendered_validation_gate_state

    try:
        state = dict(rendered_validation_gate_state(campaign_brief_id))
        path = existing_artifact_path(campaign_brief_id, "rendered-assets.json", section="validation")
        state["path"] = str(path) if path is not None else None
        return state
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError, MemoryError):
        return {
            "status": "failed",
            "code": "validation_status_unavailable",
            "reason": "Rendered validation status could not be read safely.",
            "path": None,
        }


def _invalidate_current_validation(state: dict[str, Any], code: str) -> dict[str, Any]:
    if state.get("status") != "current":
        return state
    return {
        **state,
        "status": "stale",
        "code": code,
        "reason": "Campaign workflow inputs no longer satisfy their persisted semantic contracts.",
    }


def _output_status(
    campaign_brief_id: str, channels: list[str]
) -> tuple[list[str], list[dict[str, str]], dict[str, dict[str, Any]]]:
    from .._campaign_store import existing_artifact_path_result, existing_output_paths_result

    paths, path_error = existing_output_paths_result(campaign_brief_id)
    path_errors = _output_path_errors(path_error)
    output_diagnostics: dict[str, dict[str, Any]] = {}
    for channel in channels:
        filename = _OUTPUT_NAMES[channel]
        path, error = existing_artifact_path_result(campaign_brief_id, filename, section="outputs")
        output_diagnostics[channel] = {
            "status": "current" if path is not None and error is None else "missing" if error is None else "invalid",
            "path": str(path) if path is not None else None,
            "error": error,
        }
    return [str(path) for path in paths], path_errors, output_diagnostics


def _output_path_errors(error: dict[str, object] | None) -> list[dict[str, str]]:
    if error is None:
        return []
    entries = error.get("entries")
    if isinstance(entries, list) and all(isinstance(entry, dict) for entry in entries):
        return [
            entry for entry in entries if isinstance(entry.get("code"), str) and isinstance(entry.get("message"), str)
        ]
    code = error.get("code")
    message = error.get("message")
    return [{"code": code, "message": message}] if isinstance(code, str) and isinstance(message, str) else []


def _handoff_status(
    campaign_brief_id: str,
    rendered: dict[str, Any],
    rendered_paths: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    output_paths = {Path(path).name: Path(path) for path in rendered_paths}
    review_names = ("mlr-review-summary.md", "mlr-review.html")
    missing_review = [name for name in review_names if name not in output_paths]
    verified_review: object = None
    if rendered.get("status") != "current":
        review = {"status": "stale", "paths": [], "missing": list(review_names)}
    elif missing_review:
        review = {
            "status": "missing",
            "paths": [str(output_paths[name]) for name in review_names if name in output_paths],
            "missing": missing_review,
        }
    else:
        from .._mlr_package import MlrContractError, verify_persisted_review

        try:
            verified_review = verify_persisted_review(campaign_brief_id)
        except MlrContractError as exc:
            review = {
                "status": "stale",
                "paths": [str(output_paths[name]) for name in review_names],
                "missing": [],
                "error": exc.payload()["error"],
            }
        else:
            review = {"status": "current", "paths": [str(output_paths[name]) for name in review_names], "missing": []}
    package = _package_export_status(campaign_brief_id, rendered, review, output_paths, verified_review)
    return review, package


def _package_export_status(
    campaign_brief_id: str,
    rendered: dict[str, Any],
    review: dict[str, Any],
    output_paths: dict[str, Path],
    verified_review: object = None,
) -> dict[str, Any]:
    if rendered.get("status") != "current" or review.get("status") != "current":
        return {"status": "stale", "manifest_path": None, "archive_path": None, "error": None}
    manifest_path = output_paths.get("package-manifest.json")
    if manifest_path is None:
        return {"status": "missing", "manifest_path": None, "archive_path": None, "error": None}
    from .._mlr_package import MlrContractError, PersistedReview, verify_persisted_package

    try:
        if not isinstance(verified_review, PersistedReview):
            raise MlrContractError("review_outputs_stale", "Canonical review verification is unavailable.")
        package = verify_persisted_package(verified_review)
    except MlrContractError as exc:
        return {
            "status": "invalid",
            "manifest_path": str(manifest_path),
            "archive_path": None,
            "error": {"code": "package_export_invalid", "message": exc.message},
        }
    return {
        "status": "current",
        "manifest_path": str(package.manifest_path),
        "archive_path": str(package.archive_path),
        "package_digest": package.package_digest,
        "error": None,
    }


def _workflow_status(
    brief_diagnostic: dict[str, Any],
    channels: list[str],
    diagnostics: dict[str, dict[str, Any]],
    output_diagnostics: dict[str, dict[str, Any]],
    pre_render: dict[str, Any],
    rendered: dict[str, Any],
    review: dict[str, Any],
    package: dict[str, Any],
) -> tuple[list[str], list[str], dict[str, str | None]]:
    completed: list[str] = []
    missing: list[str] = []
    brief_ready = brief_diagnostic.get("status") == "current"
    if brief_ready:
        completed.append("brief")
    else:
        missing.append("brief")
    inputs_ready = brief_ready and all(
        diagnostics.get(name, _missing_diagnostic())["status"] == "current" for name in _INPUT_ARTIFACTS
    )
    if inputs_ready:
        completed.append("preflight_inputs")
    else:
        missing.append("preflight_inputs")
    journey_ready = (
        inputs_ready and diagnostics.get("audience-journey.json", _missing_diagnostic())["status"] == "current"
    )
    if journey_ready:
        completed.append("audience_journey")
    else:
        missing.append("audience_journey")
    architecture_ready = (
        journey_ready and diagnostics.get("message-architecture.json", _missing_diagnostic())["status"] == "current"
    )
    if architecture_ready:
        completed.append("message_architecture")
    else:
        missing.append("message_architecture")
    missing_copy_channels: list[str] = []
    for channel in channels:
        step = f"copy:{channel}"
        copy_ready = (
            architecture_ready and diagnostics.get(f"copy-{channel}.json", _missing_diagnostic())["status"] == "current"
        )
        if copy_ready:
            completed.append(step)
        else:
            missing.append(step)
            missing_copy_channels.append(channel)
    copy_ready = architecture_ready and not missing_copy_channels
    if copy_ready and pre_render.get("status") == "current":
        completed.append("pre_render_validation")
    else:
        missing.append("pre_render_validation")
    missing_outputs: list[str] = []
    for channel in channels:
        step = f"rendered:{channel}"
        rendered_output = (
            pre_render.get("status") == "current"
            and output_diagnostics.get(channel, _missing_diagnostic())["status"] == "current"
        )
        if rendered_output:
            completed.append(step)
        else:
            missing.append(step)
            missing_outputs.append(channel)
    outputs_ready = pre_render.get("status") == "current" and not missing_outputs
    if outputs_ready and rendered.get("status") == "current":
        completed.append("rendered_validation")
    else:
        missing.append("rendered_validation")
    review_ready = rendered.get("status") == "current" and review.get("status") == "current"
    if review_ready:
        completed.append("mlr_review")
    else:
        missing.append("mlr_review")
    package_ready = review_ready and package.get("status") == "current"
    if package_ready:
        completed.append("mlr_export")
    else:
        missing.append("mlr_export")

    if not brief_ready:
        next_step = {"tool": "create_campaign_brief", "channel": None}
    elif not inputs_ready:
        next_step = {"tool": "preflight_campaign_inputs", "channel": None}
    elif not journey_ready:
        next_step = {"tool": "generate_audience_journey", "channel": None}
    elif not architecture_ready:
        next_step = {"tool": "generate_message_architecture", "channel": None}
    elif missing_copy_channels:
        next_step = {"tool": "generate_channel_copy", "channel": missing_copy_channels[0]}
    elif pre_render.get("status") != "current":
        next_step = {"tool": "validate_claims_and_fair_balance", "channel": None}
    elif missing_outputs:
        next_step = {"tool": f"render_{missing_outputs[0]}", "channel": missing_outputs[0]}
    elif rendered.get("status") != "current":
        next_step = {"tool": "validate_rendered_assets", "channel": None}
    elif not review_ready:
        next_step = {"tool": "render_mlr_review", "channel": None}
    elif not package_ready:
        next_step = {"tool": "export_mlr_package", "channel": None}
    else:
        next_step = {"tool": None, "channel": None}
    return completed, missing, next_step
