---
name: open-pharma-plugins-field-training
description: |
  Turn one or more approved PDF/PPTX file paths into source-grounded learning
  packages, assessments, pre-session role-play kits, and interactive role-play
  scenarios for pharmaceutical field representatives. Use when: (1) the user provides approved
  documents and asks for training materials, a learning package, or key
  messages; (2) someone says "create a quiz", "generate an assessment",
  "build a training module", "product training", "approved messages",
  "key messages", "talking points", "objection handling"; (3) the user
  asks to practice a role-play or conversation with an HCP; (4) the user
  wants to extract key messages from a set of approved product documents
  for field rep enablement.
---

# Field Training

Build source-grounded learning packages, interactive role-play scenarios, and
knowledge assessments for field representatives from approved product documents.

Every generated claim, talking point, and assessment answer traces back to a
specific page or slide in the source documents.

## Available MCP tools

| Tool | Purpose | Returns |
|---|---|---|
| `ingest_document` | Extract structured text from a PDF or PPTX and store it in the content index | Document summary: ID, title, page count, file type |
| `list_documents` | List all ingested documents in the content store | Table of documents with ID, name, type, pages, ingest date |
| `search_content` | Search across ingested documents for passages matching a query | Ranked passages with document name, page/slide number, text, relevance score |
| `get_document_page` | Get the full text of a specific page or slide | Full page text with metadata (slide title, speaker notes for PPTX) |
| `render_output` | Save structured output as JSON + interactive, self-contained HTML to the training output directory | File paths for both JSON and HTML versions |

## Compliance

> **The agent must NEVER generate product claims that are not directly
> supported by the ingested source documents.** If asked to include a claim
> without source support, refuse and explain that only approved content can
> be used. Unapproved promotion is a serious regulatory violation.

Additional rules:

1. **Every claim must cite a SourceReference** — document ID and name, page/slide
   number, and a verbatim excerpt present in the ingested source.
2. **Never extrapolate beyond the source documents.** Do not add efficacy
   data, safety information, or competitive comparisons that are not present
   in the ingested materials.
3. **Flag uncertainty.** If a claim appears in a source but its context is
   ambiguous, mark it as needing medical/legal review rather than presenting
   it as fact.
4. **Separate approved claims from general medical knowledge.** When
   providing therapeutic-area context (e.g., disease prevalence), clearly
   distinguish it from approved product claims.
5. **NEVER generate claims about off-label uses.** If a document mentions a
   use that is not in the approved indication, do not include it in learning
   materials, assessments, or role-play responses. If a rep mentions an
   off-label use during role-play, the agent must immediately flag it as
   off-label and redirect to approved indications only.
6. **Role-play model answers must use verbatim approved language** from the
   source documents wherever possible. Do not paraphrase approved claims —
   the exact wording has been through medical-legal-regulatory (MLR) review.
   When the agent provides a model answer or ideal response, quote the
   approved language directly and cite the source page/slide.
7. **Never compare the product to competitors** unless such a comparison
   exists verbatim in the approved documents. Unsupported comparative claims
   are a compliance violation.
8. **Fair balance: safety alongside efficacy.** Safety information must
   always be presented alongside efficacy claims, as required by fair balance
   regulations. If a learning module covers efficacy, it must include the
   corresponding safety/risk information from the same or related approved
   documents.
9. **Human review is mandatory.** Rendered files are drafts for qualified
   medical/legal/regulatory review, not evidence of approval.

## Path-first user workflow

An input-path prompt is a complete operational request. When the user supplies one or more explicit
absolute PDF/PPTX paths and names an output type, do not ask them to translate the request into MCP
tool calls.

1. Preserve each submitted path exactly. Do not expand directories or globs unless the user asks.
2. Call `ingest_document` once for every path and collect each returned document ID.
3. If any path is missing, unreadable, unsupported, or fails ingestion, stop before synthesis and
   report the per-path error. Never silently create a partial-source artifact.
4. If every file ingests, execute the requested workflow using only those document IDs.
5. For every `search_content` operation, call it once per collected document ID and pass that ID in
   `document_id`; combine only those results. Never omit `document_id`, because doing so searches
   the persistent store and can include material from an earlier request.
6. Retrieve pages only from the collected document IDs. Before rendering, confirm every
   `SourceReference.document_id` is in that set and `source_documents` contains only the submitted
   file names.
7. Call `render_output` with the requested output type and optional filename stem.
8. Report the validation result plus absolute JSON and HTML paths.

Supported path-first output requests are `learning_package`, `assessment`, `roleplay_kit`, and an
interactive session followed by `roleplay_scorecard`. If the user says only "field training," use
`learning_package`. If the user asks for "role-play materials," use `roleplay_kit`. If the user asks
to practice live, follow the interactive workflow and produce a `roleplay_scorecard` after the
session.

## Workflow 1: Build Learning Package

Follow this sequence when the user asks for a learning package, training
module, key messages, or talking points.

```
1. INGEST
   Call ingest_document for each provided file (PDF or PPTX).
   Confirm each document ingested successfully before proceeding.

2. SURVEY
   For every broad query, call search_content once per collected document ID,
   always passing document_id, then combine only those results to identify:
     - Product name, mechanism of action, therapeutic area
     - Efficacy, safety, dosing, administration
     - Competitive landscape, patient selection

3. EXTRACT KEY MESSAGES
   For each identified topic and collected document ID, call search_content
   with document_id and targeted queries.
   Pull verbatim approved claims with their page/slide
   references. Categorize each message: efficacy, safety, dosing, MOA,
   patient selection, etc.

4. STRUCTURE
   Organize the extracted content into LearningModules:
     - Title and therapeutic area
     - Learning objectives (Bloom's taxonomy: remember, understand, apply)
     - Key messages with source citations
     - Talking points: situation → approved response → supporting data
     - Common objections: anticipated HCP pushback → approved response

5. GROUND
   Review every message and talking point. Each MUST have at least one
   SourceReference with document_name, page_number, and excerpt.
   Remove any content that cannot be grounded.

6. OUTPUT
   Return the LearningPackage as a JSON object conforming to the schema
   in references/output-schema.md. Then call render_output with
   output_type="learning_package" and the JSON string to save both a
   JSON file and a formatted HTML document to the output directory.
```

## Workflow 2: Generate Assessment

Follow this sequence when the user asks for a quiz, test, or assessment.

```
1. IDENTIFY SCOPE
   Use the document IDs collected from the submitted paths and determine which
   topics the assessment should cover.
   If the user specifies a product or topic, filter accordingly.
   If not, assess across all submitted document IDs.

2. SEARCH
   Call search_content once per collected document ID for each topic area,
   always passing document_id, to find testable facts:
     - Approved efficacy claims and supporting data
     - Safety information and contraindications
     - Dosing and administration details
     - Mechanism of action
     - Patient selection criteria

3. GENERATE MCQ (10–15 questions)
   Create multiple-choice questions across three difficulty levels:
     - Easy (remember): direct recall of approved claims
     - Medium (understand): interpreting data or distinguishing claims
     - Hard (apply): selecting the right message for a clinical scenario
   Each question has 4 options (A–D), one correct answer, an explanation,
   and a SourceReference for the correct answer.

4. GENERATE SCENARIOS (3–5 questions)
   Create scenario-based questions with HCP personas:
     - "A skeptical oncologist asks about long-term safety data…"
     - "A busy GP wants a 30-second elevator pitch…"
   Each scenario includes: persona description, ideal response points
   to cover, and source references for each point.

5. VALIDATE
   Review every correct answer and ideal response point against the
   source content. Remove or revise any question whose answer cannot
   be verified in the ingested documents.

6. OUTPUT
   Return the Assessment as a JSON object conforming to the schema
   in references/output-schema.md. Then call render_output with
   output_type="assessment" and the JSON string to save both a
   JSON file and a formatted HTML document to the output directory.
```

## Workflow 3: Build a Pre-Session Role-Play Kit

Follow this sequence when the user asks for role-play materials, a facilitator guide, a practice
scenario, or a role-play kit.

```
1. INGEST AND SCOPE
   Ingest every supplied PDF/PPTX path. Determine the topic and HCP persona from the request;
   default to a moderately interested general physician only when the user omits the persona.

2. PREPARE APPROVED CONTENT
   For each requested topic, efficacy, safety, dosing, and likely-objection query, call
   search_content once per collected document ID and pass document_id. Retrieve full source
   pages/slides only from those IDs for every passage used.

3. BUILD THE KIT
   Create a RoleplayKit containing:
     - title, topic, HCP persona, and scenario
     - learning objectives
     - approved key messages with SourceReferences
     - likely objections with grounded approved responses
     - facilitator prompts and coaching intent
     - evaluation criteria whose weight_pct values total exactly 100

4. VALIDATE
   Remove unsupported content. Ensure efficacy is paired with relevant safety context and every
   product claim cites an exact ingested page/slide excerpt.

5. OUTPUT
   Return the RoleplayKit as schema-valid JSON, then call render_output with
   output_type="roleplay_kit". Report the absolute JSON and HTML paths and mark both as draft for
   qualified MLR review.
```

## Workflow 4: Interactive Role-Play

Follow this sequence when the user asks to practice a conversation, do a
role-play, or rehearse a detailing call.

```
1. SETUP
   Ask the user for:
     - Product or topic to discuss
     - HCP persona (optional — default: general physician, moderately
       interested, will ask 2–3 probing questions)
   If documents have not been ingested yet, prompt the user to provide
   them and call ingest_document first.
   Keep the collected document IDs as the session scope. If the user did not
   submit paths in this request, ask them to select or provide the approved
   documents rather than searching the persistent store globally.

2. PREPARE
   Call search_content once per collected document ID, always passing
   document_id, to load approved content relevant to the chosen product/topic.
   Hold this scoped content as your reference for
   evaluating the rep's responses during the conversation.

3. PLAY
   Adopt the HCP persona and begin the conversation:
     - Greet the rep and ask an opening question about the product.
     - After each rep response, silently evaluate:
       a. Did they communicate an approved key message?
       b. Did they say anything unsupported by the source documents?
       c. Did they miss an important talking point?
     - Continue the conversation naturally: ask follow-up questions,
       raise objections, request evidence or data.
     - Run for 5–8 turns or until the user says "stop" or "end".

4. DEBRIEF
   Generate a RoleplayScorecard:
     - List every claim the rep made and evaluate each:
       correct (supported by source), missed (key message not mentioned),
       unsupported (claimed but not in sources), inaccurate (wrong data)
     - Cite the source for correct/missed claims
     - Calculate an overall score (0.0–1.0)
     - Highlight strengths and areas for improvement
   Return the scorecard as JSON conforming to the schema in
   references/output-schema.md. Then call render_output with
   output_type="roleplay_scorecard" and the JSON string to save both a
   JSON file and a formatted HTML scorecard to the output directory.
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `OPEN_PHARMA_TRAINING_CONTENT_DIR` | `~/.open-pharma-plugins/training-content/` | Directory where ingested document content is stored |

## Composing with other capabilities

- Use the host application's document viewer when the user needs to inspect an original page.
- External disease context must remain clearly separated from approved product content and may
  not be supplied as a `SourceReference` unless it was deliberately ingested and approved.

## Output schema

See [references/output-schema.md](references/output-schema.md) for the complete field-by-field
schema. Visual examples are available at
[references/examples/learning-package.html](references/examples/learning-package.html) and
[references/examples/roleplay-kit.html](references/examples/roleplay-kit.html); read them only when
the user asks to preview or assess the rendered presentation.
