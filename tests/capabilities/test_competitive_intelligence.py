"""Tests for the competitive-intelligence capability."""

import json

import open_pharma_plugins_competitive_intelligence as ci

# ---------------------------------------------------------------------------
# Package sanity
# ---------------------------------------------------------------------------


def test_lists_twelve_tools():
    names = {t["name"] for t in ci.list_tools()}
    assert names == {
        "ci_status",
        "ci_track",
        "ci_scan_trials",
        "ci_trial_detail",
        "ci_scan_regulatory",
        "ci_scan_news",
        "ci_scan_publications",
        "ci_extract_events",
        "ci_landscape",
        "ci_report",
        "ci_timeline",
        "ci_refresh",
    }


def test_version():
    assert ci.__version__ == "1.1.0"


# ---------------------------------------------------------------------------
# Watchlist (ci_track)
# ---------------------------------------------------------------------------


def test_track_add_and_list():
    ci.get_handler("ci_track")({"action": "remove", "name": "TestDrug"})
    result = ci.get_handler("ci_track")(
        {
            "action": "add",
            "entity_type": "drug",
            "name": "TestDrug",
            "therapeutic_area": "oncology",
        }
    )
    data = json.loads(result[0]["text"])
    assert data["action"] == "added"
    assert any(e["name"] == "TestDrug" for e in data["watchlist"])


def test_track_remove():
    ci.get_handler("ci_track")(
        {
            "action": "add",
            "entity_type": "drug",
            "name": "ToRemove",
        }
    )
    result = ci.get_handler("ci_track")({"action": "remove", "name": "ToRemove"})
    data = json.loads(result[0]["text"])
    assert not any(e["name"] == "ToRemove" for e in data["watchlist"])


def test_track_list():
    result = ci.get_handler("ci_track")({"action": "list"})
    data = json.loads(result[0]["text"])
    assert "watchlist" in data
    assert isinstance(data["watchlist"], list)


def test_track_add_requires_entity_type():
    result = ci.get_handler("ci_track")({"action": "add", "name": "X"})
    data = json.loads(result[0]["text"])
    assert "error" in data


def test_track_add_with_aliases():
    ci.get_handler("ci_track")({"action": "remove", "name": "AliasDrug"})
    result = ci.get_handler("ci_track")(
        {
            "action": "add",
            "entity_type": "drug",
            "name": "AliasDrug",
            "aliases": ["alias1", "alias2"],
        }
    )
    data = json.loads(result[0]["text"])
    entry = next(e for e in data["watchlist"] if e["name"] == "AliasDrug")
    assert "alias1" in entry["aliases"]


# ---------------------------------------------------------------------------
# Scan tools (offline-safe — these may fail without network, but must not crash)
# ---------------------------------------------------------------------------


def test_scan_trials_handler_exists():
    handler = ci.get_handler("ci_scan_trials")
    assert callable(handler)


def test_scan_regulatory_handler_exists():
    handler = ci.get_handler("ci_scan_regulatory")
    assert callable(handler)


def test_scan_news_handler_exists():
    handler = ci.get_handler("ci_scan_news")
    assert callable(handler)


def test_scan_publications_handler_exists():
    handler = ci.get_handler("ci_scan_publications")
    assert callable(handler)


# ---------------------------------------------------------------------------
# New tools: trial_detail, landscape, extract_events, status
# ---------------------------------------------------------------------------


def test_trial_detail_handler_exists():
    handler = ci.get_handler("ci_trial_detail")
    assert callable(handler)


def test_landscape_handler_exists():
    handler = ci.get_handler("ci_landscape")
    assert callable(handler)


def test_extract_events_missing_file():
    result = ci.get_handler("ci_extract_events")({"file_path": "/nonexistent/file.txt"})
    data = json.loads(result[0]["text"])
    assert "error" in data


def test_extract_events_from_text(tmp_path):
    doc = tmp_path / "test_ci.txt"
    doc.write_text(
        "On January 15, 2026, Pfizer announced that their Phase 3 trial met "
        "its primary endpoint of overall survival in NSCLC patients.\n"
        "Merck filed an sNDA to FDA for a new indication in melanoma."
    )
    result = ci.get_handler("ci_extract_events")({"file_path": str(doc)})
    data = json.loads(result[0]["text"])
    assert data["total_events"] >= 1
    assert data["pages_processed"] == 1
    competitors = {e["competitor"] for e in data["events"]}
    assert "Pfizer" in competitors or "Merck" in competitors


def test_status_tool():
    result = ci.get_handler("ci_status")({})
    data = json.loads(result[0]["text"])
    assert "config" in data
    assert "data_sources" in data
    assert data["data_sources"]["openfda"]["rate_limit"] == (
        "240/min; 1,000/day per IP without key or 120,000/day per key"
    )
    assert "cache" in data
    assert "watchlist" in data


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_store_lookup():
    from open_pharma_plugins_competitive_intelligence._cache import (
        cache_lookup,
        cache_stats,
        cache_store,
    )

    cache_store("test", {"q": "test"}, {"result": "ok"})
    cached = cache_lookup("test", {"q": "test"})
    assert cached.payload is not None
    assert cached.payload["result"] == "ok"
    stats = cache_stats()
    assert stats["entry_count"] >= 1


def test_cache_never_persists_credentials(tmp_path, monkeypatch):
    from open_pharma_plugins_competitive_intelligence._cache import cache_store

    sentinel = "SECRET_OPENFDA_SENTINEL"
    data_dir = tmp_path / "ci-data"
    monkeypatch.setenv("OPEN_PHARMA_CI_DATA_DIR", str(data_dir))
    cache_store(
        f"https://api.fda.gov/drug/drugsfda.json?api_key={sentinel}&search=test",
        {"search": "test", "api_key": sentinel},
        {"result": "ok"},
    )

    files = list(data_dir.rglob("*"))
    assert files
    persisted = "".join(path.read_text(errors="ignore") for path in files if path.is_file())
    assert sentinel not in persisted


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_models_import():
    from open_pharma_plugins_competitive_intelligence.models import CIEvent, Trial

    t = Trial(
        nct_id="NCT001",
        title="Test",
        sponsor="Acme",
        phase="PHASE3",
        status="RECRUITING",
        source_url="https://clinicaltrials.gov/study/NCT001",
    )
    assert t.nct_id == "NCT001"
    e = CIEvent(event_type="approval", competitor="Pfizer", description="Test approval")
    assert e.confidence == "medium"


# ---------------------------------------------------------------------------
# Report and timeline
# ---------------------------------------------------------------------------


def test_report_no_entities():
    from open_pharma_plugins_competitive_intelligence._watchlist import (
        load_watchlist,
        save_watchlist,
    )

    backup = load_watchlist()
    save_watchlist([])
    result = ci.get_handler("ci_report")({"focus": None})
    data = json.loads(result[0]["text"])
    assert "error" in data
    save_watchlist(backup)


def test_timeline_no_entities():
    from open_pharma_plugins_competitive_intelligence._watchlist import (
        load_watchlist,
        save_watchlist,
    )

    backup = load_watchlist()
    save_watchlist([])
    result = ci.get_handler("ci_timeline")({"entities": None})
    data = json.loads(result[0]["text"])
    assert "error" in data
    save_watchlist(backup)
