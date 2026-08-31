# Campaign Studio 1.1 output schema

Pydantic models live in `open_pharma_plugins_campaign_studio/models/`. Examples below are fictional
draft data for qualified review, never approved promotional material.

## Campaign brief

`create_campaign_brief` returns `campaign_brief_id`, `campaign_name`, `brand`, `mode`, `channels`,
the absolute `brief_path`, and recorded `assumptions`. The persisted brief has this shape:

```json
{
  "campaign_brief_id": "fictional_oncorix_review_a1b2c3d4",
  "campaign_name": "Fictional ONCORIX evidence review",
  "country": "US",
  "policy_jurisdiction": "FDA",
  "mode": "promotional",
  "brand": "ONCORIX",
  "indication": "Fictional approved indication",
  "lifecycle_stage": "growth",
  "target_segment": "oncologists",
  "behavioral_objective": "Review the fictional approved evidence",
  "educational_objective": null,
  "desired_kpi": ["qualified_review"],
  "approved_claims_path": "/absolute/path/to/approved-claims.json",
  "demo_mode": false,
  "call_to_action": "Review the evidence",
  "call_to_action_url": "https://example.test/evidence",
  "channels": ["email", "banner", "poster"],
  "asset_dimensions": {"banner": "300x250", "poster": "A4"},
  "brand_kit_path": "/absolute/path/to/brand-kit",
  "language": "en",
  "localisation_notes": null,
  "required_safety_content": ["isi"],
  "required_legal_content": ["pi_ref", "reporting_statement"],
  "delivery_constraints": null,
  "approval_workflow": "mlr_standard",
  "generated_at": "2026-08-30T00:00:00+00:00"
}
```

`country` is ISO 3166-1 alpha-2. Jurisdictions are `FDA`, `EMA`, `MHRA`, `HSA`, `PMDA`, or `TGA`;
modes are `promotional`, `non_promotional`, or `disease_awareness`; channels are `email`, `banner`,
or `poster`; language is exactly `en`; and the CTA URL is HTTPS.

## Preflight result

`preflight_campaign_inputs` always returns ordinary JSON. Important fields are:

| Field | Type | Meaning |
|---|---|---|
| `campaign_brief_id` | string | Existing brief identity |
| `ready` | boolean | True only when the complete candidate set was activated |
| `demo_mode` | boolean | True when bundled fictional input is active/candidate |
| `claims` | array | Applicable approved claim records |
| `total_claims` | integer | Parsed source count |
| `applicable_claim_count` | integer | Claims available to generation |
| `excluded_claim_count` | integer | Claims excluded with reasons |
| `exclusions` | array | `claim_id` plus stable `reason` |
| `claims_path`, `brand_kit_path` | string or null | Selected source paths |
| `brand_manifest` | object | Selected files, values, resolved paths, hashes, and sizes |
| `provenance_path` | string or null | Absolute persisted provenance path |
| `hashes` | object | Exact source hashes |
| `active_inputs`, `candidate_inputs` | object | No-ambiguity active/candidate state |
| `warnings`, `errors` | array | Diagnostics; any error means stop |

## Approved claim

```json
{
  "claim_id": "c-001",
  "text": "Exact fictional approved wording.",
  "category": "efficacy",
  "source_document": "Fictional approved message source",
  "source_reference": "Section 2, Table 1",
  "approval_status": "approved",
  "effective_from": "2026-01-01",
  "expiry": null,
  "jurisdictions": ["US", "FDA"],
  "indications": ["Fictional approved indication"],
  "audiences": ["oncologists"],
  "channels": ["email", "banner", "poster"],
  "allowed_variants": [],
  "restrictions": null
}
```

See [input contracts](input-contracts.md) and [claim governance](claim-governance.md) for the exact
schema, filtering, wording, and applicability rules.

## Audience journey

```json
{
  "campaign_brief_id": "fictional_oncorix_review_a1b2c3d4",
  "target_segment": "oncologists",
  "stages": [
    {
      "stage": "aware",
      "objective": "Review the primary endpoint",
      "key_messages": ["c-001"],
      "channels": ["email", "banner", "poster"],
      "content_type": "promotional",
      "kpi": "qualified evidence review"
    }
  ],
  "generated_at": "2026-08-30T00:00:00+00:00"
}
```

Journeys contain 3–6 unique ordered stages. Every `key_messages` ID is applicable and every channel
belongs to the brief.

## Message architecture

```json
{
  "campaign_brief_id": "fictional_oncorix_review_a1b2c3d4",
  "brand": "ONCORIX",
  "indication": "Fictional approved indication",
  "message_tiers": [
    {
      "tier": "primary",
      "message": "Exact fictional approved wording.",
      "claim_ids": ["c-001"],
      "audience_stage": "aware",
      "rationale": "Primary endpoint"
    }
  ],
  "fair_balance_statement": "Exact fictional approved safety wording.",
  "fair_balance_sources": [
    {
      "document_id": "c-006",
      "document_name": "Fictional approved message source",
      "page_number": null,
      "excerpt": "Section 4, Safety"
    }
  ],
  "generated_at": "2026-08-30T00:00:00+00:00"
}
```

The current illustrative policy requires one primary, 1–3 secondary, and 1–5 supporting messages.
Every message and fair-balance source is grounded in an applicable claim.

## Channel copy

The persisted envelope is:

```json
{
  "campaign_brief_id": "fictional_oncorix_review_a1b2c3d4",
  "channel": "email",
  "copy": {
    "subject": {"text": "Exact fictional approved wording.", "claim_ids": ["c-001"]},
    "preheader": {"text": "Exact fictional approved safety wording.", "claim_ids": ["c-006"]},
    "headline": {"text": "Exact fictional approved wording.", "claim_ids": ["c-002"]},
    "body": [{"text": "Exact fictional approved wording.", "claim_ids": ["c-003"]}],
    "cta": {"text": "Review the evidence", "claim_ids": []}
  },
  "generated_at": "2026-08-30T00:00:00+00:00"
}
```

See [channel specifications](channel-specifications.md) for email, banner, and poster copy shapes.
`generate_channel_copy` returns the campaign ID, channel, total block count, and warnings.

## Pre-render validation report

`validate_claims_and_fair_balance` persists `validation/policy-checks.json`:

```json
{
  "campaign_brief_id": "fictional_oncorix_review_a1b2c3d4",
  "channels_validated": ["email", "banner", "poster"],
  "claims_checked": [],
  "policy_checks": [],
  "channel_results": {
    "email": {
      "channel": "email",
      "copy_exists": true,
      "claims_checked": [
        {
          "claim_id": "c-001",
          "declared_claim_id": "c-001",
          "statement": "Exact fictional approved wording.",
          "status": "approved",
          "matched_claim_text": "Exact fictional approved wording.",
          "similarity_score": 1.0,
          "deviation": null
        }
      ],
      "policy_checks": [
        {"check_name": "fair_balance", "result": "pass", "detail": "Channel meets policy."}
      ],
      "overall_pass": true
    }
  },
  "policy_version": "campaign-studio-illustrative-1.1",
  "policy_hash": "64-lowercase-hex",
  "overall_pass": true,
  "input_fingerprint": "64-lowercase-hex",
  "generated_at": "2026-08-30T00:00:00+00:00"
}
```

Claim statuses are `approved`, `needs_review`, `rejected`, or `not_found`. `overall_pass` is true
only when every channel passes. The fingerprint binds current briefs, sources, brand files, policy,
templates, copy, and Campaign Studio version.

## Renderer results

Successful renderers return `campaign_brief_id`, `channel`, an absolute `file_path`, `format`, and
`editable`. Banner also returns canonical `dimensions` and `profile`; poster returns `paper_size`;
email returns template provenance. Outputs are `outputs/email.html`, `outputs/banner.svg`, and
`outputs/poster.pdf`.

## Rendered-assets report

`validate_rendered_assets` persists `validation/rendered-assets.json` and returns:

```json
{
  "campaign_brief_id": "fictional_oncorix_review_a1b2c3d4",
  "overall_pass": true,
  "validated_at": "2026-08-30T00:00:00+00:00",
  "pre_render_input_fingerprint": "64-lowercase-hex",
  "channel_results": {
    "email": {
      "channel": "email",
      "checks": [
        {"check_name": "output_exists", "result": "pass", "detail": ""},
        {"check_name": "rendered_contract", "result": "pass", "detail": ""},
        {"check_name": "prohibited_language", "result": "pass", "detail": ""}
      ],
      "overall_pass": true
    }
  },
  "outputs": [
    {"path": "/absolute/store/campaigns/fictional_oncorix_review_a1b2c3d4/outputs/email.html", "sha256": "64-lowercase-hex", "size": 1234}
  ],
  "template_sources": []
}
```

The output set exactly matches requested channels. Review/export rejects missing, stale, changed,
malformed, invented, duplicated, or reordered validation evidence.

## Campaign status

`get_campaign_status` is read-only. It returns brief metadata, `demo_mode`, optional
`demo_provenance_disclosure`, safe provenance, absolute `artifact_paths`, per-artifact semantic
diagnostics, rendered paths/errors, ordered `completed_steps` and `missing_steps`, pre-render and
rendered-validation freshness, `review_outputs` and `package_export` freshness, and an exact
`next_step` object such as
`{"tool": "render_email", "channel": "email"}`. Callers resume from `next_step`; filenames alone
do not prove readiness. After rendered validation, status advances through `render_mlr_review` and
`export_mlr_package` before reporting a terminal null tool.

## Review and export

`render_mlr_review` returns:

```json
{
  "campaign_brief_id": "fictional_oncorix_review_a1b2c3d4",
  "draft": true,
  "demo_mode": true,
  "qualified_mlr_review_required": true,
  "completeness": {"required": 14, "present": 14, "missing": 0, "claim_rows": 8, "channels": 3},
  "outputs": [
    {
      "path": "outputs/mlr-review-summary.md",
      "absolute_path": "/store/campaigns/fictional_oncorix_review_a1b2c3d4/outputs/mlr-review-summary.md",
      "sha256": "64-lowercase-hex",
      "size": 4096
    }
  ]
}
```

The second output is `outputs/mlr-review.html`. Both are canonical, self-contained review
representations with complete claim/source, validation, provenance, and integrity evidence.

`export_mlr_package` adds `outputs/package-manifest.json` and a deterministic
`{campaign_brief_id}-mlr-{package_digest}.zip`. Its result includes absolute manifest/archive paths,
SHA-256, byte sizes, the lowercase package digest, and an optional copied archive path. Callers may
choose only `destination_dir`, never the filename. Manifest paths and ZIP members are relative; the
archive excludes itself and verifies every member hash and size.

## Storage layout

```text
campaigns/{campaign_brief_id}/
├── campaign-brief.json
├── input-provenance.json
├── approved-claims.json
├── brand-components.json
├── audience-journey.json
├── message-architecture.json
├── copy-{channel}.json
├── validation/
│   ├── claim-map.json
│   ├── policy-checks.json
│   ├── source-evidence.json
│   └── rendered-assets.json
└── outputs/
    ├── email.html
    ├── banner.svg
    ├── poster.pdf
    ├── mlr-review-summary.md
    ├── mlr-review.html
    ├── package-manifest.json
    └── {campaign_brief_id}-mlr-{package_digest}.zip
```

This storage map is descriptive, not a resume API. Use `get_campaign_status` instead of inspecting
the directory. All outputs remain fictional/demo when demo mode is active and always remain drafts
until qualified review approves them.
