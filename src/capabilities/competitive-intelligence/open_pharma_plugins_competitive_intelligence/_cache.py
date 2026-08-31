"""Schema-versioned, credential-free response cache for CI provider calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from shared.filesystem import atomic_write_json, contained_path, ensure_private_dir, sanitize_mapping, sanitize_url

from .models import CacheStatus

_SCHEMA_VERSION = 2
_DEFAULT_TTL_HOURS = 24


@dataclass(frozen=True)
class CacheLookup:
    status: CacheStatus
    payload: Any | None
    cached_at: datetime | None


def _cache_dir() -> Path:
    from shared.env import get_env

    root = ensure_private_dir(
        get_env(
            "OPEN_PHARMA_CI_DATA_DIR",
            str(Path.home() / ".open-pharma-plugins" / "competitive-intelligence"),
        )
    )
    return ensure_private_dir(contained_path(root, "cache"))


def _ttl_hours() -> int:
    try:
        from shared.env import get_env

        value = get_env("CI_CACHE_TTL_HOURS", str(_DEFAULT_TTL_HOURS))
        parsed = int(value) if value else _DEFAULT_TTL_HOURS
        return parsed if parsed >= 0 else _DEFAULT_TTL_HOURS
    except (TypeError, ValueError):
        return _DEFAULT_TTL_HOURS


def _cache_key(namespace: str, params: Mapping[str, Any] | None = None) -> str:
    clean_namespace = _sanitize_namespace(namespace)
    clean_params = _sanitize_value(params or {})
    raw = clean_namespace + "|" + json.dumps(clean_params, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def cache_lookup(namespace: str, params: Mapping[str, Any] | None = None) -> CacheLookup:
    ttl_hours = _ttl_hours()
    if ttl_hours == 0:
        return CacheLookup(status=CacheStatus.DISABLED, payload=None, cached_at=None)
    key = _cache_key(namespace, params)
    path = contained_path(_cache_dir(), f"{key}.json")
    if not path.exists():
        return CacheLookup(status=CacheStatus.MISS, payload=None, cached_at=None)
    try:
        data = json.loads(path.read_text())
        if data.get("schema_version") != _SCHEMA_VERSION:
            return CacheLookup(status=CacheStatus.MISS, payload=None, cached_at=None)
        cached_at = datetime.fromisoformat(str(data["cached_at"]).replace("Z", "+00:00"))
        if cached_at.tzinfo is None:
            raise ValueError("cache timestamp must include a timezone")
        cached_at = cached_at.astimezone(timezone.utc)
        age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
        if age_hours > ttl_hours:
            return CacheLookup(status=CacheStatus.MISS, payload=None, cached_at=None)
        return CacheLookup(status=CacheStatus.HIT, payload=data.get("payload"), cached_at=cached_at)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return CacheLookup(status=CacheStatus.MISS, payload=None, cached_at=None)


def cache_store(namespace: str, params: Mapping[str, Any] | None, payload: Any) -> None:
    if _ttl_hours() == 0:
        return
    clean_namespace = _sanitize_namespace(namespace)
    clean_params = _sanitize_value(params or {})
    key = _cache_key(clean_namespace, clean_params)
    path = contained_path(_cache_dir(), f"{key}.json")
    data = {
        "schema_version": _SCHEMA_VERSION,
        "cached_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "namespace": clean_namespace,
        "params": clean_params,
        "payload": _sanitize_value(payload),
    }
    atomic_write_json(path, data)


def cache_stats() -> dict[str, Any]:
    d = _cache_dir()
    files = list(d.glob("*.json"))
    total_bytes = sum(f.stat().st_size for f in files)
    recognized = 0
    for path in files:
        try:
            if json.loads(path.read_text()).get("schema_version") == _SCHEMA_VERSION:
                recognized += 1
        except (json.JSONDecodeError, OSError, AttributeError):
            pass
    return {
        "cache_dir": str(d),
        "schema_version": _SCHEMA_VERSION,
        "entry_count": recognized,
        "ignored_entry_count": len(files) - recognized,
        "total_bytes": total_bytes,
    }


def cache_clear() -> int:
    d = _cache_dir()
    removed = 0
    for path in d.glob("*.json"):
        try:
            if json.loads(path.read_text()).get("schema_version") != _SCHEMA_VERSION:
                continue
        except (json.JSONDecodeError, OSError, AttributeError):
            continue
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def _sanitize_namespace(namespace: str) -> str:
    return sanitize_url(namespace) if "://" in namespace else namespace


def _sanitize_value(value: Any) -> Any:
    sanitized = sanitize_mapping(value)
    if isinstance(sanitized, dict):
        return {key: _sanitize_value(item) for key, item in sanitized.items()}
    if isinstance(sanitized, list):
        return [_sanitize_value(item) for item in sanitized]
    if isinstance(sanitized, str) and "://" in sanitized:
        return sanitize_url(sanitized)
    return sanitized
