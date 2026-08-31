# ローカル開発

リポジトリルートからコマンドを実行してください。ロックファイルは Python 3.10–3.13 を対象とし、
完全なプラグインインストールテストには専用クローンを使用します。

## 高速ソースループ

ロック済みの開発環境を導入します：

```bash
uv sync --all-extras --dev --locked
```

ソースから直接サーバーを実行します：

```bash
uv run python -m open_pharma_plugins_territory_alignment --version
uv run python -m open_pharma_plugins_territory_alignment --check-system
```

コード変更は次回のプロセス起動時に反映されます。開発中は対象を絞ったテストを実行してください。
詳細は [テスト](testing.md) を参照してください。

ライブ MCP 接続のみが必要な場合は、ハーネスにソースエントリを登録し、編集後に再接続します：

```bash
claude mcp add open-pharma-plugins-territory-alignment -- \
  uv run --directory "$(pwd)" python -m open_pharma_plugins_territory_alignment
# クリーンアップ: claude mcp remove open-pharma-plugins-territory-alignment
```

## 完全プラグインインストールループ

マーケットプレイスマニフェスト、Skill 検出、MCP 登録、ハーネスの完全なインストールフローを
テストする場合はこのパスを使用してください：

```bash
bash install.sh local
```

インストーラーは選択された機能を現在のチェックアウトに向け、`uvx --refresh` を追加します。
追跡対象マニフェストに絶対ローカルパスが意図的に書き込まれるため、専用クローンを使用し、
インストール中は移動しないでください。

コミットまたはローカルモード終了前にリリースソースを復元します：

```bash
bash install.sh local --restore
```
