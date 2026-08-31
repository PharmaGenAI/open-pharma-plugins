"""ci_landscape — competitive landscape matrix for a therapeutic area."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


class LandscapeArgs(BaseModel):
    therapeutic_area: str = Field(description="Therapeutic area or indication, e.g. 'NSCLC', 'Type 2 Diabetes'")
    competitors: list[str] | None = Field(
        default=None,
        description="Specific competitors to include. If omitted, discovers top sponsors from trial data.",
    )
    phases: list[str] | None = Field(
        default=None,
        description="Trial phases to include: phase1, phase2, phase3, approved. Default: all.",
    )
    include_approved: bool = Field(
        default=True,
        description="Include already-approved products from FDA label data.",
    )
    max_competitors: int = Field(
        default=15,
        ge=1,
        le=50,
        description="Maximum number of competitors in the landscape.",
    )


TOOL: dict[str, Any] = {
    "name": "ci_landscape",
    "description": (
        "Build a competitive landscape matrix for a therapeutic area by "
        "querying ClinicalTrials.gov and OpenFDA, cross-referencing trials "
        "with approved products, deduplicating, and ranking by phase. "
        "Returns a structured landscape with competitor, product, mechanism, "
        "phase, key trials, and expected milestones."
    ),
    "args": LandscapeArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    from datetime import datetime, timezone

    from .ci_scan_trials import handle as scan_trials

    therapeutic_area = arguments["therapeutic_area"]
    target_competitors = arguments.get("competitors")
    include_approved = arguments.get("include_approved", True)
    max_competitors = arguments.get("max_competitors", 15)

    trial_result = json.loads(scan_trials({"query": therapeutic_area, "max_results": 50})[0]["text"])
    trials = trial_result.get("trials", [])

    sponsor_products: dict[str, dict[str, dict[str, Any]]] = {}

    for t in trials:
        sponsor = t.get("sponsor", "Unknown")
        if target_competitors and not any(c.lower() in sponsor.lower() for c in target_competitors):
            continue

        for intervention in t.get("interventions", []):
            key = f"{sponsor}|{intervention}"
            if key not in sponsor_products.get(sponsor, {}):
                sponsor_products.setdefault(sponsor, {})[intervention] = {
                    "sponsor": sponsor,
                    "product": intervention,
                    "phase": t.get("phase", ""),
                    "status": t.get("status", ""),
                    "conditions": t.get("conditions", []),
                    "key_trials": [t.get("nct_id", "")],
                    "enrollment": t.get("enrollment"),
                    "completion_date": t.get("completion_date", ""),
                    "start_date": t.get("start_date", ""),
                }
            else:
                entry = sponsor_products[sponsor][intervention]
                if t.get("nct_id") and t["nct_id"] not in entry["key_trials"]:
                    entry["key_trials"].append(t["nct_id"])
                new_phase = _phase_rank(t.get("phase", ""))
                if new_phase > _phase_rank(entry["phase"]):
                    entry["phase"] = t.get("phase", "")
                    entry["status"] = t.get("status", "")

    approved_products: dict[str, dict[str, Any]] = {}
    if include_approved:
        from .ci_scan_regulatory import handle as scan_reg

        reg_result = json.loads(scan_reg({"drug_name": therapeutic_area, "max_results": 30})[0]["text"])
        for event in reg_result.get("events", []):
            if event.get("type") == "approval":
                brand = event.get("brand_name", "")
                sponsor = event.get("generic_name", brand)
                if brand and brand not in approved_products:
                    approved_products[brand] = {
                        "sponsor": sponsor,
                        "product": brand,
                        "phase": "APPROVED",
                        "status": "marketed",
                        "conditions": [],
                        "key_trials": [],
                        "approval_date": event.get("date", ""),
                    }

    entries = []
    for sponsor_data in sponsor_products.values():
        for entry in sponsor_data.values():
            milestones = []
            if entry.get("completion_date"):
                milestones.append({"event": "Primary completion", "date": entry["completion_date"]})
            entries.append(
                {
                    "competitor": entry["sponsor"],
                    "product": entry["product"],
                    "mechanism": None,
                    "phase": entry["phase"],
                    "status": entry["status"],
                    "indications": entry.get("conditions", [])[:3],
                    "key_trials": entry["key_trials"][:3],
                    "expected_milestones": milestones,
                    "differentiators": [],
                    "approval_date": None,
                }
            )

    for brand, info in approved_products.items():
        generic = info.get("sponsor", "").lower()
        existing = any(
            e["product"].lower() == brand.lower()
            or e["product"].lower() == generic
            or brand.lower() in e["product"].lower()
            for e in entries
        )
        if not existing:
            entries.append(
                {
                    "competitor": info["sponsor"],
                    "product": info["product"],
                    "mechanism": None,
                    "phase": "APPROVED",
                    "status": "marketed",
                    "indications": [],
                    "key_trials": [],
                    "expected_milestones": [],
                    "differentiators": [],
                    "approval_date": info.get("approval_date"),
                }
            )

    entries.sort(key=lambda e: _phase_rank(e["phase"]), reverse=True)
    entries = entries[:max_competitors]

    phase_dist: dict[str, int] = {}
    for e in entries:
        p = e["phase"] or "Unknown"
        phase_dist[p] = phase_dist.get(p, 0) + 1

    output = {
        "therapeutic_area": therapeutic_area,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_entries": len(entries),
        "phase_distribution": phase_dist,
        "entries": entries,
        "data_sources": ["ClinicalTrials.gov", "OpenFDA"],
        "metadata": {
            "total_trials_scanned": len(trials),
            "total_approved_found": len(approved_products),
        },
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]


def _phase_rank(phase: str) -> int:
    p = phase.upper().replace(", ", "").replace(" ", "")
    if "APPROVED" in p:
        return 10
    if "PHASE4" in p:
        return 9
    if "PHASE3" in p:
        return 8
    if "PHASE2" in p and "PHASE3" in p:
        return 7
    if "PHASE2" in p:
        return 6
    if "PHASE1" in p and "PHASE2" in p:
        return 5
    if "PHASE1" in p:
        return 4
    if "EARLY" in p:
        return 3
    return 1
