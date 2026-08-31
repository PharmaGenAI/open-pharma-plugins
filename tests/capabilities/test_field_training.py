"""Tests for the field-training capability."""

import json
import os
from pathlib import Path

import pytest

import open_pharma_plugins_field_training as ft

_FIXTURES = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "src",
    "capabilities",
    "field-training",
    "open_pharma_plugins_field_training",
    "fixtures",
)
_SAMPLE_PDF = os.path.join(_FIXTURES, "sample_product_message.pdf")
_SAMPLE_PPTX = os.path.join(_FIXTURES, "sample_training_deck.pptx")


# ---------------------------------------------------------------------------
# Package sanity
# ---------------------------------------------------------------------------


def test_lists_five_tools():
    names = {t["name"] for t in ft.list_tools()}
    assert names == {"ingest_document", "list_documents", "search_content", "get_document_page", "render_output"}


def test_version():
    assert ft.__version__ == "1.1.1"


# ---------------------------------------------------------------------------
# Tool handlers exist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool",
    ["ingest_document", "list_documents", "search_content", "get_document_page", "render_output"],
)
def test_handler_exists(tool):
    assert callable(ft.get_handler(tool))


# ---------------------------------------------------------------------------
# Ingest + list + search + get_page (end-to-end, isolated via tmp dir)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_content_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_PHARMA_TRAINING_CONTENT_DIR", str(tmp_path / "training"))


def test_ingest_pdf():
    result = ft.get_handler("ingest_document")({"file_path": _SAMPLE_PDF})
    data = json.loads(result[0]["text"])
    assert "document_id" in data
    assert data["file_type"] == "pdf"
    assert data["total_pages"] > 0


def test_ingest_pptx():
    result = ft.get_handler("ingest_document")({"file_path": _SAMPLE_PPTX})
    data = json.loads(result[0]["text"])
    assert data["file_type"] == "pptx"
    assert data["total_pages"] > 0


def test_ingest_missing_file():
    result = ft.get_handler("ingest_document")({"file_path": "/nonexistent/file.pdf"})
    data = json.loads(result[0]["text"])
    assert "error" in data


def test_list_documents_after_ingest():
    ft.get_handler("ingest_document")({"file_path": _SAMPLE_PDF})
    result = ft.get_handler("list_documents")({})
    data = json.loads(result[0]["text"])
    assert data["total"] >= 1
    assert any(d["file_type"] == "pdf" for d in data["documents"])


def test_search_content_after_ingest():
    ft.get_handler("ingest_document")({"file_path": _SAMPLE_PDF})
    result = ft.get_handler("search_content")({"query": "efficacy"})
    data = json.loads(result[0]["text"])
    assert "results" in data


def test_search_content_document_scope_excludes_unrelated_persisted_document():
    from open_pharma_plugins_field_training._content_store import save_document

    common_page = {
        "page_number": 1,
        "text": "Approved efficacy scope sentinel.",
        "page_type": "page",
        "slide_title": None,
        "speaker_notes": None,
    }
    for document_id, file_name in (("submitted_doc", "submitted.pdf"), ("unrelated_doc", "unrelated.pdf")):
        save_document(
            {
                "document_id": document_id,
                "file_name": file_name,
                "file_path": f"/approved/{file_name}",
                "file_type": "pdf",
                "title": file_name,
                "total_pages": 1,
                "pages": [common_page],
                "ingested_at": "2026-08-28T00:00:00Z",
            }
        )

    result = ft.get_handler("search_content")({"query": "scope sentinel", "document_id": "submitted_doc"})
    data = json.loads(result[0]["text"])

    assert data["results"]
    assert {item["document_id"] for item in data["results"]} == {"submitted_doc"}


def test_get_document_page():
    ingest = ft.get_handler("ingest_document")({"file_path": _SAMPLE_PDF})
    doc_id = json.loads(ingest[0]["text"])["document_id"]
    result = ft.get_handler("get_document_page")({"document_id": doc_id, "page_number": 1})
    data = json.loads(result[0]["text"])
    assert "text" in data
    assert data["page_number"] == 1


def test_get_document_page_missing_doc():
    result = ft.get_handler("get_document_page")({"document_id": "nonexistent", "page_number": 1})
    data = json.loads(result[0]["text"])
    assert "error" in data


def test_render_output():
    sample = {
        "title": "Test Package",
        "generated_at": "2026-08-22T00:00:00Z",
        "modules": [],
        "source_documents": [],
    }
    result = ft.get_handler("render_output")(
        {
            "output_type": "learning_package",
            "content_json": json.dumps(sample),
            "file_name": "test_pkg",
        }
    )
    data = json.loads(result[0]["text"])
    assert data["success"] is True
    assert data["json_path"].endswith(".json")
    assert data["html_path"].endswith(".html")


def test_render_output_invalid_type():
    result = ft.get_handler("render_output")(
        {
            "output_type": "invalid_type",
            "content_json": "{}",
        }
    )
    data = json.loads(result[0]["text"])
    assert "error" in data


def test_render_output_rejects_traversal_filename(tmp_path):
    result = ft.get_handler("render_output")(
        {
            "output_type": "learning_package",
            "content_json": json.dumps(
                {
                    "title": "Traversal",
                    "modules": [],
                    "source_documents": [],
                    "generated_at": "2026-08-26T00:00:00Z",
                }
            ),
            "file_name": "../../escaped",
        }
    )
    data = json.loads(result[0]["text"])
    assert "error" in data
    assert not (tmp_path / "escaped.json").exists()


def _grounded_learning_package(*, excerpt: str = "Median overall survival was 24.1 months.") -> dict:
    return {
        "title": "Grounded package",
        "modules": [
            {
                "title": "Efficacy",
                "objectives": [{"objective": "Explain the endpoint", "bloom_level": "understand"}],
                "key_messages": [
                    {
                        "message": "Median overall survival was 24.1 months.",
                        "category": "efficacy",
                        "sources": [
                            {
                                "document_id": "approved_doc",
                                "document_name": "approved.pdf",
                                "page_number": 1,
                                "excerpt": excerpt,
                            }
                        ],
                    }
                ],
                "talking_points": [],
                "common_objections": [],
            }
        ],
        "source_documents": ["approved.pdf"],
        "generated_at": "2026-08-26T00:00:00Z",
    }


def _grounded_roleplay_kit() -> dict:
    source = {
        "document_id": "approved_doc",
        "document_name": "approved.pdf",
        "page_number": 1,
        "excerpt": "Median overall survival was 24.1 months.",
    }
    safety_source = {
        "document_id": "approved_doc",
        "document_name": "approved.pdf",
        "page_number": 1,
        "excerpt": "Safety information follows.",
    }
    return {
        "title": "Approved message practice",
        "topic": "Efficacy and safety",
        "hcp_persona": "Evidence-focused oncologist with limited time",
        "scenario": "The HCP asks for the headline result and its safety context.",
        "objectives": [
            {
                "objective": "Deliver the approved efficacy message with fair balance",
                "bloom_level": "apply",
            }
        ],
        "key_messages": [
            {
                "message": "Median overall survival was 24.1 months.",
                "category": "efficacy",
                "sources": [source],
            },
            {
                "message": "Safety information follows.",
                "category": "safety",
                "sources": [safety_source],
            },
        ],
        "common_objections": [
            {
                "objection": "What safety context should I consider?",
                "approved_response": "Safety information follows.",
                "sources": [safety_source],
            }
        ],
        "facilitator_prompts": [
            {
                "stage": "opening",
                "prompt": "Ask the representative for the headline evidence.",
                "coaching_intent": "Observe whether the response includes fair balance.",
            }
        ],
        "evaluation_rubric": [
            {
                "criterion": "Approved efficacy language",
                "weight_pct": 60,
                "evidence_to_observe": ["Uses the sourced overall-survival result"],
            },
            {
                "criterion": "Fair balance",
                "weight_pct": 40,
                "evidence_to_observe": ["Includes the sourced safety context"],
            },
        ],
        "source_documents": ["approved.pdf"],
        "generated_at": "2026-08-28T00:00:00Z",
    }


def _grounded_assessment() -> dict:
    source = {
        "document_id": "approved_doc",
        "document_name": "approved.pdf",
        "page_number": 1,
        "excerpt": "Median overall survival was 24.1 months.",
    }
    return {
        "title": "Approved message assessment",
        "mcq_questions": [
            {
                "question_id": "MCQ-001",
                "question": "What was the median overall survival?",
                "options": [
                    {"label": "A", "text": "12.0 months"},
                    {"label": "B", "text": "24.1 months"},
                    {"label": "C", "text": "36.2 months"},
                    {"label": "D", "text": "Not reported"},
                ],
                "correct_answer": "B",
                "explanation": "The approved source reports 24.1 months.",
                "source": source,
                "difficulty": "easy",
            }
        ],
        "scenario_questions": [],
        "total_points": 1,
        "passing_score_pct": 0.8,
        "source_documents": ["approved.pdf"],
        "generated_at": "2026-08-28T00:00:00Z",
    }


def _grounded_roleplay_scorecard() -> dict:
    return {
        "hcp_persona": "Evidence-focused oncologist",
        "topic": "Efficacy and safety",
        "turns": [
            {"speaker": "hcp", "message": "What is the headline result?"},
            {"speaker": "rep", "message": "Median overall survival was 24.1 months."},
        ],
        "claims_evaluated": [
            {
                "claim": "Median overall survival was 24.1 months.",
                "status": "correct",
                "source": {
                    "document_id": "approved_doc",
                    "document_name": "approved.pdf",
                    "page_number": 1,
                    "excerpt": "Median overall survival was 24.1 months.",
                },
                "feedback": "Matches the approved source.",
            }
        ],
        "score": 1.0,
        "strengths": ["Used approved language"],
        "areas_for_improvement": ["Add the sourced safety context"],
        "source_documents": ["approved.pdf"],
    }


def _save_approved_document():
    from open_pharma_plugins_field_training._content_store import save_document

    save_document(
        {
            "document_id": "approved_doc",
            "file_name": "approved.pdf",
            "file_path": "/approved/approved.pdf",
            "file_type": "pdf",
            "title": "Approved source",
            "total_pages": 1,
            "pages": [
                {
                    "page_number": 1,
                    "page_type": "page",
                    "text": "Median overall survival was 24.1 months. Safety information follows.",
                }
            ],
            "ingested_at": "2026-08-26T00:00:00Z",
        }
    )


def test_render_output_rejects_unknown_schema_fields():
    sample = {
        "title": "Unexpected data",
        "modules": [],
        "source_documents": [],
        "generated_at": "2026-08-26T00:00:00Z",
        "unreviewed_payload": {"anything": "goes"},
    }
    result = ft.get_handler("render_output")(
        {"output_type": "learning_package", "content_json": json.dumps(sample), "file_name": "invalid"}
    )
    assert "error" in json.loads(result[0]["text"])


def test_render_output_rejects_unknown_source_document():
    result = ft.get_handler("render_output")(
        {
            "output_type": "learning_package",
            "content_json": json.dumps(_grounded_learning_package()),
            "file_name": "unknown_source",
        }
    )
    data = json.loads(result[0]["text"])
    assert "error" in data
    assert "approved_doc" in data["error"]


def test_render_output_rejects_excerpt_not_present_on_page():
    _save_approved_document()
    result = ft.get_handler("render_output")(
        {
            "output_type": "learning_package",
            "content_json": json.dumps(_grounded_learning_package(excerpt="Unsupported superiority claim")),
            "file_name": "bad_excerpt",
        }
    )
    data = json.loads(result[0]["text"])
    assert "error" in data
    assert "excerpt" in data["error"].lower()


def test_render_output_accepts_schema_valid_source_grounded_content():
    _save_approved_document()
    result = ft.get_handler("render_output")(
        {
            "output_type": "learning_package",
            "content_json": json.dumps(_grounded_learning_package()),
            "file_name": "grounded",
        }
    )
    data = json.loads(result[0]["text"])
    assert data["success"] is True


def test_render_output_accepts_source_grounded_roleplay_kit():
    _save_approved_document()
    result = ft.get_handler("render_output")(
        {
            "output_type": "roleplay_kit",
            "content_json": json.dumps(_grounded_roleplay_kit()),
            "file_name": "roleplay_kit",
        }
    )
    data = json.loads(result[0]["text"])
    assert data["success"] is True
    assert data["output_type"] == "roleplay_kit"
    assert data["html_path"].endswith("roleplay_kit.html")


def test_render_output_rejects_roleplay_rubric_that_does_not_total_100():
    _save_approved_document()
    sample = _grounded_roleplay_kit()
    sample["evaluation_rubric"][0]["weight_pct"] = 50
    result = ft.get_handler("render_output")(
        {
            "output_type": "roleplay_kit",
            "content_json": json.dumps(sample),
            "file_name": "invalid_roleplay_kit",
        }
    )
    data = json.loads(result[0]["text"])
    assert "error" in data
    assert "100" in data["error"]


@pytest.mark.parametrize(
    "empty_field",
    [
        "objectives",
        "key_messages",
        "common_objections",
        "facilitator_prompts",
        "evaluation_rubric",
        "source_documents",
    ],
)
def test_render_output_rejects_incomplete_roleplay_kit_sections(empty_field):
    _save_approved_document()
    sample = _grounded_roleplay_kit()
    sample[empty_field] = []

    result = ft.get_handler("render_output")(
        {
            "output_type": "roleplay_kit",
            "content_json": json.dumps(sample),
            "file_name": "incomplete_roleplay_kit",
        }
    )
    data = json.loads(result[0]["text"])

    assert "error" in data
    assert empty_field in data["error"] or "100" in data["error"]


@pytest.mark.parametrize("claim_section", ["key_messages", "common_objections"])
def test_render_output_rejects_roleplay_claim_without_a_source(claim_section):
    _save_approved_document()
    sample = _grounded_roleplay_kit()
    sample[claim_section][0]["sources"] = []

    result = ft.get_handler("render_output")(
        {
            "output_type": "roleplay_kit",
            "content_json": json.dumps(sample),
            "file_name": "uncited_roleplay_kit",
        }
    )
    data = json.loads(result[0]["text"])

    assert "error" in data
    assert "sources" in data["error"]


def test_learning_package_html_has_offline_accessible_review_controls():
    _save_approved_document()
    result = ft.get_handler("render_output")(
        {
            "output_type": "learning_package",
            "content_json": json.dumps(_grounded_learning_package()),
            "file_name": "interactive_learning_package",
        }
    )
    data = json.loads(result[0]["text"])
    html = Path(data["html_path"]).read_text(encoding="utf-8")

    assert '<body data-output-type="learning-package">' in html
    assert 'aria-label="Document sections"' in html
    assert 'data-action="filter"' in html
    assert 'data-filter="efficacy"' in html
    assert 'data-action="print"' in html
    assert '<details class="source-disclosure">' in html
    assert "Draft for MLR review" in html
    assert "@media (prefers-reduced-motion: reduce)" in html
    assert "<script>" in html
    assert "<script src=" not in html
    assert "https://" not in html
    assert "http://" not in html


def test_rendered_html_prints_source_excerpts_even_when_disclosures_are_closed():
    _save_approved_document()
    result = ft.get_handler("render_output")(
        {
            "output_type": "learning_package",
            "content_json": json.dumps(_grounded_learning_package()),
            "file_name": "printable_learning_package",
        }
    )
    data = json.loads(result[0]["text"])
    html = Path(data["html_path"]).read_text(encoding="utf-8")

    assert ".source-disclosure:not([open])>blockquote{display:block}" in html


def test_learning_package_html_scopes_search_and_filters_to_each_module():
    _save_approved_document()
    sample = _grounded_learning_package()
    second_module = json.loads(json.dumps(sample["modules"][0]))
    second_module["title"] = "Second approved module"
    sample["modules"].append(second_module)

    result = ft.get_handler("render_output")(
        {
            "output_type": "learning_package",
            "content_json": json.dumps(sample),
            "file_name": "multi_module_learning_package",
        }
    )
    data = json.loads(result[0]["text"])
    html = Path(data["html_path"]).read_text(encoding="utf-8")

    assert html.count('data-filter-scope="module"') == 2
    assert "control.closest('[data-filter-scope]')" in html
    assert "event.target.closest('[data-filter-scope]')" in html


def test_path_first_skill_never_searches_the_global_persistent_store():
    root = Path(__file__).resolve().parents[2]
    skill = (root / "src/capabilities/field-training/skill/SKILL.md").read_text(encoding="utf-8")

    assert "all ingested content" not in skill.casefold()
    assert "Never omit `document_id`" in skill
    assert "once per collected document ID" in skill


def test_roleplay_kit_html_hides_facilitator_guidance_until_enabled():
    _save_approved_document()
    result = ft.get_handler("render_output")(
        {
            "output_type": "roleplay_kit",
            "content_json": json.dumps(_grounded_roleplay_kit()),
            "file_name": "interactive_roleplay_kit",
        }
    )
    data = json.loads(result[0]["text"])
    html = Path(data["html_path"]).read_text(encoding="utf-8")

    assert '<body data-output-type="roleplay-kit">' in html
    assert 'data-action="facilitator-mode"' in html
    assert 'aria-pressed="false">Facilitator mode</button>' in html
    assert 'class="facilitator-note" data-facilitator hidden' in html
    assert "Approved response" in html
    assert "Evaluation rubric" in html


def test_assessment_html_hides_answer_and_explanation_until_revealed():
    _save_approved_document()
    result = ft.get_handler("render_output")(
        {
            "output_type": "assessment",
            "content_json": json.dumps(_grounded_assessment()),
            "file_name": "interactive_assessment",
        }
    )
    data = json.loads(result[0]["text"])
    html = Path(data["html_path"]).read_text(encoding="utf-8")

    assert '<body data-output-type="assessment">' in html
    assert 'data-action="toggle-answer"' in html
    assert 'aria-controls="answer-MCQ-001"' in html
    assert 'id="answer-MCQ-001" class="answer-panel" hidden' in html
    assert "Correct answer: B" in html


def test_roleplay_scorecard_uses_professional_source_ledger_layout():
    _save_approved_document()
    result = ft.get_handler("render_output")(
        {
            "output_type": "roleplay_scorecard",
            "content_json": json.dumps(_grounded_roleplay_scorecard()),
            "file_name": "interactive_scorecard",
        }
    )
    data = json.loads(result[0]["text"])
    html = Path(data["html_path"]).read_text(encoding="utf-8")

    assert '<body data-output-type="roleplay-scorecard">' in html
    assert 'aria-label="Document sections"' in html
    assert 'href="#transcript"' in html
    assert 'href="#claims"' in html
    assert '<details class="source-disclosure">' in html
    assert "100%" in html
