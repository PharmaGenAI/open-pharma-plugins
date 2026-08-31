from mcp_framework import build_registry

__version__ = "1.1.0"

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

USAGE_NOTE = (
    "Campaign Studio creates end-to-end pharmaceutical campaign materials: "
    "structured briefs, audience journeys, message architectures, channel copy, "
    "rendered assets (email HTML, banner SVG, poster PDF) with claim validation "
    "and MLR review packaging."
)

SYSTEM_DEPS = []
