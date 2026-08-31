# 設定

ほとんどの場合、`install.sh` の **Configure** を実行してください。共有設定は
`~/.open-pharma-plugins/config` に `KEY=VALUE` 形式で保存され、ファイル権限は `600` です。
任意のハーネスから起動される open-pharma-plugins コンポーネントがこのファイルを参照します。
プロセス環境変数は、ファイル内の同名キーより優先されます。

`OPEN_PHARMA_CONFIG=/path/to/file` で別のファイルを指定するか、
`OPEN_PHARMA_CONFIG_DIR=/path/to/dir` でデフォルトの `config` ファイルを含むディレクトリを
変更できます。これらのブートストラップ変数は、読み取るファイルを決定するため、プロセス環境で
設定する必要があります。

## ファイル構文

1 行に 1 つの `KEY=VALUE` を記述し、コメントは別の行に置いてください。値の後ろの文字列は、
`#` を含めて値の一部として解析されます。可能な限りインストーラーの **Configure** を使用します。

`ci_status` が示すのはキーの有無であり、プロバイダーによる受理ではありません。openFDA の
カバレッジが `failed` の場合、優先される設定元で `OPENFDA_API_KEY` を確認または削除します。
環境変数は設定ファイルより優先されます。ホストを再起動し、状態を確認してから新しい実行を作成します。

## 検索選択

`OPEN_PHARMA_SEARCH_BACKEND` を未設定のままにするか `auto` に設定すると、次の固定順序で最初に
設定されたキーが選択されます：Serper、Tavily、Exa。`serper`、`tavily`、`exa` に設定すると単一の
プロバイダーに固定されます。対応するキーが見つからない場合は、フォールバックせずにエラーを返します。

検索と合成は独立しています。Exa は LLM キーなしで Web エビデンスを収集できます。HCP バッチ
スクリプトが設定済みの OpenRouter 互換エンドポイントへ送信するのは `--synthesize` 指定時だけで、
既定モデルは固定された `deepseek/deepseek-v4-flash-0731` です。抽出・合成の reasoning effort は
既定で `high`、リクエストタイムアウトは 120 秒で、SDK の自動再試行は無効です。
`--reasoning-effort xhigh` または `--synthesis-timeout-seconds <秒>` で実行単位に上書きできます。
バッチマニフェストには、実際のエンドポイント、モデル、reasoning effort、タイムアウトが記録されます。

## 設定カタログ

以下は、インストーラーの **Configure** アクションで表示される完全なカタログです。デフォルト値と
グループ分けは [`CONFIG_FIELDS`](../../src/shared/env.py) から取得されます。`—` は未設定または
無効を意味します。

### Web 検索

| 変数 | デフォルト | 用途 |
|---|---|---|
| `OPEN_PHARMA_SEARCH_BACKEND` | auto | テキスト検索バックエンド（auto: serper > tavily > exa、または1つを指定） |
| `SERPER_API_KEY` | — | Serper Web 検索 API キー *(シークレット)* |
| `TAVILY_API_KEY` | — | Tavily Web 検索 API キー *(シークレット)* |
| `EXA_API_KEY` | — | Exa Web 検索 API キー *(シークレット)* |

### HCP インテリジェンス

| 変数 | デフォルト | 用途 |
|---|---|---|
| `OPENROUTER_API_KEY` | — | オプションのバッチプロファイル合成用 OpenRouter API キー *(シークレット)* |
| `OPENROUTER_BASE_URL` | https://openrouter.ai/api/v1 | オプションのバッチプロファイル合成用 OpenRouter 互換 API ベース URL |
| `NCBI_API_KEY` | — | NCBI E-utilities API キー（オプション、PubMed レート制限を 3→10 リクエスト/秒に引き上げ） |
| `OPEN_PHARMA_HCP_DATA_DIR` | — | 可変 HCP エンリッチメントデータのディレクトリ（デフォルト：~/.open-pharma-plugins/hcp-intelligence） |

### フィールドトレーニング

| 変数 | デフォルト | 用途 |
|---|---|---|
| `OPEN_PHARMA_TRAINING_CONTENT_DIR` | — | 取り込み済みトレーニング文書のコンテンツストアディレクトリ（デフォルト：~/.open-pharma-plugins/training-content） |

### キャンペーンスタジオ

| 変数 | デフォルト | 用途 |
|---|---|---|
| `OPEN_PHARMA_CAMPAIGN_STORE_DIR` | — | キャンペーンブリーフ、クレーム、レンダリング資産のルートディレクトリ（デフォルト：~/.open-pharma-plugins/campaign-studio） |

### ネクストベストエンゲージメント

| 変数 | デフォルト | 用途 |
|---|---|---|
| `OPEN_PHARMA_NBE_OUTPUT_DIR` | — | エンゲージメントプランの出力ディレクトリ（デフォルト：~/.open-pharma-plugins/next-best-engagement） |

### テリトリーアラインメント

| 変数 | デフォルト | 用途 |
|---|---|---|
| `OPEN_PHARMA_TA_DATA_DIR` | — | hcps.csv、reps.csv、current_alignment.csv、constraints.csv を含むディレクトリ（デフォルト：組み込みフィクスチャ） |
| `OPEN_PHARMA_TA_SCENARIOS_DIR` | — | アラインメントシナリオの保存ディレクトリ（デフォルト：~/.open-pharma-plugins/territory-alignment/scenarios） |

### コンペティティブインテリジェンス

| 変数 | デフォルト | 用途 |
|---|---|---|
| `OPEN_PHARMA_CI_DATA_DIR` | — | ウォッチリスト、レポート、キャッシュのディレクトリ（デフォルト：~/.open-pharma-plugins/competitive-intelligence） |
| `OPENFDA_API_KEY` | — | openFDA API キー（オプション。毎分 240 リクエスト。1 日の上限はキーなしで IP ごとに 1,000、キーありでキーごとに 120,000） |
| `CI_CACHE_TTL_HOURS` | 24 | キャッシュされた API レスポンスの有効期限（時間、デフォルト 24） |

各機能の固有のコンテキストと前提条件は、それぞれの Skill と cookbook に記載されています。
資格情報をツール引数や検索語に含めないでください。
[コンペティティブインテリジェンス利用ガイド](competitive_intelligence.md)と
[データセキュリティとコンプライアンス境界](data_security.md)も参照してください。
