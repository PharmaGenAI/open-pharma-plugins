"""Content store for ingested training documents.

Stores extracted document content as JSON files in a configurable directory.
A manifest (_index.json) tracks all ingested documents.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from shared.filesystem import atomic_write_json, contained_path, ensure_private_dir


def _store_dir() -> Path:
    from shared.env import get_env

    d = Path(
        get_env(
            "OPEN_PHARMA_TRAINING_CONTENT_DIR",
            str(Path.home() / ".open-pharma-plugins" / "training-content"),
        )
    )
    return ensure_private_dir(d)


def document_id_from_path(file_path: str) -> str:
    """Generate a stable document ID from the file path."""
    name = Path(file_path).stem
    short_hash = hashlib.sha256(file_path.encode()).hexdigest()[:8]
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
    return f"{safe_name}_{short_hash}"


def save_document(doc: dict) -> None:
    """Save an ingested document to the content store."""
    store = _store_dir()
    doc_path = contained_path(store, f"{doc['document_id']}.json")
    atomic_write_json(doc_path, doc)
    _update_index(doc)


def load_document(document_id: str) -> dict | None:
    """Load a document by ID."""
    doc_path = contained_path(_store_dir(), f"{document_id}.json")
    if doc_path.exists():
        return json.loads(doc_path.read_text())
    return None


def list_documents(file_type: str | None = None) -> list[dict]:
    """List all ingested documents from the index."""
    index = _load_index()
    docs = index.get("documents", [])
    if file_type:
        docs = [d for d in docs if d.get("file_type") == file_type.lower()]
    return docs


def search_pages(query: str, document_id: str | None = None, max_results: int = 10) -> list[dict]:
    """Simple keyword search across all ingested documents.

    Returns ranked results with document info and page content.
    Uses term frequency scoring.
    """
    query_terms = set(query.lower().split())
    if not query_terms:
        return []

    results = []

    doc_ids = [document_id] if document_id else [d["document_id"] for d in list_documents()]

    for doc_id in doc_ids:
        doc = load_document(doc_id)
        if doc is None:
            continue
        for page in doc.get("pages", []):
            text = page.get("text", "")
            text_lower = text.lower()

            notes = page.get("speaker_notes") or ""
            combined = text_lower + " " + notes.lower()

            score = sum(combined.count(term) for term in query_terms)
            if score > 0:
                if query.lower() in combined:
                    score += 10
                results.append(
                    {
                        "document_id": doc_id,
                        "document_name": doc.get("file_name", ""),
                        "page_number": page["page_number"],
                        "page_type": page.get("page_type", "page"),
                        "slide_title": page.get("slide_title"),
                        "text": text[:500],
                        "speaker_notes": notes[:300] if notes else None,
                        "score": score,
                    }
                )

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]


def _load_index() -> dict:
    index_path = contained_path(_store_dir(), "_index.json")
    if index_path.exists():
        return json.loads(index_path.read_text())
    return {"documents": []}


def _update_index(doc: dict) -> None:
    index = _load_index()
    docs = index.get("documents", [])
    docs = [d for d in docs if d.get("document_id") != doc["document_id"]]
    docs.append(
        {
            "document_id": doc["document_id"],
            "file_name": doc.get("file_name", ""),
            "file_type": doc.get("file_type", ""),
            "title": doc.get("title"),
            "total_pages": doc.get("total_pages", 0),
            "ingested_at": doc.get("ingested_at", datetime.now(timezone.utc).isoformat()),
        }
    )
    index["documents"] = docs
    index_path = contained_path(_store_dir(), "_index.json")
    atomic_write_json(index_path, index)
