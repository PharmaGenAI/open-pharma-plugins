"""Fixture-locked openFDA and DailyMed regulatory evidence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from helpers.ci_http import FixtureTransport, parse_text_block

from open_pharma_plugins_competitive_intelligence import _dailymed, _fda, _regulatory
from open_pharma_plugins_competitive_intelligence._transport import TransportError
from open_pharma_plugins_competitive_intelligence.models import (
    CoverageStatus,
    RegulatoryEvent,
    RegulatorySearchRequest,
)
from open_pharma_plugins_competitive_intelligence.tools import ci_scan_regulatory

NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def fixture_providers(fixtures):
    openfda_transport = FixtureTransport.json_file(fixtures.path("openfda_drugsfda.json"))
    search_transport = FixtureTransport.json_file(fixtures.path("dailymed_search.json"))
    history_transport = FixtureTransport.json_file(fixtures.path("dailymed_history.json"))
    return _regulatory.RegulatoryProviders(
        openfda=lambda request, now: _fda.search_openfda(request, transport=openfda_transport, now=now),
        dailymed_search=lambda request, now: _dailymed.search_dailymed(request, transport=search_transport, now=now),
        dailymed_history=lambda set_id, now: _dailymed.get_dailymed_history(
            set_id, transport=history_transport, now=now
        ),
    )


def test_default_regulatory_scan_parses_nested_dailymed_history(monkeypatch, fixtures):
    monkeypatch.setattr(_regulatory, "DEFAULT_PROVIDERS", fixture_providers(fixtures))

    payload = parse_text_block(ci_scan_regulatory.handle({"drug_name": "ExampleDrug"}))

    assert payload["label_versions"] == 2
    assert payload["label_history"][0]["set_id"] == "9aa7140c-012c-4ea6-866d-4732e915dab6"
    assert {item["source"] for item in payload["source_ledger"]} == {"openfda", "dailymed"}


def test_openfda_query_uses_brand_or_generic_and_keeps_sponsor(fixtures):
    transport = FixtureTransport.json_file(fixtures.path("openfda_drugsfda.json"))

    result = _fda.search_openfda(
        RegulatorySearchRequest(
            drug_name="Example Drug",
            date_from="2025-01-01",
            date_to="2026-08-28",
        ),
        transport=transport,
        now=NOW,
    )

    query = parse_qs(urlsplit(transport.calls[0].url).query)["search"][0]
    assert 'openfda.brand_name:"Example Drug"' in query
    assert 'openfda.generic_name:"Example Drug"' in query
    assert '(openfda.brand_name:"Example Drug" OR openfda.generic_name:"Example Drug")' in query
    assert " AND submissions.submission_status_date:" in query
    event = RegulatoryEvent.model_validate(result.records[0])
    assert event.sponsor == "Example Sponsor LLC"
    assert event.generic_name == "examplemab"
    assert event.manufacturer_names == ["Example Manufacturing Inc"]


def test_openfda_transport_key_is_absent_from_evidence_and_cache(monkeypatch, fixtures, tmp_path):
    sentinel = "OPENFDA_SECRET_SENTINEL"
    monkeypatch.setenv("OPENFDA_API_KEY", sentinel)
    monkeypatch.setenv("OPEN_PHARMA_CI_DATA_DIR", str(tmp_path / "ci-data"))
    transport = FixtureTransport.json_file(fixtures.path("openfda_drugsfda.json"))

    result = _fda.search_openfda(
        RegulatorySearchRequest(drug_name="ExampleDrug"),
        transport=transport,
        now=NOW,
    )

    assert sentinel in transport.calls[0].url
    assert sentinel not in result.model_dump_json()
    persisted = "".join(path.read_text(errors="ignore") for path in tmp_path.rglob("*") if path.is_file())
    assert sentinel not in persisted


def test_dailymed_search_and_history_use_documented_containers(fixtures):
    search = _dailymed.search_dailymed(
        RegulatorySearchRequest(drug_name="ExampleDrug"),
        transport=FixtureTransport.json_file(fixtures.path("dailymed_search.json")),
        now=NOW,
    )
    history = _dailymed.get_dailymed_history(
        "9aa7140c-012c-4ea6-866d-4732e915dab6",
        transport=FixtureTransport.json_file(fixtures.path("dailymed_history.json")),
        now=NOW,
    )

    assert search.records[0]["set_id"] == "9aa7140c-012c-4ea6-866d-4732e915dab6"
    assert [item["spl_version"] for item in history.records] == ["3", "2"]
    assert all(item["title"] == "EXAMPLE TABLET [EXAMPLE LABELER]" for item in history.records)


def test_one_provider_failure_is_partial_not_zero_coverage(fixtures):
    failing = FixtureTransport([(None, TransportError("timeout", "provider request timed out"))])
    providers = _regulatory.RegulatoryProviders(
        openfda=lambda request, now: _fda.search_openfda(request, transport=failing, now=now),
        dailymed_search=lambda request, now: _dailymed.search_dailymed(
            request,
            transport=FixtureTransport.json_file(fixtures.path("dailymed_search.json")),
            now=now,
        ),
        dailymed_history=lambda set_id, now: _dailymed.get_dailymed_history(
            set_id,
            transport=FixtureTransport.json_file(fixtures.path("dailymed_history.json")),
            now=now,
        ),
    )

    result = _regulatory.scan_regulatory(
        RegulatorySearchRequest(drug_name="ExampleDrug"),
        providers=providers,
        now=NOW,
    )

    assert result.coverage == CoverageStatus.PARTIAL
    assert result.openfda.status == CoverageStatus.FAILED
    assert result.dailymed.status == CoverageStatus.COMPLETE
    assert result.label_history


def test_valid_openfda_zero_results_is_complete():
    request = RegulatorySearchRequest(drug_name="NoSuchDrug")
    transport = FixtureTransport.json(
        {"meta": {"results": {"total": 0}}, "results": []},
        url=_fda.build_openfda_url(request, api_key=""),
    )

    result = _fda.search_openfda(request, transport=transport, now=NOW)

    assert result.status == CoverageStatus.COMPLETE
    assert result.records == []
    assert result.error is None


def test_disabling_label_history_makes_no_dailymed_call(fixtures):
    calls = []
    providers = _regulatory.RegulatoryProviders(
        openfda=lambda request, now: _fda.search_openfda(
            request,
            transport=FixtureTransport.json_file(fixtures.path("openfda_drugsfda.json")),
            now=now,
        ),
        dailymed_search=lambda request, now: calls.append("search"),
        dailymed_history=lambda set_id, now: calls.append("history"),
    )

    result = _regulatory.scan_regulatory(
        RegulatorySearchRequest(drug_name="ExampleDrug", include_label_history=False),
        providers=providers,
        now=NOW,
    )

    assert result.dailymed is None
    assert calls == []


def test_regulatory_tool_copy_and_payload_do_not_claim_safety_alerts(monkeypatch, fixtures):
    monkeypatch.setattr(_regulatory, "DEFAULT_PROVIDERS", fixture_providers(fixtures))
    payload = parse_text_block(ci_scan_regulatory.handle({"drug_name": "ExampleDrug"}))

    assert "safety alert" not in ci_scan_regulatory.TOOL["description"].lower()
    assert "safety_alert" not in json.dumps(payload)
    assert payload["events"][0]["event_type"] in {"approval", "supplement"}
