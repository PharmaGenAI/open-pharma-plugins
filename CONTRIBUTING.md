# Contributing

Thanks for contributing to Open Pharma Plugins. Keep each change focused on a concrete problem. Discuss new capabilities and breaking MCP interface changes in an issue first.

## Development setup

Python 3.10–3.13 is supported. From the repository root:

```bash
uv sync --all-extras --dev --locked
```

Use `uv run` for repository commands so tests run against the locked development environment. See [Local development](docs/en/local_development.md) and [Adding a capability](docs/en/how_to_add_new_capability.md).

## Change rules

- Keep capability code under `src/capabilities/<name>/`; share only genuinely common behavior through `src/shared/` or `src/mcp_framework.py`.
- Preserve public MCP tool names and schemas unless a compatibility break is intentional and documented.
- Read configuration through `shared.env.get_env` and add user-facing fields to `CONFIG_FIELDS` plus the installer mirror.
- Use `shared.filesystem` for mutable runtime data. Never write into site-packages or the repository by default.
- Import optional dependencies lazily and declare every direct dependency in `pyproject.toml`.
- Never commit credentials, personal data, private source documents, or generated operational artifacts.
- Add a regression test before fixing a defect, and update the relevant Skill and cookbook when behavior changes.

## Verification

Run targeted tests during development, then the release-facing local gates:

```bash
uv run --all-extras pytest -m "not reachability"
uv run ruff format --check .
uv run ruff check .
uv run python scripts/check_manifests.py
uv run python scripts/gen_env_docs.py --check docs/en/configuration.md
uv run zizmor --pedantic .github/workflows
bash -n install.sh
uv build
uv run twine check dist/*
uv run python scripts/smoke_wheel.py dist/*.whl
```

External-provider tests are opt-in. State any unavailable credential, platform, or service in the pull request rather than weakening an offline test.

## Pull requests

Describe the problem and the reason for the chosen fix, link related issues,
and include the commands and results used for verification. Keep unrelated
changes in separate PRs.

Report security issues according to [SECURITY.md](SECURITY.md), not through a
public issue. Contributions are licensed under the repository's Apache-2.0
license.

Maintainers: follow [Plugin releases](docs/en/releasing.md) after a release PR merges.
