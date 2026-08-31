"""Package-owned batch runtime for hcp-intelligence accounts."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import random
import stat
import sys
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Literal

BUNDLED_INPUT = Path(__file__).parent / "fixtures" / "sample_accounts.csv"
INPUT_COLUMNS = ("id", "name", "specialty", "country", "account_type", "institution")
REQUIRED_VALUES = ("id", "name", "country", "account_type")
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_SYNTHESIS_MODEL = "deepseek/deepseek-v4-flash-0731"
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_SYNTHESIS_TIMEOUT_SECONDS = 120.0


class BatchUsageError(ValueError):
    """A user-correctable batch input or path error."""


class SynthesisProviderError(RuntimeError):
    """A sanitized synthesis-provider failure safe to persist."""


@dataclass(frozen=True)
class BatchOptions:
    input_file: str | Path | None
    output_dir: str | Path
    country: str | None
    account_type: str | None
    ids: tuple[str, ...]
    concurrency: int
    resume: bool
    write_back: bool
    synthesize: bool
    base_url: str
    api_key_env: str
    model: str
    reasoning_effort: Literal["high", "xhigh"]
    synthesis_timeout_seconds: float


@dataclass(frozen=True)
class BatchPlan:
    input_path: Path
    input_sha256: str
    output_dir: Path
    accounts: tuple[dict[str, str], ...]
    options: BatchOptions


@dataclass(frozen=True)
class BatchOutcome:
    output_dir: Path
    results: tuple[dict[str, Any], ...]
    manifest: dict[str, Any]
    exit_code: int


# ---------------------------------------------------------------------------
# Tool imports (lazy so importing the batch runtime has no provider side effects)
# ---------------------------------------------------------------------------

_TOOL_MODULES: dict[str, object] = {}
_WRITEBACK_LOCK = Lock()
_ACTIVE_API_KEY_ENV: ContextVar[str] = ContextVar("hcp_batch_api_key_env", default="OPENROUTER_API_KEY")
_REDACTED_PROVIDER_KEYS = ("EXA_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY", "NCBI_API_KEY")


def sanitize_error(error: object, api_key_env: str | None = None) -> str:
    """Redact configured provider credentials from an HCP batch error."""
    from shared.env import get_env

    selected_key = api_key_env or _ACTIVE_API_KEY_ENV.get()
    key_names = dict.fromkeys((selected_key, *_REDACTED_PROVIDER_KEYS))
    credentials = sorted(
        (value for key in key_names if (value := get_env(key, ""))),
        key=len,
        reverse=True,
    )
    safe = str(error)
    for credential in credentials:
        safe = safe.replace(credential, "[REDACTED]")
    return safe


def resolve_user_path(value: str | Path, *, label: str) -> Path:
    """Resolve a user path only after rejecting invisible control characters."""
    raw = str(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise BatchUsageError(f"{label} contains a control character")
    return Path(raw).expanduser().resolve()


def _load_accounts_from_bytes(payload: bytes) -> list[dict[str, str]]:
    """Parse one captured CSV byte snapshot into the normalized public contract."""
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("input file must be UTF-8 CSV") from exc

    with io.StringIO(text, newline="") as handle:
        reader = csv.DictReader(handle)
        headers = set(reader.fieldnames or [])
        missing = sorted(set(INPUT_COLUMNS) - headers)
        if missing:
            raise ValueError(f"missing columns: {', '.join(missing)}")

        accounts: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for row_number, raw in enumerate(reader, start=2):
            raw_account_id = raw.get("id") or ""
            if any(ord(char) < 32 or ord(char) == 127 for char in raw_account_id):
                raise ValueError(f"row {row_number} account id contains a control character")
            account = {key: (raw.get(key) or "").strip() for key in INPUT_COLUMNS}
            blank = [key for key in REQUIRED_VALUES if not account[key]]
            if blank:
                raise ValueError(f"row {row_number}: blank required values: {', '.join(blank)}")

            account_type = account["account_type"].upper()
            if account_type not in {"HCP", "HCO"}:
                raise ValueError(f"row {row_number}: account_type must be HCP or HCO")
            account["account_type"] = account_type

            account_id = account["id"]
            from shared.filesystem import validate_component

            validate_component(account_id, label=f"row {row_number} account id")
            if account_id in seen_ids:
                raise ValueError(f"row {row_number}: duplicate account id: {account_id}")
            seen_ids.add(account_id)
            accounts.append(account)

    if not accounts:
        raise ValueError("input file contains no account rows")
    return accounts


def load_accounts_from_csv(path: str | Path) -> list[dict[str, str]]:
    """Load and validate the public batch-input contract before any network calls."""
    source = resolve_user_path(path, label="input path")
    if not source.is_file():
        raise ValueError(f"input file does not exist: {source}")
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise ValueError(f"could not read input file: {source}") from exc
    return _load_accounts_from_bytes(payload)


def validate_synthesized_profile(profile_json: str, account_type: str) -> dict:
    """Reject malformed or out-of-contract LLM output before persistence."""
    from open_pharma_plugins_hcp_intelligence.models import HcoProfile, HcpProfile

    data = json.loads(profile_json)
    model = HcpProfile if account_type.upper() == "HCP" else HcoProfile
    return model.model_validate(data).model_dump(mode="json", exclude_unset=True)


def write_batch_json(path: str | Path, data: object) -> Path:
    """Persist a private, atomic JSON artifact."""
    from shared.filesystem import atomic_write_json

    return atomic_write_json(path, data)


def build_batch_manifest(
    *,
    input_path: str | Path,
    input_sha256: str,
    selected_count: int,
    synthesis: bool,
    model: str,
    base_url: str,
    reasoning_effort: str,
    synthesis_timeout_seconds: float,
    concurrency: int,
    results: list[dict],
    summary_csv: dict[str, object],
    started_at: str,
    completed_at: str,
) -> dict:
    """Build an auditable batch summary without duplicating account names."""
    source = Path(input_path).expanduser().resolve()
    statuses = {status: 0 for status in ("completed", "partial", "failed", "skipped")}
    accounts: list[dict] = []
    allowed = ("account_id", "status", "tools_failed", "output_file", "profile_validated", "error")
    for result in results:
        status = result["status"]
        statuses[status] += 1
        accounts.append({key: result[key] for key in allowed if key in result})

    return {
        "schema_version": 2,
        "input": {
            "path": str(source),
            "sha256": input_sha256,
        },
        "started_at": started_at,
        "completed_at": completed_at,
        "processing": {
            "synthesis": synthesis,
            "model": model if synthesis else None,
            "base_url": base_url if synthesis else None,
            "reasoning_effort": reasoning_effort if synthesis else None,
            "timeout_seconds": synthesis_timeout_seconds if synthesis else None,
            "concurrency": concurrency,
        },
        "summary": {"selected": selected_count, **statuses},
        "accounts": accounts,
        "outputs": {"summary_csv": summary_csv},
    }


def _load_tools():
    if _TOOL_MODULES:
        return
    from open_pharma_plugins_hcp_intelligence.tools import (
        search_clinical_trials,
        search_congresses,
        search_grants,
        search_guidelines,
        search_hco_web,
        search_hcp_web,
        search_orcid,
        search_publications,
    )

    _TOOL_MODULES.update(
        {
            "search_orcid": search_orcid,
            "search_publications": search_publications,
            "search_guidelines": search_guidelines,
            "search_clinical_trials": search_clinical_trials,
            "search_congresses": search_congresses,
            "search_grants": search_grants,
            "search_hcp_web": search_hcp_web,
            "search_hco_web": search_hco_web,
        }
    )


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------

MAX_RETRIES = 3


def _call_tool(tool_name: str, arguments: dict) -> dict | None:
    module = _TOOL_MODULES[tool_name]
    for attempt in range(MAX_RETRIES + 1):
        try:
            result = module.handle(arguments)
            return json.loads(result[0]["text"])
        except Exception as e:
            retryable = not isinstance(e, urllib.error.HTTPError) or e.code in {408, 425, 429} or e.code >= 500
            if not retryable or attempt == MAX_RETRIES:
                print(f"    FAIL {tool_name}: {sanitize_error(e)}", file=sys.stderr)
                return None
            delay = min(2**attempt + random.uniform(0, 1), 30.0)
            print(f"    RETRY {tool_name} (attempt {attempt + 1}): {sanitize_error(e)}", file=sys.stderr)
            time.sleep(delay)
    return None


# ---------------------------------------------------------------------------
# Per-account enrichment
# ---------------------------------------------------------------------------

HCP_TOOLS = [
    "search_orcid",
    "search_publications",
    "search_guidelines",
    "search_clinical_trials",
    "search_congresses",
    "search_grants",
    "search_hcp_web",
]

HCO_TOOLS = [
    "search_hco_web",
    "search_clinical_trials",
    "search_grants",
]


def _enrich_account(account: dict, output_dir: Path) -> dict:
    account_id = account["id"]
    name = account["name"]
    specialty = account.get("specialty", "")
    country = account.get("country", "")
    institution = account.get("institution", "")
    account_type = account.get("account_type", "HCP").upper()

    print(f"  [{account_id}] {name} ({account_type}, {country})")

    is_hcp = account_type == "HCP"
    tools = HCP_TOOLS if is_hcp else HCO_TOOLS
    results: dict[str, dict | None] = {}

    for tool_name in tools:
        args = _build_args(tool_name, name, specialty, country, institution, is_hcp)
        if args is None:
            continue
        results[tool_name] = _call_tool(tool_name, args)
        time.sleep(0.3)

    output = {
        "account": {
            "id": account_id,
            "name": name,
            "specialty": specialty,
            "country": country,
            "account_type": account_type,
            "institution": institution,
        },
        "search_results": {k: v for k, v in results.items() if v is not None},
        "tools_called": list(results.keys()),
        "tools_succeeded": [k for k, v in results.items() if v is not None],
        "tools_failed": [k for k, v in results.items() if v is None],
    }

    output_file = output_dir / f"{account_id}.json"
    write_batch_json(output_file, output)
    print(f"    saved → {output_file}")

    return output


def _build_args(
    tool_name: str,
    name: str,
    specialty: str,
    country: str,
    institution: str,
    is_hcp: bool,
) -> dict | None:
    if tool_name == "search_orcid":
        args = {"name": name, "max_results": 3}
        if institution:
            args["affiliation"] = institution
        return args

    if tool_name == "search_publications":
        args = {"author_name": name, "max_results": 20}
        if institution:
            args["affiliation"] = institution
        return args

    if tool_name == "search_guidelines":
        args = {"name": name, "scope": "both", "max_results": 10}
        if specialty:
            args["therapeutic_area"] = specialty
        return args

    if tool_name == "search_clinical_trials":
        if is_hcp:
            args = {"investigator_name": name, "max_results": 15}
        else:
            args = {"organization_name": name, "max_results": 15}
        if country:
            args["country"] = country
        return args

    if tool_name == "search_congresses":
        args = {"name": name, "max_results": 15}
        if specialty:
            args["specialty"] = specialty
        return args

    if tool_name == "search_grants":
        args: dict = {"pi_name": name, "max_results": 15}
        if institution:
            args["institution"] = institution
        if country:
            args["country"] = country
        return args

    if tool_name == "search_hcp_web":
        args = {"name": name, "max_results": 10}
        if specialty:
            args["specialty"] = specialty
        if country:
            args["country"] = country
        if institution:
            args["institution"] = institution
        return args

    if tool_name == "search_hco_web":
        args = {"name": name, "max_results": 10}
        if country:
            args["country"] = country
        return args

    return None


# ---------------------------------------------------------------------------
# LLM synthesis
# ---------------------------------------------------------------------------

SYNTHESIS_SYSTEM_PROMPT = """\
You are an HCP/HCO intelligence analyst. Given an account record and raw search \
results from PubMed, ClinicalTrials.gov, ORCID, NIH RePORTER, web search, and \
congress/guideline searches, synthesize a structured profile.

RULES:
1. Follow the supplied JSON Schema exactly. Identity and metadata fields typed as strings or numbers \
MUST remain plain JSON scalars; do not wrap them as evidenced claims.
2. Every field typed as EvidencedClaim MUST contain value, sources, and confidence. Each source \
requires a URL and access date. Assign confidence: high (2+ authoritative sources), medium \
(1 authoritative), low (1 informal).
3. SourceCitation.source_type MUST be exactly one of: pubmed, clinical_trials, web, registry. \
Map ORCID to registry, ClinicalTrials.gov to clinical_trials, and congress/guideline pages to web.
4. Do NOT fabricate data. If an optional section has no supporting evidence, omit it or leave it empty.
5. Set profile_completeness (0.0-1.0) based on how many sections have data.
6. Include disambiguation_notes as a plain string if the person has a common name.

OUTPUT FORMAT:
Return ONLY a valid JSON object conforming to the HcpProfile schema (for HCPs) or \
HcoProfile schema (for HCOs). No markdown, no commentary, just the JSON object.

HcpProfile fields: full_name, specialty, country, current_title, designations, \
affiliations, education, qualifications, society_memberships, professional_roles, \
editorial_roles, research_interests, publication_summary, key_publications (up to 10), \
guideline_publications, clinical_trial_involvement, active_grants, congress_activity, \
regulatory_advisory_roles, orcid_id, profile_completeness, disambiguation_notes, \
sources_consulted, built_at.

HcoProfile fields: name, country, organization_type, clinical_focus_areas, \
specialist_departments, bed_capacity, annual_patient_volume, staff_count, \
accreditations, research_focus, active_clinical_trials, institutional_grants, \
founding_year, key_milestones, notable_affiliations, profile_completeness, \
sources_consulted, built_at.

Each evidenced claim has: value (string), sources (list of {url, source_type, title, \
accessed_date}), confidence (high/medium/low).\
"""


def _profile_response_format(account_type: str) -> dict:
    """Build the OpenRouter structured-output contract from the Pydantic source of truth."""
    from open_pharma_plugins_hcp_intelligence.models import HcoProfile, HcpProfile

    is_hcp = account_type.upper() == "HCP"
    profile_model = HcpProfile if is_hcp else HcoProfile
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "hcp_profile" if is_hcp else "hco_profile",
            "strict": True,
            "schema": profile_model.model_json_schema(),
        },
    }


def _synthesize(raw_output: dict, options: BatchOptions) -> str | None:
    try:
        from openai import OpenAI
    except ImportError:
        print(
            "    SKIP synthesis: 'openai' package not installed. Install with: pip install openai",
            file=sys.stderr,
        )
        return None

    from shared.env import get_env

    api_key = get_env(options.api_key_env, "")
    if not api_key and options.api_key_env != "NONE":
        print(
            f"    SKIP synthesis: {options.api_key_env} not set in the process environment or user config.",
            file=sys.stderr,
        )
        return None

    client = OpenAI(
        base_url=options.base_url,
        api_key=api_key or "not-needed",
        timeout=options.synthesis_timeout_seconds,
        max_retries=0,
    )

    account = raw_output["account"]
    user_msg = (
        f"Account: {json.dumps(account)}\n\nSearch results:\n{json.dumps(raw_output['search_results'], indent=2)}"
    )

    try:
        response = client.chat.completions.create(
            model=options.model,
            messages=[
                {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=8192,
            response_format=_profile_response_format(account["account_type"]),
            extra_body={
                "reasoning": {"effort": options.reasoning_effort},
                "provider": {"require_parameters": True},
            },
        )
    except Exception as e:
        safe_error = sanitize_error(e, options.api_key_env)
        message = f"synthesis provider failed ({type(e).__name__}): {safe_error}"
        print(f"    FAIL synthesis: {message}", file=sys.stderr)
        raise SynthesisProviderError(message) from e

    choice = response.choices[0]
    if choice.message.content:
        return choice.message.content

    usage = getattr(response, "usage", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    finish_reason = getattr(choice, "finish_reason", None) or "unknown"
    completion_tokens = getattr(usage, "completion_tokens", None)
    reasoning_tokens = getattr(completion_details, "reasoning_tokens", None)
    raise ValueError(
        "OpenRouter returned no final content "
        f"(finish_reason={finish_reason}, completion_tokens={completion_tokens}, "
        f"reasoning_tokens={reasoning_tokens})"
    )


# ---------------------------------------------------------------------------
# Planning and execution
# ---------------------------------------------------------------------------


def _validate_output_directory(path: Path, *, resume: bool) -> None:
    if path.exists() and not path.is_dir():
        raise BatchUsageError("output path is not a directory")
    if path.exists() and any(path.iterdir()) and not resume:
        raise BatchUsageError("output directory is not empty; pass --resume to reuse it")


def prepare_output_directory(path: Path, *, resume: bool) -> Path:
    """Validate output reuse, then create a private execution directory."""
    from shared.filesystem import ensure_private_dir

    _validate_output_directory(path, resume=resume)
    return ensure_private_dir(path).resolve()


def plan_batch(options: BatchOptions) -> BatchPlan:
    """Resolve paths, validate the complete input, and apply account filters."""
    if options.concurrency < 1:
        raise BatchUsageError("--concurrency must be at least 1")
    if not math.isfinite(options.synthesis_timeout_seconds) or options.synthesis_timeout_seconds <= 0:
        raise BatchUsageError("--synthesis-timeout-seconds must be greater than 0 and finite")
    account_type = options.account_type.upper() if options.account_type else None
    if account_type not in {None, "HCP", "HCO"}:
        raise BatchUsageError("--account-type must be HCP or HCO")

    try:
        if options.input_file is None:
            input_path = resolve_user_path(BUNDLED_INPUT, label="input path")
        else:
            input_path = resolve_user_path(options.input_file, label="input path")
        if not input_path.is_file():
            raise ValueError(f"input file does not exist: {input_path}")
        try:
            input_payload = input_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"could not read input file: {input_path}") from exc
        accounts = _load_accounts_from_bytes(input_payload)
    except ValueError as exc:
        raise BatchUsageError(str(exc)) from exc

    output_dir = resolve_user_path(options.output_dir, label="output path")

    if options.country:
        accounts = [account for account in accounts if account["country"].lower() == options.country.lower()]
    if account_type:
        accounts = [account for account in accounts if account["account_type"] == account_type]
    if options.ids:
        selected_ids = set(options.ids)
        accounts = [account for account in accounts if account["id"] in selected_ids]

    input_sha256 = hashlib.sha256(input_payload).hexdigest()
    if accounts:
        planned_outputs = {
            *(output_dir / f"{account['id']}.json" for account in accounts),
            output_dir / "batch_summary.csv",
            output_dir / "batch_manifest.json",
        }
        if input_path in {path.resolve() for path in planned_outputs}:
            raise BatchUsageError("input file collides with planned output")
        _validate_output_directory(output_dir, resume=options.resume)

    return BatchPlan(
        input_path=input_path,
        input_sha256=input_sha256,
        output_dir=output_dir,
        accounts=tuple(accounts),
        options=options,
    )


def _load_resume_artifact(path: Path, account: dict[str, str]) -> dict[str, Any] | None:
    """Load a regular, canonical artifact owned by exactly one selected account."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        return None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened_metadata = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened_metadata.st_mode)
                or opened_metadata.st_dev != metadata.st_dev
                or opened_metadata.st_ino != metadata.st_ino
            ):
                return None
            payload = handle.read()
        loaded = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict) or loaded.get("account") != account:
        return None
    if not isinstance(loaded.get("search_results"), dict):
        return None
    for key in ("tools_called", "tools_succeeded", "tools_failed"):
        values = loaded.get(key)
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            return None
    return loaded


def run_batch(plan: BatchPlan, emit: Callable[[str], None] = print) -> BatchOutcome:
    """Run a planned batch and persist its ordered artifacts and manifest."""
    options = plan.options
    accounts = plan.accounts
    if not accounts:
        return BatchOutcome(
            output_dir=plan.output_dir,
            results=(),
            manifest={},
            exit_code=0,
        )

    output_dir = prepare_output_directory(plan.output_dir, resume=options.resume)
    _load_tools()
    write_back = options.write_back or options.input_file is None
    if write_back:
        from open_pharma_plugins_hcp_intelligence._crm_store import write_enrichment
    else:
        write_enrichment = None
    validated_artifacts: dict[str, dict[str, Any]] = {}
    artifact_lock = Lock()

    def _remember_artifact(account_id: str, artifact: dict[str, Any]) -> None:
        with artifact_lock:
            validated_artifacts[account_id] = artifact

    emit(f"Processing {len(accounts)} account(s), concurrency={options.concurrency}")
    if options.synthesize:
        emit(f"Synthesis: {options.model} via {options.base_url}")
        emit(
            f"Reasoning effort: {options.reasoning_effort}; "
            f"timeout: {options.synthesis_timeout_seconds:g}s; SDK retries: 0"
        )
    emit("")

    started_at = datetime.now(timezone.utc).isoformat()

    def _persist_writeback(account_id: str, profile_json: str, status: str) -> None:
        if not write_back:
            return
        assert write_enrichment is not None
        with _WRITEBACK_LOCK:
            write_enrichment(account_id, profile_json, status)

    def _process_account(account: dict) -> dict:
        account_id = account["id"]
        result_file = output_dir / f"{account_id}.json"
        raw: dict | None = None
        writeback_attempted = False
        artifact_write_failed = False

        if options.resume:
            existing = _load_resume_artifact(result_file, account)
            if existing is None:
                try:
                    result_file.lstat()
                except (FileNotFoundError, OSError):
                    pass
                else:
                    emit(f"  [{account_id}] existing artifact is unusable; processing normally")
            else:
                synthesized_profile = existing.get("synthesized_profile")
                profile_validated = False
                if options.synthesize and synthesized_profile:
                    try:
                        validate_synthesized_profile(json.dumps(synthesized_profile), account["account_type"])
                    except (TypeError, ValueError):
                        existing.pop("synthesized_profile", None)
                        existing.pop("synthesis_error", None)
                        raw = existing
                        emit(f"  [{account_id}] invalid synthesized profile; reusing raw evidence")
                    else:
                        profile_validated = True
                if not options.synthesize or profile_validated:
                    _remember_artifact(account_id, existing)
                    emit(f"  [{account_id}] skipped (exists)")
                    return {
                        "account_id": account_id,
                        "status": "skipped",
                        "tools_failed": existing.get("tools_failed", []),
                        "output_file": str(result_file),
                        "profile_validated": profile_validated,
                    }
                if raw is None:
                    raw = existing
                    raw.pop("synthesis_error", None)
                    emit(f"  [{account_id}] reusing raw evidence for synthesis")

        try:
            if raw is None:
                raw = _enrich_account(account, output_dir)
                try:
                    write_batch_json(result_file, raw)
                except Exception:
                    artifact_write_failed = True
                    raise
            tools_failed = raw.get("tools_failed", [])
            status = "partial" if tools_failed else "completed"
            profile_validated = False

            if options.synthesize:
                profile_json = _synthesize(raw, options)
                if not profile_json:
                    raise ValueError("synthesis returned no profile")
                profile = validate_synthesized_profile(profile_json, account["account_type"])
                raw["synthesized_profile"] = profile
                profile_validated = True
                try:
                    write_batch_json(result_file, raw)
                except Exception:
                    artifact_write_failed = True
                    raise
                writeback_attempted = write_back
                _persist_writeback(account_id, json.dumps(profile), "enriched")
                emit("    synthesized + schema validated ✓")
            else:
                try:
                    write_batch_json(result_file, raw)
                except Exception:
                    artifact_write_failed = True
                    raise
                writeback_attempted = write_back
                _persist_writeback(account_id, json.dumps(raw["search_results"]), "enriched")

            _remember_artifact(account_id, raw)

            return {
                "account_id": account_id,
                "status": status,
                "tools_failed": tools_failed,
                "output_file": str(result_file),
                "profile_validated": profile_validated,
            }

        except Exception as e:
            safe_error = sanitize_error(e)
            if raw is not None and not artifact_write_failed:
                raw["synthesis_error" if options.synthesize else "processing_error"] = safe_error
                try:
                    write_batch_json(result_file, raw)
                except Exception as persist_exc:
                    artifact_write_failed = True
                    safe_error = f"{safe_error}; artifact persistence failed: {sanitize_error(persist_exc)}"
                else:
                    _remember_artifact(account_id, raw)
            if write_back and not writeback_attempted:
                try:
                    _persist_writeback(
                        account_id,
                        json.dumps(raw.get("search_results", {})) if raw is not None else "{}",
                        "failed",
                    )
                except Exception as writeback_exc:
                    safe_error = f"{safe_error}; write-back failed: {sanitize_error(writeback_exc)}"
            print(f"  [{account_id}] ERROR: {safe_error}", file=sys.stderr)
            return {
                "account_id": account_id,
                "status": "failed",
                "tools_failed": raw.get("tools_failed", []) if raw is not None else [],
                "output_file": str(result_file),
                "profile_validated": False,
                "error": safe_error,
            }

    def _process(account: dict) -> dict:
        token = _ACTIVE_API_KEY_ENV.set(options.api_key_env)
        try:
            return _process_account(account)
        finally:
            _ACTIVE_API_KEY_ENV.reset(token)

    results: list[dict] = []
    if options.concurrency == 1:
        for account in accounts:
            results.append(_process(account))
    else:
        with ThreadPoolExecutor(max_workers=options.concurrency) as pool:
            futures = {pool.submit(_process, a): a for a in accounts}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    account = futures[future]
                    safe_error = sanitize_error(e, options.api_key_env)
                    print(f"  [{account['id']}] UNHANDLED: {safe_error}", file=sys.stderr)
                    results.append(
                        {
                            "account_id": account["id"],
                            "status": "failed",
                            "tools_failed": [],
                            "profile_validated": False,
                            "error": safe_error,
                        }
                    )

    # -- Summary --
    order = {account["id"]: index for index, account in enumerate(accounts)}
    results.sort(key=lambda result: order[result["account_id"]])
    from open_pharma_plugins_hcp_intelligence import batch_csv

    summary_path = output_dir / batch_csv.SUMMARY_FILENAME
    csv_failed = False
    try:
        rows = batch_csv.build_summary_rows(accounts, results, validated_artifacts)
        summary_csv = batch_csv.write_summary_csv(summary_path, rows)
    except Exception as exc:
        csv_failed = True
        summary_csv = {
            "status": "failed",
            "path": str(summary_path.resolve()),
            "schema_version": batch_csv.CSV_SCHEMA_VERSION,
            "error": f"CSV export failed ({type(exc).__name__})",
        }
    completed_at = datetime.now(timezone.utc).isoformat()
    manifest = build_batch_manifest(
        input_path=plan.input_path,
        input_sha256=plan.input_sha256,
        selected_count=len(accounts),
        synthesis=options.synthesize,
        model=options.model,
        base_url=options.base_url,
        reasoning_effort=options.reasoning_effort,
        synthesis_timeout_seconds=options.synthesis_timeout_seconds,
        concurrency=options.concurrency,
        results=results,
        summary_csv=summary_csv,
        started_at=started_at,
        completed_at=completed_at,
    )
    write_batch_json(output_dir / "batch_manifest.json", manifest)
    summary = manifest["summary"]
    exit_code = 1 if csv_failed or summary["failed"] or summary["partial"] else 0
    return BatchOutcome(
        output_dir=output_dir,
        results=tuple(results),
        manifest=manifest,
        exit_code=exit_code,
    )
