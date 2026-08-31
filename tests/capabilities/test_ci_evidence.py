"""Evidence, transport, and cache contracts for Competitive Intelligence."""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timezone

import pytest
from helpers.ci_http import FixtureTransport
from pydantic import ValidationError

from open_pharma_plugins_competitive_intelligence._cache import (
    cache_lookup,
    cache_stats,
    cache_store,
)
from open_pharma_plugins_competitive_intelligence._transport import (
    HttpRequest,
    TransportError,
    UrllibTransport,
)
from open_pharma_plugins_competitive_intelligence.models import (
    CacheProvenance,
    CacheStatus,
    CoverageStatus,
    SourceError,
    SourceName,
    SourceRequestEvidence,
    SourceResult,
    aggregate_cache_status,
    aggregate_coverage,
)

NOW = "2026-08-28T00:00:00Z"


def _result(**overrides) -> SourceResult:
    values = {
        "source": SourceName.PUBMED,
        "status": CoverageStatus.COMPLETE,
        "query": "examplemab AND clinical trial[pt]",
        "source_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        "retrieved_at": NOW,
        "cache": CacheProvenance(status=CacheStatus.MISS),
        "records": [],
    }
    values.update(overrides)
    return SourceResult(**values)


def test_source_result_distinguishes_failure_from_zero_results():
    zero = _result()
    failed = _result(
        status=CoverageStatus.FAILED,
        error=SourceError(code="transport_error", message="provider request failed"),
    )

    assert zero.records == [] and zero.error is None
    assert failed.records == [] and failed.error is not None
    assert failed.status != zero.status


def test_source_result_rejects_failed_status_without_error():
    with pytest.raises(ValidationError, match="requires an error"):
        _result(status="failed")


@pytest.mark.parametrize("status", ["failed", "not_configured", "not_applicable"])
def test_source_result_rejects_records_for_non_usable_status(status):
    error = None if status == "not_applicable" else {"code": status, "message": status}
    with pytest.raises(ValidationError, match="cannot contain records"):
        _result(status=status, records=[{"pmid": "123"}], error=error)


def test_source_result_rejects_partial_without_success_and_failure_or_truncation():
    with pytest.raises(ValidationError, match="partial source results"):
        _result(status="partial", requests=[])


def test_partial_source_accepts_successful_zero_request_mixed_with_failure():
    result = _result(
        status="partial",
        requests=[
            {
                "query": "q1",
                "source_url": "https://example.test/search?q=q1",
                "retrieved_at": NOW,
                "cache": {"status": "miss"},
                "status": "complete",
                "record_count": 0,
            },
            {
                "query": "q2",
                "source_url": "https://example.test/search?q=q2",
                "retrieved_at": NOW,
                "cache": {"status": "miss"},
                "status": "failed",
                "record_count": 0,
                "error": {"code": "timeout", "message": "provider request timed out"},
            },
        ],
        error={"code": "partial_coverage", "message": "one subquery failed"},
    )

    assert result.records == []
    assert result.status == CoverageStatus.PARTIAL


def test_source_result_rejects_failed_status_with_usable_constituent_request():
    with pytest.raises(ValidationError, match="cannot contain usable requests"):
        _result(
            status="failed",
            error={"code": "transport_error", "message": "provider request failed"},
            requests=[
                {
                    "query": "q1",
                    "source_url": "https://example.test/search?q=q1",
                    "retrieved_at": NOW,
                    "cache": {"status": "miss"},
                    "status": "complete",
                    "record_count": 1,
                }
            ],
        )


def test_source_request_rejects_partial_without_any_usable_records():
    with pytest.raises(ValidationError, match="partial request evidence"):
        SourceRequestEvidence(
            query="q",
            source_url="https://example.test/search",
            retrieved_at=NOW,
            cache={"status": "miss"},
            status="partial",
            record_count=0,
        )


@pytest.mark.parametrize("status", ["failed", "not_configured", "not_applicable"])
def test_source_result_rejects_totals_for_non_usable_status(status):
    error = None if status == "not_applicable" else {"code": status, "message": status}
    with pytest.raises(ValidationError, match="cannot contain a total"):
        _result(status=status, total_available=0, error=error)


def test_source_result_rejects_total_smaller_than_returned_records():
    with pytest.raises(ValidationError, match="smaller"):
        _result(records=[{"pmid": "1"}, {"pmid": "2"}], total_available=1)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], CoverageStatus.NOT_APPLICABLE),
        ([CoverageStatus.NOT_APPLICABLE], CoverageStatus.NOT_APPLICABLE),
        ([CoverageStatus.COMPLETE], CoverageStatus.COMPLETE),
        ([CoverageStatus.COMPLETE, CoverageStatus.NOT_APPLICABLE], CoverageStatus.COMPLETE),
        ([CoverageStatus.COMPLETE, CoverageStatus.FAILED], CoverageStatus.PARTIAL),
        ([CoverageStatus.PARTIAL], CoverageStatus.PARTIAL),
        ([CoverageStatus.NOT_CONFIGURED], CoverageStatus.NOT_CONFIGURED),
        ([CoverageStatus.FAILED, CoverageStatus.NOT_CONFIGURED], CoverageStatus.FAILED),
    ],
)
def test_aggregate_coverage_preserves_failure_semantics(statuses, expected):
    assert aggregate_coverage(statuses) == expected


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([CacheStatus.HIT], CacheStatus.HIT),
        ([CacheStatus.MISS], CacheStatus.MISS),
        ([CacheStatus.DISABLED], CacheStatus.DISABLED),
        ([CacheStatus.HIT, CacheStatus.MISS], CacheStatus.MIXED),
    ],
)
def test_aggregate_cache_status_reports_mixed_provenance(statuses, expected):
    assert aggregate_cache_status(statuses) == expected


@pytest.mark.parametrize(
    "source_url",
    [
        "http://example.test/search",
        "javascript:alert(1)",
        "https://example.test/search?api_key=SENTINEL",
        "https://user:password@example.test/search",
    ],
)
def test_source_result_rejects_unsafe_or_credential_bearing_urls(source_url):
    with pytest.raises(ValidationError):
        _result(source_url=source_url)


def test_document_evidence_accepts_only_sha256_urns():
    valid = _result(
        source=SourceName.DOCUMENT,
        source_url="urn:sha256:" + "a" * 64,
    )
    assert valid.source == SourceName.DOCUMENT

    with pytest.raises(ValidationError):
        _result(source=SourceName.DOCUMENT, source_url="file:///tmp/report.txt")


def test_evidence_json_never_contains_request_credentials():
    result = _result()
    serialized = result.model_dump_json()
    assert "api_key" not in serialized
    assert "SENTINEL" not in serialized


def test_fixture_transport_records_request_without_repr_leaking_headers():
    transport = FixtureTransport.json({"ok": True}, url="https://example.test/data")
    request = HttpRequest(
        method="GET",
        url="https://example.test/data",
        headers={"Authorization": "Bearer SENTINEL"},
        timeout_seconds=3,
    )

    response = transport.request(request)

    assert json.loads(response.body) == {"ok": True}
    assert transport.calls[0].headers["Authorization"] == "Bearer SENTINEL"
    assert "SENTINEL" not in repr(response)
    assert "SENTINEL" not in repr(transport.calls[0])


def test_urllib_transport_sends_json_and_keeps_credentials_transport_only(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"ok": true}'

        def geturl(self):
            return "https://example.test/data"

        def getcode(self):
            return 200

    def urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    response = UrllibTransport().request(
        HttpRequest(
            method="POST",
            url="https://example.test/data",
            headers={"Authorization": "Bearer SENTINEL"},
            json_body={"query": "drug"},
            timeout_seconds=3,
        )
    )

    assert response.body == b'{"ok": true}'
    assert captured["request"].get_header("Authorization") == "Bearer SENTINEL"
    assert json.loads(captured["request"].data) == {"query": "drug"}
    assert captured["timeout"] == 3
    assert "SENTINEL" not in repr(response)


def test_urllib_transport_error_is_credential_free(monkeypatch):
    sentinel = "SECRET_TRANSPORT_SENTINEL"

    def urlopen(_request, timeout):
        assert timeout == 30.0
        raise urllib.error.HTTPError(
            f"https://example.test/data?api_key={sentinel}",
            500,
            sentinel,
            {},
            None,
        )

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    with pytest.raises(TransportError) as caught:
        UrllibTransport().request(HttpRequest(method="GET", url="https://example.test/data"))

    assert caught.value.code == "http_error"
    assert sentinel not in str(caught.value)
    assert sentinel not in repr(caught.value)
    assert caught.value.__cause__ is None


def test_urllib_transport_rejects_non_https_url():
    with pytest.raises(TransportError, match="rejected"):
        UrllibTransport().request(HttpRequest(method="GET", url="http://example.test/data"))


def test_cache_namespace_separates_web_backends():
    cache_store("web:serper", {"q": "drug", "days": 30}, {"results": []})
    assert cache_lookup("web:serper", {"q": "drug", "days": 30}).status == CacheStatus.HIT
    assert cache_lookup("web:exa", {"q": "drug", "days": 30}).status == CacheStatus.MISS


def test_ttl_zero_disables_cache_hits(monkeypatch):
    monkeypatch.setenv("CI_CACHE_TTL_HOURS", "0")
    cache_store("pubmed", {"q": "drug"}, {"ids": []})

    lookup = cache_lookup("pubmed", {"q": "drug"})

    assert lookup.status == CacheStatus.DISABLED
    assert lookup.payload is None


@pytest.mark.parametrize("ttl", ["not-an-int", "-1"])
def test_invalid_or_negative_ttl_uses_default(monkeypatch, ttl):
    monkeypatch.setenv("CI_CACHE_TTL_HOURS", ttl)
    cache_store("pubmed", {"q": "drug"}, {"ids": ["1"]})
    assert cache_lookup("pubmed", {"q": "drug"}).status == CacheStatus.HIT


def test_schema_v1_cache_file_is_ignored(monkeypatch, tmp_path):
    data_dir = tmp_path / "ci-data"
    monkeypatch.setenv("OPEN_PHARMA_CI_DATA_DIR", str(data_dir))
    cache_dir = data_dir / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "legacy.json").write_text(json.dumps({"_cached_at": 0, "response": {"old": True}}))

    lookup = cache_lookup("pubmed", {"q": "drug"})

    assert lookup.status == CacheStatus.MISS
    assert cache_stats()["ignored_entry_count"] == 1


def test_cache_hit_exposes_utc_timestamp():
    cache_store("pubmed", {"q": "drug"}, {"ids": ["1"]})
    lookup = cache_lookup("pubmed", {"q": "drug"})

    assert lookup.status == CacheStatus.HIT
    assert isinstance(lookup.cached_at, datetime)
    assert lookup.cached_at.tzinfo == timezone.utc


def test_cache_tree_never_persists_credentials(monkeypatch, tmp_path):
    sentinel = "SECRET_OPENFDA_SENTINEL"
    data_dir = tmp_path / "ci-data"
    monkeypatch.setenv("OPEN_PHARMA_CI_DATA_DIR", str(data_dir))
    cache_store(
        f"https://api.fda.gov/drug/drugsfda.json?api_key={sentinel}",
        {"search": "test", "api_key": sentinel},
        {"result": "ok", "authorization": sentinel},
    )

    persisted = "".join(path.read_text(errors="ignore") for path in data_dir.rglob("*") if path.is_file())
    assert sentinel not in persisted
