# Other harness setup

Use the [guided installer](installation.md#guided-installer) by default. This page covers
direct Skill + MCP registration for harnesses without a compatible marketplace.

## Direct Skill + MCP registration

Replace `<cap>` with `hcp-intelligence`, `field-training`, `campaign-studio`,
`next-best-engagement`, `territory-alignment`, or `competitive-intelligence`.
Use one immutable tag for both the Skill and MCP command:

```text
open-pharma-plugins-<cap>-v<version>
```

### Register the capability

```bash
ln -s /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  ~/.claude/skills/open-pharma-plugins-<cap>

claude mcp add open-pharma-plugins-<cap> -- \
  uvx --from \
  "open-pharma-plugins[<cap>] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-<cap>-v<version>" \
  open-pharma-plugins-<cap>
```

For local source, replace the Git package spec with `/path/to/open-pharma-plugins[<cap>]`.

### Update

Check out the new immutable capability tag and replace both the copied/linked Skill and the MCP Git
ref with that tag, then run `/reload-plugins` or restart Claude Code.
