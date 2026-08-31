# Campaign Studio 1.1 channel specifications

Campaign Studio 1.1 supports English (`en`) output for `email`, `banner`, and `poster`. Requested
channels are fixed by the campaign brief. Brief dimensions and CTA values are authoritative.

Every copy field shown as a block uses `{"text": "...", "claim_ids": ["c-001"]}`. Promotional
copy blocks cite applicable approved claim IDs. The brief's exact CTA and verbatim approved legal
text are the only uncited exceptions.

## Email

```json
{
  "subject": {"text": "Exact approved wording", "claim_ids": ["c-001"]},
  "preheader": {"text": "Exact approved wording", "claim_ids": ["c-006"]},
  "headline": {"text": "Exact approved wording", "claim_ids": ["c-002"]},
  "body": [{"text": "Exact approved wording", "claim_ids": ["c-003"]}],
  "cta": {"text": "Review the evidence", "claim_ids": []}
}
```

The self-contained HTML uses a table-based responsive layout, one hidden preheader, one HTTPS CTA
matching `call_to_action_url`, an embedded data-URI logo, and each required legal element exactly
once. It must contain no local asset URL or external resource dependency.

## Banner

```json
{
  "headline": {"text": "Exact approved wording", "claim_ids": ["c-004"]},
  "sub_headline": null,
  "safety": {"text": "Exact approved safety wording", "claim_ids": ["c-010"]},
  "cta": {"text": "Review evidence", "claim_ids": []}
}
```

A promotional efficacy banner requires a safety block. Headlines should be at most eight words and
CTAs at most three words. Supported exact sizes are `728x90`, `300x250`, `300x300`, and `160x600`.
The renderer embeds the logo, includes required legal content, and rejects text that cannot fit its
dimension-specific profile. A renderer `dimensions` argument is allowed only when identical to the
brief.

## Poster

```json
{
  "headline": {"text": "Exact approved wording", "claim_ids": ["c-002"]},
  "subhead": {"text": "Exact approved wording", "claim_ids": ["c-004"]},
  "body": [{"text": "Exact approved wording", "claim_ids": ["c-001"]}],
  "bullet_points": [{"text": "Exact approved wording", "claim_ids": ["c-007"]}],
  "cta": {"text": "Review the evidence", "claim_ids": []},
  "footnotes": null
}
```

Supported exact paper sizes are `A4`, `LETTER`, and `A3`. A `paper_size` argument is allowed only
when identical to the brief. The deterministic PDF is one page, uses the optional selected product
image, includes required legal content once, and fails rather than clipping overflowing content.

## Required validation sequence

1. `validate_claims_and_fair_balance` must pass every requested channel.
2. Render each requested channel with its production renderer.
3. `validate_rendered_assets` reads the actual HTML, SVG, and PDF; checks expected copy, legal text,
   links, dimensions, embedded assets, prohibited language, and one-page poster output; and seals
   each file's SHA-256.
4. If any source, policy, template, copy, or rendered byte changes, repeat the affected validation
   and rendering stages.
5. Only current passing pre-render and rendered-file seals allow review or export.
