# Claude Code の直接セットアップ

デフォルトでは [ガイド付きインストーラー](installation.md#ガイド付きインストーラー) を使用して
ください。このページでは、デスクトップアプリ UI と直接 Skill + MCP 登録の方法を説明します。

## 直接 Skill + MCP 登録

`<cap>` を `hcp-intelligence`、`field-training`、`campaign-studio`、
`next-best-engagement`、`territory-alignment`、`competitive-intelligence` のいずれかに
置き換えてください。Skill と MCP コマンドの両方に同じ不変タグを使用します：

```text
open-pharma-plugins-<cap>-v<version>
```

### 機能を登録する

```bash
ln -s /path/to/tagged-checkout/src/capabilities/<cap>/skill \
  ~/.claude/skills/open-pharma-plugins-<cap>

claude mcp add open-pharma-plugins-<cap> -- \
  uvx --from \
  "open-pharma-plugins[<cap>] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-<cap>-v<version>" \
  open-pharma-plugins-<cap>
```

ローカルソースの場合、Git パッケージ仕様を `/path/to/open-pharma-plugins[<cap>]` に
置き換えてください。

### 更新

新しい不変の機能タグをチェックアウトし、コピー/リンクされた Skill と MCP Git 参照の両方を
そのタグに置き換えてから、`/reload-plugins` を実行するか Claude Code を再起動してください。
