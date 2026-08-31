"""Pydantic output models for field-training learning packages, assessments, and role-play.

Every generated content piece traces back to a specific source document and
page/slide via SourceReference, ensuring compliance with approved messaging.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictTrainingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Document content store
# ---------------------------------------------------------------------------


class DocumentPage(StrictTrainingModel):
    """A single page (PDF) or slide (PPTX) of extracted content."""

    page_number: int = Field(description="1-indexed page or slide number")
    text: str = Field(description="Extracted body text from the page or slide")
    page_type: str = Field(description="Content type: 'page' (PDF) or 'slide' (PPTX)")
    slide_title: str | None = Field(default=None, description="Slide title shape text (PPTX only)")
    speaker_notes: str | None = Field(default=None, description="Presenter notes attached to the slide (PPTX only)")


class IngestedDocument(StrictTrainingModel):
    """A fully ingested document stored in the content index."""

    document_id: str = Field(description="Stable identifier derived from the file path")
    file_name: str = Field(description="Original file name")
    file_path: str = Field(description="Absolute path to the source file")
    file_type: str = Field(description="File format: 'pdf' or 'pptx'")
    title: str | None = Field(default=None, description="Extracted document title")
    total_pages: int = Field(description="Total number of pages or slides")
    pages: list[DocumentPage] = Field(description="Page-level extracted content")
    ingested_at: str = Field(description="ISO-8601 timestamp of ingestion")


# ---------------------------------------------------------------------------
# Source reference (shared across all outputs)
# ---------------------------------------------------------------------------


class SourceReference(StrictTrainingModel):
    """A traceable reference to a specific passage in an approved source document."""

    document_id: str = Field(description="ID of the source document in the content store")
    document_name: str = Field(description="File name of the source document")
    page_number: int = Field(description="Page or slide number where the content appears")
    excerpt: str = Field(description="Relevant passage from the source supporting the claim")


# ---------------------------------------------------------------------------
# Learning package
# ---------------------------------------------------------------------------


class KeyMessage(StrictTrainingModel):
    """An approved key message extracted from source documents."""

    message: str = Field(description="The key message or approved claim")
    category: str = Field(description="Message category: efficacy, safety, dosing, MOA, differentiation, etc.")
    sources: list[SourceReference] = Field(
        min_length=1,
        description="Source references supporting this message",
    )


class LearningObjective(StrictTrainingModel):
    """A learning objective for a training module."""

    objective: str = Field(description="What the learner should be able to do after training")
    bloom_level: str = Field(description="Bloom's taxonomy level: 'remember', 'understand', or 'apply'")


class TalkingPoint(StrictTrainingModel):
    """A situation-response pair for field rep conversations."""

    situation: str = Field(description="The conversational context, e.g. 'When an HCP asks about efficacy...'")
    approved_response: str = Field(description="The approved response the rep should deliver")
    supporting_data: str | None = Field(default=None, description="Key data points that support the response")
    sources: list[SourceReference] = Field(
        min_length=1,
        description="Source references grounding the approved response",
    )


class Objection(StrictTrainingModel):
    """A common HCP objection with an approved response."""

    objection: str = Field(description="The objection an HCP might raise, e.g. 'What about the cardiac safety signal?'")
    approved_response: str = Field(description="The approved response addressing the objection")
    sources: list[SourceReference] = Field(
        min_length=1,
        description="Source references grounding the response",
    )


class LearningModule(StrictTrainingModel):
    """A single training module within a learning package."""

    title: str = Field(description="Module title")
    product: str | None = Field(default=None, description="Product name if product-specific")
    therapeutic_area: str | None = Field(default=None, description="Therapeutic area covered by the module")
    objectives: list[LearningObjective] = Field(description="Learning objectives for this module")
    key_messages: list[KeyMessage] = Field(description="Approved key messages the rep must know")
    talking_points: list[TalkingPoint] = Field(description="Situation-response talking points for HCP conversations")
    common_objections: list[Objection] = Field(description="Common HCP objections with approved responses")


class LearningPackage(StrictTrainingModel):
    """A complete source-grounded learning package for field reps."""

    title: str = Field(description="Package title")
    modules: list[LearningModule] = Field(description="Training modules")
    source_documents: list[str] = Field(description="Names of all source documents used to build the package")
    generated_at: str = Field(description="ISO-8601 timestamp of generation")


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


class MCQOption(StrictTrainingModel):
    """A single option in a multiple-choice question."""

    label: str = Field(description="Option label: A, B, C, or D")
    text: str = Field(description="Option text")


class MCQuestion(StrictTrainingModel):
    """A multiple-choice knowledge check question."""

    question_id: str = Field(description="Unique question identifier")
    question: str = Field(description="The question text")
    options: list[MCQOption] = Field(description="Answer options (typically 4)")
    correct_answer: str = Field(description="Label of the correct option (e.g. 'B')")
    explanation: str = Field(description="Explanation of why the correct answer is right, citing the source")
    source: SourceReference = Field(description="Source reference for the correct answer")
    difficulty: str = Field(description="Difficulty level: 'easy', 'medium', or 'hard'")


class ScenarioQuestion(StrictTrainingModel):
    """A scenario-based question simulating an HCP interaction."""

    question_id: str = Field(description="Unique question identifier")
    scenario: str = Field(description="The scenario description, e.g. 'A cardiologist asks you about...'")
    hcp_persona: str = Field(description="HCP persona details, e.g. 'Skeptical oncologist, concerned about toxicity'")
    ideal_response_points: list[str] = Field(description="Key points that an ideal response should cover")
    sources: list[SourceReference] = Field(
        min_length=1,
        description="Source references for the ideal response points",
    )
    difficulty: str = Field(description="Difficulty level: 'easy', 'medium', or 'hard'")


class Assessment(StrictTrainingModel):
    """A complete assessment with MCQ and scenario-based questions."""

    title: str = Field(description="Assessment title")
    mcq_questions: list[MCQuestion] = Field(description="Multiple-choice knowledge check questions")
    scenario_questions: list[ScenarioQuestion] = Field(description="Scenario-based application questions")
    total_points: int = Field(description="Total points available")
    passing_score_pct: float = Field(description="Passing threshold as a fraction, e.g. 0.8 for 80%")
    source_documents: list[str] = Field(description="Names of source documents the assessment is based on")
    generated_at: str = Field(description="ISO-8601 timestamp of generation")


# ---------------------------------------------------------------------------
# Role-play
# ---------------------------------------------------------------------------


class RoleplayTurn(StrictTrainingModel):
    """A single turn in an interactive role-play conversation."""

    speaker: str = Field(description="Who spoke: 'hcp' or 'rep'")
    message: str = Field(description="What was said in this turn")


class ClaimEvaluation(StrictTrainingModel):
    """Evaluation of a single claim made during role-play."""

    claim: str = Field(description="The claim or statement being evaluated")
    status: str = Field(description="Evaluation status: 'correct', 'missed', 'unsupported', or 'inaccurate'")
    source: SourceReference | None = Field(
        default=None,
        description="Source reference if the claim is correct or has a known approved version",
    )
    feedback: str = Field(description="Specific feedback explaining the evaluation")


class RoleplayScorecard(StrictTrainingModel):
    """Post-session scorecard summarizing role-play performance."""

    hcp_persona: str = Field(description="The HCP persona used in the role-play")
    topic: str = Field(description="Product or topic discussed")
    turns: list[RoleplayTurn] = Field(description="Full conversation transcript")
    claims_evaluated: list[ClaimEvaluation] = Field(description="Evaluation of each claim made by the rep")
    score: float = Field(ge=0.0, le=1.0, description="Overall score from 0.0 to 1.0")
    strengths: list[str] = Field(description="What the rep did well")
    areas_for_improvement: list[str] = Field(description="Specific areas where the rep can improve")
    source_documents: list[str] = Field(description="Source documents used for evaluation")


# ---------------------------------------------------------------------------
# Pre-session role-play kit
# ---------------------------------------------------------------------------


class FacilitatorPrompt(StrictTrainingModel):
    """A facilitator cue for one stage of a role-play session."""

    stage: str = Field(description="Session stage, such as opening, probe, objection, or close")
    prompt: str = Field(description="Prompt the facilitator delivers to the representative")
    coaching_intent: str = Field(description="Behavior or skill the facilitator should observe")


class EvaluationCriterion(StrictTrainingModel):
    """One weighted criterion in the pre-session evaluation rubric."""

    criterion: str = Field(description="Capability being evaluated")
    weight_pct: int = Field(ge=1, le=100, description="Percentage weight for this criterion")
    evidence_to_observe: list[str] = Field(description="Observable behaviors that demonstrate the criterion")


class RoleplayKit(StrictTrainingModel):
    """A source-grounded kit used to facilitate and assess a future role-play."""

    title: str = Field(description="Role-play kit title")
    topic: str = Field(description="Product or approved-message topic")
    hcp_persona: str = Field(description="HCP persona used in the scenario")
    scenario: str = Field(description="Situation the representative will practice")
    objectives: list[LearningObjective] = Field(min_length=1, description="Session learning objectives")
    key_messages: list[KeyMessage] = Field(
        min_length=1,
        description="Approved messages the representative should communicate",
    )
    common_objections: list[Objection] = Field(
        min_length=1,
        description="Likely objections with approved responses",
    )
    facilitator_prompts: list[FacilitatorPrompt] = Field(
        min_length=1,
        description="Prompts that guide the practice conversation",
    )
    evaluation_rubric: list[EvaluationCriterion] = Field(
        min_length=1,
        description="Weighted evaluation criteria",
    )
    source_documents: list[str] = Field(min_length=1, description="Names of all approved documents used")
    generated_at: str = Field(description="ISO-8601 timestamp of generation")

    @model_validator(mode="after")
    def rubric_totals_100_percent(self) -> RoleplayKit:
        total = sum(item.weight_pct for item in self.evaluation_rubric)
        if total != 100:
            raise ValueError(f"evaluation_rubric weight_pct values must total 100, got {total}")
        return self
