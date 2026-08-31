# Field Training

Turn one or more approved PDF/PPTX file paths into source-grounded learning packages, assessments,
pre-session role-play kits, or post-session scorecards.

## Tools available

| Tool | Purpose |
|---|---|
| `ingest_document` | Extract page/slide text into the private content store |
| `list_documents` | List ingested documents |
| `search_content` | Search extracted text and speaker notes |
| `get_document_page` | Retrieve one page or slide |
| `render_output` | Validate and save structured JSON plus interactive, self-contained HTML |

## Install and configure

```bash
pip install "open-pharma-plugins[field-training]"
```

`OPEN_PHARMA_TRAINING_CONTENT_DIR` defaults to `~/.open-pharma-plugins/training-content`.

## Path-first Codex workflow

Users do not need to call the MCP tools themselves. Provide absolute paths and the requested output
in one prompt:

```text
Use @open-pharma-plugins-field-training.

Use only these approved sources:
- /absolute/path/approved-messages.pdf
- /absolute/path/training-deck.pptx

Generate a learning package named melanoma-field-dossier. Ingest every file, stop if any path
fails, ground every claim to an exact page or slide excerpt, include fair balance, and render the
final JSON and HTML. Report the absolute output paths.
```

For a pre-session role-play kit:

```text
Use @open-pharma-plugins-field-training.

Create a pre-session role-play kit from:
- /absolute/path/approved-messages.pdf

Persona: time-pressed community oncologist.
Topic: efficacy with safety context.
Output name: melanoma-roleplay-kit.

Include objectives, approved messages, objections, facilitator prompts, and a weighted evaluation
rubric totaling 100%. Render the final JSON and interactive HTML.
```

The agent validates and ingests every supplied path before synthesis. A missing, unreadable, or
unsupported path stops generation and is reported explicitly; the agent does not silently render a
partial-source artifact. Each content search is restricted to one of the document IDs collected for
that request, so previously ingested files are not silently mixed into the output.

## Direct tool workflow

```text
ingest_document file_path="/path/to/approved-messages.pdf"
ingest_document file_path="/path/to/training-deck.pptx"
list_documents
search_content query="overall survival" document_id="<document_id>"
get_document_page document_id="<document_id>" page_number=3
render_output output_type="learning_package" content_json="<schema-valid JSON>"
  file_name="product_training"
```

`render_output` rejects unknown schema fields and verifies that every `SourceReference` points to an ingested document, the stated page/slide, the matching filename, and an excerpt present on that page. Output is written to `<content-dir>/outputs/` with private permissions.

Valid output types are `learning_package`, `assessment`, `roleplay_kit`, and
`roleplay_scorecard`. The HTML is self-contained and works offline, with source expansion, print
controls, message filtering, assessment answer reveal, and facilitator-mode controls where
applicable.

## HTML examples

- [Learning package example](../../src/capabilities/field-training/skill/references/examples/learning-package.html)
- [Pre-session role-play kit example](../../src/capabilities/field-training/skill/references/examples/roleplay-kit.html)

These examples use fictional bundled content and are generated from the production renderer.

## Compliance boundary

Use only approved source documents. Generated material remains a draft for qualified medical/legal/regulatory review; it must not introduce off-label, unsupported, or unbalanced claims. The bundled PDF and PPTX files are fictional demo fixtures.
