# 安装

## 选择安装来源

| 目标 | 命令 | 来源 |
|---|---|---|
| 安装发行版 | `bash install.sh install` | 各功能的最新发行标签 |
| 更新已有安装 | `bash install.sh update` | 当前发行目录 |
| 测试未发布的代码 | `bash install.sh local` | 当前检出，包括未提交的更改 |
| 回滚单个功能 | 参见 [回滚](#回滚) | 精确的不可变标签 |

发行版安装不会跟踪 `main` 分支。要测试分支，请检出该分支后使用 `local`。

## 引导式安装程序

Python 发行包只包含六个 MCP 服务器和运行时样本；Skill、cookbook 和 marketplace 清单从带标签的
仓库插件安装。引导程序支持 Claude Code、Codex 和 GitHub Copilot CLI，会调用各 harness 的原生安装机制，并将共享配置存储在
`~/.open-pharma-plugins/config` 中。它要求预先安装 `uv`/`uvx`，但不会代替用户安装包管理器。

请先下载并检查脚本，再执行：

```bash
curl -fsSLO https://raw.githubusercontent.com/PharmaGenAI/open-pharma-plugins/main/install.sh
less install.sh
bash install.sh
```

菜单提供 **Install**、**Update**、**Configure**、**Verify** 和 **Uninstall**。每个功能以 Skill
加可选 MCP 服务器的方式独立安装。

使用 GitHub Copilot 时，请先安装并认证 Copilot CLI，然后在 harness 菜单中选择 **copilot**。安装程序会注册
`.github/plugin/marketplace.json`，并通过 Copilot 原生插件命令安装所选功能。Copilot 复用每个功能的
`.claude-plugin/plugin.json`，因此其 Skill 和可选 MCP 服务器与 Claude 包保持一致。

### 更新

重新下载并检查最新脚本以获取最新的发行目录：

```bash
curl -fsSLO https://raw.githubusercontent.com/PharmaGenAI/open-pharma-plugins/main/install.sh
less install.sh
bash install.sh update
```

### 回滚

对于接受远程标签的 harness，仅选择标签指定的功能：

```bash
OPP_REF=open-pharma-plugins-territory-alignment-v<version> bash install.sh install
```

## 本地检出

使用专用克隆，其路径应保持稳定：

```bash
git clone https://github.com/PharmaGenAI/open-pharma-plugins.git
cd open-pharma-plugins
git switch <开发分支>   # 可选
bash install.sh local
```

本地模式将选定的插件清单和 MCP 包规格指向当前检出并添加 `uvx --refresh`。离开本地模式时恢复
发行来源：

```bash
bash install.sh local --restore
```

详见 [本地开发](local_development.md)。

## 手动 Skill + MCP 安装

对于不兼容的 harness，保持以下三个值一致：

- Skill: `src/capabilities/<cap>/skill`
- 包 extra: `open-pharma-plugins[<cap>]`
- 入口: `open-pharma-plugins-<cap>`

```bash
uvx --from \
  "open-pharma-plugins[<cap>] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-<cap>-v<version>" \
  open-pharma-plugins-<cap>
```

### 已安装的 HCP 批处理控制台

HCP Intelligence `1.0.2` 提供已打包的 `open-pharma-plugins-hcp-batch` 控制台。可选合成依赖使用
`hcp-intelligence-synth` extra。应从不可变 HCP 标签运行；marketplace 缓存中不包含仅供仓库使用的
`scripts/batch_enrich.py` 包装脚本：

```bash
uvx --from \
  "open-pharma-plugins[hcp-intelligence-synth] @ git+https://github.com/PharmaGenAI/open-pharma-plugins.git@open-pharma-plugins-hcp-intelligence-v1.0.2" \
  open-pharma-plugins-hcp-batch --help
```

提供业务路径或启用提供商调用前，请阅读 [HCP 批处理与 CSV 审阅](hcp_batch.md)。

只需要 MCP 服务器时，可从已发布的 Python 发行版安装：

```bash
python -m pip install "open-pharma-plugins[hcp-intelligence]"
open-pharma-plugins-hcp-intelligence --version
```

发布验证还应将下载文件与发行页的 `SHA256SUMS` 对比，并检查 SBOM 与构建来源证明。

## 依赖

`uvx` 会将各功能的 Python 依赖安装到隔离缓存中。其余需要的是服务设置。

### 常用服务设置

| 变量 | 用途 |
|---|---|
| `SERPER_API_KEY` | Serper Web 搜索（HCP 情报和竞争情报使用） |
| `TAVILY_API_KEY` | Tavily Web 搜索 |
| `EXA_API_KEY` | Exa Web 搜索 |
| `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` | 可选的 HCP 批量档案合成 |

通过安装程序的 **Configure** 操作、Shell 环境或 `~/.open-pharma-plugins/config` 设置值；
环境变量优先。

### 完整配置

详见 [配置参考](configuration.md)。
