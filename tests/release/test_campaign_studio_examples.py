"""Release contract for Campaign Studio 1.1 documentation and HTML examples."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import open_pharma_plugins_campaign_studio as campaign_studio

ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "src" / "capabilities" / "campaign-studio" / "skill"
REFERENCE_ROOT = SKILL_ROOT / "references"
EXAMPLE_ROOT = REFERENCE_ROOT / "examples"

EXPECTED_TOOLS = {
    "create_campaign_brief",
    "get_campaign_status",
    "preflight_campaign_inputs",
    "retrieve_approved_claims",
    "retrieve_brand_components",
    "generate_audience_journey",
    "generate_message_architecture",
    "generate_channel_copy",
    "validate_claims_and_fair_balance",
    "render_email",
    "render_banner",
    "render_poster",
    "validate_rendered_assets",
    "package_mlr_submission",
    "render_mlr_review",
    "export_mlr_package",
}


def _example(name: str) -> str:
    return (EXAMPLE_ROOT / name).read_text(encoding="utf-8")


def test_campaign_studio_exposes_the_documented_16_tool_contract():
    assert {tool["name"] for tool in campaign_studio.list_tools()} == EXPECTED_TOOLS


def test_campaign_studio_reference_contract_is_complete():
    expected = {
        "input-contracts.md",
        "claim-governance.md",
        "channel-specifications.md",
        "output-schema.md",
        "examples/email.html",
        "examples/mlr-review.html",
    }

    assert {str(path.relative_to(REFERENCE_ROOT)) for path in REFERENCE_ROOT.rglob("*") if path.is_file()} >= expected


def test_campaign_studio_html_examples_match_the_production_renderers():
    result = subprocess.run(
        [sys.executable, "scripts/generate_campaign_studio_html_examples.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_campaign_studio_example_generator_calls_production_renderers(monkeypatch):
    from open_pharma_plugins_campaign_studio import _mlr_package
    from open_pharma_plugins_campaign_studio.tools import render_email

    calls: list[str] = []
    email_sentinel = "<!doctype html><title>production email witness</title>"
    review_sentinel = "<!doctype html><title>production review witness</title>"

    def witness_email(context, template=None):
        calls.append("email")
        assert context["brief"]["brand"] == "ONCORIX"
        assert template is None
        return email_sentinel.encode("utf-8"), {"kind": "default"}

    def witness_review(model):
        calls.append("review")
        assert model["channels"][0]["preview"] == email_sentinel
        return review_sentinel

    monkeypatch.setattr(render_email, "_build_email_candidate", witness_email)
    monkeypatch.setattr(_mlr_package, "_render_html", witness_review)
    script = ROOT / "scripts" / "generate_campaign_studio_html_examples.py"
    spec = importlib.util.spec_from_file_location("campaign_studio_example_generator_witness", script)
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    rendered = generator.rendered_examples()

    assert calls == ["email", "review"]
    assert {path.name: content for path, content in rendered.items()} == {
        "email.html": email_sentinel,
        "mlr-review.html": review_sentinel,
    }


def test_campaign_studio_examples_are_fictional_drafts_and_review_gated():
    for name in ("email.html", "mlr-review.html"):
        text = _example(name).casefold()
        assert "fictional" in text
        assert "draft" in text
        assert "qualified" in text
        assert "review" in text
        assert "not an approval" in text


def test_campaign_studio_examples_are_self_contained_and_responsive():
    email = _example("email.html")
    review = _example("mlr-review.html")

    for text in (email, review):
        assert re.search(r'<meta\s+name="viewport"', text, re.IGNORECASE)
        assert "@media" in text
        assert not re.search(r'<(?:script|link)\b[^>]+(?:src|href)=["\']https?://', text, re.IGNORECASE)
        assert not re.search(r'url\(["\']?https?://', text, re.IGNORECASE)
        assert "file:" not in text.casefold()

    sources = re.findall(r'\bsrc=["\']([^"\']+)', email, re.IGNORECASE)
    assert sources and all(source.startswith("data:") for source in sources)
    assert not re.findall(r'\bsrc=["\']([^"\']+)', review, re.IGNORECASE)


def test_campaign_studio_examples_keep_production_accessibility_markers():
    email = _example("email.html")
    review = _example("mlr-review.html")

    assert '<html lang="en">' in email
    assert 'role="presentation"' in email
    assert 'alt="ONCORIX logo"' in email
    assert ".cta:focus" in email
    assert 'data-role="preheader"' in email
    assert 'data-role="legal-isi"' in email

    assert '<html lang="en">' in review
    assert 'role="tablist"' in review
    assert 'role="tab"' in review
    assert 'role="tabpanel"' in review
    assert "data-print" in review
    assert ":focus-visible" in review
    assert "ArrowRight" in review and "ArrowLeft" in review
    assert "Content-Security-Policy" in review
    assert "Artifact integrity" in review
    assert "<dt>Capability</dt><dd>1.1.0</dd>" in review
    assert "0" * 64 not in review and "1" * 64 not in review
