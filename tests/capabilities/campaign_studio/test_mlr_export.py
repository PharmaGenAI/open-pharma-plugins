"""Review-ready Campaign Studio MLR packaging and export contracts."""

from __future__ import annotations

import errno
import hashlib
import importlib
import json
import os
import shutil
import stat
import sys
import zipfile
from importlib.resources import files
from pathlib import Path

import anyio
import pytest
from markdown_it import MarkdownIt
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

import open_pharma_plugins_campaign_studio as campaign_studio
from mcp_framework import tool_schema
from open_pharma_plugins_campaign_studio._campaign_store import (
    campaign_dir,
    load_artifact,
    save_artifact,
    save_brief,
    save_output,
)
from open_pharma_plugins_campaign_studio._inputs import preflight_inputs
from open_pharma_plugins_campaign_studio._mlr_package import MlrContractError, build_review_plan, publish_review
from open_pharma_plugins_campaign_studio.tools.export_mlr_package import (
    ExportMlrPackageArgs,
)
from open_pharma_plugins_campaign_studio.tools.export_mlr_package import (
    handle as export_mlr_package,
)
from open_pharma_plugins_campaign_studio.tools.generate_audience_journey import handle as generate_journey
from open_pharma_plugins_campaign_studio.tools.generate_message_architecture import handle as generate_architecture
from open_pharma_plugins_campaign_studio.tools.package_mlr_submission import handle as package_mlr_submission
from open_pharma_plugins_campaign_studio.tools.render_banner import handle as render_banner
from open_pharma_plugins_campaign_studio.tools.render_email import handle as render_email
from open_pharma_plugins_campaign_studio.tools.render_mlr_review import (
    RenderMlrReviewArgs,
)
from open_pharma_plugins_campaign_studio.tools.render_mlr_review import (
    handle as render_mlr_review,
)
from open_pharma_plugins_campaign_studio.tools.render_poster import handle as render_poster
from open_pharma_plugins_campaign_studio.tools.validate_claims_and_fair_balance import handle as validate_claims
from open_pharma_plugins_campaign_studio.tools.validate_rendered_assets import handle as validate_rendered


@pytest.fixture(autouse=True)
def campaign_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_PHARMA_CAMPAIGN_STORE_DIR", str(tmp_path / "store"))


def _result(blocks: list[dict]) -> dict:
    return json.loads(blocks[0]["text"])


def _write_self_consistent_package(
    campaign_id: str,
    campaign_path: Path,
    manifest: dict,
    *,
    archive_timestamp: tuple[int, int, int, int, int, int] = (1980, 1, 1, 0, 0, 0),
    archive_mode: int = 0o600,
) -> Path:
    """Persist an internally consistent package without using production builders."""
    identity = [[item["path"], item["size"], item["sha256"]] for item in manifest["files"]]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    manifest["package_digest"] = digest
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": ")) + "\n"
    ).encode()
    output_dir = campaign_path / "outputs"
    (output_dir / "package-manifest.json").write_bytes(manifest_bytes)
    archive_path = output_dir / f"{campaign_id}-mlr-{digest}.zip"
    entries = [(item["path"], (campaign_path / item["path"]).read_bytes()) for item in manifest["files"]]
    entries.append(("package-manifest.json", manifest_bytes))
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, payload in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=archive_timestamp)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | archive_mode) << 16
            archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return archive_path


def _minimal_brief(campaign_brief_id: str = "mlr-red") -> dict:
    return {
        "campaign_brief_id": campaign_brief_id,
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
        "approved_claims_path": "/tmp/not-used-in-red.json",
        "brand_kit_path": "/tmp/not-used-in-red",
        "demo_mode": False,
        "call_to_action": "Review the evidence",
        "call_to_action_url": "https://oncorix-hcp.example.com/evidence",
        "channels": ["email"],
        "asset_dimensions": None,
        "language": "en",
        "localisation_notes": None,
        "required_safety_content": ["isi"],
        "required_legal_content": ["pi_ref", "reporting_statement"],
        "delivery_constraints": None,
        "approval_workflow": "mlr_standard",
        "generated_at": "2026-08-28T00:00:00+00:00",
    }


def _copy_block(text: str, claim_id: str | None = None) -> dict:
    return {"text": text, "claim_ids": [claim_id] if claim_id else []}


def _seed_complete_campaign(
    tmp_path: Path,
    *,
    campaign_brief_id: str = "mlr-complete",
    channels: list[str] | None = None,
    hostile_source: str | None = None,
    demo_mode: bool = False,
) -> tuple[str, Path]:
    selected_channels = channels or ["email", "banner", "poster"]
    fixture_root = Path(str(files("open_pharma_plugins_campaign_studio") / "fixtures"))
    claims = json.loads((fixture_root / "sample_approved_claims.json").read_text(encoding="utf-8"))
    if demo_mode and hostile_source is not None:
        raise ValueError("hostile_source requires a copied non-demo claims file")
    if hostile_source is not None:
        claims[0]["source_reference"] = hostile_source
    claims_by_id = {claim["claim_id"]: claim for claim in claims}
    if demo_mode:
        claims_path = fixture_root / "sample_approved_claims.json"
        brand_path = fixture_root / "brand_kit"
    else:
        claims_path = tmp_path / f"{campaign_brief_id}-claims.json"
        claims_path.write_text(json.dumps(claims), encoding="utf-8")
        brand_path = tmp_path / f"{campaign_brief_id}-brand"
        shutil.copytree(fixture_root / "brand_kit", brand_path)
    brief = _minimal_brief(campaign_brief_id)
    brief.update(
        {
            "channels": selected_channels,
            "approved_claims_path": str(claims_path),
            "brand_kit_path": str(brand_path),
            "demo_mode": demo_mode,
            "asset_dimensions": {"banner": "300x250", "poster": "A4"},
        }
    )
    save_brief(brief)
    assert preflight_inputs(campaign_brief_id, str(claims_path), str(brand_path), demo_mode)["ready"] is True

    journey = [
        {
            "stage": "aware",
            "objective": "Review the primary endpoint",
            "key_messages": ["c-001"],
            "channels": selected_channels,
            "content_type": "promotional",
            "kpi": "Evidence review",
        },
        {
            "stage": "interested",
            "objective": "Review response evidence",
            "key_messages": ["c-002"],
            "channels": selected_channels,
            "content_type": "promotional",
            "kpi": "Evidence engagement",
        },
        {
            "stage": "convinced",
            "objective": "Review safety evidence",
            "key_messages": ["c-006"],
            "channels": selected_channels,
            "content_type": "promotional",
            "kpi": "Balanced review",
        },
    ]
    assert "errors" not in _result(generate_journey({"campaign_brief_id": campaign_brief_id, "journey": journey}))
    architecture = _result(
        generate_architecture(
            {
                "campaign_brief_id": campaign_brief_id,
                "messages": [
                    {
                        "tier": "primary",
                        "message": claims_by_id["c-001"]["text"],
                        "claim_ids": ["c-001"],
                        "audience_stage": "aware",
                        "rationale": "Primary endpoint",
                    },
                    {
                        "tier": "secondary",
                        "message": claims_by_id["c-002"]["text"],
                        "claim_ids": ["c-002"],
                        "audience_stage": "interested",
                        "rationale": "Response evidence",
                    },
                    {
                        "tier": "supporting",
                        "message": claims_by_id["c-006"]["text"],
                        "claim_ids": ["c-006"],
                        "audience_stage": "convinced",
                        "rationale": "Fair balance",
                    },
                ],
                "fair_balance_statement": claims_by_id["c-006"]["text"],
                "fair_balance_sources": [
                    {
                        "document_id": "c-006",
                        "document_name": claims_by_id["c-006"]["source_document"],
                        "page_number": None,
                        "excerpt": claims_by_id["c-006"]["source_reference"],
                    }
                ],
            }
        )
    )
    assert "errors" not in architecture, architecture

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
            campaign_brief_id,
            f"copy-{channel}.json",
            {
                "campaign_brief_id": campaign_brief_id,
                "channel": channel,
                "copy": copies[channel],
                "generated_at": "2026-08-28T00:00:00+00:00",
            },
        )
    pre_report = _result(validate_claims({"campaign_brief_id": campaign_brief_id}))
    assert pre_report["overall_pass"] is True, pre_report
    renderers = {"email": render_email, "banner": render_banner, "poster": render_poster}
    for channel in selected_channels:
        rendered = _result(renderers[channel]({"campaign_brief_id": campaign_brief_id}))
        assert "error" not in rendered, rendered
    rendered_report = _result(validate_rendered({"campaign_brief_id": campaign_brief_id}))
    assert rendered_report["overall_pass"] is True, rendered_report
    assert "validated_at" in rendered_report
    return campaign_brief_id, campaign_dir(campaign_brief_id)


def test_existing_package_tool_fails_closed_and_preserves_old_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing means failure and a failed preflight cannot replace review evidence."""
    brief = _minimal_brief()
    save_brief(brief)
    prior = save_output(brief["campaign_brief_id"], "mlr-review-summary.md", "previous review\n")
    monkeypatch.setattr(
        "open_pharma_plugins_campaign_studio._renderer.check_validation_gate",
        lambda _campaign_brief_id: None,
    )

    response = _result(package_mlr_submission({"campaign_brief_id": brief["campaign_brief_id"]}))

    assert response["error"]["code"] == "mlr_package_incomplete"
    assert "approved-claims.json" in response["error"]["items"]
    assert prior.read_bytes() == b"previous review\n"


def test_new_review_and_export_tools_are_discoverable() -> None:
    names = {tool["name"] for tool in campaign_studio.list_tools()}

    assert {"render_mlr_review", "export_mlr_package"} <= names


@pytest.mark.parametrize(
    "module_name",
    [
        "open_pharma_plugins_campaign_studio.tools.render_mlr_review",
        "open_pharma_plugins_campaign_studio.tools.export_mlr_package",
    ],
)
def test_new_tool_modules_exist(module_name: str) -> None:
    assert importlib.import_module(module_name).TOOL["name"] == module_name.rsplit(".", 1)[-1]


def test_complete_all_channel_review_is_deterministic_self_contained_and_escaped(tmp_path: Path) -> None:
    hostile = "</style><script>window.PWNED=true</script>|unicode Ω\nsecond row {{ 7*7 }}"
    campaign_id, _cdir = _seed_complete_campaign(tmp_path, hostile_source=hostile)
    notes = '<img src=x onerror="window.PWNED=true">\n| reviewer {{ unsafe }}'

    build_review_plan(campaign_id, notes)
    first = _result(render_mlr_review({"campaign_brief_id": campaign_id, "reviewer_notes": notes}))
    assert "error" not in first, first
    html_path = Path(next(item["absolute_path"] for item in first["outputs"] if item["path"].endswith(".html")))
    md_path = Path(next(item["absolute_path"] for item in first["outputs"] if item["path"].endswith(".md")))
    first_html = html_path.read_bytes()
    first_md = md_path.read_bytes()
    second = _result(package_mlr_submission({"campaign_brief_id": campaign_id, "reviewer_notes": notes}))

    assert "error" not in first and "error" not in second
    assert html_path.read_bytes() == first_html
    assert md_path.read_bytes() == first_md
    html = first_html.decode("utf-8")
    markdown = first_md.decode("utf-8")
    assert "Content-Security-Policy" in html
    assert "default-src &#39;none&#39;" in html or "default-src 'none'" in html
    assert 'src="http' not in html and 'href="https' not in html
    assert "file:" not in html
    assert "window.PWNED=true</script>" not in html
    assert "&lt;script&gt;window.PWNED=true&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=&#34;window.PWNED=true&#34;&gt;" in html
    assert "{{ unsafe }}" in html
    assert "&lt;/style&gt;&lt;script&gt;window.PWNED=true&lt;/script&gt;" in html
    assert hostile.split("|", 1)[1].split("\n", 1)[1] in html
    assert "\\|unicode Ω<br>second row" in markdown
    assert len(first["outputs"]) == 2 and first["completeness"]["missing"] == 0
    claims_path = load_artifact(campaign_id, "campaign-brief.json")["approved_claims_path"]
    assert claims_path in html.split('id="provenance-title"', 1)[1]
    assert html.count('<button id="tab-') == 3
    assert "ArrowRight" in html and "@media print" in html and "data-print" in html
    assert "MLR-approved" not in html and "production-ready" not in html


def test_bundled_fictional_inputs_have_prominent_demo_disclosure(tmp_path: Path) -> None:
    campaign_id, _cdir = _seed_complete_campaign(tmp_path, demo_mode=True)

    result = _result(render_mlr_review({"campaign_brief_id": campaign_id}))
    html_path = next(Path(item["absolute_path"]) for item in result["outputs"] if item["path"].endswith("html"))

    assert result["demo_mode"] is True
    assert "Fictional demo inputs are active" in html_path.read_text(encoding="utf-8")


def test_review_paths_hashes_sizes_and_full_rows_are_exact(tmp_path: Path) -> None:
    long_reference = "Source Ω | line one\nline two " + ("evidence-" * 800)
    campaign_id, _cdir = _seed_complete_campaign(tmp_path, hostile_source=long_reference)

    plan = build_review_plan(campaign_id)
    result = _result(render_mlr_review({"campaign_brief_id": campaign_id}))

    assert len(plan.model["claim_rows"]) == 12
    matching = [row for row in plan.model["claim_rows"] if row["claim_id"] == "c-001"]
    assert len(matching) == 2
    assert all(row["source_reference"] == long_reference for row in matching)
    for item in result["outputs"]:
        path = Path(item["absolute_path"])
        payload = path.read_bytes()
        assert path.is_absolute()
        assert item["path"].startswith("outputs/")
        assert item["size"] == len(payload)
        assert item["sha256"] == hashlib.sha256(payload).hexdigest()
    html = next(Path(item["absolute_path"]).read_text() for item in result["outputs"] if item["path"].endswith("html"))
    provenance_marker = html.index('id="provenance-title"')
    claims_path = load_artifact(campaign_id, "campaign-brief.json")["approved_claims_path"]
    assert claims_path not in html[:provenance_marker]


@pytest.mark.parametrize(
    ("filename", "mutate"),
    [
        (
            "source-evidence.json",
            lambda value: (
                value
                + [{"document_id": "c-999", "document_name": "invented", "page_number": None, "excerpt": "invented"}]
            ),
        ),
        ("claim-map.json", lambda value: value | {"invented statement": ["c-999"]}),
        (
            "policy-checks.json",
            lambda value: (
                value
                | {
                    "channel_results": value["channel_results"]
                    | {
                        "email": value["channel_results"]["email"]
                        | {
                            "policy_checks": [
                                value["channel_results"]["email"]["policy_checks"][0] | {"result": "fail"},
                                *value["channel_results"]["email"]["policy_checks"][1:],
                            ]
                        }
                    },
                    "policy_checks": [
                        value["policy_checks"][0] | {"result": "fail"},
                        *value["policy_checks"][1:],
                    ],
                }
            ),
        ),
        (
            "rendered-assets.json",
            lambda value: (
                value
                | {
                    "channel_results": value["channel_results"]
                    | {
                        "email": value["channel_results"]["email"]
                        | {
                            "checks": [
                                value["channel_results"]["email"]["checks"][0] | {"result": "fail"},
                                *value["channel_results"]["email"]["checks"][1:],
                            ]
                        }
                    }
                }
            ),
        ),
    ],
)
def test_validation_evidence_is_cross_checked_even_when_top_level_says_pass(
    tmp_path: Path, filename: str, mutate
) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    old_md = save_output(campaign_id, "mlr-review-summary.md", "old-md")
    old_html = save_output(campaign_id, "mlr-review.html", "old-html")
    path = cdir / "validation" / filename
    value = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(mutate(value)), encoding="utf-8")

    response = _result(render_mlr_review({"campaign_brief_id": campaign_id}))

    expected_code = "validation_not_current" if filename == "policy-checks.json" else "invalid_validation_artifacts"
    assert response["error"]["code"] == expected_code
    assert old_md.read_bytes() == b"old-md"
    assert old_html.read_bytes() == b"old-html"


def test_validation_evidence_cannot_omit_one_canonical_copy_occurrence(tmp_path: Path) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    prior = _result(render_mlr_review({"campaign_brief_id": campaign_id, "reviewer_notes": "complete"}))
    assert prior["completeness"]["claim_rows"] == 5
    review_paths = [Path(item["absolute_path"]) for item in prior["outputs"]]
    before = {path: path.read_bytes() for path in review_paths}

    policy_path = cdir / "validation" / "policy-checks.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["channel_results"]["email"]["claims_checked"] = [
        item for item in policy["channel_results"]["email"]["claims_checked"] if item["declared_claim_id"] != "c-003"
    ]
    policy["claims_checked"] = [item for item in policy["claims_checked"] if item["declared_claim_id"] != "c-003"]
    policy_path.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")

    claim_map_path = cdir / "validation" / "claim-map.json"
    claim_map = json.loads(claim_map_path.read_text(encoding="utf-8"))
    c003_statement = next(
        block["text"]
        for block in load_artifact(campaign_id, "copy-email.json")["copy"]["body"]
        if block["claim_ids"] == ["c-003"]
    )
    del claim_map[c003_statement[:60]]
    claim_map_path.write_text(json.dumps(claim_map, sort_keys=True), encoding="utf-8")

    source_path = cdir / "validation" / "source-evidence.json"
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    source_path.write_text(
        json.dumps([item for item in sources if item["document_id"] != "c-003"], sort_keys=True),
        encoding="utf-8",
    )

    response = _result(render_mlr_review({"campaign_brief_id": campaign_id, "reviewer_notes": "forged"}))

    assert response["error"]["code"] == "validation_not_current"
    assert all(path.read_bytes() == payload for path, payload in before.items())


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [("matched_claim_text", "Invented approved wording"), ("deviation", "Invented deviation")],
)
def test_validation_claim_decision_must_match_approved_claim(tmp_path: Path, field: str, forged_value: str) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    prior = _result(render_mlr_review({"campaign_brief_id": campaign_id}))
    review_paths = [Path(item["absolute_path"]) for item in prior["outputs"]]
    before = {path: path.read_bytes() for path in review_paths}
    policy_path = cdir / "validation" / "policy-checks.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["channel_results"]["email"]["claims_checked"][0][field] = forged_value
    policy["claims_checked"][0][field] = forged_value
    policy_path.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")

    response = _result(render_mlr_review({"campaign_brief_id": campaign_id}))

    assert response["error"]["code"] == "validation_not_current"
    assert all(path.read_bytes() == payload for path, payload in before.items())


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "misordered", "copy_missing"])
def test_pre_render_policy_checks_must_be_complete_canonical_and_ordered(tmp_path: Path, mutation: str) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    prior = _result(render_mlr_review({"campaign_brief_id": campaign_id, "reviewer_notes": "canonical"}))
    review_paths = [Path(item["absolute_path"]) for item in prior["outputs"]]
    before = {path: path.read_bytes() for path in review_paths}
    policy_path = cdir / "validation" / "policy-checks.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    channel_result = policy["channel_results"]["email"]
    checks = channel_result["policy_checks"]
    if mutation == "unknown":
        checks.append({"check_name": "invented_pass", "result": "pass", "detail": "invented"})
    elif mutation == "duplicate":
        checks.append(dict(checks[0]))
    elif mutation == "misordered":
        checks.reverse()
    else:
        channel_result["copy_exists"] = False
    policy["policy_checks"] = list(checks)
    policy_path.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")

    response = _result(render_mlr_review({"campaign_brief_id": campaign_id, "reviewer_notes": "forged"}))

    assert response["error"]["code"] == "validation_not_current"
    assert all(path.read_bytes() == payload for path, payload in before.items())


def test_pre_render_policy_checks_cannot_all_be_deleted(tmp_path: Path) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    prior = _result(render_mlr_review({"campaign_brief_id": campaign_id, "reviewer_notes": "complete"}))
    review_paths = [Path(item["absolute_path"]) for item in prior["outputs"]]
    before = {path: path.read_bytes() for path in review_paths}
    policy_path = cdir / "validation" / "policy-checks.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["channel_results"]["email"]["policy_checks"] = []
    policy["policy_checks"] = []
    policy_path.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")

    response = _result(render_mlr_review({"campaign_brief_id": campaign_id, "reviewer_notes": "forged"}))

    assert response["error"]["code"] == "validation_not_current"
    assert all(path.read_bytes() == payload for path, payload in before.items())


@pytest.mark.parametrize("mutation", ["invented", "missing", "duplicate", "misordered", "forged_detail"])
def test_rendered_checks_must_be_the_exact_production_set_and_order(tmp_path: Path, mutation: str) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    prior = _result(render_mlr_review({"campaign_brief_id": campaign_id, "reviewer_notes": "complete"}))
    review_paths = [Path(item["absolute_path"]) for item in prior["outputs"]]
    before = {path: path.read_bytes() for path in review_paths}
    rendered_path = cdir / "validation" / "rendered-assets.json"
    rendered = json.loads(rendered_path.read_text(encoding="utf-8"))
    checks = rendered["channel_results"]["email"]["checks"]
    if mutation == "invented":
        checks[:] = [{"check_name": "invented", "result": "pass", "detail": "invented"}]
    elif mutation == "missing":
        checks.pop()
    elif mutation == "duplicate":
        checks.append(dict(checks[-1]))
    elif mutation == "forged_detail":
        checks[1]["detail"] = "AUTOMATED MLR APPROVED"
    else:
        checks.reverse()
    rendered_path.write_text(json.dumps(rendered, sort_keys=True), encoding="utf-8")

    response = _result(render_mlr_review({"campaign_brief_id": campaign_id, "reviewer_notes": "forged"}))

    assert response["error"]["code"] == "invalid_validation_artifacts"
    assert any("exact production checks" in item for item in response["error"]["items"])
    assert all(path.read_bytes() == payload for path, payload in before.items())


@pytest.mark.parametrize(
    "level",
    [
        "policy_report",
        "policy_channel",
        "policy_claim",
        "policy_check",
        "claim_map",
        "source_row",
        "rendered_report",
        "rendered_channel",
        "rendered_check",
        "rendered_output",
        "rendered_template_source",
    ],
)
def test_validation_artifacts_reject_unknown_fields_at_every_schema_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, level: str
) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    prior = _result(render_mlr_review({"campaign_brief_id": campaign_id, "reviewer_notes": "FIRST"}))
    assert "error" not in prior, prior
    review_paths = [Path(item["absolute_path"]) for item in prior["outputs"]]
    before = {path: path.read_bytes() for path in review_paths}
    policy_path = cdir / "validation" / "policy-checks.json"
    claim_map_path = cdir / "validation" / "claim-map.json"
    source_path = cdir / "validation" / "source-evidence.json"
    rendered_path = cdir / "validation" / "rendered-assets.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    claim_map = json.loads(claim_map_path.read_text(encoding="utf-8"))
    source = json.loads(source_path.read_text(encoding="utf-8"))
    rendered = json.loads(rendered_path.read_text(encoding="utf-8"))

    if level == "policy_report":
        policy["mlr_approved"] = True
    elif level == "policy_channel":
        policy["channel_results"]["email"]["reviewer_decision"] = "approved"
    elif level == "policy_claim":
        policy["channel_results"]["email"]["claims_checked"][0]["authoritative_decision"] = "pass"
        policy["claims_checked"][0]["authoritative_decision"] = "pass"
    elif level == "policy_check":
        policy["channel_results"]["email"]["policy_checks"][0]["mlr_approved"] = True
        policy["policy_checks"][0]["mlr_approved"] = True
    elif level == "claim_map":
        claim_map["mlr_approved"] = ["c-001"]
    elif level == "source_row":
        source[0]["reviewer_decision"] = "approved"
    elif level == "rendered_report":
        rendered["mlr_approved"] = True
    elif level == "rendered_channel":
        rendered["channel_results"]["email"]["authoritative_decision"] = "pass"
    elif level == "rendered_check":
        rendered["channel_results"]["email"]["checks"][0]["reviewer_decision"] = "approved"
    elif level == "rendered_output":
        rendered["outputs"][0]["mlr_approved"] = True
    else:
        output = rendered["outputs"][0]
        rendered["template_sources"] = [
            {
                "kind": "custom",
                "path": output["path"],
                "sha256": output["sha256"],
                "size": output["size"],
                "identity": {"device": 1, "inode": 2, "mode": stat.S_IFREG},
                "reviewer_decision": "approved",
            }
        ]
        import open_pharma_plugins_campaign_studio._mlr_package as mlr_package

        monkeypatch.setattr(
            mlr_package,
            "rendered_validation_gate_state",
            lambda _campaign_id: {"status": "current", "code": "validation_current", "reason": None},
        )

    policy_path.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
    claim_map_path.write_text(json.dumps(claim_map, sort_keys=True), encoding="utf-8")
    source_path.write_text(json.dumps(source, sort_keys=True), encoding="utf-8")
    rendered_path.write_text(json.dumps(rendered, sort_keys=True), encoding="utf-8")

    response = _result(render_mlr_review({"campaign_brief_id": campaign_id, "reviewer_notes": "SECOND"}))

    if level.startswith("policy_"):
        assert response["error"]["code"] == "validation_not_current"
    else:
        assert response["error"]["code"] == "invalid_validation_artifacts"
        assert any("undeclared" in item for item in response["error"]["items"])
    assert all(path.read_bytes() == payload for path, payload in before.items())


@pytest.mark.parametrize(
    "payload",
    [
        b'{"duplicate": 1, "duplicate": 2}',
        b"[NaN]",
        (b"[" * 300) + b"0" + (b"]" * 300),
    ],
)
def test_unsafe_json_is_structured_and_preserves_review(tmp_path: Path, payload: bytes) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    old_md = save_output(campaign_id, "mlr-review-summary.md", "old-md")
    old_html = save_output(campaign_id, "mlr-review.html", "old-html")
    (cdir / "validation" / "source-evidence.json").write_bytes(payload)

    response = _result(render_mlr_review({"campaign_brief_id": campaign_id}))

    assert response["error"]["code"] == "artifact_json_unreadable"
    assert old_md.read_bytes() == b"old-md"
    assert old_html.read_bytes() == b"old-html"


def test_export_is_content_addressed_deterministic_and_zip_verifies(tmp_path: Path) -> None:
    campaign_id, _cdir = _seed_complete_campaign(tmp_path)

    first = _result(export_mlr_package({"campaign_brief_id": campaign_id}))
    manifest_bytes = Path(first["manifest_path"]).read_bytes()
    archive_bytes = Path(first["archive_path"]).read_bytes()
    second = _result(export_mlr_package({"campaign_brief_id": campaign_id}))

    assert "error" not in first and "error" not in second
    assert first["package_digest"] == second["package_digest"]
    assert first["archive_path"] == second["archive_path"]
    assert Path(second["manifest_path"]).read_bytes() == manifest_bytes
    assert Path(second["archive_path"]).read_bytes() == archive_bytes
    assert first["archive_path"].endswith(f"-mlr-{first['package_digest']}.zip")
    assert len(first["package_digest"]) == 64 and first["package_digest"].islower()
    manifest = json.loads(manifest_bytes)
    assert manifest["package_digest"] == first["package_digest"]
    assert manifest["draft"] is True and manifest["qualified_mlr_review_required"] is True
    assert manifest["rendered_validation_time"]
    paths = [item["path"] for item in manifest["files"]]
    assert paths == sorted(paths)
    assert "outputs/mlr-review.html" in paths and "outputs/mlr-review-summary.md" in paths
    assert "outputs/package-manifest.json" not in paths
    assert not any(path.endswith(".zip") for path in paths)
    identity = [[item["path"], item["size"], item["sha256"]] for item in manifest["files"]]
    assert (
        hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        == first["package_digest"]
    )
    with zipfile.ZipFile(Path(first["archive_path"])) as archive:
        infos = archive.infolist()
        assert [info.filename for info in infos] == sorted(paths + ["package-manifest.json"])
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos)
        assert all(info.create_system == 3 for info in infos)
        assert all(stat.S_IMODE(info.external_attr >> 16) == 0o600 for info in infos)
        assert archive.read("package-manifest.json") == manifest_bytes
        for item in manifest["files"]:
            payload = archive.read(item["path"])
            assert len(payload) == item["size"]
            assert hashlib.sha256(payload).hexdigest() == item["sha256"]


def test_status_advances_through_review_and_current_export(tmp_path: Path) -> None:
    """Rendered validation is not the terminal workflow stage; review and export remain required."""
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle as get_status

    campaign_id, _cdir = _seed_complete_campaign(tmp_path, channels=["email"])

    before_review = _result(get_status({"campaign_brief_id": campaign_id}))
    assert before_review["next_step"] == {"tool": "render_mlr_review", "channel": None}
    assert "mlr_review" in before_review["missing_steps"]

    review = _result(render_mlr_review({"campaign_brief_id": campaign_id}))
    assert "error" not in review
    before_export = _result(get_status({"campaign_brief_id": campaign_id}))
    assert before_export["next_step"] == {"tool": "export_mlr_package", "channel": None}
    assert "mlr_review" in before_export["completed_steps"]
    assert "mlr_export" in before_export["missing_steps"]

    exported = _result(export_mlr_package({"campaign_brief_id": campaign_id, "reviewer_notes": "Review intact."}))
    assert "error" not in exported
    complete = _result(get_status({"campaign_brief_id": campaign_id}))
    assert complete["next_step"] == {"tool": None, "channel": None}
    assert {"mlr_review", "mlr_export"} <= set(complete["completed_steps"])
    assert complete["package_export"]["status"] == "current"


@pytest.mark.parametrize("review_name", ["mlr-review-summary.md", "mlr-review.html"])
def test_status_marks_tampered_review_stale_before_export(tmp_path: Path, review_name: str) -> None:
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle as get_status

    campaign_id, campaign_path = _seed_complete_campaign(tmp_path, channels=["email"])
    exported = _result(export_mlr_package({"campaign_brief_id": campaign_id, "reviewer_notes": "Review intact."}))
    assert "error" not in exported
    (campaign_path / "outputs" / review_name).write_text("tampered review", encoding="utf-8")

    status = _result(get_status({"campaign_brief_id": campaign_id}))

    assert status["review_outputs"]["status"] == "stale"
    assert status["package_export"]["status"] == "stale"
    assert status["next_step"] == {"tool": "render_mlr_review", "channel": None}


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_required_file",
        "extra_file",
        "invalid_manifest_metadata",
        "invalid_archive_timestamp",
        "invalid_archive_mode",
    ],
)
def test_status_rejects_noncanonical_package_evidence(tmp_path: Path, mutation: str) -> None:
    from open_pharma_plugins_campaign_studio.tools.get_campaign_status import handle as get_status

    campaign_id, campaign_path = _seed_complete_campaign(tmp_path, channels=["email"])
    exported = _result(export_mlr_package({"campaign_brief_id": campaign_id}))
    assert "error" not in exported
    manifest = json.loads(Path(exported["manifest_path"]).read_text(encoding="utf-8"))

    if mutation == "missing_required_file":
        manifest["files"] = [item for item in manifest["files"] if item["path"] != "approved-claims.json"]
        _write_self_consistent_package(campaign_id, campaign_path, manifest)
    elif mutation == "extra_file":
        extra = campaign_path / "outputs" / "operator-note.txt"
        extra.write_text("not part of the canonical package", encoding="utf-8")
        payload = extra.read_bytes()
        manifest["files"].append(
            {"path": "outputs/operator-note.txt", "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
        )
        manifest["files"].sort(key=lambda item: item["path"])
        _write_self_consistent_package(campaign_id, campaign_path, manifest)
    elif mutation == "invalid_manifest_metadata":
        manifest["draft"] = False
        _write_self_consistent_package(campaign_id, campaign_path, manifest)
    elif mutation == "invalid_archive_timestamp":
        _write_self_consistent_package(
            campaign_id,
            campaign_path,
            manifest,
            archive_timestamp=(1980, 1, 2, 0, 0, 0),
        )
    else:
        _write_self_consistent_package(campaign_id, campaign_path, manifest, archive_mode=0o644)

    status = _result(get_status({"campaign_brief_id": campaign_id}))

    assert status["review_outputs"]["status"] == "current"
    assert status["package_export"]["status"] == "invalid"
    assert status["next_step"] == {"tool": "export_mlr_package", "channel": None}


@pytest.mark.parametrize(
    ("relative_path", "section"),
    [
        ("input-provenance.json", None),
        ("approved-claims.json", None),
        ("brand-components.json", None),
        ("audience-journey.json", None),
        ("message-architecture.json", None),
        ("copy-email.json", None),
        ("claim-map.json", "validation"),
        ("policy-checks.json", "validation"),
        ("source-evidence.json", "validation"),
        ("rendered-assets.json", "validation"),
        ("email.html", "outputs"),
    ],
)
def test_every_required_artifact_missing_fails_without_overwriting_review(
    tmp_path: Path, relative_path: str, section: str | None
) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    outputs = cdir / "outputs"
    old_md = save_output(campaign_id, "mlr-review-summary.md", "old-md")
    old_html = save_output(campaign_id, "mlr-review.html", "old-html")
    target = cdir / relative_path if section is None else cdir / section / relative_path
    target.unlink()

    response = _result(render_mlr_review({"campaign_brief_id": campaign_id}))

    assert "error" in response
    assert old_md.read_bytes() == b"old-md"
    assert old_html.read_bytes() == b"old-html"
    assert not (outputs / "package-manifest.json").exists()


@pytest.mark.parametrize(
    "relative_path", ["copy-banner.json", "copy-poster.json", "outputs/banner.svg", "outputs/poster.pdf"]
)
def test_every_non_email_channel_artifact_is_required(tmp_path: Path, relative_path: str) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path)
    target = cdir / relative_path
    target.unlink()

    response = _result(render_mlr_review({"campaign_brief_id": campaign_id}))

    assert response["error"]["code"] == "mlr_package_incomplete"
    assert relative_path in response["error"]["items"]


def test_incomplete_preflight_reports_all_actionable_missing_items(tmp_path: Path) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path)
    missing = [
        "approved-claims.json",
        "brand-components.json",
        "copy-banner.json",
        "validation/source-evidence.json",
        "outputs/poster.pdf",
    ]
    for relative_path in missing:
        (cdir / relative_path).unlink()

    response = _result(render_mlr_review({"campaign_brief_id": campaign_id}))

    assert response["error"]["code"] == "mlr_package_incomplete"
    assert set(missing) <= set(response["error"]["items"])


def test_reordered_channels_are_preserved_and_unrelated_regular_outputs_are_excluded(tmp_path: Path) -> None:
    expected = ["poster", "email", "banner"]
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=expected)
    (cdir / "outputs" / "operator-note.txt").write_text("do not package", encoding="utf-8")

    plan = build_review_plan(campaign_id)
    exported = _result(export_mlr_package({"campaign_brief_id": campaign_id}))

    assert [item["channel"] for item in plan.model["channels"]] == expected
    with zipfile.ZipFile(Path(exported["archive_path"])) as archive:
        assert "outputs/operator-note.txt" not in archive.namelist()


@pytest.mark.parametrize("channels", [["email", "email"], ["email", "video"]])
def test_duplicate_or_unsupported_channels_fail_without_creating_paths(tmp_path: Path, channels: list[str]) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    brief_path = cdir / "campaign-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    brief["channels"] = channels
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    before = sorted(path.relative_to(cdir) for path in cdir.rglob("*"))

    response = _result(render_mlr_review({"campaign_brief_id": campaign_id}))

    assert response["error"]["code"] == "invalid_campaign_artifacts"
    assert sorted(path.relative_to(cdir) for path in cdir.rglob("*")) == before


def test_stale_gate_and_invalid_workflow_preserve_every_prior_export_byte(tmp_path: Path) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    exported = _result(export_mlr_package({"campaign_brief_id": campaign_id}))
    assert "error" not in exported, exported
    tracked = [
        cdir / "outputs" / "mlr-review-summary.md",
        cdir / "outputs" / "mlr-review.html",
        Path(exported["manifest_path"]),
        Path(exported["archive_path"]),
    ]
    before = {path: path.read_bytes() for path in tracked}
    journey = load_artifact(campaign_id, "audience-journey.json")
    journey["target_segment"] = "mismatched audience"
    save_artifact(campaign_id, "audience-journey.json", journey)

    response = _result(export_mlr_package({"campaign_brief_id": campaign_id}))

    assert response["error"]["code"] == "invalid_campaign_artifacts"
    assert all(path.read_bytes() == payload for path, payload in before.items())


def test_invalid_destination_after_changed_notes_preserves_every_prior_export_byte(tmp_path: Path) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    first = _result(export_mlr_package({"campaign_brief_id": campaign_id, "reviewer_notes": "FIRST NOTES"}))
    assert "error" not in first, first
    tracked = [
        cdir / "outputs" / "mlr-review-summary.md",
        cdir / "outputs" / "mlr-review.html",
        Path(first["manifest_path"]),
        Path(first["archive_path"]),
    ]
    before = {path: path.read_bytes() for path in tracked}
    invalid_destination = tmp_path / "not-a-directory"
    invalid_destination.write_text("operator-owned", encoding="utf-8")

    response = _result(
        export_mlr_package(
            {
                "campaign_brief_id": campaign_id,
                "reviewer_notes": "SECOND NOTES",
                "destination_dir": str(invalid_destination),
            }
        )
    )

    assert response["error"]["code"] == "unsafe_destination"
    assert all(path.read_bytes() == payload for path, payload in before.items())
    assert invalid_destination.read_bytes() == b"operator-owned"


def test_gate_race_after_model_build_preserves_prior_review(tmp_path: Path) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    old_md = save_output(campaign_id, "mlr-review-summary.md", "old-md")
    old_html = save_output(campaign_id, "mlr-review.html", "old-html")
    plan = build_review_plan(campaign_id)
    report_path = cdir / "validation" / "policy-checks.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["overall_pass"] = False
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(MlrContractError, match="changed during packaging") as captured:
        publish_review(plan)

    assert captured.value.code in {"artifact_changed", "validation_not_current"}
    assert old_md.read_bytes() == b"old-md"
    assert old_html.read_bytes() == b"old-html"


def test_review_write_failure_rolls_back_both_outputs_and_cleans_temps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    outputs = cdir / "outputs"
    old_md = save_output(campaign_id, "mlr-review-summary.md", "old-md")
    old_html = save_output(campaign_id, "mlr-review.html", "old-html")
    plan = build_review_plan(campaign_id)
    real_link = os.link
    failed = False

    def fail_second_placement(source, destination, **kwargs) -> None:
        nonlocal failed
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not failed
            and destination_path.name == "mlr-review-summary.md"
            and source_path.name.startswith(".mlr-review-summary.md.")
            and ".backup." not in source_path.name
        ):
            failed = True
            raise OSError("injected second placement failure")
        real_link(source, destination, **kwargs)

    monkeypatch.setattr("shared.filesystem.os.link", fail_second_placement)

    with pytest.raises(MlrContractError) as captured:
        publish_review(plan)

    assert captured.value.code == "output_write_failed"
    assert old_md.read_bytes() == b"old-md"
    assert old_html.read_bytes() == b"old-html"
    assert not [path for path in outputs.iterdir() if path.name.startswith(".")]


def test_export_reports_every_retained_backup_when_success_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id, _cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    prior = _result(export_mlr_package({"campaign_brief_id": campaign_id, "reviewer_notes": "FIRST"}))
    assert "error" not in prior, prior
    real_unlink = os.unlink
    retained_names: list[str] = []

    def fail_backup_cleanup(path, *args, **kwargs) -> None:
        name = Path(path).name
        if ".backup." in name:
            retained_names.append(name)
            raise OSError(errno.EIO, "injected backup cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr("shared.filesystem.os.unlink", fail_backup_cleanup)

    response = _result(export_mlr_package({"campaign_brief_id": campaign_id, "reviewer_notes": "SECOND"}))

    assert retained_names
    assert response["error"]["code"] == "output_write_failed"
    recovery_paths = [Path(item.removeprefix("Original target retained at ")) for item in response["error"]["items"]]
    assert {path.name for path in recovery_paths} == set(retained_names)
    assert all(path.is_file() for path in recovery_paths)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in recovery_paths)


def test_export_reports_retained_temporary_when_cleanup_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id, _cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    real_unlink = os.unlink
    temporary_attempts = 0

    def retain_one_review_temporary(path, *args, **kwargs) -> None:
        nonlocal temporary_attempts
        name = Path(path).name
        if name.startswith(".mlr-review-summary.md.") and ".backup." not in name and ".recovery." not in name:
            temporary_attempts += 1
            if temporary_attempts == 1:
                return
            raise OSError(errno.EIO, "injected temporary cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr("shared.filesystem.os.unlink", retain_one_review_temporary)

    response = _result(export_mlr_package({"campaign_brief_id": campaign_id}))

    assert temporary_attempts == 2
    assert response["error"]["code"] == "output_write_failed"
    assert len(response["error"]["items"]) == 1
    retained = Path(response["error"]["items"][0].removeprefix("Original target retained at "))
    assert retained.name.startswith(".mlr-review-summary.md.")
    assert retained.is_file()
    assert stat.S_IMODE(retained.stat().st_mode) == 0o600


def test_export_surfaces_recovery_durability_uncertainty_without_claiming_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shared.filesystem import SecurePublishError

    campaign_id, _cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    import open_pharma_plugins_campaign_studio._mlr_package as mlr_package

    note = "Recovery durability is uncertain; staged bytes could not be durably recreated."

    def fail_with_uncertainty(*args, **kwargs) -> None:
        raise SecurePublishError(
            "cleanup_failed",
            tmp_path / "uncertain-output",
            "Rollback cleanup durability could not be verified.",
            recovery_notes=(note,),
        )

    monkeypatch.setattr(mlr_package, "secure_atomic_publish", fail_with_uncertainty)

    response = _result(export_mlr_package({"campaign_brief_id": campaign_id}))

    assert response["error"]["code"] == "output_write_failed"
    assert response["error"]["items"] == [note]


def test_export_distinguishes_exact_recovery_from_partial_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shared.filesystem import SecurePublishError

    campaign_id, _cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    import open_pharma_plugins_campaign_studio._mlr_package as mlr_package

    exact = tmp_path / ".review.recovery.exact"
    exact.write_bytes(b"EXACT RECOVERY")
    exact.chmod(0o600)
    partial = tmp_path / ".review.recovery.partial"
    partial.write_bytes(b"PARTIAL")
    partial.chmod(0o600)
    conflict = tmp_path / ".review.recovery.concurrent"
    conflict.write_bytes(b"CONCURRENT")
    conflict.chmod(0o600)
    note = "Recovery durability is uncertain after recreated-file verification failed."

    def fail_with_retained_recovery(*args, **kwargs) -> None:
        raise SecurePublishError(
            "write_failed",
            tmp_path / "review.html",
            "Outputs could not be written atomically.",
            recovery_paths=(exact,),
            residue_paths=(partial,),
            conflict_paths=(conflict,),
            recovery_notes=(note,),
        )

    monkeypatch.setattr(mlr_package, "secure_atomic_publish", fail_with_retained_recovery)

    response = _result(export_mlr_package({"campaign_brief_id": campaign_id}))

    assert response["error"]["code"] == "output_write_failed"
    assert response["error"]["items"] == [
        f"Original target retained at {exact}",
        f"Private recovery residue retained at {partial}",
        f"Concurrent recovery-name conflict retained at {conflict}",
        note,
    ]


def test_safe_destination_copy_and_destination_rejections(tmp_path: Path) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    destination = tmp_path / "review handoff"

    copied = _result(export_mlr_package({"campaign_brief_id": campaign_id, "destination_dir": str(destination)}))
    assert "error" not in copied, copied

    copied_path = Path(copied["destination_archive_path"])
    assert copied_path.parent == destination.resolve()
    assert copied_path.read_bytes() == Path(copied["archive_path"]).read_bytes()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert stat.S_IMODE(copied_path.stat().st_mode) == 0o600
    non_directory = tmp_path / "a-file"
    non_directory.write_text("no", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink = tmp_path / "linked"
    symlink.symlink_to(outside, target_is_directory=True)
    unsafe_destinations = [str(non_directory), str(symlink), str(cdir / "outputs"), "../escape", "bad\x00dir"]
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "destination-fifo"
        os.mkfifo(fifo)
        unsafe_destinations.append(str(fifo))
    for unsafe in unsafe_destinations:
        rejected = _result(export_mlr_package({"campaign_brief_id": campaign_id, "destination_dir": unsafe}))
        assert rejected["error"]["code"] == "unsafe_destination"


def test_nested_campaign_destination_is_rejected_without_poisoning_outputs(tmp_path: Path) -> None:
    """A rejected handoff path must not create an unsafe directory inside campaign evidence."""
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    nested = cdir / "outputs" / "handoff"

    rejected = _result(export_mlr_package({"campaign_brief_id": campaign_id, "destination_dir": str(nested)}))

    assert rejected["error"]["code"] == "unsafe_destination"
    assert not nested.exists()
    retry = _result(export_mlr_package({"campaign_brief_id": campaign_id}))
    assert "error" not in retry, retry


def test_existing_destination_mode_is_preserved_while_archive_is_private(tmp_path: Path) -> None:
    campaign_id, _cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    destination = tmp_path / "operator-handoff"
    destination.mkdir(mode=0o750)
    destination.chmod(0o750)

    response = _result(export_mlr_package({"campaign_brief_id": campaign_id, "destination_dir": str(destination)}))

    assert "error" not in response, response
    assert stat.S_IMODE(destination.stat().st_mode) == 0o750
    assert stat.S_IMODE(Path(response["destination_archive_path"]).stat().st_mode) == 0o600


@pytest.mark.parametrize("existing_final", [False, True], ids=["create-nested", "existing-nested"])
def test_destination_ancestor_symlink_swap_never_writes_outside_lexical_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_final: bool
) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    prior = _result(export_mlr_package({"campaign_brief_id": campaign_id, "reviewer_notes": "FIRST"}))
    assert "error" not in prior, prior
    tracked = {path: path.read_bytes() for path in (cdir / "outputs").iterdir() if path.is_file()}
    trusted = tmp_path / "trusted"
    trusted.mkdir(mode=0o750)
    trusted.chmod(0o750)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o750)
    outside.chmod(0o750)
    relative = Path("nested") / "handoff"
    destination = trusted / relative
    outside_destination = outside / relative
    if existing_final:
        destination.mkdir(parents=True, mode=0o750)
        destination.chmod(0o750)
        outside_destination.mkdir(parents=True, mode=0o750)
        outside_destination.chmod(0o750)
    marker = outside / "operator-owned.txt"
    marker.write_bytes(b"OUTSIDE OPERATOR BYTES")
    outside_before = {
        path.relative_to(outside): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in outside.rglob("*")
        if path.is_file()
    }
    outside_directory_modes = {
        path.relative_to(outside): stat.S_IMODE(path.stat().st_mode)
        for path in [outside, *(path for path in outside.rglob("*") if path.is_dir())]
    }
    displaced = tmp_path / "trusted-original"
    import open_pharma_plugins_campaign_studio._mlr_package as mlr_package

    real_inspect = mlr_package._inspect_destination_ancestors
    injected = False

    def inspect_then_swap(path: Path) -> None:
        nonlocal injected
        real_inspect(path)
        if not injected:
            injected = True
            trusted.rename(displaced)
            trusted.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(mlr_package, "_inspect_destination_ancestors", inspect_then_swap)

    response = _result(
        export_mlr_package(
            {
                "campaign_brief_id": campaign_id,
                "reviewer_notes": "SECOND",
                "destination_dir": str(destination),
            }
        )
    )

    assert injected is True
    assert response["error"]["code"] == "unsafe_destination"
    assert {path: path.read_bytes() for path in (cdir / "outputs").iterdir() if path.is_file()} == tracked
    assert {
        path.relative_to(outside): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in outside.rglob("*")
        if path.is_file()
    } == outside_before
    assert {
        path.relative_to(outside): stat.S_IMODE(path.stat().st_mode)
        for path in [outside, *(path for path in outside.rglob("*") if path.is_dir())]
    } == outside_directory_modes
    assert displaced.exists()


def test_destination_replacement_race_preserves_prior_manifest_and_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id, _cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    prior = _result(export_mlr_package({"campaign_brief_id": campaign_id}))
    manifest = Path(prior["manifest_path"])
    archive = Path(prior["archive_path"])
    before = {manifest: manifest.read_bytes(), archive: archive.read_bytes()}
    destination = tmp_path / "handoff"
    destination.mkdir()
    displaced = tmp_path / "handoff-displaced"
    import open_pharma_plugins_campaign_studio._mlr_package as mlr_package

    real_recheck = mlr_package._recheck_plan
    calls = 0

    def replace_destination_during_final_recheck(plan) -> None:
        nonlocal calls
        calls += 1
        real_recheck(plan)
        if calls == 1:
            destination.rename(displaced)
            destination.mkdir()

    monkeypatch.setattr(mlr_package, "_recheck_plan", replace_destination_during_final_recheck)

    response = _result(export_mlr_package({"campaign_brief_id": campaign_id, "destination_dir": str(destination)}))

    assert response["error"]["code"] == "unsafe_destination"
    assert not list(destination.iterdir())
    assert all(path.read_bytes() == payload for path, payload in before.items())


def test_destination_replacement_after_final_recheck_is_rejected_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    first = _result(export_mlr_package({"campaign_brief_id": campaign_id, "reviewer_notes": "FIRST NOTES"}))
    assert "error" not in first, first
    outputs = cdir / "outputs"
    before = {path.name: path.read_bytes() for path in outputs.iterdir() if path.is_file()}
    destination = tmp_path / "handoff"
    destination.mkdir()
    displaced = tmp_path / "handoff-original"
    import open_pharma_plugins_campaign_studio._mlr_package as mlr_package

    real_recheck = mlr_package._recheck_destination

    def replace_after_recheck(captured_destination) -> None:
        real_recheck(captured_destination)
        destination.rename(displaced)
        destination.mkdir()

    monkeypatch.setattr(mlr_package, "_recheck_destination", replace_after_recheck)

    response = _result(
        export_mlr_package(
            {
                "campaign_brief_id": campaign_id,
                "reviewer_notes": "SECOND NOTES",
                "destination_dir": str(destination),
            }
        )
    )

    assert response["error"]["code"] == "unsafe_destination"
    assert {path.name: path.read_bytes() for path in outputs.iterdir() if path.is_file()} == before
    assert not list(destination.iterdir())
    assert not list(displaced.iterdir())


@pytest.mark.parametrize("force_fallback", [False, True], ids=["dirfd", "path-fallback"])
@pytest.mark.parametrize("target_kind", ["review", "manifest", "archive", "destination"])
def test_target_replacement_immediately_before_backup_is_preserved_and_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    force_fallback: bool,
    target_kind: str,
) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    destination = tmp_path / "handoff"
    arguments = {"campaign_brief_id": campaign_id, "reviewer_notes": "SAME NOTES"}
    if target_kind == "destination":
        arguments["destination_dir"] = str(destination)
    first = _result(export_mlr_package(arguments))
    assert "error" not in first, first
    targets = {
        "review": cdir / "outputs" / "mlr-review-summary.md",
        "manifest": Path(first["manifest_path"]),
        "archive": Path(first["archive_path"]),
        "destination": Path(first.get("destination_archive_path", first["archive_path"])),
    }
    target = targets[target_kind]
    tracked = {
        path: path.read_bytes()
        for directory in {cdir / "outputs", target.parent}
        for path in directory.iterdir()
        if path.is_file()
    }
    concurrent = b"CONCURRENT OPERATOR BYTES"
    replacement = target.parent / f".{target.name}.concurrent-source"
    replacement.write_bytes(concurrent)
    target_parent_identity = (target.parent.stat().st_dev, target.parent.stat().st_ino)

    import shared.filesystem as filesystem

    monkeypatch.setattr(filesystem, "_dirfd_transactions_supported", lambda: not force_fallback, raising=False)
    real_replace = os.replace
    injected = False

    def replace_with_race(source, destination_path, **kwargs) -> None:
        nonlocal injected
        source_name = Path(source).name
        destination_name = Path(destination_path).name
        source_fd = kwargs.get("src_dir_fd")
        is_target_directory = (
            source_fd is not None and (os.fstat(source_fd).st_dev, os.fstat(source_fd).st_ino) == target_parent_identity
        ) or (source_fd is None and Path(source).parent == target.parent)
        if (
            not injected
            and is_target_directory
            and source_name == target.name
            and destination_name.startswith(f".{target.name}.backup.")
        ):
            injected = True
            if source_fd is None:
                real_replace(replacement, target)
            else:
                real_replace(
                    replacement.name,
                    target.name,
                    src_dir_fd=source_fd,
                    dst_dir_fd=source_fd,
                )
        real_replace(source, destination_path, **kwargs)

    monkeypatch.setattr(filesystem.os, "replace", replace_with_race)

    response = _result(export_mlr_package(arguments))

    assert injected is True
    assert response["error"]["code"] == ("unsafe_destination" if target_kind == "destination" else "unsafe_output_path")
    assert target.read_bytes() == concurrent
    for path, payload in tracked.items():
        if path != target:
            assert path.read_bytes() == payload
    assert not [path for path in target.parent.iterdir() if path.name.startswith(".")]


@pytest.mark.parametrize("force_fallback", [False, True], ids=["dirfd", "path-fallback"])
@pytest.mark.parametrize("target_kind", ["review", "manifest", "archive", "destination"])
def test_concurrent_target_at_final_placement_is_not_clobbered_and_original_is_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    force_fallback: bool,
    target_kind: str,
) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    destination = tmp_path / "handoff"
    arguments = {"campaign_brief_id": campaign_id, "reviewer_notes": "SAME NOTES"}
    if target_kind == "destination":
        arguments["destination_dir"] = str(destination)
    first = _result(export_mlr_package(arguments))
    assert "error" not in first, first
    targets = {
        "review": cdir / "outputs" / "mlr-review-summary.md",
        "manifest": Path(first["manifest_path"]),
        "archive": Path(first["archive_path"]),
        "destination": Path(first.get("destination_archive_path", first["archive_path"])),
    }
    target = targets[target_kind]
    tracked = {
        path: path.read_bytes()
        for directory in {cdir / "outputs", target.parent}
        for path in directory.iterdir()
        if path.is_file()
    }
    concurrent = b"CONCURRENT FINAL PLACEMENT BYTES"
    target_parent_identity = (target.parent.stat().st_dev, target.parent.stat().st_ino)

    import shared.filesystem as filesystem

    monkeypatch.setattr(filesystem, "_dirfd_transactions_supported", lambda: not force_fallback)
    real_replace = os.replace
    real_link = os.link
    injected = False

    def is_target_placement(source, destination_path, kwargs) -> bool:
        source_name = Path(source).name
        source_fd = kwargs.get("src_dir_fd")
        is_target_directory = (
            source_fd is not None and (os.fstat(source_fd).st_dev, os.fstat(source_fd).st_ino) == target_parent_identity
        ) or (source_fd is None and Path(source).parent == target.parent)
        return (
            is_target_directory
            and Path(destination_path).name == target.name
            and source_name.startswith(f".{target.name}.")
            and ".backup." not in source_name
            and ".recovery." not in source_name
        )

    def create_concurrent(directory_fd: int | None) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = (
            os.open(target.name, flags, 0o600, dir_fd=directory_fd)
            if directory_fd is not None
            else os.open(target, flags, 0o600)
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(concurrent)

    def replace_with_race(source, destination_path, **kwargs) -> None:
        nonlocal injected
        if not injected and is_target_placement(source, destination_path, kwargs):
            injected = True
            create_concurrent(kwargs.get("dst_dir_fd"))
        real_replace(source, destination_path, **kwargs)

    def link_with_race(source, destination_path, **kwargs) -> None:
        nonlocal injected
        if not injected and is_target_placement(source, destination_path, kwargs):
            injected = True
            create_concurrent(kwargs.get("dst_dir_fd"))
        real_link(source, destination_path, **kwargs)

    monkeypatch.setattr(filesystem.os, "replace", replace_with_race)
    monkeypatch.setattr(filesystem.os, "link", link_with_race)

    response = _result(export_mlr_package(arguments))

    assert injected is True
    assert response["error"]["code"] == ("unsafe_destination" if target_kind == "destination" else "unsafe_output_path")
    assert target.read_bytes() == concurrent
    for path, payload in tracked.items():
        if path != target:
            assert path.read_bytes() == payload
    recovery_paths = [Path(item.removeprefix("Original target retained at ")) for item in response["error"]["items"]]
    assert len(recovery_paths) == 1
    assert recovery_paths[0].parent == target.parent
    assert recovery_paths[0].name.startswith(f".{target.name}.recovery.")
    assert recovery_paths[0].read_bytes() == tracked[target]
    residue = [path.name for path in target.parent.iterdir() if path.name.startswith(".")]
    assert residue == [recovery_paths[0].name]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_fifo_and_oversized_artifacts_are_rejected_without_blocking_or_overwrite(tmp_path: Path) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    old_md = save_output(campaign_id, "mlr-review-summary.md", "old-md")
    old_html = save_output(campaign_id, "mlr-review.html", "old-html")
    source_path = cdir / "validation" / "source-evidence.json"
    source_path.unlink()
    os.mkfifo(source_path)

    fifo_response = _result(render_mlr_review({"campaign_brief_id": campaign_id}))
    source_path.unlink()
    source_path.write_bytes(b"[" + (b" " * 2_000_001) + b"]")
    oversized_response = _result(render_mlr_review({"campaign_brief_id": campaign_id}))

    assert fifo_response["error"]["code"] == "mlr_package_incomplete"
    assert oversized_response["error"]["code"] == "mlr_package_incomplete"
    assert all(
        "validation/source-evidence.json" in response["error"]["items"][0]
        for response in (fifo_response, oversized_response)
    )
    assert old_md.read_bytes() == b"old-md"
    assert old_html.read_bytes() == b"old-html"


def test_closed_schemas_and_direct_invalid_calls_are_structured_without_store_creation(tmp_path: Path) -> None:
    for model in (RenderMlrReviewArgs, ExportMlrPackageArgs):
        schema = tool_schema(model)
        assert schema["additionalProperties"] is False
    for handler in (render_mlr_review, export_mlr_package):
        response = _result(handler({"campaign_brief_id": "../escape", "unexpected": True}))
        assert response["error"]["code"] == "invalid_arguments"
    assert not (tmp_path / "store").exists()


def test_unsafe_output_sibling_and_artifact_symlink_fail_without_following(tmp_path: Path) -> None:
    campaign_id, cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    outputs = cdir / "outputs"
    outside = tmp_path / "outside.html"
    outside.write_text("outside", encoding="utf-8")
    (outputs / "unsafe-link").symlink_to(outside)

    sibling = _result(render_mlr_review({"campaign_brief_id": campaign_id}))
    assert sibling["error"]["code"] == "unsafe_outputs_directory"
    (outputs / "unsafe-link").unlink()
    email = outputs / "email.html"
    email.unlink()
    email.symlink_to(outside)

    artifact = _result(render_mlr_review({"campaign_brief_id": campaign_id}))
    assert "error" in artifact
    assert outside.read_text(encoding="utf-8") == "outside"


def test_reviewer_notes_change_digest_and_unchanged_notes_do_not(tmp_path: Path) -> None:
    campaign_id, _cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    first = _result(export_mlr_package({"campaign_brief_id": campaign_id, "reviewer_notes": "first"}))
    same = _result(export_mlr_package({"campaign_brief_id": campaign_id, "reviewer_notes": "first"}))
    changed = _result(export_mlr_package({"campaign_brief_id": campaign_id, "reviewer_notes": "second"}))
    assert "error" not in first, first
    assert "error" not in same, same
    assert "error" not in changed, changed

    assert first["package_digest"] == same["package_digest"]
    assert changed["package_digest"] != first["package_digest"]
    assert Path(first["archive_path"]).exists()
    with zipfile.ZipFile(Path(changed["archive_path"])) as archive:
        assert all(not name.endswith(Path(first["archive_path"]).name) for name in archive.namelist())


def test_reviewer_notes_are_literal_plain_text_in_markdown(tmp_path: Path) -> None:
    campaign_id, _cdir = _seed_complete_campaign(tmp_path, channels=["email"])
    notes = (
        "![tracking](https://tracker.example/pixel) [run](javascript:alert(1))\n"
        "**bold** *emphasis* # heading <script>alert(2)</script> `code`"
    )

    result = _result(render_mlr_review({"campaign_brief_id": campaign_id, "reviewer_notes": notes}))
    markdown_path = next(Path(item["absolute_path"]) for item in result["outputs"] if item["path"].endswith(".md"))
    notes_markdown = markdown_path.read_text(encoding="utf-8").split("## Reviewer notes", 1)[1]
    rendered_notes = MarkdownIt().render(notes_markdown)

    assert all(tag not in rendered_notes for tag in ("<a ", "<img ", "<em>", "<strong>", "<script"))
    assert "![tracking](https://tracker.example/pixel)" in rendered_notes
    assert "[run](javascript:alert(1))" in rendered_notes
    assert "**bold** *emphasis* # heading" in rendered_notes


def test_real_stdio_mcp_discovers_and_calls_review_tools(tmp_path: Path) -> None:
    campaign_id, _cdir = _seed_complete_campaign(tmp_path, channels=["email"])

    async def exercise() -> None:
        env = os.environ.copy()
        env["OPEN_PHARMA_CAMPAIGN_STORE_DIR"] = str(tmp_path / "store")
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "open_pharma_plugins_campaign_studio"],
            env=env,
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                by_name = {tool.name: tool for tool in tools.tools}
                assert {"render_mlr_review", "export_mlr_package"} <= set(by_name)
                assert by_name["render_mlr_review"].inputSchema["additionalProperties"] is False
                rendered = await session.call_tool("render_mlr_review", {"campaign_brief_id": campaign_id})
                payload = json.loads(rendered.content[0].text)
                assert "error" not in payload and len(payload["outputs"]) == 2

    anyio.run(exercise)
