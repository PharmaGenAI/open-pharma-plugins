# Installation

Python 3.10–3.13 is supported. The guided plugin installer also requires `uv`/`uvx`; install it separately from the [official uv instructions](https://docs.astral.sh/uv/getting-started/installation/) and verify `uvx --version` first.

## Choose the install surface

| Need | Install surface | Includes |
|---|---|---|
| MCP servers only | Python distribution | Server code and runtime fixtures |
| Skill + MCP plugin | Tagged repository plugin | Skill, manifests, and tag-pinned MCP command |
| Unpublished development code | Local checkout | Current files, including uncommitted changes |

The Python wheel does not contain Skills, cookbooks, or marketplace manifests.

## Python distribution

After a release is published:

```bash
python -m pip install "open-pharma-plugins[hcp-intelligence]"
open-pharma-plugins-hcp-intelligence --version
```

Install every optional runtime with `open-pharma-plugins[all]`, or combine only the capability extras you need.

## Guided Skill + MCP installer

Download and inspect the script instead of piping remote code into a shell:

```bash
curl -fsSLO https://raw.githubusercontent.com/PharmaGenAI/open-pharma-plugins/main/install.sh
less install.sh
bash install.sh
```

The menu installs, updates, configures, verifies, or uninstalls one capability at a time for Claude Code, Codex, or GitHub Copilot CLI. It uses each capability's immutable release tag. If `uvx` is missing, the installer stops and links to the official installation instructions; it does not install `uv` itself.

For GitHub Copilot, install and authenticate Copilot CLI first, then select **copilot** in the harness menu. The installer registers the repository's `.github/plugin/marketplace.json` catalog and installs the selected capability through Copilot's native plugin commands. Copilot reuses each capability's `.claude-plugin/plugin.json` manifest, so its Skill and optional MCP server stay aligned with the Claude package.

### Roll back

Select the capability matching the exact tag:

```bash
OPP_REF=open-pharma-plugins-territory-alignment-v<version> bash install.sh install
```

## Local checkout

```bash
git clone https://github.com/PharmaGenAI/open-pharma-plugins.git
cd open-pharma-plugins
git switch <development-branch>
bash install.sh local
```

Local mode rewrites tracked manifests with absolute paths. Use a dedicated clone and restore release sources before committing:

```bash
bash install.sh local --restore
```

## Manual Skill + MCP registration

Keep the Skill directory, Python extra, console entry, and immutable tag aligned:

```bash
uvx --from \
  "open-pharma-plugins[<cap>] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-<cap>-v<version>" \
  open-pharma-plugins-<cap>
```

Copy or link `src/capabilities/<cap>/skill` into the host's Skill directory. See [Manual harness setup](manual_harnesses.md) for examples.

### Installed HCP batch console

HCP Intelligence `1.0.2` includes the packaged `open-pharma-plugins-hcp-batch` console. Its optional
synthesis dependencies use the `hcp-intelligence-synth` extra. Run it from the immutable HCP tag;
do not expect the repository-only `scripts/batch_enrich.py` wrapper in a marketplace cache:

```bash
uvx --from \
  "open-pharma-plugins[hcp-intelligence-synth] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-hcp-intelligence-v1.0.2" \
  open-pharma-plugins-hcp-batch --help
```

See [HCP batch processing and CSV review](hcp_batch.md) before supplying operational paths or
enabling provider calls.

## Verify

```bash
open-pharma-plugins-<cap> --version
open-pharma-plugins-<cap> --check-system
```

For release verification, also compare the downloaded artifact to the release's `SHA256SUMS`, review the SBOM, and verify GitHub build provenance.
