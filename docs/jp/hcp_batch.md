# HCP バッチ処理と CSV レビュー

営業ユーザーがアカウント CSV と出力ディレクトリのパスを指定した場合は、パッケージ済み HCP
バッチコンソールを使用します。インストール済みワークフローはリポジトリのチェックアウトに依存せず、
HCP Intelligence `1.0.2` のソースに固定されています。

## 入力と事前検証

CSV ヘッダーは次のとおりです：

```csv
id,name,specialty,country,account_type,institution
```

6列すべてが必要です。`id`、`name`、`country`、`account_type` は空にできず、`specialty` と
`institution` は空でも構いません。ID は一意で、制御文字を含まず、ファイル名として安全である必要が
あります。`account_type` は `HCP` または `HCO` です。BOM の有無を問わず UTF-8 を受け付けます。

ユーザー指定の両パスを絶対パスへ解決し、制御文字を拒否して、それぞれを1つの引用符付き Shell 引数
として扱います。コマンド置換や `eval` にパスを入れてはいけません。ユーザーが実際に指定した
フィルターだけを保持します。`--ids` の各 ID は個別の引用符付き引数にし、指定された `--country` と
`--account-type` の値をそのまま保持します。以下は3種類すべてを指定した例で、必ず dry run から
開始します：

```bash
uvx --from \
  "open-pharma-plugins[hcp-intelligence-synth] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-hcp-intelligence-v1.0.2" \
  open-pharma-plugins-hcp-batch \
  --input-file "/absolute/path/accounts.csv" \
  --output-dir "/absolute/path/results" \
  --ids "HCP-SG-001" "HCP-SG-002" \
  --country "Singapore" \
  --account-type "HCP" \
  --dry-run
```

Dry run はファイル全体と出力先を検証し、選択された ID、総数、HCP/HCO 内訳、プロバイダー/モデル
設定、予定される成果物を表示します。外部呼び出しは行わず、出力ディレクトリも作成しません。表示
された ID、総数、HCP/HCO 内訳を、件数だけでなくユーザー指定の範囲と照合してください。フィルター
の欠落や変更、余分または不足した ID、件数や種類内訳の不一致があれば実行を停止し、コマンドを修正
して新しい dry run を行います。範囲が完全に一致した後に限り、ユーザーが実行を明示的に依頼し、
選択件数が10件以下なら続行できます。パスだけを指定された場合は確認が必要です。10件を超える場合は
プロバイダー呼び出しへの明示的承認が必要ですが、元の依頼に明確な事前承認があれば、その承認で要件を
満たします。

## 承認済みの合成実行

`OPENROUTER_API_KEY` を設定し、対象データをプロバイダーへ送信できることを確認します。検証済みの
dry-run コマンドをコピーし、`--dry-run` だけを削除して、`--synthesize`、`--resume`、
`--concurrency 3` を追加します。ソース、パス、フィルターは引数単位で完全に一致させます：

```bash
uvx --from \
  "open-pharma-plugins[hcp-intelligence-synth] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-hcp-intelligence-v1.0.2" \
  open-pharma-plugins-hcp-batch \
  --input-file "/absolute/path/accounts.csv" \
  --output-dir "/absolute/path/results" \
  --ids "HCP-SG-001" "HCP-SG-002" \
  --country "Singapore" \
  --account-type "HCP" \
  --synthesize \
  --resume \
  --concurrency 3
```

`uvx` が固定ソースを導入できない場合、`main`、別タグ、未固定パッケージ、またはチェックアウトへ
暗黙に切り替えてはいけません。生エビデンス収集は同じコンソールから `--synthesize` を省略して実行
でき、LLM キーは不要です。ただし Web 検索には Serper、Tavily、Exa のいずれかの設定が必要です。

## 出力契約

選択された各アカウントには正規の `<id>.json` エビデンス成果物があります。`batch_summary.csv` は
営業レビュー用の投影であり、正規データストアではありません。安定した schema version 1 は、次の
27列をこの順序で含みます：

```text
account_id, account_type, input_name, input_specialty, input_country,
input_institution, status, profile_validated, profile_completeness,
profile_name, profile_specialty, profile_country, current_title,
organization_type, affiliations, qualifications, research_or_clinical_focus,
professional_roles, key_publication_count, clinical_trial_count,
active_grant_count, congress_activity_count, source_count, source_urls,
tools_failed, error, json_file
```

CSV は表計算ソフトとの互換性のため UTF-8 BOM と CRLF レコードを使用します。複数値は元の順序で
重複を除き、` | ` で連結します。`=`、`+`、`-`、`@` で始まるすべてのテキストセルには、数式実行を
無効化するためアポストロフィを付けます。検証済みプロファイルがある数値項目は数値のまま保持し、利用
できない値は空欄です。フラット化された値を利用する前に、参照先 JSON とソースを確認してください。

`batch_manifest.json` は schema version 2 です。入力パスと SHA-256、時刻、実際の合成エンドポイント/
モデル/reasoning/タイムアウト/並列数、選択件数とステータス件数、各アカウントの成果物状態、CSV の
状態/パス/schema/行数/SHA-256 を記録します。API キーとアカウント名の重複は保存しません。

## 出力先と再開の安全性

出力先はディレクトリでなければなりません。新規または空のディレクトリは使用できますが、空でない
ディレクトリには `--resume` が必要です。POSIX 権限を利用できる場合、新規ディレクトリは `0700`、
ファイルは `0600` です。再開時は利用可能なアカウント JSON をスキップし、必要な合成では生エビデンス
を再利用し、破損・無効な成果物を再処理します。無関係なファイルは保持しますが、必要に応じて選択した
`<id>.json` を原子的に置換し、`batch_summary.csv` と `batch_manifest.json` を再生成します。

生成済み CSV、JSON、マニフェストへ手動レビュー注記を直接追加しないでください。再開実行ではその編集
を保持しません。CSV を別のレビュー用ブックまたは記録システムへコピーしてください。

## 完了状態と終了コード

プロセス終了まで監視し、completed、partial、failed、skipped の件数と、出力ディレクトリ、サマリー
CSV、マニフェストの絶対パスを報告します。

- 終了 `0`：dry run、または partial/failed アカウントと CSV 出力失敗がなく完了。フィルター結果が
  空の場合も `0` で、成果物は作成しません。
- 終了 `1`：partial/failed アカウント、または CSV 出力失敗があります。利用可能な JSON と
  schema-v2 マニフェストは、確認と意図的な再開のため保持されます。
- 終了 `2`：無効な CSV、安全でないパス、無効な値、空でない出力先に `--resume` がない場合など、
  コマンドラインまたは事前検証の使用エラーです。プロバイダー呼び出しは行われません。

## プロバイダーと人によるレビューの境界

生検索は必要な検索語を設定済み公開 API/検索プロバイダーへ送信しますが、LLM は呼び出しません。
合成は選択アカウント項目と収集済みエビデンスを `OPENROUTER_BASE_URL` へ送信し、
`OPENROUTER_API_KEY` を必要とします。抽出と合成の既定値は DeepSeek V4 Flash
（`deepseek/deepseek-v4-flash-0731`）、`high` reasoning effort、リクエストごとに120秒の
タイムアウト、厳格な構造化出力、SDK 自動再試行0回です。実行前に
プロバイダー承認、ログ/保持条件、データ最小化を確認してください。人によるレビューでは引き続き
HCP/HCO の本人性、ソース来歴、完全性、すべての partial/failed レコードを確認する必要があります。

## チェックアウト開発

意図的にリポジトリチェックアウトで開発する場合だけ、ソースラッパーを別にテストします：

```bash
uv run --all-extras python scripts/batch_enrich.py \
  --input-file src/capabilities/hcp-intelligence/open_pharma_plugins_hcp_intelligence/fixtures/sample_accounts.csv \
  --output-dir data/hcp-intelligence \
  --ids "HCP-SG-001" "HCP-SG-002" \
  --country "Singapore" \
  --account-type "HCP" \
  --dry-run
```

同じ厳密な範囲照合と必要な承認の後、そのチェックアウトコマンドをコピーし、`--dry-run` だけを
削除して実行フラグを追加します：

```bash
uv run --all-extras python scripts/batch_enrich.py \
  --input-file src/capabilities/hcp-intelligence/open_pharma_plugins_hcp_intelligence/fixtures/sample_accounts.csv \
  --output-dir data/hcp-intelligence \
  --ids "HCP-SG-001" "HCP-SG-002" \
  --country "Singapore" \
  --account-type "HCP" \
  --synthesize \
  --resume \
  --concurrency 3
```

インストール済み marketplace プラグインのキャッシュには、このリポジトリスクリプトはありません。
固定されたインストールコマンドが失敗した場合のフォールバックとして使用してはいけません。
