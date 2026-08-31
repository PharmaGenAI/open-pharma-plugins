"""Immutable report and DOM-safe timeline artifacts projected from one CI run."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from helpers.ci_http import parse_text_block
from pydantic import ValidationError

from open_pharma_plugins_competitive_intelligence import _runs
from open_pharma_plugins_competitive_intelligence._artifacts import (
    safe_csv_cell,
    sanitize_display_stem,
)
from open_pharma_plugins_competitive_intelligence._runs import ProviderBundle, create_run
from open_pharma_plugins_competitive_intelligence.models import (
    CacheProvenance,
    RefreshRequest,
    RegulatorySearchResult,
    SourceName,
    SourceResult,
    TrackedEntity,
)
from open_pharma_plugins_competitive_intelligence.tools import ci_report, ci_timeline

NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
HOSTILE = "</script><img src=x onerror=alert(1)>"


def _result(source, query, records, now):
    endpoints = {
        SourceName.CLINICAL_TRIALS: "https://clinicaltrials.gov/api/v2/studies",
        SourceName.OPENFDA: "https://api.fda.gov/drug/drugsfda.json",
        SourceName.DAILYMED: "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json",
        SourceName.WEB: "https://api.exa.ai/search",
        SourceName.PUBMED: "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
    }
    return SourceResult(
        source=source,
        provider="fixture",
        status="complete",
        query=query,
        source_url=endpoints[source],
        retrieved_at=now,
        cache=CacheProvenance(status="disabled"),
        records=records,
        total_available=len(records),
    )


class OutputProviders:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def bundle(self):
        return ProviderBundle(
            trials=self.trials,
            regulatory=self.regulatory,
            news=self.news,
            publications=self.publications,
        )

    def trials(self, request, now):
        self.calls += 1
        return _result(
            SourceName.CLINICAL_TRIALS,
            request.query,
            [
                {
                    "nct_id": "NCT12345678",
                    "title": f'=HYPERLINK("https://evil.test") {HOSTILE}',
                    "sponsor": HOSTILE,
                    "phase": "PHASE3",
                    "status": "RECRUITING",
                    "start_date": "2026-08-01",
                    "source_url": "https://clinicaltrials.gov/study/NCT12345678",
                }
            ],
            now,
        )

    def regulatory(self, request, now):
        self.calls += 1
        return RegulatorySearchResult(
            drug_name=request.drug_name,
            coverage="complete",
            openfda=_result(
                SourceName.OPENFDA,
                request.drug_name,
                [
                    {
                        "date": "2026-08-15",
                        "event_type": "approval",
                        "application_number": "BLA123",
                        "description": HOSTILE,
                        "source_url": "https://api.fda.gov/drug/drugsfda.json",
                    }
                ],
                now,
            ),
            dailymed=_result(
                SourceName.DAILYMED,
                request.drug_name,
                [
                    {
                        "set_id": "set-1",
                        "spl_version": "3",
                        "published_date": "not-a-date",
                        "title": HOSTILE,
                        "source_url": "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=set-1",
                    }
                ],
                now,
            ),
        )

    def news(self, request, now):
        self.calls += 1
        return _result(
            SourceName.WEB,
            request.query,
            [
                {
                    "title": HOSTILE,
                    "url": "https://news.example.test/item",
                    "snippet": "+cmd",
                    "source": "Example News",
                    "published_date": "2026-08-20",
                }
            ],
            now,
        )

    def publications(self, request, now):
        self.calls += 1
        return _result(
            SourceName.PUBMED,
            request.query,
            [
                {
                    "pmid": "12345678",
                    "title": HOSTILE,
                    "journal": "Example Journal",
                    "pub_date": "2025-12",
                    "source_url": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
                }
            ],
            now,
        )


@pytest.fixture
def output_run():
    return create_run(
        RefreshRequest(entities=[TrackedEntity(entity_type="drug", name=HOSTILE)]),
        providers=OutputProviders().bundle,
        now=NOW,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('=HYPERLINK("https://evil.test")', '\'=HYPERLINK("https://evil.test")'),
        ("+cmd", "'+cmd"),
        ("-2+3", "'-2+3"),
        ("@SUM(A1:A2)", "'@SUM(A1:A2)"),
        ("\t=cmd", "'\t=cmd"),
        ("\r=cmd", "'\r=cmd"),
        ("  =cmd", "'  =cmd"),
        ("ordinary text", "ordinary text"),
    ],
)
def test_safe_csv_cell_neutralizes_formula_prefixes(value, expected):
    assert safe_csv_cell(value) == expected


@pytest.mark.parametrize("value", ["../bad", "/tmp/bad", "a/b", "a\\b", "", "药物", "x" * 120])
def test_display_stem_is_one_bounded_safe_component(value):
    stem = sanitize_display_stem(value, default="briefing")
    assert stem not in {"", ".", ".."}
    assert len(stem) <= 100
    assert "/" not in stem and "\\" not in stem


def test_explicit_report_name_never_overwrites_and_manifest_hashes(output_run):
    first = ci_report.write_report_artifacts(output_run, display_stem="launch_review", now=NOW)
    first_bytes = Path(first.json_path).read_bytes()
    second = ci_report.write_report_artifacts(output_run, display_stem="launch_review", now=NOW)

    assert first.output_dir != second.output_dir
    assert Path(first.json_path).read_bytes() == first_bytes
    assert len(first.csv_files) == 4
    assert first.manifest.run_records_sha256 == output_run.records_sha256
    for artifact in first.manifest.artifacts:
        payload = Path(first.output_dir, artifact.relative_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == artifact.sha256
        assert len(payload) == artifact.byte_size


def test_report_csv_is_formula_safe_and_html_escaped(output_run):
    result = ci_report.write_report_artifacts(output_run, now=NOW)
    trials_csv = next(path for path in result.csv_files if path.endswith("_trials.csv"))
    html = Path(result.html_path).read_text()

    assert "'=HYPERLINK" in Path(trials_csv).read_text(encoding="utf-8-sig")
    assert HOSTILE not in html
    assert "&lt;/script&gt;&lt;img" in html
    assert "Content-Security-Policy" in html


def test_report_from_run_performs_no_provider_call(output_run, monkeypatch):
    monkeypatch.setattr(_runs, "DEFAULT_PROVIDERS", providers_that_raise())

    payload = parse_text_block(ci_report.handle({"run_id": output_run.run_id}))

    assert payload["success"] is True
    assert payload["run_id"] == output_run.run_id
    assert payload["run_records_sha256"] == output_run.records_sha256


def test_legacy_report_creates_exactly_one_run(monkeypatch):
    from open_pharma_plugins_competitive_intelligence._watchlist import save_watchlist

    save_watchlist([{"entity_type": "drug", "name": "ExampleDrug"}])
    providers = OutputProviders()
    monkeypatch.setattr(_runs, "DEFAULT_PROVIDERS", providers.bundle)
    original = ci_report.create_run
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(ci_report, "create_run", counted)
    payload = parse_text_block(ci_report.handle({"focus": "ExampleDrug"}))

    assert payload["success"] is True
    assert len(calls) == 1
    assert providers.calls == 4


def test_report_selectors_are_mutually_exclusive():
    with pytest.raises(ValidationError, match="run_id cannot be combined with focus"):
        ci_report.ReportArgs(run_id="run", focus="drug")


def test_months_back_uses_calendar_cutoff():
    now = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
    assert ci_timeline.calendar_month_cutoff(now, 6) == date(2026, 2, 28)


@pytest.mark.parametrize(
    ("raw", "expected", "precision"),
    [
        ("2024-02-29", date(2024, 2, 29), "day"),
        ("2026-08", date(2026, 8, 1), "month"),
        ("2026", date(2026, 1, 1), "year"),
        ("Sep 26, 2012", date(2012, 9, 26), "day"),
        ("not-a-date", None, "unknown"),
    ],
)
def test_timeline_normalizes_supported_date_precision(raw, expected, precision):
    assert ci_timeline.normalize_event_date(raw) == (expected, precision)


def test_timeline_filters_dates_and_uses_dom_text_nodes(output_run):
    result = ci_timeline.write_timeline_artifact(output_run, months_back=6, now=NOW)
    html = Path(result.html_path).read_text()

    assert result.included_events == 2
    assert result.excluded_old_events == 1
    assert result.excluded_undated_events == 1
    assert HOSTILE not in html
    assert "innerHTML" not in html
    assert "textContent" in html
    assert "replaceChildren" in html
    assert 'onclick="' not in html
    assert result.manifest.run_records_sha256 == output_run.records_sha256


def test_report_and_timeline_reuse_same_run_without_collection(output_run, monkeypatch):
    monkeypatch.setattr(_runs, "DEFAULT_PROVIDERS", providers_that_raise())

    report = parse_text_block(ci_report.handle({"run_id": output_run.run_id}))
    timeline = parse_text_block(ci_timeline.handle({"run_id": output_run.run_id, "months_back": 6}))

    assert report["run_records_sha256"] == timeline["run_records_sha256"] == output_run.records_sha256


def test_timeline_selectors_are_mutually_exclusive():
    with pytest.raises(ValidationError, match="run_id cannot be combined with entities"):
        ci_timeline.TimelineArgs(run_id="run", entities=["drug"])


def providers_that_raise() -> ProviderBundle:
    def fail(_request, _now):
        raise AssertionError("unexpected provider call")

    return ProviderBundle(trials=fail, regulatory=fail, news=fail, publications=fail)
