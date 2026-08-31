"""Shared rendering utilities — template loading, brand tokens, validation gate."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import stat
from importlib.resources import files
from pathlib import Path
from typing import Any

_VALID_CHANNELS = frozenset({"email", "banner", "poster"})
_OUTPUT_FILENAMES = {"email": "email.html", "banner": "banner.svg", "poster": "poster.pdf"}
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_CUSTOM_TEMPLATE_LIMIT = 128_000


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _gate(status: str, code: str, reason: str | None) -> dict[str, str | None]:
    return {"status": status, "code": code, "reason": reason}


def _file_state(path: Path, *, limit: int | None = None) -> dict[str, Any]:
    """Capture regular-file bytes and identity without following symlinks."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        return {
            "exists": False,
            "kind": "missing",
            "identity": None,
            "sha256": None,
            "size": None,
            "error": "file is missing",
        }
    except OSError:
        return {
            "exists": False,
            "kind": "unreadable",
            "identity": None,
            "sha256": None,
            "size": None,
            "error": "file could not be inspected",
        }
    identity = {"device": before.st_dev, "inode": before.st_ino, "mode": stat.S_IFMT(before.st_mode)}
    if stat.S_ISLNK(before.st_mode):
        return {
            "exists": True,
            "kind": "symlink",
            "identity": identity,
            "sha256": None,
            "size": None,
            "error": "file may not be a symlink",
        }
    if not stat.S_ISREG(before.st_mode):
        return {
            "exists": True,
            "kind": "non_regular",
            "identity": identity,
            "sha256": None,
            "size": None,
            "error": "file must be regular",
        }
    if limit is not None and before.st_size > limit:
        return {
            "exists": True,
            "kind": "oversized",
            "identity": identity,
            "sha256": None,
            "size": before.st_size,
            "error": "file exceeds the supported size limit",
        }
    try:
        payload = path.read_bytes()
        after = path.lstat()
    except OSError:
        return {
            "exists": True,
            "kind": "unreadable",
            "identity": identity,
            "sha256": None,
            "size": None,
            "error": "file could not be read",
        }
    after_identity = {"device": after.st_dev, "inode": after.st_ino, "mode": stat.S_IFMT(after.st_mode)}
    if (
        not stat.S_ISREG(after.st_mode)
        or after_identity != identity
        or after.st_size != before.st_size
        or len(payload) != after.st_size
    ):
        return {
            "exists": True,
            "kind": "changed_during_read",
            "identity": after_identity,
            "sha256": None,
            "size": None,
            "error": "file changed during inspection",
        }
    return {
        "exists": True,
        "kind": "regular",
        "identity": identity,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "error": None,
    }


def _directory_state(path: Path, *, contained_by: Path | None = None) -> dict[str, Any]:
    """Capture a directory identity without accepting a symlinked resource root."""
    try:
        before = path.lstat()
    except FileNotFoundError:
        return {
            "path": str(path),
            "exists": False,
            "kind": "missing",
            "identity": None,
            "resolved_path": None,
            "contained": False,
            "error": "directory is missing",
        }
    except OSError:
        return {
            "path": str(path),
            "exists": False,
            "kind": "unreadable",
            "identity": None,
            "resolved_path": None,
            "contained": False,
            "error": "directory could not be inspected",
        }
    identity = {"device": before.st_dev, "inode": before.st_ino, "mode": stat.S_IFMT(before.st_mode)}
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        resolved = None
    if stat.S_ISLNK(before.st_mode):
        return {
            "path": str(path),
            "exists": True,
            "kind": "symlink",
            "identity": identity,
            "resolved_path": str(resolved) if resolved is not None else None,
            "contained": False,
            "error": "directory may not be a symlink",
        }
    if not stat.S_ISDIR(before.st_mode):
        return {
            "path": str(path),
            "exists": True,
            "kind": "non_directory",
            "identity": identity,
            "resolved_path": str(resolved) if resolved is not None else None,
            "contained": False,
            "error": "path must be a directory",
        }
    if resolved is None:
        return {
            "path": str(path),
            "exists": True,
            "kind": "unreadable",
            "identity": identity,
            "resolved_path": None,
            "contained": False,
            "error": "directory could not be resolved",
        }
    contained = True
    if contained_by is not None:
        try:
            resolved.relative_to(contained_by.resolve(strict=True))
        except (OSError, ValueError):
            contained = False
    try:
        after = path.lstat()
    except OSError:
        after = None
    after_identity = (
        {"device": after.st_dev, "inode": after.st_ino, "mode": stat.S_IFMT(after.st_mode)}
        if after is not None
        else None
    )
    if after_identity != identity:
        return {
            "path": str(path),
            "exists": True,
            "kind": "changed_during_read",
            "identity": after_identity,
            "resolved_path": str(resolved),
            "contained": contained,
            "error": "directory changed during inspection",
        }
    return {
        "path": str(path),
        "exists": True,
        "kind": "directory",
        "identity": identity,
        "resolved_path": str(resolved),
        "contained": contained,
        "error": None if contained else "directory escapes its resource root",
    }


def _unsafe_directory_state(path: Path, reason: str) -> dict[str, Any]:
    """Represent a child resource that must not be traversed after an unsafe ancestor."""
    return {
        "path": str(path),
        "exists": False,
        "kind": "unsafe_ancestor",
        "identity": None,
        "resolved_path": None,
        "contained": False,
        "error": reason,
    }


def _resource_path(*parts: str) -> Path:
    root = Path(str(files("open_pharma_plugins_campaign_studio")))
    return root.joinpath(*parts)


def _channel_list(channels: object) -> tuple[list[str], list[dict[str, str]]]:
    """Validate channels without sorting heterogeneous untrusted values."""
    if not isinstance(channels, list):
        return [], [_error("invalid_channels", "Channels must be a list of supported unique channel names.")]
    errors: list[dict[str, str]] = []
    valid: list[str] = []
    seen: set[str] = set()
    for channel in channels:
        if not isinstance(channel, str) or channel not in _VALID_CHANNELS:
            errors.append(_error("invalid_channels", "Channels must contain only supported string channel names."))
            continue
        if channel in seen:
            errors.append(_error("invalid_channels", "Channels must not contain duplicates."))
            continue
        seen.add(channel)
        valid.append(channel)
    if not valid:
        errors.append(_error("invalid_channels", "At least one supported channel is required."))
    return sorted(valid), errors


def _read_payload_artifact(campaign_brief_id: object, filename: str) -> tuple[object | None, list[dict[str, str]]]:
    from ._campaign_store import read_campaign_json

    value, error, _path = read_campaign_json(campaign_brief_id, filename)
    return value, [error] if error else []


def _selected_brand_root(brand_components: dict[str, object]) -> tuple[Path | None, str | None]:
    """Return the resolved persisted non-symlink kit root that owns selected brand files."""
    raw_root = brand_components.get("brand_kit_path")
    if not isinstance(raw_root, str) or not raw_root:
        return None, "persisted brand kit path is malformed"
    resolved_root = brand_components.get("resolved_brand_kit_path")
    if resolved_root is not None:
        if not isinstance(resolved_root, str) or not resolved_root or not Path(resolved_root).is_absolute():
            return None, "persisted resolved brand kit path is malformed"
        root = Path(resolved_root)
    else:
        # Migration compatibility: Task 1 persisted relative user paths exactly.
        # New manifests bind an absolute resolved root; old relative manifests may
        # still be resumed only when their lexical path resolves safely in the
        # current process directory.
        raw_path = Path(raw_root)
        try:
            root = raw_path if raw_path.is_absolute() else raw_path.resolve(strict=True)
        except OSError:
            return None, "persisted brand kit directory is missing"
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        return None, "persisted brand kit directory is missing"
    except OSError:
        return None, "persisted brand kit directory could not be inspected"
    if stat.S_ISLNK(root_stat.st_mode):
        return None, "persisted brand kit directory may not be a symlink"
    if not stat.S_ISDIR(root_stat.st_mode):
        return None, "persisted brand kit path must be a directory"
    return root, None


def _selected_brand_file_parent_error(root: Path, path: Path) -> str | None:
    """Reject an asset that escapes its kit or crosses a symlinked subdirectory."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return "persisted brand file path escapes the selected brand kit"
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        return "persisted brand file path is malformed"
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        try:
            component_stat = current.lstat()
        except OSError:
            return "persisted brand file parent could not be inspected"
        if stat.S_ISLNK(component_stat.st_mode):
            return "persisted brand file path may not cross a symlink"
        if not stat.S_ISDIR(component_stat.st_mode):
            return "persisted brand file parent must be a directory"
    return None


def _unsafe_brand_file_state(path: Path | None, error: str, *, resolved_path: Path | None = None) -> dict[str, Any]:
    return {
        "path": str(path) if path is not None else None,
        "resolved_path": str(resolved_path) if resolved_path is not None else None,
        "kind": "unsafe_path",
        "identity": None,
        "exists": False,
        "sha256": None,
        "size": None,
        "error": error,
    }


def _brand_files_state(brand_components: object) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(brand_components, dict):
        return result, [_error("invalid_brand_components", "Persisted brand-components.json must be an object.")]
    # Early/legacy manifests may have no selected live files.  Their absence is
    # still sealed in ``brand_components`` itself; a present but malformed
    # selection is never silently ignored.
    if "files" not in brand_components:
        return result, errors
    files_metadata = brand_components.get("files")
    if not isinstance(files_metadata, dict):
        return result, [_error("invalid_brand_components", "Persisted brand file metadata must be an object.")]
    brand_root, brand_root_error = _selected_brand_root(brand_components)
    for name, persisted in sorted(files_metadata.items(), key=lambda item: str(item[0])):
        key = str(name)
        if not isinstance(name, str) or not isinstance(persisted, dict) or not isinstance(persisted.get("path"), str):
            result[key] = {
                "path": persisted.get("path") if isinstance(persisted, dict) else None,
                "resolved_path": persisted.get("resolved_path") if isinstance(persisted, dict) else None,
                "kind": "invalid",
                "identity": None,
                "exists": False,
                "sha256": None,
                "size": None,
                "error": "persisted brand file metadata is malformed",
            }
            errors.append(_error("invalid_brand_components", "Persisted brand file metadata is malformed."))
            continue
        raw_path = Path(persisted["path"])
        raw_resolved_path = persisted.get("resolved_path")
        if raw_resolved_path is not None and (
            not isinstance(raw_resolved_path, str) or not raw_resolved_path or not Path(raw_resolved_path).is_absolute()
        ):
            state = _unsafe_brand_file_state(raw_path, "persisted resolved brand file path is malformed")
        elif raw_resolved_path is not None:
            path = Path(raw_resolved_path)
            state = None
        else:
            try:
                path = raw_path if raw_path.is_absolute() else raw_path.resolve(strict=True)
                state = None
            except OSError:
                state = _unsafe_brand_file_state(raw_path, "persisted brand file is missing")
        if state is not None:
            pass
        elif brand_root_error:
            state = _unsafe_brand_file_state(raw_path, brand_root_error, resolved_path=path)
        else:
            assert brand_root is not None
            parent_error = _selected_brand_file_parent_error(brand_root, path)
            state = (
                _unsafe_brand_file_state(raw_path, parent_error, resolved_path=path)
                if parent_error
                else {"path": str(raw_path), "resolved_path": str(path), **_file_state(path)}
            )
        result[key] = state
        if state["error"]:
            errors.append(_error("invalid_brand_file", f"Selected brand file is unsafe or unreadable: {key}."))
    return result, errors


def _default_template_states(
    templates_dir: Path, templates_directory_state: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Seal every shipped template only after its containing resource directory is safe."""
    errors: list[dict[str, str]] = []
    if templates_directory_state["error"]:
        return {}, [_error("default_templates_unreadable", "Default templates directory is unsafe or unreadable.")]
    try:
        entries = sorted(templates_dir.iterdir(), key=lambda path: path.name)
    except OSError:
        return {}, [_error("default_templates_unreadable", "Default templates could not be inspected.")]
    templates: dict[str, dict[str, Any]] = {}
    for path in entries:
        state = _file_state(path)
        templates[path.name] = {"path": str(path), **state}
        if state["error"]:
            errors.append(_error("default_template_invalid", f"Default template is unsafe or unreadable: {path.name}."))
    if not templates:
        errors.append(_error("default_templates_unreadable", "No shipped default templates were found."))
    return templates, errors


def validation_input_payload(campaign_brief_id: str, channels: list[str]) -> dict:
    """Return deterministic, fail-closed input provenance sealed by pre-render validation."""
    from . import __version__
    from ._workflow_validation import (
        BrandState,
        validate_brand_components,
        validate_campaign_brief,
        validate_channel_copy,
        validate_claims,
        validate_input_provenance,
    )

    try:
        ordered_channels, errors = _channel_list(channels)
        brief, artifact_errors = _read_payload_artifact(campaign_brief_id, "campaign-brief.json")
        errors.extend(artifact_errors)
        if not isinstance(brief, dict):
            errors.append(_error("invalid_campaign_brief", "Persisted campaign-brief.json must be an object."))
        claims, artifact_errors = _read_payload_artifact(campaign_brief_id, "approved-claims.json")
        errors.extend(artifact_errors)
        if claims is not None and not isinstance(claims, list):
            errors.append(_error("invalid_approved_claims", "Persisted approved-claims.json must be an array."))
        brand_components, artifact_errors = _read_payload_artifact(campaign_brief_id, "brand-components.json")
        errors.extend(artifact_errors)
        input_provenance, artifact_errors = _read_payload_artifact(campaign_brief_id, "input-provenance.json")
        errors.extend(artifact_errors)
        selected_brand_files, brand_errors = _brand_files_state(brand_components)
        errors.extend(brand_errors)
        selected_channel_copies: dict[str, object | None] = {}
        for channel in ordered_channels:
            copy, copy_errors = _read_payload_artifact(campaign_brief_id, f"copy-{channel}.json")
            selected_channel_copies[channel] = copy
            errors.extend(copy_errors)
            if copy is not None and (
                not isinstance(copy, dict) or copy.get("channel") != channel or not isinstance(copy.get("copy"), dict)
            ):
                errors.append(_error("invalid_channel_copy", f"Persisted copy artifact is malformed: {channel}."))
        brief_result = validate_campaign_brief(brief, campaign_brief_id)
        errors.extend(
            _error("invalid_campaign_brief", f"Persisted campaign-brief.json: {message}")
            for message in brief_result.errors
        )
        validated_brief = brief_result.value if isinstance(brief_result.value, dict) else None
        if validated_brief is None:
            claims_result = None
            errors.append(_error("invalid_approved_claims", "Approved claims depend on a valid campaign brief."))
        else:
            claims_result = validate_claims(claims, validated_brief)
            errors.extend(
                _error("invalid_approved_claims", f"Persisted approved-claims.json: {message}")
                for message in claims_result.errors
            )
        brand_result = validate_brand_components(brand_components)
        errors.extend(
            _error("invalid_brand_components", f"Persisted brand-components.json: {message}")
            for message in brand_result.errors
        )
        validated_claims = (
            claims_result.value if claims_result is not None and isinstance(claims_result.value, list) else None
        )
        validated_brand = brand_result.value if isinstance(brand_result.value, BrandState) else None
        if validated_brief is None or validated_claims is None or validated_brand is None:
            errors.append(
                _error(
                    "invalid_input_provenance",
                    "Input provenance depends on valid campaign brief, claims, and brand components.",
                )
            )
        else:
            provenance_result = validate_input_provenance(
                input_provenance,
                validated_brief,
                validated_claims,
                validated_brand,
            )
            errors.extend(
                _error("invalid_input_provenance", f"Persisted input-provenance.json: {message}")
                for message in provenance_result.errors
            )
            claims_by_id = {claim["claim_id"]: claim for claim in validated_claims}
            for selected_channel, copy in selected_channel_copies.items():
                copy_result = validate_channel_copy(
                    copy,
                    campaign_brief_id,
                    selected_channel,
                    validated_brief,
                    claims_by_id,
                    validated_brand,
                )
                errors.extend(
                    _error(
                        "invalid_channel_copy",
                        f"Persisted copy-{selected_channel}.json: {message}",
                    )
                    for message in copy_result.errors
                )
        resource_root = _resource_path()
        package_state = _directory_state(resource_root)
        if package_state["error"]:
            policy_directory_state = _unsafe_directory_state(
                resource_root / "policy", "package resource directory is unsafe or unreadable"
            )
            templates_directory_state = _unsafe_directory_state(
                resource_root / "templates", "package resource directory is unsafe or unreadable"
            )
        else:
            policy_directory_state = _directory_state(resource_root / "policy", contained_by=resource_root)
            templates_directory_state = _directory_state(resource_root / "templates", contained_by=resource_root)
        resource_directories = {
            "package": package_state,
            "policy": policy_directory_state,
            "templates": templates_directory_state,
        }
        for name, state in resource_directories.items():
            if state["error"]:
                errors.append(
                    _error("resource_directory_invalid", f"Shipped {name} resource directory is unsafe or unreadable.")
                )

        policy_path = resource_root / "policy" / "rules.json"
        if policy_directory_state["error"]:
            policy_state = {
                "path": str(policy_path),
                "exists": False,
                "kind": "unsafe_ancestor",
                "identity": None,
                "sha256": None,
                "size": None,
                "error": "policy resource directory is unsafe or unreadable",
            }
        else:
            policy_state = {"path": str(policy_path), **_file_state(policy_path)}
        if policy_state["error"]:
            errors.append(_error("policy_invalid", "Current policy file is unsafe or unreadable."))
        templates, template_errors = _default_template_states(resource_root / "templates", templates_directory_state)
        errors.extend(template_errors)
        return {
            "brief": brief,
            "applicable_approved_claims": claims,
            "brand_components": brand_components,
            "input_provenance": input_provenance,
            "selected_channel_copies": selected_channel_copies,
            "selected_brand_files": selected_brand_files,
            "policy": policy_state,
            "resource_directories": resource_directories,
            "default_templates": templates,
            "channels": ordered_channels,
            "campaign_studio_version": __version__,
            "errors": sorted(errors, key=lambda error: (error["code"], error["message"])),
        }
    except Exception:
        return {
            "brief": None,
            "applicable_approved_claims": None,
            "brand_components": None,
            "input_provenance": None,
            "selected_channel_copies": {},
            "selected_brand_files": {},
            "policy": None,
            "resource_directories": {},
            "default_templates": {},
            "channels": [],
            "campaign_studio_version": __version__,
            "errors": [
                _error("validation_payload_unavailable", "Validation input payload could not be assembled safely.")
            ],
        }


def validation_input_fingerprint(campaign_brief_id: str, channels: list[str]) -> str:
    """Bind a validation decision to the exact brief, claims, and copy inputs."""
    payload = validation_input_payload(campaign_brief_id, channels)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _brief_channels(brief: object) -> tuple[list[str], dict[str, str] | None]:
    if not isinstance(brief, dict):
        return [], _error("invalid_campaign_brief", "Campaign brief is missing or malformed.")
    channels, errors = _channel_list(brief.get("channels"))
    if errors:
        return [], _error("invalid_brief_channels", "Campaign brief channels are malformed.")
    return channels, None


def validation_gate_state(campaign_brief_id: str, channel: str | None = None) -> dict[str, str | None]:
    """Classify pre-render validation deterministically without throwing on persisted data faults."""
    from ._campaign_store import read_campaign_json

    brief, brief_error, _brief_path = read_campaign_json(campaign_brief_id, "campaign-brief.json")
    if brief_error or not isinstance(brief, dict):
        return _gate("failed", "invalid_campaign_brief", "Campaign brief is missing or malformed.")
    brief_channels, brief_channel_error = _brief_channels(brief)
    if brief_channel_error:
        return _gate("failed", brief_channel_error["code"], brief_channel_error["message"])
    report, report_error, _report_path = read_campaign_json(
        campaign_brief_id, "policy-checks.json", section="validation"
    )
    if report_error:
        if report_error["code"] == "artifact_missing":
            return _gate(
                "missing",
                "validation_report_missing",
                "No validation report found. Run validate_claims_and_fair_balance before rendering.",
            )
        return _gate("failed", "malformed_validation_report", "Validation report is malformed or unreadable.")
    if not isinstance(report, dict):
        return _gate("failed", "malformed_validation_report", "Validation report is malformed or unreadable.")
    if report.get("overall_pass") is not True:
        checks = report.get("policy_checks")
        if not isinstance(checks, list):
            return _gate("failed", "malformed_validation_report", "Validation report is malformed or unreadable.")
        failures = [check for check in checks if isinstance(check, dict) and check.get("result") == "fail"]
        names = [str(failure.get("check_name", "unknown")) for failure in failures]
        return _gate(
            "failed",
            "validation_failed",
            f"Validation failed ({len(failures)} failures: {', '.join(names)}). Fix and re-validate before rendering.",
        )
    validated_channels, report_channel_errors = _channel_list(report.get("channels_validated"))
    if report_channel_errors:
        return _gate("failed", "invalid_validation_channels", "Validation report channels are malformed.")
    if validated_channels != brief_channels:
        return _gate(
            "failed",
            "validation_channel_coverage_invalid",
            "The passing validation report does not cover every campaign channel. Re-validate before packaging.",
        )
    if channel is not None and (not isinstance(channel, str) or channel not in validated_channels):
        return _gate(
            "failed",
            "validation_channel_coverage_invalid",
            f"Channel '{channel}' was not included in the passing validation report. Re-validate before rendering.",
        )
    payload = validation_input_payload(campaign_brief_id, validated_channels)
    if payload["errors"]:
        return _gate(
            "stale",
            "validation_input_invalid",
            "Campaign inputs changed after validation. Re-run validation before rendering or packaging.",
        )
    stored_fingerprint = report.get("input_fingerprint")
    if not isinstance(stored_fingerprint, str) or not _SHA256_PATTERN.fullmatch(stored_fingerprint):
        return _gate("failed", "malformed_validation_report", "Validation report is malformed or unreadable.")
    current_fingerprint = validation_input_fingerprint(campaign_brief_id, validated_channels)
    if not hmac.compare_digest(stored_fingerprint, current_fingerprint):
        return _gate(
            "stale",
            "validation_input_changed",
            "Campaign inputs changed after validation. Re-run validation before rendering or packaging.",
        )
    from ._workflow_validation import validate_policy_report

    canonical_channels = brief.get("channels")
    claims = payload.get("applicable_approved_claims")
    brand = payload.get("brand_components")
    copies = payload.get("selected_channel_copies")
    if (
        not isinstance(canonical_channels, list)
        or not isinstance(claims, list)
        or not isinstance(brand, dict)
        or not isinstance(copies, dict)
    ):
        return _gate("failed", "malformed_validation_report", "Validation report is malformed or unreadable.")
    report_validation = validate_policy_report(
        campaign_brief_id,
        brief,
        canonical_channels,
        {claim["claim_id"]: claim for claim in claims},
        brand,
        copies,
        report,
    )
    if report_validation.errors:
        return _gate(
            "failed",
            "malformed_validation_report",
            "Validation report is incomplete or inconsistent with current canonical policy decisions.",
        )
    return _gate("current", "validation_current", None)


def check_validation_gate(campaign_brief_id: str, channel: str | None = None) -> str | None:
    """Return a compatibility error string unless the structured pre-render state is current."""
    state = validation_gate_state(campaign_brief_id, channel)
    return None if state["status"] == "current" else str(state["reason"])


def _rendered_report_fingerprint(report: dict) -> tuple[str | None, dict[str, str] | None]:
    values = [report[name] for name in ("pre_render_input_fingerprint", "input_fingerprint") if name in report]
    if len(values) != 1 or not isinstance(values[0], str) or not _SHA256_PATTERN.fullmatch(values[0]):
        return None, _error(
            "malformed_rendered_report", "Rendered validation report has an invalid pre-render fingerprint."
        )
    return values[0], None


def rendered_validation_gate_state(campaign_brief_id: str) -> dict[str, str | None]:
    """Classify rendered validation with strict expected-output and filesystem safety checks."""
    from ._campaign_store import (
        existing_artifact_path_result,
        existing_directory_path,
        existing_output_paths_result,
        read_campaign_json,
    )

    report, report_error, _report_path = read_campaign_json(
        campaign_brief_id, "rendered-assets.json", section="validation"
    )
    if report_error:
        if report_error["code"] == "artifact_missing":
            return _gate(
                "missing",
                "rendered_validation_report_missing",
                "No rendered validation report found. Run validate_rendered_assets before packaging.",
            )
        return _gate("failed", "malformed_rendered_report", "Rendered validation report is malformed or unreadable.")
    if not isinstance(report, dict):
        return _gate("failed", "malformed_rendered_report", "Rendered validation report is malformed or unreadable.")
    if report.get("overall_pass") is False:
        return _gate(
            "failed",
            "rendered_validation_failed",
            "Rendered validation did not pass. Fix rendered assets and re-run validation.",
        )
    if report.get("overall_pass") is not True or report.get("campaign_brief_id") != campaign_brief_id:
        return _gate("failed", "malformed_rendered_report", "Rendered validation report is malformed or unreadable.")
    outputs = report.get("outputs")
    if not isinstance(outputs, list):
        return _gate(
            "failed",
            "malformed_rendered_report",
            "Rendered validation report is malformed: outputs must be a list.",
        )
    declared_paths: list[str] = []
    for entry in outputs:
        if not isinstance(entry, dict):
            return _gate(
                "failed", "malformed_rendered_output", "Rendered validation report has a malformed output entry."
            )
        raw_path = entry.get("path")
        expected_hash = entry.get("sha256")
        expected_size = entry.get("size")
        if (
            not isinstance(raw_path, str)
            or not raw_path
            or "\x00" in raw_path
            or not Path(raw_path).is_absolute()
            or not isinstance(expected_hash, str)
            or type(expected_size) is not int
            or expected_size < 0
        ):
            return _gate(
                "failed", "malformed_rendered_output", "Rendered validation report has malformed output metadata."
            )
        if not _SHA256_PATTERN.fullmatch(expected_hash):
            return _gate(
                "failed", "malformed_rendered_output", "Rendered validation report has malformed output metadata."
            )
        declared_paths.append(raw_path)
    template_sources = report.get("template_sources")
    fingerprint, fingerprint_error = _rendered_report_fingerprint(report)
    if fingerprint_error:
        return _gate("failed", fingerprint_error["code"], fingerprint_error["message"])
    pre_state = validation_gate_state(campaign_brief_id)
    if pre_state["status"] != "current":
        status = "stale" if pre_state["status"] in {"missing", "stale"} else "failed"
        return _gate(
            status, f"pre_render_{pre_state['code']}", f"Pre-render validation is not current: {pre_state['reason']}"
        )
    brief, brief_error, _brief_path = read_campaign_json(campaign_brief_id, "campaign-brief.json")
    if brief_error or not isinstance(brief, dict):
        return _gate("failed", "invalid_campaign_brief", "Campaign brief is missing or malformed.")
    channels, channel_error = _brief_channels(brief)
    if channel_error:
        return _gate("failed", channel_error["code"], channel_error["message"])
    if not isinstance(template_sources, list):
        return _gate(
            "failed",
            "malformed_template_sources",
            "Rendered validation report has malformed custom-template source metadata.",
        )
    if "email" not in channels:
        if template_sources:
            return _gate(
                "failed",
                "malformed_template_sources",
                "A campaign without email must have no custom-template sources.",
            )
    else:
        provenance, provenance_error, _provenance_path = read_campaign_json(
            campaign_brief_id, "render-provenance-email.json"
        )
        if (
            provenance_error
            or not isinstance(provenance, dict)
            or set(provenance) != {"campaign_brief_id", "channel", "template"}
            or provenance.get("campaign_brief_id") != campaign_brief_id
            or provenance.get("channel") != "email"
            or not isinstance(provenance.get("template"), dict)
        ):
            return _gate(
                "failed",
                "custom_template_provenance_unsafe",
                "Email template provenance is missing or malformed after rendered validation.",
            )
        recorded_template = provenance["template"]
        kind = recorded_template.get("kind")
        expected_keys = {"kind", "path", "sha256", "size"}
        if kind == "custom":
            expected_keys.add("identity")
        if (
            kind not in {"default", "custom"}
            or set(recorded_template) != expected_keys
            or not isinstance(recorded_template.get("path"), str)
            or not recorded_template["path"]
            or "\x00" in recorded_template["path"]
            or not Path(recorded_template["path"]).is_absolute()
            or not isinstance(recorded_template.get("sha256"), str)
            or not _SHA256_PATTERN.fullmatch(recorded_template["sha256"])
            or type(recorded_template.get("size")) is not int
            or recorded_template["size"] < 0
        ):
            return _gate(
                "failed",
                "custom_template_provenance_unsafe",
                "Email template provenance is malformed after rendered validation.",
            )
        if kind == "default" and template_sources:
            return _gate(
                "failed",
                "malformed_template_sources",
                "Default email rendering must have no custom-template sources.",
            )
        if kind == "custom":
            if len(template_sources) != 1:
                return _gate(
                    "failed",
                    "malformed_template_sources",
                    "Custom email rendering must have exactly one sealed template source.",
                )
            reported_source = template_sources[0]
            reported_identity = reported_source.get("identity") if isinstance(reported_source, dict) else None
            if (
                not isinstance(reported_source, dict)
                or set(reported_source) != {"kind", "path", "sha256", "size", "identity"}
                or reported_source.get("kind") != "custom"
                or not isinstance(reported_source.get("path"), str)
                or not reported_source["path"]
                or "\x00" in reported_source["path"]
                or not Path(reported_source["path"]).is_absolute()
                or not isinstance(reported_source.get("sha256"), str)
                or not _SHA256_PATTERN.fullmatch(reported_source["sha256"])
                or type(reported_source.get("size")) is not int
                or not 0 <= reported_source["size"] <= _CUSTOM_TEMPLATE_LIMIT
                or not isinstance(reported_identity, dict)
                or set(reported_identity) != {"device", "inode", "mode"}
                or any(type(reported_identity.get(name)) is not int for name in ("device", "inode", "mode"))
            ):
                return _gate(
                    "failed",
                    "malformed_template_sources",
                    "Rendered validation report has malformed custom-template source metadata.",
                )
            if reported_source != recorded_template:
                return _gate(
                    "stale",
                    "custom_template_provenance_changed",
                    "Custom email template provenance changed after rendered validation.",
                )
            expected_identity = recorded_template.get("identity")
            if (
                not isinstance(expected_identity, dict)
                or set(expected_identity) != {"device", "inode", "mode"}
                or any(type(expected_identity.get(name)) is not int for name in ("device", "inode", "mode"))
                or recorded_template["size"] > _CUSTOM_TEMPLATE_LIMIT
            ):
                return _gate(
                    "failed",
                    "malformed_template_sources",
                    "Rendered validation report has malformed custom-template source metadata.",
                )
            current = _file_state(Path(recorded_template["path"]), limit=_CUSTOM_TEMPLATE_LIMIT)
            if current["error"]:
                return _gate(
                    "stale",
                    "custom_template_unsafe",
                    "The custom email template is missing or unsafe after rendered validation. Re-render and validate.",
                )
            if (
                current["sha256"] != recorded_template["sha256"]
                or current["size"] != recorded_template["size"]
                or current["identity"] != expected_identity
            ):
                return _gate(
                    "stale",
                    "custom_template_changed",
                    "The custom email template changed after rendered validation. Re-render and validate.",
                )
    current_fingerprint = validation_input_fingerprint(campaign_brief_id, channels)
    if not hmac.compare_digest(fingerprint, current_fingerprint):
        return _gate(
            "stale",
            "pre_render_input_changed",
            "Pre-render inputs changed after rendered validation. Re-run rendered validation.",
        )
    output_dir, output_dir_error = existing_directory_path(campaign_brief_id, "outputs")
    if output_dir_error:
        return _gate("failed", output_dir_error["code"], output_dir_error["message"])
    expected = {channel: str((output_dir / _OUTPUT_FILENAMES[channel])) for channel in channels} if output_dir else {}
    if output_dir is None:
        return _gate(
            "stale",
            "rendered_output_missing",
            "Rendered output is missing after rendered validation. Re-run rendering and validation.",
        )
    expected_paths = set(expected.values())
    if len(declared_paths) != len(set(declared_paths)) or set(declared_paths) != expected_paths:
        return _gate(
            "stale",
            "rendered_output_set_changed",
            "Declared rendered output set changed after rendered validation. Re-run rendered validation.",
        )
    entries_by_path = {entry["path"]: entry for entry in outputs}
    for channel, expected_path in expected.items():
        path, output_error = existing_artifact_path_result(
            campaign_brief_id, _OUTPUT_FILENAMES[channel], section="outputs"
        )
        if output_error:
            return _gate(
                "stale",
                "rendered_output_unsafe",
                "Rendered output is unsafe after rendered validation. Re-run rendering and validation.",
            )
        if path is None:
            return _gate(
                "stale",
                "rendered_output_missing",
                "Rendered output is missing after rendered validation. Re-run rendering and validation.",
            )
        if str(path) != expected_path:
            return _gate(
                "stale",
                "rendered_output_unsafe",
                "Rendered output is unsafe after rendered validation. Re-run rendering and validation.",
            )
        state = _file_state(path)
        if state["error"]:
            return _gate(
                "stale",
                "rendered_output_unsafe",
                "Rendered output is unsafe after rendered validation. Re-run rendering and validation.",
            )
        entry = entries_by_path[expected_path]
        if state["sha256"] != entry["sha256"] or state["size"] != entry["size"]:
            return _gate(
                "stale",
                "rendered_output_changed",
                "Rendered output changed after rendered validation. Re-run rendered validation.",
            )
    _safe_output_paths, output_entries_error = existing_output_paths_result(campaign_brief_id)
    if output_entries_error:
        return _gate(
            "failed",
            "unsafe_outputs_directory",
            "Campaign outputs contain unsafe entries. Remove them before rendered validation can be current.",
        )
    return _gate("current", "rendered_validation_current", None)


def rendered_validation_gate(campaign_brief_id: str) -> str | None:
    """Return a compatibility error string unless structured rendered validation is current."""
    state = rendered_validation_gate_state(campaign_brief_id)
    return None if state["status"] == "current" else str(state["reason"])


def load_template(name: str) -> str:
    """Load a Jinja2 template from the templates directory."""
    tpl_path = Path(str(files("open_pharma_plugins_campaign_studio") / "templates" / name))
    return tpl_path.read_text()


def load_brand_kit(campaign_brief_id: str) -> dict:
    """Load the inspected, persisted brand manifest and never a live fallback."""
    from ._inputs import current_brand_manifest

    manifest = current_brand_manifest(campaign_brief_id)
    if not manifest:
        raise ValueError("No brand-components.json found. Run preflight_campaign_inputs first.")
    return manifest


def extract_copy_text(copy_data: dict, channel: str) -> dict:
    """Extract text fields from channel copy into a flat dict for templates."""
    result: dict = {}

    if channel == "email":
        result["subject"] = _block_text(copy_data.get("subject"))
        result["preheader"] = _block_text(copy_data.get("preheader"))
        result["headline"] = _block_text(copy_data.get("headline"))
        result["cta"] = _block_text(copy_data.get("cta"))
        result["body_paragraphs"] = [_block_text(b) for b in copy_data.get("body", [])]
    elif channel == "banner":
        result["headline"] = _block_text(copy_data.get("headline"))
        result["sub_headline"] = _block_text(copy_data.get("sub_headline"))
        result["cta"] = _block_text(copy_data.get("cta"))
    elif channel == "poster":
        result["headline"] = _block_text(copy_data.get("headline"))
        result["subhead"] = _block_text(copy_data.get("subhead"))
        result["cta"] = _block_text(copy_data.get("cta"))
        result["body_paragraphs"] = [_block_text(b) for b in copy_data.get("body", [])]
        result["bullet_points"] = [_block_text(b) for b in (copy_data.get("bullet_points") or [])]
        result["footnotes"] = [_block_text(footnote) for footnote in (copy_data.get("footnotes") or [])]

    return result


def _block_text(block: dict | None) -> str:
    if not block:
        return ""
    if isinstance(block, dict):
        return block.get("text", "")
    return str(block)
