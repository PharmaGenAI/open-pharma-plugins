from mcp_framework import build_registry

__version__ = "1.0.2"

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

USAGE_NOTE = (
    "Next-Best-Engagement recommends which HCPs to engage, through which "
    "channel, by which rep, subject to visit capacity, explicit consent, "
    "and minimum-gap constraints. Load an HCP universe (CSV or built-in fixtures), then "
    "generate an optimised engagement plan."
)

SYSTEM_DEPS = []
