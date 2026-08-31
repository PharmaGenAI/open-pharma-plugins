from mcp_framework import build_registry

__version__ = "1.1.0"

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

USAGE_NOTE = (
    "Competitive Intelligence tracks competitor trial pipelines, FDA "
    "regulatory events, label changes, news, and publications. Maintains "
    "a persistent watchlist and generates shareable briefing reports."
)

SYSTEM_DEPS = []
