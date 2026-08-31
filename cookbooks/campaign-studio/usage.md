# Campaign Studio

Create campaign briefs, validate exact approved-claims and brand-kit inputs, draft channel content,
render assets, and export a content-addressed package for qualified MLR review.

Campaign Studio supports English (`en`) email, banner, and poster output. Approved claims must be
UTF-8 JSON, and a brand kit must be a directory with the documented files. The plugin does not
extract approved claims from other formats.

## Tools

| Tool | Purpose |
|---|---|
| `create_campaign_brief` | Create or update the campaign brief |
| `get_campaign_status` | Read workflow status, validation freshness, and the next required tool |
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
| `package_mlr_submission` | Assemble the compatibility MLR review package |
| `render_mlr_review` | Render canonical Markdown and interactive HTML review files |
| `export_mlr_package` | Export a deterministic manifest and content-addressed ZIP |

For exact schemas, check the installed Skill or MCP tool list.

## Install

Register the marketplace once, then install the Skill and MCP server for your agent host. If the
marketplace is already registered, skip its `add` command and refresh it before installing a newly
released capability.

The marketplace catalog pins this capability to its current immutable release tag.

### Claude Code

```bash
claude plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
claude plugin install open-pharma-plugins-campaign-studio@open-pharma-plugins
```

### Codex

```bash
codex plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
codex plugin marketplace upgrade open-pharma-plugins
codex plugin add open-pharma-plugins-campaign-studio@open-pharma-plugins
```

### GitHub Copilot CLI

```bash
copilot plugin marketplace add https://github.com/PharmaGenAI/open-pharma-plugins.git
copilot plugin install open-pharma-plugins-campaign-studio@open-pharma-plugins
```

### Python MCP server only

The Python distribution installs the server without the companion Skill:

```bash
python -m pip install "open-pharma-plugins[campaign-studio]"
```

The guided installer, local-checkout setup, and rollback instructions are in the shared
[installation guide](../../docs/en/installation.md).

## Configure

`OPEN_PHARMA_CAMPAIGN_STORE_DIR` controls the private campaign store and defaults to
`~/.open-pharma-plugins/campaign-studio`. Campaign Studio does not require an external provider key.

Production work requires the absolute path to an approved-claims JSON file and the absolute path to
a brand-kit directory. Bundled fictional inputs are available only when the operator explicitly
selects `demo_mode=true`. An explicit invalid path never falls back to demo data.

## Recommended workflow

1. Define the country, policy jurisdiction, mode, brand, indication, audience, objective, KPI, CTA,
   HTTPS CTA URL, requested channels, and exact input paths.
2. Create the brief and preflight both inputs. Stop on any missing, malformed, excluded, or
   inconsistent source.
3. Build the audience journey, claim-linked message architecture, and structured copy for every
   requested channel.
4. Validate each channel's claims, fair balance, prohibited language, and required content.
5. Render the email, banner, or poster only after the current validation fingerprint passes.
6. Validate the actual rendered files, then render the canonical MLR review and export the
   content-addressed package.
7. Hand the draft to qualified medical, legal, and regulatory reviewers. Do not send or publish the
   assets from this workflow.

Preserve every supplied path exactly. Stop at the first failed gate rather than substituting copied,
inferred, or bundled inputs.

## Example requests

```text
@approved-claims.json @brand-kit
Create an English email and 300x250 banner for US oncologists using only applicable approved claims.
Validate both rendered files and prepare a draft package for qualified MLR review. Do not send or
publish anything.

@approved-claims.json @brand-kit
Create an English disease-awareness poster for the UK under MHRA policy. Keep the headline
non-promotional, include the required legal content, and prepare a reviewable PDF and MLR summary.

Using explicit demo mode, create a fictional English demonstration campaign for email, banner, and
poster. Label every output as fictional draft content, run every validation gate, and export the
review package.
```

## Outputs and safeguards

Every promotional statement must cite an applicable approved `claim_id`. Only exact approved text
or an exact allowed variant passes automatically; fuzzy matches cannot approve copy. Changed
numbers, negation, off-label language, and unsupported competitive comparisons are rejected.
Exact approved legal text and the brief's exact CTA are the uncited exceptions. Fair-balance and
required-content checks apply independently to every claim-bearing channel.

The validation fingerprint binds the brief, applicable claims, channel copy, live brand files,
policy, templates, and capability version. A change to any bound input makes the prior validation
stale. Rendered files have a separate validation gate, and review or export requires current passing
pre-render and rendered-file evidence.

Artifacts are written under `<store>/campaigns/<campaign_brief_id>/`. Rendered assets and review
exports are under `outputs/`, and validation evidence is under `validation/`. The review and export
tools return absolute paths, SHA-256 hashes, and byte sizes. The ZIP filename contains its package
digest, and `package-manifest.json` lists relative member paths with their hashes and sizes.
Runtime artifacts are private where POSIX permissions are available.

The package is a draft review aid, not regulatory approval. The tools do not send email, traffic
ads, publish assets, or record an authoritative external decision. Bundled claims and brand assets
are fictional and must not be used in production.

## Advanced usage

To resume a campaign, provide its `campaign_brief_id`. Check `get_campaign_status` and follow its
`next_step`; do not infer readiness from stored filenames. Rebuild the reported stage and every
dependent gate when status reports stale, malformed, failed, or missing evidence.

The direct production tool sequence is:

```text
create_campaign_brief approved_claims_path="/absolute/path/approved-claims.json"
  brand_kit_path="/absolute/path/brand-kit" demo_mode=false <campaign fields>
preflight_campaign_inputs campaign_brief_id="<id>" demo_mode=false
generate_audience_journey campaign_brief_id="<id>" journey="<JSON>"
generate_message_architecture campaign_brief_id="<id>" messages="<JSON>"
  fair_balance_statement="<approved safety statement>" fair_balance_sources="<JSON>"
generate_channel_copy campaign_brief_id="<id>" channel="<channel>" copy_json="<JSON>"
validate_claims_and_fair_balance campaign_brief_id="<id>"
render_email campaign_brief_id="<id>"
render_banner campaign_brief_id="<id>"
render_poster campaign_brief_id="<id>"
validate_rendered_assets campaign_brief_id="<id>"
render_mlr_review campaign_brief_id="<id>"
export_mlr_package campaign_brief_id="<id>" destination_dir="/absolute/path/review-handoff"
```

Repeat `generate_channel_copy` and call only the matching renderers for the channels in the brief.
`package_mlr_submission` remains the compatibility route to the canonical review outputs and uses
the same gates.

Detailed references:

- [Input contracts](../../src/capabilities/campaign-studio/skill/references/input-contracts.md)
- [Claim governance](../../src/capabilities/campaign-studio/skill/references/claim-governance.md)
- [Channel specifications](../../src/capabilities/campaign-studio/skill/references/channel-specifications.md)
- [Output schema](../../src/capabilities/campaign-studio/skill/references/output-schema.md)
