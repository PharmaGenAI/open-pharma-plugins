"""Immutable Competitive Intelligence evidence-run orchestration."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from helpers.ci_http import parse_text_block

from open_pharma_plugins_competitive_intelligence import _runs
from open_pharma_plugins_competitive_intelligence._runs import (
    ProviderBundle,
    RunIntegrityError,
    create_run,
    load_run,
)
from open_pharma_plugins_competitive_intelligence.models import (
    CacheProvenance,
    CoverageStatus,
    RefreshRequest,
    RegulatorySearchResult,
    SourceName,
    SourceResult,
    TrackedEntity,
)
from open_pharma_plugins_competitive_intelligence.tools import ci_refresh, ci_track

NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def _source(source: SourceName, query: str, *, status=CoverageStatus.COMPLETE) -> SourceResult:
    endpoint = {
        SourceName.CLINICAL_TRIALS: "https://clinicaltrials.gov/api/v2/studies",
        SourceName.OPENFDA: "https://api.fda.gov/drug/drugsfda.json",
        SourceName.DAILYMED: "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json",
        SourceName.PUBMED: "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        SourceName.WEB: "https://api.exa.ai/search",
    }[source]
    error = (
        {"code": "not_configured", "message": "fixture provider is not configured"}
        if status == CoverageStatus.NOT_CONFIGURED
        else None
    )
    return SourceResult(
        source=source,
        provider="fixture",
        status=status,
        query=query,
        source_url=endpoint,
        retrieved_at=NOW,
        cache=CacheProvenance(status="disabled"),
        records=[] if status != CoverageStatus.COMPLETE else [{"name": query}],
        total_available=None if status == CoverageStatus.NOT_CONFIGURED else 1,
        error=error,
        limitations=["fixture limitation"] if error else [],
    )


class CountingProviders:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, datetime]] = []

    @property
    def bundle(self) -> ProviderBundle:
        return ProviderBundle(
            trials=self.trials,
            regulatory=self.regulatory,
            news=self.news,
            publications=self.publications,
        )

    def trials(self, request, now):
        self.calls.append(("trials", request, now))
        return _source(SourceName.CLINICAL_TRIALS, request.query)

    def regulatory(self, request, now):
        self.calls.append(("regulatory", request, now))
        return RegulatorySearchResult(
            drug_name=request.drug_name,
            coverage="complete",
            openfda=_source(SourceName.OPENFDA, request.drug_name),
            dailymed=_source(SourceName.DAILYMED, request.drug_name),
        )

    def news(self, request, now):
        self.calls.append(("news", request, now))
        return _source(SourceName.WEB, request.query, status=CoverageStatus.NOT_CONFIGURED)

    def publications(self, request, now):
        self.calls.append(("publications", request, now))
        return _source(SourceName.PUBMED, request.query)


@pytest.fixture
def created_run():
    providers = CountingProviders()
    return create_run(
        RefreshRequest(
            entities=[
                TrackedEntity(
                    entity_type="drug",
                    name="ExampleDrug",
                    aliases=["examplemab"],
                )
            ]
        ),
        providers=providers.bundle,
        now=NOW,
    )


def test_create_run_collects_once_and_preserves_source_status():
    providers = CountingProviders()
    run = create_run(
        RefreshRequest(
            entities=[
                TrackedEntity(
                    entity_type="drug",
                    name="ExampleDrug",
                    aliases=["examplemab", "EXAMPLEMAB"],
                )
            ]
        ),
        providers=providers.bundle,
        now=NOW,
    )

    assert run.run_id.startswith("ci_20260828T000000Z_")
    assert [call[0] for call in providers.calls] == [
        "trials",
        "regulatory",
        "news",
        "publications",
    ]
    assert all(call[2] is NOW for call in providers.calls)
    assert '"ExampleDrug" OR "examplemab"' in providers.calls[0][1].query
    assert run.entities[0].sources["web_search"].status == CoverageStatus.NOT_CONFIGURED
    assert Path(run.manifest_path).is_file()
    assert Path(run.records_path).is_file()


def test_company_regulatory_is_not_applicable_without_provider_call():
    providers = CountingProviders()
    run = create_run(
        RefreshRequest(
            entities=[TrackedEntity(entity_type="company", name="Example Pharma")],
            include_sections=["regulatory"],
        ),
        providers=providers.bundle,
        now=NOW,
    )

    assert providers.calls == []
    sources = run.entities[0].sources
    assert sources["openfda"].status == CoverageStatus.NOT_APPLICABLE
    assert sources["dailymed"].status == CoverageStatus.NOT_APPLICABLE


def test_run_files_are_private_and_manifest_hash_loads(created_run):
    loaded = load_run(created_run.run_id)

    assert loaded.records_sha256 == created_run.records_sha256
    assert loaded.entities[0].entity.name == "ExampleDrug"
    if os.name != "nt":
        assert Path(created_run.manifest_path).stat().st_mode & 0o777 == 0o600
        assert Path(created_run.records_path).stat().st_mode & 0o777 == 0o600
        assert Path(created_run.records_path).parent.stat().st_mode & 0o777 == 0o700


def test_load_run_rejects_records_hash_mismatch(created_run):
    Path(created_run.records_path).write_text('{"tampered": true}')

    with pytest.raises(RunIntegrityError, match="records hash mismatch"):
        load_run(created_run.run_id)


@pytest.mark.parametrize("run_id", ["../outside", "..", "/tmp/outside", "a/b", "a\\b"])
def test_load_run_rejects_path_control(run_id):
    with pytest.raises(RunIntegrityError, match="invalid run id"):
        load_run(run_id)


def test_create_run_rejects_naive_clock():
    with pytest.raises(ValueError, match="timezone-aware"):
        create_run(
            RefreshRequest(entities=[TrackedEntity(entity_type="drug", name="ExampleDrug")]),
            providers=CountingProviders().bundle,
            now=datetime(2026, 8, 28),
        )


def test_run_id_collision_retries_without_recollecting(monkeypatch):
    from open_pharma_plugins_competitive_intelligence._watchlist import runs_dir

    (runs_dir() / "ci_20260828T000000Z_11111111").mkdir()
    tokens = iter(["11111111", "22222222"])
    monkeypatch.setattr(_runs.secrets, "token_hex", lambda _size: next(tokens))
    providers = CountingProviders()

    run = create_run(
        RefreshRequest(entities=[TrackedEntity(entity_type="drug", name="ExampleDrug")]),
        providers=providers.bundle,
        now=NOW,
    )

    assert run.run_id == "ci_20260828T000000Z_22222222"
    assert len(providers.calls) == 4


def test_watchlist_load_preserves_legacy_fields(monkeypatch, tmp_path):
    data_dir = tmp_path / "ci-data"
    data_dir.mkdir()
    monkeypatch.setenv("OPEN_PHARMA_CI_DATA_DIR", str(data_dir))
    (data_dir / "watchlist.json").write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "entity_type": "drug",
                        "name": "ExampleDrug",
                        "therapeutic_area": "oncology",
                        "aliases": ["examplemab"],
                        "added_at": "2026-08-01T00:00:00Z",
                    }
                ]
            }
        )
    )

    from open_pharma_plugins_competitive_intelligence._watchlist import load_tracked_entities

    entity = load_tracked_entities()[0]
    assert entity.aliases == ["examplemab"]
    assert entity.added_at == datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_malformed_watchlist_returns_tool_error(monkeypatch, tmp_path):
    data_dir = tmp_path / "ci-data"
    data_dir.mkdir()
    monkeypatch.setenv("OPEN_PHARMA_CI_DATA_DIR", str(data_dir))
    (data_dir / "watchlist.json").write_text('{"entities": [{"entity_type": "organization", "name": "Bad"}]}')

    payload = parse_text_block(ci_track.handle({"action": "list"}))

    assert payload["error"]["code"] == "invalid_watchlist"


def test_refresh_rejects_unknown_selection_before_collection(monkeypatch):
    from open_pharma_plugins_competitive_intelligence._watchlist import save_watchlist

    save_watchlist([{"entity_type": "drug", "name": "KnownDrug"}])
    monkeypatch.setattr(
        ci_refresh,
        "create_run",
        lambda *_args, **_kwargs: pytest.fail("collection started before selection validation"),
    )

    payload = parse_text_block(ci_refresh.handle({"entities": ["KnownDrug", "UnknownDrug"]}))

    assert payload["error"]["code"] == "unknown_entity"
    assert payload["error"]["entities"] == ["UnknownDrug"]


def test_refresh_response_omits_raw_records(monkeypatch):
    from open_pharma_plugins_competitive_intelligence._watchlist import save_watchlist

    save_watchlist([{"entity_type": "drug", "name": "ExampleDrug", "aliases": ["examplemab"]}])
    monkeypatch.setattr(_runs, "DEFAULT_PROVIDERS", CountingProviders().bundle)

    payload = parse_text_block(ci_refresh.handle({"entities": ["exampledrug"]}))

    assert payload["success"] is True
    assert payload["entities"] == ["ExampleDrug"]
    assert "records" not in payload
    assert Path(payload["manifest_path"]).is_file()
