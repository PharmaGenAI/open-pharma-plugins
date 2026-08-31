"""Validate training-output citations against the ingested content store."""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel

from ._content_store import list_documents, load_document
from .models import SourceReference


def validate_output_sources(output: BaseModel) -> list[str]:
    errors: list[str] = []
    declared_names = set(getattr(output, "source_documents", []))
    known_documents = {item.get("file_name", ""): item for item in list_documents()}

    for name in sorted(declared_names):
        if name not in known_documents:
            errors.append(f"source_documents contains unknown ingested document {name!r}")

    for reference in _source_references(output):
        if reference.document_name not in declared_names:
            errors.append(
                f"source {reference.document_id!r} names {reference.document_name!r} "
                "but that file is absent from source_documents"
            )
        try:
            document = load_document(reference.document_id)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if document is None:
            errors.append(f"source document_id {reference.document_id!r} is not ingested")
            continue
        if document.get("file_name") != reference.document_name:
            errors.append(
                f"source {reference.document_id!r} document_name does not match ingested file "
                f"{document.get('file_name')!r}"
            )
        page = next(
            (item for item in document.get("pages", []) if item.get("page_number") == reference.page_number),
            None,
        )
        if page is None:
            errors.append(f"source {reference.document_id!r} has no page or slide {reference.page_number}")
            continue
        excerpt = _normalize(reference.excerpt)
        source_text = _normalize(f"{page.get('text', '')} {page.get('speaker_notes') or ''}")
        if not excerpt or excerpt not in source_text:
            errors.append(
                f"source {reference.document_id!r} page {reference.page_number} excerpt is not present in ingested content"
            )
    return errors


def _source_references(value: object) -> Iterator[SourceReference]:
    if isinstance(value, SourceReference):
        yield value
        return
    if isinstance(value, BaseModel):
        for field in type(value).model_fields:
            yield from _source_references(getattr(value, field))
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _source_references(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _source_references(item)


def _normalize(text: str) -> str:
    return " ".join(str(text).split()).casefold()
