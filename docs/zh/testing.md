# 测试

测试按行为组织：

- **单元和处理程序测试** 覆盖模式、工具发现、转换和错误路径。
- **协议测试** 通过真实 MCP 客户端测试 initialize → `tools/list` → `tools/call`。
- **仓库一致性测试** 保护重复的清单、发行引用、包版本和同步源文件。
- **可达性测试** 调用外部提供商，属于可选项。

`tests/conftest.py` 将 `src/` 和各服务器功能目录添加到 `sys.path`，因此新的 MCP 服务器包无需安装
wheel 即可被发现。

## 命令

运行 CI 使用的离线测试套件：

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

还要验证实际分发物，而非仅测试源码树：

```bash
uv build
uv run twine check dist/*
uv run python scripts/smoke_wheel.py dist/*.whl
```

竞争情报提供商测试使用已清理的录制固定数据，并保持离线。运行与输出测试会验证单次收集、经过哈希检查的
不可变数据加载、覆盖状态语义、防公式执行的 CSV，以及 DOM 安全的报告和时间线渲染。

更改配置指南时，必须同步 `CONFIG_FIELDS`、`install.sh`、`.env.example` 和三种语言的配置页面。
配置示例中的注释必须单独成行。

实时凭据诊断不得记录密钥。应比较提供商覆盖状态、安全错误码和无凭据请求。即使另一个来源成功，
失败来源仍表示无法判断。

仅在需要发起实时请求并已配置凭据时运行提供商测试：

```bash
OPEN_PHARMA_RUN_REACHABILITY=1 uv run pytest -m reachability tests/integration/test_reachability.py
```

对于 Shell 更改，还需运行 `bash -n <script>`。开发期间先针对相关测试模块运行，或使用
`-k <pattern>`，然后再运行完整的离线套件。

## 测试内容

根据功能的组件匹配测试：

1. 每个 MCP 服务器需要模式/发现和处理程序的成功/错误覆盖。
2. 为服务器特定的启动、流式传输或传输行为添加协议测试。
3. 当读取器或渲染器需要代表性输入时，添加小型确定性已提交的固定数据。
4. 当两个文件或清单字段必须同步时，添加反漂移断言。
5. 纯 Skill 更改应验证 frontmatter、引用的资源和清单打包。

默认离线套件中不得要求凭据、GUI 应用程序、GPU 硬件或公共网络可达性。

## HCP 批处理检查

HCP 批处理单元测试均离线运行，覆盖 CSV 验证、路径/输出预检、提供商请求契约、恢复、
schema-v2 清单，以及稳定且防公式执行的汇总 CSV：

```bash
uv run --all-extras python -m pytest -q \
  tests/capabilities/test_hcp_batch.py \
  tests/capabilities/test_hcp_batch_csv.py
```

在明确的仓库检出中，还可用内置样本测试源码包装脚本的无提供商调用预检：

```bash
uv run --all-extras python scripts/batch_enrich.py \
  --input-file src/capabilities/hcp-intelligence/open_pharma_plugins_hcp_intelligence/fixtures/sample_accounts.csv \
  --output-dir data/hcp-intelligence \
  --dry-run
```

这条命令仅适用于检出，不得描述为已安装 marketplace 缓存的路径。固定版本的已安装命令和实时
提供商边界参见 [HCP 批处理](hcp_batch.md)。

有关证据运行流程和 openFDA 故障排查边界，请参阅
[竞争情报使用指南](competitive_intelligence.md)。
