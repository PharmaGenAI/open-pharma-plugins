from mcp_framework import build_registry

__version__ = "1.1.1"

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

USAGE_NOTE = (
    "Field Training turns approved PDF/PPTX file paths into source-grounded "
    "learning packages, assessments, pre-session role-play kits, and "
    "post-session scorecards for field reps."
)

SYSTEM_DEPS = []
