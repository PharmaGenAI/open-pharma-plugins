"""Professional, self-contained Campaign Studio rendering contracts."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import sys
from importlib.resources import files
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from open_pharma_plugins_campaign_studio._campaign_store import (
    campaign_dir,
    load_artifact,
    load_validation_artifact,
    save_artifact,
    save_brief,
    save_output,
    save_validation_artifact,
)
from open_pharma_plugins_campaign_studio._inputs import preflight_inputs
from open_pharma_plugins_campaign_studio._renderer import validation_input_fingerprint
from open_pharma_plugins_campaign_studio.tools.render_banner import handle as render_banner
from open_pharma_plugins_campaign_studio.tools.render_email import handle as render_email
from open_pharma_plugins_campaign_studio.tools.render_poster import handle as render_poster
from open_pharma_plugins_campaign_studio.tools.validate_claims_and_fair_balance import handle as validate_claims


@pytest.fixture(autouse=True)
def campaign_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_PHARMA_CAMPAIGN_STORE_DIR", str(tmp_path / "store"))


def _result(blocks: list[dict]) -> dict:
    return json.loads(blocks[0]["text"])


def _copy_block(text: str, claim_id: str | None = None) -> dict:
    return {"text": text, "claim_ids": [claim_id] if claim_id else []}


def _fixture_claims() -> list[dict]:
    path = Path(str(files("open_pharma_plugins_campaign_studio") / "fixtures" / "sample_approved_claims.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_campaign(
    tmp_path: Path,
    *,
    brief_id: str = "render-contract",
    channels: list[str] | None = None,
    asset_dimensions: dict | None = None,
    include_product: bool = True,
    heading_family: str | None = None,
    demo_mode: bool = False,
) -> tuple[str, Path, dict[str, dict]]:
    selected_channels = channels or ["email", "banner", "poster"]
    claims = _fixture_claims()
    claims_by_id = {claim["claim_id"]: claim for claim in claims}
    fixture_root = Path(str(files("open_pharma_plugins_campaign_studio") / "fixtures"))
    if demo_mode:
        source_claims = fixture_root / "sample_approved_claims.json"
        kit = fixture_root / "brand_kit"
    else:
        source_claims = tmp_path / f"{brief_id}-claims.json"
        source_claims.write_text(json.dumps(claims), encoding="utf-8")
        kit = tmp_path / f"{brief_id}-brand-kit"
        shutil.copytree(fixture_root / "brand_kit", kit)
    if heading_family is not None:
        typography_path = kit / "typography.json"
        typography = json.loads(typography_path.read_text(encoding="utf-8"))
        typography["heading_family"] = heading_family
        typography_path.write_text(json.dumps(typography), encoding="utf-8")
    if not include_product:
        (kit / "product.png").unlink()
    brief = {
        "campaign_brief_id": brief_id,
        "campaign_name": "ONCORIX evidence review",
        "country": "US",
        "policy_jurisdiction": "FDA",
        "mode": "promotional",
        "brand": "ONCORIX",
        "indication": "oncology",
        "lifecycle_stage": "growth",
        "target_segment": "oncologists",
        "behavioral_objective": "Review the approved evidence",
        "educational_objective": None,
        "desired_kpi": ["qualified_review"],
        "approved_claims_path": str(source_claims),
        "brand_kit_path": str(kit),
        "demo_mode": demo_mode,
        "call_to_action": "Review the evidence",
        "call_to_action_url": "https://oncorix-hcp.example.com/evidence",
        "channels": selected_channels,
        "asset_dimensions": asset_dimensions,
        "language": "en",
        "localisation_notes": None,
        "required_safety_content": ["isi"],
        "required_legal_content": ["pi_ref", "reporting_statement"],
        "delivery_constraints": None,
        "approval_workflow": "mlr_standard",
        "generated_at": "2026-08-28T00:00:00+00:00",
    }
    save_brief(brief)
    preflight = preflight_inputs(brief_id, str(source_claims), str(kit), demo_mode=demo_mode)
    assert preflight["ready"] is True, preflight

    copies = {
        "email": {
            "subject": _copy_block(claims_by_id["c-001"]["text"], "c-001"),
            "preheader": _copy_block(claims_by_id["c-006"]["text"], "c-006"),
            "headline": _copy_block(claims_by_id["c-002"]["text"], "c-002"),
            "body": [
                _copy_block(claims_by_id["c-003"]["text"], "c-003"),
                _copy_block(claims_by_id["c-007"]["text"], "c-007"),
            ],
            "cta": _copy_block(brief["call_to_action"]),
        },
        "banner": {
            "headline": _copy_block(claims_by_id["c-004"]["text"], "c-004"),
            "sub_headline": None,
            "safety": _copy_block(claims_by_id["c-010"]["text"], "c-010"),
            "cta": _copy_block(brief["call_to_action"]),
        },
        "poster": {
            "headline": _copy_block(claims_by_id["c-002"]["text"], "c-002"),
            "subhead": _copy_block(claims_by_id["c-004"]["text"], "c-004"),
            "body": [
                _copy_block(claims_by_id["c-001"]["text"], "c-001"),
                _copy_block(claims_by_id["c-006"]["text"], "c-006"),
            ],
            "bullet_points": [_copy_block(claims_by_id["c-007"]["text"], "c-007")],
            "cta": _copy_block(brief["call_to_action"]),
            "footnotes": None,
        },
    }
    for channel in selected_channels:
        save_artifact(
            brief_id,
            f"copy-{channel}.json",
            {
                "campaign_brief_id": brief_id,
                "channel": channel,
                "copy": copies[channel],
                "generated_at": "2026-08-28T00:00:00+00:00",
            },
        )
    validation = _result(validate_claims({"campaign_brief_id": brief_id}))
    assert validation["overall_pass"] is True, validation
    return brief_id, kit, copies


def _refresh_gate(brief_id: str) -> None:
    provenance = load_artifact(brief_id, "input-provenance.json")
    claims_path = Path(provenance["claims"]["resolved_path"])
    brand_kit_path = provenance["brand_kit"]["resolved_path"]
    claims = json.loads(claims_path.read_text(encoding="utf-8"))
    claims_by_id = {claim["claim_id"]: claim for claim in claims}

    def approve_variants(value: object) -> None:
        if isinstance(value, dict):
            text = value.get("text")
            claim_ids = value.get("claim_ids")
            if isinstance(text, str) and isinstance(claim_ids, list):
                for claim_id in claim_ids:
                    claim = claims_by_id.get(claim_id)
                    if claim is not None and text != claim["text"] and text not in claim["allowed_variants"]:
                        claim["allowed_variants"].append(text)
            for nested in value.values():
                approve_variants(nested)
        elif isinstance(value, list):
            for nested in value:
                approve_variants(nested)

    brief = load_artifact(brief_id, "campaign-brief.json")
    for channel in brief["channels"]:
        approve_variants(load_artifact(brief_id, f"copy-{channel}.json"))
    claims_path.write_text(json.dumps(claims), encoding="utf-8")
    preflight = preflight_inputs(brief_id, str(claims_path), brand_kit_path, demo_mode=False)
    assert preflight["ready"] is True, preflight
    validation = _result(validate_claims({"campaign_brief_id": brief_id}))
    assert validation["overall_pass"] is True, validation


def test_email_is_self_contained_and_preserves_preheader_cta_and_required_legal(tmp_path: Path):
    """Removing preheader, exact CTA, embedded logo, or required legal must fail this contract."""
    brief_id, kit, copies = _seed_campaign(tmp_path, channels=["email"])

    response = _result(render_email({"campaign_brief_id": brief_id}))

    assert "error" not in response
    html_path = Path(response["file_path"])
    html = html_path.read_text(encoding="utf-8")
    assert copies["email"]["preheader"]["text"] in html
    assert 'data-role="preheader"' in html
    assert 'href="https://oncorix-hcp.example.com/evidence"' in html
    assert 'href="#"' not in html
    assert "data:image/svg+xml;base64," in html
    assert str(kit) not in html
    assert "file:" not in html
    assert "<table" in html and "@media" in html and ":focus" in html
    legal = json.loads((kit / "legal.json").read_text())
    for key in ("isi", "pi_ref", "reporting_statement"):
        assert html.count(legal[key]) == 1


def test_demo_email_visibly_discloses_fictional_draft_and_qualified_mlr_boundary(tmp_path: Path):
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"], demo_mode=True)

    response = _result(render_email({"campaign_brief_id": brief_id}))

    assert "error" not in response
    html = Path(response["file_path"]).read_text(encoding="utf-8")
    assert "Fictional demonstration" in html
    assert "draft review aid only" in html
    assert "Qualified Medical, Legal, and Regulatory reviewers" in html
    assert 'data-role="legal-demo_disclosure"' in html


def test_banner_contains_embedded_logo_safety_and_distinct_required_legal(tmp_path: Path):
    """Dropping safety/legal roles or leaking the selected logo path must fail."""
    brief_id, kit, copies = _seed_campaign(
        tmp_path,
        channels=["banner"],
        asset_dimensions={"banner": "300x250"},
    )

    response = _result(render_banner({"campaign_brief_id": brief_id}))

    assert response["dimensions"] == "300x250"
    svg = Path(response["file_path"]).read_text(encoding="utf-8")
    assert 'width="300"' in svg and 'height="250"' in svg and 'viewBox="0 0 300 250"' in svg
    assert copies["banner"]["safety"]["text"] in svg
    legal = json.loads((kit / "legal.json").read_text())
    assert svg.count(legal["isi"]) == 1
    assert svg.count(legal["pi_ref"]) == 1
    assert "data:image/svg+xml;base64," in svg
    assert str(kit) not in svg
    assert "<title" in svg and "<desc" in svg


def test_poster_uses_valid_product_and_is_deterministic_single_page(tmp_path: Path):
    """Losing the image, exact MediaBox, or deterministic PDF bytes must fail."""
    from pypdf import PdfReader

    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        channels=["poster"],
        asset_dimensions={"poster": "LETTER"},
    )

    first = _result(render_poster({"campaign_brief_id": brief_id}))
    first_bytes = Path(first["file_path"]).read_bytes()
    second = _result(render_poster({"campaign_brief_id": brief_id, "paper_size": "letter"}))
    second_bytes = Path(second["file_path"]).read_bytes()

    assert first["paper_size"] == "LETTER"
    assert hashlib.sha256(first_bytes).digest() == hashlib.sha256(second_bytes).digest()
    reader = PdfReader(Path(first["file_path"]))
    assert len(reader.pages) == 1
    box = reader.pages[0].mediabox
    assert float(box.width) == pytest.approx(612, abs=0.01)
    assert float(box.height) == pytest.approx(792, abs=0.01)
    xobjects = reader.pages[0]["/Resources"].get("/XObject", {})
    assert any(obj.get_object().get("/Subtype") == "/Image" for obj in xobjects.values())


def test_renderer_conflict_is_structured_and_does_not_overwrite(tmp_path: Path):
    """A conflicting compatibility override cannot replace a previously rendered asset."""
    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        channels=["banner"],
        asset_dimensions={"banner": "300x250"},
    )
    output = Path(_result(render_banner({"campaign_brief_id": brief_id}))["file_path"])
    before = output.read_bytes()

    rejected = _result(render_banner({"campaign_brief_id": brief_id, "dimensions": "728x90"}))

    assert rejected["error"]["code"] == "dimension_conflict"
    assert output.read_bytes() == before


def test_invalid_declared_product_is_structured_and_does_not_overwrite(tmp_path: Path):
    """A live invalid product image invalidates render authorization without overwriting output."""
    brief_id, kit, _copies = _seed_campaign(tmp_path, channels=["poster"])
    old = save_output(brief_id, "poster.pdf", "previous")
    (kit / "product.png").write_bytes(b"not a png")
    manifest = load_artifact(brief_id, "brand-components.json")
    product = manifest["files"]["product.png"]
    product["sha256"] = hashlib.sha256(b"not a png").hexdigest()
    product["size"] = len(b"not a png")
    save_artifact(brief_id, "brand-components.json", manifest)
    rejected = _result(render_poster({"campaign_brief_id": brief_id}))

    assert rejected["error"]["code"] == "pre_render_validation_not_current"
    assert old.read_bytes() == b"previous"


def test_validate_rendered_assets_is_discovered_and_makes_task3_gate_current(tmp_path: Path):
    """Removing the MCP validator or exact fingerprint/hash report must fail."""
    import open_pharma_plugins_campaign_studio as campaign_studio
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email", "banner"])
    assert "error" not in _result(render_email({"campaign_brief_id": brief_id}))
    assert "error" not in _result(render_banner({"campaign_brief_id": brief_id}))

    report = _result(validate_rendered({"campaign_brief_id": brief_id}))

    assert "validate_rendered_assets" in {tool["name"] for tool in campaign_studio.list_tools()}
    assert report["overall_pass"] is True
    assert [entry["path"] for entry in report["outputs"]] == sorted(entry["path"] for entry in report["outputs"])
    assert rendered_validation_gate_state(brief_id)["status"] == "current"
    persisted = load_artifact(brief_id, "campaign-brief.json")
    assert persisted["campaign_brief_id"] == brief_id


def test_embedded_logo_decodes_to_the_selected_bytes(tmp_path: Path):
    """Substituting a path or wrong data URI for the sealed logo must fail."""
    brief_id, kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    html = Path(_result(render_email({"campaign_brief_id": brief_id}))["file_path"]).read_text()
    marker = "data:image/svg+xml;base64,"
    encoded = html.split(marker, 1)[1].split('"', 1)[0]
    assert base64.b64decode(encoded) == (kit / "logo.svg").read_bytes()


@pytest.mark.parametrize(
    ("dimensions", "profile"),
    [("728x90", "horizontal"), ("300x250", "rectangle"), ("300x300", "rectangle"), ("160x600", "skyscraper")],
)
def test_banner_profiles_render_exact_geometry_and_pass_actual_inspection(
    tmp_path: Path, dimensions: str, profile: str
):
    """Breaking any supported profile geometry or role fit must fail."""
    from open_pharma_plugins_campaign_studio._render_validation import inspect_svg, load_render_context

    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        brief_id=f"profile-{dimensions}",
        channels=["banner"],
        asset_dimensions={"banner": dimensions},
    )
    result = _result(render_banner({"campaign_brief_id": brief_id, "dimensions": dimensions}))
    width, height = (int(value) for value in dimensions.split("x"))
    payload = Path(result["file_path"]).read_bytes()

    assert result["profile"] == profile
    assert inspect_svg(payload, load_render_context(brief_id, "banner"), (width, height))["errors"] == []


@pytest.mark.parametrize(
    ("dimensions", "code"),
    [
        ("300X250", "invalid_dimensions"),
        ("0x90", "invalid_dimensions"),
        ("3000x3000", "dimensions_too_large"),
        ("100x100", "unsupported_dimensions"),
    ],
)
def test_banner_invalid_dimensions_are_structured_and_never_write(tmp_path: Path, dimensions: str, code: str):
    """Relaxing canonical, positive, bounded profile validation must fail."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["banner"])
    output = campaign_dir(brief_id) / "outputs" / "banner.svg"

    result = _result(render_banner({"campaign_brief_id": brief_id, "dimensions": dimensions}))

    assert result["error"]["code"] == code
    assert not output.exists()


def test_poster_default_equal_override_conflict_and_no_image_composition(tmp_path: Path):
    """Poster defaults and compatibility overrides remain exact without requiring product art."""
    from pypdf import PdfReader

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["poster"], include_product=False)
    first = _result(render_poster({"campaign_brief_id": brief_id}))
    output = Path(first["file_path"])
    before = output.read_bytes()
    equal = _result(render_poster({"campaign_brief_id": brief_id, "paper_size": "a4"}))
    conflict = _result(render_poster({"campaign_brief_id": brief_id, "paper_size": "LETTER"}))

    assert first["paper_size"] == equal["paper_size"] == "A4"
    assert PdfReader(output).pages[0]["/Resources"].get("/XObject", {}) == {}
    assert conflict["error"]["code"] == "dimension_conflict"
    assert output.read_bytes() == before


def test_banner_one_word_overflow_is_rejected_without_overwrite(tmp_path: Path):
    """Removing deterministic unbreakable-word protection must fail."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["banner"])
    output = Path(_result(render_banner({"campaign_brief_id": brief_id}))["file_path"])
    before = output.read_bytes()
    copy = load_artifact(brief_id, "copy-banner.json")
    copy["copy"]["headline"]["text"] = "X" * 100
    save_artifact(brief_id, "copy-banner.json", copy)
    _refresh_gate(brief_id)

    result = _result(render_banner({"campaign_brief_id": brief_id}))

    assert result["error"]["code"] == "banner_text_overflow"
    assert output.read_bytes() == before


def test_poster_long_copy_is_rejected_without_overwrite(tmp_path: Path):
    """Removing poster pre-measurement must allow this test to overwrite the prior one-page output."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["poster"], include_product=False)
    output = Path(_result(render_poster({"campaign_brief_id": brief_id}))["file_path"])
    before = output.read_bytes()
    copy = load_artifact(brief_id, "copy-poster.json")
    copy["copy"]["body"] = [_copy_block("Approved safety evidence " * 800, "c-006")]
    save_artifact(brief_id, "copy-poster.json", copy)
    _refresh_gate(brief_id)

    result = _result(render_poster({"campaign_brief_id": brief_id}))

    assert result["error"]["code"] == "poster_text_overflow"
    assert output.read_bytes() == before


def test_email_escapes_hostile_copy_and_has_only_the_approved_network_url(tmp_path: Path):
    """Removing autoescape or the single-CTA URL boundary must fail."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    copy = load_artifact(brief_id, "copy-email.json")
    hostile = '<img src="https://evil.example/x" onerror="alert(1)">'
    copy["copy"]["headline"]["text"] = hostile
    save_artifact(brief_id, "copy-email.json", copy)
    _refresh_gate(brief_id)

    result = _result(render_email({"campaign_brief_id": brief_id}))
    html = Path(result["file_path"]).read_text(encoding="utf-8")

    assert hostile not in html
    assert "&lt;img" in html
    assert html.count('href="https://') == 1
    assert 'src="https://evil.example' not in html


def _safe_custom_email_template() -> str:
    return """<!doctype html><html><head><title data-role="subject">{{ subject }}</title></head><body>
<div data-role="preheader" style="display:none">{{ preheader }}</div><table><tbody><tr><td>
<img src="{{ logo_data_uri }}" alt="{{ brand }} logo" />
<h1 data-role="headline">{{ headline }}</h1>
<p data-role="body-0">{{ body_paragraphs[0] }}</p><p data-role="body-1">{{ body_paragraphs[1] }}</p>
<a data-role="cta" href="{{ cta_url }}">{{ cta }}</a>
<p data-role="legal-isi">{{ legal_isi }}</p>
<p data-role="legal-pi_ref">{{ remaining_legal[0]['value'] }}</p>
<p data-role="legal-reporting_statement">{{ remaining_legal[1]['value'] }}</p>
</td></tr></tbody></table></body></html>"""


def test_safe_custom_email_template_records_hash_and_passes(tmp_path: Path):
    """Breaking the bounded primitive-only custom-template path must fail."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "safe-email.html.j2"
    template.write_text(_safe_custom_email_template(), encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert "error" not in result
    template_state = template.stat()
    assert result["template"] == {
        "kind": "custom",
        "path": str(template.resolve()),
        "sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
        "size": len(template.read_bytes()),
        "identity": {
            "device": template_state.st_dev,
            "inode": template_state.st_ino,
            "mode": template_state.st_mode & 0o170000,
        },
    }
    assert load_artifact(brief_id, "render-provenance-email.json")["template"] == result["template"]


@pytest.mark.parametrize(
    "unsafe_source",
    [
        "{% include 'other' %}",
        "{% import 'other' as x %}",
        "{{ brand.upper() }}",
        "{{ brand.__class__ }}",
        "{{ missing_value }}",
        _safe_custom_email_template().replace("</td>", "Unapproved sales text</td>"),
        _safe_custom_email_template().replace('data-role="headline"', 'data-role="missing-headline"'),
        _safe_custom_email_template().replace("</body>", "<script>alert(1)</script></body>"),
        _safe_custom_email_template().replace("</body>", "<form></form></body>"),
        _safe_custom_email_template().replace("<table>", '<table onclick="alert(1)">'),
        _safe_custom_email_template().replace(
            "<head>", '<head><meta http-equiv="refresh" content="0;url=https://evil.example">'
        ),
        _safe_custom_email_template().replace("<head>", "<head><style>@import 'https://evil.example/x.css';</style>"),
        _safe_custom_email_template().replace("</body>", '<p aria-hidden="true">Guaranteed cure</p></body>'),
        _safe_custom_email_template().replace(
            'href="{{ cta_url }}"', 'href="javascript:alert(1)" href="{{ cta_url }}"'
        ),
        _safe_custom_email_template().replace(
            'href="{{ cta_url }}"', 'href="{{ cta_url }}" ping="https://evil.example/collect"'
        ),
        _safe_custom_email_template().replace(
            "</head>", r"<style>.leak{background:u\72 l(\68 ttps\3a \2f \2f evil.example/x)}</style></head>"
        ),
        _safe_custom_email_template().replace(
            "</body>", "<p>Important Safety Information</p><p>Important Safety Information</p></body>"
        ),
    ],
)
def test_custom_email_template_sandbox_rejects_code_active_content_and_role_drift(tmp_path: Path, unsafe_source: str):
    """Removing any sandbox, active-content, URL, or role check must fail without output."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "unsafe-email.html.j2"
    template.write_text(unsafe_source, encoding="utf-8")
    output = campaign_dir(brief_id) / "outputs" / "email.html"

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert "error" in result
    assert not output.exists()


def test_custom_template_symlink_directory_and_oversize_are_rejected(tmp_path: Path):
    """Following a symlink/non-file or unbounded template must fail before rendering."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    real = tmp_path / "real.html.j2"
    real.write_text(_safe_custom_email_template())
    symlink = tmp_path / "link.html.j2"
    symlink.symlink_to(real)
    directory = tmp_path / "template-dir"
    directory.mkdir()
    oversized = tmp_path / "oversized.html.j2"
    oversized.write_bytes(b"x" * 128_001)

    assert (
        _result(render_email({"campaign_brief_id": brief_id, "template": str(symlink)}))["error"]["code"]
        == "invalid_custom_template"
    )
    assert (
        _result(render_email({"campaign_brief_id": brief_id, "template": str(directory)}))["error"]["code"]
        == "invalid_custom_template"
    )
    assert (
        _result(render_email({"campaign_brief_id": brief_id, "template": str(oversized)}))["error"]["code"]
        == "invalid_custom_template"
    )


def test_renderers_require_current_gate_and_do_not_overwrite(tmp_path: Path):
    """Removing the Task3 pre-gate or fail-before-write behavior must fail."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    output = Path(_result(render_email({"campaign_brief_id": brief_id}))["file_path"])
    before = output.read_bytes()
    copy = load_artifact(brief_id, "copy-email.json")
    copy["copy"]["headline"]["text"] += " changed"
    save_artifact(brief_id, "copy-email.json", copy)

    result = _result(render_email({"campaign_brief_id": brief_id}))

    assert result["error"]["code"] == "pre_render_validation_not_current"
    assert output.read_bytes() == before


def _pdf_fixture(*, pages: int = 1, encrypted: bool = False) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _index in range(pages):
        writer.add_blank_page(width=200, height=300)
    if encrypted:
        writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_extractors_handle_visible_html_entities_and_nested_svg_text(tmp_path: Path):
    """Entity decoding, hidden exclusion, and nested SVG text are extraction contracts."""
    from open_pharma_plugins_campaign_studio._render_validation import extract_rendered_text

    html = tmp_path / "visible.html"
    html.write_text(
        """<!doctype html><html><head><title>Hidden title</title><style>.x{color:red}</style></head>
<body><div hidden>Hidden preheader</div><script>hidden()</script><p>Visible &amp; approved</p></body></html>""",
        encoding="utf-8",
    )
    svg = tmp_path / "nested.svg"
    svg.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg"><title>Accessible title</title>
<desc>Accessible description</desc><text>Evidence <tspan>nested</tspan></text></svg>""",
        encoding="utf-8",
    )

    assert extract_rendered_text(html, "html") == "Visible & approved"
    assert extract_rendered_text(svg, "svg") == "Accessible title Accessible description Evidence nested"


@pytest.mark.parametrize(
    ("kind", "payload", "format", "code"),
    [
        ("malformed-svg", b"<svg><", "svg", "invalid_rendered_svg"),
        ("not-html", b"plain text", "html", "format_mismatch"),
        ("not-pdf", b"<html></html>", "pdf", "format_mismatch"),
        ("malformed-pdf", b"%PDF-not-valid", "pdf", "invalid_rendered_pdf"),
    ],
)
def test_extractors_reject_malformed_and_mismatched_files(
    tmp_path: Path, kind: str, payload: bytes, format: str, code: str
):
    """A suffix or caller format hint cannot bypass actual parse checks."""
    from open_pharma_plugins_campaign_studio._render_validation import RenderContractError, extract_rendered_text

    path = tmp_path / kind
    path.write_bytes(payload)

    with pytest.raises(RenderContractError) as caught:
        extract_rendered_text(path, format)
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("pages", "encrypted", "code"),
    [(2, False, "poster_page_count"), (1, True, "encrypted_pdf")],
)
def test_pdf_extractor_accepts_one_page_and_rejects_multi_page_or_encrypted(
    tmp_path: Path, pages: int, encrypted: bool, code: str
):
    """Removing PDF page/encryption checks must fail this extractor boundary."""
    from open_pharma_plugins_campaign_studio._render_validation import RenderContractError, extract_rendered_text

    one_page = tmp_path / "one-page.pdf"
    one_page.write_bytes(_pdf_fixture())
    assert extract_rendered_text(one_page, "pdf") == ""

    rejected = tmp_path / "rejected.pdf"
    rejected.write_bytes(_pdf_fixture(pages=pages, encrypted=encrypted))
    with pytest.raises(RenderContractError) as caught:
        extract_rendered_text(rejected, "pdf")
    assert caught.value.code == code


def test_extractor_rejects_oversize_symlink_and_unsupported_format(tmp_path: Path):
    """Extraction is bounded to regular non-symlink files and three explicit formats."""
    from open_pharma_plugins_campaign_studio._render_validation import RenderContractError, extract_rendered_text

    oversized = tmp_path / "oversized.html"
    oversized.write_bytes(b"x" * 1_000_001)
    real = tmp_path / "real.html"
    real.write_text("<html><body>Visible</body></html>")
    symlink = tmp_path / "linked.html"
    symlink.symlink_to(real)

    with pytest.raises(RenderContractError) as too_large:
        extract_rendered_text(oversized, "html")
    assert too_large.value.code == "invalid_rendered_file"
    with pytest.raises(RenderContractError) as linked:
        extract_rendered_text(symlink, "html")
    assert linked.value.code == "invalid_rendered_file"
    with pytest.raises(RenderContractError) as unsupported:
        extract_rendered_text(real, "txt")
    assert unsupported.value.code == "unsupported_rendered_format"


def test_rendered_validator_persists_current_failed_reports_for_missing_and_corrupt_outputs(tmp_path: Path):
    """Current inputs must leave actionable failed evidence for every missing/corrupt output."""
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email", "banner"])

    missing = _result(validate_rendered({"campaign_brief_id": brief_id}))
    assert missing["overall_pass"] is False
    assert set(missing["channel_results"]) == {"email", "banner"}
    assert all(result["overall_pass"] is False for result in missing["channel_results"].values())
    assert load_validation_artifact(brief_id, "rendered-assets.json") == missing
    assert rendered_validation_gate_state(brief_id)["status"] == "failed"

    email = Path(_result(render_email({"campaign_brief_id": brief_id}))["file_path"])
    assert "error" not in _result(render_banner({"campaign_brief_id": brief_id}))
    email.write_text("<html><body>corrupt</body></html>", encoding="utf-8")
    corrupt = _result(validate_rendered({"campaign_brief_id": brief_id}))
    assert corrupt["overall_pass"] is False
    assert corrupt["channel_results"]["email"]["overall_pass"] is False
    assert corrupt["channel_results"]["banner"]["overall_pass"] is True
    assert load_validation_artifact(brief_id, "rendered-assets.json") == corrupt


def test_rendered_validator_seals_exact_all_channel_outputs_and_mutation_becomes_stale(tmp_path: Path):
    """A passing report is exact, deterministic, and immediately invalidated by output byte drift."""
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path)
    rendered = [
        _result(render_email({"campaign_brief_id": brief_id})),
        _result(render_banner({"campaign_brief_id": brief_id})),
        _result(render_poster({"campaign_brief_id": brief_id})),
    ]
    assert all("error" not in result for result in rendered)

    report = _result(validate_rendered({"campaign_brief_id": brief_id}))
    assert set(report) == {
        "campaign_brief_id",
        "overall_pass",
        "validated_at",
        "pre_render_input_fingerprint",
        "channel_results",
        "outputs",
        "template_sources",
    }
    assert report["validated_at"].endswith("+00:00")
    assert report["template_sources"] == []
    assert report["overall_pass"] is True
    assert report["pre_render_input_fingerprint"] == validation_input_fingerprint(
        brief_id, ["email", "banner", "poster"]
    )
    expected_paths = sorted(str(Path(item["file_path"]).resolve()) for item in rendered)
    assert [item["path"] for item in report["outputs"]] == expected_paths
    for item in report["outputs"]:
        path = Path(item["path"])
        assert path.is_absolute() and path.is_file()
        assert item["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert type(item["size"]) is int and item["size"] == path.stat().st_size
    assert rendered_validation_gate_state(brief_id)["status"] == "current"

    Path(rendered[0]["file_path"]).write_text("changed", encoding="utf-8")
    changed = rendered_validation_gate_state(brief_id)
    assert changed["status"] == "stale"
    assert changed["code"] == "rendered_output_changed"


def test_stale_pre_render_gate_does_not_overwrite_prior_rendered_evidence(tmp_path: Path):
    """Missing/stale/failed pre-gates can never replace an older rendered report."""
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    previous = {"sentinel": "preserve exact prior evidence"}
    save_validation_artifact(brief_id, "rendered-assets.json", previous)
    copy = load_artifact(brief_id, "copy-email.json")
    copy["copy"]["headline"]["text"] += " changed after policy validation"
    save_artifact(brief_id, "copy-email.json", copy)

    result = _result(validate_rendered({"campaign_brief_id": brief_id}))

    assert result["error"]["code"] == "pre_render_validation_not_current"
    assert load_validation_artifact(brief_id, "rendered-assets.json") == previous


def test_rendered_validator_unsafe_id_and_output_symlink_are_structured_without_output_write(tmp_path: Path):
    """Unsafe IDs and symlinked outputs stay ordinary JSON failures and never follow the link."""
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    store = tmp_path / "store"
    unsafe = _result(validate_rendered({"campaign_brief_id": "../escape"}))
    assert "error" in unsafe
    assert not store.exists()

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    output = Path(_result(render_email({"campaign_brief_id": brief_id}))["file_path"])
    outside = tmp_path / "outside.html"
    outside.write_text("do not touch", encoding="utf-8")
    output.unlink()
    output.symlink_to(outside)

    report = _result(validate_rendered({"campaign_brief_id": brief_id}))
    assert report["overall_pass"] is False
    assert report["channel_results"]["email"]["overall_pass"] is False
    assert outside.read_text(encoding="utf-8") == "do not touch"


def test_validator_mcp_schema_discovery_success_and_single_argument_model(tmp_path: Path):
    """Real stdio discovery/calls and the Pydantic boundary expose only the campaign ID."""
    from pydantic import ValidationError

    import open_pharma_plugins_campaign_studio as campaign_studio
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import ValidateRenderedAssetsArgs

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    assert "error" not in _result(render_email({"campaign_brief_id": brief_id}))
    schemas = {tool["name"]: tool["inputSchema"] for tool in campaign_studio.list_tools()}
    assert set(schemas["validate_rendered_assets"]["properties"]) == {"campaign_brief_id"}
    assert schemas["validate_rendered_assets"]["required"] == ["campaign_brief_id"]
    assert schemas["validate_rendered_assets"]["additionalProperties"] is False
    assert set(schemas["render_email"]["properties"]) == {"campaign_brief_id", "template"}
    assert set(schemas["render_banner"]["properties"]) == {"campaign_brief_id", "dimensions"}
    assert set(schemas["render_poster"]["properties"]) == {"campaign_brief_id", "paper_size"}

    with pytest.raises(ValidationError):
        ValidateRenderedAssetsArgs.model_validate({"campaign_brief_id": brief_id, "unexpected": True})

    async def exercise() -> tuple[object, object]:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "open_pharma_plugins_campaign_studio"],
            env={**os.environ, "OPEN_PHARMA_CAMPAIGN_STORE_DIR": str(campaign_dir(brief_id).parents[1])},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                valid = await session.call_tool("validate_rendered_assets", {"campaign_brief_id": brief_id})
                return listed, valid

    listed, valid = anyio.run(exercise)
    assert "validate_rendered_assets" in {tool.name for tool in listed.tools}
    assert valid.isError is False
    assert json.loads(valid.content[0].text)["overall_pass"] is True


def test_banner_renders_optional_approved_subheadline_in_its_own_role(tmp_path: Path):
    """An optional persisted banner subheadline is approved copy, not disposable decoration."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["banner"])
    copy = load_artifact(brief_id, "copy-banner.json")
    copy["copy"]["sub_headline"] = _copy_block("A distinct approved evidence statement.", "c-004")
    save_artifact(brief_id, "copy-banner.json", copy)
    _refresh_gate(brief_id)

    result = _result(render_banner({"campaign_brief_id": brief_id}))

    assert "error" not in result
    svg = Path(result["file_path"]).read_text(encoding="utf-8")
    from open_pharma_plugins_campaign_studio._render_validation import extract_rendered_text

    assert extract_rendered_text(Path(result["file_path"]), "svg").count("A distinct approved evidence statement.") == 1
    assert 'data-role="sub_headline"' in svg


@pytest.mark.parametrize(
    "unsafe_source",
    [
        _safe_custom_email_template().replace('style="display:none"', ""),
        _safe_custom_email_template().replace(' alt="{{ brand }} logo"', ""),
    ],
)
def test_custom_email_requires_hidden_preheader_and_meaningful_logo_alt(tmp_path: Path, unsafe_source: str):
    """A role label alone cannot substitute for hidden preview semantics or accessible logo text."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "semantically-unsafe.html.j2"
    template.write_text(unsafe_source, encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] == "rendered_email_invalid"


def test_custom_email_rejects_wrong_data_uri_mime_even_for_selected_logo_bytes(tmp_path: Path):
    """Matching bytes under a false image MIME cannot satisfy the embedded-asset contract."""
    brief_id, kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    encoded = base64.b64encode((kit / "logo.svg").read_bytes()).decode("ascii")
    source = _safe_custom_email_template().replace("{{ logo_data_uri }}", f"data:image/png;base64,{encoded}")
    template = tmp_path / "wrong-mime.html.j2"
    template.write_text(source, encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}


def test_email_prohibited_scan_includes_hidden_subject_and_preheader_roles(tmp_path: Path):
    """Moving prohibited language to metadata or preview text must not hide it from final inspection."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    copy = load_artifact(brief_id, "copy-email.json")
    copy["copy"]["subject"]["text"] = "Guaranteed cure"
    save_artifact(brief_id, "copy-email.json", copy)
    result = _result(render_email({"campaign_brief_id": brief_id}))

    assert result["error"]["code"] == "pre_render_validation_not_current"
    assert not (campaign_dir(brief_id) / "outputs" / "email.html").exists()


def test_rendered_validator_rejects_unapproved_unroled_svg_text(tmp_path: Path):
    """Expected role text can remain intact while extra visible marketing copy makes the file fail."""
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["banner"])
    output = Path(_result(render_banner({"campaign_brief_id": brief_id}))["file_path"])
    output.write_text(
        output.read_text(encoding="utf-8").replace(
            "</svg>", '<text x="5" y="5">Invented unapproved benefit</text></svg>'
        ),
        encoding="utf-8",
    )

    report = _result(validate_rendered({"campaign_brief_id": brief_id}))

    assert report["overall_pass"] is False
    assert report["channel_results"]["banner"]["overall_pass"] is False


def test_rendered_report_uses_stable_check_names_for_present_and_missing_outputs(tmp_path: Path):
    """Operators need the same deterministic checks even when an output is absent."""
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email", "banner"])
    assert "error" not in _result(render_email({"campaign_brief_id": brief_id}))

    report = _result(validate_rendered({"campaign_brief_id": brief_id}))
    expected = ["output_exists", "rendered_contract", "prohibited_language"]

    assert [item["check_name"] for item in report["channel_results"]["email"]["checks"]] == expected
    assert [item["check_name"] for item in report["channel_results"]["banner"]["checks"]] == expected


def test_rendered_validator_rejects_extra_unapproved_pdf_text(tmp_path: Path):
    """A PDF cannot pass merely by retaining every expected phrase alongside invented copy."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen.canvas import Canvas

    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["poster"])
    output = Path(_result(render_poster({"campaign_brief_id": brief_id}))["file_path"])
    original = PdfReader(output)
    page = original.pages[0]
    overlay_buffer = io.BytesIO()
    overlay = Canvas(overlay_buffer, pagesize=(float(page.mediabox.width), float(page.mediabox.height)), invariant=1)
    overlay.drawString(50, 320, "Invented unapproved benefit")
    overlay.showPage()
    overlay.save()
    page.merge_page(PdfReader(io.BytesIO(overlay_buffer.getvalue())).pages[0])
    writer = PdfWriter()
    writer.add_page(page)
    with output.open("wb") as stream:
        writer.write(stream)

    report = _result(validate_rendered({"campaign_brief_id": brief_id}))

    assert report["overall_pass"] is False
    assert report["channel_results"]["poster"]["overall_pass"] is False


def test_validator_detects_same_byte_inode_swap_and_persists_failed_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A same-byte replacement during inspection cannot be sealed under the prior file identity."""
    import open_pharma_plugins_campaign_studio._render_validation as validation
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    output = Path(_result(render_email({"campaign_brief_id": brief_id}))["file_path"])
    previous = {"sentinel": "preserve"}
    save_validation_artifact(brief_id, "rendered-assets.json", previous)
    original_snapshot = validation._regular_snapshot
    calls = 0

    def swap_after_first_read(path: Path, *, limit: int, code: str):
        nonlocal calls
        snapshot = original_snapshot(path, limit=limit, code=code)
        if path.name == output.name and path.parent.name == "outputs" and calls == 0:
            replacement = path.with_name("same-byte-replacement.html")
            replacement.write_bytes(snapshot[0])
            os.replace(replacement, path)
            calls += 1
        return snapshot

    monkeypatch.setattr(validation, "_regular_snapshot", swap_after_first_read)
    result = _result(validate_rendered({"campaign_brief_id": brief_id}))

    assert result["overall_pass"] is False
    assert "changed during validation" in result["channel_results"]["email"]["checks"][1]["detail"]
    assert load_validation_artifact(brief_id, "rendered-assets.json") == result


@pytest.mark.parametrize(
    "mutation",
    [
        "aria-hidden-prohibited",
        "duplicate-href",
        "external-ping",
        "escaped-css-url",
        "duplicate-structural-text",
    ],
)
def test_rendered_validator_rejects_html_authored_text_and_reference_bypasses(tmp_path: Path, mutation: str):
    """Removing authored-text, duplicate-attribute, or decoded-reference checks must fail."""
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    output = Path(_result(render_email({"campaign_brief_id": brief_id}))["file_path"])
    html = output.read_text(encoding="utf-8")
    cta = "https://oncorix-hcp.example.com/evidence"
    if mutation == "aria-hidden-prohibited":
        html = html.replace("</body>", '<p aria-hidden="true">Guaranteed cure</p></body>')
    elif mutation == "duplicate-href":
        html = html.replace(f'href="{cta}"', f'href="javascript:alert(1)" href="{cta}"', 1)
    elif mutation == "external-ping":
        html = html.replace(f'href="{cta}"', f'href="{cta}" ping="https://evil.example/collect"', 1)
    elif mutation == "escaped-css-url":
        escaped = r"<style>.leak{background:u\72 l(\68 ttps\3a \2f \2f evil.example/x)}</style>"
        html = html.replace("</head>", f"{escaped}</head>")
    else:
        html = html.replace("</body>", "<p>Important Safety Information</p></body>")
    output.write_text(html, encoding="utf-8")

    report = _result(validate_rendered({"campaign_brief_id": brief_id}))

    assert report["overall_pass"] is False
    assert report["channel_results"]["email"]["overall_pass"] is False


@pytest.mark.parametrize(
    "mutation",
    ["extra-description", "internal-entity", "local-use", "escaped-css-url"],
)
def test_rendered_validator_rejects_svg_accessibility_entity_and_reference_bypasses(tmp_path: Path, mutation: str):
    """Removing exact SVG accessibility or active-reference checks must fail."""
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["banner"])
    output = Path(_result(render_banner({"campaign_brief_id": brief_id}))["file_path"])
    svg = output.read_text(encoding="utf-8")
    if mutation == "extra-description":
        svg = svg.replace("</svg>", "<desc>Invented efficacy narrative</desc></svg>")
    elif mutation == "internal-entity":
        svg = svg.replace("<svg ", '<!DOCTYPE svg [<!ENTITY invented "Invented efficacy narrative">]><svg ', 1).replace(
            "</svg>", "<desc>&invented;</desc></svg>"
        )
    elif mutation == "local-use":
        svg = svg.replace('data-role="headline"', 'id="approved-headline" data-role="headline"', 1)
        svg = svg.replace("</svg>", '<use href="#approved-headline" x="0" y="20"/></svg>')
    else:
        escaped = r"<style>.leak{background:u\72 l(\68 ttps\3a \2f \2f evil.example/x)}</style>"
        svg = svg.replace("</svg>", f"{escaped}</svg>")
    output.write_text(svg, encoding="utf-8")

    report = _result(validate_rendered({"campaign_brief_id": brief_id}))

    assert report["overall_pass"] is False
    assert report["channel_results"]["banner"]["overall_pass"] is False


def test_rendered_validator_rejects_visible_raster_overlay_with_unchanged_pdf_text(tmp_path: Path):
    """Binding only extracted PDF text and a minimum image count must fail this raster-overlay attack."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas

    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, kit, _copies = _seed_campaign(tmp_path, channels=["poster"])
    output = Path(_result(render_poster({"campaign_brief_id": brief_id}))["file_path"])
    original = PdfReader(output)
    page = original.pages[0]
    overlay_buffer = io.BytesIO()
    overlay = Canvas(overlay_buffer, pagesize=(float(page.mediabox.width), float(page.mediabox.height)), invariant=1)
    overlay.drawImage(ImageReader(kit / "product.png"), 40, 320, width=500, height=140, preserveAspectRatio=False)
    overlay.showPage()
    overlay.save()
    page.merge_page(PdfReader(io.BytesIO(overlay_buffer.getvalue())).pages[0])
    writer = PdfWriter()
    writer.add_page(page)
    with output.open("wb") as stream:
        writer.write(stream)

    report = _result(validate_rendered({"campaign_brief_id": brief_id}))

    assert report["overall_pass"] is False
    assert report["channel_results"]["poster"]["overall_pass"] is False


def test_banner_rejects_undocumented_profile_even_when_ratio_matches(tmp_path: Path):
    """Replacing the exact four-profile allowlist with broad ratio matching must fail."""
    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        channels=["banner"],
        asset_dimensions={"banner": "320x50"},
    )
    output = campaign_dir(brief_id) / "outputs" / "banner.svg"

    result = _result(render_banner({"campaign_brief_id": brief_id}))

    assert result["error"]["code"] == "unsupported_dimensions"
    assert not output.exists()


@pytest.mark.parametrize("headline", ["W" * 25])
def test_banner_uses_glyph_width_not_character_count_for_headline_fit(tmp_path: Path, headline: str):
    """A nominally short wide-glyph headline cannot be emitted beyond the 300x250 safe zone."""
    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        channels=["banner"],
        asset_dimensions={"banner": "300x250"},
    )
    copy = load_artifact(brief_id, "copy-banner.json")
    copy["copy"]["headline"]["text"] = headline
    save_artifact(brief_id, "copy-banner.json", copy)
    _refresh_gate(brief_id)

    result = _result(render_banner({"campaign_brief_id": brief_id}))

    assert result["error"]["code"] == "banner_text_overflow"


def test_email_provenance_write_failure_preserves_prior_output_and_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Writing the output before a failed provenance write must not split the email transaction."""
    import open_pharma_plugins_campaign_studio._campaign_store as store

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    first = _result(render_email({"campaign_brief_id": brief_id}))
    output = Path(first["file_path"])
    provenance_path = campaign_dir(brief_id) / "render-provenance-email.json"
    before_output = output.read_bytes()
    before_provenance = provenance_path.read_bytes()
    template = tmp_path / "safe-email.html.j2"
    template.write_text(_safe_custom_email_template(), encoding="utf-8")

    original_save_artifact = store.save_artifact

    def fail_provenance(*args, **kwargs):
        original_save_artifact(*args, **kwargs)
        raise OSError("injected provenance persistence failure")

    monkeypatch.setattr(store, "save_artifact", fail_provenance)
    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] == "render_failed"
    assert output.read_bytes() == before_output
    assert provenance_path.read_bytes() == before_provenance


def test_custom_email_validation_binds_output_to_unchanged_template_provenance(tmp_path: Path):
    """A custom output cannot stay current after its recorded template source changes."""
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "safe-email.html.j2"
    template.write_text(_safe_custom_email_template(), encoding="utf-8")
    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))
    assert "error" not in result
    template.write_text(_safe_custom_email_template() + "\n", encoding="utf-8")

    report = _result(validate_rendered({"campaign_brief_id": brief_id}))

    assert report["overall_pass"] is False
    assert report["channel_results"]["email"]["overall_pass"] is False


@pytest.mark.parametrize(
    "comment",
    [
        '<!--[if mso]><img src="https://evil.example/track"><p>Guaranteed cure</p><![endif]-->',
        "<!--[ IF  MSO ]><p>Guaranteed cure</p><![ ENDIF ]-->",
        "<!--[if !mso]><!--><div></div><!--<![endif]-->",
        "<!--[ IF  !mso ]><!--><div></div><!--<![ ENDIF ]-->",
        '<!-- <img src="https://evil.example/track"> -->',
    ],
)
def test_custom_email_rejects_conditional_and_markup_bearing_comments(tmp_path: Path, comment: str):
    """Removing custom-source comment checks must admit Outlook-only or hidden active markup."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "conditional-comment.html.j2"
    template.write_text(_safe_custom_email_template().replace("</body>", f"{comment}</body>"), encoding="utf-8")
    output = campaign_dir(brief_id) / "outputs" / "email.html"

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}
    assert not output.exists()


def test_actual_html_inspector_rejects_conditional_comment_markup(tmp_path: Path):
    """Removing comment handling from actual-file inspection must expose Outlook-only content."""
    from open_pharma_plugins_campaign_studio._render_validation import inspect_html, load_render_context

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    output = Path(_result(render_email({"campaign_brief_id": brief_id}))["file_path"])
    payload = output.read_text(encoding="utf-8").replace(
        "</body>",
        '<!--[if mso]><img src="https://evil.example/track"><p>Guaranteed cure</p><![endif]--></body>',
    )

    inspection = inspect_html(payload.encode("utf-8"), load_render_context(brief_id, "email"))

    assert any("comment" in error.casefold() for error in inspection["errors"])


@pytest.mark.parametrize(
    "css",
    [
        'a::after{content:" Guaranteed cure"}',
        'a::before{content:" "}',
        '.hero{background:image-set("data:image/svg+xml;base64,PHN2Zz4=" 1x)}',
        '.hero{background:-webkit-image-set("data:image/svg+xml;base64,PHN2Zz4=" 1x)}',
        "@font-face{font-family:Hidden;src:data:font/woff2;base64,d09GMg==}",
        'a::after{c/**/ontent:" Guaranteed cure"}',
        r'.hero{background:\69 mage-set("d\61 ta:image/svg+xml;base64,PHN2Zz4=" 1x)}',
        '.hero{background:i\x00mage-set("data:image/svg+xml;base64,PHN2Zz4=" 1x)}',
    ],
)
def test_custom_email_rejects_generated_content_and_css_asset_vectors(tmp_path: Path, css: str):
    """Removing decoded generated-content/resource checks must admit browser-rendered content or assets."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "unsafe-css.html.j2"
    source = _safe_custom_email_template().replace("</head>", f"<style>{css}</style></head>")
    template.write_text(source, encoding="utf-8")
    output = campaign_dir(brief_id) / "outputs" / "email.html"

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}
    assert not output.exists()


@pytest.mark.parametrize(
    "fragment",
    [
        '<input value="Guaranteed cure">',
        '<input placeholder="Guaranteed cure">',
        '<div title="Guaranteed cure"></div>',
        '<div aria-label="Guaranteed cure"></div>',
        '<div aria-description="Guaranteed cure"></div>',
        '<div alt="Guaranteed cure"></div>',
        "<title>Guaranteed cure</title>",
    ],
)
def test_custom_email_rejects_form_and_unapproved_accessible_attribute_content(tmp_path: Path, fragment: str):
    """Removing form/accessibility attribute semantics must admit unapproved browser or assistive text."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "unsafe-accessibility.html.j2"
    template.write_text(_safe_custom_email_template().replace("</body>", f"{fragment}</body>"), encoding="utf-8")
    output = campaign_dir(brief_id) / "outputs" / "email.html"

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}
    assert not output.exists()


def test_custom_email_rejects_split_duplicate_expected_role_elements(tmp_path: Path):
    """Removing expected-role element cardinality must admit a split duplicate headline role."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "split-role.html.j2"
    split_headline = (
        '<h1 data-role="headline">Overall response rate was 42.3% with ONCORIX</h1>'
        '<p data-role="headline">versus 16.2% with control (p&lt;0.001).</p>'
    )
    source = _safe_custom_email_template().replace('<h1 data-role="headline">{{ headline }}</h1>', split_headline)
    template.write_text(source, encoding="utf-8")
    output = campaign_dir(brief_id) / "outputs" / "email.html"

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] == "rendered_email_invalid"
    assert not output.exists()


def test_svg_inspector_rejects_split_duplicate_expected_role_elements(tmp_path: Path):
    """Removing SVG role cardinality must admit an extra element whose aggregated text is unchanged."""
    from open_pharma_plugins_campaign_studio._render_validation import inspect_svg, load_render_context

    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        channels=["banner"],
        asset_dimensions={"banner": "300x250"},
    )
    output = Path(_result(render_banner({"campaign_brief_id": brief_id}))["file_path"])
    source = output.read_text(encoding="utf-8")
    target = ">Among responders, 68%</text>"
    replacement = (
        ">Among responders,</text>"
        '<text data-role="headline" x="84" y="32" fill="#003B5C" '
        'font-family="Arial, Helvetica, sans-serif" font-size="13" font-weight="bold">68%</text>'
    )
    mutated = source.replace(target, replacement, 1)
    assert mutated != source

    inspection = inspect_svg(mutated.encode("utf-8"), load_render_context(brief_id, "banner"), (300, 250))

    assert any("cardinality" in error.casefold() for error in inspection["errors"])


@pytest.mark.parametrize(
    ("heading_family", "headline", "expect_overflow"),
    [
        ("Arial, Helvetica, sans-serif", "i" * 30, False),
        ("Times New Roman, Times, serif", "i" * 30, False),
        ("Courier New, Courier, monospace", "i" * 30, True),
        ("Arial, Helvetica, sans-serif", "W" * 18, True),
        ("Times New Roman, Times, serif", "W" * 18, True),
        ("Courier New, Courier, monospace", "W" * 26, True),
    ],
)
def test_banner_fit_uses_selected_brand_font_metrics(
    tmp_path: Path, heading_family: str, headline: str, expect_overflow: bool
):
    """Replacing selected-font metrics with one Helvetica proxy must misclassify these safe-zone boundaries."""
    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        channels=["banner"],
        asset_dimensions={"banner": "300x250"},
        heading_family=heading_family,
    )
    copy = load_artifact(brief_id, "copy-banner.json")
    copy["copy"]["headline"]["text"] = headline
    save_artifact(brief_id, "copy-banner.json", copy)
    _refresh_gate(brief_id)

    result = _result(render_banner({"campaign_brief_id": brief_id}))

    if expect_overflow:
        assert result["error"]["code"] == "banner_text_overflow"
    else:
        assert "error" not in result


@pytest.mark.parametrize(
    "fragment",
    [
        '<svg><animate attributeName="href" to="https://evil.example/x" values="x;https://evil.example/y"/></svg>',
        '<SVG><SET attributeName="href" to="https://evil.example/x"></SET></SVG>',
        '<svg:svg xmlns:svg="http://www.w3.org/2000/svg"><svg:animate to="https://evil.example/x"/></svg:svg>',
        "<math><mtext></mtext></math>",
        "<marquee></marquee>",
        "<blink></blink>",
        "<template><table><tr><td></td></tr></table></template>",
    ],
)
def test_custom_email_rejects_inline_namespaced_inert_and_legacy_markup(tmp_path: Path, fragment: str):
    """Removing the email element boundary must admit SVG/SMIL, MathML, inert, or legacy markup."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "unsafe-markup.html.j2"
    template.write_text(_safe_custom_email_template().replace("</body>", f"{fragment}</body>"), encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}
    assert not (campaign_dir(brief_id) / "outputs" / "email.html").exists()


@pytest.mark.parametrize(
    "css",
    [
        "@keyframes pulse{from{opacity:1}to{opacity:0}}.cta{animation:pulse 1s}",
        "@-webkit-keyframes pulse{from{opacity:1}to{opacity:0}}",
        ".cta{transition:all 1s}",
        r"@\6b eyframes pulse{from{opacity:1}to{opacity:0}}",
        ".cta{ani/**/mation-name:pulse}",
    ],
)
def test_custom_email_rejects_css_animation_and_transition(tmp_path: Path, css: str):
    """Removing decoded motion checks must admit browser-active styling."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "unsafe-motion.html.j2"
    template.write_text(
        _safe_custom_email_template().replace("</head>", f"<style>{css}</style></head>"), encoding="utf-8"
    )

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}


@pytest.mark.parametrize(
    "mutation",
    [
        "hidden",
        "display-none",
        "transparent",
        "zero-font",
        "aria-hidden",
        "mso-hide",
        "opacity-ancestor",
        "stylesheet-selector",
        "template-ancestor",
        "off-canvas",
        "clip",
        "scale-zero",
    ],
)
def test_custom_email_requires_every_non_preheader_role_to_be_visibly_accessible(tmp_path: Path, mutation: str):
    """Removing visibility/accessibility checks must let required legal text exist only inertly or invisibly."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    source = _safe_custom_email_template()
    target = '<p data-role="legal-isi">{{ legal_isi }}</p>'
    replacements = {
        "hidden": '<p hidden data-role="legal-isi">{{ legal_isi }}</p>',
        "display-none": '<p data-role="legal-isi" style="display:none">{{ legal_isi }}</p>',
        "transparent": '<p data-role="legal-isi" style="color:transparent">{{ legal_isi }}</p>',
        "zero-font": '<p data-role="legal-isi" style="font-size:0">{{ legal_isi }}</p>',
        "aria-hidden": '<p data-role="legal-isi" aria-hidden="true">{{ legal_isi }}</p>',
        "mso-hide": '<p data-role="legal-isi" style="mso-hide:all">{{ legal_isi }}</p>',
        "opacity-ancestor": f'<div style="opacity:0">{target}</div>',
        "stylesheet-selector": target,
        "template-ancestor": f"<template>{target}</template>",
        "off-canvas": '<p data-role="legal-isi" style="position:absolute;left:-9999px">{{ legal_isi }}</p>',
        "clip": '<p data-role="legal-isi" style="clip-path:inset(100%)">{{ legal_isi }}</p>',
        "scale-zero": '<p data-role="legal-isi" style="transform:scale(0)">{{ legal_isi }}</p>',
    }
    source = source.replace(target, replacements[mutation])
    if mutation == "stylesheet-selector":
        source = source.replace("</head>", '<style>[data-role="legal-isi"]{display:none}</style></head>')
    template = tmp_path / "hidden-role.html.j2"
    template.write_text(source, encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}


def test_custom_email_requires_subject_title_inside_actual_head(tmp_path: Path):
    """Treating any hidden ancestor as head must allow an approved title in inert body markup."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    source = _safe_custom_email_template().replace(
        '<head><title data-role="subject">{{ subject }}</title></head>',
        '<head></head><div hidden><title data-role="subject">{{ subject }}</title></div>',
    )
    template = tmp_path / "misplaced-title.html.j2"
    template.write_text(source, encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}


@pytest.mark.parametrize(
    "css",
    [
        "li{list-style-type:'Guaranteed cure'}",
        "@counter-style leak{system:cyclic;symbols:'Guaranteed cure';suffix:' '}li{list-style:leak}",
        r"li{l\69 st-style-type:'Guaranteed cure'}",
        "li{list-/**/style:'Guaranteed cure'}",
    ],
)
def test_custom_email_rejects_css_marker_and_counter_text(tmp_path: Path, css: str):
    """Removing marker/counter checks must admit unapproved browser-generated list text."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "unsafe-marker.html.j2"
    template.write_text(
        _safe_custom_email_template().replace("</head>", f"<style>{css}</style></head>"), encoding="utf-8"
    )

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}


def test_skyscraper_rejects_combined_headline_and_subheadline_geometry_before_write(tmp_path: Path):
    """Independent role line limits must not let the combined headline block overlap safety at y=230."""
    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        channels=["banner"],
        asset_dimensions={"banner": "160x600"},
    )
    copy = load_artifact(brief_id, "copy-banner.json")
    copy["copy"]["headline"]["text"] = " ".join(["MMMMMMMMMM"] * 5)
    copy["copy"]["sub_headline"] = _copy_block(" ".join(["MMMMMMMMMMMM"] * 2), "c-004")
    save_artifact(brief_id, "copy-banner.json", copy)
    _refresh_gate(brief_id)
    output = campaign_dir(brief_id) / "outputs" / "banner.svg"

    result = _result(render_banner({"campaign_brief_id": brief_id}))

    assert result["error"]["code"] == "banner_text_overflow"
    assert not output.exists()


@pytest.mark.parametrize("glyph", ["\u0478", "漢", "🙂"])
def test_banner_rejects_non_portable_english_glyphs_before_write(tmp_path: Path, glyph: str):
    """Proxy metrics must not admit Cyrillic, CJK, or emoji that the portable banner stack cannot guarantee."""
    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        channels=["banner"],
        asset_dimensions={"banner": "300x250"},
    )
    copy = load_artifact(brief_id, "copy-banner.json")
    copy["copy"]["headline"]["text"] = glyph * 8
    save_artifact(brief_id, "copy-banner.json", copy)
    _refresh_gate(brief_id)

    result = _result(render_banner({"campaign_brief_id": brief_id}))

    assert result["error"]["code"] == "unsupported_banner_glyph"


@pytest.mark.parametrize(
    ("dimensions", "heading_family"),
    [
        ("728x90", "Arial, Helvetica, sans-serif"),
        ("300x250", "Times New Roman, Arial, serif"),
        ("300x300", "Courier New, Arial, monospace"),
        ("160x600", "Unbundled Brand Font, Arial, sans-serif"),
    ],
)
def test_banner_accepts_portable_western_text_across_profiles_and_font_stacks(
    tmp_path: Path, dimensions: str, heading_family: str
):
    """Narrowing the repertoire or ignoring fallback tokens must not reject supported English/Western copy."""
    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        channels=["banner"],
        asset_dimensions={"banner": dimensions},
        heading_family=heading_family,
    )
    copy = load_artifact(brief_id, "copy-banner.json")
    copy["copy"]["headline"]["text"] = "Café – 42.3% evidence"
    copy["copy"]["cta"]["text"] = "Review"
    save_artifact(brief_id, "copy-banner.json", copy)
    brief = load_artifact(brief_id, "campaign-brief.json")
    brief["call_to_action"] = "Review"
    save_artifact(brief_id, "campaign-brief.json", brief)
    _refresh_gate(brief_id)

    result = _result(render_banner({"campaign_brief_id": brief_id}))

    assert "error" not in result


def test_custom_template_source_is_sealed_in_report_and_direct_status_and_mcp_gates(tmp_path: Path):
    """A custom template mutation after validation must immediately stale direct and MCP status gates."""
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle as status_handle
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "sealed-custom-email.html.j2"
    template.write_text(_safe_custom_email_template(), encoding="utf-8")
    assert "error" not in _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))
    report = _result(validate_rendered({"campaign_brief_id": brief_id}))
    assert report["overall_pass"] is True
    assert report["template_sources"] == [load_artifact(brief_id, "render-provenance-email.json")["template"]]
    assert rendered_validation_gate_state(brief_id)["status"] == "current"

    template.write_text(_safe_custom_email_template() + "\n", encoding="utf-8")
    assert rendered_validation_gate_state(brief_id)["code"] == "custom_template_changed"
    assert _result(status_handle({"campaign_brief_id": brief_id}))["rendered_validation"]["status"] == "stale"

    async def exercise() -> dict:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "open_pharma_plugins_campaign_studio"],
            env={**os.environ, "OPEN_PHARMA_CAMPAIGN_STORE_DIR": str(campaign_dir(brief_id).parents[1])},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.call_tool("get_campaign_status", {"campaign_brief_id": brief_id})
                return json.loads(response.content[0].text)

    mcp_status = anyio.run(exercise)
    assert mcp_status["rendered_validation"]["status"] == "stale"
    assert mcp_status["rendered_validation"]["code"] == "custom_template_changed"


@pytest.mark.parametrize("mutation", ["same-bytes-new-identity", "missing", "symlink", "directory"])
def test_custom_template_missing_unsafe_or_identity_replaced_stales_rendered_gate(tmp_path: Path, mutation: str):
    """Hash-only gate checks must not trust a missing, linked, nonregular, or identity-replaced source."""
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "sealed-custom-email.html.j2"
    payload = _safe_custom_email_template().encode()
    template.write_bytes(payload)
    assert "error" not in _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))
    assert _result(validate_rendered({"campaign_brief_id": brief_id}))["overall_pass"] is True

    if mutation == "same-bytes-new-identity":
        replacement = tmp_path / "replacement.html.j2"
        replacement.write_bytes(payload)
        os.replace(replacement, template)
    elif mutation == "missing":
        template.unlink()
    elif mutation == "symlink":
        target = tmp_path / "target.html.j2"
        target.write_bytes(payload)
        template.unlink()
        template.symlink_to(target)
    else:
        template.unlink()
        template.mkdir()

    gate = rendered_validation_gate_state(brief_id)

    assert gate["status"] in {"stale", "failed"}
    assert gate["code"] in {"custom_template_changed", "custom_template_unsafe"}


@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_custom_template_provenance_artifact_is_bound_to_rendered_gate(tmp_path: Path, mutation: str):
    """A report source cannot remain current after its persisted custom-template provenance drifts."""
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "sealed-custom-email.html.j2"
    template.write_text(_safe_custom_email_template(), encoding="utf-8")
    assert "error" not in _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))
    assert _result(validate_rendered({"campaign_brief_id": brief_id}))["overall_pass"] is True
    provenance_path = campaign_dir(brief_id) / "render-provenance-email.json"
    if mutation == "missing":
        provenance_path.unlink()
    else:
        envelope = load_artifact(brief_id, "render-provenance-email.json")
        envelope["template"]["sha256"] = "0" * 64
        save_artifact(brief_id, "render-provenance-email.json", envelope)

    gate = rendered_validation_gate_state(brief_id)

    assert gate["status"] in {"stale", "failed"}
    assert gate["code"] in {"custom_template_provenance_changed", "custom_template_provenance_unsafe"}


@pytest.mark.parametrize(
    "mutation",
    [
        "hidden-misnested-role",
        "body-reparented-title",
        "head-reparented-flow",
        "table-foster-parent",
    ],
)
def test_custom_email_requires_well_formed_tree_ancestry(tmp_path: Path, mutation: str):
    """HTML error recovery must not move a required role out of a hidden ancestor or title out of head."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    source = _safe_custom_email_template()
    if mutation == "hidden-misnested-role":
        target = '<p data-role="legal-isi">{{ legal_isi }}</p>'
        source = source.replace(target, f'<div hidden="hidden"></span>{target}</div>')
    elif mutation == "body-reparented-title":
        source = source.replace(
            '<head><title data-role="subject">{{ subject }}</title></head><body>',
            '<head><body><title data-role="subject">{{ subject }}</title></head><body>',
        )
    elif mutation == "head-reparented-flow":
        source = source.replace("</head>", "<div></div></head>")
    else:
        source = source.replace("<table><tbody>", "<table><div></div><tbody>")
    template = tmp_path / f"{mutation}.html.j2"
    template.write_text(source, encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}
    assert not (campaign_dir(brief_id) / "outputs" / "email.html").exists()


@pytest.mark.parametrize(
    "style",
    [
        "position:absolute;left:9999px",
        "color:rgba(0,0,0,0)",
        "filter:opacity(0)",
        "--hidden:none;display:var(--hidden)",
        "position:relative",
        "width:calc(100%)",
        "background-image:none",
    ],
)
def test_custom_email_inline_css_is_restricted_to_positive_static_subset(tmp_path: Path, style: str):
    """Unknown, transparent, positioned, functional, or custom-property CSS must fail closed."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    source = _safe_custom_email_template().replace(
        '<p data-role="legal-isi">', f'<p data-role="legal-isi" style="{style}">'
    )
    template = tmp_path / "unsafe-inline-css.html.j2"
    template.write_text(source, encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}


def test_custom_email_accepts_documented_static_inline_css_subset(tmp_path: Path):
    """The positive CSS contract must retain useful opaque typography and table layout styling."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    source = _safe_custom_email_template().replace(
        '<p data-role="legal-isi">',
        '<p data-role="legal-isi" '
        'style="color:#123456;font-family:Arial, sans-serif;font-size:12px;line-height:16px;'
        'text-align:left;padding:4px">',
    )
    template = tmp_path / "safe-inline-css.html.j2"
    template.write_text(source, encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert "error" not in result


@pytest.mark.parametrize("tag", ["frameset", "noframes", "plaintext", "section"])
def test_custom_email_rejects_every_tag_outside_static_email_allowlist(tmp_path: Path, tag: str):
    """A blacklist must not admit legacy parser-changing tags or new unreviewed HTML elements."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    source = _safe_custom_email_template().replace("</body>", f"<{tag}></{tag}></body>")
    template = tmp_path / f"unsafe-{tag}.html.j2"
    template.write_text(source, encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}


@pytest.mark.parametrize("attribute", ['class="unreviewed"', 'loading="lazy"', 'data-extra="x"'])
def test_custom_email_rejects_unknown_attributes(tmp_path: Path, attribute: str):
    """Only the documented per-tag/global attributes may cross the custom-template boundary."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    source = _safe_custom_email_template().replace("<table>", f"<table {attribute}>")
    template = tmp_path / "unknown-attribute.html.j2"
    template.write_text(source, encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}


@pytest.mark.parametrize("text", ["\u00af" * 8, "safe\u00adcopy", "Cafe\u0301"])
def test_banner_rejects_modifier_control_and_combining_categories_before_write(tmp_path: Path, text: str):
    """CP1252 membership alone must not admit spacing modifiers, format controls, or combining input."""
    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        channels=["banner"],
        asset_dimensions={"banner": "300x250"},
    )
    copy = load_artifact(brief_id, "copy-banner.json")
    copy["copy"]["headline"]["text"] = text
    save_artifact(brief_id, "copy-banner.json", copy)
    if text != "safe\u00adcopy":
        _refresh_gate(brief_id)

    result = _result(render_banner({"campaign_brief_id": brief_id}))

    assert result["error"]["code"] in {
        "invalid_channel_copy",
        "unsupported_banner_glyph",
        "pre_render_validation_not_current",
    }
    assert not (campaign_dir(brief_id) / "outputs" / "banner.svg").exists()


@pytest.mark.parametrize("dimensions", ["728x90", "300x250", "300x300", "160x600"])
def test_every_banner_line_is_bounded_by_explicit_svg_text_length(tmp_path: Path, dimensions: str):
    """Actual fallback rendering must retain an explicit per-line width bound in every banner profile."""
    from xml.etree import ElementTree

    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        channels=["banner"],
        asset_dimensions={"banner": dimensions},
    )
    result = _result(render_banner({"campaign_brief_id": brief_id}))
    root = ElementTree.fromstring(Path(result["file_path"]).read_text(encoding="utf-8"))
    safe_widths = {
        "728x90": {"headline": 225, "safety": 365, "legal-isi": 365, "legal-pi_ref": 365, "cta": 112},
        "300x250": {"headline": 198, "safety": 198, "legal-isi": 264, "legal-pi_ref": 264, "cta": 94},
        "300x300": {"headline": 198, "safety": 198, "legal-isi": 264, "legal-pi_ref": 264, "cta": 94},
        "160x600": {"headline": 128, "safety": 128, "legal-isi": 128, "legal-pi_ref": 128, "cta": 112},
    }[dimensions]
    role_lines = [element for element in root.iter() if element.get("data-role")]

    assert role_lines
    for element in role_lines:
        assert element.get("lengthAdjust") == "spacing"
        assert 0 < float(element.attrib["textLength"]) <= safe_widths[element.attrib["data-role"]]


def test_rendered_gate_rejects_template_sources_for_banner_only_report_without_reading_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A non-email report must require an empty source list and never dereference attacker-selected paths."""
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["banner"])
    assert "error" not in _result(render_banner({"campaign_brief_id": brief_id}))
    report = _result(validate_rendered({"campaign_brief_id": brief_id}))
    oversized = tmp_path / "untrusted-template.html.j2"
    payload = b"x" * 129_000
    oversized.write_bytes(payload)
    state = oversized.stat()
    report["template_sources"] = [
        {
            "kind": "custom",
            "path": str(oversized),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
            "identity": {"device": state.st_dev, "inode": state.st_ino, "mode": state.st_mode & 0o170000},
        }
    ]
    save_validation_artifact(brief_id, "rendered-assets.json", report)
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == oversized:
            raise AssertionError("banner-only gate dereferenced an untrusted template source")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    gate = rendered_validation_gate_state(brief_id)

    assert gate["status"] == "failed"
    assert gate["code"] == "malformed_template_sources"


def test_rendered_gate_bounds_custom_template_before_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A forged custom-email report must not make the gate read a template over the 128 KB contract."""
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    assert "error" not in _result(render_email({"campaign_brief_id": brief_id}))
    report = _result(validate_rendered({"campaign_brief_id": brief_id}))
    oversized = tmp_path / "oversized-forged-template.html.j2"
    payload = b"x" * 129_000
    oversized.write_bytes(payload)
    state = oversized.stat()
    source = {
        "kind": "custom",
        "path": str(oversized),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "identity": {"device": state.st_dev, "inode": state.st_ino, "mode": state.st_mode & 0o170000},
    }
    report["template_sources"] = [source]
    save_validation_artifact(brief_id, "rendered-assets.json", report)
    save_artifact(
        brief_id,
        "render-provenance-email.json",
        {"campaign_brief_id": brief_id, "channel": "email", "template": source},
    )
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == oversized:
            raise AssertionError("oversized template was read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    gate = rendered_validation_gate_state(brief_id)

    assert gate["status"] == "failed"
    assert gate["code"] == "malformed_template_sources"


@pytest.mark.parametrize("mode", ["default-extra", "custom-missing"])
def test_rendered_gate_requires_template_sources_to_match_email_provenance_exactly(tmp_path: Path, mode: str):
    """Email reports require empty default sources or one exact sealed custom source, never another shape."""
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    if mode == "custom-missing":
        template = tmp_path / "safe-email.html.j2"
        template.write_text(_safe_custom_email_template(), encoding="utf-8")
        assert "error" not in _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))
    else:
        assert "error" not in _result(render_email({"campaign_brief_id": brief_id}))
    report = _result(validate_rendered({"campaign_brief_id": brief_id}))
    if mode == "custom-missing":
        report["template_sources"] = []
    else:
        template = tmp_path / "arbitrary.html.j2"
        template.write_text(_safe_custom_email_template(), encoding="utf-8")
        state = template.stat()
        payload = template.read_bytes()
        report["template_sources"] = [
            {
                "kind": "custom",
                "path": str(template),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
                "identity": {"device": state.st_dev, "inode": state.st_ino, "mode": state.st_mode & 0o170000},
            }
        ]
    save_validation_artifact(brief_id, "rendered-assets.json", report)

    gate = rendered_validation_gate_state(brief_id)

    assert gate["status"] == "failed"
    assert gate["code"] == "malformed_template_sources"


def test_rendered_gate_fails_malformed_custom_template_source_entry(tmp_path: Path):
    """A one-item source list is not valid unless every field matches the strict custom-source schema."""
    from open_pharma_plugins_campaign_studio._renderer import rendered_validation_gate_state
    from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered

    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    template = tmp_path / "safe-email.html.j2"
    template.write_text(_safe_custom_email_template(), encoding="utf-8")
    assert "error" not in _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))
    report = _result(validate_rendered({"campaign_brief_id": brief_id}))
    report["template_sources"] = [{"kind": "custom"}]
    save_validation_artifact(brief_id, "rendered-assets.json", report)

    gate = rendered_validation_gate_state(brief_id)

    assert gate["status"] == "failed"
    assert gate["code"] == "malformed_template_sources"


@pytest.mark.parametrize(
    "style",
    [
        "margin-left:9999px",
        "margin-left:101%",
        "margin-left:100em",
        "font-size:.000001px",
        "font-size:500%",
        "line-height:.000001",
        "line-height:9999pt",
        "letter-spacing:9999px",
        "letter-spacing:1%",
        "padding:9999rem",
        "width:9999px",
        "height:9999em",
        "max-width:.000001px",
    ],
)
def test_custom_email_rejects_invisible_off_canvas_and_extreme_css_lengths(tmp_path: Path, style: str):
    """Allowed property names still require property-specific safe numeric units and ranges."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    source = _safe_custom_email_template().replace(
        '<p data-role="legal-isi">', f'<p data-role="legal-isi" style="{style}">'
    )
    template = tmp_path / "extreme-layout.html.j2"
    template.write_text(source, encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}
    assert not (campaign_dir(brief_id) / "outputs" / "email.html").exists()


def test_custom_email_accepts_bounded_layout_units(tmp_path: Path):
    """Useful bounded px, pt, em, percent, margin, padding, size, and spacing values remain supported."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    source = (
        _safe_custom_email_template()
        .replace("<table>", '<table style="height:24px">')
        .replace(
            '<p data-role="legal-isi">',
            '<p data-role="legal-isi" '
            'style="font-size:12px;line-height:16pt;letter-spacing:1px;margin:4px auto;padding:4px 8px;'
            'width:100%;max-width:600px;min-width:20em">',
        )
    )
    template = tmp_path / "bounded-layout.html.j2"
    template.write_text(source, encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert "error" not in result


@pytest.mark.parametrize("placement", ["table-text", "table-tail", "head-text"])
def test_custom_email_structural_containers_cannot_own_visible_roles_or_text(tmp_path: Path, placement: str):
    """Browser-reparented structural text cannot satisfy a visible role in the XML ownership tree."""
    brief_id, _kit, _copies = _seed_campaign(tmp_path, channels=["email"])
    source = _safe_custom_email_template()
    headline = '<h1 data-role="headline">{{ headline }}</h1>'
    source = source.replace(headline, "")
    if placement == "table-text":
        source = source.replace("<table><tbody>", '<table data-role="headline">{{ headline }}<tbody>')
    elif placement == "table-tail":
        source = source.replace("<table><tbody>", '<table data-role="headline"><tbody>').replace(
            "</tbody></table>", "</tbody>{{ headline }}</table>"
        )
    else:
        source = source.replace("<head>", '<head data-role="headline">{{ headline }}')
    template = tmp_path / f"structural-{placement}.html.j2"
    template.write_text(source, encoding="utf-8")

    result = _result(render_email({"campaign_brief_id": brief_id, "template": str(template)}))

    assert result["error"]["code"] in {"unsafe_custom_template", "rendered_email_invalid"}
    assert not (campaign_dir(brief_id) / "outputs" / "email.html").exists()


@pytest.mark.parametrize("text", ["&#175;" * 8, "safe&#173;copy", "Cafe&#769;"])
def test_banner_rejects_entity_encoded_modifier_format_and_combining_glyphs(tmp_path: Path, text: str):
    """HTML entity spelling cannot bypass the portable banner glyph-category contract."""
    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        channels=["banner"],
        asset_dimensions={"banner": "300x250"},
    )
    copy = load_artifact(brief_id, "copy-banner.json")
    copy["copy"]["headline"]["text"] = text
    save_artifact(brief_id, "copy-banner.json", copy)
    _refresh_gate(brief_id)

    result = _result(render_banner({"campaign_brief_id": brief_id}))

    assert result["error"]["code"] == "unsupported_banner_glyph"
    assert not (campaign_dir(brief_id) / "outputs" / "banner.svg").exists()


@pytest.mark.parametrize("text", ["R&D evidence", "R&amp;D evidence", "Caf&eacute; evidence"])
def test_banner_preserves_safe_literal_ampersands_and_entities(tmp_path: Path, text: str):
    """Safe literal ampersands and Western entities normalize once and remain escaped in SVG."""
    from open_pharma_plugins_campaign_studio._render_validation import extract_rendered_text

    brief_id, _kit, _copies = _seed_campaign(
        tmp_path,
        channels=["banner"],
        asset_dimensions={"banner": "300x250"},
    )
    copy = load_artifact(brief_id, "copy-banner.json")
    copy["copy"]["headline"]["text"] = text
    save_artifact(brief_id, "copy-banner.json", copy)
    _refresh_gate(brief_id)

    result = _result(render_banner({"campaign_brief_id": brief_id}))

    assert "error" not in result
    output = Path(result["file_path"])
    visible = extract_rendered_text(output, "svg")
    if "Caf" in text:
        assert "Café evidence" in visible
    else:
        assert "R&D evidence" in visible
        assert "R&amp;D evidence" in output.read_text(encoding="utf-8")
