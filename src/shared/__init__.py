"""`shared` — reusable library for open-pharma-plugins capabilities.

A plain library at the src/ root (peer to mcp_framework), importable by any
capability from source AND the wheel — it is NOT a server/capability (no __main__, no
tools, no console entry). Submodules:

  - shared.env           config + get_env (the single call-time entry for reading env vars)

Reuse these across capabilities instead of importing a sibling capability's server package
(that only resolves once installed, and couples the servers together).
"""
