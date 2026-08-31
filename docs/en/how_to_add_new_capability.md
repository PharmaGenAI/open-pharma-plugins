# Adding a Capability

Start from the smallest existing capability with a similar dependency profile. There is no generated example capability in this repository.

## Structure

```text
src/capabilities/<name>/
├── .claude-plugin/plugin.json  # shared by Claude Code and GitHub Copilot
├── .codex-plugin/plugin.json
├── .mcp.json
├── skill/SKILL.md
└── open_pharma_plugins_<name>/
    ├── __init__.py
    ├── __main__.py
    └── tools/
        └── my_tool.py
```

Each tool module exports a Pydantic argument model, `TOOL`, and `handle`:

```python
from pydantic import BaseModel, Field


class MyToolArgs(BaseModel):
    query: str = Field(description="Search query")


TOOL = {"name": "my_tool", "description": "...", "args": MyToolArgs}


def handle(arguments: dict) -> list[dict]:
    return [{"type": "text", "text": arguments["query"]}]
```

The generic `__main__.py` must expose the callable used by the console entry and remain import-safe:

```python
from pathlib import Path

from mcp_framework import run_main


def main() -> None:
    run_main(__package__ or Path(__file__).resolve().parent.name)


if __name__ == "__main__":
    main()
```

## Registration checklist

1. Add the capability extra and include its dependencies in `all`.
2. Add the console entry, package-dir mapping, package discovery path, and package data to `pyproject.toml`.
3. Add the capability version to `plugin-versions.json` and tag-pinned entries to both `.claude-plugin/marketplace.json` and `.github/plugin/marketplace.json`. The Copilot entry points to the same capability directory and reuses its `.claude-plugin/plugin.json` manifest.
4. Copy and update all three capability manifests.
5. Add the Skill, cookbook, handler tests, entry-point/protocol coverage, and any fixtures.
6. Add user-facing settings to `shared.env.CONFIG_FIELDS` and mirror them in `install.sh`.
7. Use `shared.filesystem` for contained paths and private atomic writes.
8. Run the full [Testing](testing.md) and [Releasing](releasing.md) gates.

Use `shared.env.get_env` instead of reading the environment directly. Declare every direct Python dependency even if another package currently installs it transitively.
