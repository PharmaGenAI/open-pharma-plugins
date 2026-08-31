"""Fixture-locked ClinicalTrials.gov API-v2 behavior."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from helpers.ci_http import FixtureTransport, parse_text_block

from open_pharma_plugins_competitive_intelligence import _clinical_trials
from open_pharma_plugins_competitive_intelligence._transport import TransportError
from open_pharma_plugins_competitive_intelligence.models import (
    CoverageStatus,
    TrialDetail,
    TrialDetailRequest,
    TrialSearchRequest,
)
from open_pharma_plugins_competitive_intelligence.tools import ci_scan_trials, ci_trial_detail


def _observational_study() -> dict:
    return {
        "hasResults": False,
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT07654321",
                "briefTitle": "Observational Example",
            },
            "statusModule": {"overallStatus": "RECRUITING"},
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Example Sponsor"}},
            "designModule": {
                "studyType": "OBSERVATIONAL",
                "enrollmentInfo": {"count": 25},
            },
            "conditionsModule": {"conditions": ["Example Condition"]},
        },
    }


def test_search_requests_total_and_uses_top_level_has_results(fixtures):
    transport = FixtureTransport.sequence(
        fixtures.bytes("clinicaltrials_search_page_1.json"),
        fixtures.bytes("clinicaltrials_search_page_2.json"),
    )

    result = _clinical_trials.search_trials(
        TrialSearchRequest(query="examplemab", max_results=3),
        transport=transport,
    )

    first_query = parse_qs(urlsplit(transport.calls[0].url).query)
    assert first_query["countTotal"] == ["true"]
    assert "HasResults" in first_query["fields"][0]
    assert parse_qs(urlsplit(transport.calls[1].url).query)["pageToken"] == ["page-two"]
    assert result.total_available == 3
    assert [record["has_results"] for record in result.records] == [True, False, True]
    assert result.status == CoverageStatus.COMPLETE


def test_search_normalizes_short_phase_filter(fixtures):
    transport = FixtureTransport.json_file(fixtures.path("clinicaltrials_search_page_1.json"))

    _clinical_trials.search_trials(
        TrialSearchRequest(query="examplemab", phase="3", max_results=1),
        transport=transport,
    )

    query = parse_qs(urlsplit(transport.calls[0].url).query)
    assert "filter.phase" not in query
    assert query["filter.advanced"] == ["AREA[Phase]PHASE3"]


def test_search_retains_intervention_types_and_canonical_urls(fixtures):
    result = _clinical_trials.search_trials(
        TrialSearchRequest(query="examplemab", max_results=2),
        transport=FixtureTransport.json_file(fixtures.path("clinicaltrials_search_page_1.json")),
    )

    first = result.records[0]
    assert first["source_url"] == "https://clinicaltrials.gov/study/NCT01234567"
    assert first["collaborators"] == ["Example Research Group"]
    assert first["interventions"][0]["intervention_type"] == "DRUG"
    assert first["interventions"][1]["name"] == "Placebo"


def test_search_accepts_observational_study_without_arms_module():
    request = TrialSearchRequest(query="observational example")
    result = _clinical_trials.search_trials(
        request,
        transport=FixtureTransport.json(
            {"studies": [_observational_study()], "totalCount": 1},
            url=_clinical_trials.build_search_url(request),
        ),
    )

    assert result.status == CoverageStatus.COMPLETE
    assert result.records[0]["study_type"] == "OBSERVATIONAL"
    assert result.records[0]["interventions"] == []


def test_later_page_failure_returns_partial_without_losing_records(fixtures):
    transport = FixtureTransport.sequence(
        fixtures.bytes("clinicaltrials_search_page_1.json"),
        TransportError("timeout", "provider request timed out"),
    )

    result = _clinical_trials.search_trials(
        TrialSearchRequest(query="examplemab", max_results=3),
        transport=transport,
    )

    assert result.status == CoverageStatus.PARTIAL
    assert len(result.records) == 2
    assert result.error.code == "pagination_failed"
    assert "pagination" in " ".join(result.limitations).lower()


def test_first_page_malformed_shape_is_failed_not_zero_results():
    result = _clinical_trials.search_trials(
        TrialSearchRequest(query="examplemab"),
        transport=FixtureTransport.json(
            {"studies": {}}, url=_clinical_trials.build_search_url(TrialSearchRequest(query="examplemab"))
        ),
    )

    assert result.status == CoverageStatus.FAILED
    assert result.records == []
    assert result.error.code == "schema_mismatch"


def test_first_page_with_only_invalid_records_is_failed_not_an_exception():
    request = TrialSearchRequest(query="invalid study")
    result = _clinical_trials.search_trials(
        request,
        transport=FixtureTransport.json(
            {"studies": [{"hasResults": False, "protocolSection": {}}], "totalCount": 1},
            url=_clinical_trials.build_search_url(request),
        ),
    )

    assert result.status == CoverageStatus.FAILED
    assert result.records == []
    assert result.error.code == "schema_mismatch"


def test_trial_detail_parses_arm_groups_and_string_intervention_names(fixtures):
    result = _clinical_trials.get_trial_detail(
        TrialDetailRequest(nct_id="NCT01234567"),
        transport=FixtureTransport.json_file(fixtures.path("clinicaltrials_detail.json")),
    )

    detail = TrialDetail.model_validate(result.records[0])
    assert detail.arms[0].interventions == ["Examplemab 100 mg"]
    assert detail.trial.has_results is True
    assert detail.trial.source_url == "https://clinicaltrials.gov/study/NCT01234567"
    assert detail.results_summary.primary_outcomes_count == 1
    assert detail.publications == ["12345678"]


def test_trial_detail_accepts_observational_study_without_arms_module():
    result = _clinical_trials.get_trial_detail(
        TrialDetailRequest(nct_id="NCT07654321"),
        transport=FixtureTransport.json(
            _observational_study(),
            url="https://clinicaltrials.gov/api/v2/studies/NCT07654321",
        ),
    )

    assert result.status == CoverageStatus.COMPLETE
    detail = TrialDetail.model_validate(result.records[0])
    assert detail.trial.study_type == "OBSERVATIONAL"
    assert detail.arms == []


def test_scan_handler_preserves_legacy_keys_and_adds_evidence(monkeypatch, fixtures):
    transport = FixtureTransport.json_file(fixtures.path("clinicaltrials_search_page_1.json"))
    monkeypatch.setattr(_clinical_trials, "DEFAULT_TRANSPORT", transport)

    payload = parse_text_block(ci_scan_trials.handle({"query": "examplemab", "max_results": 2}))

    assert payload["total_found"] == 3
    assert payload["returned"] == 2
    assert payload["trials"][0]["interventions"] == ["Examplemab", "Placebo"]
    assert payload["coverage"] == "partial"
    assert payload["source_ledger"][0]["source"] == "clinicaltrials_gov"


def test_detail_handler_preserves_legacy_shape_and_adds_evidence(monkeypatch, fixtures):
    transport = FixtureTransport.json_file(fixtures.path("clinicaltrials_detail.json"))
    monkeypatch.setattr(_clinical_trials, "DEFAULT_TRANSPORT", transport)

    payload = parse_text_block(ci_trial_detail.handle({"nct_id": "NCT01234567"}))

    assert payload["trial"]["interventions"] == ["Examplemab 100 mg"]
    assert payload["arms"][0]["interventions"] == ["Examplemab 100 mg"]
    assert payload["coverage"] == "complete"
    assert payload["status_history"] == []
    assert any("history" in item.lower() for item in payload["limitations"])
