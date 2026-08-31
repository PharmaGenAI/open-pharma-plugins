"""render_output — validate and save structured training output as JSON plus HTML."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RenderOutputArgs(BaseModel):
    output_type: str = Field(
        description="Type of output: 'learning_package', 'assessment', 'roleplay_kit', or 'roleplay_scorecard'"
    )
    content_json: str = Field(
        description=(
            "The structured output as a JSON string (LearningPackage, Assessment, RoleplayKit, or RoleplayScorecard)"
        )
    )
    file_name: str | None = Field(
        default=None,
        description="Optional filename stem (without extension). Auto-generated from title + timestamp if omitted.",
    )


TOOL: dict[str, Any] = {
    "name": "render_output",
    "description": (
        "Validate and render a learning package, assessment, pre-session role-play kit, "
        "or post-session role-play scorecard as structured JSON and polished, interactive, "
        "self-contained HTML. Saves both files to the private training output directory."
    ),
    "args": RenderOutputArgs,
}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    from pydantic import ValidationError

    from shared.env import get_env
    from shared.filesystem import atomic_write_json, atomic_write_text, contained_path, ensure_private_dir

    from .._grounding import validate_output_sources
    from .._html_renderers import RENDERERS
    from ..models import Assessment, LearningPackage, RoleplayKit, RoleplayScorecard

    output_type = arguments["output_type"]
    content_json = arguments["content_json"]
    file_name = arguments.get("file_name")

    output_models = {
        "learning_package": LearningPackage,
        "assessment": Assessment,
        "roleplay_kit": RoleplayKit,
        "roleplay_scorecard": RoleplayScorecard,
    }
    if output_type not in output_models:
        valid_types = ", ".join(output_models)
        return [
            {
                "type": "text",
                "text": json.dumps({"error": f"Invalid output_type '{output_type}'. Must be one of: {valid_types}"}),
            }
        ]

    try:
        data = json.loads(content_json)
    except json.JSONDecodeError as exc:
        return [{"type": "text", "text": json.dumps({"error": f"Invalid JSON: {exc}"})}]

    try:
        validated = output_models[output_type].model_validate(data)
    except ValidationError as exc:
        return [
            {
                "type": "text",
                "text": json.dumps({"error": f"Output failed {output_type} schema validation: {exc}"}),
            }
        ]

    grounding_errors = validate_output_sources(validated)
    if grounding_errors:
        return [{"type": "text", "text": json.dumps({"error": "; ".join(grounding_errors)})}]
    data = validated.model_dump(mode="json", exclude_none=True)

    if not file_name:
        title = data.get("title") or data.get("topic") or output_type
        safe = "".join(character if character.isalnum() or character in "-_ " else "" for character in title)
        safe = safe[:50].strip().replace(" ", "_")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        file_name = f"{safe}_{timestamp}"

    content_dir = get_env(
        "OPEN_PHARMA_TRAINING_CONTENT_DIR",
        str(Path.home() / ".open-pharma-plugins" / "training-content"),
    )
    output_dir = ensure_private_dir(Path(content_dir) / "outputs")

    try:
        json_path = contained_path(output_dir, f"{file_name}.json")
        html_path = contained_path(output_dir, f"{file_name}.html")
    except ValueError as exc:
        return [{"type": "text", "text": json.dumps({"error": str(exc)})}]

    atomic_write_json(json_path, data)
    atomic_write_text(html_path, RENDERERS[output_type](data))

    result = {
        "success": True,
        "output_type": output_type,
        "output_dir": str(output_dir),
        "json_path": str(json_path),
        "html_path": str(html_path),
    }
    return [{"type": "text", "text": json.dumps(result, indent=2)}]
