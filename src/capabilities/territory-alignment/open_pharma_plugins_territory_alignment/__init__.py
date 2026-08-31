from mcp_framework import build_registry

__version__ = "1.2.0"

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

USAGE_NOTE = (
    "Territory Alignment assigns HCPs to representatives (strategic mode) "
    "and plans visit routes (operational mode), balancing workload, travel, "
    "relationship continuity, and priority coverage. Supports named scenario "
    "comparison for what-if planning."
)

SYSTEM_DEPS = []
