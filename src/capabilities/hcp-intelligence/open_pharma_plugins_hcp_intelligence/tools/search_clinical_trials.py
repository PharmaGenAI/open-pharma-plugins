"""search_clinical_trials — query ClinicalTrials.gov v2 API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchClinicalTrialsArgs(BaseModel):
    investigator_name: str | None = Field(
        default=None,
        description="Name of the principal investigator or contact",
    )
    organization_name: str | None = Field(
        default=None,
        description="Sponsor or site organization name",
    )
    condition: str | None = Field(
        default=None,
        description="Disease or condition studied (e.g. 'Type 2 Diabetes')",
    )
    intervention: str | None = Field(
        default=None,
        description="Drug, device, or procedure (e.g. 'semaglutide')",
    )
    country: str | None = Field(
        default=None,
        description="Country of the trial site (e.g. 'Singapore')",
    )
    status: str | None = Field(
        default=None,
        description="Recruitment status filter: RECRUITING, COMPLETED, ACTIVE_NOT_RECRUITING, etc.",
    )
    max_results: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum trials to return",
    )


TOOL: dict[str, Any] = {
    "name": "search_clinical_trials",
    "description": (
        "Search ClinicalTrials.gov for clinical trials by investigator, organization, "
        "condition, or intervention. Returns structured records with NCT ID, title, "
        "status, phase, conditions, interventions, sponsor, investigators, and dates. "
        "Use to identify an HCP's trial involvement or an HCO's trial activity."
    ),
    "args": SearchClinicalTrialsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    import urllib.parse
    import urllib.request
    from datetime import datetime, timezone

    base = "https://clinicaltrials.gov/api/v2/studies"

    query_parts = []
    if arguments.get("investigator_name"):
        query_parts.append(f"SEARCH[Study]{arguments['investigator_name']}")
    if arguments.get("organization_name"):
        query_parts.append(f"SEARCH[Study]{arguments['organization_name']}")
    if arguments.get("condition"):
        query_parts.append(f"COND={arguments['condition']}")
    if arguments.get("intervention"):
        query_parts.append(f"INTR={arguments['intervention']}")

    if not query_parts:
        return [{"type": "text", "text": json.dumps({"error": "At least one search parameter is required"})}]

    params: dict[str, str] = {
        "format": "json",
        "pageSize": str(arguments.get("max_results", 20)),
        "query.term": " AND ".join(query_parts),
    }
    if arguments.get("country"):
        params["query.locn"] = f"SEARCH[Location]{arguments['country']}"
    if arguments.get("status"):
        params["filter.overallStatus"] = arguments["status"]

    fields = [
        "NCTId",
        "BriefTitle",
        "OverallStatus",
        "Phase",
        "Condition",
        "InterventionName",
        "LeadSponsorName",
        "CollaboratorName",
        "StartDate",
        "PrimaryCompletionDate",
        "ResponsiblePartyInvestigatorFullName",
        "OverallOfficialName",
        "OverallOfficialRole",
    ]
    params["fields"] = "|".join(fields)

    url = f"{base}?{urllib.parse.urlencode(params, safe='|=')}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    total_count = data.get("totalCount", 0)
    studies = data.get("studies", [])

    trials = []
    for study in studies:
        proto = study.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status_mod = proto.get("statusModule", {})
        design = proto.get("designModule", {})
        cond_mod = proto.get("conditionsModule", {})
        interv_mod = proto.get("armsInterventionsModule", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        contacts_mod = proto.get("contactsLocationsModule", {})

        nct_id = ident.get("nctId", "")
        title = ident.get("briefTitle", "")
        overall_status = status_mod.get("overallStatus", "")

        phases = design.get("phases", [])
        phase = ", ".join(phases) if phases else None

        conditions = cond_mod.get("conditions", [])

        interventions = []
        for arm in interv_mod.get("interventions", []):
            name = arm.get("name", "")
            itype = arm.get("type", "")
            interventions.append(f"{itype}: {name}" if itype else name)

        lead_sponsor = sponsor_mod.get("leadSponsor", {}).get("name")
        collabs = [c.get("name", "") for c in sponsor_mod.get("collaborators", [])]

        start_date = status_mod.get("startDateStruct", {}).get("date")
        completion_date = status_mod.get("primaryCompletionDateStruct", {}).get("date")

        inv_name = None
        inv_role = None
        officials = contacts_mod.get("overallOfficials", [])
        if officials:
            inv_name = officials[0].get("name")
            inv_role = officials[0].get("role")

        trials.append(
            {
                "nct_id": nct_id,
                "title": title,
                "status": overall_status,
                "phase": phase,
                "conditions": conditions,
                "interventions": interventions,
                "sponsor": lead_sponsor,
                "collaborators": collabs,
                "start_date": start_date,
                "completion_date": completion_date,
                "investigator_name": inv_name,
                "investigator_role": inv_role,
                "source_url": f"https://clinicaltrials.gov/study/{nct_id}",
            }
        )

    output = {
        "query": " AND ".join(query_parts),
        "total_count": total_count,
        "trials": trials,
        "source": "clinicaltrials.gov",
        "searched_at": datetime.now(timezone.utc).isoformat(),
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]
