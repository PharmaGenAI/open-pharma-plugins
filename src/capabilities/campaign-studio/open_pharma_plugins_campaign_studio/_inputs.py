"""Fail-closed resolution and provenance for Campaign Studio source inputs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import Counter
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Any, TypedDict
from xml.etree import ElementTree

from pydantic import ValidationError

from shared.filesystem import validate_component

from ._campaign_store import load_artifact, load_brief, save_artifact, save_brief
from .models.claims import ApprovedClaim, canonical_claim_category


class CampaignInputResult(TypedDict):
    """Stable result contract shared by every Campaign Studio input operation."""

    campaign_brief_id: str
    ready: bool
    demo_mode: bool
    claims: list[dict]
    claims_count: int
    total_claims: int
    applicable_claim_count: int
    excluded_claims_count: int
    excluded_claim_count: int
    brand_files_count: int
    categories: list[str]
    exclusions: list[dict]
    claims_path: str | None
    brand_kit_path: str | None
    brand_manifest: dict
    provenance_path: str | None
    hashes: dict
    warnings: list[str]
    errors: list[str]
    active_inputs: dict
    candidate_inputs: dict
    logo_path: str | None
    product_image_path: str | None
    palette: dict
    typography: dict
    legal: dict
    files: dict


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of exactly the bytes at *path*."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_brand_manifest(campaign_brief_id: str) -> dict:
    """Return the persisted brand manifest used by downstream renderers."""
    if not _safe_campaign_id(campaign_brief_id):
        return {}
    try:
        return load_artifact(campaign_brief_id, "brand-components.json") or {}
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def preflight_inputs(
    campaign_brief_id: str,
    claims_path: str | None,
    brand_kit_path: str | None,
    demo_mode: bool,
) -> CampaignInputResult:
    """Validate and persist the complete claim and brand input set for a brief."""
    claims_hint, claims_hint_is_demo = _claims_source_hint(claims_path, None, demo_mode)
    brand_hint, brand_hint_is_demo = _brand_kit_source_hint(brand_kit_path, None, demo_mode)
    try:
        return _preflight_inputs(campaign_brief_id, claims_path, brand_kit_path, demo_mode)
    except Exception as exc:  # The MCP contract is fail-closed even for filesystem failures.
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[str(exc)],
            source_demo=claims_hint_is_demo or brand_hint_is_demo,
            candidate_claims_path=claims_hint,
            candidate_brand_kit_path=brand_hint,
        )


def _preflight_inputs(
    campaign_brief_id: str,
    claims_path: str | None,
    brand_kit_path: str | None,
    demo_mode: bool,
) -> CampaignInputResult:
    """Internal preflight implementation, separated so all failures share one result shape."""
    claims_hint, claims_hint_is_demo = _claims_source_hint(claims_path, None, demo_mode)
    brand_hint, brand_hint_is_demo = _brand_kit_source_hint(brand_kit_path, None, demo_mode)
    try:
        brief = load_brief(campaign_brief_id)
    except Exception as exc:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[str(exc)],
            source_demo=claims_hint_is_demo or brand_hint_is_demo,
            candidate_claims_path=claims_hint,
            candidate_brand_kit_path=brand_hint,
        )
    if not brief:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[f"Campaign brief not found: {campaign_brief_id}"],
            source_demo=claims_hint_is_demo or brand_hint_is_demo,
            candidate_claims_path=claims_hint,
            candidate_brand_kit_path=brand_hint,
        )

    claims_hint, claims_hint_is_demo = _claims_source_hint(claims_path, brief, demo_mode)
    brand_hint, brand_hint_is_demo = _brand_kit_source_hint(brand_kit_path, brief, demo_mode)
    try:
        claims_source, claims_is_demo, claims_error = _resolve_claims_source(claims_path, brief, demo_mode)
    except Exception as exc:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[str(exc)],
            source_demo=claims_hint_is_demo or brand_hint_is_demo,
            candidate_claims_path=claims_hint,
            candidate_brand_kit_path=brand_hint,
        )
    candidate_claims_path = claims_source or claims_hint
    source_demo = claims_is_demo or claims_hint_is_demo
    try:
        kit_source, kit_is_demo, kit_error = _resolve_brand_kit_source(brand_kit_path, brief, demo_mode)
    except Exception as exc:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[str(exc)],
            source_demo=source_demo or brand_hint_is_demo,
            candidate_claims_path=candidate_claims_path,
            candidate_brand_kit_path=brand_hint,
        )
    candidate_brand_kit_path = kit_source or brand_hint
    source_demo = source_demo or kit_is_demo or brand_hint_is_demo
    errors = [error for error in (claims_error, kit_error) if error]
    if errors:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=errors,
            source_demo=source_demo,
            candidate_claims_path=candidate_claims_path,
            candidate_brand_kit_path=candidate_brand_kit_path,
        )

    assert claims_source is not None
    assert kit_source is not None
    claims: list[dict] | None = None
    exclusions: list[dict] = []
    brand_manifest: dict | None = None
    try:
        claims, exclusions, claims_error = _validated_claims(claims_source, brief)
        if claims_error:
            return build_input_result(
                campaign_brief_id,
                ready=False,
                errors=[claims_error],
                exclusions=exclusions,
                source_demo=source_demo,
                candidate_claims_path=claims_source,
                candidate_brand_kit_path=kit_source,
            )
        if not claims:
            return build_input_result(
                campaign_brief_id,
                ready=False,
                errors=["No applicable approved claims remain after preflight."],
                exclusions=exclusions,
                source_demo=source_demo,
                candidate_claims_path=claims_source,
                candidate_brand_kit_path=kit_source,
                candidate_claims=claims,
            )
        brand_manifest, brand_error = _brand_manifest(kit_source)
        if brand_error:
            return build_input_result(
                campaign_brief_id,
                ready=False,
                errors=[brand_error],
                exclusions=exclusions,
                source_demo=source_demo,
                candidate_claims_path=claims_source,
                candidate_brand_kit_path=kit_source,
                candidate_claims=claims,
            )

        assert brand_manifest is not None
        _activate_input_set(
            campaign_brief_id,
            brief,
            claims=(claims_source, claims, claims_is_demo),
            brand=(kit_source, brand_manifest, kit_is_demo),
        )
    except Exception as exc:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[str(exc)],
            exclusions=exclusions,
            source_demo=source_demo,
            candidate_claims_path=claims_source,
            candidate_brand_kit_path=kit_source,
            candidate_claims=claims,
            candidate_brand_manifest=brand_manifest,
        )
    return build_input_result(
        campaign_brief_id,
        ready=True,
        exclusions=exclusions,
        source_demo=source_demo,
        candidate_claims_path=claims_source,
        candidate_brand_kit_path=kit_source,
        candidate_claims=claims,
        candidate_brand_manifest=brand_manifest,
    )


def resolve_and_persist_claims(
    campaign_brief_id: str, source: str | None, demo_mode: bool, categories: list[str] | None = None
) -> CampaignInputResult:
    """Resolve claims independently for the retrieval tool without fallback."""
    source_hint, hint_is_demo = _claims_source_hint(source, None, demo_mode)
    try:
        brief = load_brief(campaign_brief_id)
    except Exception as exc:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[str(exc)],
            source_demo=hint_is_demo,
            candidate_claims_path=source_hint,
        )
    if not brief:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[f"Campaign brief not found: {campaign_brief_id}"],
            source_demo=hint_is_demo,
            candidate_claims_path=source_hint,
        )
    source_hint, hint_is_demo = _claims_source_hint(source, brief, demo_mode)
    try:
        path, is_demo, error = _resolve_claims_source(source, brief, demo_mode)
    except Exception as exc:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[str(exc)],
            source_demo=hint_is_demo,
            candidate_claims_path=source_hint,
        )
    candidate_claims_path = path or source_hint
    source_demo = is_demo or hint_is_demo
    if error:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[error],
            source_demo=source_demo,
            candidate_claims_path=candidate_claims_path,
        )
    assert path is not None
    claims: list[dict] | None = None
    exclusions: list[dict] = []
    try:
        claims, exclusions, parse_error = _validated_claims(path, brief)
        if parse_error:
            return build_input_result(
                campaign_brief_id,
                ready=False,
                errors=[parse_error],
                exclusions=exclusions,
                source_demo=is_demo,
                candidate_claims_path=path,
            )
        if not claims:
            return build_input_result(
                campaign_brief_id,
                ready=False,
                errors=["No applicable approved claims remain after preflight."],
                exclusions=exclusions,
                source_demo=is_demo,
                candidate_claims_path=path,
                candidate_claims=claims,
            )
        if categories:
            invalid_categories = [category for category in categories if canonical_claim_category(category) is None]
            if invalid_categories:
                return build_input_result(
                    campaign_brief_id,
                    ready=False,
                    errors=[f"Invalid claim categories filter: {invalid_categories}"],
                    exclusions=exclusions,
                    source_demo=is_demo,
                    candidate_claims_path=path,
                    candidate_claims=claims,
                )
            category_set = {canonical_claim_category(category) for category in categories}
            selected_claims = [
                claim for claim in claims if canonical_claim_category(claim.get("category")) in category_set
            ]
            exclusions.extend(
                {"claim_id": claim["claim_id"], "reason": "category_not_selected"}
                for claim in claims
                if canonical_claim_category(claim.get("category")) not in category_set
            )
            claims = selected_claims
            if not claims:
                return build_input_result(
                    campaign_brief_id,
                    ready=False,
                    errors=["No applicable approved claims remain after category filtering."],
                    exclusions=exclusions,
                    source_demo=is_demo,
                    candidate_claims_path=path,
                    candidate_claims=claims,
                )
        _activate_input_set(campaign_brief_id, brief, claims=(path, claims, is_demo), brand=None)
    except Exception as exc:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[str(exc)],
            exclusions=exclusions,
            source_demo=is_demo,
            candidate_claims_path=path,
            candidate_claims=claims,
        )
    return build_input_result(
        campaign_brief_id,
        ready=True,
        exclusions=exclusions,
        source_demo=is_demo,
        candidate_claims_path=path,
        candidate_claims=claims,
    )


def resolve_and_persist_brand_kit(
    campaign_brief_id: str, brand_kit_path: str | None, demo_mode: bool
) -> CampaignInputResult:
    """Resolve a brand kit independently for the retrieval tool without fallback."""
    source_hint, hint_is_demo = _brand_kit_source_hint(brand_kit_path, None, demo_mode)
    try:
        brief = load_brief(campaign_brief_id)
    except Exception as exc:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[str(exc)],
            source_demo=hint_is_demo,
            candidate_brand_kit_path=source_hint,
        )
    if not brief:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[f"Campaign brief not found: {campaign_brief_id}"],
            source_demo=hint_is_demo,
            candidate_brand_kit_path=source_hint,
        )
    source_hint, hint_is_demo = _brand_kit_source_hint(brand_kit_path, brief, demo_mode)
    try:
        path, is_demo, error = _resolve_brand_kit_source(brand_kit_path, brief, demo_mode)
    except Exception as exc:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[str(exc)],
            source_demo=hint_is_demo,
            candidate_brand_kit_path=source_hint,
        )
    candidate_brand_kit_path = path or source_hint
    source_demo = is_demo or hint_is_demo
    if error:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[error],
            source_demo=source_demo,
            candidate_brand_kit_path=candidate_brand_kit_path,
        )
    assert path is not None
    manifest: dict | None = None
    try:
        manifest, parse_error = _brand_manifest(path)
        if parse_error:
            return build_input_result(
                campaign_brief_id,
                ready=False,
                errors=[parse_error],
                source_demo=is_demo,
                candidate_brand_kit_path=path,
            )
        assert manifest is not None
        _activate_input_set(campaign_brief_id, brief, claims=None, brand=(path, manifest, is_demo))
    except Exception as exc:
        return build_input_result(
            campaign_brief_id,
            ready=False,
            errors=[str(exc)],
            source_demo=is_demo,
            candidate_brand_kit_path=path,
            candidate_brand_manifest=manifest,
        )
    return build_input_result(
        campaign_brief_id,
        ready=True,
        source_demo=is_demo,
        candidate_brand_kit_path=path,
        candidate_brand_manifest=manifest,
    )


def _claims_source_hint(source: str | None, brief: dict | None, demo_mode: bool) -> tuple[str | None, bool]:
    """Derive claims diagnostics without checking or opening the candidate path."""
    return _source_hint(
        source,
        brief,
        configured_key="approved_claims_path",
        demo_mode=demo_mode,
        fixture_parts=("sample_approved_claims.json",),
    )


def _brand_kit_source_hint(source: str | None, brief: dict | None, demo_mode: bool) -> tuple[str | None, bool]:
    """Derive brand diagnostics without checking or opening the candidate path."""
    return _source_hint(
        source,
        brief,
        configured_key="brand_kit_path",
        demo_mode=demo_mode,
        fixture_parts=("brand_kit",),
    )


def _source_hint(
    source: str | None,
    brief: dict | None,
    *,
    configured_key: str,
    demo_mode: bool,
    fixture_parts: tuple[str, ...],
) -> tuple[str | None, bool]:
    """Return only provenance that is knowable before a resolver touches the filesystem."""
    if source is not None:
        candidate = str(source)
    elif isinstance(brief, dict):
        configured = brief.get(configured_key)
        if configured:
            candidate = str(configured)
        else:
            candidate = None
    else:
        candidate = None
    if candidate is None and demo_mode:
        try:
            fixture = files("open_pharma_plugins_campaign_studio") / "fixtures"
            for part in fixture_parts:
                fixture = fixture / part
            candidate = str(fixture)
        except Exception:
            return None, False
    if candidate is None:
        return None, False
    return candidate, demo_mode and _is_lexically_bundled_fixture(candidate)


def _is_lexically_bundled_fixture(path: str) -> bool:
    """Classify a hint under the known fixture root without inspecting either path."""
    try:
        candidate = Path(os.path.abspath(path))
        fixture_root = Path(os.path.abspath(str(files("open_pharma_plugins_campaign_studio") / "fixtures")))
        candidate.relative_to(fixture_root)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _resolve_claims_source(source: str | None, brief: dict, demo_mode: bool) -> tuple[Path | None, bool, str | None]:
    if source is not None:
        path = Path(source)
        path_error = _claims_path_error(path)
        if path_error:
            return None, False, path_error
        if _is_bundled_fixture(path) and not demo_mode:
            return None, False, f"Bundled claims fixture requires demo_mode=true: {path}"
        return path, _is_bundled_fixture(path), None
    configured = brief.get("approved_claims_path")
    if configured:
        path = Path(configured)
        path_error = _claims_path_error(path)
        if path_error:
            return None, False, path_error
        if _is_bundled_fixture(path) and not demo_mode:
            return None, False, f"Bundled claims fixture requires demo_mode=true: {path}"
        return path, _is_bundled_fixture(path), None
    if demo_mode:
        return (
            Path(str(files("open_pharma_plugins_campaign_studio") / "fixtures" / "sample_approved_claims.json")),
            True,
            None,
        )
    return None, False, "No claims path is configured. Supply claims_path or enable demo_mode=true."


def _claims_path_error(path: Path) -> str | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return f"Claims path does not exist or is not a file: {path}"
    except OSError:
        return f"Claims path does not exist or could not be inspected safely: {path}"
    if stat.S_ISLNK(metadata.st_mode):
        return f"Claims path may not be a symlink: {path}"
    if not stat.S_ISREG(metadata.st_mode):
        return f"Claims path does not exist or is not a regular file: {path}"
    return None


def _resolve_brand_kit_source(source: str | None, brief: dict, demo_mode: bool) -> tuple[Path | None, bool, str | None]:
    if source is not None:
        path = Path(source)
        if not path.is_dir():
            return None, False, f"Brand kit path does not exist or is not a directory: {path}"
        if _is_bundled_fixture(path) and not demo_mode:
            return None, False, f"Bundled brand kit fixture requires demo_mode=true: {path}"
        return path, _is_bundled_fixture(path), None
    configured = brief.get("brand_kit_path")
    if configured:
        path = Path(configured)
        if not path.is_dir():
            return None, False, f"Brand kit path does not exist or is not a directory: {path}"
        if _is_bundled_fixture(path) and not demo_mode:
            return None, False, f"Bundled brand kit fixture requires demo_mode=true: {path}"
        return path, _is_bundled_fixture(path), None
    if demo_mode:
        return Path(str(files("open_pharma_plugins_campaign_studio") / "fixtures" / "brand_kit")), True, None
    return None, False, "No brand kit path is configured. Supply brand_kit_path or enable demo_mode=true."


def _validated_claims(path: Path, brief: dict) -> tuple[list[dict], list[dict], str | None]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [], [], f"Claims file could not be parsed: {path} ({exc})"
    if not isinstance(raw, list):
        return [], [], f"Claims file must contain a JSON array: {path}"
    if not raw:
        return [], [], f"Claims file must contain a non-empty JSON array: {path}"

    raw_id_counts = Counter(
        _normalise(item["claim_id"])
        for item in raw
        if isinstance(item, dict) and isinstance(item.get("claim_id"), str) and item["claim_id"].strip()
    )
    duplicate_raw_ids = {claim_id for claim_id, count in raw_id_counts.items() if count > 1}
    records: list[tuple[int, ApprovedClaim]] = []
    excluded_by_index: dict[int, dict] = {}
    for index, item in enumerate(raw):
        claim_id = (
            item.get("claim_id") if isinstance(item, dict) and isinstance(item.get("claim_id"), str) else "unknown"
        )
        normalised_id = _normalise(claim_id) if isinstance(claim_id, str) else ""
        if normalised_id in duplicate_raw_ids:
            excluded_by_index[index] = {"claim_id": normalised_id, "reason": "duplicate_claim_id"}
            continue
        try:
            claim = ApprovedClaim.model_validate(item)
        except ValidationError:
            excluded_by_index[index] = {"claim_id": claim_id, "reason": "invalid_schema"}
            continue
        records.append((index, claim))

    approved: list[dict] = []
    for index, claim in records:
        if claim.approval_status.lower() != "approved":
            excluded_by_index[index] = {"claim_id": claim.claim_id, "reason": "not_approved"}
            continue
        if claim.effective_from is not None and claim.effective_from > date.today():
            excluded_by_index[index] = {"claim_id": claim.claim_id, "reason": "not_yet_effective"}
            continue
        if claim.expiry is not None and claim.expiry < date.today():
            excluded_by_index[index] = {"claim_id": claim.claim_id, "reason": "expired"}
            continue
        if claim.restrictions and claim.restrictions.strip():
            excluded_by_index[index] = {"claim_id": claim.claim_id, "reason": "restricted"}
            continue
        applicability_reason = _applicability_exclusion(claim, brief)
        if applicability_reason:
            excluded_by_index[index] = {"claim_id": claim.claim_id, "reason": applicability_reason}
            continue
        approved.append(claim.model_dump(mode="json"))
    return approved, [excluded_by_index[index] for index in sorted(excluded_by_index)], None


def _applicability_exclusion(claim: ApprovedClaim, brief: dict) -> str | None:
    country = str(brief.get("country", "")).strip().upper()
    jurisdiction = str(brief.get("policy_jurisdiction", "")).strip().upper()
    if claim.jurisdictions and not _allowlist_matches(claim.jurisdictions, {country, jurisdiction}):
        return "jurisdiction_inapplicable"
    if claim.indications and not _allowlist_matches(claim.indications, {str(brief.get("indication", ""))}):
        return "indication_inapplicable"
    if claim.audiences and not _audience_matches(claim.audiences, str(brief.get("target_segment", ""))):
        return "audience_inapplicable"
    channels = {_normalise(str(channel)) for channel in brief.get("channels", [])}
    if claim.channels and not channels.intersection({_normalise(item) for item in claim.channels}):
        return "channel_inapplicable"
    return None


def _allowlist_matches(allowlist: list[str], values: set[str]) -> bool:
    return bool({_normalise(value) for value in allowlist} & {_normalise(value) for value in values})


_AUDIENCE_TAXONOMY = {
    "hcp": {
        "hcp",
        "healthcare professional",
        "health care professional",
        "physician",
        "physicians",
        "doctor",
        "doctors",
        "oncologist",
        "oncologists",
        "pcp",
        "pcps",
        "primary care physician",
        "primary care physicians",
        "nurse",
        "nurses",
        "pharmacist",
        "pharmacists",
    },
    "patient": {"patient", "patients"},
    "consumer": {"consumer", "consumers"},
    "caregiver": {"caregiver", "caregivers", "carer", "carers"},
}


def _audience_matches(allowlist: list[str], target_segment: str) -> bool:
    """Match only documented audience categories; unknown restricted targets fail closed."""
    target = _normalise(target_segment)
    if target in {_normalise(value) for value in allowlist}:
        return True
    target_categories = {name for name, values in _AUDIENCE_TAXONOMY.items() if target in values}
    if not target_categories:
        return False
    allowed_categories = {
        category
        for value in allowlist
        for category, values in _AUDIENCE_TAXONOMY.items()
        if _normalise(value) in values
    }
    return bool(target_categories & allowed_categories)


def _normalise(value: str) -> str:
    return value.strip().casefold()


def _brand_manifest(path: Path) -> tuple[dict | None, str | None]:
    json_files = ("palette.json", "typography.json", "legal.json")
    files_metadata: dict[str, dict[str, Any]] = {}
    values: dict[str, dict] = {}
    if path.is_symlink():
        return None, f"Brand kit path may not be a symlink: {path}"
    try:
        kit_root = path.resolve(strict=True)
    except OSError as exc:
        return None, f"Brand kit path could not be resolved safely: {path} ({exc})"
    if not kit_root.is_dir():
        return None, f"Brand kit path is not a directory: {path}"
    for name in json_files:
        file_path, path_error = _kit_file(path, kit_root, name)
        if path_error:
            return None, path_error
        assert file_path is not None
        if not file_path.is_file():
            return None, f"Brand kit is missing required file: {file_path}"
        try:
            value = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, f"Brand kit file could not be parsed: {file_path} ({exc})"
        if not isinstance(value, dict):
            return None, f"Brand kit JSON must contain an object: {file_path}"
        values[name.removesuffix(".json")] = value
        files_metadata[name] = _file_metadata(file_path)
    schema_error = _validate_brand_values(values)
    if schema_error:
        return None, schema_error

    logo_path, logo_error = _kit_file(path, kit_root, "logo.svg")
    if logo_error:
        return None, logo_error
    assert logo_path is not None
    if not logo_path.is_file():
        return None, f"Brand kit is missing required file: {logo_path}"
    svg_error = _validate_svg(logo_path)
    if svg_error:
        return None, svg_error
    files_metadata["logo.svg"] = _file_metadata(logo_path)

    for name in ("product.png",):
        file_path, file_error = _kit_file(path, kit_root, name)
        if file_error:
            return None, file_error
        assert file_path is not None
        if file_path.is_file():
            files_metadata[name] = _file_metadata(file_path)
    return {
        "brand_kit_path": str(path),
        "resolved_brand_kit_path": str(kit_root),
        "logo_path": str(logo_path),
        "resolved_logo_path": files_metadata["logo.svg"]["resolved_path"],
        "product_image_path": files_metadata.get("product.png", {}).get("path"),
        "resolved_product_image_path": files_metadata.get("product.png", {}).get("resolved_path"),
        "palette": values["palette"],
        "typography": values["typography"],
        "legal": values["legal"],
        "files": files_metadata,
    }, None


def _kit_file(kit_path: Path, kit_root: Path, name: str) -> tuple[Path | None, str | None]:
    file_path = kit_path / name
    if file_path.exists() and (file_path.is_symlink() or not _within_root(file_path.resolve(), kit_root)):
        return None, f"Brand kit file escapes the kit directory: {file_path}"
    return file_path, None


def _within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_brand_values(values: dict[str, dict]) -> str | None:
    palette_keys = {
        "primary",
        "secondary",
        "accent",
        "text",
        "text_light",
        "background",
        "background_alt",
        "safety_highlight",
        "success",
    }
    palette = values["palette"]
    if not palette_keys.issubset(palette) or any(
        not isinstance(palette[key], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", palette[key]) for key in palette_keys
    ):
        return "Brand kit has invalid palette values."

    typography = values["typography"]
    typography_keys = {"heading_family", "body_family", "heading_weight", "body_weight", "sizes"}
    size_keys = {"h1", "h2", "h3", "body", "small", "legal"}
    sizes = typography.get("sizes")
    family_pattern = r"[A-Za-z][A-Za-z0-9 .,'-]*(?:\s*,\s*[A-Za-z][A-Za-z0-9 .,'-]*)*"
    valid_weights = {"normal", "bold", "100", "200", "300", "400", "500", "600", "700", "800", "900"}
    size_pattern = r"(?:0|[1-9]\d*(?:\.\d+)?)(?:px|pt|em|rem|%)"
    if (
        not typography_keys.issubset(typography)
        or any(
            not isinstance(typography[key], str) or not typography[key].strip()
            for key in {"heading_family", "body_family"}
        )
        or not all(re.fullmatch(family_pattern, typography[key]) for key in {"heading_family", "body_family"})
        or any(typography.get(key) not in valid_weights for key in {"heading_weight", "body_weight"})
        or not isinstance(sizes, dict)
        or not size_keys.issubset(sizes)
        or any(not isinstance(sizes[key], str) or not re.fullmatch(size_pattern, sizes[key]) for key in size_keys)
    ):
        return "Brand kit has invalid typography values."

    legal = values["legal"]
    legal_keys = {"isi", "pi_ref", "copyright", "reporting_statement", "disclaimer", "jurisdictions"}
    jurisdictions = legal.get("jurisdictions")
    if (
        not legal_keys.issubset(legal)
        or any(not isinstance(legal[key], str) or not legal[key].strip() for key in legal_keys - {"jurisdictions"})
        or not isinstance(jurisdictions, dict)
        or not jurisdictions
    ):
        return "Brand kit has invalid legal values."
    for config in jurisdictions.values():
        if (
            not isinstance(config, dict)
            or not isinstance(config.get("required_elements"), list)
            or not config["required_elements"]
            or not all(isinstance(item, str) and item.strip() for item in config["required_elements"])
        ):
            return "Brand kit has invalid legal jurisdiction values."
        if not isinstance(config.get("fair_balance_required"), bool):
            return "Brand kit has invalid legal jurisdiction values."
    return None


def _validate_svg(path: Path) -> str | None:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"Brand kit has unsafe SVG: {path} ({exc})"
    if "<!DOCTYPE" in content.upper() or "<!ENTITY" in content.upper():
        return f"Brand kit has unsafe SVG: {path}"
    # An XML declaration is allowed; every processing instruction is denied.
    if re.search(r"<\?(?!xml\s)", content, flags=re.IGNORECASE):
        return f"Brand kit has unsafe SVG: {path}"
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return f"Brand kit has unsafe SVG: {path}"
    if root.tag.rsplit("}", 1)[-1].casefold() != "svg":
        return f"Brand kit has unsafe SVG: {path}"
    unsafe_tags = {
        "script",
        "style",
        "foreignobject",
        "animate",
        "animatemotion",
        "animatecolor",
        "animatetransform",
        "set",
    }
    if re.search(r"(?i)(?:@import|url\s*\(\s*['\"]?\s*(?:https?:|//|data:|file:|javascript:))", content):
        return f"Brand kit has unsafe SVG: {path}"
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].casefold() in unsafe_tags:
            return f"Brand kit has unsafe SVG: {path}"
        for raw_name, raw_value in element.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].casefold()
            value = raw_value.strip().casefold()
            if not _safe_svg_value(value):
                return f"Brand kit has unsafe SVG: {path}"
            if (
                name.startswith("on")
                or name == "style"
                or value.startswith(("javascript:", "data:", "file:", "http:", "https:", "//"))
            ):
                return f"Brand kit has unsafe SVG: {path}"
            if name == "href" and not value.startswith("#"):
                return f"Brand kit has unsafe SVG: {path}"
            if (
                name == "style"
                and "url(" in value
                and any(unsafe in value for unsafe in ("http:", "https:", "data:", "file:", "//"))
            ):
                return f"Brand kit has unsafe SVG: {path}"
    return None


def _safe_svg_value(value: str) -> bool:
    """Accept only inert fragment paint references in SVG attribute values.

    CSS has multiple escape forms, so Task 1 deliberately does not implement a
    general CSS decoder. Any backslash is rejected, and every recognised
    ``url(...)`` value must be a local ``#id`` fragment.
    """
    if "\\" in value:
        return False
    url_values = re.findall(r"(?i)url\s*\(\s*([^)]*?)\s*\)", value)
    return all(re.fullmatch(r"#[A-Za-z_][A-Za-z0-9_.:-]*", url_value.strip(" '\"")) for url_value in url_values)


def _file_metadata(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    return {
        "path": str(path),
        "resolved_path": str(resolved),
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def _is_bundled_fixture(path: Path) -> bool:
    fixtures = Path(str(files("open_pharma_plugins_campaign_studio") / "fixtures"))
    try:
        path.resolve().relative_to(fixtures.resolve())
    except ValueError:
        return False
    return True


_INPUT_ARTIFACTS = (
    "campaign-brief.json",
    "approved-claims.json",
    "brand-components.json",
    "input-provenance.json",
)


def _activate_input_set(
    campaign_brief_id: str,
    brief: dict,
    *,
    claims: tuple[Path, list[dict], bool] | None,
    brand: tuple[Path, dict, bool] | None,
) -> tuple[dict, dict]:
    """Activate a coherent input set, restoring all target bytes on any failure.

    The existing filesystem store has no transaction primitive.  Snapshotting every
    Task 1 target makes the activation all-or-nothing for both new and existing
    campaigns; downstream consumers consequently see only the previous complete
    set or the new complete set.
    """
    from shared.filesystem import atomic_write_bytes

    from ._campaign_store import campaign_dir

    directory = campaign_dir(campaign_brief_id)
    targets = {name: directory / name for name in _INPUT_ARTIFACTS}
    snapshots = {name: target.read_bytes() if target.exists() else None for name, target in targets.items()}
    provenance = load_artifact(campaign_brief_id, "input-provenance.json") or {}
    provenance = json.loads(json.dumps(provenance))
    updated_brief = dict(brief)

    if claims is not None:
        claims_path, approved_claims, is_demo = claims
        provenance["claims"] = {"path": str(claims_path), "is_demo_fixture": is_demo, **_file_metadata(claims_path)}
        updated_brief["approved_claims_path"] = str(claims_path)
    if brand is not None:
        brand_path, manifest, is_demo = brand
        provenance["brand_kit"] = {
            "path": str(brand_path),
            "resolved_path": manifest.get("resolved_brand_kit_path"),
            "is_demo_fixture": is_demo,
            "files": manifest["files"],
        }
        updated_brief["brand_kit_path"] = str(brand_path)
    updated_brief["demo_mode"] = bool(provenance.get("claims", {}).get("is_demo_fixture")) or bool(
        provenance.get("brand_kit", {}).get("is_demo_fixture")
    )

    try:
        if claims is not None:
            save_artifact(campaign_brief_id, "approved-claims.json", claims[1])
        if brand is not None:
            save_artifact(campaign_brief_id, "brand-components.json", brand[1])
        save_artifact(campaign_brief_id, "input-provenance.json", provenance)
        save_brief(updated_brief)
    except Exception:
        for name, target in targets.items():
            previous = snapshots[name]
            if previous is None:
                if target.exists():
                    target.unlink()
            else:
                atomic_write_bytes(target, previous)
        raise
    return provenance, updated_brief


def _campaign_demo_status(campaign_brief_id: str) -> bool:
    return bool(_active_input_diagnostics(campaign_brief_id).get("demo_mode"))


def _provenance_path(campaign_brief_id: str) -> Path | None:
    from ._campaign_store import campaign_dir

    if not _safe_campaign_id(campaign_brief_id):
        return None
    return campaign_dir(campaign_brief_id) / "input-provenance.json"


def _safe_campaign_id(campaign_brief_id: object) -> bool:
    try:
        validate_component(campaign_brief_id, label="campaign_brief_id")
    except ValueError:
        return False
    return True


def _active_input_diagnostics(campaign_brief_id: object) -> dict:
    """Read the post-operation persisted state without opening an untrusted path."""
    if not _safe_campaign_id(campaign_brief_id):
        return {}
    try:
        provenance = load_artifact(campaign_brief_id, "input-provenance.json") or {}
        stored_claims = load_artifact(campaign_brief_id, "approved-claims.json")
        stored_manifest = current_brand_manifest(campaign_brief_id)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(provenance, dict):
        return {}
    claim_source = provenance.get("claims", {})
    brand_source = provenance.get("brand_kit", {})
    if not isinstance(claim_source, dict) or not isinstance(brand_source, dict):
        return {}
    has_claims = (
        bool(claim_source)
        and isinstance(stored_claims, list)
        and all(isinstance(claim, dict) for claim in stored_claims)
    )
    has_brand = bool(brand_source) and isinstance(stored_manifest, dict) and bool(stored_manifest)
    if not has_claims and not has_brand:
        return {}
    claims = stored_claims if has_claims else []
    manifest = stored_manifest if has_brand else {}
    files = manifest.get("files", {})
    if not isinstance(files, dict):
        files = {}
    return {
        "claims_path": claim_source.get("path") if has_claims else None,
        "brand_kit_path": brand_source.get("path") if has_brand else None,
        "claims": claims,
        "claims_count": len(claims),
        "applicable_claim_count": len(claims),
        "brand_files_count": len(files),
        "hashes": {
            "claims": claim_source.get("sha256") if has_claims else None,
            "brand_files": {name: item.get("sha256") for name, item in files.items() if isinstance(item, dict)},
        },
        "brand_manifest": manifest,
        "demo_mode": bool(has_claims and claim_source.get("is_demo_fixture"))
        or bool(has_brand and brand_source.get("is_demo_fixture")),
    }


def _candidate_input_diagnostics(
    claims_path: Path | str | None,
    brand_kit_path: Path | str | None,
    claims: list[dict] | None,
    brand_manifest: dict | None,
    source_demo: bool,
) -> dict:
    if claims_path is None and brand_kit_path is None and claims is None and brand_manifest is None:
        return {}
    return {
        "claims_path": str(claims_path) if claims_path is not None else None,
        "brand_kit_path": str(brand_kit_path) if brand_kit_path is not None else None,
        "claims_count": len(claims or []),
        "applicable_claim_count": len(claims or []),
        "brand_files_count": len((brand_manifest or {}).get("files", {})),
        "demo_mode": source_demo,
        "hashes": {
            "claims": _metadata_hash(claims_path),
            "brand_files": {
                name: item.get("sha256")
                for name, item in (brand_manifest or {}).get("files", {}).items()
                if isinstance(item, dict)
            },
        },
    }


def _metadata_hash(path: Path | str | None) -> str | None:
    if not isinstance(path, Path):
        return None
    try:
        return sha256_file(path)
    except OSError:
        return None


def input_failure_result(campaign_brief_id: object, errors: list[str]) -> CampaignInputResult:
    """Public stable failure builder for thin MCP handler exception boundaries."""
    safe_id = campaign_brief_id if isinstance(campaign_brief_id, str) else ""
    return build_input_result(safe_id, ready=False, errors=errors)


def build_input_result(
    campaign_brief_id: object,
    *,
    ready: bool,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
    exclusions: list[dict] | None = None,
    source_demo: bool = False,
    candidate_claims_path: Path | str | None = None,
    candidate_brand_kit_path: Path | str | None = None,
    candidate_claims: list[dict] | None = None,
    candidate_brand_manifest: dict | None = None,
) -> CampaignInputResult:
    """Build the only top-level result shape used by all input operations."""
    safe_id = campaign_brief_id if isinstance(campaign_brief_id, str) else ""
    active = _active_input_diagnostics(safe_id)
    candidate = _candidate_input_diagnostics(
        candidate_claims_path,
        candidate_brand_kit_path,
        candidate_claims,
        candidate_brand_manifest,
        source_demo,
    )
    provenance_path = _provenance_path(safe_id)
    claims = active.get("claims", [])
    brand_manifest = active.get("brand_manifest", {})
    result: CampaignInputResult = {
        "campaign_brief_id": safe_id,
        "ready": ready,
        "demo_mode": bool(active.get("demo_mode")) or source_demo,
        "claims": claims,
        "claims_count": active.get("claims_count", 0),
        "total_claims": active.get("claims_count", 0),
        "applicable_claim_count": active.get("applicable_claim_count", 0),
        "excluded_claims_count": len(exclusions or []),
        "excluded_claim_count": len(exclusions or []),
        "brand_files_count": active.get("brand_files_count", 0),
        "categories": sorted(
            {category for claim in claims if (category := canonical_claim_category(claim.get("category"))) is not None}
        ),
        "exclusions": exclusions or [],
        "claims_path": active.get("claims_path"),
        "brand_kit_path": active.get("brand_kit_path"),
        "brand_manifest": brand_manifest,
        "provenance_path": str(provenance_path) if provenance_path is not None else None,
        "hashes": active.get("hashes", {}),
        "active_inputs": active,
        "candidate_inputs": candidate,
        "errors": errors or [],
        "warnings": warnings or [],
        "logo_path": brand_manifest.get("logo_path"),
        "product_image_path": brand_manifest.get("product_image_path"),
        "palette": brand_manifest.get("palette", {}),
        "typography": brand_manifest.get("typography", {}),
        "legal": brand_manifest.get("legal", {}),
        "files": brand_manifest.get("files", {}),
    }
    return result
