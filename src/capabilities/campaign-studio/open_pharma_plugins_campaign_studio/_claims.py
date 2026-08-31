"""Strict reader for the persisted approved-claims artifact."""

from __future__ import annotations

from pydantic import ValidationError

from .models.claims import ApprovedClaim


class PersistedClaimsError(Exception):
    """Controlled input-artifact error suitable for a tool JSON response."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def load_persisted_claims(campaign_brief_id: str) -> list[dict]:
    """Load a complete, schema-valid, nonempty, uniquely identified claim list.

    Consumers must never treat a partially readable claims artifact as trusted.
    The public tool boundary catches ``PersistedClaimsError`` and converts it to
    normal MCP text content rather than exposing a storage or parsing exception.
    """
    from ._campaign_store import load_artifact

    try:
        raw_claims = load_artifact(campaign_brief_id, "approved-claims.json")
    except (OSError, UnicodeError, ValueError) as exc:
        raise PersistedClaimsError([f"Could not read approved claims artifact: {exc}"]) from exc

    claims, errors = validate_persisted_claims(raw_claims)
    if errors:
        raise PersistedClaimsError(errors)
    return claims


def validate_persisted_claims(raw_claims: object) -> tuple[list[dict], list[str]]:
    """Validate an already-read persisted claim list without filesystem access.

    Status and seal readers use this alongside the writer-facing loader so an
    invalid on-disk artifact cannot be made trustworthy by a shallower reader.
    """
    if not isinstance(raw_claims, list) or not raw_claims:
        return [], ["Approved claims artifact must contain a non-empty JSON array."]

    claims: list[dict] = []
    errors: list[str] = []
    seen_claim_ids: set[str] = set()
    for index, raw_claim in enumerate(raw_claims):
        try:
            claim = ApprovedClaim.model_validate(raw_claim)
        except ValidationError as exc:
            errors.append(f"Approved claims artifact item {index} is invalid: {exc}")
            continue
        if claim.claim_id in seen_claim_ids:
            errors.append(f"Approved claims artifact item {index} duplicates claim_id '{claim.claim_id}'.")
            continue
        seen_claim_ids.add(claim.claim_id)
        claims.append(claim.model_dump(mode="json"))

    return claims, errors
