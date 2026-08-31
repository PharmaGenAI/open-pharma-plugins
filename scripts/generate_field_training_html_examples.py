#!/usr/bin/env python3
"""Generate or verify the canonical Field Training HTML reference examples."""

from __future__ import annotations

import argparse
from pathlib import Path

from open_pharma_plugins_field_training._html_renderers import render_learning_package, render_roleplay_kit
from open_pharma_plugins_field_training.models import LearningPackage, RoleplayKit

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "src" / "capabilities" / "field-training" / "skill" / "references" / "examples"
SOURCE_DOCUMENT = "sample_product_message.pdf"
DOCUMENT_ID = "sample_product_message_reference"
GENERATED_AT = "2026-08-28T00:00:00Z"


def _source(page_number: int, excerpt: str) -> dict:
    return {
        "document_id": DOCUMENT_ID,
        "document_name": SOURCE_DOCUMENT,
        "page_number": page_number,
        "excerpt": excerpt,
    }


def _learning_package() -> dict:
    efficacy = (
        "In the MERIDIAN-301 trial, ONCORIX demonstrated a statistically significant improvement "
        "in overall survival (OS) vs standard of care (median OS 24.1 months vs 13.6 months; "
        "HR 0.68; 95% CI: 0.53–0.87; p=0.002)."
    )
    safety = (
        "The most common adverse reactions (≥20%) were fatigue (38%), rash (27%), diarrhoea (24%), and nausea (21%)."
    )
    return {
        "title": "ONCORIX field learning dossier",
        "modules": [
            {
                "title": "Evidence with fair balance",
                "product": "ONCORIX (rivolumab)",
                "therapeutic_area": "Unresectable or metastatic melanoma",
                "objectives": [
                    {
                        "objective": "State the approved overall-survival result accurately",
                        "bloom_level": "remember",
                    },
                    {
                        "objective": "Present efficacy with the corresponding safety context",
                        "bloom_level": "apply",
                    },
                ],
                "key_messages": [
                    {
                        "message": "Median overall survival was 24.1 months with ONCORIX versus 13.6 months with standard of care.",
                        "category": "efficacy",
                        "sources": [_source(2, efficacy)],
                    },
                    {
                        "message": "The common adverse reactions listed in the approved material include fatigue, rash, diarrhoea, and nausea.",
                        "category": "safety",
                        "sources": [_source(3, safety)],
                    },
                ],
                "talking_points": [
                    {
                        "situation": "When an oncologist asks for the headline evidence",
                        "approved_response": efficacy,
                        "supporting_data": "Median OS 24.1 months vs 13.6 months; HR 0.68.",
                        "sources": [_source(2, efficacy)],
                    }
                ],
                "common_objections": [
                    {
                        "objection": "What safety context should accompany the efficacy result?",
                        "approved_response": safety,
                        "sources": [_source(3, safety)],
                    }
                ],
            }
        ],
        "source_documents": [SOURCE_DOCUMENT],
        "generated_at": GENERATED_AT,
    }


def _roleplay_kit() -> dict:
    efficacy = "Median progression-free survival (PFS) was 11.2 months (95% CI: 8.9–14.1) with ONCORIX vs 5.4 months with standard of care."
    safety = "Grade 3-4 treatment-related adverse events occurred in 18% of patients in the ONCORIX arm vs 12% in the control arm."
    return {
        "title": "ONCORIX evidence conversation practice",
        "topic": "Balanced efficacy and safety discussion",
        "hcp_persona": "Time-pressed community oncologist who asks for comparative numbers",
        "scenario": (
            "A scheduled five-minute discussion. The HCP asks for the progression-free survival "
            "result and then challenges the representative to put the safety findings in context."
        ),
        "objectives": [
            {
                "objective": "Deliver the sourced PFS result and its safety context without extrapolation",
                "bloom_level": "apply",
            }
        ],
        "key_messages": [
            {
                "message": efficacy,
                "category": "efficacy",
                "sources": [_source(2, efficacy)],
            },
            {
                "message": safety,
                "category": "safety",
                "sources": [_source(3, safety)],
            },
        ],
        "common_objections": [
            {
                "objection": "How should I interpret the treatment-related adverse events?",
                "approved_response": safety,
                "sources": [_source(3, safety)],
            }
        ],
        "facilitator_prompts": [
            {
                "stage": "opening",
                "prompt": "Ask for the PFS result in one concise response.",
                "coaching_intent": "Listen for the endpoint, both medians, and no unsupported interpretation.",
            },
            {
                "stage": "objection",
                "prompt": "Ask what safety context belongs beside that result.",
                "coaching_intent": "Confirm that the representative uses the sourced adverse-event language.",
            },
        ],
        "evaluation_rubric": [
            {
                "criterion": "Approved efficacy language",
                "weight_pct": 40,
                "evidence_to_observe": ["States both PFS medians", "Does not add an unsupported superiority claim"],
            },
            {
                "criterion": "Fair balance",
                "weight_pct": 40,
                "evidence_to_observe": ["Provides the sourced Grade 3-4 adverse-event rates"],
            },
            {
                "criterion": "Conversation discipline",
                "weight_pct": 20,
                "evidence_to_observe": ["Responds concisely and stays within approved material"],
            },
        ],
        "source_documents": [SOURCE_DOCUMENT],
        "generated_at": GENERATED_AT,
    }


def rendered_examples() -> dict[Path, str]:
    learning = LearningPackage.model_validate(_learning_package()).model_dump(mode="json", exclude_none=True)
    roleplay = RoleplayKit.model_validate(_roleplay_kit()).model_dump(mode="json", exclude_none=True)
    return {
        OUTPUT_DIR / "learning-package.html": render_learning_package(learning),
        OUTPUT_DIR / "roleplay-kit.html": render_roleplay_kit(roleplay),
    }


def check_examples() -> int:
    stale: list[Path] = []
    for path, expected in rendered_examples().items():
        try:
            actual = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            stale.append(path)
            continue
        if actual != expected:
            stale.append(path)
    if stale:
        print("Field Training HTML examples are missing or stale:")
        for path in stale:
            print(f"  {path.relative_to(ROOT)}")
        print("Run: uv run python scripts/generate_field_training_html_examples.py --write")
        return 1
    print(f"Field Training HTML examples are current ({len(rendered_examples())} files).")
    return 0


def write_examples() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, content in rendered_examples().items():
        path.write_text(content, encoding="utf-8")
        print(f"Wrote {path.relative_to(ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="fail when checked-in examples differ from the renderer")
    action.add_argument("--write", action="store_true", help="regenerate the checked-in examples")
    args = parser.parse_args()
    return check_examples() if args.check else write_examples()


if __name__ == "__main__":
    raise SystemExit(main())
