# 竞争情报使用指南

竞争情报功能只收集一次公共来源证据，记录来源覆盖情况，并从同一个不可变运行生成简报和时间线。

高影响结论仍须依据链接的一手来源复核。提供商失败属于无法判断，不能表述为零结果。

## 安装与启用

如需完整的 Claude Code、Codex 或 GitHub Copilot Skill + MCP 插件，请使用引导式安装程序：

```bash
bash install.sh
```

如只需不含 Skill 的 MCP 服务器，请安装 Python 发行包：

```bash
python -m pip install "open-pharma-plugins[competitive-intelligence]"
open-pharma-plugins-competitive-intelligence --version
```

安装或更新插件后，请新建任务或重启宿主，以加载新的工具清单。

## 配置

| 变量 | 是否必需 | 用途 |
|---|---|---|
| `SERPER_API_KEY`、`TAVILY_API_KEY` 或 `EXA_API_KEY` | 新闻必需 | Web 搜索证据 |
| `OPENFDA_API_KEY` | 否 | 提高 openFDA 每日配额 |
| `NCBI_API_KEY` | 否 | 提高 PubMed 请求速率 |
| `OPEN_PHARMA_CI_DATA_DIR` | 否 | 观察列表、缓存、运行和报告 |
| `CI_CACHE_TTL_HOURS` | 否 | 缓存有效期，默认 `24` |

在 `~/.open-pharma-plugins/config` 中每行只写一个 `KEY=VALUE`。值后的文本（包括 `#`）会被解析为值的
一部分，因此注释必须单独成行。

openFDA 在两种模式下均允许每分钟 240 个请求。每日上限为无密钥时每 IP 1,000 个请求、有密钥时每密钥
120,000 个请求。

`ci_status` 只表示是否配置了密钥，不代表提供商已接受该密钥。凭据不会写入证据 URL、缓存标识、运行或报告。

## 执行一次证据收集

```text
ci_status
ci_track action="add" entity_type="drug" name="ExampleDrug" aliases=["examplemab"]
ci_track action="add" entity_type="company" name="Example Pharma"
ci_refresh entities=["ExampleDrug", "Example Pharma"]
```

药物及其生产企业必须作为不同实体跟踪。不得把公司别名推断为产品。

解释结论前，应检查每个来源的状态、查询、来源 URL、检索时间、记录数、缓存状态和限制。

## 解释覆盖状态

- `complete`：限定范围的提供商请求已完成；该请求返回零记录也可信。
- `partial`：存在可用记录，但部分请求覆盖不完整。
- `failed`：提供商或解析器未能产生可信记录。
- `not_configured`：缺少所需的提供商配置。
- `not_applicable`：该来源不适用于此实体身份。

`failed` 和 `not_configured` 均表示无法判断。一个来源成功，不能使另一个失败来源变为完整。

如果 `total_available` 大于返回数量，说明该限定运行未检查全部匹配记录。报告中应同时注明返回数量和
可用总数。

## 从同一运行生成产物

复用 `ci_refresh` 返回的 `run_id`：

```text
ci_report run_id="<run_id>"
ci_timeline run_id="<run_id>" months_back=12
```

报告和时间线清单必须包含相同的 `run_records_sha256`。不要仅为生成另一种视图而重复收集。

旧式 `ci_report focus=...` 和 `ci_timeline entities=[...]` 会在渲染前创建一个新运行。重视可复现性时，
应显式调用 `ci_refresh`。

## openFDA 故障排查

如果 DailyMed 成功而 openFDA 覆盖状态为 `failed`：

1. 将 openFDA 视为无法判断，不得报告不存在 FDA 事件。
2. 运行 `ci_status`，检查是否配置了 `OPENFDA_API_KEY`。
3. 在提供商账户中验证密钥，或从配置文件和进程环境中删除密钥。
4. 环境变量优先于配置文件；修改任一来源后都应重启宿主。
5. 使用 `ci_status` 确认预期的配置状态；排查时不得输出密钥。
6. 修正配置后创建新的证据运行；已有运行保持不可变。

传输层会主动隐藏包含凭据的请求详情。因此，通用 HTTP 失败可能代表密钥被拒绝、达到速率限制或其他
提供商响应。

## 本地数据与审核边界

`OPEN_PHARMA_CI_DATA_DIR` 默认为 `~/.open-pharma-plugins/competitive-intelligence`，其中包含观察列表、
schema-v2 缓存、不可变运行和不覆盖旧文件的报告目录。

本地权限不等同于加密、租户隔离、保留策略或企业访问控制。将高影响结论用于商业或医疗场景前，必须依据
一手来源复核。

另请参阅[配置](configuration.md)、[数据安全](data_security.md)和[测试](testing.md)。
