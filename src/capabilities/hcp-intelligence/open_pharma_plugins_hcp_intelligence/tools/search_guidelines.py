"""search_guidelines — guideline authorship (PubMed) + regulatory advisory roles (web)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SearchGuidelinesArgs(BaseModel):
    name: str = Field(description="Full name of the HCP (e.g. 'Yvonne Lim')")
    specialty: str | None = Field(
        default=None,
        description="Medical specialty to narrow guideline search (e.g. 'Paediatric Medicine')",
    )
    scope: Literal["guidelines", "regulatory", "both"] = Field(
        default="both",
        description=(
            "What to search: 'guidelines' for PubMed guideline/consensus publications, "
            "'regulatory' for FDA/EMA/WHO advisory committee rosters, or 'both'."
        ),
    )
    therapeutic_area: str | None = Field(
        default=None,
        description="Therapeutic area or disease to narrow the guideline search (e.g. 'diabetes')",
    )
    max_results: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum results to return per scope (guidelines and regulatory each up to this limit)",
    )


TOOL: dict[str, Any] = {
    "name": "search_guidelines",
    "description": (
        "Search for an HCP's clinical guideline authorship and regulatory advisory "
        "committee roles. Guideline authorship (via PubMed) and regulatory influence "
        "(FDA/EMA/WHO advisory membership, via web search) are top-tier KOL signals. "
        "Use scope to search guidelines only, regulatory only, or both."
    ),
    "args": SearchGuidelinesArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    from datetime import datetime, timezone

    scope = arguments.get("scope", "both")

    guideline_publications: list[dict] = []
    total_guidelines_found = 0
    guideline_query = ""

    regulatory_results: list[dict] = []
    total_regulatory_results = 0
    regulatory_query = ""

    if scope in ("guidelines", "both"):
        guideline_query, total_guidelines_found, guideline_publications = _search_guideline_publications(arguments)

    if scope in ("regulatory", "both"):
        regulatory_query, regulatory_results = _search_regulatory_roles(arguments)
        total_regulatory_results = len(regulatory_results)

    query = guideline_query or regulatory_query
    if guideline_query and regulatory_query:
        query = f"guidelines: {guideline_query} | regulatory: {regulatory_query}"

    output = {
        "query": query,
        "guideline_publications": guideline_publications,
        "regulatory_results": regulatory_results,
        "total_guidelines_found": total_guidelines_found,
        "total_regulatory_results": total_regulatory_results,
        "scope": scope,
        "searched_at": datetime.now(timezone.utc).isoformat(),
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]


def _search_guideline_publications(
    arguments: dict[str, Any],
) -> tuple[str, int, list[dict]]:
    """Query PubMed for guideline/consensus publications authored by the HCP."""
    import json
    import urllib.parse
    import urllib.request

    from shared.env import get_env

    api_key = get_env("NCBI_API_KEY", "")
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    name = arguments["name"]
    pub_type_filter = (
        "(Practice Guideline[pt] OR Guideline[pt] "
        "OR Consensus Development Conference[pt] "
        "OR Consensus Development Conference, NIH[pt])"
    )
    parts = [f"{name}[Author]", pub_type_filter]

    if arguments.get("therapeutic_area"):
        parts.append(arguments["therapeutic_area"])
    if arguments.get("specialty"):
        parts.append(arguments["specialty"])

    query = " AND ".join(parts)
    max_results = arguments.get("max_results", 20)

    search_params = {
        "db": "pubmed",
        "term": query,
        "retmax": str(max_results),
        "retmode": "json",
        "sort": "date",
    }
    if api_key:
        search_params["api_key"] = api_key

    search_url = f"{base}/esearch.fcgi?{urllib.parse.urlencode(search_params)}"
    with urllib.request.urlopen(search_url, timeout=30) as resp:
        search_data = json.loads(resp.read())

    result = search_data.get("esearchresult", {})
    total_count = int(result.get("count", 0))
    id_list = result.get("idlist", [])

    publications: list[dict] = []
    if id_list:
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(id_list),
            "retmode": "xml",
            "rettype": "abstract",
        }
        if api_key:
            fetch_params["api_key"] = api_key

        fetch_url = f"{base}/efetch.fcgi?{urllib.parse.urlencode(fetch_params)}"
        with urllib.request.urlopen(fetch_url, timeout=60) as resp:
            xml_data = resp.read()

        publications = _parse_guideline_xml(xml_data)

    return query, total_count, publications


def _parse_guideline_xml(xml_bytes: bytes) -> list[dict]:
    """Parse PubMed efetch XML into guideline publication records."""
    import re
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_bytes)
    pubs = []

    for article in root.findall(".//PubmedArticle"):
        medline = article.find("MedlineCitation")
        if medline is None:
            continue

        pmid_el = medline.find("PMID")
        pmid = pmid_el.text if pmid_el is not None else None

        art = medline.find("Article")
        if art is None:
            continue

        title_el = art.find("ArticleTitle")
        title = "".join(title_el.itertext()) if title_el is not None else ""

        journal_el = art.find("Journal/Title")
        journal = journal_el.text if journal_el is not None else ""

        year = None
        for date_path in [
            "Journal/JournalIssue/PubDate/Year",
            "ArticleDate/Year",
        ]:
            y_el = art.find(date_path)
            if y_el is not None and y_el.text:
                year = int(y_el.text)
                break
        if year is None:
            medline_date = art.find("Journal/JournalIssue/PubDate/MedlineDate")
            if medline_date is not None and medline_date.text:
                m = re.search(r"(\d{4})", medline_date.text)
                if m:
                    year = int(m.group(1))

        authors = []
        for au in art.findall("AuthorList/Author"):
            last = au.find("LastName")
            fore = au.find("ForeName")
            if last is not None:
                n = last.text or ""
                if fore is not None and fore.text:
                    n += f" {fore.text}"
                authors.append(n)

        pub_types = []
        for pt in art.findall("PublicationTypeList/PublicationType"):
            if pt.text:
                pub_types.append(pt.text)

        pubs.append(
            {
                "pmid": pmid,
                "title": title,
                "authors": authors,
                "journal": journal,
                "year": year,
                "publication_types": pub_types,
                "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
            }
        )

    return pubs


def _search_regulatory_roles(arguments: dict[str, Any]) -> tuple[str, list[dict]]:
    """Web-search for FDA/EMA/WHO advisory committee membership."""
    from .search_hcp_web import _web_search

    name = arguments["name"]
    max_results = arguments.get("max_results", 20)

    query = (
        f'"{name}" '
        "(FDA advisory committee OR EMA CHMP OR EMA SAWP OR EMA PDCO "
        "OR WHO expert advisory OR advisory board roster "
        "OR guideline committee member)"
    )

    results = _web_search(query, max_results)
    return query, results
