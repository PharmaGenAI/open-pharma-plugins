"""Fixture-locked PubMed and web-search evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from helpers.ci_http import FixtureTransport, parse_text_block

from open_pharma_plugins_competitive_intelligence import _pubmed, _web_search
from open_pharma_plugins_competitive_intelligence._cache import cache_stats
from open_pharma_plugins_competitive_intelligence._transport import TransportError
from open_pharma_plugins_competitive_intelligence.models import (
    CoverageStatus,
    NewsSearchRequest,
    Publication,
    PublicationSearchRequest,
)
from open_pharma_plugins_competitive_intelligence.tools import ci_scan_news, ci_scan_publications

NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def test_pubmed_preserves_nested_title_and_labeled_abstract(fixtures):
    transport = FixtureTransport.sequence(
        fixtures.bytes("pubmed_search.json"),
        fixtures.bytes("pubmed_fetch.xml"),
    )

    result = _pubmed.search_publications(
        PublicationSearchRequest(query="examplemab", days_back=365, max_results=10),
        transport=transport,
        now=NOW,
    )

    publication = Publication.model_validate(result.records[0])
    assert publication.title == "Examplemab plus standard therapy in Phase 3"
    assert "BACKGROUND:" in publication.abstract_excerpt
    assert "RESULTS:" in publication.abstract_excerpt
    assert publication.source_url == f"https://pubmed.ncbi.nlm.nih.gov/{publication.pmid}/"
    assert publication.pub_date == "2026-08-15"
    search_query = parse_qs(urlsplit(transport.calls[0].url).query)
    assert search_query["term"] == ["examplemab AND clinical trial[pt]"]
    assert search_query["reldate"] == ["365"]


def test_pubmed_zero_ids_is_complete_and_skips_fetch():
    request = PublicationSearchRequest(query="nothing")
    transport = FixtureTransport.json(
        {"esearchresult": {"count": "0", "idlist": []}},
        url=_pubmed.build_search_url(request, api_key=""),
    )

    result = _pubmed.search_publications(request, transport=transport, now=NOW)

    assert result.status == CoverageStatus.COMPLETE
    assert result.records == []
    assert len(transport.calls) == 1


def test_pubmed_fetch_failure_is_failed_not_zero(fixtures):
    transport = FixtureTransport.sequence(
        fixtures.bytes("pubmed_search.json"),
        TransportError("timeout", "provider request timed out"),
    )

    result = _pubmed.search_publications(
        PublicationSearchRequest(query="examplemab"),
        transport=transport,
        now=NOW,
    )

    assert result.status == CoverageStatus.FAILED
    assert result.records == []
    assert result.error.code == "fetch_failed"


def test_pubmed_search_failure_is_failed_not_zero():
    transport = FixtureTransport([(None, TransportError("timeout", "provider request timed out"))])

    result = _pubmed.search_publications(PublicationSearchRequest(query="examplemab"), transport=transport, now=NOW)

    assert result.status == CoverageStatus.FAILED
    assert result.error.code == "timeout"


def test_pubmed_invalid_search_is_not_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_PHARMA_CI_DATA_DIR", str(tmp_path / "ci-data"))
    result = _pubmed.search_publications(
        PublicationSearchRequest(query="examplemab"),
        transport=FixtureTransport.json(
            {"esearchresult": {"count": "1", "idlist": "not-a-list"}},
            url=_pubmed.build_search_url(PublicationSearchRequest(query="examplemab"), api_key=""),
        ),
        now=NOW,
    )

    assert result.status == CoverageStatus.FAILED
    assert cache_stats()["entry_count"] == 0


def test_pubmed_api_key_is_transport_only(monkeypatch, fixtures, tmp_path):
    sentinel = "NCBI_SECRET_SENTINEL"
    monkeypatch.setenv("NCBI_API_KEY", sentinel)
    monkeypatch.setenv("OPEN_PHARMA_CI_DATA_DIR", str(tmp_path / "ci-data"))

    result = _pubmed.search_publications(
        PublicationSearchRequest(query="examplemab"),
        transport=FixtureTransport.sequence(fixtures.bytes("pubmed_search.json"), fixtures.bytes("pubmed_fetch.xml")),
        now=NOW,
    )

    assert result.status == CoverageStatus.COMPLETE
    assert sentinel not in result.model_dump_json()
    persisted = "".join(path.read_text(errors="ignore") for path in tmp_path.rglob("*") if path.is_file())
    assert sentinel not in persisted


def test_pubmed_malformed_xml_is_failed(fixtures):
    transport = FixtureTransport.sequence(fixtures.bytes("pubmed_search.json"), b"<broken")
    result = _pubmed.search_publications(PublicationSearchRequest(query="examplemab"), transport=transport, now=NOW)
    assert result.status == CoverageStatus.FAILED
    assert result.error.code == "schema_mismatch"


def test_pubmed_empty_fetch_xml_is_failed(fixtures):
    transport = FixtureTransport.sequence(fixtures.bytes("pubmed_search.json"), b"<PubmedArticleSet />")
    result = _pubmed.search_publications(PublicationSearchRequest(query="examplemab"), transport=transport, now=NOW)
    assert result.status == CoverageStatus.FAILED
    assert result.error.code == "schema_mismatch"


def test_exa_applies_days_back_as_start_published_date(monkeypatch, fixtures, tmp_path):
    sentinel = "EXA_SECRET_SENTINEL"
    monkeypatch.setenv("OPEN_PHARMA_SEARCH_BACKEND", "exa")
    monkeypatch.setenv("EXA_API_KEY", sentinel)
    monkeypatch.setenv("OPEN_PHARMA_CI_DATA_DIR", str(tmp_path / "ci-data"))
    transport = FixtureTransport.json_file(fixtures.path("exa_search.json"))

    result = _web_search.search_news(
        NewsSearchRequest(query="ExampleDrug", days_back=30, max_results=5),
        transport=transport,
        now=NOW,
    )

    assert transport.calls[0].json_body["startPublishedDate"] == "2026-07-29T00:00:00Z"
    assert result.status == CoverageStatus.COMPLETE
    assert result.provider == "exa"
    assert sentinel not in result.model_dump_json()
    persisted = "".join(path.read_text(errors="ignore") for path in tmp_path.rglob("*") if path.is_file())
    assert sentinel not in persisted


def test_auto_selects_tavily_and_records_coarse_window(monkeypatch, fixtures):
    monkeypatch.setenv("OPEN_PHARMA_SEARCH_BACKEND", "auto")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "TAVILY_SENTINEL")
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    transport = FixtureTransport.json_file(fixtures.path("tavily_search.json"))

    result = _web_search.search_news(
        NewsSearchRequest(query="ExampleDrug", days_back=14),
        transport=transport,
        now=NOW,
    )

    assert result.status == CoverageStatus.COMPLETE
    assert result.provider == "tavily"
    assert transport.calls[0].json_body["time_range"] == "month"
    assert any("coarse recency bucket" in limitation for limitation in result.limitations)


def test_explicit_unconfigured_backend_is_not_cached(monkeypatch):
    monkeypatch.setenv("OPEN_PHARMA_SEARCH_BACKEND", "serper")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)

    result = _web_search.search_news(NewsSearchRequest(query="ExampleDrug"), now=NOW)

    assert result.status == CoverageStatus.NOT_CONFIGURED
    assert cache_stats()["entry_count"] == 0


def test_web_transport_failure_is_failed_not_empty(monkeypatch):
    monkeypatch.setenv("OPEN_PHARMA_SEARCH_BACKEND", "serper")
    monkeypatch.setenv("SERPER_API_KEY", "SENTINEL")
    transport = FixtureTransport([(None, TransportError("timeout", "provider request timed out"))])

    result = _web_search.search_news(NewsSearchRequest(query="ExampleDrug"), transport=transport, now=NOW)

    assert result.status == CoverageStatus.FAILED
    assert result.records == []


def test_web_valid_empty_response_is_complete(monkeypatch):
    monkeypatch.setenv("OPEN_PHARMA_SEARCH_BACKEND", "serper")
    monkeypatch.setenv("SERPER_API_KEY", "SENTINEL")
    transport = FixtureTransport.json({}, url=_web_search.SERPER_ENDPOINT)

    result = _web_search.search_news(NewsSearchRequest(query="ExampleDrug"), transport=transport, now=NOW)

    assert result.status == CoverageStatus.COMPLETE
    assert result.records == []


def test_invalid_web_url_is_dropped_with_partial_coverage(monkeypatch, fixtures):
    monkeypatch.setenv("OPEN_PHARMA_SEARCH_BACKEND", "serper")
    monkeypatch.setenv("SERPER_API_KEY", "SENTINEL")

    result = _web_search.search_news(
        NewsSearchRequest(query="ExampleDrug"),
        transport=FixtureTransport.json_file(fixtures.path("serper_search.json")),
        now=NOW,
    )

    assert result.status == CoverageStatus.PARTIAL
    assert len(result.records) == 1
    assert all(record["url"].startswith("https://") for record in result.records)
    assert any("dropped" in limitation.lower() for limitation in result.limitations)


def test_publication_and_news_handlers_preserve_keys_and_add_coverage(monkeypatch, fixtures):
    monkeypatch.setattr(
        _pubmed,
        "DEFAULT_TRANSPORT",
        FixtureTransport.sequence(fixtures.bytes("pubmed_search.json"), fixtures.bytes("pubmed_fetch.xml")),
    )
    publications = parse_text_block(ci_scan_publications.handle({"query": "examplemab"}))
    assert publications["returned"] == 2
    assert publications["coverage"] == "complete"

    monkeypatch.setenv("OPEN_PHARMA_SEARCH_BACKEND", "exa")
    monkeypatch.setenv("EXA_API_KEY", "SENTINEL")
    monkeypatch.setattr(
        _web_search,
        "DEFAULT_TRANSPORT",
        FixtureTransport.json_file(fixtures.path("exa_search.json")),
    )
    news = parse_text_block(ci_scan_news.handle({"query": "ExampleDrug"}))
    assert news["total_results"] == 1
    assert news["coverage"] == "complete"
    assert "source_ledger" in news
