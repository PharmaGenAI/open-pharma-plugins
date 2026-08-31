"""Claim validation engine — exact governance, fair balance, and policy checks."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from importlib.resources import files
from pathlib import Path

from .models.claims import (
    PROMOTIONAL_CLAIM_CATEGORIES,
    SAFETY_CLAIM_CATEGORIES,
    canonical_claim_category,
)


def load_policy_rules(jurisdiction: str) -> dict:
    """Load jurisdiction-specific policy rules from rules.json."""
    all_rules = _load_policy_bundle()
    defaults = all_rules.get("_defaults", {})
    selected = all_rules.get(jurisdiction, all_rules.get("FDA", {}))
    return {**defaults, **selected}


def policy_metadata() -> dict:
    """Return policy provenance for persisted validation reports."""
    rules_path = _policy_path()
    bundle = _load_policy_bundle()
    metadata = bundle.get("_metadata", {})
    return {
        "version": str(metadata.get("version", "unversioned")),
        "hash": sha256(rules_path.read_bytes()).hexdigest(),
        "effective_date": metadata.get("effective_date"),
        "status": metadata.get("status"),
    }


def validate_claim_wording(statement: str, claim: dict) -> dict:
    """Validate one cited statement against one claim without fuzzy approval.

    Exact normalised canonical wording and explicit variants are the only automated
    approvals.  Token similarity is deliberately retained solely to explain why a
    human review is needed; neither it nor citations can promote altered wording.
    """
    canonical = str(claim.get("text", ""))
    candidates = [canonical, *[str(value) for value in claim.get("allowed_variants", []) if str(value).strip()]]
    normalized_statement = _normalize(statement)
    statement_quantities = _quantitative_sequence(statement)
    candidate_quantities = [(candidate, _quantitative_sequence(candidate)) for candidate in candidates]
    exact_candidates = [
        (candidate, quantities)
        for candidate, quantities in candidate_quantities
        if normalized_statement and normalized_statement == _normalize(candidate)
    ]
    matched_approved_text = next(
        (candidate for candidate, quantities in exact_candidates if statement_quantities == quantities),
        None,
    )
    if matched_approved_text is not None:
        return {
            "claim_id": claim.get("claim_id"),
            "status": "approved",
            "matched_claim_text": matched_approved_text,
            "similarity_score": 1.0,
            "deviation": None,
        }

    if exact_candidates:
        return {
            "claim_id": claim.get("claim_id"),
            "status": "rejected",
            "matched_claim_text": exact_candidates[0][0],
            "similarity_score": 1.0,
            "deviation": "Quantitative semantics differ from the approved claim wording.",
        }

    statement_polarity = _polarity_tokens(statement)
    candidate_polarities = [_polarity_tokens(candidate) for candidate in candidates]
    diagnostic = fuzzy_match(statement, [claim])
    if not any(statement_polarity == polarity for polarity in candidate_polarities):
        diagnostic.update(
            {
                "claim_id": claim.get("claim_id"),
                "status": "rejected",
                "matched_claim_text": canonical or None,
                "deviation": "Negation or polarity tokens differ from the approved claim.",
            }
        )
        return diagnostic

    if not any(statement_quantities == quantities for _, quantities in candidate_quantities):
        diagnostic.update(
            {
                "claim_id": claim.get("claim_id"),
                "status": "rejected",
                "matched_claim_text": canonical or None,
                "deviation": "Quantitative semantics differ from the approved claim.",
            }
        )
        return diagnostic

    if diagnostic["status"] == "not_found" and statement_quantities:
        diagnostic["status"] = "needs_review"
        diagnostic["deviation"] = "Quantitative semantics match, but wording is not an exact approved text."
    diagnostic.update(
        {
            "claim_id": claim.get("claim_id"),
            "status": "needs_review" if diagnostic["status"] != "not_found" else "not_found",
            "matched_claim_text": canonical or None,
            "deviation": diagnostic.get("deviation") or "Wording is not an exact approved claim or allowed variant.",
        }
    )
    return diagnostic


def claim_applicability_errors(claim: dict, brief: dict, channel: str | None) -> list[str]:
    """Return every deterministic governance reason a cited claim cannot be used."""
    errors: list[str] = []
    if str(claim.get("approval_status", "")).casefold() != "approved":
        errors.append("not_approved")
    if claim.get("restrictions") and str(claim["restrictions"]).strip():
        errors.append("restricted")
    effective_from = _parse_date(claim.get("effective_from"))
    expiry = _parse_date(claim.get("expiry"))
    if claim.get("effective_from") not in (None, "") and effective_from is None:
        errors.append("invalid_effective_from")
    if claim.get("expiry") not in (None, "") and expiry is None:
        errors.append("invalid_expiry")
    if effective_from and effective_from > date.today():
        errors.append("not_yet_effective")
    if expiry and expiry < date.today():
        errors.append("expired")

    country = str(brief.get("country", "")).strip()
    jurisdiction = str(brief.get("policy_jurisdiction", "")).strip()
    if claim.get("jurisdictions") and not _allowlist_matches(claim["jurisdictions"], {country, jurisdiction}):
        errors.append("jurisdiction_inapplicable")
    if claim.get("indications") and not _allowlist_matches(claim["indications"], {str(brief.get("indication", ""))}):
        errors.append("indication_inapplicable")
    if claim.get("audiences"):
        from ._inputs import _audience_matches

        if not _audience_matches(claim["audiences"], str(brief.get("target_segment", ""))):
            errors.append("audience_inapplicable")
    if channel is not None and claim.get("channels"):
        if not _allowlist_matches(claim["channels"], {channel}):
            errors.append("channel_inapplicable")
    return errors


def banner_safety_errors(copy_data: dict, claims_by_id: dict[str, dict], brief: dict) -> list[str]:
    """Require dedicated exact, applicable safety copy for promotional efficacy banners."""
    if brief.get("mode") != "promotional":
        return []
    cited_claim_ids = {
        claim_id
        for field in ("headline", "sub_headline", "safety", "cta")
        if isinstance(copy_data.get(field), dict)
        for claim_id in copy_data[field].get("claim_ids", [])
    }
    invalid_categories = sorted(
        claim_id
        for claim_id in cited_claim_ids
        if claim_id in claims_by_id and canonical_claim_category(claims_by_id[claim_id].get("category")) is None
    )
    if invalid_categories:
        return [f"Cited banner claims have invalid categories: {invalid_categories}."]
    if not any(
        canonical_claim_category(claims_by_id.get(claim_id, {}).get("category")) in PROMOTIONAL_CLAIM_CATEGORIES
        for claim_id in cited_claim_ids
    ):
        return []
    safety = copy_data.get("safety")
    if not isinstance(safety, dict) or not safety.get("text", "").strip():
        return ["Promotional efficacy banners require a dedicated banner.safety CopyBlock."]
    safety_claim_ids = safety.get("claim_ids", [])
    if not safety_claim_ids:
        return ["banner.safety must cite an approved safety or tolerability claim."]
    for claim_id in safety_claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is None:
            continue
        if canonical_claim_category(claim.get("category")) not in SAFETY_CLAIM_CATEGORIES:
            continue
        if claim_applicability_errors(claim, brief, "banner"):
            continue
        if validate_claim_wording(safety["text"], claim)["status"] == "approved":
            return []
    return ["banner.safety must exactly match an approved, current, applicable safety or tolerability claim."]


def fuzzy_match(statement: str, approved_claims: list[dict]) -> dict:
    """Match a statement against approved claims using token overlap.

    Returns the best match with similarity score, or a not_found result.
    """
    statement_tokens = _tokenize(statement)
    if not statement_tokens:
        return {
            "claim_id": None,
            "status": "not_found",
            "matched_claim_text": None,
            "similarity_score": 0.0,
            "deviation": "Empty statement",
        }

    best_score = 0.0
    best_claim: dict | None = None

    for claim in approved_claims:
        claim_tokens = _tokenize(claim.get("text", ""))
        if not claim_tokens:
            continue

        intersection = statement_tokens & claim_tokens
        union = statement_tokens | claim_tokens
        score = len(intersection) / len(union) if union else 0.0

        if score > best_score:
            best_score = score
            best_claim = claim

    if best_score >= 0.6:
        deviation = None
        if best_score < 1.0 and best_claim:
            claim_tokens = _tokenize(best_claim["text"])
            missing = claim_tokens - statement_tokens
            extra = statement_tokens - claim_tokens
            parts = []
            if missing:
                parts.append(f"missing terms: {', '.join(sorted(missing)[:5])}")
            if extra:
                parts.append(f"extra terms: {', '.join(sorted(extra)[:5])}")
            deviation = "; ".join(parts) if parts else None

        return {
            "claim_id": best_claim["claim_id"] if best_claim else None,
            "status": "needs_review",
            "matched_claim_text": best_claim["text"] if best_claim else None,
            "similarity_score": round(best_score, 3),
            "deviation": deviation,
        }

    return {
        "claim_id": best_claim["claim_id"] if best_claim else None,
        "status": "not_found",
        "matched_claim_text": best_claim["text"] if best_claim else None,
        "similarity_score": round(best_score, 3),
        "deviation": "No close match found in approved claims",
    }


def check_fair_balance(copy_blocks: list[dict], approved_claims: list[dict], min_ratio: float) -> dict:
    """Check that safety content accompanies efficacy claims.

    Returns a policy check result with the efficacy-to-safety ratio.
    """
    claim_id_to_category = {
        claim["claim_id"]: canonical_claim_category(claim.get("category")) for claim in approved_claims
    }

    efficacy_count = 0
    safety_count = 0

    for block in copy_blocks:
        cited_claim_ids = set(block.get("claim_ids", []))
        invalid_categories = sorted(
            claim_id
            for claim_id in cited_claim_ids
            if claim_id in claim_id_to_category and claim_id_to_category[claim_id] is None
        )
        if invalid_categories:
            return {
                "check_name": "fair_balance",
                "result": "fail",
                "detail": f"Cited claims have invalid categories: {invalid_categories}.",
            }
        categories = {claim_id_to_category.get(claim_id) for claim_id in cited_claim_ids}
        if categories.intersection(PROMOTIONAL_CLAIM_CATEGORIES):
            efficacy_count += 1
        if categories.intersection(SAFETY_CLAIM_CATEGORIES):
            safety_count += 1

    total = efficacy_count + safety_count
    if total == 0:
        return {
            "check_name": "fair_balance",
            "result": "pass",
            "detail": "No efficacy or safety claims found in copy.",
        }

    safety_ratio = safety_count / total if total > 0 else 0.0

    if safety_ratio >= min_ratio:
        return {
            "check_name": "fair_balance",
            "result": "pass",
            "detail": (
                f"Safety ratio: {safety_ratio:.0%} "
                f"({safety_count} safety / {total} total). "
                f"Meets minimum {min_ratio:.0%}."
            ),
        }

    return {
        "check_name": "fair_balance",
        "result": "fail",
        "detail": (
            f"Safety ratio: {safety_ratio:.0%} "
            f"({safety_count} safety / {total} total). "
            f"Below minimum {min_ratio:.0%}. "
            "Add more safety/tolerability content."
        ),
    }


def check_prohibited_language(text: str, patterns: list[dict]) -> list[dict]:
    """Check text against prohibited language patterns."""
    violations: list[dict] = []
    for rule in patterns:
        if re.search(rule["pattern"], text, re.IGNORECASE):
            violations.append(
                {
                    "check_name": "prohibited_language",
                    "result": "fail",
                    "detail": f'{rule["reason"]}. Found in: "{_excerpt(text, rule["pattern"])}"',
                }
            )
    return violations


def check_required_elements(legal_data: object, required: list[str]) -> list[dict]:
    """Check that required selected-kit legal inputs are available before rendering."""
    results: list[dict] = []
    for element in required:
        element_text = legal_data.get(element) if isinstance(legal_data, dict) else None
        if not _is_visible_string(element_text):
            results.append(
                {
                    "check_name": f"required_{element}",
                    "result": "fail",
                    "detail": (
                        f"Required legal input '{element}' is missing, non-string, blank, "
                        "or non-visible in the selected brand manifest."
                    ),
                }
            )
        else:
            results.append(
                {
                    "check_name": f"required_{element}",
                    "result": "pass",
                    "detail": f"Required legal input '{element}' is available for rendering.",
                }
            )

    return results


def _is_visible_string(value: object) -> bool:
    """Return whether a persisted legal value is safe visible text."""
    if not isinstance(value, str) or not value.strip():
        return False
    if any(unicodedata.category(char).startswith("C") for char in value):
        return False
    return any(unicodedata.category(char)[0] in {"L", "N", "P", "S"} for char in value)


def is_claim_citation_exempt(block_name: str, text: str, brief: dict, legal_data: dict) -> bool:
    """Only the configured CTA and verbatim approved legal text may be uncited."""
    normalized = _normalize(text)
    if not normalized:
        return True
    if block_name == "cta" and normalized == _normalize(brief.get("call_to_action", "")):
        return True
    approved_legal = {
        _normalize(value)
        for key, value in legal_data.items()
        if key != "jurisdictions" and isinstance(value, str) and value.strip()
    }
    return normalized in approved_legal


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text)).split()).casefold()


def _policy_path() -> Path:
    return Path(str(files("open_pharma_plugins_campaign_studio") / "policy" / "rules.json"))


def _load_policy_bundle() -> dict:
    return json.loads(_policy_path().read_text(encoding="utf-8"))


def _parse_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _allowlist_matches(allowlist: list[object], values: set[str]) -> bool:
    allowed = {_normalize(value) for value in allowlist if str(value).strip()}
    return bool(allowed & {_normalize(value) for value in values if value.strip()})


def _polarity_tokens(text: str) -> Counter[str]:
    normalized = unicodedata.normalize("NFC", str(text)).casefold()
    remaining = normalized
    for phrase in sorted(_QUANTITY_COMPARATOR_PHRASE_ALIASES, key=len, reverse=True):
        pattern = rf"\b{re.escape(phrase)}\b"
        remaining = re.sub(pattern, " ", remaining)
    tokens = _unicode_word_tokens(remaining)
    polarity = {
        "no",
        "not",
        "never",
        "none",
        "neither",
        "nor",
        "without",
        "cannot",
        "can't",
        "doesn't",
        "don't",
        "isn't",
        "aren't",
        "wasn't",
        "weren't",
        "won't",
        "increase",
        "increases",
        "increased",
        "higher",
        "greater",
        "decrease",
        "decreases",
        "decreased",
        "reduce",
        "reduces",
        "reduced",
        "lower",
        "less",
        "likely",
        "unlikely",
        "positive",
        "negative",
        "minimum",
        "maximum",
    }
    return Counter(token for token in tokens if token in polarity)


def _unicode_word_tokens(text: str) -> list[str]:
    """Tokenize Unicode words without exposing ASCII substrings inside them."""
    tokens: list[str] = []
    current: list[str] = []
    for index, character in enumerate(text):
        if _is_unicode_word_character(character):
            current.append(character)
            continue
        if (
            character in {"'", "\u2019"}
            and current
            and index + 1 < len(text)
            and _is_unicode_word_character(text[index + 1])
        ):
            current.append("'")
            continue
        if current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _is_unicode_word_character(character: str) -> bool:
    category = unicodedata.category(character)
    return category[0] in {"L", "M", "N"} or category in {"Pc", "Cf"}


_QUANTITY_COMPARATOR_ALIASES = (
    ("!=", "!="),
    ("<=", "<="),
    (">=", ">="),
    ("≠", "!="),
    ("≤", "<="),
    ("≥", ">="),
    ("<", "<"),
    (">", ">"),
    ("=", "="),
)
_QUANTITY_COMPARATOR_PHRASE_ALIASES = {
    "no less than": ">=",
    "no more than": "<=",
    "not less than": ">=",
    "not more than": "<=",
    "greater than": ">",
    "more than": ">",
    "fewer than": "<",
    "less than": "<",
    "at least": ">=",
    "at most": "<=",
    "up to": "<=",
    "above": ">",
    "below": "<",
    "over": ">",
    "under": "<",
}
_QUANTITY_SIGN_ALIASES = (
    ("+/-", "±"),
    ("±", "±"),
    ("−", "-"),
    ("+", "+"),
    ("-", "-"),
)
_QUANTITY_RANGE_ALIASES = (
    ("to", ".."),
    ("−", ".."),
    ("–", ".."),
    ("—", ".."),
    ("-", ".."),
)


def _operator_pattern(aliases: tuple[tuple[str, str], ...]) -> str:
    """Build an explicitly ordered regular-expression alternative."""
    return "|".join(re.escape(source) for source, _ in aliases)


_QUANTITY_COMPARATOR_PATTERN = (
    rf"(?:{_operator_pattern(_QUANTITY_COMPARATOR_ALIASES)}|(?<!\S)(?i:"
    + "|".join(re.escape(source) for source in sorted(_QUANTITY_COMPARATOR_PHRASE_ALIASES, key=len, reverse=True))
    + r")(?!\w))"
)
_QUANTITY_SIGN_PATTERN = _operator_pattern(_QUANTITY_SIGN_ALIASES)
_QUANTITY_RANGE_PATTERN = rf"(?:(?i:to)\b|{_operator_pattern(_QUANTITY_RANGE_ALIASES[1:])})"

# Bounded clinical/measurement vocabulary.  Symbols are intentionally
# case-sensitive: ``mg`` and ``Mg`` (or ``mJ`` and ``MJ``) are different
# quantities.  Duration words are the only case-insensitive unit family.
_QUANTITY_UNIT_SYMBOLS = (
    "mmHg",
    "mmol",
    "µmol",
    "μmol",
    "umol",
    "nmol",
    "mEq",
    "µEq",
    "μEq",
    "uEq",
    "mIU",
    "µIU",
    "μIU",
    "uIU",
    "kcal",
    "MHz",
    "GHz",
    "kHz",
    "bpm",
    "rpm",
    "mcg",
    "kg",
    "mg",
    "Mg",
    "µg",
    "μg",
    "ug",
    "ng",
    "pg",
    "lb",
    "lbs",
    "oz",
    "ML",
    "mL",
    "µL",
    "μL",
    "uL",
    "nL",
    "dL",
    "cL",
    "L",
    "l",
    "km",
    "cm",
    "mm",
    "µm",
    "μm",
    "um",
    "nm",
    "m",
    "mM",
    "µM",
    "μM",
    "uM",
    "nM",
    "pM",
    "M",
    "mol",
    "MJ",
    "mJ",
    "kJ",
    "J",
    "cal",
    "°C",
    "°F",
    "C",
    "F",
    "K",
    "IU",
    "U",
    "kPa",
    "MPa",
    "Pa",
    "bar",
    "Hz",
    "mV",
    "V",
    "mA",
    "A",
    "mW",
    "kW",
    "W",
    "ms",
    "µs",
    "μs",
    "us",
    "min",
    "hr",
    "hrs",
    "wk",
    "wks",
    "mo",
    "mos",
    "yr",
    "yrs",
    "s",
    "h",
    "d",
)
_DURATION_WORD_ALIASES = {
    "second": "s",
    "seconds": "s",
    "minute": "min",
    "minutes": "min",
    "hour": "h",
    "hours": "h",
    "day": "d",
    "days": "d",
    "week": "wk",
    "weeks": "wk",
    "month": "mo",
    "months": "mo",
    "year": "yr",
    "years": "yr",
}
_QUANTITY_SYMBOL_UNIT_PATTERN = (
    "(?:" + "|".join(re.escape(unit) for unit in sorted(_QUANTITY_UNIT_SYMBOLS, key=len, reverse=True)) + ")"
)
_QUANTITY_DURATION_WORD_PATTERN = (
    "(?i:" + "|".join(re.escape(unit) for unit in sorted(_DURATION_WORD_ALIASES, key=len, reverse=True)) + ")"
)
_QUANTITY_UNIT_BASE = rf"(?:{_QUANTITY_SYMBOL_UNIT_PATTERN}|{_QUANTITY_DURATION_WORD_PATTERN})"
_QUANTITY_EXPONENT = r"(?:\^(?:[+\-−]?[123])|[+\-−][123]|[⁺⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)"
_QUANTITY_UNIT_ATOM = rf"{_QUANTITY_UNIT_BASE}(?:{_QUANTITY_EXPONENT})?"
_QUANTITY_INVERSE_ATOM = rf"{_QUANTITY_UNIT_BASE}{_QUANTITY_EXPONENT}"
_QUANTITY_UNIT_SEPARATOR = r"(?:\s*(?:/|·)\s*|\s+(?i:per)\s+)"
_QUANTITY_UNIT_PATTERN = (
    rf"(?:%|{_QUANTITY_UNIT_ATOM}(?:{_QUANTITY_UNIT_SEPARATOR}{_QUANTITY_UNIT_ATOM})*"
    rf"(?:\s+{_QUANTITY_INVERSE_ATOM})*)(?![^\W\d_])"
)
_QUANTITY_NUMBER_PATTERN = r"(?:\d+(?:[.,]\d+)?|[.,]\d+)"
_QUANTITY_EXPRESSION = re.compile(
    rf"(?P<comparator>{_QUANTITY_COMPARATOR_PATTERN})?\s*"
    rf"(?P<sign>{_QUANTITY_SIGN_PATTERN})?\s*"
    rf"(?P<number>{_QUANTITY_NUMBER_PATTERN})"
    rf"(?:\s*(?P<unit>{_QUANTITY_UNIT_PATTERN}))?"
    rf"(?:\s*(?P<range>{_QUANTITY_RANGE_PATTERN})\s*"
    rf"(?P<second_sign>{_QUANTITY_SIGN_PATTERN})?\s*"
    rf"(?P<second_number>{_QUANTITY_NUMBER_PATTERN})"
    rf"(?:\s*(?P<second_unit>{_QUANTITY_UNIT_PATTERN}))?"
    r")?"
)


def _quantitative_sequence(text: str) -> list[str]:
    """Preserve ordered operator, sign, range, number, and unit semantics."""
    normalized = unicodedata.normalize("NFC", str(text))
    comparator_aliases = dict(_QUANTITY_COMPARATOR_ALIASES)
    sign_aliases = dict(_QUANTITY_SIGN_ALIASES)
    signatures: list[str] = []
    for match in _QUANTITY_EXPRESSION.finditer(normalized):
        raw_comparator = match.group("comparator") or ""
        if raw_comparator.casefold() in {"less than", "more than"} and re.search(
            r"(?i)(?:no|not)\s+\Z", normalized[: match.start("comparator")]
        ):
            raw_comparator = ""
        comparator = comparator_aliases.get(
            raw_comparator,
            _QUANTITY_COMPARATOR_PHRASE_ALIASES.get(raw_comparator.casefold(), ""),
        )
        sign = sign_aliases.get(match.group("sign") or "", "")
        number = _canonical_number(match.group("number"))
        unit = _canonical_unit(match.group("unit") or "")
        second_number = match.group("second_number")
        second_sign = sign_aliases.get(match.group("second_sign") or "", "")
        second_unit = _canonical_unit(match.group("second_unit") or "")
        if second_number is not None:
            second_number = _canonical_number(second_number)
            shared_unit = unit or second_unit
            unit = unit or shared_unit
            second_unit = second_unit or shared_unit
        signature = f"cmp={comparator}|sign={sign}|value={number}|unit={unit}"
        if second_number is not None:
            signature += f"..sign={second_sign}|value={second_number}|unit={second_unit}"
        signatures.append(signature)
    return signatures


def _canonical_number(value: str) -> str:
    """Normalize equivalent decimal spellings while retaining exact value."""
    decimal_value = value.replace(",", ".")
    try:
        number = Decimal(decimal_value)
    except InvalidOperation:
        return decimal_value
    if number == 0:
        return "0"
    return format(number.normalize(), "f")


def _canonical_unit(value: str) -> str:
    """Normalize only explicit unit/operator aliases without case folding SI symbols."""
    if not value:
        return ""
    unit = unicodedata.normalize("NFC", value).replace("µ", "μ")
    unit = re.sub(
        _QUANTITY_DURATION_WORD_PATTERN,
        lambda match: _DURATION_WORD_ALIASES[match.group(0).casefold()],
        unit,
    )
    unit = re.sub(r"(?i:\bper\b)", "/", unit)
    unit = unit.translate(str.maketrans({"−": "-", "⁻": "-", "⁺": "+"}))
    unit = unit.translate(str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789"))
    unit = unit.replace("^", "")
    unit = re.sub(r"\s*([/·])\s*", r"\1", unit)
    unit = re.sub(r"\s+([^\s/·]+)-1(?=$|\s)", r"/\1", unit)
    return re.sub(r"\s+", "·", unit)


def _tokenize(text: str) -> set[str]:
    """Lowercase tokenization with stop-word removal."""
    stop_words = {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "was",
        "were",
        "are",
        "been",
        "be",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "shall",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "as",
        "not",
        "no",
    }
    normalized = unicodedata.normalize("NFC", text).replace("−", "-").replace("–", "-").replace("—", "-")
    tokens = set(re.findall(r"\b[a-z0-9][a-z0-9.%<>=-]*\b", normalized.lower()))
    return tokens - stop_words


def _excerpt(text: str, pattern: str) -> str:
    """Extract a short excerpt around the first match."""
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return ""
    start = max(0, m.start() - 20)
    end = min(len(text), m.end() + 20)
    return text[start:end].strip()
