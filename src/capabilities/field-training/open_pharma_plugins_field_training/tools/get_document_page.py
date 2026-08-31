"""get_document_page — retrieve the full text of a specific page or slide."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GetDocumentPageArgs(BaseModel):
    document_id: str = Field(description="Document ID (from list_documents or search_content results)")
    page_number: int = Field(ge=1, description="1-indexed page or slide number")


TOOL: dict[str, Any] = {
    "name": "get_document_page",
    "description": (
        "Get the full text of a specific page (PDF) or slide (PPTX) from an "
        "ingested document. Returns the complete text content, slide title, and "
        "speaker notes (for PPTX). Use this for precise citation when building "
        "learning content or verifying a claim found via search_content."
    ),
    "args": GetDocumentPageArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from .._content_store import load_document

    document_id = arguments["document_id"]
    page_number = arguments["page_number"]

    doc = load_document(document_id)
    if doc is None:
        return [{"type": "text", "text": json.dumps({"error": f"Document '{document_id}' not found"})}]

    page = None
    for p in doc.get("pages", []):
        if p["page_number"] == page_number:
            page = p
            break

    if page is None:
        return [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "error": f"Page {page_number} not found in document '{document_id}' (total pages: {doc.get('total_pages', 0)})"
                    }
                ),
            }
        ]

    output = {
        "document_id": document_id,
        "document_name": doc["file_name"],
        "document_title": doc.get("title", ""),
        "page_number": page["page_number"],
        "page_type": page["page_type"],
        "text": page["text"],
        "slide_title": page.get("slide_title"),
        "speaker_notes": page.get("speaker_notes"),
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]
