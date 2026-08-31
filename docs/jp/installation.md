# インストール

## ソースの選択

| 目的 | コマンド | ソース |
|---|---|---|
| リリース版のインストール | `bash install.sh install` | 各機能の最新リリースタグ |
| 既存インストールの更新 | `bash install.sh update` | 現在のリリースカタログ |
| 未公開コードのテスト | `bash install.sh local` | 現在のチェックアウト（未コミットの変更を含む） |
| 単一機能のロールバック | [ロールバック](#ロールバック) を参照 | 正確な不変タグ |

リリースインストールは `main` ブランチを追跡しません。ブランチをテストするには、チェックアウト後に
`local` を使用してください。

## ガイド付きインストーラー

Python ディストリビューションには6つの MCP サーバーと実行時フィクスチャのみが含まれます。
Skill、cookbook、マーケットプレイスマニフェストはタグ付きリポジトリプラグインから導入します。
ガイド付きインストーラーは Claude Code、Codex、GitHub Copilot CLI に対応し、各ハーネスのネイティブ機構を使って共有設定を
`~/.open-pharma-plugins/config` に保存します。`uv`/`uvx` は事前に導入してください。
インストーラーがパッケージマネージャーを自動導入することはありません。

スクリプトをダウンロードして確認してから実行します：

```bash
curl -fsSLO https://raw.githubusercontent.com/PharmaGenAI/open-pharma-plugins/main/install.sh
less install.sh
bash install.sh
```

メニューは **Install**、**Update**、**Configure**、**Verify**、**Uninstall** を提供します。
各機能は Skill とオプションの MCP サーバーとして個別にインストールされます。

GitHub Copilot を使用する場合は、先に Copilot CLI をインストールして認証し、ハーネスメニューで
**copilot** を選択します。インストーラーは `.github/plugin/marketplace.json` を登録し、Copilot の
ネイティブプラグインコマンドで選択した機能を導入します。Copilot は各機能の
`.claude-plugin/plugin.json` を再利用するため、Skill とオプションの MCP サーバーは Claude パッケージと一致します。

### 更新

最新のリリースカタログを含むスクリプトを再取得し、確認してください：

```bash
curl -fsSLO https://raw.githubusercontent.com/PharmaGenAI/open-pharma-plugins/main/install.sh
less install.sh
bash install.sh update
```

### ロールバック

リモートタグを受け入れるハーネスの場合、タグで指定された機能のみを選択します：

```bash
OPP_REF=open-pharma-plugins-territory-alignment-v<version> bash install.sh install
```

## ローカルチェックアウト

パスが安定した専用クローンを使用してください：

```bash
git clone https://github.com/PharmaGenAI/open-pharma-plugins.git
cd open-pharma-plugins
git switch <開発ブランチ>   # オプション
bash install.sh local
```

ローカルモードでは、選択されたプラグインマニフェストと MCP パッケージ仕様が現在のチェックアウトを
指し、`uvx --refresh` が追加されます。ローカルモードを終了する際にリリースソースを復元します：

```bash
bash install.sh local --restore
```

詳細は [ローカル開発](local_development.md) を参照してください。

## 手動 Skill + MCP インストール

互換性のないハーネスの場合、以下の3つの値を一致させてください：

- Skill: `src/capabilities/<cap>/skill`
- パッケージ extra: `open-pharma-plugins[<cap>]`
- エントリ: `open-pharma-plugins-<cap>`

```bash
uvx --from \
  "open-pharma-plugins[<cap>] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-<cap>-v<version>" \
  open-pharma-plugins-<cap>
```

### インストール済み HCP バッチコンソール

HCP Intelligence `1.0.2` にはパッケージ済み `open-pharma-plugins-hcp-batch` コンソールが含まれます。
オプションの合成依存関係には `hcp-intelligence-synth` extra を使用します。不変 HCP タグから実行し、
marketplace キャッシュにリポジトリ専用 `scripts/batch_enrich.py` ラッパーがあると想定しないでください：

```bash
uvx --from \
  "open-pharma-plugins[hcp-intelligence-synth] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-hcp-intelligence-v1.0.2" \
  open-pharma-plugins-hcp-batch --help
```

運用パスの指定やプロバイダー呼び出しを有効化する前に、
[HCP バッチ処理と CSV レビュー](hcp_batch.md)を参照してください。

MCP サーバーだけが必要な場合は、公開済み Python ディストリビューションから導入できます：

```bash
python -m pip install "open-pharma-plugins[hcp-intelligence]"
open-pharma-plugins-hcp-intelligence --version
```

リリース検証では、ダウンロードしたファイルを `SHA256SUMS` と照合し、SBOM とビルド来歴も
確認してください。

## 依存関係

`uvx` は各機能の Python 依存関係を分離されたキャッシュにインストールします。

### 一般的なサービス設定

| 変数 | 用途 |
|---|---|
| `SERPER_API_KEY` | Serper Web 検索（HCP インテリジェンスとコンペティティブインテリジェンスで使用） |
| `TAVILY_API_KEY` | Tavily Web 検索 |
| `EXA_API_KEY` | Exa Web 検索 |
| `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` | オプションの HCP バッチプロファイル合成 |

インストーラーの **Configure** アクション、シェル環境、または `~/.open-pharma-plugins/config`
で値を設定します。環境変数が優先されます。

### 完全な設定

[設定リファレンス](configuration.md) を参照してください。
