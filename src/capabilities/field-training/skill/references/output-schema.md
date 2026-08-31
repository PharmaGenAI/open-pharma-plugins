# Field Training — Output Schema Reference

Pydantic models live in `open_pharma_plugins_field_training/models.py`.

## SourceReference

Every generated claim cites one or more source references.

```json
{
  "document_id": "abc123",
  "document_name": "ProductX_Approved_Messages_2026.pptx",
  "page_number": 12,
  "excerpt": "ProductX demonstrated a 42% reduction in disease progression (p<0.001) vs placebo at 24 months."
}
```

| Field | Type | Description |
|---|---|---|
| `document_id` | string | Internal document identifier |
| `document_name` | string | Original filename |
| `page_number` | int | 1-indexed page or slide number |
| `excerpt` | string | Verbatim passage present on the referenced ingested page or slide |

## LearningPackage

```json
{
  "title": "ProductX Field Training — Oncology",
  "modules": [
    {
      "title": "Efficacy and Clinical Evidence",
      "product": "ProductX",
      "therapeutic_area": "Non-Small Cell Lung Cancer",
      "objectives": [
        {"objective": "State the primary endpoint result from the Phase 3 trial", "bloom_level": "remember"},
        {"objective": "Explain the clinical significance of the PFS benefit", "bloom_level": "understand"}
      ],
      "key_messages": [
        {
          "message": "ProductX demonstrated a 42% reduction in disease progression vs placebo.",
          "category": "efficacy",
          "sources": [{"document_id": "abc123", "document_name": "ProductX_Approved_Messages_2026.pptx", "page_number": 12, "excerpt": "42% reduction in disease progression (HR 0.58, p<0.001)"}]
        }
      ],
      "talking_points": [
        {
          "situation": "When an HCP asks about the primary endpoint",
          "approved_response": "In the Phase 3 APEX trial, ProductX reduced the risk of disease progression by 42% compared to placebo, with a hazard ratio of 0.58.",
          "supporting_data": "Median PFS was 14.2 months vs 8.4 months.",
          "sources": [{"document_id": "abc123", "document_name": "ProductX_Approved_Messages_2026.pptx", "page_number": 12, "excerpt": "..."}]
        }
      ],
      "common_objections": [
        {
          "objection": "What about the hepatotoxicity signal from the trial?",
          "approved_response": "Grade 3+ ALT elevations occurred in 5.2% of patients. The prescribing information recommends liver function monitoring at baseline and monthly for the first 6 months.",
          "sources": [{"document_id": "abc123", "document_name": "ProductX_Approved_Messages_2026.pptx", "page_number": 18, "excerpt": "..."}]
        }
      ]
    }
  ],
  "source_documents": ["ProductX_Approved_Messages_2026.pptx", "ProductX_PI_2026.pdf"],
  "generated_at": "2026-08-19T15:00:00Z"
}
```

| Section | Key fields |
|---|---|
| `modules[].objectives` | `objective` (string), `bloom_level` (remember / understand / apply) |
| `modules[].key_messages` | `message`, `category` (efficacy / safety / dosing / MOA / patient selection), `sources` |
| `modules[].talking_points` | `situation`, `approved_response`, `supporting_data`, `sources` |
| `modules[].common_objections` | `objection`, `approved_response`, `sources` |

## Assessment

```json
{
  "title": "ProductX Knowledge Assessment",
  "mcq_questions": [
    {
      "question_id": "MCQ-001",
      "question": "What was the hazard ratio for PFS in the APEX trial?",
      "options": [
        {"label": "A", "text": "0.42"},
        {"label": "B", "text": "0.58"},
        {"label": "C", "text": "0.73"},
        {"label": "D", "text": "0.85"}
      ],
      "correct_answer": "B",
      "explanation": "The APEX trial demonstrated a hazard ratio of 0.58 for PFS.",
      "source": {"document_id": "abc123", "document_name": "ProductX_Approved_Messages_2026.pptx", "page_number": 12, "excerpt": "HR 0.58, 95% CI 0.45-0.74, p<0.001"},
      "difficulty": "easy"
    }
  ],
  "scenario_questions": [
    {
      "question_id": "SCN-001",
      "scenario": "A skeptical oncologist says: 'I've seen PFS benefits before that don't translate to OS. Why should I change my practice for ProductX?'",
      "hcp_persona": "Experienced medical oncologist, evidence-driven, skeptical of incremental PFS gains",
      "ideal_response_points": [
        "Acknowledge the valid concern about PFS-OS translation",
        "Cite the magnitude of PFS benefit (42% risk reduction)",
        "Reference the OS trend data if available in approved materials",
        "Highlight the quality-of-life data from the trial"
      ],
      "sources": [
        {"document_id": "abc123", "document_name": "ProductX_Approved_Messages_2026.pptx", "page_number": 12, "excerpt": "..."},
        {"document_id": "abc123", "document_name": "ProductX_Approved_Messages_2026.pptx", "page_number": 15, "excerpt": "..."}
      ],
      "difficulty": "hard"
    }
  ],
  "total_points": 20,
  "passing_score_pct": 0.8,
  "source_documents": ["ProductX_Approved_Messages_2026.pptx"],
  "generated_at": "2026-08-19T15:30:00Z"
}
```

## RoleplayKit

Use `RoleplayKit` for reusable materials prepared before a practice session. Do not use it as a
post-session scorecard.

```json
{
  "title": "ProductX Evidence Conversation Practice",
  "topic": "Efficacy with safety context",
  "hcp_persona": "Time-pressed community oncologist who asks for comparative numbers",
  "scenario": "The HCP asks for the headline result and then requests the relevant safety context.",
  "objectives": [
    {
      "objective": "Deliver the approved efficacy message with fair balance",
      "bloom_level": "apply"
    }
  ],
  "key_messages": [
    {
      "message": "ProductX demonstrated the approved endpoint result.",
      "category": "efficacy",
      "sources": [
        {
          "document_id": "abc123",
          "document_name": "ProductX_Approved_Messages_2026.pptx",
          "page_number": 12,
          "excerpt": "Verbatim approved endpoint passage"
        }
      ]
    }
  ],
  "common_objections": [
    {
      "objection": "What safety context should I consider?",
      "approved_response": "Verbatim approved safety response.",
      "sources": [
        {
          "document_id": "abc123",
          "document_name": "ProductX_Approved_Messages_2026.pptx",
          "page_number": 18,
          "excerpt": "Verbatim approved safety passage"
        }
      ]
    }
  ],
  "facilitator_prompts": [
    {
      "stage": "opening",
      "prompt": "Ask the representative for the headline evidence.",
      "coaching_intent": "Observe whether the response includes fair balance."
    }
  ],
  "evaluation_rubric": [
    {
      "criterion": "Approved efficacy language",
      "weight_pct": 60,
      "evidence_to_observe": ["Uses the sourced endpoint result"]
    },
    {
      "criterion": "Fair balance",
      "weight_pct": 40,
      "evidence_to_observe": ["Includes the sourced safety context"]
    }
  ],
  "source_documents": ["ProductX_Approved_Messages_2026.pptx"],
  "generated_at": "2026-08-28T00:00:00Z"
}
```

`evaluation_rubric[].weight_pct` must be an integer from 1 to 100 and all rubric weights must total
exactly 100. Every RoleplayKit collection shown above is required and must contain at least one
item. Every key message and objection response must contain at least one `SourceReference`, and
`source_documents` must contain at least one approved document name. Product claims belong in
`key_messages` or grounded objection responses; facilitator instructions and observable coaching
behaviors must not introduce new product claims.

## RoleplayScorecard

```json
{
  "hcp_persona": "Community oncologist, time-pressed, open to new therapies",
  "topic": "ProductX efficacy and safety in NSCLC",
  "turns": [
    {"speaker": "hcp", "message": "So tell me about ProductX. What's the headline?"},
    {"speaker": "rep", "message": "ProductX showed a 42% reduction in disease progression in the Phase 3 APEX trial..."}
  ],
  "claims_evaluated": [
    {
      "claim": "42% reduction in disease progression",
      "status": "correct",
      "source": {"document_id": "abc123", "document_name": "ProductX_Approved_Messages_2026.pptx", "page_number": 12, "excerpt": "42% reduction in disease progression"},
      "feedback": "Accurate. Matches approved messaging."
    },
    {
      "claim": "Median PFS of 14.2 months",
      "status": "missed",
      "source": {"document_id": "abc123", "document_name": "ProductX_Approved_Messages_2026.pptx", "page_number": 12, "excerpt": "Median PFS 14.2 months vs 8.4 months"},
      "feedback": "This key data point was available but not mentioned."
    }
  ],
  "score": 0.75,
  "strengths": ["Opened with the headline efficacy result", "Handled the safety question accurately"],
  "areas_for_improvement": ["Include the median PFS numbers alongside the hazard ratio", "Ask the HCP about their current treatment approach before presenting data"],
  "source_documents": ["ProductX_Approved_Messages_2026.pptx"]
}
```

| Field | Type | Description |
|---|---|---|
| `turns` | RoleplayTurn[] | Full conversation transcript |
| `claims_evaluated[].status` | enum | `correct`, `missed`, `unsupported`, `inaccurate` |
| `score` | float [0, 1] | Proportion of key messages correctly communicated |
| `strengths` | string[] | What the rep did well |
| `areas_for_improvement` | string[] | Specific coaching feedback |

`render_output` rejects unknown fields and verifies each source against the ingested document ID,
filename, page/slide number, and excerpt before writing JSON or HTML.
