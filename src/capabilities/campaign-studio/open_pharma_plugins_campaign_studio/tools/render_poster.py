"""render_poster - produce an inspected deterministic one-page PDF poster."""

from __future__ import annotations

import io
import json
from typing import Any
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field


class RenderPosterArgs(BaseModel):
    campaign_brief_id: str = Field(description="Links to the campaign")
    paper_size: str | None = Field(default=None, description="Compatibility paper override; must equal the brief")


TOOL: dict[str, Any] = {
    "name": "render_poster",
    "description": "Render an inspected deterministic one-page poster at the exact brief-controlled MediaBox.",
    "args": RenderPosterArgs,
}


def _colour(value: object, fallback: str):
    from reportlab.lib.colors import HexColor

    try:
        return HexColor(value if isinstance(value, str) else fallback)
    except Exception:
        return HexColor(fallback)


def _build_pdf(context: dict[str, Any], width: float, height: float) -> bytes:
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import Paragraph

    palette = context["palette"]
    copy = context["copy"]
    legal = context["legal"]
    product = context.get("product")
    margin = 42.0
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(width, height), pageCompression=1, invariant=1)
    canvas.setTitle(f"{context['brief'].get('brand', '')} campaign poster")
    canvas.setAuthor("Open Pharma Plugins")
    canvas.setCreator("Open Pharma Plugins Campaign Studio")
    navy = _colour(palette.get("primary"), "#003B5C")
    cyan = _colour(palette.get("secondary"), "#00A3E0")
    orange = _colour(palette.get("accent"), "#E8792F")
    ink = _colour(palette.get("text"), "#1A202C")
    white = _colour(palette.get("background"), "#FFFFFF")
    red = _colour(palette.get("safety_highlight"), "#E53E3E")

    canvas.setFillColor(white)
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.setFillColor(navy)
    canvas.rect(0, height - 76, width, 76, fill=1, stroke=0)
    canvas.setFillColor(white)
    canvas.setFont("Helvetica-Bold", 17)
    canvas.drawString(margin, height - 47, str(context["brief"].get("brand", "")))
    canvas.setFont("Helvetica", 7.5)
    canvas.drawRightString(width - margin, height - 45, "DRAFT - FOR QUALIFIED MLR REVIEW")
    canvas.setStrokeColor(cyan)
    canvas.setLineWidth(4)
    canvas.line(margin, height - 96, margin, 300)
    canvas.setStrokeColor(orange)
    canvas.setLineWidth(4)
    canvas.line(margin, height - 96, margin + 16, height - 96)

    headline_style = ParagraphStyle(
        "EvidenceHeadline", fontName="Helvetica-Bold", fontSize=24, leading=28, textColor=navy, spaceAfter=12
    )
    subhead_style = ParagraphStyle(
        "EvidenceSubhead", fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=ink, spaceAfter=8
    )
    body_style = ParagraphStyle("EvidenceBody", fontName="Helvetica", fontSize=10, leading=14, textColor=ink)
    cta_style = ParagraphStyle("EvidenceCTA", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=navy)
    safety_style = ParagraphStyle("Safety", fontName="Helvetica", fontSize=8, leading=10.2, textColor=ink)
    legal_style = ParagraphStyle("Legal", fontName="Helvetica", fontSize=7, leading=8.6, textColor=ink)

    product_box = min(174.0, width * 0.29)
    content_x = margin + 18
    content_top = height - 112
    content_width = width - content_x - margin
    if product is not None:
        content_width -= product_box + 20
        image = ImageReader(io.BytesIO(product.payload))
        source_w, source_h = image.getSize()
        if source_w <= 0 or source_h <= 0:
            raise ValueError("invalid product image dimensions")
        scale = min(product_box / source_w, product_box / source_h)
        image_w, image_h = source_w * scale, source_h * scale
        image_x = width - margin - product_box + (product_box - image_w) / 2
        image_y = content_top - product_box + (product_box - image_h) / 2
        canvas.drawImage(image, image_x, image_y, width=image_w, height=image_h, preserveAspectRatio=True, mask="auto")

    y = content_top
    rendered_values = {
        " ".join(block["text"].split()) for block in [copy["headline"], *(copy["body"] or []), copy["cta"]]
    }
    rendered_values.update(" ".join(value.split()) for value in legal.values())
    if copy.get("subhead"):
        rendered_values.add(" ".join(copy["subhead"]["text"].split()))
    rendered_values.update(" ".join(block["text"].split()) for block in copy.get("bullet_points") or [])

    def draw_paragraph(text: str, style: ParagraphStyle, available_width: float, *, gap: float = 8.0) -> None:
        nonlocal y
        paragraph = Paragraph(escape(text), style)
        _wrapped_width, wrapped_height = paragraph.wrap(available_width, height)
        if y - wrapped_height < 330:
            raise OverflowError("poster evidence content exceeds its safe zone")
        paragraph.drawOn(canvas, content_x, y - wrapped_height)
        y -= wrapped_height + gap

    draw_paragraph(copy["headline"]["text"], headline_style, content_width, gap=11)
    if copy.get("subhead"):
        draw_paragraph(copy["subhead"]["text"], subhead_style, content_width)
    for block in copy["body"]:
        draw_paragraph(block["text"], body_style, content_width, gap=7)
    for block in copy.get("bullet_points") or []:
        draw_paragraph(f"- {block['text']}", body_style, content_width, gap=5)
    draw_paragraph(copy["cta"]["text"], cta_style, content_width, gap=0)

    safety_top = 286.0
    canvas.setStrokeColor(red)
    canvas.setLineWidth(4)
    canvas.line(margin, safety_top, width - margin, safety_top)
    canvas.setFillColor(ink)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(margin, safety_top - 18, "IMPORTANT SAFETY INFORMATION")
    legal_y = safety_top - 27

    def draw_legal(text: str, style: ParagraphStyle, role_gap: float) -> None:
        nonlocal legal_y
        paragraph = Paragraph(escape(text), style)
        _wrapped_width, wrapped_height = paragraph.wrap(width - 2 * margin, height)
        if legal_y - wrapped_height < margin:
            raise OverflowError("poster legal content exceeds its safe zone")
        paragraph.drawOn(canvas, margin, legal_y - wrapped_height)
        legal_y -= wrapped_height + role_gap

    draw_legal(legal["isi"], safety_style, 8)
    for name, value in legal.items():
        if name != "isi":
            draw_legal(value, legal_style, 6)
    for footnote in copy.get("footnotes") or []:
        footnote_text = footnote["text"] if isinstance(footnote, dict) else footnote
        normalised = " ".join(footnote_text.split())
        if normalised not in rendered_values:
            draw_legal(footnote_text, legal_style, 5)
            rendered_values.add(normalised)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    from .._campaign_store import save_output_bytes
    from .._render_validation import (
        RenderContractError,
        error_response,
        inspect_pdf,
        load_render_context,
        prohibited_errors,
        resolve_poster_dimensions,
    )

    campaign_brief_id = arguments.get("campaign_brief_id")
    try:
        context = load_render_context(campaign_brief_id, "poster")
        paper, width, height = resolve_poster_dimensions(context["brief"], arguments.get("paper_size"))
        try:
            payload = _build_pdf(context, width, height)
        except OverflowError as exc:
            raise RenderContractError("poster_text_overflow", "Poster content does not fit one page safely.") from exc
        except RenderContractError:
            raise
        except Exception as exc:
            if context.get("product") is not None:
                raise RenderContractError(
                    "invalid_product_image", "The selected product image could not be decoded."
                ) from exc
            raise RenderContractError("poster_render_failed", "Poster could not be rendered safely.") from exc
        inspection = inspect_pdf(payload, context, (width, height))
        errors = [*inspection["errors"], *prohibited_errors(inspection["text"], context["brief"])]
        if errors:
            raise RenderContractError("rendered_poster_invalid", "; ".join(sorted(set(errors))))
        path = save_output_bytes(campaign_brief_id, "poster.pdf", payload)
        result = {
            "campaign_brief_id": campaign_brief_id,
            "channel": "poster",
            "file_path": str(path),
            "format": "pdf",
            "editable": False,
            "paper_size": paper,
        }
    except Exception as exc:
        result = error_response(exc)
    return [{"type": "text", "text": json.dumps(result, ensure_ascii=False, sort_keys=True)}]
