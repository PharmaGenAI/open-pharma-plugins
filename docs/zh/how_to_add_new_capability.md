# 添加新功能/插件

在 `src/capabilities/` 下，每个插件是一个短目录，可包含 `skill/`（Agent Skill）和/或
`<import_name>/`（MCP 服务器包）——两者均为可选。

## 结构

```
src/capabilities/<yourname>/
├── .claude-plugin/plugin.json      # Claude 与 GitHub Copilot 共用的 harness 清单
├── .codex-plugin/plugin.json       # Codex harness 清单
├── .mcp.json                       # MCP 服务器启动配置（仅服务器功能）
├── skill/SKILL.md                  # Agent Skill（frontmatter: name/description + 正文）
└── open_pharma_plugins_<yourname>/  # MCP 服务器包
    ├── __init__.py                 # __version__ + build_registry + SYSTEM_DEPS
    ├── __main__.py                 # 通用入口垫片（从任何服务器原样复制）
    └── tools/                      # 每个 .py 文件一个工具，导出 TOOL + handle，启动时自动发现
        └── my_tool.py
```

## 工具约定（自动发现）

在 `tools/` 下创建新的 `.py` 文件，仅导出两项：

```python
from pydantic import BaseModel, Field


class MyToolArgs(BaseModel):
    query: str = Field(description="搜索查询。")


TOOL = {"name": "my_tool", "description": "...", "args": MyToolArgs}


def handle(arguments: dict) -> list[dict]:
    ...
    return [{"type": "text", "text": ...}]
```

- `args` 是 Pydantic 模型，自动生成工具的 `inputSchema` 并验证每次调用；`handle` 接收普通 dict
  并返回 MCP 内容块（`text` / `image`）。
- 延迟导入：在 `handle` 内部导入重型依赖，以免影响其他工具。

## 注册步骤

复制现有功能目录到 `src/capabilities/<yourname>/`，重命名包目录，然后：

1. `pyproject.toml` `[project.scripts]` — 添加入口：
   ```toml
   open-pharma-plugins-<yourname> = "<import_name>.__main__:main"
   ```
2. `pyproject.toml` `[project.optional-dependencies]` — 添加 extra 组：
   ```toml
   <yourname> = ["...你的依赖..."]
   ```
3. `pyproject.toml` `[tool.setuptools] package-dir` — 映射导入名到目录：
   ```toml
   "<import_name>" = "src/capabilities/<yourname>/<import_name>"
   ```
4. `pyproject.toml` `[tool.setuptools.packages.find] where` — 添加功能目录
5. 在 `plugin-versions.json` 中添加初始版本，并在 `.claude-plugin/marketplace.json` 与
   `.github/plugin/marketplace.json` 中添加锁定标签的条目；Copilot 条目复用功能目录中的 Claude 清单
6. 运行 `scripts/check_manifests.py`，然后按 [插件发布](releasing.md) 创建首个标签

`__main__.py` 从现有功能 **原样复制**，并必须公开可由控制台入口调用的 `main()`，同时保持导入安全。

## 复用共享库代码

`src/shared/` 中的共享库：

- `shared.env` — 配置 + `get_env`（读取环境变量的单一入口；优先级：环境变量 > 配置文件 > 默认值）
- `shared.filesystem` — 路径包含校验、私有目录和原子 `0600` 文件写入

使用 `from shared.env import get_env` 读取运行时设置，而非直接调用 `os.environ`。可变运行时数据不得默认
写入仓库或 site-packages；用 `shared.filesystem` 构造受约束路径和私有原子写入。新工具还应加入入口点、
真实 MCP 协议和隔离 wheel 冒烟测试。
