# 配置

大多数情况下，运行 `install.sh` 中的 **Configure** 即可完成设置。共享配置存储在
`~/.open-pharma-plugins/config` 中，格式为 `KEY=VALUE`，文件权限为 `600`。所有由任何
harness 启动的 open-pharma-plugins 组件均会读取此文件。进程环境变量优先于文件中的同名键。

使用 `OPEN_PHARMA_CONFIG=/path/to/file` 指定替代配置文件，或使用
`OPEN_PHARMA_CONFIG_DIR=/path/to/dir` 更改包含默认 `config` 文件的目录。这些引导变量必须在
进程环境中设置，因为它们决定了读取哪个文件。

## 文件语法

每行只写一个 `KEY=VALUE`，注释必须单独成行。值后的文本（包括 `#`）会被解析为值的一部分。
应尽量使用安装程序的 **Configure** 操作。

`ci_status` 只表示是否配置了密钥，不代表提供商已接受该密钥。如果 openFDA 覆盖状态为
`failed`，请在实际生效的配置来源中检查或删除 `OPENFDA_API_KEY`。环境变量优先于配置文件。
请重启宿主、确认状态，然后创建新的证据运行。

## 搜索选择

将 `OPEN_PHARMA_SEARCH_BACKEND` 保持未设置或设为 `auto`，将按固定顺序选择第一个已配置的密钥：
Serper、Tavily、Exa。设为 `serper`、`tavily` 或 `exa` 可锁定单一提供商；此时缺少对应密钥将
直接报错而非回退。

搜索与合成相互独立。Exa 可以在没有 LLM 密钥的情况下收集 Web 证据。仅当 HCP 批处理脚本使用
`--synthesize` 时，才会把数据发送到配置的 OpenRouter 兼容端点；默认使用固定模型
`deepseek/deepseek-v4-flash-0731`。提取与合成的 reasoning effort 默认为 `high`，请求超时默认为
120 秒，并禁用 SDK 自动重试。可使用 `--reasoning-effort xhigh` 或
`--synthesis-timeout-seconds <秒数>` 进行单次运行覆盖。批处理清单会记录实际使用的端点、模型、
reasoning effort 和超时值。

## 配置目录

以下是安装程序 **Configure** 操作显示的完整目录。默认值和分组来自
[`CONFIG_FIELDS`](../../src/shared/env.py)；`—` 表示未设置或已禁用。

### Web 搜索

| 变量 | 默认值 | 用途 |
|---|---|---|
| `OPEN_PHARMA_SEARCH_BACKEND` | auto | 文本搜索后端（auto: serper > tavily > exa；或指定一个） |
| `SERPER_API_KEY` | — | Serper Web 搜索 API 密钥 *(密钥)* |
| `TAVILY_API_KEY` | — | Tavily Web 搜索 API 密钥 *(密钥)* |
| `EXA_API_KEY` | — | Exa Web 搜索 API 密钥 *(密钥)* |

### HCP 情报

| 变量 | 默认值 | 用途 |
|---|---|---|
| `OPENROUTER_API_KEY` | — | 用于可选批量档案合成的 OpenRouter API 密钥 *(密钥)* |
| `OPENROUTER_BASE_URL` | https://openrouter.ai/api/v1 | 用于可选批量档案合成的 OpenRouter 兼容 API 基础 URL |
| `NCBI_API_KEY` | — | NCBI E-utilities API 密钥（可选；将 PubMed 速率限制从 3 提升到 10 请求/秒） |
| `OPEN_PHARMA_HCP_DATA_DIR` | — | 可变 HCP 丰富化数据目录（默认：~/.open-pharma-plugins/hcp-intelligence） |

### 现场培训

| 变量 | 默认值 | 用途 |
|---|---|---|
| `OPEN_PHARMA_TRAINING_CONTENT_DIR` | — | 已导入培训文档的内容存储目录（默认：~/.open-pharma-plugins/training-content） |

### 营销活动工作室

| 变量 | 默认值 | 用途 |
|---|---|---|
| `OPEN_PHARMA_CAMPAIGN_STORE_DIR` | — | 活动简报、声明和渲染资源的根目录（默认：~/.open-pharma-plugins/campaign-studio） |

### 下一最佳互动

| 变量 | 默认值 | 用途 |
|---|---|---|
| `OPEN_PHARMA_NBE_OUTPUT_DIR` | — | 导出互动计划的目录（默认：~/.open-pharma-plugins/next-best-engagement） |

### 区域对齐

| 变量 | 默认值 | 用途 |
|---|---|---|
| `OPEN_PHARMA_TA_DATA_DIR` | — | 包含 hcps.csv、reps.csv、current_alignment.csv、constraints.csv 的目录（默认：内置样本数据） |
| `OPEN_PHARMA_TA_SCENARIOS_DIR` | — | 保存对齐方案的目录（默认：~/.open-pharma-plugins/territory-alignment/scenarios） |

### 竞争情报

| 变量 | 默认值 | 用途 |
|---|---|---|
| `OPEN_PHARMA_CI_DATA_DIR` | — | 观察列表、报告和缓存目录（默认：~/.open-pharma-plugins/competitive-intelligence） |
| `OPENFDA_API_KEY` | — | openFDA API 密钥（可选；每分钟均为 240 个请求；每日上限为无密钥时每 IP 1,000 个、有密钥时每密钥 120,000 个） |
| `CI_CACHE_TTL_HOURS` | 24 | 缓存的 API 响应过期时间（小时，默认 24） |

各功能的特定上下文和前置条件详见各自的 Skill 和 cookbook。不要把密钥放入工具参数或搜索词。
另请参阅[竞争情报使用指南](competitive_intelligence.md)和
[数据安全与合规边界](data_security.md)。
