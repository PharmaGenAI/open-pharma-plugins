"""Private, non-overwriting artifact primitives for CI run projections."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.filesystem import (
    atomic_write_bytes,
    atomic_write_text,
    contained_path,
    ensure_private_dir,
    validate_component,
)

from ._watchlist import reports_dir
from .models import ArtifactManifest, ArtifactRecord

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_MAX_ATTEMPTS = 3


def sanitize_display_stem(value: str | None, *, default: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]", "_", value or "")
    clean = clean[:100]
    return clean if clean not in {"", ".", ".."} else default


def safe_csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    candidate = text.lstrip(" \u00a0")
    return f"'{text}" if candidate.startswith(_FORMULA_PREFIXES) else text


def create_artifact_dir(run_id: str, *, now: datetime | None = None) -> Path:
    validate_component(run_id, label="run id")
    generated_at = _utc(now)
    run_root = ensure_private_dir(contained_path(reports_dir(), run_id))
    for _attempt in range(_MAX_ATTEMPTS):
        artifact_id = f"artifact_{generated_at.strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(4)}"
        candidate = contained_path(run_root, artifact_id)
        try:
            candidate.mkdir(mode=0o700, exist_ok=False)
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError("could not allocate a unique artifact directory after three attempts")


def write_artifact(
    output_dir: Path,
    relative_path: str,
    content: str | bytes,
    *,
    media_type: str,
) -> ArtifactRecord:
    validate_component(relative_path, label="artifact path")
    path = contained_path(output_dir, relative_path)
    payload = content.encode("utf-8") if isinstance(content, str) else content
    atomic_write_bytes(path, payload)
    return ArtifactRecord(
        relative_path=relative_path,
        media_type=media_type,
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def write_manifest(output_dir: Path, manifest: ArtifactManifest) -> Path:
    path = contained_path(output_dir, "manifest.json")
    atomic_write_text(
        path,
        json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False),
    )
    return path


def _utc(value: datetime | None) -> datetime:
    now = value or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)
