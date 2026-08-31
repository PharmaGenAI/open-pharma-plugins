"""File-system store for campaign briefs and artifacts.

Layout:
    <store_root>/
        _index.json              # manifest of all campaigns
        campaigns/
            <campaign_brief_id>/
                campaign-brief.json
                approved-claims.json
                audience-journey.json
                message-architecture.json
                copy-email.json
                copy-banner.json
                copy-poster.json
                validation/
                    claim-map.json
                    policy-checks.json
                    source-evidence.json
                outputs/
                    email.html
                    banner.svg
                    poster.pdf
                    mlr-review-summary.md
"""

from __future__ import annotations

import hashlib
import json
import math
import stat
from datetime import datetime, timezone
from pathlib import Path

from shared.filesystem import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    contained_path,
    ensure_private_dir,
    validate_component,
)


def store_root_path() -> Path:
    """Return the configured store root without creating it."""
    from shared.env import get_env

    return Path(
        get_env(
            "OPEN_PHARMA_CAMPAIGN_STORE_DIR",
            str(Path.home() / ".open-pharma-plugins" / "campaign-studio"),
        )
    )


def _store_dir() -> Path:
    """Return the private store root for write operations."""
    return ensure_private_dir(store_root_path())


def _storage_error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _configured_root_for_read() -> tuple[Path | None, dict[str, str] | None]:
    """Resolve an existing configured root only after rejecting a symlink root."""
    root = store_root_path().expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).absolute()
    try:
        if root.is_symlink():
            return None, _storage_error("unsafe_store_root", "Campaign store root may not be a symlink.")
        if root.exists():
            if not root.is_dir():
                return None, _storage_error("unsafe_store_root", "Campaign store root must be a directory.")
            return root.resolve(strict=True), None
    except OSError:
        return None, _storage_error("unsafe_store_root", "Campaign store root could not be inspected safely.")
    return root, None


def _existing_safe_directory(path: Path, *, code: str, label: str) -> tuple[Path | None, dict[str, str] | None]:
    """Return a real directory or a structured error; never follow a symlink."""
    try:
        if path.is_symlink():
            return None, _storage_error(code, f"{label} may not be a symlink.")
        if not path.exists():
            return None, None
        if not path.is_dir():
            return None, _storage_error(code, f"{label} must be a directory.")
        return path.resolve(strict=True), None
    except OSError:
        return None, _storage_error(code, f"{label} could not be inspected safely.")


def safe_campaign_path(campaign_brief_id: object) -> tuple[Path | None, dict[str, str] | None]:
    """Resolve a non-creating campaign path, rejecting every symlinked store component."""
    try:
        validate_component(campaign_brief_id, label="campaign_brief_id")
    except ValueError as exc:
        return None, {"code": "unsafe_campaign_brief_id", "message": str(exc)}
    root, root_error = _configured_root_for_read()
    if root_error or root is None:
        return None, root_error
    campaigns, campaigns_error = _existing_safe_directory(
        root / "campaigns", code="unsafe_campaign_path", label="Campaigns directory"
    )
    if campaigns_error:
        return None, campaigns_error
    if campaigns is None:
        return root / "campaigns" / str(campaign_brief_id), None
    try:
        campaigns.relative_to(root)
    except ValueError:
        return None, _storage_error("unsafe_campaign_path", "Campaigns directory escapes the configured store root.")
    campaign, campaign_error = _existing_safe_directory(
        campaigns / str(campaign_brief_id), code="unsafe_campaign_path", label="Campaign directory"
    )
    if campaign_error:
        return None, campaign_error
    if campaign is not None:
        try:
            campaign.relative_to(campaigns)
        except ValueError:
            return None, _storage_error("unsafe_campaign_path", "Campaign directory escapes the campaigns root.")
    return campaign or campaigns / str(campaign_brief_id), None


def existing_campaign_path_result(campaign_brief_id: object) -> tuple[Path | None, dict[str, str] | None]:
    """Return an existing real campaign directory or its structured safety error."""
    path, error = safe_campaign_path(campaign_brief_id)
    if error or path is None:
        return None, error
    return _existing_safe_directory(path, code="unsafe_campaign_path", label="Campaign directory")


def existing_campaign_path(campaign_brief_id: object) -> Path | None:
    """Return an existing campaign directory without creating any path."""
    path, _error = existing_campaign_path_result(campaign_brief_id)
    return path


def existing_directory_path(campaign_brief_id: object, section: str) -> tuple[Path | None, dict[str, str] | None]:
    """Return an existing real campaign subdirectory without following a symlink."""
    try:
        validate_component(section, label="campaign section")
    except ValueError as exc:
        return None, _storage_error("unsafe_artifact_directory", str(exc))
    campaign, campaign_error = existing_campaign_path_result(campaign_brief_id)
    if campaign_error or campaign is None:
        return None, campaign_error
    code = "unsafe_outputs_directory" if section == "outputs" else "unsafe_artifact_directory"
    directory, directory_error = _existing_safe_directory(
        campaign / section, code=code, label=f"Campaign {section} directory"
    )
    if directory_error or directory is None:
        return directory, directory_error
    try:
        directory.relative_to(campaign)
    except ValueError:
        return None, _storage_error(code, f"Campaign {section} directory escapes the campaign path.")
    return directory, None


def existing_artifact_path_result(
    campaign_brief_id: object, filename: str, *, section: str | None = None
) -> tuple[Path | None, dict[str, str] | None]:
    """Return a real contained regular artifact file or a structured safety error."""
    try:
        validate_component(filename, label="artifact filename")
    except ValueError as exc:
        return None, _storage_error("unsafe_artifact_path", str(exc))
    if section is None:
        directory, error = existing_campaign_path_result(campaign_brief_id)
    else:
        directory, error = existing_directory_path(campaign_brief_id, section)
    if error or directory is None:
        return None, error
    candidate = directory / filename
    try:
        if candidate.is_symlink():
            return None, _storage_error("unsafe_artifact_path", "Campaign artifact may not be a symlink.")
        if not candidate.exists():
            return None, None
        if not candidate.is_file():
            return None, _storage_error("unsafe_artifact_path", "Campaign artifact must be a regular file.")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(directory)
        return resolved, None
    except (OSError, ValueError):
        return None, _storage_error("unsafe_artifact_path", "Campaign artifact path is unsafe or unreadable.")


def existing_artifact_path(campaign_brief_id: object, filename: str, *, section: str | None = None) -> Path | None:
    """Return an existing contained artifact path without creating it."""
    path, _error = existing_artifact_path_result(campaign_brief_id, filename, section=section)
    return path


def read_existing_json(path: Path | None) -> tuple[object | None, str | None]:
    """Read a JSON file for a read-only caller without leaking filesystem exceptions."""
    if path is None:
        return None, "missing"
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_invalid_json_constant,
            parse_float=_finite_json_float,
            object_pairs_hook=_unique_json_object,
        )
        if not _json_within_safe_limits(value):
            return None, "JSON exceeds safe nesting or node limits"
        return value, None
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        RecursionError,
        OverflowError,
        MemoryError,
    ) as exc:
        return None, f"unreadable JSON: {exc}"


def _json_within_safe_limits(value: object) -> bool:
    """Bound parser output before status/seal serialization can recurse or exhaust resources."""
    maximum_depth = 256
    maximum_nodes = 100_000
    nodes = 0
    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if depth > maximum_depth or nodes > maximum_nodes:
            return False
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)
    return True


def _invalid_json_constant(value: str) -> object:
    """Reject non-standard numeric constants so seal JSON remains canonical."""
    raise ValueError(f"non-finite JSON constant: {value}")


def _finite_json_float(value: str) -> float:
    """Reject syntactically valid but non-finite numeric literals."""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate object keys rather than silently selecting a mutable last value."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_campaign_json(
    campaign_brief_id: object, filename: str, *, section: str | None = None
) -> tuple[object | None, dict[str, str] | None, Path | None]:
    """Safely read a persisted JSON artifact for fail-closed status and seal consumers."""
    path, path_error = existing_artifact_path_result(campaign_brief_id, filename, section=section)
    if path_error:
        return None, path_error, None
    if path is None:
        return None, _storage_error("artifact_missing", f"Campaign artifact is missing: {filename}"), None
    data, read_error = read_existing_json(path)
    if read_error:
        return (
            None,
            _storage_error("artifact_json_unreadable", f"Campaign artifact JSON is unreadable: {filename}"),
            path,
        )
    return data, None, path


def existing_output_paths(campaign_brief_id: object) -> list[Path]:
    """List regular files in an existing outputs directory, sorted and non-creating."""
    paths, _error = existing_output_paths_result(campaign_brief_id)
    return paths


def existing_output_paths_result(campaign_brief_id: object) -> tuple[list[Path], dict[str, object] | None]:
    """List safe regular output files and report unsafe siblings without hiding the safe ones."""
    outputs, error = existing_directory_path(campaign_brief_id, "outputs")
    if error or outputs is None:
        return [], error
    try:
        result: list[Path] = []
        entry_errors: list[dict[str, str]] = []
        for entry in sorted(outputs.iterdir(), key=lambda path: path.name):
            try:
                entry_stat = entry.lstat()
            except OSError:
                entry_errors.append(
                    _storage_error("unsafe_output_entry", f"Campaign outputs contain an unsafe entry: {entry.name}.")
                )
                continue
            if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISREG(entry_stat.st_mode):
                entry_errors.append(
                    _storage_error("unsafe_output_entry", f"Campaign outputs contain an unsafe entry: {entry.name}.")
                )
                continue
            resolved = entry.resolve(strict=True)
            resolved.relative_to(outputs)
            result.append(resolved)
        if entry_errors:
            return (
                result,
                {
                    "code": "unsafe_outputs_directory",
                    "message": "Campaign outputs contain unsafe entries.",
                    "entries": entry_errors,
                },
            )
        return result, None
    except (OSError, ValueError):
        return [], _storage_error("unsafe_outputs_directory", "Campaign outputs could not be inspected safely.")


def campaign_dir(campaign_brief_id: str) -> Path:
    """Return (and create) the directory for a specific campaign."""
    return ensure_private_dir(contained_path(_store_dir(), "campaigns", campaign_brief_id))


def validation_dir(campaign_brief_id: str) -> Path:
    """Return (and create) the validation subdirectory."""
    return ensure_private_dir(contained_path(campaign_dir(campaign_brief_id), "validation"))


def outputs_dir(campaign_brief_id: str) -> Path:
    """Return (and create) the outputs subdirectory."""
    return ensure_private_dir(contained_path(campaign_dir(campaign_brief_id), "outputs"))


def generate_campaign_id(campaign_name: str, brand: str) -> str:
    """Generate a stable campaign ID from name + brand."""
    raw = f"{campaign_name}:{brand}"
    short_hash = hashlib.sha256(raw.encode()).hexdigest()[:8]
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in campaign_name.lower())[:30]
    return f"{safe_name}_{short_hash}"


def save_artifact(campaign_brief_id: str, filename: str, data: dict) -> Path:
    """Save a JSON artifact to the campaign directory."""
    path = contained_path(campaign_dir(campaign_brief_id), filename)
    atomic_write_json(path, data)
    return path


def load_artifact(campaign_brief_id: str, filename: str) -> dict | None:
    """Load a JSON artifact without creating storage; corrupt persisted JSON remains an error."""
    path = existing_artifact_path(campaign_brief_id, filename)
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_validation_artifact(campaign_brief_id: str, filename: str, data: dict) -> Path:
    """Save a validation artifact."""
    path = contained_path(validation_dir(campaign_brief_id), filename)
    atomic_write_json(path, data)
    return path


def load_validation_artifact(campaign_brief_id: str, filename: str) -> dict | None:
    """Load a validation artifact without creating storage; corrupt persisted JSON remains an error."""
    path = existing_artifact_path(campaign_brief_id, filename, section="validation")
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_output(campaign_brief_id: str, filename: str, content: str) -> Path:
    """Save a rendered output (HTML, SVG, PDF bytes, MD)."""
    path = contained_path(outputs_dir(campaign_brief_id), filename)
    atomic_write_text(path, content)
    return path


def save_output_bytes(campaign_brief_id: str, filename: str, content: bytes) -> Path:
    """Save binary output (e.g. PDF)."""
    path = contained_path(outputs_dir(campaign_brief_id), filename)
    atomic_write_bytes(path, content)
    return path


def list_campaigns() -> list[dict]:
    """List all campaigns from the index."""
    index = _load_index()
    return index.get("campaigns", [])


def save_brief(brief: dict) -> None:
    """Save a campaign brief and update the index."""
    campaign_brief_id = brief["campaign_brief_id"]
    save_artifact(campaign_brief_id, "campaign-brief.json", brief)
    _update_index(brief)


def load_brief(campaign_brief_id: str) -> dict | None:
    """Load a campaign brief by ID."""
    return load_artifact(campaign_brief_id, "campaign-brief.json")


def _load_index() -> dict:
    index_path = _store_dir() / "_index.json"
    if index_path.exists():
        return json.loads(index_path.read_text(encoding="utf-8"))
    return {"campaigns": []}


def _update_index(brief: dict) -> None:
    index = _load_index()
    campaigns = index.get("campaigns", [])
    campaigns = [c for c in campaigns if c.get("campaign_brief_id") != brief["campaign_brief_id"]]
    campaigns.append(
        {
            "campaign_brief_id": brief["campaign_brief_id"],
            "campaign_name": brief.get("campaign_name", ""),
            "brand": brief.get("brand", ""),
            "mode": brief.get("mode", ""),
            "channels": brief.get("channels", []),
            "created_at": brief.get("generated_at", datetime.now(timezone.utc).isoformat()),
        }
    )
    index["campaigns"] = campaigns
    index_path = _store_dir() / "_index.json"
    atomic_write_json(index_path, index)
