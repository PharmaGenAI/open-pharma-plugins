# 新しい機能/プラグインの追加

`src/capabilities/` の下に、各プラグインは `skill/`（Agent Skill）および/または
`<import_name>/`（MCP サーバーパッケージ）を含む短いディレクトリです。どちらもオプションです。

## 構造

```
src/capabilities/<yourname>/
├── .claude-plugin/plugin.json      # Claude と GitHub Copilot 共用のハーネスマニフェスト
├── .codex-plugin/plugin.json       # Codex ハーネスマニフェスト
├── .mcp.json                       # MCP サーバー起動設定（サーバー機能のみ）
├── skill/SKILL.md                  # Agent Skill（frontmatter: name/description + 本文）
└── open_pharma_plugins_<yourname>/  # MCP サーバーパッケージ
    ├── __init__.py                 # __version__ + build_registry + SYSTEM_DEPS
    ├── __main__.py                 # 汎用エントリシム（任意のサーバーからそのままコピー）
    └── tools/                      # .py ファイルごとに1つのツール、TOOL + handle をエクスポート、起動時に自動検出
        └── my_tool.py
```

## ツール規約（自動検出）

`tools/` の下に新しい `.py` ファイルを作成し、2つだけエクスポートします：

```python
from pydantic import BaseModel, Field


class MyToolArgs(BaseModel):
    query: str = Field(description="検索クエリ。")


TOOL = {"name": "my_tool", "description": "...", "args": MyToolArgs}


def handle(arguments: dict) -> list[dict]:
    ...
    return [{"type": "text", "text": ...}]
```

- `args` は Pydantic モデルで、ツールの `inputSchema` を自動生成し、各呼び出しを検証します。
  `handle` はプレーンな dict を受け取り、MCP コンテンツブロック（`text` / `image`）を返します。
- 遅延インポート：重い依存関係は `handle` 内でインポートし、他のツールに影響を与えないようにします。

## 登録手順

既存の機能ディレクトリを `src/capabilities/<yourname>/` にコピーし、パッケージディレクトリを
リネームしてから：

1. `pyproject.toml` `[project.scripts]` — エントリを追加：
   ```toml
   open-pharma-plugins-<yourname> = "<import_name>.__main__:main"
   ```
2. `pyproject.toml` `[project.optional-dependencies]` — extra グループを追加：
   ```toml
   <yourname> = ["...依存関係..."]
   ```
3. `pyproject.toml` `[tool.setuptools] package-dir` — インポート名をディレクトリにマッピング：
   ```toml
   "<import_name>" = "src/capabilities/<yourname>/<import_name>"
   ```
4. `pyproject.toml` `[tool.setuptools.packages.find] where` — 機能ディレクトリを追加
5. `plugin-versions.json` に初期バージョンを追加し、`.claude-plugin/marketplace.json` と
   `.github/plugin/marketplace.json` の両方にタグ固定エントリを追加する。Copilot エントリは機能内の Claude マニフェストを再利用する
6. `scripts/check_manifests.py` を実行し、[プラグインリリース](releasing.md) に従って最初のタグを作成

`__main__.py` は既存の機能から **そのままコピー** し、コンソールエントリから呼び出せる `main()` を
公開すると同時に、インポート時にサーバーを起動しない形を維持します。

## 共有ライブラリコードの再利用

`src/shared/` の共有ライブラリ：

- `shared.env` — 設定 + `get_env`（環境変数を読み取る単一エントリ。優先順位：環境変数 > 設定ファイル > デフォルト値）
- `shared.filesystem` — パス包含検証、プライベートディレクトリ、アトミックな `0600` ファイル書き込み

ランタイム設定には `os.environ` ではなく `from shared.env import get_env` を使用してください。可変データを
リポジトリや site-packages に既定で書き込まず、`shared.filesystem` で包含されたパスとプライベートな
アトミック書き込みを使います。新ツールはエントリポイント、実 MCP プロトコル、隔離 wheel の
スモークテストにも追加してください。
