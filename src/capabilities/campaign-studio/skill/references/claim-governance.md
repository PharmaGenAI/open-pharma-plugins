# Campaign Studio 1.1 claim governance

Campaign Studio automates evidence checks; it does not make an approval decision. All campaign
outputs are drafts requiring qualified medical, legal, and regulatory review before use.

## Automated wording decision

- `approved` — after Unicode normalisation and whitespace folding, the statement exactly equals the
  claim's `text` or one exact `allowed_variants` entry, and the claim is currently applicable.
- `needs_review` — wording is similar but is not an approved exact value. Similarity is diagnostic
  only and can never approve a statement.
- `rejected` — numbers or negation/polarity differ from the approved wording.
- `not_found` — the cited claim ID is absent from the active applicable set.

Do not paraphrase, shorten, combine, translate, or add an implication to make copy fit a channel.
Obtain a separately approved exact variant or change the channel plan.

## Applicability

Applicability is checked at preflight and again during validation. A claim must be approved, within
its effective dates, free of unenforceable free-text restrictions, and allowed for the campaign's
jurisdiction/country, indication, audience, and channel. Empty optional allowlists mean no additional
restriction on that dimension.

Use only claim IDs returned as applicable by the current preflight. A later change to the brief,
claim source, policy, brand kit, templates, or copy invalidates dependent seals.

## Channel-scoped compliance

Every requested channel is evaluated independently for:

- copy existence and schema;
- declared claim IDs and exact wording;
- current claim applicability to that channel;
- prohibited language;
- required legal-input availability; and
- fair balance when promotional efficacy, positioning, or mechanism-of-action claims appear.

One safe channel cannot offset a failing channel. `overall_pass` is true only when every requested
channel passes. The bundled policy is explicitly illustrative engineering policy; qualified
reviewers own jurisdiction-specific legal correctness and maintenance.

## Source evidence

Every claim retains `source_document` and `source_reference`. Message architecture fair-balance
sources and all channel blocks point to exact active claim IDs. Review outputs retain the complete,
untruncated claim-to-source rows, validation provenance, and hashes.

## Draft boundary

Never describe a passing automated result as MLR-approved, production-ready, safe to distribute, or
an authoritative external decision. Campaign Studio does not send email, traffic advertising,
publish assets, or record an external approval. Handoff language must say:

> Draft review aid only. Qualified Medical, Legal, and Regulatory reviewers must assess and approve
> all content before any use. Automated checks are not an approval decision.

When `demo_mode=true`, also state that the product, claims, brand kit, and campaign are fictional
demonstration content and must not be used for live promotion.
