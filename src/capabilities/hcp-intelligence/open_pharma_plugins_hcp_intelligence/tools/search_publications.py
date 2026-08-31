"""search_publications — query PubMed via NCBI E-utilities."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SearchPublicationsArgs(BaseModel):
    author_name: str = Field(description="Full name of the author (e.g. 'Yvonne Lim')")
    affiliation: str | None = Field(
        default=None,
        description="Institution or affiliation to narrow results (e.g. 'KK Women's and Children's Hospital')",
    )
    keywords: str | None = Field(
        default=None,
        description="Additional search terms: specialty, disease area, or MeSH terms",
    )
    year_from: int | None = Field(default=None, description="Earliest publication year (e.g. 2019)")
    year_to: int | None = Field(default=None, description="Latest publication year (e.g. 2026)")
    max_results: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum publications to return",
    )


TOOL: dict[str, Any] = {
    "name": "search_publications",
    "description": (
        "Search PubMed for publications by a specific author. Returns structured "
        "records with PMID, title, authors, journal, year, abstract, MeSH terms, and "
        "DOI. Use to identify an HCP's research output, publication themes, and "
        "co-author network. Supports filtering by affiliation, keywords, and year range."
    ),
    "args": SearchPublicationsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    import urllib.parse
    import urllib.request
    from datetime import datetime, timezone

    from shared.env import get_env

    api_key = get_env("NCBI_API_KEY", "")
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    author = arguments["author_name"]
    parts = [f"{author}[Author]"]
    if arguments.get("affiliation"):
        parts.append(f"{arguments['affiliation']}[Affiliation]")
    if arguments.get("keywords"):
        parts.append(arguments["keywords"])
    query = " AND ".join(parts)

    if arguments.get("year_from") or arguments.get("year_to"):
        mindate = str(arguments.get("year_from", 1900))
        maxdate = str(arguments.get("year_to", 2099))
        query += f" AND {mindate}:{maxdate}[dp]"

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

    publications = []
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

        publications = _parse_pubmed_xml(xml_data)

    output = {
        "query": query,
        "total_count": total_count,
        "publications": publications,
        "source": "pubmed",
        "searched_at": datetime.now(timezone.utc).isoformat(),
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]


def _parse_pubmed_xml(xml_bytes: bytes) -> list[dict]:
    """Parse PubMed efetch XML into structured publication records."""
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
                import re

                m = re.search(r"(\d{4})", medline_date.text)
                if m:
                    year = int(m.group(1))

        authors = []
        for au in art.findall("AuthorList/Author"):
            last = au.find("LastName")
            fore = au.find("ForeName")
            if last is not None:
                name = last.text or ""
                if fore is not None and fore.text:
                    name += f" {fore.text}"
                authors.append(name)

        abstract_parts = []
        for abs_text in art.findall("Abstract/AbstractText"):
            label = abs_text.get("Label", "")
            text = "".join(abs_text.itertext())
            if label:
                abstract_parts.append(f"{label}: {text}")
            else:
                abstract_parts.append(text)
        abstract = "\n".join(abstract_parts) if abstract_parts else None

        doi = None
        for eid in art.findall("ELocationID"):
            if eid.get("EIdType") == "doi":
                doi = eid.text
                break

        mesh_terms = []
        for mh in medline.findall("MeshHeadingList/MeshHeading/DescriptorName"):
            if mh.text:
                mesh_terms.append(mh.text)

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
                "doi": doi,
                "abstract": abstract[:2000] if abstract and len(abstract) > 2000 else abstract,
                "mesh_terms": mesh_terms,
                "publication_types": pub_types,
                "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
            }
        )

    return pubs
