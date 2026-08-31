# テスト

テストは動作別に整理されています：

- **ユニットおよびハンドラーテスト** はスキーマ、ツール検出、変換、エラーパスをカバーします。
- **プロトコルテスト** は実際の MCP クライアントを使用して initialize → `tools/list` → `tools/call` を検証します。
- **リポジトリ整合性テスト** は重複マニフェスト、リリース参照、パッケージバージョン、同期ソースファイルを保護します。
- **到達性テスト** は外部プロバイダーを呼び出すオプトイン項目です。

`tests/conftest.py` は `src/` とサーバー機能ディレクトリを `sys.path` に追加するため、新しい MCP
サーバーパッケージは wheel をインストールせずに検出されます。

## コマンド

CI で使用されるオフラインテストスイートを実行します：

```bash
uv sync --all-extras --dev --locked
uv run pytest -m "not reachability"
uv run python scripts/check_manifests.py
uv run python scripts/gen_env_docs.py --check docs/en/configuration.md
uv run ruff format --check .
uv run ruff check .
uv run zizmor --pedantic .github/workflows
bash -n install.sh
```

ソースツリーだけでなく、実際の配布物も検証します：

```bash
uv build
uv run twine check dist/*
uv run python scripts/smoke_wheel.py dist/*.whl
```

Competitive Intelligence のプロバイダーテストは、秘匿化された記録済みフィクスチャを使用し、
オフラインで実行します。実行と出力のテストは、1 回の収集、不変データのハッシュ検証付き読み込み、
カバレッジの意味、数式安全な CSV、DOM 安全なレポートとタイムラインを検証します。

設定ガイドを変更する場合は、`CONFIG_FIELDS`、`install.sh`、`.env.example`、3 言語の設定ページを
同期させます。設定例のコメントは値と別の行に置いてください。

ライブ資格情報の診断ではキーを記録しません。プロバイダーのカバレッジ、安全なエラーコード、
資格情報なしの要求を比較します。別のソースが成功しても、失敗したソースは不確定です。

ライブリクエストを行う意図があり、資格情報が設定されている場合にのみ、プロバイダーテストを
実行します：

```bash
OPEN_PHARMA_RUN_REACHABILITY=1 uv run pytest -m reachability tests/integration/test_reachability.py
```

シェルの変更については `bash -n <script>` も実行してください。開発中は関連するテストモジュールを
対象にするか、`-k <pattern>` を使用してから、完全なオフラインスイートを実行してください。

## テスト対象

機能のコンポーネントに合わせてテストを作成します：

1. すべての MCP サーバーにはスキーマ/検出とハンドラーの成功/エラーカバレッジが必要です。
2. サーバー固有の起動、ストリーミング、トランスポート動作のプロトコルテストを追加します。
3. リーダーやレンダラーに代表的な入力が必要な場合は、小さな決定論的なフィクスチャをコミットします。
4. 2つのファイルまたはマニフェストフィールドが同期を維持する必要がある場合、ドリフト防止アサーションを追加します。
5. Skill のみの変更では、frontmatter、参照リソース、マニフェストパッケージングを検証する必要があります。

デフォルトのオフラインスイートでは、資格情報、GUI アプリケーション、GPU ハードウェア、
パブリックネットワーク到達性を要求してはなりません。

## HCP バッチ検証

HCP バッチのユニットテストはオフラインです。CSV 検証、パス/出力の事前検証、
プロバイダー要求契約、再開、schema-v2 マニフェスト、安定した数式安全化済みサマリー CSV を
検証します：

```bash
uv run --all-extras python -m pytest -q \
  tests/capabilities/test_hcp_batch.py \
  tests/capabilities/test_hcp_batch_csv.py
```

明示的なリポジトリチェックアウトでは、同梱フィクスチャを使ってソースラッパーのプロバイダー呼び出し
なしの事前検証も実行できます：

```bash
uv run --all-extras python scripts/batch_enrich.py \
  --input-file src/capabilities/hcp-intelligence/open_pharma_plugins_hcp_intelligence/fixtures/sample_accounts.csv \
  --output-dir data/hcp-intelligence \
  --dry-run
```

これはチェックアウト専用であり、インストール済み marketplace キャッシュのパスとして説明しては
いけません。固定されたインストール済みコマンドとライブプロバイダー境界は
[HCP バッチ処理](hcp_batch.md)を参照してください。

エビデンス実行と openFDA のトラブルシューティング境界は、
[コンペティティブインテリジェンス利用ガイド](competitive_intelligence.md)を参照してください。
