# コンペティティブインテリジェンス利用ガイド

コンペティティブインテリジェンスは公開ソースのエビデンスを一度収集し、ソースカバレッジを記録して、
同じ不変の実行からブリーフィングとタイムラインを生成します。

重要な所見は、リンクされた一次情報で確認する必要があります。プロバイダー障害は不確定であり、
ゼロ件の所見として提示してはいけません。

## インストールと有効化

Claude Code、Codex、または GitHub Copilot の Skill + MCP プラグイン一式には、ガイド付きインストーラーを使用します：

```bash
bash install.sh
```

Skill を含まない MCP サーバーだけを使う場合は、Python 配布物をインストールします：

```bash
python -m pip install "open-pharma-plugins[competitive-intelligence]"
open-pharma-plugins-competitive-intelligence --version
```

プラグインのインストールまたは更新後は、新しいツール一覧を読み込むために、新しいタスクを開始するか
ホストを再起動してください。

## 設定

| 変数 | 必須 | 用途 |
|---|---|---|
| `SERPER_API_KEY`、`TAVILY_API_KEY`、`EXA_API_KEY` のいずれか | ニュースで必須 | Web 検索エビデンス |
| `OPENFDA_API_KEY` | いいえ | openFDA の 1 日上限を引き上げる |
| `NCBI_API_KEY` | いいえ | PubMed のリクエストレートを引き上げる |
| `OPEN_PHARMA_CI_DATA_DIR` | いいえ | ウォッチリスト、キャッシュ、実行、レポート |
| `CI_CACHE_TTL_HOURS` | いいえ | キャッシュ有効時間。既定値は `24` |

`~/.open-pharma-plugins/config` には 1 行に 1 つの `KEY=VALUE` を記述します。値の後ろの文字列は
`#` を含めて値の一部になるため、コメントは別の行に置いてください。

openFDA はどちらのモードでも毎分 240 リクエストです。1 日の上限はキーなしで IP ごとに 1,000、
キーありでキーごとに 120,000 です。

`ci_status` が示すのはキーの設定有無であり、プロバイダーによる受理ではありません。資格情報は
エビデンス URL、キャッシュ ID、実行、レポートから除外されます。

## エビデンスを一度収集する

```text
ci_status
ci_track action="add" entity_type="drug" name="ExampleDrug" aliases=["examplemab"]
ci_track action="add" entity_type="company" name="Example Pharma"
ci_refresh entities=["ExampleDrug", "Example Pharma"]
```

医薬品と製造会社は別のエンティティとして追跡します。会社の別名を製品として推測してはいけません。

所見を解釈する前に、各ソースのステータス、クエリ、ソース URL、取得時刻、件数、キャッシュ状態、
制限事項を確認してください。

## カバレッジの解釈

- `complete`：範囲を限定したリクエストが完了し、ゼロ件もその範囲では信頼できます。
- `partial`：利用可能なレコードはありますが、要求したカバレッジの一部が不完全です。
- `failed`：プロバイダーまたはパーサーが信頼できるレコードを生成できませんでした。
- `not_configured`：必要なプロバイダー設定がありません。
- `not_applicable`：その ID にソースが適用されません。

`failed` と `not_configured` は不確定です。1 つのソースが成功しても、別の失敗したソースが
完全になったことにはなりません。

`total_available` が返却件数より大きい場合、限定実行は一致した全レコードを調査していません。
返却件数と利用可能総数を併記してください。

## 同じ実行から成果物を作成する

`ci_refresh` が返す `run_id` を再利用します：

```text
ci_report run_id="<run_id>"
ci_timeline run_id="<run_id>" months_back=12
```

レポートとタイムラインのマニフェストに同じ `run_records_sha256` が含まれることを確認します。
別の表示を作るためだけに収集を繰り返してはいけません。

従来の `ci_report focus=...` と `ci_timeline entities=[...]` は、レンダリング前に新しい実行を 1 つ
作成します。再現性が重要な場合は、明示的な `ci_refresh` を使用してください。

## openFDA のトラブルシューティング

DailyMed が成功し、openFDA のカバレッジが `failed` の場合：

1. openFDA は不確定として扱い、FDA イベントが存在しないと報告しません。
2. `ci_status` を実行し、`OPENFDA_API_KEY` の設定有無を確認します。
3. プロバイダー側でキーを確認するか、設定ファイルとプロセス環境から削除します。
4. 環境変数は設定ファイルより優先されます。いずれかを変更した後はホストを再起動します。
5. `ci_status` で想定した設定状態を確認し、診断中にキーを表示しません。
6. 設定修正後に新しい実行を作成します。既存の実行は不変のままです。

トランスポートは資格情報を含むリクエスト詳細を意図的に秘匿します。そのため、一般的な HTTP 障害は、
拒否されたキー、レート制限、その他のプロバイダー応答を表す場合があります。

## ローカルデータとレビュー境界

`OPEN_PHARMA_CI_DATA_DIR` の既定値は `~/.open-pharma-plugins/competitive-intelligence` です。
ウォッチリスト、schema-v2 キャッシュ、不変の実行、非上書きレポートが保存されます。

ローカル権限は暗号化、テナント分離、保持方針、企業アクセス制御ではありません。商用または医療用途の
前に、重要な所見を一次情報で確認してください。

[設定](configuration.md)、[データセキュリティ](data_security.md)、[テスト](testing.md)も参照してください。
