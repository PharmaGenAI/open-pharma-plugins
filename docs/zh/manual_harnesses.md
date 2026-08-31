# Claude Code 直接设置

默认情况下请使用 [引导式安装程序](installation.md#引导式安装程序)。本页介绍桌面应用 UI 以及
直接 Skill + MCP 注册方法。

## 直接 Skill + MCP 注册

将 `<cap>` 替换为 `hcp-intelligence`、`field-training`、`campaign-studio`、
`next-best-engagement`、`territory-alignment` 或 `competitive-intelligence`。
使用同一不可变标签用于 Skill 和 MCP 命令：

```text
open-pharma-plugins-<cap>-v<version>
```

### 注册功能

```bash
ln -s /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  ~/.claude/skills/open-pharma-plugins-<cap>

claude mcp add open-pharma-plugins-<cap> -- \
  uvx --from \
  "open-pharma-plugins[<cap>] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-<cap>-v<version>" \
  open-pharma-plugins-<cap>
```

本地源码开发时，将 Git 包规格替换为 `/path/to/open-pharma-plugins[<cap>]`。

### 更新

检出新的不可变功能标签，将复制/链接的 Skill 和 MCP Git 引用都替换为该标签，然后运行
`/reload-plugins` 或重启 Claude Code。
