"""Canonical fail-closed MLR review and content-addressed export service."""

from __future__ import annotations

import hashlib
import html
import io
import json
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined, select_autoescape

from shared.filesystem import (
    DirectoryIdentity,
    FileExpectation,
    SecurePublishError,
    capture_directory_identity,
    prepare_secure_directory,
    secure_atomic_publish,
    validate_component,
)

from ._campaign_store import (
    _finite_json_float,
    _invalid_json_constant,
    _json_within_safe_limits,
    _unique_json_object,
    existing_artifact_path_result,
    existing_campaign_path_result,
    existing_directory_path,
    existing_output_paths_result,
)
from ._renderer import rendered_validation_gate_state, validation_gate_state, validation_input_payload
from ._workflow_validation import (
    validate_audience_journey,
    validate_brand_components,
    validate_campaign_brief,
    validate_channel_copy,
    validate_claims,
    validate_input_provenance,
    validate_message_architecture,
    validate_policy_report,
)

_JSON_LIMIT = 2_000_000
_TEXT_LIMIT = 2_000_000
_PDF_LIMIT = 10_000_000
_REVIEWER_NOTES_LIMIT = 50_000
_CHANNEL_OUTPUTS = {"email": "email.html", "banner": "banner.svg", "poster": "poster.pdf"}
_ROOT_FILES = (
    "campaign-brief.json",
    "input-provenance.json",
    "approved-claims.json",
    "brand-components.json",
    "audience-journey.json",
    "message-architecture.json",
)
_VALIDATION_FILES = ("claim-map.json", "policy-checks.json", "source-evidence.json", "rendered-assets.json")
_REVIEW_FILES = ("mlr-review-summary.md", "mlr-review.html")
_SHA256 = frozenset("0123456789abcdef")
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_SOURCE_ROW_KEYS = frozenset({"document_id", "document_name", "page_number", "excerpt"})
_RENDERED_REPORT_KEYS = frozenset(
    {
        "campaign_brief_id",
        "overall_pass",
        "validated_at",
        "pre_render_input_fingerprint",
        "channel_results",
        "outputs",
        "template_sources",
    }
)
_RENDERED_CHANNEL_KEYS = frozenset({"channel", "checks", "overall_pass"})
_RENDERED_CHECK_KEYS = frozenset({"check_name", "result", "detail"})
_RENDERED_OUTPUT_KEYS = frozenset({"path", "sha256", "size"})
_TEMPLATE_SOURCE_KEYS = frozenset({"kind", "path", "sha256", "size", "identity"})
_TEMPLATE_IDENTITY_KEYS = frozenset({"device", "inode", "mode"})


class MlrContractError(Exception):
    """A stable ordinary-JSON contract failure."""

    def __init__(self, code: str, message: str, *, items: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.items = list(dict.fromkeys(items or []))

    def payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.items:
            error["items"] = self.items
        return {"error": error}


@dataclass(frozen=True)
class FileSnapshot:
    relative_path: str
    path: Path
    payload: bytes
    sha256: str
    size: int
    identity: tuple[int, int, int, int, int]

    def metadata(self) -> dict[str, Any]:
        return {"path": self.relative_path, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class ReviewPlan:
    campaign_brief_id: str
    campaign_dir: Path
    output_dir: Path
    output_dir_identity: DirectoryIdentity
    snapshots: tuple[FileSnapshot, ...]
    model: dict[str, Any]
    markdown: bytes
    html: bytes


@dataclass(frozen=True)
class PersistedReview:
    plan: ReviewPlan
    snapshots: tuple[FileSnapshot, ...]


@dataclass(frozen=True)
class PersistedPackage:
    manifest_path: Path
    archive_path: Path
    package_digest: str


def error_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, MlrContractError):
        return exc.payload()
    return {
        "error": {
            "code": "mlr_package_failed",
            "message": "The MLR review package could not be assembled safely.",
        }
    }


def normalise_reviewer_notes(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MlrContractError("invalid_reviewer_notes", "reviewer_notes must be plain text when supplied.")
    if len(value.encode("utf-8")) > _REVIEWER_NOTES_LIMIT:
        raise MlrContractError("invalid_reviewer_notes", "reviewer_notes exceeds the supported size limit.")
    return value


def build_review_plan(campaign_brief_id: str, reviewer_notes: object = None) -> ReviewPlan:
    """Read one immutable evidence snapshot and build both review representations."""
    try:
        validate_component(campaign_brief_id, label="campaign_brief_id")
    except ValueError as exc:
        raise MlrContractError("unsafe_campaign_brief_id", str(exc)) from exc
    notes = normalise_reviewer_notes(reviewer_notes)
    campaign_dir, campaign_error = existing_campaign_path_result(campaign_brief_id)
    if campaign_error:
        raise MlrContractError(str(campaign_error["code"]), str(campaign_error["message"]))
    if campaign_dir is None:
        raise MlrContractError(
            "campaign_not_found", f"Campaign brief '{campaign_brief_id}' was not found.", items=["campaign-brief.json"]
        )
    validation_dir, validation_error = existing_directory_path(campaign_brief_id, "validation")
    output_dir, output_error = existing_directory_path(campaign_brief_id, "outputs")
    directory_items: list[str] = []
    if validation_error or validation_dir is None:
        directory_items.append("validation/")
        directory_items.extend(f"validation/{name}" for name in _VALIDATION_FILES)
    if output_error or output_dir is None:
        directory_items.append("outputs/")
    if directory_items:
        for name in _ROOT_FILES:
            path, _error = existing_artifact_path_result(campaign_brief_id, name)
            if path is None:
                directory_items.append(name)
        brief_path, _brief_error = existing_artifact_path_result(campaign_brief_id, "campaign-brief.json")
        if brief_path is not None:
            try:
                early_brief = _parse_json(_snapshot_path(brief_path, "campaign-brief.json", limit=_JSON_LIMIT))
            except MlrContractError:
                early_brief = None
            if isinstance(early_brief, dict) and isinstance(early_brief.get("channels"), list):
                for channel in early_brief["channels"]:
                    if isinstance(channel, str) and channel in _CHANNEL_OUTPUTS:
                        copy_path, _copy_error = existing_artifact_path_result(
                            campaign_brief_id, f"copy-{channel}.json"
                        )
                        if copy_path is None:
                            directory_items.append(f"copy-{channel}.json")
                        if output_error or output_dir is None:
                            directory_items.append(f"outputs/{_CHANNEL_OUTPUTS[channel]}")
        raise MlrContractError(
            "mlr_package_incomplete",
            "Required campaign directories are missing or unsafe.",
            items=directory_items,
        )
    output_dir_identity = _directory_identity(output_dir, "unsafe_outputs_directory")

    brief_snapshot = _required_snapshot(campaign_brief_id, "campaign-brief.json", section=None, limit=_JSON_LIMIT)
    brief_value = _parse_json(brief_snapshot)
    brief_result = validate_campaign_brief(brief_value, campaign_brief_id)
    if brief_result.errors or not isinstance(brief_result.value, dict):
        raise MlrContractError(
            "invalid_campaign_artifacts",
            "The campaign brief does not satisfy its persisted contract.",
            items=[f"campaign-brief.json: {item}" for item in brief_result.errors],
        )
    brief = brief_result.value
    channels = brief.get("channels")
    if not isinstance(channels, list):
        raise MlrContractError("invalid_campaign_artifacts", "Campaign channels are malformed.")

    required_specs = [
        (f"root:{name}", name, None, _JSON_LIMIT) for name in _ROOT_FILES if name != "campaign-brief.json"
    ]
    required_specs.extend((f"copy:{channel}", f"copy-{channel}.json", None, _JSON_LIMIT) for channel in channels)
    required_specs.extend((f"validation:{name}", name, "validation", _JSON_LIMIT) for name in _VALIDATION_FILES)
    required_specs.extend(
        (
            f"rendered:{channel}",
            _CHANNEL_OUTPUTS[channel],
            "outputs",
            _PDF_LIMIT if channel == "poster" else _TEXT_LIMIT,
        )
        for channel in channels
    )
    collected = _collect_required_snapshots(campaign_brief_id, required_specs)
    root_snapshots = {"campaign-brief.json": brief_snapshot}
    root_snapshots.update({name: collected[f"root:{name}"] for name in _ROOT_FILES if name != "campaign-brief.json"})
    copy_snapshots = {channel: collected[f"copy:{channel}"] for channel in channels}
    validation_snapshots = {name: collected[f"validation:{name}"] for name in _VALIDATION_FILES}
    rendered_snapshots = {channel: collected[f"rendered:{channel}"] for channel in channels}
    _reject_unsafe_output_siblings(campaign_brief_id)

    values = {name: _parse_json(snapshot) for name, snapshot in root_snapshots.items() if name.endswith(".json")}
    validation_values = {name: _parse_json(snapshot) for name, snapshot in validation_snapshots.items()}
    claims_result = validate_claims(values["approved-claims.json"], brief)
    brand_result = validate_brand_components(values["brand-components.json"])
    claims = claims_result.value
    brand = brand_result.value
    semantic_errors: list[str] = []
    semantic_errors.extend(f"approved-claims.json: {item}" for item in claims_result.errors)
    semantic_errors.extend(f"brand-components.json: {item}" for item in brand_result.errors)
    if not isinstance(claims, list):
        claims = []
    claims_by_id = {claim["claim_id"]: claim for claim in claims if isinstance(claim, dict)}
    provenance_result = validate_input_provenance(values["input-provenance.json"], brief, claims, brand)
    semantic_errors.extend(f"input-provenance.json: {item}" for item in provenance_result.errors)
    journey_result = validate_audience_journey(values["audience-journey.json"], campaign_brief_id, brief, claims_by_id)
    semantic_errors.extend(f"audience-journey.json: {item}" for item in journey_result.errors)
    stage_names = _journey_stage_names(journey_result.value)
    architecture_result = validate_message_architecture(
        values["message-architecture.json"], campaign_brief_id, brief, claims_by_id, stage_names
    )
    semantic_errors.extend(f"message-architecture.json: {item}" for item in architecture_result.errors)
    copy_values: dict[str, dict[str, Any]] = {}
    for channel, snapshot in copy_snapshots.items():
        raw_copy = _parse_json(snapshot)
        copy_result = validate_channel_copy(raw_copy, campaign_brief_id, channel, brief, claims_by_id, brand)
        semantic_errors.extend(f"copy-{channel}.json: {item}" for item in copy_result.errors)
        if isinstance(copy_result.value, dict):
            copy_values[channel] = copy_result.value
    if semantic_errors:
        raise MlrContractError(
            "invalid_campaign_artifacts",
            "Required campaign artifacts are malformed, mismatched, or semantically invalid.",
            items=semantic_errors,
        )

    pre_gate = validation_gate_state(campaign_brief_id)
    rendered_gate = rendered_validation_gate_state(campaign_brief_id)
    gate_errors = _gate_errors(pre_gate, rendered_gate)
    if gate_errors:
        raise MlrContractError(
            "validation_not_current",
            "Both pre-render and rendered validation must be current and passing.",
            items=gate_errors,
        )

    policy_report = validation_values["policy-checks.json"]
    rendered_report = validation_values["rendered-assets.json"]
    claim_map = validation_values["claim-map.json"]
    source_evidence = validation_values["source-evidence.json"]
    report_errors = _validate_validation_artifacts(
        campaign_brief_id,
        brief,
        channels,
        claims_by_id,
        values["brand-components.json"],
        copy_values,
        policy_report,
        rendered_report,
        claim_map,
        source_evidence,
        rendered_snapshots,
    )
    if report_errors:
        raise MlrContractError(
            "invalid_validation_artifacts",
            "Validation evidence is malformed, incomplete, or inconsistent.",
            items=report_errors,
        )

    all_snapshots = tuple(
        sorted(
            [
                *root_snapshots.values(),
                *copy_snapshots.values(),
                *validation_snapshots.values(),
                *rendered_snapshots.values(),
            ],
            key=lambda item: item.relative_path,
        )
    )
    model = _build_review_model(
        brief,
        values,
        copy_values,
        policy_report,
        rendered_report,
        claims_by_id,
        all_snapshots,
        rendered_snapshots,
        notes,
    )
    markdown = _render_markdown(model).encode("utf-8")
    html = _render_html(model).encode("utf-8")
    return ReviewPlan(
        campaign_brief_id=campaign_brief_id,
        campaign_dir=campaign_dir,
        output_dir=output_dir,
        output_dir_identity=output_dir_identity,
        snapshots=all_snapshots,
        model=model,
        markdown=markdown,
        html=html,
    )


def publish_review(plan: ReviewPlan) -> dict[str, Any]:
    """Publish the two canonical review representations after a final gate/snapshot check."""
    _recheck_plan(plan)
    paths = {
        plan.output_dir / "mlr-review-summary.md": plan.markdown,
        plan.output_dir / "mlr-review.html": plan.html,
    }
    _atomic_publish(
        paths,
        directory_guards={plan.output_dir: ("unsafe_output_directory", plan.output_dir_identity)},
    )
    snapshots = {
        name: _snapshot_path(plan.output_dir / name, f"outputs/{name}", limit=_TEXT_LIMIT) for name in _REVIEW_FILES
    }
    return {
        "campaign_brief_id": plan.campaign_brief_id,
        "draft": True,
        "demo_mode": bool(plan.model["campaign"]["demo_mode"]),
        "qualified_mlr_review_required": True,
        "completeness": plan.model["completeness"],
        "outputs": [
            snapshots[name].metadata() | {"absolute_path": str(snapshots[name].path)} for name in _REVIEW_FILES
        ],
    }


def verify_persisted_review(campaign_brief_id: str) -> PersistedReview:
    """Verify both persisted review representations against current canonical inputs."""
    review_snapshots = tuple(
        _required_snapshot(campaign_brief_id, name, section="outputs", limit=_TEXT_LIMIT) for name in _REVIEW_FILES
    )
    notes = _reviewer_notes_from_html(review_snapshots[1].payload)
    plan = build_review_plan(campaign_brief_id, notes)
    expected_payloads = {
        "outputs/mlr-review-summary.md": plan.markdown,
        "outputs/mlr-review.html": plan.html,
    }
    if any(snapshot.payload != expected_payloads[snapshot.relative_path] for snapshot in review_snapshots):
        raise MlrContractError(
            "review_outputs_stale",
            "Persisted MLR review outputs do not match the current canonical review.",
        )
    return PersistedReview(plan=plan, snapshots=review_snapshots)


def verify_persisted_package(review: PersistedReview) -> PersistedPackage:
    """Verify persisted manifest and ZIP against the exporter's complete canonical contract."""
    plan = review.plan
    package_snapshots = tuple(sorted((*plan.snapshots, *review.snapshots), key=lambda item: item.relative_path))
    digest = _package_digest(package_snapshots)
    expected_manifest = _canonical_json_bytes(_build_manifest(plan, package_snapshots, digest))
    archive_name = f"{plan.campaign_brief_id}-mlr-{digest}.zip"
    manifest_snapshot = _required_snapshot(
        plan.campaign_brief_id, "package-manifest.json", section="outputs", limit=_JSON_LIMIT
    )
    if manifest_snapshot.payload != expected_manifest:
        raise MlrContractError(
            "package_manifest_invalid",
            "The package manifest does not match the complete canonical package contract.",
        )
    archive_snapshot = _required_snapshot(plan.campaign_brief_id, archive_name, section="outputs", limit=64_000_000)
    _verify_archive(archive_snapshot.payload, package_snapshots, expected_manifest)
    _recheck_plan(plan)
    for snapshot in (*review.snapshots, manifest_snapshot, archive_snapshot):
        _recheck_snapshot(snapshot)
    return PersistedPackage(
        manifest_path=manifest_snapshot.path,
        archive_path=archive_snapshot.path,
        package_digest=digest,
    )


def export_package(
    campaign_brief_id: str,
    *,
    destination_dir: object = None,
    reviewer_notes: object = None,
) -> dict[str, Any]:
    """Render current reviews and atomically publish a deterministic manifest and archive."""
    plan = build_review_plan(campaign_brief_id, reviewer_notes)
    review_payloads = {
        "mlr-review-summary.md": plan.markdown,
        "mlr-review.html": plan.html,
    }
    review_snapshots = tuple(
        _generated_snapshot(plan.output_dir / name, f"outputs/{name}", review_payloads[name]) for name in _REVIEW_FILES
    )
    package_snapshots = tuple(sorted((*plan.snapshots, *review_snapshots), key=lambda item: item.relative_path))
    digest = _package_digest(package_snapshots)
    manifest = _build_manifest(plan, package_snapshots, digest)
    manifest_bytes = _canonical_json_bytes(manifest)
    archive_name = f"{campaign_brief_id}-mlr-{digest}.zip"
    archive_bytes = _build_archive(package_snapshots, manifest_bytes)
    _verify_archive(archive_bytes, package_snapshots, manifest_bytes)

    destination = _prepare_destination(destination_dir, plan, archive_name) if destination_dir is not None else None
    targets = {
        plan.output_dir / "mlr-review-summary.md": plan.markdown,
        plan.output_dir / "mlr-review.html": plan.html,
        plan.output_dir / "package-manifest.json": manifest_bytes,
        plan.output_dir / archive_name: archive_bytes,
    }
    destination_archive: Path | None = None
    directory_guards = {plan.output_dir: ("unsafe_output_directory", plan.output_dir_identity)}
    target_expectations: dict[Path, tuple[str, FileSnapshot | None]] = {}
    if destination is not None:
        destination_archive = destination[0] / archive_name
        if destination_archive in targets or destination_archive == plan.output_dir / "package-manifest.json":
            raise MlrContractError("unsafe_destination", "Destination aliases campaign package evidence.")
        destination_existing = _validate_existing_destination_file(destination_archive, archive_bytes)
        targets[destination_archive] = archive_bytes
        directory_guards[destination[0]] = ("unsafe_destination", destination[1])
        target_expectations[destination_archive] = ("unsafe_destination", destination_existing)
    for path in targets:
        if path not in target_expectations:
            target_expectations[path] = (
                "unsafe_output_path",
                _capture_existing_target(path, f"outputs/{path.name}"),
            )
    _recheck_plan(plan)
    if destination is not None:
        _recheck_destination(destination)
    _atomic_publish(
        targets,
        directory_guards=directory_guards,
        target_expectations=target_expectations,
    )
    result: dict[str, Any] = {
        "campaign_brief_id": campaign_brief_id,
        "draft": True,
        "demo_mode": bool(plan.model["campaign"]["demo_mode"]),
        "qualified_mlr_review_required": True,
        "package_digest": digest,
        "manifest_path": str(plan.output_dir / "package-manifest.json"),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "manifest_size": len(manifest_bytes),
        "archive_path": str(plan.output_dir / archive_name),
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "archive_size": len(archive_bytes),
        "file_count": len(package_snapshots),
    }
    if destination_archive is not None:
        result["destination_archive_path"] = str(destination_archive)
    return result


def _generated_snapshot(path: Path, relative: str, payload: bytes) -> FileSnapshot:
    return FileSnapshot(
        relative,
        path,
        payload,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        (0, 0, stat.S_IFREG, len(payload), 0),
    )


def _reviewer_notes_from_html(payload: bytes) -> str:
    try:
        rendered = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MlrContractError("review_outputs_stale", "Persisted MLR review HTML is not UTF-8.") from exc
    marker = '<div class="notes">'
    count = rendered.count(marker)
    if count == 0:
        return ""
    if count != 1:
        raise MlrContractError("review_outputs_stale", "Persisted MLR review HTML has invalid reviewer notes.")
    start = rendered.index(marker) + len(marker)
    end = rendered.find("</div>", start)
    if end < 0:
        raise MlrContractError("review_outputs_stale", "Persisted MLR review HTML has invalid reviewer notes.")
    return html.unescape(rendered[start:end])


def _required_snapshot(campaign_id: str, filename: str, *, section: str | None, limit: int) -> FileSnapshot:
    path, error = existing_artifact_path_result(campaign_id, filename, section=section)
    relative = f"{section}/{filename}" if section else filename
    if error:
        raise MlrContractError(
            "mlr_package_incomplete", "A required artifact is unsafe.", items=[f"{relative}: {error['message']}"]
        )
    if path is None:
        raise MlrContractError("mlr_package_incomplete", "Required package artifacts are missing.", items=[relative])
    return _snapshot_path(path, relative, limit=limit)


def _collect_required_snapshots(
    campaign_id: str,
    specs: list[tuple[str, str, str | None, int]],
) -> dict[str, FileSnapshot]:
    snapshots: dict[str, FileSnapshot] = {}
    failures: list[str] = []
    for key, filename, section, limit in specs:
        relative = f"{section}/{filename}" if section else filename
        try:
            snapshots[key] = _required_snapshot(campaign_id, filename, section=section, limit=limit)
        except MlrContractError as exc:
            failures.extend(exc.items or [f"{relative}: {exc.message}"])
    if failures:
        raise MlrContractError(
            "mlr_package_incomplete",
            "Required package artifacts are missing, unsafe, or unreadable.",
            items=failures,
        )
    return snapshots


def _snapshot_path(path: Path, relative: str, *, limit: int) -> FileSnapshot:
    try:
        before = path.lstat()
    except OSError as exc:
        raise MlrContractError("artifact_unreadable", f"Artifact is missing or unreadable: {relative}.") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise MlrContractError("artifact_unsafe", f"Artifact must be a regular non-symlink file: {relative}.")
    if before.st_size > limit:
        raise MlrContractError("artifact_oversized", f"Artifact exceeds the supported size limit: {relative}.")
    try:
        payload = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise MlrContractError("artifact_unreadable", f"Artifact could not be read safely: {relative}.") from exc
    identity = (before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode), before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode), after.st_size, after.st_mtime_ns)
    if identity != after_identity or len(payload) != after.st_size:
        raise MlrContractError("artifact_changed", f"Artifact changed during inspection: {relative}.")
    return FileSnapshot(relative, path, payload, hashlib.sha256(payload).hexdigest(), len(payload), identity)


def _parse_json(snapshot: FileSnapshot) -> object:
    try:
        value = json.loads(
            snapshot.payload.decode("utf-8"),
            parse_constant=_invalid_json_constant,
            parse_float=_finite_json_float,
            object_pairs_hook=_unique_json_object,
        )
        if not _json_within_safe_limits(value):
            raise ValueError("JSON exceeds safe nesting or node limits")
        return value
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError, OverflowError, MemoryError) as exc:
        raise MlrContractError(
            "artifact_json_unreadable", f"Artifact JSON is malformed or unsafe: {snapshot.relative_path}."
        ) from exc


def _journey_stage_names(value: object) -> set[str] | None:
    if not isinstance(value, dict) or not isinstance(value.get("stages"), list):
        return None
    return {
        stage["stage"] for stage in value["stages"] if isinstance(stage, dict) and isinstance(stage.get("stage"), str)
    }


def _gate_errors(pre: dict[str, Any], rendered: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if pre.get("status") != "current":
        errors.append(f"pre-render gate [{pre.get('code')}]: {pre.get('reason')}")
    if rendered.get("status") != "current":
        errors.append(f"rendered gate [{rendered.get('code')}]: {rendered.get('reason')}")
    return errors


def _validate_exact_keys(
    value: object,
    expected: frozenset[str],
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(value, dict):
        return
    actual = set(value)
    undeclared = sorted(actual - expected)
    missing = sorted(expected - actual)
    if undeclared:
        errors.append(f"{label} contains undeclared fields: {', '.join(undeclared)}.")
    if missing:
        errors.append(f"{label} is missing declared fields: {', '.join(missing)}.")


def _validate_validation_artifacts(
    campaign_id: str,
    brief: dict[str, Any],
    channels: list[str],
    claims_by_id: dict[str, dict[str, Any]],
    brand_manifest: object,
    copies: dict[str, dict[str, Any]],
    policy: object,
    rendered: object,
    claim_map: object,
    source_evidence: object,
    rendered_snapshots: dict[str, FileSnapshot],
) -> list[str]:
    policy_validation = validate_policy_report(
        campaign_id,
        brief,
        channels,
        claims_by_id,
        brand_manifest,
        copies,
        policy,
    )
    errors = list(policy_validation.errors)
    canonical_claim_rows = list(policy_validation.claim_rows)

    valid_claim_map_shape = isinstance(claim_map, dict) and not any(
        not isinstance(key, str) or not isinstance(value, list) or any(not isinstance(item, str) for item in value)
        for key, value in (claim_map.items() if isinstance(claim_map, dict) else [])
    )
    if not valid_claim_map_shape:
        errors.append("claim-map.json must be a string-to-claim-ID-list object.")
    else:
        expected_claim_map: dict[str, list[str]] = {}
        for item in canonical_claim_rows:
            expected_claim_map.setdefault(item["statement"][:60], []).append(item["claim_id"])
        undeclared_claim_map_keys = sorted(set(claim_map) - set(expected_claim_map))
        if undeclared_claim_map_keys:
            errors.append(
                "claim-map.json contains undeclared statement keys: " + ", ".join(undeclared_claim_map_keys) + "."
            )
        if claim_map != expected_claim_map:
            errors.append("claim-map.json does not match the complete channel-scoped claim results.")

    valid_source_shape = isinstance(source_evidence, list) and not any(
        not isinstance(item, dict) for item in source_evidence
    )
    if not valid_source_shape:
        errors.append("source-evidence.json must be an array of source objects.")
    else:
        for index, item in enumerate(source_evidence):
            _validate_exact_keys(item, _SOURCE_ROW_KEYS, f"source-evidence.json[{index}]", errors)
        used_claim_ids = {item["claim_id"] for item in canonical_claim_rows}
        expected_source_evidence = [
            {
                "document_id": claim_id,
                "document_name": claim.get("source_document", ""),
                "page_number": None,
                "excerpt": claim.get("source_reference", ""),
            }
            for claim_id, claim in claims_by_id.items()
            if claim_id in used_claim_ids
        ]
        if source_evidence != expected_source_evidence:
            errors.append("source-evidence.json does not exactly match the approved claims used by the copy.")

    if not isinstance(rendered, dict):
        errors.append("rendered-assets.json must be an object.")
        return errors
    _validate_exact_keys(rendered, _RENDERED_REPORT_KEYS, "rendered-assets.json", errors)
    if rendered.get("campaign_brief_id") != campaign_id or rendered.get("overall_pass") is not True:
        errors.append("rendered-assets.json campaign identity/pass state is invalid.")
    if not _valid_timestamp(rendered.get("validated_at")):
        errors.append("rendered-assets.json validated_at is missing or invalid.")
    post_results = rendered.get("channel_results")
    if not isinstance(post_results, dict) or set(post_results) != set(channels):
        errors.append("rendered-assets.json channel_results coverage is invalid.")
    else:
        for channel in channels:
            result = post_results[channel]
            if (
                not isinstance(result, dict)
                or result.get("channel") != channel
                or result.get("overall_pass") is not True
            ):
                errors.append(f"rendered-assets.json channel result is invalid: {channel}.")
                continue
            _validate_exact_keys(
                result,
                _RENDERED_CHANNEL_KEYS,
                f"rendered-assets.json channel_results.{channel}",
                errors,
            )
            checks = result.get("checks")
            if isinstance(checks, list):
                for index, check in enumerate(checks):
                    _validate_exact_keys(
                        check,
                        _RENDERED_CHECK_KEYS,
                        f"rendered-assets.json channel_results.{channel}.checks[{index}]",
                        errors,
                    )
            expected_checks = [
                {"check_name": "output_exists", "result": "pass", "detail": ""},
                {"check_name": "rendered_contract", "result": "pass", "detail": ""},
                {"check_name": "prohibited_language", "result": "pass", "detail": ""},
            ]
            if checks != expected_checks:
                errors.append(f"rendered-assets.json does not contain the exact production checks in order: {channel}.")
    outputs = rendered.get("outputs")
    expected = {str(snapshot.path): snapshot for snapshot in rendered_snapshots.values()}
    if not isinstance(outputs, list) or any(not isinstance(item, dict) for item in outputs):
        errors.append("rendered-assets.json outputs is invalid.")
    else:
        for index, item in enumerate(outputs):
            _validate_exact_keys(item, _RENDERED_OUTPUT_KEYS, f"rendered-assets.json outputs[{index}]", errors)
        declared = {item.get("path"): item for item in outputs}
        if len(declared) != len(outputs) or set(declared) != set(expected):
            errors.append("rendered-assets.json output set does not match brief outputs.")
        for path, snapshot in expected.items():
            item = declared.get(path)
            if (
                not isinstance(item, dict)
                or item.get("sha256") != snapshot.sha256
                or type(item.get("size")) is not int
                or item.get("size") != snapshot.size
            ):
                errors.append(f"rendered-assets.json metadata does not match {snapshot.relative_path}.")
    template_sources = rendered.get("template_sources")
    if not isinstance(template_sources, list) or any(not isinstance(item, dict) for item in template_sources):
        errors.append("rendered-assets.json template_sources is invalid.")
    else:
        for index, item in enumerate(template_sources):
            _validate_exact_keys(
                item,
                _TEMPLATE_SOURCE_KEYS,
                f"rendered-assets.json template_sources[{index}]",
                errors,
            )
            identity = item.get("identity")
            _validate_exact_keys(
                identity,
                _TEMPLATE_IDENTITY_KEYS,
                f"rendered-assets.json template_sources[{index}].identity",
                errors,
            )
    return list(dict.fromkeys(errors))


def _build_review_model(
    brief: dict[str, Any],
    root_values: dict[str, object],
    copies: dict[str, dict[str, Any]],
    policy: dict[str, Any],
    rendered: dict[str, Any],
    claims_by_id: dict[str, dict[str, Any]],
    snapshots: tuple[FileSnapshot, ...],
    rendered_snapshots: dict[str, FileSnapshot],
    reviewer_notes: str,
) -> dict[str, Any]:
    channels = list(brief["channels"])
    claim_rows: list[dict[str, Any]] = []
    channel_models: list[dict[str, Any]] = []
    for channel in channels:
        pre = policy["channel_results"][channel]
        post = rendered["channel_results"][channel]
        for item in pre["claims_checked"]:
            claim = claims_by_id[item["declared_claim_id"]]
            claim_rows.append(
                {
                    "channel": channel,
                    "statement": item.get("statement", ""),
                    "claim_id": item.get("declared_claim_id", ""),
                    "approved_wording": claim.get("text", ""),
                    "matched_wording": item.get("matched_claim_text") or claim.get("text", ""),
                    "status": item.get("status", ""),
                    "deviation": item.get("deviation") or "None",
                    "source_document": claim.get("source_document", ""),
                    "source_reference": claim.get("source_reference", ""),
                }
            )
        rendered_snapshot = rendered_snapshots[channel]
        channel_models.append(
            {
                "channel": channel,
                "copy": copies[channel]["copy"],
                "pre_render_checks": pre["policy_checks"],
                "post_render_checks": post["checks"],
                "rendered_asset": rendered_snapshot.metadata(),
                "preview_kind": "visible PDF text" if channel == "poster" else "escaped exact source",
                "preview": _preview(rendered_snapshot, channel),
            }
        )
    provenance = root_values["input-provenance.json"]
    brand = root_values["brand-components.json"]
    brand_hashes = []
    if isinstance(brand, dict) and isinstance(brand.get("files"), dict):
        brand_hashes = [
            {"name": name, "sha256": item.get("sha256"), "size": item.get("size")}
            for name, item in sorted(brand["files"].items())
            if isinstance(item, dict)
        ]
    model = {
        "campaign": {
            "campaign_brief_id": brief["campaign_brief_id"],
            "campaign_name": brief["campaign_name"],
            "brand": brief["brand"],
            "indication": brief["indication"],
            "objective": brief["behavioral_objective"],
            "audience": brief["target_segment"],
            "channels": channels,
            "jurisdiction": brief["policy_jurisdiction"],
            "workflow": brief["approval_workflow"],
            "demo_mode": brief["demo_mode"],
        },
        "capability_version": _capability_version(),
        "policy": {"version": policy["policy_version"], "sha256": policy["policy_hash"]},
        "validation_time": rendered["validated_at"],
        "draft_boundary": (
            "Draft review aid only. Qualified Medical, Legal, and Regulatory reviewers must assess and approve "
            "all content before any use. Automated checks are not an approval decision."
        ),
        "channels": channel_models,
        "claim_rows": claim_rows,
        "provenance": provenance,
        "brand_files": brand_hashes,
        "artifacts": [snapshot.metadata() for snapshot in snapshots],
        "reviewer_notes": reviewer_notes,
        "completeness": {
            "required": len(snapshots),
            "present": len(snapshots),
            "missing": 0,
            "claim_rows": len(claim_rows),
            "channels": len(channels),
        },
    }
    return model


def _preview(snapshot: FileSnapshot, channel: str) -> str:
    if channel in {"email", "banner"}:
        try:
            return snapshot.payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MlrContractError("invalid_rendered_asset", f"{snapshot.relative_path} is not valid UTF-8.") from exc
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(snapshot.payload), strict=True)
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        raise MlrContractError("invalid_rendered_asset", "Poster PDF text could not be represented safely.") from exc


def _render_html(model: dict[str, Any]) -> str:
    template_path = Path(str(files("open_pharma_plugins_campaign_studio") / "templates" / "mlr-review.html.j2"))
    template_snapshot = _snapshot_path(template_path, "templates/mlr-review.html.j2", limit=_TEXT_LIMIT)
    try:
        source = template_snapshot.payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MlrContractError("review_template_invalid", "The shipped review template is not valid UTF-8.") from exc
    environment = Environment(
        autoescape=select_autoescape(default=True, default_for_string=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return environment.from_string(source).render(review=model)


def _render_markdown(model: dict[str, Any]) -> str:
    campaign = model["campaign"]
    lines = [
        f"# MLR review — {_md(campaign['campaign_name'])}",
        "",
        "> **DRAFT — QUALIFIED MLR REVIEW REQUIRED**",
        f"> {_md(model['draft_boundary'])}",
        "",
        "## Campaign overview",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Campaign ID | {_md(campaign['campaign_brief_id'])} |",
        f"| Brand | {_md(campaign['brand'])} |",
        f"| Indication | {_md(campaign['indication'])} |",
        f"| Audience | {_md(campaign['audience'])} |",
        f"| Objective | {_md(campaign['objective'])} |",
        f"| Channels | {_md(', '.join(campaign['channels']))} |",
        f"| Jurisdiction | {_md(campaign['jurisdiction'])} |",
        f"| Workflow | {_md(campaign['workflow'])} |",
        f"| Demo inputs | {'Yes' if campaign['demo_mode'] else 'No'} |",
        f"| Policy | {_md(model['policy']['version'])} (`{model['policy']['sha256']}`) |",
        f"| Render validation | {_md(model['validation_time'])} |",
        "",
        "## Claim-to-source evidence",
        "",
        "| Channel | Exact copy statement | Claim ID | Source wording | Matched wording | Status | Deviation | Source document | Source reference |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in model["claim_rows"]:
        lines.append(
            "| "
            + " | ".join(
                _md(row[name])
                for name in (
                    "channel",
                    "statement",
                    "claim_id",
                    "approved_wording",
                    "matched_wording",
                    "status",
                    "deviation",
                    "source_document",
                    "source_reference",
                )
            )
            + " |"
        )
    lines.extend(["", "## Validation checks", ""])
    for channel in model["channels"]:
        lines.extend(
            [f"### {_md(channel['channel'].title())}", "", "| Gate | Check | Result | Detail |", "|---|---|---|---|"]
        )
        for gate, checks in (("Pre-render", channel["pre_render_checks"]), ("Rendered", channel["post_render_checks"])):
            for check in checks:
                lines.append(
                    f"| {gate} | {_md(check.get('check_name', ''))} | {_md(check.get('result', ''))} | {_md(check.get('detail', ''))} |"
                )
        lines.append("")
    lines.extend(["## Artifact integrity", "", "| Relative path | Bytes | SHA-256 |", "|---|---:|---|"])
    for artifact in model["artifacts"]:
        lines.append(f"| {_md(artifact['path'])} | {artifact['size']} | `{artifact['sha256']}` |")
    lines.extend(
        [
            "",
            "## Source provenance",
            "",
            "```text",
            json.dumps(model["provenance"], indent=2, ensure_ascii=False),
            "```",
            "",
        ]
    )
    lines.extend(["## Selected brand files", "", "| File | Bytes | SHA-256 |", "|---|---:|---|"])
    for item in model["brand_files"]:
        lines.append(f"| {_md(item['name'])} | {item['size']} | `{item['sha256']}` |")
    if model["reviewer_notes"]:
        lines.extend(["", "## Reviewer notes", ""])
        lines.extend(f"    {line}" for line in model["reviewer_notes"].split("\n"))
    return "\n".join(lines).rstrip() + "\n"


def _md(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\r", "<br>")
        .replace("\n", "<br>")
    )


def _build_manifest(plan: ReviewPlan, snapshots: tuple[FileSnapshot, ...], digest: str) -> dict[str, Any]:
    payload = validation_input_payload(plan.campaign_brief_id, plan.model["campaign"]["channels"])
    templates = payload.get("default_templates") if isinstance(payload, dict) else None
    template_hashes = []
    if isinstance(templates, dict):
        template_hashes = [
            {"name": name, "sha256": item.get("sha256"), "size": item.get("size")}
            for name, item in sorted(templates.items())
            if isinstance(item, dict)
        ]
    return {
        "schema_version": "1.0",
        "campaign_brief_id": plan.campaign_brief_id,
        "campaign_studio_version": _capability_version(),
        "draft": True,
        "demo_mode": bool(plan.model["campaign"]["demo_mode"]),
        "qualified_mlr_review_required": True,
        "rendered_validation_time": plan.model["validation_time"],
        "policy": plan.model["policy"],
        "template_hashes": template_hashes,
        "source_provenance": plan.model["provenance"],
        "package_digest": digest,
        "files": [snapshot.metadata() for snapshot in snapshots],
    }


def _package_digest(snapshots: tuple[FileSnapshot, ...]) -> str:
    identity = [[item.relative_path, item.size, item.sha256] for item in snapshots]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": ")) + "\n").encode(
        "utf-8"
    )


def _build_archive(snapshots: tuple[FileSnapshot, ...], manifest: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, strict_timestamps=True
    ) as archive:
        entries = [(snapshot.relative_path, snapshot.payload) for snapshot in snapshots]
        entries.append(("package-manifest.json", manifest))
        for name, payload in sorted(entries):
            _validate_member_name(name)
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def _verify_archive(archive_bytes: bytes, snapshots: tuple[FileSnapshot, ...], manifest: bytes) -> None:
    expected = {snapshot.relative_path: snapshot for snapshot in snapshots}
    expected_names = set(expected) | {"package-manifest.json"}
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
            infos = archive.infolist()
            if [item.filename for item in infos] != sorted(expected_names) or len(infos) != len(expected_names):
                raise ValueError("archive member set/order mismatch")
            for info in infos:
                _validate_member_name(info.filename)
                if (
                    info.date_time != _ZIP_TIMESTAMP
                    or info.create_system != 3
                    or info.external_attr >> 16 != stat.S_IFREG | 0o600
                ):
                    raise ValueError("archive metadata mismatch")
                payload = archive.read(info)
                if info.filename == "package-manifest.json":
                    if payload != manifest:
                        raise ValueError("manifest bytes mismatch")
                else:
                    snapshot = expected[info.filename]
                    if len(payload) != snapshot.size or hashlib.sha256(payload).hexdigest() != snapshot.sha256:
                        raise ValueError("archive content mismatch")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise MlrContractError("archive_verification_failed", "The deterministic archive failed verification.") from exc


def _validate_member_name(name: str) -> None:
    path = Path(name)
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != name
    ):
        raise MlrContractError("unsafe_archive_member", "Archive member path is unsafe.")


def _recheck_plan(plan: ReviewPlan) -> None:
    if _directory_identity(plan.output_dir, "unsafe_outputs_directory") != plan.output_dir_identity:
        raise MlrContractError("output_directory_changed", "Campaign outputs directory changed during packaging.")
    for snapshot in plan.snapshots:
        _recheck_snapshot(snapshot)
    gates = _gate_errors(
        validation_gate_state(plan.campaign_brief_id), rendered_validation_gate_state(plan.campaign_brief_id)
    )
    if gates:
        raise MlrContractError(
            "validation_not_current", "Validation changed during packaging; prior outputs were preserved.", items=gates
        )


def _recheck_snapshot(snapshot: FileSnapshot) -> None:
    current = _snapshot_path(snapshot.path, snapshot.relative_path, limit=max(snapshot.size, 1))
    if current.identity != snapshot.identity or current.payload != snapshot.payload:
        raise MlrContractError("artifact_changed", f"Artifact changed during packaging: {snapshot.relative_path}.")


def _directory_identity(path: Path, code: str) -> DirectoryIdentity:
    try:
        return capture_directory_identity(path)
    except SecurePublishError as exc:
        raise MlrContractError(code, "Required directory must be a real non-symlink directory.") from exc


def _reject_unsafe_output_siblings(campaign_id: str) -> None:
    _paths, error = existing_output_paths_result(campaign_id)
    if error:
        raise MlrContractError("unsafe_outputs_directory", str(error.get("message", "Outputs are unsafe.")))


def _atomic_publish(
    files_to_write: dict[Path, bytes],
    *,
    directory_guards: dict[Path, tuple[str, DirectoryIdentity]] | None = None,
    target_expectations: dict[Path, tuple[str, FileSnapshot | None]] | None = None,
) -> None:
    """Translate Campaign contracts around the shared secure publication primitive."""
    supplied_guards = directory_guards or {}
    guards: dict[Path, tuple[str, DirectoryIdentity]] = {}
    for parent in {destination.parent for destination in files_to_write}:
        guards[parent] = supplied_guards.get(parent) or (
            "unsafe_output_directory",
            _directory_identity(parent, "unsafe_output_directory"),
        )
    supplied_expectations = target_expectations or {}
    expectations: dict[Path, tuple[str, FileSnapshot | None]] = {}
    for destination in files_to_write:
        expectations[destination] = supplied_expectations.get(destination) or (
            "unsafe_output_path",
            _capture_existing_target(destination, destination.name),
        )
    shared_expectations = {
        destination: (
            None if snapshot is None else FileExpectation(payload=snapshot.payload, identity=snapshot.identity)
        )
        for destination, (_code, snapshot) in expectations.items()
    }
    try:
        secure_atomic_publish(
            files_to_write,
            directory_identities={parent: identity for parent, (_code, identity) in guards.items()},
            target_expectations=shared_expectations,
        )
    except SecurePublishError as exc:
        if exc.reason in {"target_changed", "unsafe_target"}:
            code = expectations.get(exc.path, (guards.get(exc.path.parent, ("unsafe_output_path", None))[0], None))[0]
            message = f"Output target changed or became unsafe during publication: {exc.path.name}."
        elif exc.reason in {"directory_changed", "unsafe_directory"}:
            code = guards.get(exc.path, ("unsafe_output_directory", None))[0]
            message = "Guarded output directory changed or became unsafe during publication."
        else:
            code = "output_write_failed"
            message = "Package outputs could not be written atomically."
        recovery_items = [f"Original target retained at {path}" for path in exc.recovery_paths]
        recovery_items.extend(f"Private recovery residue retained at {path}" for path in exc.residue_paths)
        recovery_items.extend(f"Concurrent recovery-name conflict retained at {path}" for path in exc.conflict_paths)
        recovery_items.extend(exc.recovery_notes)
        raise MlrContractError(code, message, items=recovery_items) from exc


def _validate_write_target(path: Path) -> None:
    try:
        if path.is_symlink():
            raise MlrContractError("unsafe_output_path", f"Output path may not be a symlink: {path.name}.")
        if path.exists() and not path.is_file():
            raise MlrContractError("unsafe_output_path", f"Output path must be a regular file: {path.name}.")
    except OSError as exc:
        raise MlrContractError("unsafe_output_path", f"Output path could not be inspected: {path.name}.") from exc


def _prepare_destination(raw: object, plan: ReviewPlan, archive_name: str) -> tuple[Path, DirectoryIdentity]:
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise MlrContractError("unsafe_destination", "destination_dir must be a nonblank directory path.")
    lexical = Path(raw).expanduser()
    if ".." in lexical.parts:
        raise MlrContractError("unsafe_destination", "destination_dir may not contain parent traversal.")
    candidate = lexical if lexical.is_absolute() else Path.cwd() / lexical
    candidate = candidate.absolute()
    try:
        candidate.relative_to(plan.campaign_dir)
    except ValueError:
        pass
    else:
        raise MlrContractError("unsafe_destination", "destination_dir must remain outside campaign evidence.")
    _inspect_destination_ancestors(candidate)
    try:
        prepared, identity = prepare_secure_directory(candidate, mode=0o700)
    except SecurePublishError as exc:
        raise MlrContractError("unsafe_destination", "destination_dir could not be created safely.") from exc
    protected_identities = {
        _directory_identity(plan.campaign_dir, "unsafe_destination"),
        _directory_identity(plan.output_dir, "unsafe_destination"),
        _directory_identity(plan.campaign_dir / "validation", "unsafe_destination"),
    }
    if identity in protected_identities:
        raise MlrContractError("unsafe_destination", "destination_dir may not alias campaign evidence directories.")
    destination_file = prepared / archive_name
    for snapshot in plan.snapshots:
        if destination_file == snapshot.path:
            raise MlrContractError("unsafe_destination", "Destination aliases a packaged source artifact.")
    return prepared, identity


def _inspect_destination_ancestors(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            if not current.exists() and not current.is_symlink():
                continue
            info = current.lstat()
        except OSError as exc:
            raise MlrContractError("unsafe_destination", "A destination ancestor could not be inspected.") from exc
        if stat.S_ISLNK(info.st_mode):
            raise MlrContractError("unsafe_destination", "destination_dir may not cross a symlink.")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise MlrContractError("unsafe_destination", "A destination ancestor is not a directory.")


def _recheck_destination(destination: tuple[Path, DirectoryIdentity]) -> None:
    path, identity = destination
    _inspect_destination_ancestors(path)
    if _directory_identity(path, "unsafe_destination") != identity:
        raise MlrContractError("unsafe_destination", "destination_dir changed during export.")


def _capture_existing_target(path: Path, relative: str) -> FileSnapshot | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MlrContractError("unsafe_output_path", f"Output path could not be inspected: {path.name}.") from exc
    return _snapshot_path(path, relative, limit=50_000_000)


def _validate_existing_destination_file(path: Path, expected: bytes) -> FileSnapshot | None:
    if path.is_symlink():
        raise MlrContractError("unsafe_destination", "Destination archive may not be a symlink.")
    if not path.exists():
        return None
    if not path.is_file():
        raise MlrContractError("unsafe_destination", "Destination archive path must be a regular file.")
    current = _snapshot_path(path, path.name, limit=50_000_000)
    if current.payload != expected:
        raise MlrContractError("unsafe_destination", "Destination archive path contains unrelated content.")
    return current


def _valid_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _SHA256


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_timestamp(value: object) -> bool:
    if not _nonblank(value):
        return False
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _capability_version() -> str:
    from . import __version__

    return __version__
