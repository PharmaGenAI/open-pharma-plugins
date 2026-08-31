"""PubMed ESearch/EFetch adapter with nested XML preservation."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlencode

from pydantic import ValidationError

from shared.filesystem import sanitize_url

from ._cache import cache_lookup, cache_store
from ._transport import HttpRequest, HttpTransport, TransportError, UrllibTransport
from .models import (
    CacheProvenance,
    CoverageStatus,
    Publication,
    PublicationSearchRequest,
    SourceError,
    SourceName,
    SourceRequestEvidence,
    SourceResult,
    aggregate_cache_status,
)

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DEFAULT_TRANSPORT: HttpTransport = UrllibTransport()


def build_search_url(request: PublicationSearchRequest, *, api_key: str) -> str:
    params = {
        "db": "pubmed",
        "term": _exact_query(request),
        "retmax": str(request.max_results),
        "sort": "date",
        "datetype": "pdat",
        "reldate": str(request.days_back),
        "retmode": "json",
    }
    if api_key:
        params["api_key"] = api_key
    return f"{_EUTILS}/esearch.fcgi?{urlencode(params)}"


def search_publications(
    request: PublicationSearchRequest,
    *,
    transport: HttpTransport | None = None,
    now: datetime | None = None,
) -> SourceResult:
    from shared.env import get_env

    transport = transport or DEFAULT_TRANSPORT
    retrieved_at = _utc(now)
    api_key = get_env("NCBI_API_KEY", "")
    search_wire_url = build_search_url(request, api_key=api_key)
    search_url = sanitize_url(search_wire_url)
    search_params = {
        "query": _exact_query(request),
        "days_back": request.days_back,
        "max_results": request.max_results,
    }
    search_lookup = cache_lookup("pubmed:esearch", search_params)
    try:
        search_payload = search_lookup.payload
        if search_payload is None:
            response = transport.request(HttpRequest(method="GET", url=search_wire_url))
            search_payload = _json_mapping(response.body)
            ids, total = _parse_search(search_payload)
            cache_store("pubmed:esearch", search_params, search_payload)
        else:
            ids, total = _parse_search(search_payload)
    except TransportError as error:
        return _failure(request, search_url, retrieved_at, search_lookup.status, error.code, str(error))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return _failure(
            request,
            search_url,
            retrieved_at,
            search_lookup.status,
            "schema_mismatch",
            "PubMed ESearch response shape was invalid",
        )

    search_evidence = SourceRequestEvidence(
        query=_exact_query(request),
        source_url=search_url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=search_lookup.status, cached_at=search_lookup.cached_at),
        status=CoverageStatus.COMPLETE,
        record_count=len(ids),
    )
    if not ids:
        return SourceResult(
            source=SourceName.PUBMED,
            provider="pubmed",
            status=CoverageStatus.COMPLETE,
            query=_exact_query(request),
            source_url=search_url,
            retrieved_at=retrieved_at,
            cache=CacheProvenance(status=search_lookup.status, cached_at=search_lookup.cached_at),
            records=[],
            total_available=total,
            requests=[search_evidence],
        )

    fetch_wire_url = _build_fetch_url(ids, api_key=api_key)
    fetch_url = sanitize_url(fetch_wire_url)
    fetch_params = {"ids": ids}
    fetch_lookup = cache_lookup("pubmed:efetch", fetch_params)
    try:
        xml_text = fetch_lookup.payload
        if xml_text is None:
            response = transport.request(HttpRequest(method="GET", url=fetch_wire_url, timeout_seconds=60))
            xml_text = response.body.decode("utf-8")
            records, dropped = _parse_xml(xml_text.encode("utf-8"))
            cache_store("pubmed:efetch", fetch_params, xml_text)
        else:
            if not isinstance(xml_text, str):
                raise ValueError("cached PubMed XML must be text")
            records, dropped = _parse_xml(xml_text.encode("utf-8"))
    except TransportError:
        return _fetch_failure(request, fetch_url, retrieved_at, fetch_lookup.status, "fetch_failed")
    except (ValueError, ValidationError, ET.ParseError, UnicodeDecodeError):
        return _fetch_failure(request, fetch_url, retrieved_at, fetch_lookup.status, "schema_mismatch")

    if dropped and not records:
        return _fetch_failure(request, fetch_url, retrieved_at, fetch_lookup.status, "schema_mismatch")
    status = CoverageStatus.PARTIAL if dropped else CoverageStatus.COMPLETE
    error = SourceError(code="schema_mismatch", message="some PubMed records were invalid") if dropped else None
    fetch_evidence = SourceRequestEvidence(
        query=",".join(ids),
        source_url=fetch_url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=fetch_lookup.status, cached_at=fetch_lookup.cached_at),
        status=status,
        record_count=len(records),
        error=error,
    )
    limitations = [f"Dropped {dropped} malformed PubMed record(s)."] if dropped else []
    return SourceResult(
        source=SourceName.PUBMED,
        provider="pubmed",
        status=status,
        query=_exact_query(request),
        source_url=search_url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=aggregate_cache_status([search_lookup.status, fetch_lookup.status])),
        records=records,
        total_available=total,
        requests=[search_evidence, fetch_evidence],
        limitations=limitations,
        error=error,
    )


def _parse_search(payload: Mapping[str, Any]) -> tuple[list[str], int]:
    result = payload.get("esearchresult")
    if not isinstance(result, Mapping):
        raise ValueError("esearchresult must be a mapping")
    ids = result.get("idlist")
    if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
        raise ValueError("idlist must be a string list")
    total = int(result.get("count", 0))
    if total < len(ids):
        raise ValueError("PubMed count cannot be smaller than returned IDs")
    return ids, total


def _parse_xml(xml_data: bytes) -> tuple[list[dict[str, Any]], int]:
    root = ET.fromstring(xml_data)
    articles = root.findall(".//PubmedArticle")
    if not articles:
        raise ValueError("PubMed EFetch contained no articles")
    records = []
    dropped = 0
    for element in articles:
        try:
            records.append(_parse_article(element).model_dump(mode="json"))
        except (ValueError, ValidationError, TypeError):
            dropped += 1
    if not records:
        raise ValueError("no valid PubMed articles")
    return records, dropped


def _parse_article(article: ET.Element) -> Publication:
    medline = article.find("MedlineCitation")
    if medline is None:
        raise ValueError("MedlineCitation is required")
    pmid = _text(medline.find("PMID"))
    body = medline.find("Article")
    if body is None:
        raise ValueError("Article is required")
    title = _element_text(body.find("ArticleTitle"))
    abstract_parts = []
    for block in body.findall(".//Abstract/AbstractText"):
        text = _element_text(block)
        label = block.attrib.get("Label", "").strip()
        abstract_parts.append(f"{label}: {text}" if label else text)
    authors = []
    for author in body.findall(".//AuthorList/Author")[:5]:
        last = author.findtext("LastName", "").strip()
        fore = author.findtext("ForeName", "").strip()
        if last:
            authors.append(f"{last} {fore}".strip())
    pub_types = [_element_text(value) for value in body.findall(".//PublicationTypeList/PublicationType")]
    return Publication(
        pmid=pmid,
        title=title,
        authors=authors,
        journal=_element_text(body.find(".//Journal/Title"), required=False),
        pub_date=_publication_date(body),
        abstract_excerpt=" ".join(abstract_parts)[:1000],
        pub_types=[value for value in pub_types if value],
        source_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    )


def _publication_date(article: ET.Element) -> str:
    article_date = article.find(".//ArticleDate")
    if article_date is not None:
        year = article_date.findtext("Year", "")
        month = article_date.findtext("Month", "")
        day = article_date.findtext("Day", "")
        if year:
            return _join_date(year, month, day)
    pub_date = article.find(".//Journal/JournalIssue/PubDate")
    if pub_date is None:
        return ""
    year = pub_date.findtext("Year", "")
    if year:
        return _join_date(year, pub_date.findtext("Month", ""), pub_date.findtext("Day", ""))
    return pub_date.findtext("MedlineDate", "").strip()


def _join_date(year: str, month: str, day: str) -> str:
    month_value = _month(month)
    if day:
        return f"{year}-{month_value}-{day.zfill(2)}"
    return f"{year}-{month_value}" if month_value else year


def _month(value: str) -> str:
    value = value.strip()
    if value.isdigit():
        return value.zfill(2)
    months = {
        "jan": "01",
        "feb": "02",
        "mar": "03",
        "apr": "04",
        "may": "05",
        "jun": "06",
        "jul": "07",
        "aug": "08",
        "sep": "09",
        "oct": "10",
        "nov": "11",
        "dec": "12",
    }
    return months.get(value[:3].lower(), "")


def _element_text(element: ET.Element | None, *, required: bool = True) -> str:
    if element is None:
        if required:
            raise ValueError("required XML element is missing")
        return ""
    value = re.sub(r"\s+", " ", "".join(element.itertext())).strip()
    if required and not value:
        raise ValueError("required XML text is missing")
    return value


def _text(element: ET.Element | None) -> str:
    return _element_text(element)


def _build_fetch_url(ids: list[str], *, api_key: str) -> str:
    params = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
    if api_key:
        params["api_key"] = api_key
    return f"{_EUTILS}/efetch.fcgi?{urlencode(params)}"


def _exact_query(request: PublicationSearchRequest) -> str:
    return f"{request.query} AND clinical trial[pt]"


def _failure(request, url, retrieved_at, cache_status, code, message) -> SourceResult:
    error = SourceError(code=code, message=message)
    evidence = SourceRequestEvidence(
        query=_exact_query(request),
        source_url=url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=cache_status),
        status=CoverageStatus.FAILED,
        record_count=0,
        error=error,
    )
    return SourceResult(
        source=SourceName.PUBMED,
        provider="pubmed",
        status=CoverageStatus.FAILED,
        query=_exact_query(request),
        source_url=url,
        retrieved_at=retrieved_at,
        cache=CacheProvenance(status=cache_status),
        requests=[evidence],
        limitations=["No trustworthy PubMed response was obtained."],
        error=error,
    )


def _fetch_failure(request, url, retrieved_at, cache_status, code) -> SourceResult:
    return _failure(
        request,
        url,
        retrieved_at,
        cache_status,
        code,
        "PubMed EFetch did not produce trustworthy publication records",
    )


def _json_mapping(body: bytes) -> Mapping[str, Any]:
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("provider response must be a mapping")
    return payload


def _utc(value: datetime | None) -> datetime:
    now = value or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)
