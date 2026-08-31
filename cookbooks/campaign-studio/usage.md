# Campaign Studio

Create campaign briefs, preflight exact approved-claims and brand-kit paths, validate every channel and rendered file, then export a content-addressed draft MLR review package.

Campaign Studio 1.1 supports English (`en`) output for email, banner, and poster. It accepts claims as UTF-8 JSON and a brand kit as a directory; it does not extract approved claims from other formats. Every output remains a draft until qualified medical, legal, and regulatory review is complete.

## Tools available

| Tool | Purpose |
|---|---|
| `create_campaign_brief` | Create or update the campaign brief |
| `get_campaign_status` | Read non-mutating workflow status, validation freshness, and the next required tool |
| `preflight_campaign_inputs` | Fail-closed validate and persist claims and brand-kit sources |
| `retrieve_approved_claims` | Load approved claims from JSON; fixtures require `demo_mode=true` |
| `retrieve_brand_components` | Load the logo, palette, typography, and legal content |
| `generate_audience_journey` | Validate and save an audience journey |
| `generate_message_architecture` | Validate and save claim-linked message tiers |
| `generate_channel_copy` | Validate and save email, banner, or poster copy |
| `validate_claims_and_fair_balance` | Run claim, fair-balance, and policy checks |
| `render_email` | Render validated email copy to HTML |
| `render_banner` | Render validated banner copy to SVG |
| `render_poster` | Render validated poster copy to PDF |
| `validate_rendered_assets` | Inspect and hash every rendered channel file |
| `package_mlr_submission` | Assemble artifacts and a human-readable review summary |
| `render_mlr_review` | Render the canonical Markdown and interactive HTML review |
| `export_mlr_package` | Export a deterministic manifest and content-addressed ZIP |

## Install and configure

```bash
pip install "open-pharma-plugins[campaign-studio]"
```

`OPEN_PHARMA_CAMPAIGN_STORE_DIR` defaults to `~/.open-pharma-plugins/campaign-studio`. Runtime artifacts are private where POSIX permissions are available.

## Production path-first prompt

Replace only the two absolute input paths and the campaign details. Preserve the paths exactly.

```text
Create an English Campaign Studio draft for qualified MLR review.

Approved claims JSON: /absolute/path/to/approved-claims.json
Brand kit directory: /absolute/path/to/brand-kit
Country: US
Policy jurisdiction: FDA
Mode: promotional
Brand: ExampleBrand
Indication: exact approved indication
Audience: oncologists
Objective: review the approved evidence
KPI: qualified review completion
CTA: Review the evidence
CTA URL: https://example.test/evidence
Channels: email, banner, poster
Banner size: 300x250
Poster size: A4

Use demo_mode=false. Create the brief, preflight both paths, and stop if any input is missing,
malformed, excluded, or inconsistent. Use only applicable approved claim IDs. Build the journey,
message architecture, and copy; validate every channel; render every requested asset; validate the
actual rendered files; render the MLR review; and export the content-addressed package. Return demo
status, draft status, and every absolute path, SHA-256, size, and package digest. Do not send or
publish anything.
```

The equivalent direct tool sequence is:

```text
create_campaign_brief campaign_name="ExampleBrand evidence review" country="US"
  policy_jurisdiction="FDA" mode="promotional" brand="ExampleBrand"
  indication="exact approved indication" target_segment="oncologists"
  behavioral_objective="review the approved evidence" desired_kpi=["reach"]
  call_to_action="Learn more" call_to_action_url="https://example.test/learn"
  channels=["email","banner","poster"] language="en" demo_mode=false
  approved_claims_path="/absolute/path/to/approved-claims.json"
  brand_kit_path="/absolute/path/to/brand-kit"
  asset_dimensions={"banner":"300x250","poster":"A4"}

preflight_campaign_inputs campaign_brief_id="<id>" claims_path="/absolute/path/to/approved-claims.json"
  brand_kit_path="/absolute/path/to/brand-kit" demo_mode=false
generate_audience_journey campaign_brief_id="<id>" journey="<JSON>"
generate_message_architecture campaign_brief_id="<id>" messages="<JSON>"
  fair_balance_statement="<exact approved safety statement>" fair_balance_sources="<JSON>"
generate_channel_copy campaign_brief_id="<id>" channel="email" copy_json="<JSON>"
generate_channel_copy campaign_brief_id="<id>" channel="banner" copy_json="<JSON>"
generate_channel_copy campaign_brief_id="<id>" channel="poster" copy_json="<JSON>"
validate_claims_and_fair_balance campaign_brief_id="<id>"
render_email campaign_brief_id="<id>"
render_banner campaign_brief_id="<id>"
render_poster campaign_brief_id="<id>"
validate_rendered_assets campaign_brief_id="<id>"
render_mlr_review campaign_brief_id="<id>" reviewer_notes="Draft for qualified MLR review"
export_mlr_package campaign_brief_id="<id>" destination_dir="/absolute/path/to/review-handoff"
```

## Explicit fictional demo prompt

```text
Create a fictional ONCORIX demonstration campaign using Campaign Studio with demo_mode=true.
Use all three channels in English. Run the full fail-closed workflow through rendered-asset
validation and content-addressed export. Label every handoff as fictional demonstration content and
a draft requiring qualified MLR review. Return absolute paths, hashes, sizes, and package digest.
Do not use the result for live promotion, send it, or publish it.
```

Omit `claims_path` and `brand_kit_path` only in this explicit demo workflow. An explicit invalid path never falls back to demo data.

## Validation and resume rules

Promotional copy must cite applicable approved claim IDs in every non-CTA block. Exact approved legal text and the brief's exact CTA are the only uncited exceptions. Only exact approved claim text or an exact allowed variant can pass automatically; fuzzy matches cannot approve copy. Each channel must independently pass claim, fair-balance, prohibited-language, and required-content checks.

Any brief, claim, brand file, policy, template, copy, or rendered-output change can invalidate a previous seal. To resume, call `get_campaign_status campaign_brief_id="<id>"` and follow its `next_step`. Do not infer readiness by inspecting stored filenames.

## Compliance boundary

The package is a review aid, not regulatory approval. A qualified medical/legal/regulatory reviewer must approve content before use. The bundled claims and brand kit are fictional demo data and must not be used in production. The tools do not send email, traffic ads, publish assets, or record an authoritative external decision.

## Output

Artifacts are written under `<store>/campaigns/<campaign_brief_id>/`; rendered assets and review exports are under `outputs/`, and validation evidence is under `validation/`. `render_mlr_review` and `export_mlr_package` return absolute paths, hashes, and sizes. The ZIP filename contains its package digest, and `package-manifest.json` lists relative member paths with SHA-256 and byte size.

See the installed Skill references for the exact [input contracts](../../src/capabilities/campaign-studio/skill/references/input-contracts.md), [claim governance](../../src/capabilities/campaign-studio/skill/references/claim-governance.md), [channel specifications](../../src/capabilities/campaign-studio/skill/references/channel-specifications.md), and [output schema](../../src/capabilities/campaign-studio/skill/references/output-schema.md).
