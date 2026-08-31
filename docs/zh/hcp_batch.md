# HCP 批处理与 CSV 审阅

商业用户提供账户 CSV 路径和输出目录后，应使用已打包的 HCP 批处理控制台。已安装工作流不依赖仓库
检出，并固定到 HCP Intelligence `1.0.2` 源码。

## 输入与预检

CSV 表头为：

```csv
id,name,specialty,country,account_type,institution
```

六列都必须存在；`id`、`name`、`country`、`account_type` 不得为空，`specialty` 和 `institution`
可以为空。ID 必须唯一、不含控制字符且可安全用作文件名。`account_type` 只能是 `HCP` 或 `HCO`。
支持带或不带 BOM 的 UTF-8 文件。

把两个用户路径解析为绝对路径，拒绝控制字符，并把每个路径作为一个加引号的 Shell 参数。不得将路径
放入命令替换或 `eval`。只保留用户实际提供的筛选条件：`--ids` 后每个 ID 都必须是独立的加引号参数，
并原样保留用户提供的 `--country` 与 `--account-type` 值。以下包含全部三种筛选条件的示例始终先执行
dry run：

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

Dry run 会验证完整文件和输出位置，显示所选 ID、总数、HCP/HCO 数量拆分、提供商/模型设置及计划
产物；不会发起外部调用，也不会创建输出目录。必须把这些所选 ID、总数和 HCP/HCO 拆分与用户请求的
范围逐项比较，不能只比较数量。若筛选条件遗漏或改变、ID 多出或缺失、数量或类型拆分不符，必须停止
执行，修正命令并重新 dry run。只有范围完全相等后，用户明确要求运行且最多选中 10 个账户时，才可
在成功预检后继续。仅提供路径时须先确认；超过 10 个账户必须明确批准提供商调用。原始请求中清晰的
预先批准可满足该要求。

## 经批准的合成运行

配置 `OPENROUTER_API_KEY` 并确认所选数据可以发送给提供商。复制已验证的 dry-run 命令，只删除
`--dry-run`，再加入 `--synthesize`、`--resume` 和 `--concurrency 3`；来源、路径和筛选参数必须
逐参数完全相同：

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

若 `uvx` 无法安装固定来源，不得静默回退到 `main`、其他标签、未固定的软件包或本地检出。原始证据
收集可使用同一控制台并省略 `--synthesize`；它不需要 LLM 密钥，但 Web 搜索仍需配置 Serper、Tavily
或 Exa。

## 输出契约

每个所选账户都有一份规范的 `<id>.json` 证据产物。`batch_summary.csv` 是便于业务审阅的投影，不是
规范数据存储。其稳定的 schema 版本 1 按以下顺序包含 27 列：

```text
account_id, account_type, input_name, input_specialty, input_country,
input_institution, status, profile_validated, profile_completeness,
profile_name, profile_specialty, profile_country, current_title,
organization_type, affiliations, qualifications, research_or_clinical_focus,
professional_roles, key_publication_count, clinical_trial_count,
active_grant_count, congress_activity_count, source_count, source_urls,
tools_failed, error, json_file
```

CSV 使用 UTF-8 BOM 和 CRLF 记录以兼容电子表格。多值字段按原顺序去重，并用 ` | ` 连接。所有以
`=`、`+`、`-` 或 `@` 开头的文本单元格都会添加英文单引号，以消除公式执行风险。有已验证档案时，
数值字段保持数值；不可用值留空。采取行动前必须核对引用的 JSON 与来源。

`batch_manifest.json` 使用 schema 版本 2，记录输入路径和 SHA-256、时间戳、实际合成端点/模型/
reasoning/超时/并发、所选数量与状态计数、每账户产物状态，以及 CSV 的状态、路径、schema、行数和
SHA-256。它不会保存 API 密钥，也不会重复账户名称。

## 输出目录与恢复安全

输出路径必须是目录。新目录或空目录可直接使用；非空目录必须加 `--resume`。在支持 POSIX 权限的
系统上，新建目录为 `0700`，文件为 `0600`。恢复会跳过可用账户 JSON，在需要合成时复用原始证据，
并重新处理损坏或无效产物。它保留目录内无关文件，但会按需原子替换所选 `<id>.json`，并重新生成
`batch_summary.csv` 和 `batch_manifest.json`。

不要直接在生成的 CSV、JSON 或清单中添加人工审阅备注；恢复运行不会保留这些修改。应先把 CSV
复制到单独的审阅工作簿或记录系统。

## 完成状态与退出码

监控进程直到退出，然后报告 completed、partial、failed、skipped 数量，以及输出目录、汇总 CSV 和
清单的绝对路径。

- 退出 `0`：dry run，或运行完成且没有 partial/failed 账户及 CSV 导出失败；过滤后为空也返回 `0`
  且不生成产物。
- 退出 `1`：存在 partial/failed 账户或 CSV 导出失败；已有 JSON 和 schema-v2 清单会保留，以便检查
  和有意识地恢复。
- 退出 `2`：命令行或预检用法错误，例如 CSV 无效、路径不安全、参数无效，或非空目录未加
  `--resume`；不会调用提供商。

## 提供商与人工审阅边界

原始搜索会把必要查询词发送给配置的公共 API/搜索提供商，但不调用 LLM。合成会把所选账户字段和
收集证据发送到 `OPENROUTER_BASE_URL`，并需要 `OPENROUTER_API_KEY`。提取与合成默认为
DeepSeek V4 Flash（`deepseek/deepseek-v4-flash-0731`）、`high` reasoning effort、
每请求 120 秒超时、严格结构化输出及零次 SDK 自动重试。执行前确认提供商批准、日志/保留条款和
数据最小化。人工审阅者仍必须确认 HCP/HCO
身份、来源来历、完整性及每个 partial/failed 记录。

## 检出开发

仅在明确使用仓库检出进行开发时，单独测试源码包装脚本：

```bash
uv run --all-extras python scripts/batch_enrich.py \
  --input-file src/capabilities/hcp-intelligence/open_pharma_plugins_hcp_intelligence/fixtures/sample_accounts.csv \
  --output-dir data/hcp-intelligence \
  --ids "HCP-SG-001" "HCP-SG-002" \
  --country "Singapore" \
  --account-type "HCP" \
  --dry-run
```

通过相同的精确范围校验并获得所需批准后，复制该检出命令，只删除 `--dry-run` 并添加执行参数：

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

已安装的 marketplace 插件缓存中不存在该仓库脚本；固定安装命令失败时，绝不能把它当作回退方案。
