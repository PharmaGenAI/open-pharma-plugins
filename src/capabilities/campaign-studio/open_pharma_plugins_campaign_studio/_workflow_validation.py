"""Read-only semantic validators shared by Campaign Studio workflow readers.

These helpers intentionally accept already-read JSON values.  They neither call
MCP handlers nor create campaign storage, so status and sealing code can reject
corrupt persisted state without changing it.
"""

from __future__ import annotations

import hashlib
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ._campaign_store import read_existing_json
from ._claim_engine import (
    banner_safety_errors,
    check_fair_balance,
    check_prohibited_language,
    check_required_elements,
    claim_applicability_errors,
    is_claim_citation_exempt,
    load_policy_rules,
    policy_metadata,
    validate_claim_wording,
)
from ._claims import validate_persisted_claims
from ._inputs import _is_bundled_fixture, _validate_brand_values, _validate_svg
from .models._common import SourceReference
from .models.brief import CampaignBrief, _valid_https_url
from .models.claims import SAFETY_CLAIM_CATEGORIES, canonical_claim_category
from .models.copy import BannerCopy, EmailCopy, PersistedChannelCopy, PosterCopy
from .models.journey import AudienceJourney, JourneyStage
from .models.message import MessageArchitecture, MessageTier

VALID_MODES = frozenset({"promotional", "non_promotional", "disease_awareness"})
VALID_JURISDICTIONS = frozenset({"FDA", "EMA", "MHRA", "HSA", "PMDA", "TGA"})
VALID_LIFECYCLES = frozenset({"pre_launch", "launch", "growth", "mature", "LOE"})
VALID_CHANNELS = frozenset({"email", "banner", "poster"})
VALID_WORKFLOWS = frozenset({"mlr_standard", "mlr_expedited", "medical_only"})
VALID_STAGES = ("unaware", "aware", "interested", "convinced", "acting", "advocating")
VALID_CONTENT_TYPES = frozenset({"educational", "promotional", "reminder"})
VALID_TIERS = frozenset({"primary", "secondary", "supporting"})

_REQUIRED_BRAND_FILES = ("palette.json", "typography.json", "legal.json", "logo.svg")
_OPTIONAL_BRAND_FILES = ("product.png",)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_POLICY_REPORT_KEYS = frozenset(
    {
        "campaign_brief_id",
        "channels_validated",
        "claims_checked",
        "policy_checks",
        "channel_results",
        "policy_version",
        "policy_hash",
        "overall_pass",
        "input_fingerprint",
        "generated_at",
    }
)
_POLICY_CHANNEL_KEYS = frozenset({"channel", "copy_exists", "claims_checked", "policy_checks", "overall_pass"})
_POLICY_CLAIM_KEYS = frozenset(
    {
        "claim_id",
        "declared_claim_id",
        "statement",
        "status",
        "matched_claim_text",
        "similarity_score",
        "deviation",
    }
)
_POLICY_CHECK_KEYS = frozenset({"check_name", "result", "detail"})


@dataclass(frozen=True)
class ValidationResult:
    """A stable result for a pure semantic validation pass."""

    value: object | None
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class BrandState:
    """Verified renderer-selected brand state needed by provenance and copy checks."""

    manifest: dict[str, Any]
    root: Path


@dataclass(frozen=True)
class PolicyReportValidation:
    """Canonical report verification plus copy occurrences used by downstream evidence checks."""

    errors: tuple[str, ...]
    claim_rows: tuple[dict[str, str], ...]


def validate_campaign_brief(value: object, campaign_brief_id: str) -> ValidationResult:
    """Validate the full persisted brief plus create-tool domain constraints."""
    if not isinstance(value, dict):
        return ValidationResult(None, ("Campaign brief must be a JSON object.",))

    errors: list[str] = []
    missing = sorted(set(CampaignBrief.model_fields) - set(value))
    if missing:
        errors.append(f"Campaign brief is missing required fields: {', '.join(missing)}.")
    try:
        CampaignBrief.model_validate(value)
    except (ValidationError, TypeError, ValueError, RecursionError, OverflowError, MemoryError) as exc:
        errors.append(f"Campaign brief does not satisfy CampaignBrief: {exc}")

    _require_strings(
        value,
        (
            "campaign_brief_id",
            "campaign_name",
            "country",
            "policy_jurisdiction",
            "mode",
            "brand",
            "indication",
            "lifecycle_stage",
            "target_segment",
            "behavioral_objective",
            "call_to_action",
            "call_to_action_url",
            "language",
            "approval_workflow",
            "generated_at",
        ),
        errors,
        "Campaign brief",
    )
    for field in (
        "approved_claims_path",
        "brand_kit_path",
        "educational_objective",
        "localisation_notes",
        "delivery_constraints",
    ):
        item = value.get(field)
        if item is not None and (not isinstance(item, str) or not item.strip()):
            errors.append(f"Campaign brief {field} must be null or a nonblank string.")
    for field in ("desired_kpi", "required_safety_content", "required_legal_content"):
        items = value.get(field)
        if not isinstance(items, list) or any(not _nonblank_string(item) for item in items):
            errors.append(f"Campaign brief {field} must be a list of nonblank strings.")
    desired_kpi = value.get("desired_kpi")
    if isinstance(desired_kpi, list):
        if not desired_kpi:
            errors.append("Campaign brief desired_kpi must contain at least one value.")
        elif all(_nonblank_string(item) for item in desired_kpi) and len(desired_kpi) != len(set(desired_kpi)):
            errors.append("Campaign brief desired_kpi values must be unique.")
    if not isinstance(value.get("demo_mode"), bool):
        errors.append("Campaign brief demo_mode must be a boolean.")
    if value.get("asset_dimensions") is not None and not isinstance(value.get("asset_dimensions"), dict):
        errors.append("Campaign brief asset_dimensions must be null or an object.")

    if value.get("campaign_brief_id") != campaign_brief_id:
        errors.append("Campaign brief campaign_brief_id does not match the requested campaign.")
    country = value.get("country")
    if (
        not isinstance(country, str)
        or country != country.upper()
        or len(country) != 2
        or not country.isascii()
        or not country.isalpha()
    ):
        errors.append("Campaign brief country must be an uppercase two-letter ISO 3166-1 alpha-2 code.")
    if value.get("mode") not in VALID_MODES:
        errors.append("Campaign brief mode is unsupported.")
    if value.get("policy_jurisdiction") not in VALID_JURISDICTIONS:
        errors.append("Campaign brief policy_jurisdiction is unsupported.")
    if value.get("lifecycle_stage") not in VALID_LIFECYCLES:
        errors.append("Campaign brief lifecycle_stage is unsupported.")
    if value.get("approval_workflow") not in VALID_WORKFLOWS:
        errors.append("Campaign brief approval_workflow is unsupported.")
    channels = value.get("channels")
    if not isinstance(channels, list) or not channels:
        errors.append("Campaign brief channels must contain at least one supported channel.")
    elif any(not isinstance(channel, str) or channel not in VALID_CHANNELS for channel in channels):
        errors.append("Campaign brief channels contain an unsupported value.")
    elif len(channels) != len(set(channels)):
        errors.append("Campaign brief channels must be unique.")
    if value.get("language") != "en":
        errors.append("Campaign brief language must be 'en'.")
    if not _valid_https_url(value.get("call_to_action_url", "")):
        errors.append("Campaign brief call_to_action_url must be a valid HTTPS URL.")
    generated_at = value.get("generated_at")
    if isinstance(generated_at, str) and generated_at.strip():
        try:
            datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            errors.append("Campaign brief generated_at must be an ISO 8601 timestamp.")
    return ValidationResult(value if not errors else None, tuple(_unique(errors)))


def validate_claims(value: object, brief: dict[str, Any]) -> ValidationResult:
    """Require strict, currently usable claims for the selected campaign channels."""
    claims, errors = validate_persisted_claims(value)
    if errors:
        return ValidationResult(None, tuple(_unique(errors)))
    for claim in claims:
        claim_id = claim["claim_id"]
        base_errors = claim_applicability_errors(claim, brief, None)
        if base_errors:
            errors.append(f"Claim '{claim_id}' is not current and unrestricted: {', '.join(base_errors)}.")
        channels = brief.get("channels")
        if not isinstance(channels, list) or not channels:
            errors.append(f"Claim '{claim_id}' cannot be checked because the brief has no channels.")
            continue
        applicable = any(not claim_applicability_errors(claim, brief, channel) for channel in channels)
        if not applicable:
            errors.append(f"Claim '{claim_id}' is inapplicable to every requested campaign channel.")
    return ValidationResult(claims if not errors else None, tuple(_unique(errors)))


def validate_brand_components(value: object) -> ValidationResult:
    """Validate every renderer-consumed brand value and selected live asset."""
    if not isinstance(value, dict):
        return ValidationResult(None, ("Brand components artifact must be a JSON object.",))
    errors: list[str] = []
    root_raw = value.get("brand_kit_path")
    root_resolved_raw = value.get("resolved_brand_kit_path")
    if not _nonblank_string(root_raw):
        errors.append("Brand components brand_kit_path must be a nonblank string.")
    if not _nonblank_string(root_resolved_raw):
        errors.append("Brand components resolved_brand_kit_path must be a nonblank string.")
    root = _canonical_directory(root_resolved_raw, "Brand components resolved_brand_kit_path", errors)
    if isinstance(root_raw, str) and root is not None:
        _validate_lexical_target(root_raw, root, "Brand components brand_kit_path", errors)

    brand_values = {name: value.get(name) for name in ("palette", "typography", "legal")}
    if any(not isinstance(item, dict) for item in brand_values.values()):
        errors.append("Brand components palette, typography, and legal values must be objects.")
    else:
        try:
            schema_error = _validate_brand_values(brand_values)
        except (KeyError, TypeError, ValueError, RecursionError, OverflowError, MemoryError) as exc:
            schema_error = f"Brand components values could not be validated safely: {exc}"
        if schema_error:
            errors.append(schema_error)

    files = value.get("files")
    if not isinstance(files, dict):
        errors.append("Brand components files must be an object containing every selected renderer file.")
        files = {}
    allowed = set(_REQUIRED_BRAND_FILES + _OPTIONAL_BRAND_FILES)
    unexpected = sorted(set(files) - allowed) if all(isinstance(name, str) for name in files) else ["non-string"]
    if unexpected:
        errors.append(f"Brand components files contain unsupported selections: {', '.join(unexpected)}.")
    for name in _REQUIRED_BRAND_FILES:
        if name not in files:
            errors.append(f"Brand components files is missing required selected file: {name}.")
    selected_names = [name for name in _REQUIRED_BRAND_FILES + _OPTIONAL_BRAND_FILES if name in files]
    if root is not None and isinstance(root_raw, str):
        for name in selected_names:
            metadata = files.get(name)
            _validate_brand_file(value, root_raw, root, name, metadata, errors)
        _validate_brand_top_level_paths(value, root_raw, root, files, errors)
    return ValidationResult(
        BrandState(value, root) if root is not None and not errors else None,
        tuple(_unique(errors)),
    )


def validate_input_provenance(
    value: object,
    brief: dict[str, Any],
    claims: list[dict[str, Any]],
    brand: BrandState | None,
) -> ValidationResult:
    """Validate the activated source set against brief, claims, and brand state."""
    if not isinstance(value, dict):
        return ValidationResult(None, ("Input provenance artifact must be a JSON object.",))
    errors: list[str] = []
    claims_source = value.get("claims")
    brand_source = value.get("brand_kit")
    if not isinstance(claims_source, dict):
        errors.append("Input provenance claims entry must be an object.")
    if not isinstance(brand_source, dict):
        errors.append("Input provenance brand_kit entry must be an object.")

    claims_demo = _validate_claims_source(claims_source, brief, claims, errors)
    brand_demo = _validate_brand_source(brand_source, brief, brand, errors)
    if isinstance(brief.get("demo_mode"), bool) and brief["demo_mode"] != (claims_demo or brand_demo):
        errors.append("Campaign brief demo_mode does not match activated source provenance.")
    return ValidationResult(value if not errors else None, tuple(_unique(errors)))


def validate_audience_journey(
    value: object, campaign_brief_id: str, brief: dict[str, Any], claims_by_id: dict[str, dict[str, Any]]
) -> ValidationResult:
    """Validate the persisted journey envelope and every writer-enforced stage rule."""
    if not isinstance(value, dict):
        return ValidationResult(None, ("Audience journey artifact must be a JSON object.",))
    errors: list[str] = []
    try:
        AudienceJourney.model_validate(value)
    except (ValidationError, TypeError, ValueError, RecursionError, OverflowError, MemoryError) as exc:
        errors.append(f"Audience journey does not satisfy its persisted model: {exc}")
    if value.get("campaign_brief_id") != campaign_brief_id:
        errors.append("Audience journey campaign_brief_id does not match the validated campaign.")
    if value.get("target_segment") != brief.get("target_segment"):
        errors.append("Audience journey target_segment does not match the campaign brief.")
    if not _timestamp(value.get("generated_at")):
        errors.append("Audience journey generated_at must be an ISO 8601 timestamp.")
    stages = value.get("stages")
    errors.extend(validate_journey_stages(stages, brief, claims_by_id))
    return ValidationResult(value if not errors else None, tuple(_unique(errors)))


def validate_journey_stages(
    stages: object, brief: dict[str, Any], claims_by_id: dict[str, dict[str, Any]]
) -> list[str]:
    """Validate journey stage data without reads or writes; shared by the writer and status."""
    errors: list[str] = []
    if not isinstance(stages, list) or not 3 <= len(stages) <= 6:
        return ["Journey must contain between 3 and 6 ordered stages."]
    seen_stages: set[str] = set()
    previous_stage_index = -1
    brief_channels = brief.get("channels") if isinstance(brief.get("channels"), list) else []
    for index, raw_stage in enumerate(stages):
        prefix = f"Stage {index + 1}"
        if not isinstance(raw_stage, dict):
            errors.append(f"{prefix}: must be an object.")
            continue
        try:
            JourneyStage.model_validate(raw_stage)
        except (ValidationError, TypeError, ValueError, RecursionError, OverflowError, MemoryError) as exc:
            errors.append(f"{prefix}: does not satisfy JourneyStage: {exc}")
        stage_name = raw_stage.get("stage")
        if not isinstance(stage_name, str) or stage_name not in VALID_STAGES:
            errors.append(f"{prefix}: stage must be a supported journey stage.")
        elif stage_name in seen_stages:
            errors.append(f"{prefix}: stage '{stage_name}' must be unique.")
        else:
            seen_stages.add(stage_name)
            stage_index = VALID_STAGES.index(stage_name)
            if stage_index <= previous_stage_index:
                errors.append(f"{prefix}: stage '{stage_name}' is not in journey order.")
            previous_stage_index = stage_index
        for field in ("objective", "kpi"):
            if not _nonblank_string(raw_stage.get(field)):
                errors.append(f"{prefix}: {field} must be non-empty.")
        content_type = raw_stage.get("content_type")
        if not isinstance(content_type, str) or content_type not in VALID_CONTENT_TYPES:
            errors.append(f"{prefix}: content_type must be supported.")
        channels = raw_stage.get("channels")
        if not _unique_nonblank_list(channels):
            errors.append(f"{prefix}: channels must be a non-empty list of unique nonblank strings.")
            channels = []
        invalid_channels = [
            channel for channel in channels if channel not in VALID_CHANNELS or channel not in brief_channels
        ]
        if invalid_channels:
            errors.append(f"{prefix}: channels {sorted(invalid_channels)} are not supported by the brief.")
        claim_ids = raw_stage.get("key_messages")
        if not _nonempty_nonblank_list(claim_ids):
            errors.append(f"{prefix}: key_messages must contain approved claim IDs.")
            claim_ids = []
        for claim_id in claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                errors.append(f"{prefix}: claim_id '{claim_id}' is not in the approved claims set.")
                continue
            for channel in channels:
                reasons = claim_applicability_errors(claim, brief, channel)
                if reasons:
                    errors.append(
                        f"{prefix}: claim_id '{claim_id}' is inapplicable to channel '{channel}': {', '.join(reasons)}."
                    )
    return _unique(errors)


def validate_message_architecture(
    value: object,
    campaign_brief_id: str,
    brief: dict[str, Any],
    claims_by_id: dict[str, dict[str, Any]],
    journey_stage_names: set[str] | None,
) -> ValidationResult:
    """Validate persisted message hierarchy, policy tier limits, and fair balance evidence."""
    if not isinstance(value, dict):
        return ValidationResult(None, ("Message architecture artifact must be a JSON object.",))
    errors: list[str] = []
    try:
        MessageArchitecture.model_validate(value)
    except (ValidationError, TypeError, ValueError, RecursionError, OverflowError, MemoryError) as exc:
        errors.append(f"Message architecture does not satisfy its persisted model: {exc}")
    if value.get("campaign_brief_id") != campaign_brief_id:
        errors.append("Message architecture campaign_brief_id does not match the validated campaign.")
    if value.get("brand") != brief.get("brand"):
        errors.append("Message architecture brand does not match the campaign brief.")
    if value.get("indication") != brief.get("indication"):
        errors.append("Message architecture indication does not match the campaign brief.")
    if not _timestamp(value.get("generated_at")):
        errors.append("Message architecture generated_at must be an ISO 8601 timestamp.")
    errors.extend(
        validate_message_data(
            value.get("message_tiers"),
            value.get("fair_balance_statement"),
            value.get("fair_balance_sources"),
            brief,
            claims_by_id,
            journey_stage_names,
        )
    )
    return ValidationResult(value if not errors else None, tuple(_unique(errors)))


def validate_message_data(
    messages: object,
    fair_balance: object,
    fair_balance_sources: object,
    brief: dict[str, Any],
    claims_by_id: dict[str, dict[str, Any]],
    journey_stage_names: set[str] | None = None,
) -> list[str]:
    """Validate message writer inputs/read-only payloads with one rule implementation."""
    errors: list[str] = []
    if not isinstance(messages, list) or not messages:
        errors.append("Message architecture must contain at least one message tier.")
        messages = []
    brief_channels = brief.get("channels") if isinstance(brief.get("channels"), list) else []
    for index, raw_message in enumerate(messages):
        prefix = f"Message {index + 1}"
        if not isinstance(raw_message, dict):
            errors.append(f"{prefix}: must be an object.")
            continue
        try:
            MessageTier.model_validate(raw_message)
        except (ValidationError, TypeError, ValueError, RecursionError, OverflowError, MemoryError) as exc:
            errors.append(f"{prefix}: does not satisfy MessageTier: {exc}")
        tier = raw_message.get("tier")
        if not isinstance(tier, str) or tier not in VALID_TIERS:
            errors.append(f"{prefix}: tier must be primary, secondary, or supporting.")
        message = raw_message.get("message")
        if not _nonblank_string(message):
            errors.append(f"{prefix}: message must be non-empty.")
        claim_ids = raw_message.get("claim_ids")
        if not _unique_nonblank_list(claim_ids):
            errors.append(f"{prefix}: claim_ids must contain unique approved claim IDs.")
            claim_ids = []
        for claim_id in claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                errors.append(f"{prefix}: claim_id '{claim_id}' is not in the approved claims set.")
                continue
            if not any(not claim_applicability_errors(claim, brief, channel) for channel in brief_channels):
                errors.append(f"{prefix}: claim_id '{claim_id}' is inapplicable to every requested campaign channel.")
            elif isinstance(message, str) and validate_claim_wording(message, claim)["status"] != "approved":
                errors.append(
                    f"{prefix}: message must exactly match approved claim '{claim_id}' or an allowed variant."
                )
        audience_stage = raw_message.get("audience_stage")
        if audience_stage is not None:
            if not _nonblank_string(audience_stage):
                errors.append(f"{prefix}: audience_stage must be null or a nonblank journey stage.")
            elif journey_stage_names is not None and audience_stage not in journey_stage_names:
                errors.append(f"{prefix}: audience_stage '{audience_stage}' is not present in the audience journey.")

    try:
        tier_limits = load_policy_rules(str(brief.get("policy_jurisdiction", "FDA"))).get("message_tier_counts", {})
    except (OSError, ValueError, TypeError, RecursionError, OverflowError, MemoryError) as exc:
        errors.append(f"Message architecture policy rules could not be loaded safely: {exc}")
        tier_limits = {}
    for tier, limits in tier_limits.items():
        if not isinstance(limits, dict):
            errors.append(f"Message architecture policy tier '{tier}' is malformed.")
            continue
        count = sum(isinstance(message, dict) and message.get("tier") == tier for message in messages)
        minimum = limits.get("min", 0)
        maximum = limits.get("max", float("inf"))
        if not isinstance(minimum, int) or isinstance(minimum, bool) or not isinstance(maximum, (int, float)):
            errors.append(f"Message architecture policy tier '{tier}' is malformed.")
        elif count < minimum or count > maximum:
            errors.append(f"Tier '{tier}' must contain between {minimum} and {maximum} messages; got {count}.")

    if not _nonblank_string(fair_balance):
        errors.append("fair_balance_statement is required.")
    if not isinstance(fair_balance_sources, list) or not fair_balance_sources:
        errors.append("fair_balance_sources must include approved source IDs.")
        fair_balance_sources = []
    for index, raw_source in enumerate(fair_balance_sources):
        prefix = f"fair_balance_sources[{index}]"
        if not isinstance(raw_source, dict):
            errors.append(f"{prefix}: must be an object.")
            continue
        try:
            SourceReference.model_validate(raw_source)
        except (ValidationError, TypeError, ValueError, RecursionError, OverflowError, MemoryError) as exc:
            errors.append(f"{prefix}: does not satisfy SourceReference: {exc}")
        document_id = raw_source.get("document_id")
        if not _nonblank_string(document_id):
            errors.append(f"{prefix}: document_id must be non-empty.")
            continue
        claim = claims_by_id.get(document_id)
        if claim is None:
            errors.append(f"{prefix}: document_id '{document_id}' is not an approved claim source ID.")
            continue
        if canonical_claim_category(claim.get("category")) not in SAFETY_CLAIM_CATEGORIES:
            errors.append(f"{prefix}: '{document_id}' must reference a safety or tolerability claim.")
        if not any(not claim_applicability_errors(claim, brief, channel) for channel in brief_channels):
            errors.append(f"{prefix}: '{document_id}' is inapplicable to every requested channel.")
        if isinstance(fair_balance, str) and validate_claim_wording(fair_balance, claim)["status"] != "approved":
            errors.append(
                f"fair_balance_statement must exactly match safety claim '{document_id}' or an allowed variant."
            )
        if raw_source.get("document_name") != claim.get("source_document"):
            errors.append(f"{prefix}: document_name must match the approved claim provenance.")
        if raw_source.get("excerpt") != claim.get("source_reference"):
            errors.append(f"{prefix}: excerpt must match the approved claim provenance.")
    return _unique(errors)


def validate_channel_copy(
    value: object,
    campaign_brief_id: str,
    channel: str,
    brief: dict[str, Any],
    claims_by_id: dict[str, dict[str, Any]],
    brand: BrandState | None,
) -> ValidationResult:
    """Validate a persisted channel copy envelope and all generation-time controls."""
    if not isinstance(value, dict):
        return ValidationResult(None, ("Channel copy artifact must be a JSON object.",))
    errors: list[str] = []
    try:
        envelope = PersistedChannelCopy.model_validate(value)
    except (ValidationError, TypeError, ValueError, RecursionError, OverflowError, MemoryError) as exc:
        return ValidationResult(None, (f"Copy artifact envelope is invalid: {exc}",))
    if envelope.campaign_brief_id != campaign_brief_id:
        errors.append("Copy artifact campaign_brief_id does not match the validated campaign.")
    if envelope.channel != channel:
        errors.append(f"Copy artifact channel '{envelope.channel}' does not match requested channel '{channel}'.")
    if channel not in VALID_CHANNELS or channel not in brief.get("channels", []):
        errors.append(f"Copy artifact channel '{channel}' is not selected by the campaign brief.")
    model = {"email": EmailCopy, "banner": BannerCopy, "poster": PosterCopy}.get(channel)
    copy_data = envelope.copy_data
    if model is None:
        errors.append(f"Copy artifact channel '{channel}' is unsupported.")
    else:
        try:
            copy_data = model.model_validate(copy_data).model_dump(mode="json", exclude_none=True)
        except (ValidationError, TypeError, ValueError, RecursionError, OverflowError, MemoryError) as exc:
            errors.append(f"{channel} copy is invalid: {exc}")
    if not _timestamp_or_absent(value.get("generated_at")):
        errors.append("Channel copy generated_at must be an ISO 8601 timestamp when present.")
    legal = brand.manifest.get("legal") if brand is not None else None
    if not isinstance(legal, dict):
        errors.append("Channel copy cannot be checked because brand legal inputs are invalid.")
        legal = {}
    if not errors or (model is not None and isinstance(copy_data, dict)):
        errors.extend(validate_copy_data(copy_data, channel, brief, claims_by_id, legal))
    return ValidationResult(value if not errors else None, tuple(_unique(errors)))


def extract_copy_blocks(copy_data: dict[str, Any], channel: str) -> list[tuple[str, dict[str, Any]]]:
    """Extract concrete copy blocks for both the writer and status validator."""
    blocks: list[tuple[str, dict[str, Any]]] = []
    simple = {
        "email": ("subject", "preheader", "headline", "cta"),
        "banner": ("headline", "sub_headline", "safety", "cta"),
        "poster": ("headline", "subhead", "cta"),
    }
    for field in simple.get(channel, ()):
        item = copy_data.get(field)
        if isinstance(item, dict):
            blocks.append((field, item))
    lists = {"email": ("body",), "poster": ("body", "bullet_points")}
    for field in lists.get(channel, ()):
        items = copy_data.get(field)
        if isinstance(items, list):
            blocks.extend((f"{field}[{index}]", item) for index, item in enumerate(items) if isinstance(item, dict))
    if channel == "poster":
        footnotes = copy_data.get("footnotes")
        if isinstance(footnotes, list):
            for index, item in enumerate(footnotes):
                if isinstance(item, dict):
                    blocks.append((f"footnotes[{index}]", item))
                elif isinstance(item, str):
                    blocks.append((f"footnotes[{index}]", {"text": item, "claim_ids": []}))
    return blocks


def validate_policy_report(
    campaign_id: str,
    brief: dict[str, Any],
    channels: list[str],
    claims_by_id: dict[str, dict[str, Any]],
    brand_manifest: object,
    copies: dict[str, dict[str, Any]],
    report: object,
) -> PolicyReportValidation:
    """Reconstruct every policy decision and reject noncanonical passing reports."""
    errors: list[str] = []
    canonical_claim_rows: list[dict[str, str]] = []
    if not isinstance(report, dict):
        return PolicyReportValidation(("policy-checks.json must be an object.",), ())
    _require_exact_keys(report, _POLICY_REPORT_KEYS, "policy-checks.json", errors)
    if report.get("campaign_brief_id") != campaign_id or report.get("overall_pass") is not True:
        errors.append("policy-checks.json campaign identity/pass state is invalid.")
    if report.get("channels_validated") != channels:
        errors.append("policy-checks.json channel order/coverage does not match the brief.")
    metadata = policy_metadata()
    if report.get("policy_hash") != metadata["hash"] or report.get("policy_version") != metadata["version"]:
        errors.append("policy-checks.json policy version/hash does not match the shipped policy.")
    if not _timestamp(report.get("generated_at")):
        errors.append("policy-checks.json generated_at is invalid.")
    channel_results = report.get("channel_results")
    if not isinstance(channel_results, dict) or list(channel_results) != channels:
        errors.append("policy-checks.json channel_results does not preserve exact brief channels.")
        channel_results = {}
    flattened_report_claims = report.get("claims_checked")
    if isinstance(flattened_report_claims, list):
        for index, claim in enumerate(flattened_report_claims):
            _require_exact_keys(
                claim,
                _POLICY_CLAIM_KEYS,
                f"policy-checks.json claims_checked[{index}]",
                errors,
            )
    else:
        errors.append("policy-checks.json claims_checked must be an array.")
    flattened_report_checks = report.get("policy_checks")
    if isinstance(flattened_report_checks, list):
        for index, check in enumerate(flattened_report_checks):
            _require_exact_keys(
                check,
                _POLICY_CHECK_KEYS,
                f"policy-checks.json policy_checks[{index}]",
                errors,
            )
    else:
        errors.append("policy-checks.json policy_checks must be an array.")
    flattened_claims: list[dict[str, Any]] = []
    flattened_checks: list[dict[str, Any]] = []
    rules = load_policy_rules(str(brief.get("policy_jurisdiction", "FDA")))
    legal = brand_manifest.get("legal") if isinstance(brand_manifest, dict) else None
    approved_claims = list(claims_by_id.values())
    for channel in channels:
        result = channel_results.get(channel)
        if not isinstance(result, dict) or result.get("channel") != channel or result.get("overall_pass") is not True:
            errors.append(f"policy-checks.json channel result is invalid: {channel}.")
            continue
        _require_exact_keys(
            result,
            _POLICY_CHANNEL_KEYS,
            f"policy-checks.json channel_results.{channel}",
            errors,
        )
        claims = result.get("claims_checked")
        checks = result.get("policy_checks")
        if not isinstance(claims, list) or any(not isinstance(item, dict) for item in claims):
            errors.append(f"policy-checks.json claims_checked is invalid: {channel}.")
            claims = []
        if not isinstance(checks, list) or any(not isinstance(item, dict) for item in checks):
            errors.append(f"policy-checks.json policy_checks is invalid: {channel}.")
            checks = []
        for index, claim in enumerate(claims):
            _require_exact_keys(
                claim,
                _POLICY_CLAIM_KEYS,
                f"policy-checks.json channel_results.{channel}.claims_checked[{index}]",
                errors,
            )
        for index, check in enumerate(checks):
            _require_exact_keys(
                check,
                _POLICY_CHECK_KEYS,
                f"policy-checks.json channel_results.{channel}.policy_checks[{index}]",
                errors,
            )
        flattened_claims.extend(claims)
        flattened_checks.extend(checks)
        copy_envelope = copies.get(channel, {})
        copy_data = copy_envelope.get("copy") if isinstance(copy_envelope, dict) else None
        if result.get("copy_exists") is not True:
            errors.append(f"policy-checks.json copy_exists must be true for {channel}.")
        channel_rows: list[dict[str, str]] = []
        blocks = extract_copy_blocks(copy_data or {}, channel)
        for occurrence, block in blocks:
            statement = block.get("text")
            claim_ids = block.get("claim_ids")
            if not isinstance(statement, str) or not isinstance(claim_ids, list):
                continue
            channel_rows.extend(
                {
                    "channel": channel,
                    "occurrence": occurrence,
                    "statement": statement,
                    "claim_id": claim_id,
                }
                for claim_id in claim_ids
                if isinstance(claim_id, str)
            )
        canonical_claim_rows.extend(channel_rows)
        if len(claims) != len(channel_rows):
            expected_occurrences = [f"{row['channel']}.{row['occurrence']} [{row['claim_id']}]" for row in channel_rows]
            errors.append(
                f"policy-checks.json does not cover every canonical copy occurrence in {channel}: "
                + ", ".join(expected_occurrences)
                + "."
            )
        for index, claim_result in enumerate(claims):
            claim_id = claim_result.get("declared_claim_id")
            if claim_id not in claims_by_id:
                errors.append(f"policy-checks.json references unknown claim '{claim_id}'.")
                continue
            if claim_result.get("status") != "approved":
                errors.append(f"policy-checks.json contains a non-passing claim result in {channel}.")
            if index >= len(channel_rows):
                errors.append(f"policy-checks.json contains an unmatched extra claim result in {channel}.")
                continue
            expected_row = channel_rows[index]
            if claim_result.get("statement") != expected_row["statement"] or claim_id != expected_row["claim_id"]:
                errors.append(
                    "policy-checks.json claim result does not match canonical copy occurrence "
                    f"{channel}.{expected_row['occurrence']}."
                )
                continue
            expected_match = validate_claim_wording(expected_row["statement"], claims_by_id[claim_id])
            if any(
                claim_result.get(field) != expected_match.get(field)
                for field in ("claim_id", "status", "matched_claim_text", "similarity_score", "deviation")
            ):
                errors.append(
                    "policy-checks.json claim decision differs from the approved claim for canonical copy "
                    f"occurrence {channel}.{expected_row['occurrence']}."
                )
            if not isinstance(claim_result.get("deviation"), (str, type(None))):
                errors.append(f"policy-checks.json claim deviation is invalid in {channel}.")
            if not isinstance(claim_result.get("matched_claim_text"), (str, type(None))):
                errors.append(f"policy-checks.json matched claim wording is invalid in {channel}.")
        expected_checks: list[dict[str, Any]] = []
        if rules.get("fair_balance_required", True):
            expected_checks.append(
                check_fair_balance(
                    [block for _occurrence, block in blocks],
                    approved_claims,
                    rules.get("min_safety_ratio", 0.3),
                )
            )
        channel_text = " ".join(str(block.get("text", "")) for _occurrence, block in blocks)
        prohibited = check_prohibited_language(channel_text, rules.get("prohibited_patterns", []))
        if brief.get("mode") in {"non_promotional", "disease_awareness"}:
            prohibited.extend(check_prohibited_language(channel_text, rules.get("non_promotional_prohibited", [])))
        if prohibited:
            errors.append(f"Canonical {channel} copy contains prohibited language under the shipped policy.")
        if channel == "banner" and banner_safety_errors(copy_data or {}, claims_by_id, brief):
            errors.append("Canonical banner copy does not satisfy the production banner-safety constraint.")
        required = (
            rules.get("channel_requirements", {})
            .get(channel, {})
            .get("required_elements", rules.get("required_elements", []))
        )
        expected_checks.extend(check_required_elements(legal, required))
        if checks != expected_checks:
            errors.append(
                f"policy-checks.json does not contain the exact canonical policy checks in production order: {channel}."
            )
    if report.get("claims_checked") != flattened_claims:
        errors.append("policy-checks.json flattened claim results do not match channel results.")
    if report.get("policy_checks") != flattened_checks:
        errors.append("policy-checks.json flattened policy checks do not match channel results.")
    return PolicyReportValidation(tuple(_unique(errors)), tuple(canonical_claim_rows))


def validate_copy_data(
    copy_data: object,
    channel: str,
    brief: dict[str, Any],
    claims_by_id: dict[str, dict[str, Any]],
    legal: dict[str, Any],
) -> list[str]:
    if not isinstance(copy_data, dict):
        return ["Channel copy payload must be an object."]
    errors: list[str] = []
    promotional = brief.get("mode") == "promotional"
    for block_name, block in extract_copy_blocks(copy_data, channel):
        text = block.get("text")
        claim_ids = block.get("claim_ids")
        if not isinstance(text, str) or not isinstance(claim_ids, list):
            errors.append(f"{block_name}: copy block is malformed.")
            continue
        for claim_id in claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                errors.append(f"{block_name}: claim_id '{claim_id}' is not in the approved claims set.")
            else:
                reasons = claim_applicability_errors(claim, brief, channel)
                if reasons:
                    errors.append(f"{block_name}: claim_id '{claim_id}' is inapplicable: {', '.join(reasons)}.")
        if (
            promotional
            and text.strip()
            and not claim_ids
            and not is_claim_citation_exempt(block_name, text, brief, legal)
        ):
            errors.append(f"{block_name}: promotional copy must cite at least one approved claim ID.")
    cta = copy_data.get("cta")
    cta_text = cta.get("text") if isinstance(cta, dict) else None
    if (
        not isinstance(cta_text, str)
        or cta_text.strip().casefold() != str(brief.get("call_to_action", "")).strip().casefold()
    ):
        errors.append("cta: text must exactly match the campaign brief call_to_action.")
    footnotes = copy_data.get("footnotes")
    if isinstance(footnotes, list):
        for index, footnote in enumerate(footnotes):
            if isinstance(footnote, str) and not is_claim_citation_exempt("footnote", footnote, brief, legal):
                errors.append(f"footnotes[{index}]: string footnotes must be verbatim approved legal text.")
            if isinstance(footnote, dict) and not footnote.get("claim_ids"):
                errors.append(f"footnotes[{index}]: CopyBlock footnotes must cite at least one approved claim ID.")
    if channel == "banner":
        errors.extend(banner_safety_errors(copy_data, claims_by_id, brief))
    return _unique(errors)


def _validate_brand_file(
    manifest: dict[str, Any], root_raw: str, root: Path, name: str, metadata: object, errors: list[str]
) -> None:
    if not isinstance(metadata, dict):
        errors.append(f"Brand file metadata for {name} must be an object.")
        return
    expected_path = str(Path(root_raw) / name)
    expected_resolved = root / name
    if metadata.get("path") != expected_path:
        errors.append(f"Brand file metadata path for {name} does not match the selected kit path.")
    if metadata.get("resolved_path") != str(expected_resolved):
        errors.append(f"Brand file metadata resolved_path for {name} does not match the selected kit root.")
    if not _valid_hash(metadata.get("sha256")):
        errors.append(f"Brand file metadata sha256 for {name} is invalid.")
    if not _valid_size(metadata.get("size")):
        errors.append(f"Brand file metadata size for {name} is invalid.")
    live = _regular_file(expected_resolved, f"Brand selected file {name}")
    if live is None:
        errors.append(f"Brand selected file {name} is missing, unsafe, or not a regular file.")
        return
    digest, size = _file_digest_size(live)
    if digest is None:
        errors.append(f"Brand selected file {name} could not be read safely.")
        return
    if metadata.get("sha256") != digest or metadata.get("size") != size:
        errors.append(f"Brand selected file {name} no longer matches persisted metadata.")
    if name.endswith(".json"):
        live_value, parse_error = read_existing_json(live)
        if parse_error or not isinstance(live_value, dict):
            errors.append(f"Brand selected JSON file {name} is malformed or unreadable.")
        elif live_value != manifest.get(name.removesuffix(".json")):
            errors.append(f"Brand selected JSON file {name} no longer matches persisted renderer values.")
    elif name == "logo.svg":
        svg_error = _validate_svg(live)
        if svg_error:
            errors.append("Brand selected logo.svg is unsafe or malformed.")


def _validate_brand_top_level_paths(
    manifest: dict[str, Any], root_raw: str, root: Path, files: dict[str, Any], errors: list[str]
) -> None:
    logo = files.get("logo.svg")
    if isinstance(logo, dict):
        if manifest.get("logo_path") != str(Path(root_raw) / "logo.svg"):
            errors.append("Brand components logo_path does not match the selected logo file.")
        if manifest.get("resolved_logo_path") != str(root / "logo.svg"):
            errors.append("Brand components resolved_logo_path does not match the selected logo file.")
    product = files.get("product.png")
    if product is None:
        if manifest.get("product_image_path") is not None or manifest.get("resolved_product_image_path") is not None:
            errors.append("Brand components product image paths are declared without selected product metadata.")
    elif isinstance(product, dict):
        if manifest.get("product_image_path") != str(Path(root_raw) / "product.png"):
            errors.append("Brand components product_image_path does not match the selected product file.")
        if manifest.get("resolved_product_image_path") != str(root / "product.png"):
            errors.append("Brand components resolved_product_image_path does not match the selected product file.")


def _validate_claims_source(
    source: object, brief: dict[str, Any], claims: list[dict[str, Any]], errors: list[str]
) -> bool:
    if not isinstance(source, dict):
        return False
    path = source.get("path")
    resolved = source.get("resolved_path")
    if path != brief.get("approved_claims_path"):
        errors.append("Input provenance claims path does not match the campaign brief.")
    file_path = _validate_provenance_file(path, resolved, source, "claims", errors)
    is_demo = source.get("is_demo_fixture")
    if not isinstance(is_demo, bool):
        errors.append("Input provenance claims is_demo_fixture must be a boolean.")
        is_demo = False
    if file_path is None:
        return bool(is_demo)
    actual_demo = _is_bundled_fixture(file_path)
    if actual_demo != is_demo:
        errors.append("Input provenance claims demo flag does not match the selected source.")
    raw_source, parse_error = read_existing_json(file_path)
    if parse_error or not isinstance(raw_source, list):
        errors.append("Input provenance claims source is malformed or unreadable.")
        return bool(is_demo)
    canonical_source: dict[str, dict[str, Any]] = {}
    for raw_claim in raw_source:
        if not isinstance(raw_claim, dict):
            continue
        claim_id = raw_claim.get("claim_id")
        if not isinstance(claim_id, str) or claim_id in canonical_source:
            continue
        parsed, parse_errors = validate_persisted_claims([raw_claim])
        if not parse_errors:
            canonical_source[claim_id] = parsed[0]
    for claim in claims:
        source_claim = canonical_source.get(claim["claim_id"])
        if source_claim != claim:
            errors.append(
                f"Input provenance claims source does not match persisted approved claim '{claim['claim_id']}'."
            )
    return bool(is_demo)


def _validate_brand_source(source: object, brief: dict[str, Any], brand: BrandState | None, errors: list[str]) -> bool:
    if not isinstance(source, dict):
        return False
    path = source.get("path")
    resolved = source.get("resolved_path")
    is_demo = source.get("is_demo_fixture")
    if not isinstance(is_demo, bool):
        errors.append("Input provenance brand_kit is_demo_fixture must be a boolean.")
        is_demo = False
    if brand is None:
        errors.append("Input provenance brand_kit cannot be checked because brand components are invalid.")
        return bool(is_demo)
    if path != brief.get("brand_kit_path") or path != brand.manifest.get("brand_kit_path"):
        errors.append("Input provenance brand_kit path does not match the campaign brief and selected manifest.")
    if resolved != brand.manifest.get("resolved_brand_kit_path"):
        errors.append("Input provenance brand_kit resolved_path does not match the selected manifest.")
    if source.get("files") != brand.manifest.get("files"):
        errors.append("Input provenance brand_kit files do not match selected brand metadata.")
    if not isinstance(path, str) or not isinstance(resolved, str):
        errors.append("Input provenance brand_kit requires lexical and resolved paths.")
        return bool(is_demo)
    _validate_lexical_target(path, brand.root, "Input provenance brand_kit path", errors)
    actual_demo = _is_bundled_fixture(brand.root)
    if actual_demo != is_demo:
        errors.append("Input provenance brand_kit demo flag does not match the selected source.")
    return bool(is_demo)


def _validate_provenance_file(
    lexical: object, resolved: object, source: dict[str, Any], label: str, errors: list[str]
) -> Path | None:
    if not _nonblank_string(lexical) or not _nonblank_string(resolved):
        errors.append(f"Input provenance {label} requires lexical and resolved paths.")
        return None
    path = _regular_file(Path(resolved), f"Input provenance {label} resolved path")
    if path is None:
        errors.append(f"Input provenance {label} resolved path is missing, unsafe, or not a regular file.")
        return None
    _validate_lexical_target(lexical, path, f"Input provenance {label} path", errors)
    if not _valid_hash(source.get("sha256")) or not _valid_size(source.get("size")):
        errors.append(f"Input provenance {label} hash or size is invalid.")
        return path
    digest, size = _file_digest_size(path)
    if digest is None or source.get("sha256") != digest or source.get("size") != size:
        errors.append(f"Input provenance {label} no longer matches the selected source file.")
    return path


def _canonical_directory(raw_path: object, label: str, errors: list[str]) -> Path | None:
    if not _nonblank_string(raw_path):
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        errors.append(f"{label} must be an absolute canonical path.")
        return None
    try:
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            errors.append(f"{label} must be a non-symlink directory.")
            return None
        resolved = candidate.resolve(strict=True)
        if resolved != candidate:
            errors.append(f"{label} must be a canonical non-symlink directory path.")
            return None
        return resolved
    except OSError:
        errors.append(f"{label} could not be inspected safely.")
        return None


def _regular_file(candidate: Path, _label: str) -> Path | None:
    try:
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return None
        resolved = candidate.resolve(strict=True)
        if resolved != candidate:
            return None
        return resolved
    except OSError:
        return None


def _validate_lexical_target(lexical: object, resolved: Path, label: str, errors: list[str]) -> None:
    if not _nonblank_string(lexical):
        errors.append(f"{label} must be a nonblank path.")
        return
    candidate = Path(lexical)
    if ".." in candidate.parts:
        errors.append(f"{label} may not contain parent traversal.")
        return
    if not candidate.is_absolute():
        return
    try:
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or candidate.resolve(strict=True) != resolved:
            errors.append(f"{label} does not resolve to the persisted canonical target.")
    except OSError:
        errors.append(f"{label} could not be inspected safely.")


def _file_digest_size(path: Path) -> tuple[str | None, int | None]:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest(), path.stat().st_size
    except OSError:
        return None, None


def _require_strings(value: dict[str, Any], fields: tuple[str, ...], errors: list[str], prefix: str) -> None:
    for field in fields:
        if not _nonblank_string(value.get(field)):
            errors.append(f"{prefix} {field} must be a nonblank string.")


def _nonblank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _unique_nonblank_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(_nonblank_string(item) for item in value)
        and len(value) == len(set(value))
    )


def _nonempty_nonblank_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(_nonblank_string(item) for item in value)


def _timestamp(value: object) -> bool:
    if not _nonblank_string(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _timestamp_or_absent(value: object) -> bool:
    return value is None or _timestamp(value)


def _valid_hash(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _valid_size(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _require_exact_keys(value: object, expected: frozenset[str], label: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object.")
        return
    actual = set(value)
    undeclared = sorted(actual - expected)
    missing = sorted(expected - actual)
    if undeclared:
        errors.append(f"{label} contains undeclared fields: {', '.join(undeclared)}.")
    if missing:
        errors.append(f"{label} is missing declared fields: {', '.join(missing)}.")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
