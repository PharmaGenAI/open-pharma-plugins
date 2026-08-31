# Campaign Studio 1.1 input contracts

Campaign Studio is path-first. Preserve the user's submitted paths exactly and pass them to
`preflight_campaign_inputs`; do not copy, rename, discover, or substitute source material.

## Required sequence

1. Call `create_campaign_brief` with `language="en"`, at least one KPI, at least one supported
   channel, and an HTTPS `call_to_action_url` with no credentials.
2. Call `preflight_campaign_inputs` with the returned `campaign_brief_id`.
3. Stop unless `ready` is exactly `true`. Review `errors`, `warnings`, `exclusions`, candidate and
   active inputs, resolved paths, file sizes, and SHA-256 hashes.
4. Generate content only from the applicable `claims`/claim IDs returned by preflight.

With `demo_mode=false`, both inputs resolve from the explicit `claims_path` and `brand_kit_path`, or
from those paths already stored in the brief. An explicit bad path always fails; it never falls back
to a fixture. With `demo_mode=true`, omitted paths resolve to bundled fictional fixtures and every
status, review, and export handoff must disclose demo use.

Preflight activates the input set only when claims and brand kit both pass. Failure does not create
partial downstream evidence or replace a previously active set.

## Approved claims JSON

The source is one UTF-8 JSON file whose root is a non-empty array. Unknown claim fields are rejected.
Each element has this shape:

```json
{
  "claim_id": "c-001",
  "text": "Exact approved wording.",
  "category": "efficacy",
  "source_document": "Approved message source",
  "source_reference": "Section 2, Table 1",
  "approval_status": "approved",
  "effective_from": "2026-01-01",
  "expiry": null,
  "jurisdictions": ["US", "FDA"],
  "indications": ["Exact approved indication"],
  "audiences": ["oncologists"],
  "channels": ["email", "banner", "poster"],
  "allowed_variants": ["Separately approved exact variant."],
  "restrictions": null
}
```

Required fields are `claim_id`, `text`, `category`, `source_document`, `source_reference`, and
`approval_status`. Categories are exactly `efficacy`, `positioning`, `moa`, `safety`,
`tolerability`, or `dosing`. Dates use ISO `YYYY-MM-DD`. Optional allowlists are arrays of non-blank
strings. Claim IDs must be unique.

Preflight excludes, with a reason, invalid-schema or duplicate claims; claims not marked approved;
claims that are not yet effective or are expired; claims with any non-blank legacy `restrictions`;
and claims inapplicable to the brief's jurisdiction/country, indication, audience, or requested
channels. At least one applicable approved claim must remain.

## Brand-kit directory

The directory contains these required regular files:

```text
brand-kit/
├── palette.json
├── typography.json
├── legal.json
└── logo.svg
```

`product.png` is optional. Files must be readable, bounded, non-symlink inputs. SVG is inspected and
must not contain active content or unsafe external references.

`palette.json` supplies six-digit hex values for `primary`, `secondary`, `accent`, `text`,
`text_light`, `background`, `background_alt`, `safety_highlight`, and `success`.

`typography.json` supplies non-blank `heading_family`, `body_family`, `heading_weight`,
`body_weight`, and `sizes` values for `h1`, `h2`, `h3`, `body`, `small`, and `legal`.

`legal.json` supplies non-blank `isi`, `pi_ref`, `copyright`, `reporting_statement`, and
`disclaimer`, plus a non-empty `jurisdictions` object. Each jurisdiction declares
`required_elements` and `fair_balance_required`.

The persisted brand manifest records the resolved absolute path, exact size, and SHA-256 of every
selected file. Renderers use that persisted selection; they do not independently re-resolve a kit.

## Failure boundary

Campaign Studio does not infer approval, translate approved wording, scrape sources, or extract
claims from documents or web pages. Resolve uncertainty outside the tool, complete the qualified
approval process, and provide a conforming JSON claim set before continuing.
