"""Environment access for the shared library and every capability.

Import `get_env` (call-time accessor for any env var) instead of calling os.getenv across the
codebase, so this stays the one place that reads the environment. Capability-private env vars
keep their own defaults in the capability that owns them.
"""

from __future__ import annotations

import os
from collections.abc import Iterable


def get_env(name: str, default: str | None = None) -> str | None:
    """Env config, read at CALL time. Precedence: environment > user config file > default."""
    val = os.environ.get(name)
    return val if val is not None else _config().get(name, default)


# ── User config file (~/.open-pharma-plugins/config): KEY=VALUE lines, read when a var isn't in the
# environment. Location is fixed (not per-OS like cache_dir): "where is the config" can't live in
# the config, and pointing at it via env var would reintroduce the inheritance problem it solves. ──


def config_dir() -> str:
    """Fixed config dir (~/.open-pharma-plugins), overridable via OPEN_PHARMA_CONFIG_DIR."""
    return os.path.expanduser(os.environ.get("OPEN_PHARMA_CONFIG_DIR") or "~/.open-pharma-plugins")


def config_file() -> str:
    """Config file path, overridable via OPEN_PHARMA_CONFIG (full path)."""
    override = os.environ.get("OPEN_PHARMA_CONFIG")
    return os.path.expanduser(override) if override else os.path.join(config_dir(), "config")


def _parse_config(text: str) -> dict[str, str]:
    """Minimal dotenv parse: KEY=VALUE per line; skip blank/# lines; strip `export ` and quotes.
    Stdlib-only on purpose — the package floor is 3.10, so tomllib (3.11+) isn't guaranteed."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip().removeprefix("export ").lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        if key.strip():
            out[key.strip()] = val
    return out


_config_cache: dict[str, str] | None = None


def _config() -> dict[str, str]:
    """Parsed config, loaded once and cached (empty if missing/unreadable)."""
    global _config_cache
    if _config_cache is None:
        try:
            with open(config_file(), encoding="utf-8") as f:
                _config_cache = _parse_config(f.read())
        except (OSError, UnicodeDecodeError):
            _config_cache = {}
    return _config_cache


_CONFIG_HEADER = "# open-pharma-plugins config — KEY=VALUE per line, read when the var isn't in the environment.\n\n"


def _write_config(path: str, merged: dict[str, str]) -> None:
    """Atomically write `merged` as a sorted dotenv file (0600) and invalidate the cache."""
    global _config_cache
    from shared.filesystem import atomic_write_text, ensure_private_dir

    ensure_private_dir(os.path.dirname(path))
    content = _CONFIG_HEADER + "".join(f"{key}={merged[key]}\n" for key in sorted(merged))
    atomic_write_text(path, content)
    _config_cache = None


def set_config(values: dict[str, str | None]) -> str:
    """Merge non-None `values` into the config file (atomic, 0600) and return its path."""
    path = config_file()
    try:
        with open(path, encoding="utf-8") as f:
            merged = _parse_config(f.read())
    except OSError:
        merged = {}
    merged.update({k: v for k, v in values.items() if v is not None})
    _write_config(path, merged)
    return path


def del_config(keys: Iterable[str]) -> str:
    """Remove `keys` from the config file (atomic, 0600) and return its path."""
    path = config_file()
    try:
        with open(path, encoding="utf-8") as f:
            merged = _parse_config(f.read())
    except OSError:
        return path
    present = [k for k in keys if k in merged]
    if not present:
        return path
    for k in present:
        del merged[k]
    _write_config(path, merged)
    return path


# ── Config-field catalog: the ONE declarative list of user-settable config vars, driving the
# interactive `--setup` (grouped) and documenting what belongs in the config file. install.sh mirrors
# this list (CONFIG_SPEC) for its own editor — keep the two in sync when adding a var. ──
CONFIG_FIELDS: list[tuple[str, bool, str, str, str]] = [
    # Web Search (used by hcp-intelligence and competitive-intelligence)
    (
        "OPEN_PHARMA_SEARCH_BACKEND",
        False,
        "Web Search",
        "auto",
        "text search backend (auto: serper > tavily > exa; or choose one)",
    ),
    ("SERPER_API_KEY", True, "Web Search", "", "Serper web search API key"),
    ("TAVILY_API_KEY", True, "Web Search", "", "Tavily web search API key"),
    ("EXA_API_KEY", True, "Web Search", "", "Exa web search API key"),
    # HCP Intelligence
    (
        "OPENROUTER_API_KEY",
        True,
        "HCP Intelligence",
        "",
        "OpenRouter API key for optional batch profile synthesis",
    ),
    (
        "OPENROUTER_BASE_URL",
        False,
        "HCP Intelligence",
        "https://openrouter.ai/api/v1",
        "OpenRouter-compatible API base URL for optional batch profile synthesis",
    ),
    (
        "NCBI_API_KEY",
        False,
        "HCP Intelligence",
        "",
        "NCBI E-utilities API key (optional; raises PubMed rate limit from 3 to 10 req/s)",
    ),
    (
        "OPEN_PHARMA_HCP_DATA_DIR",
        False,
        "HCP Intelligence",
        "",
        "mutable enrichment data directory (default: ~/.open-pharma-plugins/hcp-intelligence)",
    ),
    # Field Training
    (
        "OPEN_PHARMA_TRAINING_CONTENT_DIR",
        False,
        "Field Training",
        "",
        "content store directory for ingested training documents (default: ~/.open-pharma-plugins/training-content)",
    ),
    # Campaign Studio
    (
        "OPEN_PHARMA_CAMPAIGN_STORE_DIR",
        False,
        "Campaign Studio",
        "",
        "root directory for campaign briefs, claims, and rendered assets (default: ~/.open-pharma-plugins/campaign-studio)",
    ),
    # Next Best Engagement
    (
        "OPEN_PHARMA_NBE_OUTPUT_DIR",
        False,
        "Next Best Engagement",
        "",
        "directory for exported engagement plans (default: ~/.open-pharma-plugins/next-best-engagement)",
    ),
    # Territory Alignment
    (
        "OPEN_PHARMA_TA_DATA_DIR",
        False,
        "Territory Alignment",
        "",
        "directory containing hcps.csv, reps.csv, current_alignment.csv, constraints.csv (default: built-in fixtures)",
    ),
    (
        "OPEN_PHARMA_TA_SCENARIOS_DIR",
        False,
        "Territory Alignment",
        "",
        "directory for saved alignment scenarios (default: ~/.open-pharma-plugins/territory-alignment/scenarios)",
    ),
    # Competitive Intelligence
    (
        "OPEN_PHARMA_CI_DATA_DIR",
        False,
        "Competitive Intelligence",
        "",
        "watchlist, report, and cache directory (default: ~/.open-pharma-plugins/competitive-intelligence)",
    ),
    (
        "OPENFDA_API_KEY",
        False,
        "Competitive Intelligence",
        "",
        "openFDA API key (optional; raises daily quota from 1,000/IP to 120,000/key; 240 req/min either way)",
    ),
    (
        "CI_CACHE_TTL_HOURS",
        False,
        "Competitive Intelligence",
        "24",
        "hours before cached API responses expire (default: 24)",
    ),
]
