#!/usr/bin/env python3
"""Generate or verify fictional Campaign Studio examples with production renderers."""

from __future__ import annotations

import argparse
import hashlib
import json
from importlib.resources import files
from pathlib import Path

from open_pharma_plugins_campaign_studio import __version__
from open_pharma_plugins_campaign_studio._mlr_package import _render_html
from open_pharma_plugins_campaign_studio._render_validation import DEMO_DISCLOSURE, AssetSnapshot
from open_pharma_plugins_campaign_studio.tools.render_email import _build_email_candidate

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "src" / "capabilities" / "campaign-studio" / "skill" / "references" / "examples"
FIXTURE_ROOT = Path(str(files("open_pharma_plugins_campaign_studio") / "fixtures"))
VALIDATED_AT = "2026-08-30T00:00:00+00:00"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _logo() -> AssetSnapshot:
    path = FIXTURE_ROOT / "brand_kit" / "logo.svg"
    payload = path.read_bytes()
    return AssetSnapshot(
        name="logo.svg",
        path=path,
        payload=payload,
        sha256=_sha256(payload),
        size=len(payload),
        mime_type="image/svg+xml",
    )


def _email_context() -> tuple[dict, list[dict]]:
    claims = json.loads((FIXTURE_ROOT / "sample_approved_claims.json").read_text(encoding="utf-8"))
    claims_by_id = {claim["claim_id"]: claim for claim in claims}
    palette = json.loads((FIXTURE_ROOT / "brand_kit" / "palette.json").read_text(encoding="utf-8"))
    typography = json.loads((FIXTURE_ROOT / "brand_kit" / "typography.json").read_text(encoding="utf-8"))
    source_legal = json.loads((FIXTURE_ROOT / "brand_kit" / "legal.json").read_text(encoding="utf-8"))
    copy = {
        "subject": {"text": claims_by_id["c-001"]["text"], "claim_ids": ["c-001"]},
        "preheader": {"text": claims_by_id["c-006"]["text"], "claim_ids": ["c-006"]},
        "headline": {"text": claims_by_id["c-002"]["text"], "claim_ids": ["c-002"]},
        "body": [
            {"text": claims_by_id["c-003"]["text"], "claim_ids": ["c-003"]},
            {"text": claims_by_id["c-007"]["text"], "claim_ids": ["c-007"]},
        ],
        "cta": {"text": "Review the evidence", "claim_ids": []},
    }
    legal = {
        "isi": source_legal["isi"],
        "pi_ref": source_legal["pi_ref"],
        "reporting_statement": source_legal["reporting_statement"],
        "demo_disclosure": DEMO_DISCLOSURE,
    }
    return (
        {
            "brief": {
                "brand": "ONCORIX",
                "call_to_action_url": "https://oncorix-hcp.example.com/evidence",
            },
            "copy": copy,
            "legal": legal,
            "palette": palette,
            "typography": typography,
            "logo": _logo(),
        },
        claims,
    )


def _email() -> tuple[str, list[dict]]:
    context, claims = _email_context()
    payload, _provenance = _build_email_candidate(context)
    return payload.decode("utf-8"), claims


def _claim_rows(claims: list[dict]) -> list[dict]:
    rows = []
    for claim_id in ("c-001", "c-002", "c-003", "c-006", "c-007"):
        claim = next(item for item in claims if item["claim_id"] == claim_id)
        rows.append(
            {
                "channel": "email",
                "statement": claim["text"],
                "claim_id": claim_id,
                "approved_wording": claim["text"],
                "matched_wording": claim["text"],
                "status": "approved",
                "deviation": "None",
                "source_document": claim["source_document"],
                "source_reference": claim["source_reference"],
            }
        )
    return rows


def _review(email: str, claims: list[dict]) -> str:
    email_bytes = email.encode("utf-8")
    claims_bytes = (FIXTURE_ROOT / "sample_approved_claims.json").read_bytes()
    policy_bytes = (Path(str(files("open_pharma_plugins_campaign_studio") / "policy")) / "rules.json").read_bytes()
    brand_files = []
    for path in sorted((FIXTURE_ROOT / "brand_kit").iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        brand_files.append({"name": path.name, "sha256": _sha256(payload), "size": len(payload)})
    email_metadata = {"path": "outputs/email.html", "sha256": _sha256(email_bytes), "size": len(email_bytes)}
    review = {
        "campaign": {
            "campaign_brief_id": "fictional_oncorix_demo_reference",
            "campaign_name": "Fictional ONCORIX evidence review",
            "brand": "ONCORIX",
            "indication": "Fictional oncology demonstration",
            "objective": "Review the fictional evidence chain",
            "audience": "Qualified MLR reviewers",
            "channels": ["email"],
            "jurisdiction": "FDA",
            "workflow": "mlr_standard",
            "demo_mode": True,
        },
        "capability_version": __version__,
        "policy": {"version": "campaign-studio-illustrative-1.1", "sha256": _sha256(policy_bytes)},
        "validation_time": VALIDATED_AT,
        "draft_boundary": DEMO_DISCLOSURE,
        "channels": [
            {
                "channel": "email",
                "copy": _email_context()[0]["copy"],
                "pre_render_checks": [
                    {"check_name": "copy_exists", "result": "pass", "detail": "Fictional example copy exists."},
                    {"check_name": "fair_balance", "result": "pass", "detail": "Illustrative check only."},
                    {"check_name": "required_legal", "result": "pass", "detail": "Required demo legal input exists."},
                    {"check_name": "prohibited_language", "result": "pass", "detail": "No configured pattern found."},
                ],
                "post_render_checks": [
                    {"check_name": "output_exists", "result": "pass", "detail": ""},
                    {"check_name": "rendered_contract", "result": "pass", "detail": ""},
                    {"check_name": "prohibited_language", "result": "pass", "detail": ""},
                ],
                "rendered_asset": email_metadata,
                "preview_kind": "escaped exact source",
                "preview": email,
            }
        ],
        "claim_rows": _claim_rows(claims),
        "provenance": {
            "claims": {
                "submitted_path": None,
                "resolved_path": "/fictional-demo/sample_approved_claims.json",
                "is_demo_fixture": True,
                "sha256": _sha256(claims_bytes),
            },
            "brand_kit": {
                "submitted_path": None,
                "resolved_path": "/fictional-demo/brand_kit",
                "is_demo_fixture": True,
            },
        },
        "brand_files": brand_files,
        "artifacts": [email_metadata],
        "reviewer_notes": "Fictional demonstration. Draft for qualified MLR review; not for distribution.",
        "completeness": {"required": 1, "present": 1, "missing": 0, "claim_rows": 5, "channels": 1},
    }
    return _render_html(review)


def rendered_examples() -> dict[Path, str]:
    email, claims = _email()
    return {
        OUTPUT_DIR / "email.html": email,
        OUTPUT_DIR / "mlr-review.html": _review(email, claims),
    }


def check_examples() -> int:
    stale = []
    rendered = rendered_examples()
    for path, expected in rendered.items():
        try:
            actual = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            stale.append(path)
            continue
        if actual != expected:
            stale.append(path)
    if stale:
        print("Campaign Studio HTML examples are missing or stale:")
        for path in stale:
            print(f"  {path.relative_to(ROOT)}")
        print("Run: uv run python scripts/generate_campaign_studio_html_examples.py --write")
        return 1
    print(f"Campaign Studio HTML examples are current ({len(rendered)} files).")
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
    action.add_argument("--check", action="store_true", help="fail when examples differ from the renderers")
    action.add_argument("--write", action="store_true", help="regenerate the checked-in examples")
    args = parser.parse_args()
    return check_examples() if args.check else write_examples()


if __name__ == "__main__":
    raise SystemExit(main())
