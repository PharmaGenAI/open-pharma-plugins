from mcp_framework import build_registry

__version__ = "1.0.2"

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

USAGE_NOTE = (
    "HCP Intelligence builds evidence-backed profiles for healthcare professionals "
    "and organizations. Call the search tools to gather data from PubMed, "
    "ClinicalTrials.gov, and the web, then synthesize a structured profile "
    "following the schema in the Skill."
)

SYSTEM_DEPS = []
