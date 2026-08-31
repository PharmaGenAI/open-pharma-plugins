# Open Pharma Plugins

Agent Skills and MCP servers for pharmaceutical commercial operations.

## Architecture

<p align="center"><img src="docs/assets/architecture.svg" width="960" alt="Open Pharma Plugins architecture"></p>

Each capability installs independently as a Skill plus an MCP server. The Python distribution contains the servers; Skills, cookbooks, and marketplace manifests are installed from this repository.

## Install

The guided installer supports Claude Code, Codex, and GitHub Copilot CLI. It requires `uv`/`uvx` and uses independent, immutable capability tags.

Download and inspect the installer before running it:

```bash
curl -fsSLO https://raw.githubusercontent.com/PharmaGenAI/open-pharma-plugins/main/install.sh
less install.sh
bash install.sh
```

Update capabilities already installed in a harness:

```bash
bash install.sh update
```

To install MCP servers without the companion Skills, use one or more extras from the Python distribution:

```bash
python -m pip install "open-pharma-plugins[territory-alignment,competitive-intelligence]"
```

See [Installation](docs/en/installation.md) for tagged releases, local checkouts, rollback, and manual setup.

## Capabilities

| Capability | Tools | Use case |
|---|---:|---|
| [HCP Intelligence](cookbooks/hcp-intelligence/usage.md) | 11 | Build evidence-backed HCP/HCO profiles from public sources |
| [Field Training](cookbooks/field-training/usage.md) | 5 | Create grounded learning packages, assessments, role-play kits, and scorecards from approved documents |
| [Campaign Studio](cookbooks/campaign-studio/usage.md) | 16 | Draft campaigns, validate claims, render assets, and prepare MLR review packages |
| [Next-Best-Engagement](cookbooks/next-best-engagement/usage.md) | 3 | Score HCPs and produce consent-aware engagement plans |
| [Territory Alignment](cookbooks/territory-alignment/usage.md) | 6 | Compare HCP-to-rep assignments and plan visit clusters |
| [Competitive Intelligence](cookbooks/competitive-intelligence/usage.md) | 12 | Collect evidence and produce reproducible briefings and timelines |

## Try it

After installing a capability, reference your files and ask naturally. The Skill selects the relevant MCP tools.

```text
@speakers.csv       Build evidence-backed profiles for these HCPs.
@training.pdf       Create a learning package and assessment from this approved document.
@brief.md           Draft a campaign and prepare it for MLR review.
@alignment.csv      Compare assignments and identify workload imbalances.
```

## Requirements and configuration

- Python 3.10–3.13
- `uv`/`uvx` for the guided plugin installer
- Provider credentials only for the capabilities and external services you use

Run the installer's **Configure** and **Verify** actions to set credentials and check dependencies. Shared configuration lives in `~/.open-pharma-plugins/config`.

See [Configuration](docs/en/configuration.md) and [Data security](docs/en/data_security.md) for settings, local data, provider calls, and compliance boundaries.

## Documentation

- [Installation](docs/en/installation.md) · [Configuration](docs/en/configuration.md)
- [Local development](docs/en/local_development.md) · [Testing](docs/en/testing.md)
- [Contributing](CONTRIBUTING.md) · [Adding a capability](docs/en/how_to_add_new_capability.md)
- [Security policy](SECURITY.md) · [Release guide](docs/en/releasing.md)
- [中文文档](docs/zh/) · [日本語ドキュメント](docs/jp/)
- [Project website](https://pharmagenai.github.io/)

## License

Apache-2.0. See [LICENSE](LICENSE).
