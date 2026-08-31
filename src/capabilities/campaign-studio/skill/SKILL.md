---
name: open-pharma-plugins-campaign-studio
description: |
  End-to-end pharmaceutical campaign creation: structured brief, audience
  journey, message architecture, channel copy with claim validation, asset
  rendering, and MLR review packaging. Use when: (1) the user asks to create
  a campaign, build marketing materials, or generate promotional content;
  (2) someone says "create a campaign", "build an email", "render a banner",
  "generate promotional assets", "prepare MLR submission", "campaign brief",
  "message architecture", "audience journey"; (3) the user wants to produce
  channel assets (email, banner, poster) for a pharmaceutical brand;
  (4) the user needs claim-validated promotional materials with source
  traceability for MLR review.
---

# Campaign Studio

Create end-to-end pharmaceutical campaign materials: structured briefs,
audience journeys, message architectures, channel copy, rendered assets
(email HTML, banner SVG, poster PDF) with claim validation and MLR review
packaging.

Every promotional statement traces back to an applicable approved claim.
Campaign Studio accepts approved claims only as UTF-8 JSON and brand kits as
directories with the documented files. It does not extract claims from other
formats. Version 1.1 supports English (`en`) output only.

Every output is a draft review aid. Automated validation is not medical,
legal, or regulatory approval. A qualified reviewer must approve the final
materials before distribution. Bundled inputs are fictional and are available
only when the operator explicitly selects `demo_mode=true`.

## Available MCP tools

| Tool | Purpose | Returns |
|---|---|---|
| `create_campaign_brief` | Persist interview data as a validated campaign brief | campaign_brief_id, path, assumptions |
| `get_campaign_status` | Read non-mutating workflow and validation status | Completed/missing steps, provenance, review/export freshness, next tool |
| `preflight_campaign_inputs` | Fail-closed resolve, validate, and persist claims and brand inputs | Readiness, exclusions, paths, hashes |
| `retrieve_approved_claims` | Load and filter approved claims for the campaign | List of ApprovedClaim objects |
| `retrieve_brand_components` | Load brand kit (logo, palette, typography, legal) | Brand manifest with paths and data |
| `generate_audience_journey` | Validate and persist an audience journey | Stage summary with validation results |
| `generate_message_architecture` | Validate and persist a tiered message hierarchy | Message tier counts, fair balance status |
| `generate_channel_copy` | Validate and persist structured copy for a channel | Block count, warnings |
| `validate_claims_and_fair_balance` | Run the compliance gate on all channel copy | Pass/fail with claim and policy details |
| `render_email` | Merge validated copy into email HTML | File path to email.html |
| `render_banner` | Merge validated copy into SVG banner | File path to banner.svg |
| `render_poster` | Build print-ready PDF poster via ReportLab | File path to poster.pdf |
| `validate_rendered_assets` | Inspect and seal every rendered channel file | Per-channel checks and output hashes |
| `package_mlr_submission` | Assemble the full MLR review package | Package directory, summary path |
| `render_mlr_review` | Render the canonical interactive MLR review | Markdown and HTML paths, hashes, sizes |
| `export_mlr_package` | Export a deterministic content-addressed MLR package | Manifest, ZIP, digest, optional copied path |

## Compliance

> **The agent must NEVER use claim language that is not in the approved
> claims set.** Every promotional statement must trace to a specific
> approved claim with its source document and reference. Using unapproved
> language in promotional materials is a serious regulatory violation.

Additional rules:

1. **Every promotional statement must reference approved `claim_ids`** that
   ground it. Only exact approved wording or an exact `allowed_variants` value
   can pass automatically. Fuzzy similarity is diagnostic only; changed
   numbers or negation are rejected.
2. **Never extrapolate beyond the approved claims.** Do not add efficacy
   data, safety information, or competitive comparisons not present in the
   claims set.
3. **Fair balance is channel scoped.** Every promotional claim-bearing channel
   must independently meet its configured safety and required-content checks.
   Safety in one channel cannot compensate for another channel.
4. **Promotional mode:** every efficacy statement needs a balancing safety
   statement. The message architecture must include a fair_balance_statement.
5. **Non-promotional / disease awareness mode:** no brand name in headlines,
   no comparative claims, no promotional language.
6. **Never generate off-label claims.** If the brief's indication is X,
   only claims approved for X may be used.
7. **Never compare to competitors** unless such a comparison exists in the
   approved claims set.
8. **Renderers refuse to run** unless `validate_claims_and_fair_balance` passed
   for the current fingerprint. The fingerprint binds the brief, applicable
   claims, copy, live brand files, policy, default templates, and capability
   version. Any change requires re-validation.
9. **Jurisdiction-specific requirements:** the validator checks configured
   elements (ISI, PI reference, reporting statement) across the validated
   campaign copy. A passing automated check is not regulatory approval.
10. **Final files need their own gate.** Call `validate_rendered_assets` after
    every requested renderer. Review and export require current passing
    pre-render and rendered-file reports.
11. **Qualified human review is mandatory** before any asset is distributed.

## Workflow: Campaign Creation

Execute steps in order. Preserve every submitted path exactly; do not replace
it with a copied file, inferred path, or bundled fixture. Stop at the first
failed gate.

```
1. INTERVIEW
   Collect campaign parameters conversationally. Ask ONLY for information
   not yet provided. The required fields are:
     - country (ISO 3166-1 alpha-2)
     - policy_jurisdiction (FDA | EMA | MHRA | HSA | PMDA | TGA)
     - mode (promotional | non_promotional | disease_awareness)
     - brand, indication, lifecycle_stage
     - target_segment
     - behavioral_objective
     - desired_kpi (list)
     - call_to_action
     - call_to_action_url (required HTTPS URL)
     - channels (email, banner, poster)
   Optional fields with sensible defaults:
     - campaign_name (generate from brand + objective if not given)
     - educational_objective (null)
     - approved_claims_path (required unless demo_mode=true)
     - asset_dimensions (uses standard sizes if omitted)
     - brand_kit_path (required unless demo_mode=true)
     - demo_mode (false; bundled fixtures are permitted only when true)
     - language (en)
     - localisation_notes (null)
     - required_safety_content (default from brand kit)
     - required_legal_content (default from brand kit)
     - delivery_constraints (null)
     - approval_workflow (mlr_standard)
   Summarise any assumptions before calling create_campaign_brief.

2. INPUT PREFLIGHT
   Call preflight_campaign_inputs for the existing brief before generating
   campaign content. Supply claims_path and brand_kit_path, or set
   demo_mode=true to use bundled fictional fixtures. Stop if it is not ready.
   An explicit bad path never falls back to a fixture. Review every reported
   exclusion plus the resolved absolute paths, sizes, and SHA-256 hashes.

3. CLAIMS AND BRAND
   Use only the applicable claim IDs returned by preflight. Call
   retrieve_approved_claims or retrieve_brand_components only when the user
   asks to refresh the persisted selection. Both use the same fail-closed
   resolver; an omitted source requires either a source already stored in the
   brief or explicit demo mode.

4. AUDIENCE JOURNEY
   Generate an audience journey with 3-6 stages. For each stage:
     - stage: where the audience is (unaware → advocating)
     - objective: what should happen
     - key_messages: claim_ids to deliver at this stage
     - channels: which channels serve this stage
     - content_type: educational | promotional | reminder
     - kpi: measurable outcome
   Call generate_audience_journey to validate claim_ids exist and
   channels match the brief.

5. MESSAGE ARCHITECTURE
   Generate a three-tier message hierarchy:
     - primary: 1 message — the core campaign message
     - secondary: 1-3 messages — supporting evidence
     - supporting: 1-5 messages — additional context
   Each message must reference approved claim_ids.
   Include a fair_balance_statement with sources.
   Call generate_message_architecture to validate.

6. CHANNEL COPY
   For each channel in the brief, generate structured copy:

   Email (generate_channel_copy with channel="email"):
     {
       "subject": {"text": "...", "claim_ids": [...]},
       "preheader": {"text": "...", "claim_ids": [...]},
       "headline": {"text": "...", "claim_ids": [...]},
       "body": [{"text": "...", "claim_ids": [...]}],
       "cta": {"text": "...", "claim_ids": []}
     }

   Banner (generate_channel_copy with channel="banner"):
     {
       "headline": {"text": "≤8 words", "claim_ids": [...]},
       "sub_headline": {"text": "...", "claim_ids": [...]},
       "safety": {"text": "...", "claim_ids": [...]},
       "cta": {"text": "≤3 words", "claim_ids": []}
     }

   Poster (generate_channel_copy with channel="poster"):
     {
       "headline": {"text": "...", "claim_ids": [...]},
       "subhead": {"text": "...", "claim_ids": [...]},
       "body": [{"text": "...", "claim_ids": [...]}],
       "bullet_points": [{"text": "...", "claim_ids": [...]}],
       "cta": {"text": "...", "claim_ids": []},
       "footnotes": ["..."]
     }

   Use verbatim approved claim language wherever possible. Only the exact CTA
   and verbatim approved legal text may omit claim_ids in promotional mode.
   Every copy block with promotional content must have claim_ids.

7. VALIDATE
   Call validate_claims_and_fair_balance.
   If ANY check fails:
     - Review the failures
     - Revise the copy (re-run generate_channel_copy for affected channels)
     - Re-validate until overall_pass is true
   Do NOT proceed to rendering until validation passes.

8. RENDER
   Call the appropriate renderer for each channel in the brief:
     - render_email → email.html
     - render_banner → banner.svg (with dimensions from brief)
     - render_poster → poster.pdf (with paper_size from brief)
   Brief dimensions are authoritative; an override is accepted only when it
   is identical. Each renderer reads current persisted copy and the exact
   brand manifest selected during preflight.

9. VALIDATE RENDERED FILES
   Call validate_rendered_assets. Stop if any requested output is missing,
   changed, unsafe, or fails its channel contract. Re-render affected channels
   and repeat both gates when an input or output changes.

10. REVIEW AND EXPORT
   Call render_mlr_review for canonical Markdown and interactive HTML review
   outputs. Call export_mlr_package for package-manifest.json and the
   content-addressed ZIP. destination_dir, when supplied, is a directory; the
   tool chooses the archive filename. package_mlr_submission remains the
   compatibility route to the canonical review outputs and has the same gates.

11. HANDOFF
   Report demo status, the draft/review boundary, every returned absolute
   output path, SHA-256, byte size, package digest, and copied archive path if
   requested. Do not describe any output as approved, production-ready, sent,
   or published.
```

## Interview guidance

When collecting brief fields:

| Field | Ask if | Default if not provided |
|---|---|---|
| country | Always | — |
| policy_jurisdiction | Always | — |
| mode | Always | — |
| brand | Always | — |
| indication | Always | — |
| lifecycle_stage | Not mentioned | "growth" |
| target_segment | Always | — |
| behavioral_objective | Always | — |
| educational_objective | Mode is non_promotional or disease_awareness | null |
| desired_kpi | Always | — |
| call_to_action | Always | — |
| channels | Always | — |
| campaign_name | Not mentioned | Generate from brand + objective |
| asset_dimensions | User mentions specific sizes | Standard sizes per channel |
| brand_kit_path | User has custom brand kit | Required unless demo_mode=true |
| language | User mentions non-English | "en" |
| approval_workflow | User mentions expedited or special | "mlr_standard" |

## Resumability

If the user provides a `campaign_brief_id`, call `get_campaign_status` before
continuing. Trust its semantic diagnostics, validation freshness, demo
disclosure, and `next_step`; do not inspect the campaign directory directly or
infer readiness from filenames. Call the reported next tool. If status reports
failed, stale, malformed, or missing evidence, rebuild that stage and every
dependent gate.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `OPEN_PHARMA_CAMPAIGN_STORE_DIR` | `~/.open-pharma-plugins/campaign-studio/` | Root directory for campaign briefs, claims, and rendered assets |

## Composing with other capabilities

- Use the `field-training` capability's ingested documents as a source for
  approved claims only after exporting them into the Campaign Studio JSON
  claims schema and completing the required approval process.
- Use the host application's preview and web tools when available. External
  context is never an approved promotional claim unless it appears in the
  loaded approved-claims JSON.

## References

- [Input contracts](references/input-contracts.md)
- [Claim governance](references/claim-governance.md)
- [Channel specifications](references/channel-specifications.md)
- [Output schema](references/output-schema.md)
- [Fictional production-rendered email example](references/examples/email.html)
- [Fictional production-rendered MLR review example](references/examples/mlr-review.html)
