"""list_documents — list all ingested documents in the content store."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ListDocumentsArgs(BaseModel):
    file_type: str | None = Field(
        default=None,
        description="Filter by file type: 'pdf' or 'pptx'. Omit to list all.",
    )


TOOL: dict[str, Any] = {
    "name": "list_documents",
    "description": (
        "List all documents ingested into the training content store. Shows "
        "document ID, file name, type, page count, title, and ingest date. "
        "Optionally filter by file type. Use this to see what source material "
        "is available before searching or generating training content."
    ),
    "args": ListDocumentsArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    from .._content_store import list_documents

    file_type = arguments.get("file_type")
    docs = list_documents(file_type)

    output = {
        "total": len(docs),
        "documents": [
            {
                "document_id": d["document_id"],
                "file_name": d["file_name"],
                "file_type": d["file_type"],
                "title": d.get("title", ""),
                "total_pages": d["total_pages"],
                "ingested_at": d["ingested_at"],
            }
            for d in docs
        ],
    }
    return [{"type": "text", "text": json.dumps(output, indent=2)}]
