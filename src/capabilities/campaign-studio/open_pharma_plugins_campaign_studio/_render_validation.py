"""Fail-closed rendering, extraction, and actual-file validation for Campaign Studio."""

from __future__ import annotations

import base64
import hashlib
import io
import re
import stat
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from importlib.resources import files
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from pydantic import ValidationError

_TEXT_LIMIT = 1_000_000
_PDF_LIMIT = 10_000_000
_TEMPLATE_LIMIT = 128_000
_OUTPUT_LIMIT = 2_000_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DIMENSION = re.compile(r"([1-9][0-9]{1,3})x([1-9][0-9]{1,3})\Z")
_PAPER_SIZES = {
    "A4": (595.2755905511812, 841.8897637795277),
    "LETTER": (612.0, 792.0),
    "A3": (841.8897637795277, 1190.5511811023623),
}
_BANNER_PROFILES = {
    "728x90": "horizontal",
    "300x250": "rectangle",
    "300x300": "rectangle",
    "160x600": "skyscraper",
}
_OUTPUTS = {"email": ("email.html", "html"), "banner": ("banner.svg", "svg"), "poster": ("poster.pdf", "pdf")}
_ACTIVE_HTML = {
    "animate",
    "animatemotion",
    "animatetransform",
    "audio",
    "blink",
    "button",
    "canvas",
    "datalist",
    "details",
    "discard",
    "dialog",
    "embed",
    "fieldset",
    "foreignobject",
    "form",
    "frame",
    "iframe",
    "input",
    "marquee",
    "math",
    "menu",
    "noscript",
    "object",
    "optgroup",
    "option",
    "output",
    "portal",
    "select",
    "set",
    "slot",
    "script",
    "summary",
    "svg",
    "template",
    "textarea",
    "video",
}
_ACTIVE_SVG = {
    "a",
    "script",
    "foreignobject",
    "animate",
    "animatemotion",
    "animatecolor",
    "animatetransform",
    "set",
    "use",
}
_URL_ATTRS = {
    "archive",
    "background",
    "cite",
    "codebase",
    "data",
    "formaction",
    "href",
    "longdesc",
    "manifest",
    "ping",
    "poster",
    "profile",
    "src",
    "to",
    "values",
    "xlink:href",
}
_ACCESSIBLE_TEXT_ATTRS = {"aria-description", "aria-label", "placeholder", "title", "value"}
_STRUCTURAL_TEXT = {
    "Important Safety Information",
    "Draft - for qualified MLR review",
    "Prescribing and reporting information",
}
DEMO_DISCLOSURE = (
    "Fictional demonstration — draft review aid only. Qualified Medical, Legal, and Regulatory reviewers "
    "must assess and approve all content before any use. Automated checks are not an approval decision."
)
_CUSTOM_EMAIL_TAG_ATTRIBUTES = {
    "html": frozenset({"lang"}),
    "head": frozenset(),
    "title": frozenset(),
    "body": frozenset({"bgcolor"}),
    "table": frozenset({"align", "bgcolor", "border", "cellpadding", "cellspacing", "role", "width"}),
    "thead": frozenset(),
    "tbody": frozenset(),
    "tfoot": frozenset(),
    "tr": frozenset({"align", "bgcolor", "valign"}),
    "td": frozenset({"align", "bgcolor", "colspan", "height", "rowspan", "valign", "width"}),
    "th": frozenset({"align", "bgcolor", "colspan", "height", "rowspan", "valign", "width"}),
    "div": frozenset({"align"}),
    "span": frozenset(),
    "p": frozenset({"align"}),
    "h1": frozenset({"align"}),
    "h2": frozenset({"align"}),
    "h3": frozenset({"align"}),
    "h4": frozenset({"align"}),
    "strong": frozenset(),
    "em": frozenset(),
    "b": frozenset(),
    "i": frozenset(),
    "a": frozenset({"href"}),
    "img": frozenset({"alt", "height", "src", "width"}),
    "br": frozenset(),
}
_CUSTOM_EMAIL_GLOBAL_ATTRIBUTES = frozenset({"data-role", "style"})
_CUSTOM_EMAIL_PHRASING_TAGS = frozenset({"a", "b", "br", "em", "i", "img", "span", "strong"})
_CUSTOM_EMAIL_FLOW_TAGS = _CUSTOM_EMAIL_PHRASING_TAGS | frozenset({"div", "h1", "h2", "h3", "h4", "p", "table"})
_CUSTOM_EMAIL_CHILD_TAGS = {
    "html": frozenset({"body", "head"}),
    "head": frozenset({"title"}),
    "title": frozenset(),
    "body": _CUSTOM_EMAIL_FLOW_TAGS,
    "table": frozenset({"tbody", "tfoot", "thead"}),
    "thead": frozenset({"tr"}),
    "tbody": frozenset({"tr"}),
    "tfoot": frozenset({"tr"}),
    "tr": frozenset({"td", "th"}),
    "td": _CUSTOM_EMAIL_FLOW_TAGS,
    "th": _CUSTOM_EMAIL_FLOW_TAGS,
    "div": _CUSTOM_EMAIL_FLOW_TAGS,
    "span": _CUSTOM_EMAIL_PHRASING_TAGS,
    "p": _CUSTOM_EMAIL_PHRASING_TAGS,
    "h1": _CUSTOM_EMAIL_PHRASING_TAGS,
    "h2": _CUSTOM_EMAIL_PHRASING_TAGS,
    "h3": _CUSTOM_EMAIL_PHRASING_TAGS,
    "h4": _CUSTOM_EMAIL_PHRASING_TAGS,
    "strong": _CUSTOM_EMAIL_PHRASING_TAGS,
    "em": _CUSTOM_EMAIL_PHRASING_TAGS,
    "b": _CUSTOM_EMAIL_PHRASING_TAGS,
    "i": _CUSTOM_EMAIL_PHRASING_TAGS,
    "a": _CUSTOM_EMAIL_PHRASING_TAGS - {"a"},
    "img": frozenset(),
    "br": frozenset(),
}
_CUSTOM_EMAIL_STRUCTURAL_TEXT_TAGS = frozenset({"html", "head", "body", "table", "thead", "tbody", "tfoot", "tr"})
_CUSTOM_EMAIL_CONTENT_ROLE_TAGS = frozenset({"div", "p", "span", "td", "th"})
_CUSTOM_EMAIL_HEADLINE_TAGS = _CUSTOM_EMAIL_CONTENT_ROLE_TAGS | frozenset({"h1", "h2", "h3", "h4"})
_CUSTOM_CSS_PROPERTIES = frozenset(
    {
        "background-color",
        "border-collapse",
        "color",
        "display",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "height",
        "letter-spacing",
        "line-height",
        "margin",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "margin-top",
        "max-width",
        "min-width",
        "padding",
        "padding-bottom",
        "padding-left",
        "padding-right",
        "padding-top",
        "table-layout",
        "text-align",
        "text-decoration",
        "text-transform",
        "vertical-align",
        "white-space",
        "width",
    }
)
_OPAQUE_COLOR_NAMES = frozenset(
    {
        "black",
        "blue",
        "gray",
        "green",
        "grey",
        "maroon",
        "navy",
        "olive",
        "orange",
        "purple",
        "red",
        "silver",
        "teal",
        "white",
        "yellow",
    }
)


class RenderContractError(Exception):
    """Stable renderer/validator failure suitable for ordinary MCP JSON."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def payload(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class AssetSnapshot:
    name: str
    path: Path
    payload: bytes
    sha256: str
    size: int
    mime_type: str
    width: int | None = None
    height: int | None = None

    @property
    def data_uri(self) -> str:
        encoded = base64.b64encode(self.payload).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


def error_response(exc: BaseException, *, fallback_code: str = "render_failed") -> dict[str, Any]:
    """Convert every renderer fault into the stable ordinary-JSON error envelope."""
    if isinstance(exc, RenderContractError):
        error = exc.payload()
    else:
        error = {"code": fallback_code, "message": "The campaign asset could not be rendered safely."}
    return {"error": error}


def _normalise(value: object) -> str:
    text = unescape(str(value))
    text = unicodedata.normalize("NFC", text)
    return " ".join(text.split())


def _regular_snapshot(path: Path, *, limit: int, code: str) -> tuple[bytes, tuple[int, int, int, int, int]]:
    """Read one bounded regular non-symlink file and return its stable identity."""
    try:
        before = path.lstat()
    except OSError as exc:
        raise RenderContractError(code, f"File is missing or unreadable: {path.name}.") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RenderContractError(code, f"File must be a regular non-symlink file: {path.name}.")
    if before.st_size > limit:
        raise RenderContractError(code, f"File exceeds the supported size limit: {path.name}.")
    try:
        payload = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise RenderContractError(code, f"File could not be read safely: {path.name}.") from exc
    before_id = (before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode), before.st_size, before.st_mtime_ns)
    after_id = (after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode), after.st_size, after.st_mtime_ns)
    if before_id != after_id or not stat.S_ISREG(after.st_mode) or len(payload) != after.st_size:
        raise RenderContractError(code, f"File changed during inspection: {path.name}.")
    return payload, after_id


def _regular_bytes(path: Path, *, limit: int, code: str) -> bytes:
    """Read one bounded regular non-symlink file and reject replacement during read."""
    return _regular_snapshot(path, limit=limit, code=code)[0]


def _strict_artifact(campaign_brief_id: str, filename: str) -> object:
    from ._campaign_store import read_campaign_json

    value, error, _path = read_campaign_json(campaign_brief_id, filename)
    if error:
        raise RenderContractError("invalid_render_input", f"Required campaign artifact is unavailable: {filename}.")
    return value


def _channel_copy(value: object, campaign_brief_id: str, channel: str) -> dict[str, Any]:
    from .models.copy import BannerCopy, EmailCopy, PersistedChannelCopy, PosterCopy

    try:
        envelope = PersistedChannelCopy.model_validate(value)
        if envelope.campaign_brief_id != campaign_brief_id or envelope.channel != channel:
            raise ValueError("copy identity mismatch")
        model = {"email": EmailCopy, "banner": BannerCopy, "poster": PosterCopy}[channel]
        return model.model_validate(envelope.copy_data).model_dump(mode="json")
    except (ValidationError, ValueError, TypeError) as exc:
        raise RenderContractError(
            "invalid_channel_copy", f"Persisted {channel} copy is malformed or mismatched."
        ) from exc


def load_render_context(campaign_brief_id: str, channel: str) -> dict[str, Any]:
    """Load the current gated brief/copy/brand context without fallback inputs."""
    from shared.filesystem import validate_component

    from ._renderer import validation_gate_state

    try:
        validate_component(campaign_brief_id, label="campaign_brief_id")
    except ValueError as exc:
        raise RenderContractError("unsafe_campaign_brief_id", str(exc)) from exc
    gate = validation_gate_state(campaign_brief_id, channel)
    if gate["status"] != "current":
        raise RenderContractError(
            "pre_render_validation_not_current",
            str(gate.get("reason") or "Pre-render validation is not current."),
        )
    brief = _strict_artifact(campaign_brief_id, "campaign-brief.json")
    brand = _strict_artifact(campaign_brief_id, "brand-components.json")
    if not isinstance(brief, dict) or brief.get("campaign_brief_id") != campaign_brief_id:
        raise RenderContractError("invalid_campaign_brief", "Campaign brief is malformed or mismatched.")
    if brief.get("language", "en") != "en":
        raise RenderContractError("unsupported_language", "Campaign Studio 1.1 renders English assets only.")
    if not isinstance(brief.get("channels"), list) or channel not in brief["channels"]:
        raise RenderContractError("invalid_campaign_brief", f"The campaign brief does not select {channel}.")
    if not isinstance(brand, dict):
        raise RenderContractError("invalid_brand_components", "Persisted brand components must be an object.")
    copy = _channel_copy(_strict_artifact(campaign_brief_id, f"copy-{channel}.json"), campaign_brief_id, channel)
    legal = brand.get("legal")
    if not isinstance(legal, dict):
        raise RenderContractError("invalid_brand_components", "Persisted legal brand content must be an object.")
    required_legal = required_legal_roles(brief, legal, channel)
    if channel == "email" and brief.get("demo_mode") is True:
        required_legal["demo_disclosure"] = DEMO_DISCLOSURE
    context = {
        "campaign_brief_id": campaign_brief_id,
        "brief": brief,
        "brand": brand,
        "copy": copy,
        "legal": required_legal,
        "palette": brand.get("palette") if isinstance(brand.get("palette"), dict) else {},
        "typography": brand.get("typography") if isinstance(brand.get("typography"), dict) else {},
    }
    if channel in {"email", "banner"}:
        context["logo"] = sealed_asset(brand, "logo.svg", required=True)
    if channel == "poster":
        context["product"] = sealed_asset(brand, "product.png", required=False)
    return context


def required_legal_roles(brief: dict[str, Any], legal: dict[str, Any], channel: str) -> dict[str, str]:
    from ._claim_engine import load_policy_rules

    rules = load_policy_rules(str(brief.get("policy_jurisdiction", "FDA")))
    names = (
        rules.get("channel_requirements", {})
        .get(channel, {})
        .get("required_elements", rules.get("required_elements", []))
    )
    if not isinstance(names, list):
        raise RenderContractError("invalid_policy", "Channel legal requirements are malformed.")
    result: dict[str, str] = {}
    for name in names:
        value = legal.get(name)
        if not isinstance(name, str) or not isinstance(value, str) or not value.strip():
            raise RenderContractError("missing_required_legal", f"Required legal content is unavailable: {name}.")
        result[name] = value
    return result


def _asset_root(brand: dict[str, Any]) -> Path:
    raw = brand.get("resolved_brand_kit_path") or brand.get("brand_kit_path")
    if not isinstance(raw, str) or not raw:
        raise RenderContractError("invalid_brand_asset", "Persisted brand-kit path is missing.")
    root = Path(raw)
    if not root.is_absolute():
        try:
            root = root.resolve(strict=True)
        except OSError as exc:
            raise RenderContractError("invalid_brand_asset", "Persisted brand-kit path is unavailable.") from exc
    try:
        state = root.lstat()
    except OSError as exc:
        raise RenderContractError("invalid_brand_asset", "Persisted brand-kit path is unavailable.") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        raise RenderContractError("invalid_brand_asset", "Persisted brand-kit path must be a real directory.")
    return root


def sealed_asset(brand: dict[str, Any], name: str, *, required: bool) -> AssetSnapshot | None:
    """Read and verify exactly the selected brand bytes sealed by Task 1/3."""
    files_metadata = brand.get("files")
    metadata = files_metadata.get(name) if isinstance(files_metadata, dict) else None
    if metadata is None and not required:
        return None
    if not isinstance(metadata, dict):
        raise RenderContractError("missing_brand_asset", f"Selected brand asset is unavailable: {name}.")
    raw = metadata.get("resolved_path") or metadata.get("path")
    if not isinstance(raw, str) or not raw:
        raise RenderContractError("invalid_brand_asset", f"Selected brand asset path is malformed: {name}.")
    path = Path(raw)
    if not path.is_absolute():
        try:
            path = path.resolve(strict=True)
        except OSError as exc:
            raise RenderContractError("invalid_brand_asset", f"Selected brand asset is unavailable: {name}.") from exc
    root = _asset_root(brand)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RenderContractError("invalid_brand_asset", f"Selected brand asset escapes its kit: {name}.") from exc
    payload = _regular_bytes(path, limit=_OUTPUT_LIMIT, code="invalid_brand_asset")
    digest = hashlib.sha256(payload).hexdigest()
    expected_hash = metadata.get("sha256")
    expected_size = metadata.get("size")
    if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash) or type(expected_size) is not int:
        raise RenderContractError("invalid_brand_asset", f"Selected brand asset metadata is malformed: {name}.")
    if digest != expected_hash or len(payload) != expected_size:
        raise RenderContractError("brand_asset_changed", f"Selected brand asset changed after preflight: {name}.")
    mime, width, height = _image_type(payload)
    if name == "logo.svg" and mime != "image/svg+xml":
        raise RenderContractError("invalid_logo", "The selected logo is not a safe SVG image.")
    if name == "product.png" and mime not in {"image/png", "image/jpeg"}:
        raise RenderContractError("invalid_product_image", "The selected product image has invalid image bytes.")
    return AssetSnapshot(name, path, payload, digest, len(payload), mime, width, height)


def _image_type(payload: bytes) -> tuple[str | None, int | None, int | None]:
    stripped = payload.lstrip()
    if stripped.startswith(b"<"):
        if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", payload, re.I):
            return None, None, None
        try:
            root = ElementTree.fromstring(payload.decode("utf-8"))
        except (UnicodeDecodeError, ElementTree.ParseError):
            return None, None, None
        if root.tag.rsplit("}", 1)[-1].casefold() != "svg" or _unsafe_svg_tree(root):
            return None, None, None
        return "image/svg+xml", None, None
    if len(payload) >= 24 and payload.startswith(b"\x89PNG\r\n\x1a\n") and payload[12:16] == b"IHDR":
        width = int.from_bytes(payload[16:20], "big")
        height = int.from_bytes(payload[20:24], "big")
        if width > 0 and height > 0:
            return "image/png", width, height
    if payload.startswith(b"\xff\xd8"):
        dimensions = _jpeg_dimensions(payload)
        if dimensions:
            return "image/jpeg", *dimensions
    return None, None, None


def _jpeg_dimensions(payload: bytes) -> tuple[int, int] | None:
    offset = 2
    while offset + 9 < len(payload):
        if payload[offset] != 0xFF:
            return None
        marker = payload[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(payload):
            return None
        length = int.from_bytes(payload[offset : offset + 2], "big")
        if length < 2 or offset + length > len(payload):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(payload[offset + 3 : offset + 5], "big")
            width = int.from_bytes(payload[offset + 5 : offset + 7], "big")
            return (width, height) if width and height else None
        offset += length
    return None


def resolve_banner_dimensions(brief: dict[str, Any], override: object = None) -> tuple[str, int, int, str]:
    dimensions = brief.get("asset_dimensions")
    if dimensions is not None and not isinstance(dimensions, dict):
        raise RenderContractError("invalid_dimensions", "Brief asset_dimensions must be an object.")
    raw = (dimensions or {}).get("banner", "728x90")
    canonical, width, height, profile = _parse_banner_dimension(raw)
    if override is not None:
        requested, _rw, _rh, _rp = _parse_banner_dimension(override)
        if requested != canonical:
            raise RenderContractError("dimension_conflict", "Banner dimensions conflict with the campaign brief.")
    return canonical, width, height, profile


def _parse_banner_dimension(value: object) -> tuple[str, int, int, str]:
    if not isinstance(value, str) or (match := _DIMENSION.fullmatch(value)) is None:
        raise RenderContractError("invalid_dimensions", "Banner dimensions must use canonical WxH pixels.")
    width, height = (int(match.group(1)), int(match.group(2)))
    if width > 2000 or height > 2000 or width * height > 2_000_000:
        raise RenderContractError("dimensions_too_large", "Banner dimensions exceed the supported limit.")
    canonical = f"{width}x{height}"
    profile = _BANNER_PROFILES.get(canonical)
    if profile is None:
        raise RenderContractError("unsupported_dimensions", "Banner dimensions do not match a supported profile.")
    return canonical, width, height, profile


def resolve_poster_dimensions(brief: dict[str, Any], override: object = None) -> tuple[str, float, float]:
    dimensions = brief.get("asset_dimensions")
    if dimensions is not None and not isinstance(dimensions, dict):
        raise RenderContractError("invalid_dimensions", "Brief asset_dimensions must be an object.")
    raw = (dimensions or {}).get("poster", "A4")
    canonical = _parse_paper(raw)
    if override is not None and _parse_paper(override) != canonical:
        raise RenderContractError("dimension_conflict", "Poster paper size conflicts with the campaign brief.")
    width, height = _PAPER_SIZES[canonical]
    return canonical, width, height


def _parse_paper(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RenderContractError("invalid_dimensions", "Poster paper size is malformed.")
    canonical = value.strip().upper()
    if canonical not in _PAPER_SIZES:
        raise RenderContractError("unsupported_dimensions", "Poster paper size is unsupported.")
    return canonical


def copy_role_values(channel: str, copy: dict[str, Any]) -> dict[str, str]:
    roles: dict[str, str] = {}
    simple = {
        "email": ("subject", "preheader", "headline", "cta"),
        "banner": ("headline", "sub_headline", "safety", "cta"),
        "poster": ("headline", "subhead", "cta"),
    }[channel]
    for name in simple:
        block = copy.get(name)
        if isinstance(block, dict):
            roles[name] = block["text"]
    for name in {"email": ("body",), "banner": (), "poster": ("body", "bullet_points", "footnotes")}[channel]:
        for index, block in enumerate(copy.get(name) or []):
            roles[f"{name}-{index}"] = block["text"] if isinstance(block, dict) else block
    return roles


def expected_roles(context: dict[str, Any], channel: str) -> dict[str, str]:
    roles = copy_role_values(channel, context["copy"])
    roles.update({f"legal-{name}": value for name, value in context["legal"].items()})
    return roles


def _decode_css(value: str) -> str:
    decoded = unescape(value)
    decoded = re.sub(r"/\*.*?\*/", "", decoded, flags=re.S)

    def replace_escape(match: re.Match[str]) -> str:
        if match.group(1):
            try:
                return chr(int(match.group(1), 16))
            except (ValueError, OverflowError):
                return "\ufffd"
        return match.group(2) or ""

    for _index in range(4):
        expanded = re.sub(r"\\([0-9a-fA-F]{1,6})(?:[\t\n\f\r ]|\r\n)?|\\(.)", replace_escape, decoded, flags=re.S)
        if expanded == decoded:
            break
        decoded = expanded
    return "".join(character for character in decoded if ord(character) >= 32 and ord(character) != 127).casefold()


def _css_hides_content(value: str) -> bool:
    compact = re.sub(r"\s+", "", _decode_css(value))
    declaration = r"(?:\A|[;{])"
    patterns = (
        r"display:none",
        r"visibility:(?:hidden|collapse)",
        r"content-visibility:hidden",
        r"opacity:(?:0+(?:\.0*)?|\.0+)",
        r"color:transparent",
        r"font-size:0(?:[a-z%]+)?",
        r"line-height:0(?:[a-z%]+)?",
        r"mso-hide:all",
        r"overflow:hidden",
        r"(?:max-)?(?:width|height):0(?:[a-z%]+)?",
        r"clip(?:-path)?:",
        r"transform:[^;}]*?(?:scale[xy]?\(0|translate[xy]?\(-?[1-9]\d{2,})",
        r"(?:left|right|top|bottom|text-indent|margin-(?:left|right|top|bottom)):-[1-9]\d{2,}",
    )
    return any(re.search(declaration + pattern, compact) for pattern in patterns)


def _safe_preheader_hiding_css(value: str) -> bool:
    decoded = _decode_css(value)
    declarations = [item.strip() for item in decoded.split(";") if item.strip()]
    if not declarations:
        return False
    allowed = {
        "display": {"none"},
        "max-height": {"0", "0px"},
        "overflow": {"hidden"},
        "opacity": {"0", "0.0"},
        "color": {"transparent"},
    }
    parsed: dict[str, str] = {}
    for declaration in declarations:
        if ":" not in declaration:
            return False
        name, raw = declaration.split(":", 1)
        name = name.strip()
        raw = re.sub(r"\s*!important\s*\Z", "", raw).strip()
        if name in parsed or name not in allowed or raw not in allowed[name]:
            return False
        parsed[name] = raw
    return parsed.get("display") == "none"


def _unsafe_css(value: str, *, allow_preheader_hiding: bool = False) -> bool:
    lowered = _decode_css(value)
    compact = re.sub(r"\s+", "", lowered)
    if re.search(
        r"(?:@import|@font-face|@(?:-webkit-)?keyframes|@counter-style|expression\(|javascript:|file:|https?:|"
        r"data:|//|url\(|(?:-webkit-)?image-set\(|(?:\A|[;{])(?:-webkit-)?(?:animation|transition)(?:-[a-z-]+)?:|"
        r"(?:\A|[;{])(?:list-style(?:-type|-image)?|marker(?:-side)?):|::marker)",
        compact,
    ):
        return True
    for match in re.finditer(r"(?:\A|[;{])\s*content\s*:\s*([^;}]*?)(?=[;}]|\Z)", lowered):
        generated = re.sub(r"\s*!important\s*\Z", "", match.group(1)).strip()
        if generated not in {"", "none", "normal", '""', "''"}:
            return True
    if _css_hides_content(lowered):
        return not (allow_preheader_hiding and _safe_preheader_hiding_css(lowered))
    return False


def _unsafe_stylesheet(value: str) -> bool:
    lowered = _decode_css(value)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", lowered, flags=re.S):
        selector = " ".join(match.group(1).split())
        body = match.group(2)
        if _unsafe_css(body, allow_preheader_hiding=selector == ".preheader"):
            return True
    structural = re.sub(r"[^{}]+\{[^{}]*\}", "", lowered, flags=re.S)
    return _unsafe_css(structural)


_CONDITIONAL_COMMENT = re.compile(r"(?:\[\s*(?:if|endif)\b|<!\s*\[\s*(?:if|endif)\b)", re.I)
_COMMENT_MARKUP = re.compile(r"<\s*(?:/?\s*[a-z][\w:.-]*|[!?])", re.I)


def _unsafe_html_comment(value: str) -> bool:
    decoded = unescape(value)
    return bool(_CONDITIONAL_COMMENT.search(decoded) or _COMMENT_MARKUP.search(decoded))


def _unsafe_comment_source(source: str) -> bool:
    comments = list(re.finditer(r"<!--(.*?)-->", source, flags=re.S))
    if any(_unsafe_html_comment(match.group(1)) for match in comments):
        return True
    without_comments = re.sub(r"<!--.*?-->", "", source, flags=re.S)
    return "<!--" in without_comments or "-->" in without_comments or bool(_CONDITIONAL_COMMENT.search(source))


_SAFE_EMAIL_DOCTYPE = re.compile(r"\A\s*<!doctype\s+html\s*>\s*", re.I)
_CSS_MEASURE = re.compile(r"(?P<number>(?:\d+(?:\.\d+)?|\.\d+))(?P<unit>px|pt|em|rem|%)?\Z")
_CSS_FONT_SIZE_BOUNDS = {"px": (8.0, 72.0), "pt": (6.0, 54.0), "em": (0.5, 4.0), "rem": (0.5, 4.0), "%": (50.0, 300.0)}
_CSS_LINE_HEIGHT_BOUNDS = {
    "": (1.0, 3.0),
    "px": (8.0, 96.0),
    "pt": (6.0, 72.0),
    "em": (0.8, 4.0),
    "rem": (0.8, 4.0),
    "%": (80.0, 300.0),
}
_CSS_LETTER_SPACING_BOUNDS = {
    "px": (0.0, 10.0),
    "pt": (0.0, 8.0),
    "em": (0.0, 1.0),
    "rem": (0.0, 1.0),
}
_CSS_BOX_BOUNDS = {
    "px": (0.0, 64.0),
    "pt": (0.0, 48.0),
    "em": (0.0, 4.0),
    "rem": (0.0, 4.0),
    "%": (0.0, 25.0),
}
_CSS_SIZE_BOUNDS = {
    "px": (1.0, 1200.0),
    "pt": (0.75, 900.0),
    "em": (0.1, 100.0),
    "rem": (0.1, 100.0),
    "%": (1.0, 100.0),
}
_CSS_HEIGHT_BOUNDS = {
    "px": (1.0, 2000.0),
    "pt": (0.75, 1500.0),
    "em": (0.1, 100.0),
    "rem": (0.1, 100.0),
    "%": (1.0, 100.0),
}


def _bounded_css_measure(
    value: str,
    bounds: dict[str, tuple[float, float]],
    *,
    allow_zero: bool = False,
) -> bool:
    match = _CSS_MEASURE.fullmatch(value)
    if match is None:
        return False
    number = float(match.group("number"))
    unit = match.group("unit") or ""
    if number == 0 and allow_zero and unit in {"", *bounds}:
        return True
    limits = bounds.get(unit)
    return bool(limits and limits[0] <= number <= limits[1])


def _opaque_css_color(value: str) -> bool:
    lowered = value.casefold()
    if lowered in _OPAQUE_COLOR_NAMES or re.fullmatch(r"#[0-9a-f]{3}(?:[0-9a-f]{3})?", lowered):
        return True
    match = re.fullmatch(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", lowered)
    return bool(match and all(int(component) <= 255 for component in match.groups()))


def _box_css_value(value: str, *, allow_auto: bool) -> bool:
    parts = value.split()
    return (
        bool(parts)
        and len(parts) <= 4
        and all(
            (allow_auto and part == "auto") or _bounded_css_measure(part, _CSS_BOX_BOUNDS, allow_zero=True)
            for part in parts
        )
    )


def _custom_css_value_allowed(name: str, value: str) -> bool:
    lowered = value.casefold().strip()
    if not lowered or any(token in lowered for token in ("var(", "url(", "calc(", "expression(", "!important")):
        return False
    if name in {"background-color", "color"}:
        return _opaque_css_color(lowered)
    if name == "font-family":
        return bool(re.fullmatch(r"[a-z0-9 _,'\"-]+", lowered))
    if name == "font-size":
        return _bounded_css_measure(lowered, _CSS_FONT_SIZE_BOUNDS)
    if name == "line-height":
        return _bounded_css_measure(lowered, _CSS_LINE_HEIGHT_BOUNDS)
    if name in {"max-width", "min-width", "width"}:
        return _bounded_css_measure(lowered, _CSS_SIZE_BOUNDS)
    if name == "height":
        return _bounded_css_measure(lowered, _CSS_HEIGHT_BOUNDS)
    if name == "letter-spacing":
        return lowered == "normal" or _bounded_css_measure(lowered, _CSS_LETTER_SPACING_BOUNDS, allow_zero=True)
    if name.startswith("padding"):
        return _box_css_value(lowered, allow_auto=False)
    if name.startswith("margin"):
        return _box_css_value(lowered, allow_auto=True)
    if name == "font-style":
        return lowered in {"normal", "italic"}
    if name == "font-weight":
        return lowered in {"normal", "bold", "400", "500", "600", "700"}
    if name == "text-align":
        return lowered in {"left", "center", "right"}
    if name == "text-decoration":
        return lowered in {"none", "underline"}
    if name == "text-transform":
        return lowered in {"none", "uppercase", "lowercase", "capitalize"}
    if name == "vertical-align":
        return lowered in {"top", "middle", "bottom", "baseline"}
    if name == "white-space":
        return lowered in {"normal", "nowrap"}
    if name == "border-collapse":
        return lowered in {"collapse", "separate"}
    if name == "table-layout":
        return lowered in {"auto", "fixed"}
    if name == "display":
        return lowered in {"block", "inline", "inline-block", "table", "table-cell", "table-row"}
    return False


def _custom_inline_style_errors(value: str, *, preheader: bool) -> list[str]:
    if preheader:
        return [] if value == "display:none" else ["preheader must use the exact approved hidden style"]
    if (
        not value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(token in value for token in ("/*", "*/", "\\", "{", "}", "@"))
        or "--" in value
    ):
        return ["custom inline CSS is outside the static subset"]
    declarations = [item.strip() for item in value.split(";") if item.strip()]
    seen: set[str] = set()
    for declaration in declarations:
        if ":" not in declaration:
            return ["custom inline CSS is malformed"]
        name, raw_value = (item.strip() for item in declaration.split(":", 1))
        name = name.casefold()
        if name in seen or name not in _CUSTOM_CSS_PROPERTIES or not _custom_css_value_allowed(name, raw_value):
            return ["custom inline CSS is outside the static subset"]
        seen.add(name)
    return [] if seen else ["custom inline CSS is empty"]


def _custom_email_xml_root(source: str) -> ElementTree.Element:
    if "<!--" in source or "-->" in source or "<?" in source:
        raise ValueError("custom email comments and processing instructions are unsupported")
    without_doctype = _SAFE_EMAIL_DOCTYPE.sub("", source, count=1)
    if re.search(r"<!\s*(?:doctype|entity)\b", without_doctype, re.I):
        raise ValueError("custom email declarations are unsupported")
    root = ElementTree.fromstring(without_doctype)
    if root.tag != "html":
        raise ValueError("custom email root must be lowercase html without a namespace")
    return root


def _custom_email_attribute_errors(tag: str, attributes: dict[str, str], *, source: bool) -> list[str]:
    allowed = _CUSTOM_EMAIL_GLOBAL_ATTRIBUTES | _CUSTOM_EMAIL_TAG_ATTRIBUTES[tag]
    errors: list[str] = []
    if set(attributes) - allowed:
        errors.append("custom email attribute is outside the per-tag allowlist")
    role = attributes.get("data-role")
    if role is not None and not re.fullmatch(r"[a-z][a-z0-9_-]*", role):
        errors.append("custom email role is malformed")
    style = attributes.get("style")
    if style is not None:
        errors.extend(_custom_inline_style_errors(style, preheader=role == "preheader"))
    for name, value in attributes.items():
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            errors.append("custom email attribute contains a control character")
        if name == "href" and source and value != "{{ cta_url }}":
            errors.append("custom email href must use the approved CTA variable")
        elif name == "src" and source and value != "{{ logo_data_uri }}":
            errors.append("custom email image must use the sealed logo variable")
        elif name == "alt" and source and value != "{{ brand }} logo":
            errors.append("custom email logo alt must use the approved brand variable")
        elif name == "lang" and value not in {"en", "{{ language }}"}:
            errors.append("custom email language must be English")
        elif name in {"align"} and value.casefold() not in {"left", "center", "right"}:
            errors.append("custom email alignment is unsupported")
        elif name == "valign" and value.casefold() not in {"top", "middle", "bottom", "baseline"}:
            errors.append("custom email vertical alignment is unsupported")
        elif name in {"border", "cellpadding", "cellspacing"} and not re.fullmatch(r"\d{1,3}", value):
            errors.append("custom email table spacing is malformed")
        elif name in {"colspan", "rowspan"} and not re.fullmatch(r"[1-9]\d{0,2}", value):
            errors.append("custom email table span is malformed")
        elif name in {"height", "width"} and not re.fullmatch(r"(?:[1-9]\d{0,3})(?:%)?", value):
            errors.append("custom email dimension is malformed")
        elif name == "bgcolor" and not _opaque_css_color(value):
            errors.append("custom email background color must be opaque")
        elif name == "role" and value != "presentation":
            errors.append("custom email table role is unsupported")
    return errors


def _custom_email_tree_errors(root: ElementTree.Element, *, source: bool) -> list[str]:
    errors: list[str] = []
    children = list(root)
    parents = {child: parent for parent in root.iter() for child in parent}
    if [child.tag for child in children] != ["head", "body"]:
        errors.append("custom email must contain exact head and body children")
    head = children[0] if len(children) == 2 and children[0].tag == "head" else None
    body = children[1] if len(children) == 2 and children[1].tag == "body" else None

    def is_descendant(element: ElementTree.Element, ancestor: ElementTree.Element | None) -> bool:
        while element in parents:
            element = parents[element]
            if element is ancestor:
                return True
        return False

    for element in root.iter():
        tag = element.tag
        if not isinstance(tag, str) or tag not in _CUSTOM_EMAIL_TAG_ATTRIBUTES:
            errors.append("custom email tag is outside the static allowlist")
            continue
        errors.extend(_custom_email_attribute_errors(tag, element.attrib, source=source))
        child_tags = [child.tag for child in element]
        if any(child_tag not in _CUSTOM_EMAIL_CHILD_TAGS[tag] for child_tag in child_tags):
            errors.append("custom email element nesting is outside the static content model")
        if tag in _CUSTOM_EMAIL_STRUCTURAL_TEXT_TAGS and (
            (element.text and element.text.strip()) or any(child.tail and child.tail.strip() for child in element)
        ):
            errors.append("custom email structural containers may contain only element children")
        role = element.attrib.get("data-role")
        if role == "subject":
            if tag != "title" or head is None or parents.get(element) is not head:
                errors.append("custom email subject role must be the actual head title")
        elif role == "preheader":
            if tag != "div" or body is None or not is_descendant(element, body):
                errors.append("custom email preheader role must be a body div")
        elif role == "headline":
            if tag not in _CUSTOM_EMAIL_HEADLINE_TAGS or body is None or not is_descendant(element, body):
                errors.append("custom email headline role must be a body heading")
        elif role == "cta":
            if tag != "a" or body is None or not is_descendant(element, body):
                errors.append("custom email CTA role must be a body anchor")
        elif role is not None:
            content_role = bool(re.fullmatch(r"(?:body-\d+|legal-[a-z][a-z0-9_-]*)", role))
            if (
                not content_role
                or tag not in _CUSTOM_EMAIL_CONTENT_ROLE_TAGS
                or body is None
                or not is_descendant(element, body)
            ):
                errors.append("custom email content role is outside the semantic role model")
    if children and [child.tag for child in children[0]] != ["title"]:
        errors.append("custom email head must contain exactly one title")
    return sorted(set(errors))


def _custom_template_source_errors(source: str) -> list[str]:
    try:
        root = _custom_email_xml_root(source)
    except (ElementTree.ParseError, ValueError):
        return ["custom template must be well-formed XHTML"]
    return _custom_email_tree_errors(root, source=True)


class _HTMLInspector(HTMLParser):
    def __init__(self, *, approved_cta: str | None):
        super().__init__(convert_charrefs=True)
        self.approved_cta = approved_cta
        self.stack: list[tuple[str, str | None, bool, bool, bool]] = []
        self.roles: dict[str, list[str]] = {}
        self.role_elements: dict[str, int] = {}
        self.role_visibility: dict[str, list[bool]] = {}
        self.extra_visible: list[str] = []
        self.extra_authored: list[str] = []
        self.attribute_text: list[str] = []
        self.errors: list[str] = []
        self.anchors: list[str] = []
        self.images: list[str] = []
        self.image_alts: list[str] = []
        self.has_table = False
        self.style_chunks: list[str] = []
        self.title_chunks: list[str] = []
        self.title_elements: list[str | None] = []

    def handle_decl(self, decl: str) -> None:
        if decl.casefold().startswith("doctype html"):
            return
        self.errors.append("unsupported HTML declaration")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        names = [name.casefold() for name, _value in attrs]
        if len(names) != len(set(names)):
            self.errors.append("duplicate HTML attribute")
        attr = {name.casefold(): value or "" for name, value in attrs}
        inherited_hidden = self.stack[-1][2] if self.stack else False
        inherited_ignored = self.stack[-1][3] if self.stack else False
        inherited_inaccessible = self.stack[-1][4] if self.stack else False
        explicit_role = attr.get("data-role")
        hidden = inherited_hidden or tag == "head" or "hidden" in attr or "inert" in attr
        style = attr.get("style", "")
        is_preheader = explicit_role == "preheader"
        if _css_hides_content(style):
            hidden = True
        ignored = inherited_ignored or tag in {"style", "script"}
        inaccessible = inherited_inaccessible or attr.get("aria-hidden", "").strip().casefold() == "true"
        if "data-role" in attr:
            if explicit_role:
                self.role_elements[explicit_role] = self.role_elements.get(explicit_role, 0) + 1
                self.role_visibility.setdefault(explicit_role, []).append(
                    not hidden and not inaccessible and not ignored
                )
            else:
                self.errors.append("blank rendered role")
        role = explicit_role or (self.stack[-1][1] if self.stack else None)
        inside_head = any(item[0] == "head" for item in self.stack)
        self.stack.append((tag, role, hidden, ignored, inaccessible))
        if tag == "title":
            self.title_elements.append(explicit_role)
            if explicit_role != "subject" or not inside_head:
                self.errors.append("document title must be the sole approved subject in head")
        if role == "preheader" and not hidden:
            self.errors.append("preheader must be hidden")
        if explicit_role and explicit_role not in {"preheader", "subject"} and (hidden or inaccessible or ignored):
            self.errors.append(f"rendered role is hidden or inaccessible: {explicit_role}")
        if tag in _ACTIVE_HTML or ":" in tag:
            self.errors.append(f"active HTML element: {tag}")
        if tag == "meta" and attr.get("http-equiv", "").casefold() == "refresh":
            self.errors.append("meta refresh is forbidden")
        if "srcset" in attr:
            self.errors.append("srcset is forbidden")
        if "srcdoc" in attr:
            self.errors.append("srcdoc is forbidden")
        for name, value in attr.items():
            if name.startswith("on"):
                self.errors.append("event handler is forbidden")
            if name in _ACCESSIBLE_TEXT_ATTRS or (name.startswith("aria-") and name != "aria-hidden"):
                if value:
                    self.attribute_text.append(value)
                self.errors.append("unapproved accessible attribute content")
            if name == "alt":
                if value:
                    self.attribute_text.append(value)
                if tag != "img":
                    self.errors.append("alt text is only permitted for the sealed logo image")
            if name == "style" and _unsafe_css(value, allow_preheader_hiding=is_preheader):
                self.errors.append("unsafe inline CSS")
            if name in _URL_ATTRS:
                self._url(tag, name, value, role)
        if tag == "table":
            self.has_table = True
        if tag == "img":
            self.image_alts.append(attr.get("alt", ""))
        if tag in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.pop()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        if not self.stack:
            return
        tag, role, hidden, ignored, _inaccessible = self.stack[-1]
        if tag == "style":
            self.style_chunks.append(data)
        if tag == "title":
            self.title_chunks.append(data)
            if role:
                self.roles.setdefault(role, []).append(data)
            return
        if ignored:
            return
        if role:
            self.roles.setdefault(role, []).append(data)
        elif data.strip():
            self.extra_authored.append(data)
        if not hidden and data.strip():
            if role is None:
                self.extra_visible.append(data)

    def handle_comment(self, data: str) -> None:
        if _unsafe_html_comment(data):
            self.errors.append("conditional or markup-bearing HTML comment")

    def _url(self, tag: str, name: str, value: str, role: str | None) -> None:
        decoded = unescape(value)
        if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
            self.errors.append("control character in resource reference")
            return
        if tag == "a" and name == "href":
            self.anchors.append(decoded)
            if role != "cta" or decoded != self.approved_cta:
                self.errors.append("unapproved anchor target")
            return
        if tag == "img" and name == "src" and decoded.startswith("data:image/"):
            self.images.append(decoded)
            return
        self.errors.append("external or local resource reference")


def _inspect_custom_html(source: str, context: dict[str, Any]) -> dict[str, Any]:
    try:
        root = _custom_email_xml_root(source)
    except (ElementTree.ParseError, ValueError) as exc:
        raise RenderContractError("invalid_rendered_html", "Rendered custom email must be well-formed XHTML.") from exc
    errors = _custom_email_tree_errors(root, source=False)
    parents = {child: parent for parent in root.iter() for child in parent}
    role_chunks: dict[str, list[str]] = {}
    role_elements: dict[str, int] = {}
    role_visibility: dict[str, list[bool]] = {}
    authored_extra: list[str] = []

    def append_text(role: str | None, value: str | None) -> None:
        if not value or not value.strip():
            return
        if role:
            role_chunks.setdefault(role, []).append(value)
        else:
            authored_extra.append(value)

    def walk(element: ElementTree.Element, inherited_role: str | None = None) -> None:
        explicit_role = element.get("data-role")
        role = explicit_role or inherited_role
        if explicit_role:
            role_elements[explicit_role] = role_elements.get(explicit_role, 0) + 1
            ancestor = parents.get(element)
            hidden_ancestor = False
            while ancestor is not None:
                if ancestor.get("data-role") == "preheader" and ancestor.get("style") == "display:none":
                    hidden_ancestor = True
                    break
                ancestor = parents.get(ancestor)
            hidden = (explicit_role == "preheader" and element.get("style") == "display:none") or hidden_ancestor
            role_visibility.setdefault(explicit_role, []).append(not hidden)
        append_text(role, element.text)
        for child in element:
            walk(child, role)
            append_text(role, child.tail)

    walk(root)
    roles = {name: _normalise(" ".join(chunks)) for name, chunks in role_chunks.items()}
    expected = expected_roles(context, "email")
    for name, value in expected.items():
        if roles.get(name) != _normalise(value):
            errors.append(f"role mismatch: {name}")
        if role_elements.get(name) != 1:
            errors.append(f"role cardinality mismatch: {name}")
        visibility = role_visibility.get(name)
        if name == "preheader":
            elements = [element for element in root.iter() if element.get("data-role") == name]
            if (
                visibility != [False]
                or len(elements) != 1
                or elements[0].tag != "div"
                or elements[0].get("style") != "display:none"
            ):
                errors.append("preheader visibility mismatch")
        elif name != "subject" and visibility != [True]:
            errors.append(f"role visibility mismatch: {name}")
    if set(roles) - set(expected) or set(role_elements) - set(expected):
        errors.append("unexpected rendered role")
    heads = [element for element in root if element.tag == "head"]
    titles = [element for element in root.iter() if element.tag == "title"]
    if (
        len(heads) != 1
        or len(titles) != 1
        or titles[0].get("data-role") != "subject"
        or not any(ancestor is heads[0] for ancestor in _element_ancestors(titles[0], parents))
        or _normalise(" ".join(titles[0].itertext())) != _normalise(expected.get("subject", ""))
    ):
        errors.append("email must contain exactly one approved document title inside head")
    allowed_extra = {_normalise(value) for value in _STRUCTURAL_TEXT | {str(context["brief"].get("brand", ""))}}
    normalised_extra = [_normalise(value) for value in authored_extra if _normalise(value)]
    if any(value not in allowed_extra for value in normalised_extra):
        errors.append("unapproved authored text")
    if any(normalised_extra.count(value) > 1 for value in set(normalised_extra)):
        errors.append("duplicated structural text")
    anchors = [element.get("href", "") for element in root.iter() if element.tag == "a"]
    if anchors != [context["brief"].get("call_to_action_url")]:
        errors.append("email must contain exactly one approved CTA anchor")
    images = [element.get("src", "") for element in root.iter() if element.tag == "img"]
    image_alts = [element.get("alt", "") for element in root.iter() if element.tag == "img"]
    if not any(element.tag == "table" for element in root.iter()):
        errors.append("email must use a table layout")
    if len(images) != 1:
        errors.append("email must contain exactly one embedded logo")
    elif _data_uri_identity(images[0]) != (context["logo"].mime_type, context["logo"].sha256):
        errors.append("embedded email logo does not match the selected bytes")
    if image_alts != [f"{context['brief'].get('brand', '')} logo"]:
        errors.append("email logo must have meaningful brand alt text")
    content_text = _normalise(" ".join([*roles.values(), *normalised_extra, *image_alts]))
    visible_text = _normalise(" ".join(value for name, value in roles.items() if name not in {"preheader", "subject"}))
    return {
        "roles": roles,
        "errors": sorted(set(errors)),
        "assets": images,
        "visible_text": visible_text,
        "content_text": content_text,
    }


def _element_ancestors(
    element: ElementTree.Element, parents: dict[ElementTree.Element, ElementTree.Element]
) -> list[ElementTree.Element]:
    ancestors: list[ElementTree.Element] = []
    current = parents.get(element)
    while current is not None:
        ancestors.append(current)
        current = parents.get(current)
    return ancestors


def inspect_html(payload: bytes, context: dict[str, Any], *, custom: bool = False) -> dict[str, Any]:
    try:
        source = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RenderContractError("invalid_rendered_html", "Rendered email is not UTF-8 HTML.") from exc
    if len(payload) > _OUTPUT_LIMIT:
        raise RenderContractError("rendered_output_too_large", "Rendered email exceeds the size limit.")
    if custom:
        return _inspect_custom_html(source, context)
    parser = _HTMLInspector(approved_cta=context["brief"].get("call_to_action_url"))
    try:
        parser.feed(source)
        parser.close()
    except Exception as exc:
        raise RenderContractError("invalid_rendered_html", "Rendered email HTML is malformed.") from exc
    if _unsafe_stylesheet(" ".join(parser.style_chunks)):
        parser.errors.append("unsafe stylesheet")
    roles = {name: _normalise(" ".join(chunks)) for name, chunks in parser.roles.items()}
    errors = list(parser.errors)
    expected = expected_roles(context, "email")
    for name, value in expected.items():
        if roles.get(name) != _normalise(value):
            errors.append(f"role mismatch: {name}")
        if parser.role_elements.get(name) != 1:
            errors.append(f"role cardinality mismatch: {name}")
        visibility = parser.role_visibility.get(name)
        if name == "preheader":
            if visibility != [False]:
                errors.append("preheader visibility mismatch")
        elif name != "subject" and visibility != [True]:
            errors.append(f"role visibility mismatch: {name}")
    if set(roles) - set(expected) or set(parser.role_elements) - set(expected):
        errors.append("unexpected rendered role")
    expected_subject = _normalise(expected.get("subject", ""))
    if parser.title_elements != ["subject"] or _normalise(" ".join(parser.title_chunks)) != expected_subject:
        errors.append("email must contain exactly one approved document title")
    allowed_extra = {_normalise(value) for value in _STRUCTURAL_TEXT | {str(context["brief"].get("brand", ""))}}
    authored_extra = [_normalise(value) for value in parser.extra_authored if _normalise(value)]
    if any(value not in allowed_extra for value in authored_extra):
        errors.append("unapproved authored text")
    if any(authored_extra.count(value) > 1 for value in set(authored_extra)):
        errors.append("duplicated structural text")
    if parser.anchors != [context["brief"].get("call_to_action_url")]:
        errors.append("email must contain exactly one approved CTA anchor")
    if not parser.has_table:
        errors.append("email must use a table layout")
    if len(parser.images) != 1:
        errors.append("email must contain exactly one embedded logo")
    elif _data_uri_identity(parser.images[0]) != (context["logo"].mime_type, context["logo"].sha256):
        errors.append("embedded email logo does not match the selected bytes")
    if parser.image_alts != [f"{context['brief'].get('brand', '')} logo"]:
        errors.append("email logo must have meaningful brand alt text")
    content_text = _normalise(" ".join([*roles.values(), *authored_extra, *parser.attribute_text]))
    return {
        "roles": roles,
        "errors": sorted(set(errors)),
        "assets": parser.images,
        "visible_text": _visible_html(source),
        "content_text": content_text,
    }


def _visible_html(source: str) -> str:
    parser = _HTMLInspector(approved_cta=None)
    parser.feed(source)
    chunks = [*parser.extra_visible]
    for role, values in parser.roles.items():
        if role not in {"preheader", "subject"}:
            chunks.extend(values)
    return _normalise(" ".join(chunks))


def _unsafe_svg_tree(root: ElementTree.Element) -> bool:
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].casefold()
        if tag in _ACTIVE_SVG:
            return True
        for raw_name, raw_value in element.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].casefold()
            value = raw_value.strip()
            if name.startswith("on") or (
                name in {"href", "src"}
                and not value.startswith("#")
                and not (tag == "image" and value.startswith("data:image/"))
            ):
                return True
            if name == "style" and _unsafe_css(value):
                return True
        if tag == "style" and _unsafe_css(element.text or ""):
            return True
    return False


def inspect_svg(payload: bytes, context: dict[str, Any], dimensions: tuple[int, int]) -> dict[str, Any]:
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", payload, re.I):
        raise RenderContractError("invalid_rendered_svg", "Rendered banner may not declare a DTD or entity.")
    try:
        root = ElementTree.fromstring(payload.decode("utf-8"))
    except (UnicodeDecodeError, ElementTree.ParseError) as exc:
        raise RenderContractError("invalid_rendered_svg", "Rendered banner SVG is malformed.") from exc
    if root.tag.rsplit("}", 1)[-1].casefold() != "svg" or _unsafe_svg_tree(root):
        raise RenderContractError("invalid_rendered_svg", "Rendered banner contains unsafe SVG content.")
    width, height = dimensions
    if (
        root.get("width") != str(width)
        or root.get("height") != str(height)
        or root.get("viewBox") != f"0 0 {width} {height}"
    ):
        raise RenderContractError("dimension_mismatch", "Rendered banner geometry does not match the brief.")
    role_chunks: dict[str, list[str]] = {}
    role_nodes: dict[str, list[ElementTree.Element]] = {}
    role_counts: dict[str, int] = {}
    images: list[str] = []
    errors: list[str] = []
    titles: list[str] = []
    descriptions: list[str] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].casefold()
        role = element.get("data-role")
        if role:
            role_chunks.setdefault(role, []).append(" ".join(element.itertext()))
            role_nodes.setdefault(role, []).append(element)
            role_counts[role] = role_counts.get(role, 0) + 1
        elif tag == "text" and _normalise(" ".join(element.itertext())):
            errors.append("unapproved visible SVG text")
        if tag == "image":
            href = next((value for key, value in element.attrib.items() if key.rsplit("}", 1)[-1] == "href"), "")
            if not href.startswith("data:image/"):
                raise RenderContractError("unsafe_rendered_asset", "Rendered banner has an external image reference.")
            images.append(href)
        if tag == "title":
            titles.append(_normalise(" ".join(element.itertext())))
        elif tag == "desc":
            descriptions.append(_normalise(" ".join(element.itertext())))
    roles = {name: _normalise(" ".join(chunks)) for name, chunks in role_chunks.items()}
    expected = expected_roles(context, "banner")
    _canonical, _resolved_width, _resolved_height, profile = _parse_banner_dimension(f"{width}x{height}")
    expected_layout = banner_layout(profile, width, height, expected, context["typography"])
    expected_lines = expected_layout["lines"]
    expected_lengths = expected_layout["text_lengths"]
    for name, value in expected.items():
        if roles.get(name) != _normalise(value):
            errors.append(f"role mismatch: {name}")
        if role_counts.get(name) != len(expected_lines[name]):
            errors.append(f"role cardinality mismatch: {name}")
        nodes = role_nodes.get(name, [])
        if [node.get("textLength") for node in nodes] != expected_lengths[name] or any(
            node.get("lengthAdjust") != "spacing" for node in nodes
        ):
            errors.append(f"role text width contract mismatch: {name}")
    if set(roles) - set(expected) or set(role_counts) - set(expected):
        errors.append("unexpected rendered role")
    if len(images) != 1:
        errors.append("banner must contain exactly one embedded logo")
    elif _data_uri_identity(images[0]) != (context["logo"].mime_type, context["logo"].sha256):
        errors.append("embedded banner logo does not match the selected bytes")
    expected_title = _normalise(f"{context['brief'].get('brand', '')} campaign banner")
    if titles != [expected_title]:
        errors.append("banner must contain exactly one approved title")
    if descriptions != ["Draft for qualified MLR review"]:
        errors.append("banner must contain exactly one approved description")
    if root.get("role") != "img":
        errors.append("banner root must expose the image role")
    return {
        "roles": roles,
        "errors": sorted(set(errors)),
        "assets": images,
        "visible_text": _normalise(" ".join(root.itertext())),
    }


def _pdf_reader(payload: bytes):
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(payload), strict=True)
        if reader.is_encrypted:
            raise RenderContractError("encrypted_pdf", "Encrypted rendered PDFs are unsupported.")
        return reader
    except RenderContractError:
        raise
    except Exception as exc:
        raise RenderContractError("invalid_rendered_pdf", "Rendered poster PDF is malformed.") from exc


def inspect_pdf(payload: bytes, context: dict[str, Any], dimensions: tuple[float, float]) -> dict[str, Any]:
    reader = _pdf_reader(payload)
    if len(reader.pages) != 1:
        raise RenderContractError("poster_page_count", "Rendered poster must contain exactly one page.")
    page = reader.pages[0]
    width, height = dimensions
    if abs(float(page.mediabox.width) - width) > 0.01 or abs(float(page.mediabox.height) - height) > 0.01:
        raise RenderContractError("dimension_mismatch", "Rendered poster MediaBox does not match the brief.")
    text = _normalise(page.extract_text() or "")
    errors: list[str] = []
    value_roles: dict[str, list[str]] = {}
    for name, value in expected_roles(context, "poster").items():
        value_roles.setdefault(_normalise(value), []).append(name)
    for value, names in value_roles.items():
        if text.count(value) != 1:
            errors.append(f"text mismatch: {','.join(names)}")
    if text != _expected_poster_text(context):
        errors.append("poster contains missing, altered, or unapproved visible text")
    product_expected = isinstance(context["brand"].get("files"), dict) and "product.png" in context["brand"]["files"]
    xobjects = page.get("/Resources", {}).get("/XObject", {})
    image_count = sum(1 for value in xobjects.values() if value.get_object().get("/Subtype") == "/Image")
    if product_expected and image_count < 1:
        errors.append("poster product image is missing")
    if not product_expected and image_count:
        errors.append("poster contains an undeclared image")
    return {"text": text, "errors": sorted(set(errors)), "image_count": image_count}


def _expected_poster_text(context: dict[str, Any]) -> str:
    copy = context["copy"]
    legal = context["legal"]
    pieces = [
        str(context["brief"].get("brand", "")),
        "DRAFT - FOR QUALIFIED MLR REVIEW",
        copy["headline"]["text"],
    ]
    rendered_values = {_normalise(copy["headline"]["text"])}
    if copy.get("subhead"):
        pieces.append(copy["subhead"]["text"])
        rendered_values.add(_normalise(copy["subhead"]["text"]))
    for block in copy["body"]:
        pieces.append(block["text"])
        rendered_values.add(_normalise(block["text"]))
    for block in copy.get("bullet_points") or []:
        pieces.append(f"- {block['text']}")
        rendered_values.add(_normalise(block["text"]))
    pieces.append(copy["cta"]["text"])
    rendered_values.add(_normalise(copy["cta"]["text"]))
    pieces.append("IMPORTANT SAFETY INFORMATION")
    for value in legal.values():
        pieces.append(value)
        rendered_values.add(_normalise(value))
    for footnote in copy.get("footnotes") or []:
        value = footnote["text"] if isinstance(footnote, dict) else footnote
        normalised = _normalise(value)
        if normalised not in rendered_values:
            pieces.append(value)
            rendered_values.add(normalised)
    return _normalise(" ".join(pieces))


def extract_rendered_text(path: Path, format: str) -> str:
    """Extract bounded visible text from an actual HTML, SVG, or PDF file."""
    if format not in {"html", "svg", "pdf"}:
        raise RenderContractError("unsupported_rendered_format", "Rendered format must be html, svg, or pdf.")
    payload = _regular_bytes(path, limit=_PDF_LIMIT if format == "pdf" else _TEXT_LIMIT, code="invalid_rendered_file")
    if format == "html":
        try:
            source = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RenderContractError("invalid_rendered_html", "Rendered HTML must be UTF-8.") from exc
        if "<html" not in source.casefold():
            raise RenderContractError("format_mismatch", "Rendered file is not HTML.")
        return _visible_html(source)
    if format == "svg":
        if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", payload, re.I):
            raise RenderContractError("invalid_rendered_svg", "Rendered SVG may not declare a DTD or entity.")
        try:
            root = ElementTree.fromstring(payload.decode("utf-8"))
        except (UnicodeDecodeError, ElementTree.ParseError) as exc:
            raise RenderContractError("invalid_rendered_svg", "Rendered SVG is malformed.") from exc
        if root.tag.rsplit("}", 1)[-1].casefold() != "svg":
            raise RenderContractError("format_mismatch", "Rendered file is not SVG.")
        return _normalise(" ".join(root.itertext()))
    if not payload.startswith(b"%PDF-"):
        raise RenderContractError("format_mismatch", "Rendered file is not PDF.")
    reader = _pdf_reader(payload)
    if len(reader.pages) != 1:
        raise RenderContractError("poster_page_count", "Rendered PDF must contain exactly one page.")
    return _normalise(reader.pages[0].extract_text() or "")


def banner_layout(
    profile: str,
    width: int,
    height: int,
    roles: dict[str, str],
    typography: dict[str, Any],
) -> dict[str, Any]:
    """Build deterministic profile-specific line layouts and reject text overflow."""
    fit = {
        "horizontal": {
            "headline": (225.0, 11.0, 2, True),
            "sub_headline": (225.0, 6.8, 1, False),
            "safety": (365.0, 6.8, 3, False),
            "legal-isi": (365.0, 5.4, 7, False),
            "legal-pi_ref": (365.0, 5.4, 2, False),
            "cta": (112.0, 9.0, 1, True),
        },
        "rectangle": {
            "headline": (198.0, 13.0, 3, True),
            "sub_headline": (198.0, 8.0, 1, False),
            "safety": (198.0, 8.0, 4, False),
            "legal-isi": (264.0, 7.0, 7, False),
            "legal-pi_ref": (264.0, 7.0, 2, False),
            "cta": (94.0, 9.0, 1, True),
        },
        "skyscraper": {
            "headline": (128.0, 14.0, 5, True),
            "sub_headline": (128.0, 8.0, 2, False),
            "safety": (128.0, 8.3, 5, False),
            "legal-isi": (128.0, 7.0, 16, False),
            "legal-pi_ref": (128.0, 7.0, 5, False),
            "cta": (112.0, 9.0, 1, True),
        },
    }[profile]
    lines: dict[str, list[str]] = {}
    text_lengths: dict[str, list[str]] = {}
    for role, (available_width, font_size, maximum, bold) in fit.items():
        if role == "sub_headline" and role not in roles:
            continue
        value = roles.get(role, "")
        family_key = "heading_family" if role in {"headline", "cta"} else "body_family"
        wrapped = _wrap_banner_text(
            value,
            available_width,
            font_size,
            font_family=typography.get(family_key),
            bold=bold,
            role=role,
        )
        if len(wrapped) > maximum:
            raise RenderContractError("banner_text_overflow", f"Banner {role} does not fit the selected profile.")
        lines[role] = wrapped
        text_lengths[role] = [
            f"{min(available_width, _banner_text_width(line, font_size, font_family=typography.get(family_key), bold=bold)):.3f}"
            for line in wrapped
        ]
    if profile == "skyscraper" and lines.get("sub_headline"):
        subheadline_y = 124 + len(lines["headline"]) * 18 + 4
        subheadline_bottom = subheadline_y + (len(lines["sub_headline"]) - 1) * 11 + 4
        if subheadline_bottom > 222:
            raise RenderContractError(
                "banner_text_overflow", "Banner headline and subheadline overlap the safety safe zone."
            )
    return {
        "profile": profile,
        "width": width,
        "height": height,
        "lines": lines,
        "text_lengths": text_lengths,
    }


_FONT_METRICS = {
    "arial": "Helvetica",
    "helvetica": "Helvetica",
    "sans-serif": "Helvetica",
    "times": "Times-Roman",
    "times new roman": "Times-Roman",
    "serif": "Times-Roman",
    "courier": "Courier",
    "courier new": "Courier",
    "monospace": "Courier",
}


def _font_metric_names(family: object, *, bold: bool) -> tuple[tuple[str, ...], bool]:
    if isinstance(family, str):
        tokens = [token.strip().strip("'\"").casefold() for token in family.split(",") if token.strip()]
    else:
        tokens = []
    mapped = [_FONT_METRICS[token] for token in tokens if token in _FONT_METRICS]
    conservative = not tokens or len(mapped) != len(tokens)
    if conservative:
        mapped.extend(("Helvetica", "Times-Roman", "Courier"))
    if not mapped:
        mapped.extend(("Helvetica", "Times-Roman", "Courier"))
    if bold:
        bold_names = {"Helvetica": "Helvetica-Bold", "Times-Roman": "Times-Bold", "Courier": "Courier-Bold"}
        mapped = [bold_names[name] for name in mapped]
    return tuple(dict.fromkeys(mapped)), conservative


def _banner_text_width(value: str, font_size: float, *, font_family: object, bold: bool) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    font_names, conservative = _font_metric_names(font_family, bold=bold)
    measured = 0.0
    for character in value:
        if unicodedata.combining(character):
            continue
        if unicodedata.east_asian_width(character) in {"W", "F"}:
            measured += font_size
        else:
            widths = [stringWidth(character, font_name, font_size) for font_name in font_names]
            measured += max([font_size, *widths]) if conservative else max(widths)
    return measured * 1.06


def _wrap_banner_text(
    value: object,
    width: float,
    font_size: float,
    *,
    font_family: object,
    bold: bool,
    role: str,
) -> list[str]:
    original = str(value)
    for _index in range(4):
        decoded = unescape(original)
        if decoded == original:
            break
        original = decoded
    else:
        if unescape(original) != original:
            raise RenderContractError(
                "unsupported_banner_glyph",
                f"Banner {role} contains excessively nested HTML entity encoding.",
            )
    if any(
        unicodedata.category(character).startswith(("C", "M")) or unicodedata.category(character) in {"Lm", "Sk"}
        for character in original
    ):
        raise RenderContractError(
            "unsupported_banner_glyph",
            f"Banner {role} contains unsupported modifier, control, or combining characters.",
        )
    normalised = unicodedata.normalize("NFC", original)
    normalised = " ".join(normalised.split())
    try:
        encoded = normalised.encode("cp1252")
    except UnicodeEncodeError as exc:
        raise RenderContractError(
            "unsupported_banner_glyph",
            f"Banner {role} contains glyphs outside the portable English/Western repertoire.",
        ) from exc
    if any(byte < 32 or byte == 127 for byte in encoded):
        raise RenderContractError(
            "unsupported_banner_glyph",
            f"Banner {role} contains unsupported control characters.",
        )
    words = normalised.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        if _banner_text_width(word, font_size, font_family=font_family, bold=bold) > width:
            raise RenderContractError("banner_text_overflow", f"Banner {role} contains an unbreakable word.")
        candidate = f"{current} {word}".strip()
        if current and _banner_text_width(candidate, font_size, font_family=font_family, bold=bold) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    return lines


def render_jinja(source: str, context: dict[str, Any], *, custom: bool) -> str:
    from jinja2 import StrictUndefined, nodes
    from jinja2.sandbox import SandboxedEnvironment

    if len(source.encode("utf-8")) > _TEMPLATE_LIMIT:
        raise RenderContractError("template_too_large", "Email template exceeds the supported size limit.")
    environment = SandboxedEnvironment(autoescape=True, undefined=StrictUndefined, loader=None)
    environment.globals.clear()
    environment.filters = {name: environment.filters[name] for name in ("default", "escape")}
    if custom:
        source_errors = _custom_template_source_errors(source)
        if source_errors:
            raise RenderContractError("unsafe_custom_template", "; ".join(source_errors))
        try:
            parsed = environment.parse(source)
        except Exception as exc:
            raise RenderContractError("invalid_custom_template", "Custom email template is not valid Jinja.") from exc
        forbidden = (
            nodes.Call,
            nodes.Getattr,
            nodes.Include,
            nodes.Import,
            nodes.FromImport,
            nodes.Extends,
            nodes.Macro,
        )
        if any(next(iter(parsed.find_all(kind)), None) is not None for kind in forbidden):
            raise RenderContractError(
                "unsafe_custom_template", "Custom email template uses a forbidden Jinja construct."
            )
    try:
        output = environment.from_string(source).render(**context)
    except Exception as exc:
        raise RenderContractError("invalid_custom_template", "Email template could not be rendered safely.") from exc
    if len(output.encode("utf-8")) > _OUTPUT_LIMIT:
        raise RenderContractError("rendered_output_too_large", "Rendered email exceeds the supported size limit.")
    return output


def _data_uri_identity(value: str) -> tuple[str, str] | None:
    try:
        header, encoded = value.split(",", 1)
        if not header.startswith("data:") or not header.endswith(";base64"):
            return None
        mime_type = header[5:-7]
        if mime_type not in {"image/svg+xml", "image/png", "image/jpeg"}:
            return None
        return mime_type, hashlib.sha256(base64.b64decode(encoded, validate=True)).hexdigest()
    except (ValueError, TypeError):
        return None


def load_email_template(path: object) -> tuple[str, dict[str, Any]]:
    if path is None:
        template_path = Path(str(files("open_pharma_plugins_campaign_studio") / "templates" / "email.html.j2"))
        payload = _regular_bytes(template_path, limit=_TEMPLATE_LIMIT, code="invalid_default_template")
        return payload.decode("utf-8"), {
            "kind": "default",
            "path": str(template_path.resolve()),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
    if not isinstance(path, str) or not path:
        raise RenderContractError("invalid_custom_template", "Custom template path must be a non-empty string.")
    template_path = Path(path).expanduser()
    payload, identity = _regular_snapshot(template_path, limit=_TEMPLATE_LIMIT, code="invalid_custom_template")
    try:
        source = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RenderContractError("invalid_custom_template", "Custom template must be UTF-8.") from exc
    return source, {
        "kind": "custom",
        "path": str(template_path.resolve(strict=True)),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "identity": {"device": identity[0], "inode": identity[1], "mode": identity[2]},
    }


def prohibited_errors(text: str, brief: dict[str, Any]) -> list[str]:
    from ._claim_engine import load_policy_rules

    rules = load_policy_rules(str(brief.get("policy_jurisdiction", "FDA")))
    patterns = list(rules.get("prohibited_patterns", []))
    if brief.get("mode") in {"non_promotional", "disease_awareness"}:
        patterns.extend(rules.get("non_promotional_prohibited", []))
    return [
        str(item.get("reason", "prohibited language")) for item in patterns if re.search(item["pattern"], text, re.I)
    ]


def _recorded_email_provenance(campaign_brief_id: str) -> dict[str, Any]:
    envelope = _strict_artifact(campaign_brief_id, "render-provenance-email.json")
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"campaign_brief_id", "channel", "template"}
        or envelope.get("campaign_brief_id") != campaign_brief_id
        or envelope.get("channel") != "email"
        or not isinstance(envelope.get("template"), dict)
    ):
        raise RenderContractError("invalid_email_provenance", "Email render provenance is malformed or mismatched.")
    provenance = envelope["template"]
    expected_keys = {"kind", "path", "sha256", "size"}
    if provenance.get("kind") == "custom":
        expected_keys.add("identity")
    identity = provenance.get("identity")
    if (
        set(provenance) != expected_keys
        or provenance.get("kind") not in {"default", "custom"}
        or not isinstance(provenance.get("path"), str)
        or not isinstance(provenance.get("sha256"), str)
        or not _SHA256.fullmatch(provenance["sha256"])
        or type(provenance.get("size")) is not int
        or provenance["size"] < 0
        or (
            provenance["kind"] == "custom"
            and (
                not isinstance(identity, dict)
                or set(identity) != {"device", "inode", "mode"}
                or any(type(identity.get(name)) is not int for name in ("device", "inode", "mode"))
            )
        )
    ):
        raise RenderContractError("invalid_email_provenance", "Email template provenance is malformed.")
    return provenance


def _canonical_rendered_payload(channel: str, context: dict[str, Any]) -> bytes:
    """Rebuild the exact approved renderer output for byte-identity validation."""
    if channel == "email":
        from .tools.render_email import _build_email_candidate

        provenance = _recorded_email_provenance(context["campaign_brief_id"])
        template = None if provenance["kind"] == "default" else provenance["path"]
        payload, _current = _build_email_candidate(context, template, expected_provenance=provenance)
        return payload
    if channel == "banner":
        from .tools.render_banner import _build_banner_candidate

        payload, _canonical, _width, _height, _profile = _build_banner_candidate(context)
        return payload
    if channel == "poster":
        from .tools.render_poster import _build_pdf

        _paper, width, height = resolve_poster_dimensions(context["brief"])
        return _build_pdf(context, width, height)
    raise RenderContractError("invalid_campaign_brief", "Campaign channel is unsupported.")


def _check_result(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check_name": name, "result": "pass" if passed else "fail", "detail": detail}


def validate_rendered_campaign(campaign_brief_id: str) -> dict[str, Any]:
    """Validate every actual brief output and persist a Task-3-compatible report."""
    from ._campaign_store import existing_artifact_path_result, save_validation_artifact
    from ._renderer import validation_gate_state, validation_input_fingerprint

    try:
        brief = _strict_artifact(campaign_brief_id, "campaign-brief.json")
        if not isinstance(brief, dict) or brief.get("campaign_brief_id") != campaign_brief_id:
            raise RenderContractError("invalid_campaign_brief", "Campaign brief is malformed or mismatched.")
        channels = brief.get("channels")
        if (
            not isinstance(channels, list)
            or not channels
            or len(channels) != len(set(channels))
            or any(channel not in _OUTPUTS for channel in channels)
        ):
            raise RenderContractError("invalid_campaign_brief", "Campaign brief channels are malformed.")
        gate = validation_gate_state(campaign_brief_id)
        if gate["status"] != "current":
            return error_response(
                RenderContractError("pre_render_validation_not_current", str(gate.get("reason") or "Validation stale."))
            )
        fingerprint = validation_input_fingerprint(campaign_brief_id, channels)
        channel_results: dict[str, Any] = {}
        outputs: list[dict[str, Any]] = []
        sealed_outputs: list[tuple[Path, bytes, tuple[int, int, int, int, int], int]] = []
        all_pass = True
        for channel in sorted(channels):
            context = load_render_context(campaign_brief_id, channel)
            filename, format = _OUTPUTS[channel]
            path, path_error = existing_artifact_path_result(campaign_brief_id, filename, section="outputs")
            output_pass = path_error is None and path is not None
            output_detail = "" if output_pass else f"Required {channel} output is missing or unsafe."
            contract_pass = False
            contract_detail = "Rendered contract was not evaluated because the output is unavailable."
            prohibited_pass = False
            prohibited_detail = "Prohibited-language inspection was not evaluated because the output is unavailable."
            if path_error or path is None:
                pass
            else:
                try:
                    limit = _PDF_LIMIT if format == "pdf" else _OUTPUT_LIMIT
                    payload, identity = _regular_snapshot(path, limit=limit, code="invalid_rendered_file")
                    if channel == "email":
                        provenance = _recorded_email_provenance(campaign_brief_id)
                        inspection = inspect_html(payload, context, custom=provenance["kind"] == "custom")
                    elif channel == "banner":
                        _canonical, width, height, _profile = resolve_banner_dimensions(brief)
                        inspection = inspect_svg(payload, context, (width, height))
                    else:
                        _paper, width, height = resolve_poster_dimensions(brief)
                        inspection = inspect_pdf(payload, context, (width, height))
                    if payload != _canonical_rendered_payload(channel, context):
                        inspection["errors"].append(
                            "rendered output does not match the deterministic approved renderer"
                        )
                    contract_pass = not inspection["errors"]
                    contract_detail = "; ".join(inspection["errors"])
                    inspected_text = (
                        inspection.get("content_text") or inspection.get("visible_text") or inspection.get("text", "")
                    )
                    prohibited = prohibited_errors(inspected_text, brief)
                    prohibited_pass = not prohibited
                    prohibited_detail = "; ".join(prohibited)
                    after, after_identity = _regular_snapshot(path, limit=limit, code="invalid_rendered_file")
                    if after != payload or after_identity != identity:
                        raise RenderContractError(
                            "rendered_output_changed", "Rendered output changed during validation."
                        )
                    outputs.append(
                        {
                            "path": str(path),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "size": len(payload),
                        }
                    )
                    sealed_outputs.append((path, payload, identity, limit))
                except RenderContractError as exc:
                    contract_pass = False
                    contract_detail = exc.message
                    prohibited_pass = False
                    prohibited_detail = "Prohibited-language inspection was not completed safely."
            checks = [
                _check_result("output_exists", output_pass, output_detail),
                _check_result("rendered_contract", contract_pass, contract_detail),
                _check_result("prohibited_language", prohibited_pass, prohibited_detail),
            ]
            channel_pass = all(check["result"] == "pass" for check in checks)
            all_pass = all_pass and channel_pass
            channel_results[channel] = {"channel": channel, "checks": checks, "overall_pass": channel_pass}
        ending_gate = validation_gate_state(campaign_brief_id)
        ending_fingerprint = validation_input_fingerprint(campaign_brief_id, channels)
        if ending_gate["status"] != "current" or ending_fingerprint != fingerprint:
            return error_response(
                RenderContractError(
                    "pre_render_validation_not_current",
                    "Campaign inputs changed during rendered-asset validation; prior evidence was preserved.",
                )
            )
        for path, payload, identity, limit in sealed_outputs:
            final_payload, final_identity = _regular_snapshot(path, limit=limit, code="invalid_rendered_file")
            if final_payload != payload or final_identity != identity:
                return error_response(
                    RenderContractError(
                        "rendered_output_changed",
                        "A rendered output changed during validation; prior evidence was preserved.",
                    )
                )
        template_sources: list[dict[str, Any]] = []
        if all_pass and "email" in channels:
            provenance = _recorded_email_provenance(campaign_brief_id)
            if provenance["kind"] == "custom":
                _source, current_provenance = load_email_template(provenance["path"])
                if current_provenance != provenance:
                    return error_response(
                        RenderContractError(
                            "email_template_changed",
                            "The custom email template changed during rendered-asset validation; prior evidence was preserved.",
                        )
                    )
                template_sources.append(provenance)
        report = {
            "campaign_brief_id": campaign_brief_id,
            "overall_pass": all_pass,
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "pre_render_input_fingerprint": fingerprint,
            "channel_results": channel_results,
            "outputs": sorted(outputs, key=lambda item: item["path"]),
            "template_sources": template_sources,
        }
        save_validation_artifact(campaign_brief_id, "rendered-assets.json", report)
        return report
    except RenderContractError as exc:
        return error_response(exc, fallback_code="rendered_validation_failed")
    except Exception as exc:
        return error_response(exc, fallback_code="rendered_validation_failed")
