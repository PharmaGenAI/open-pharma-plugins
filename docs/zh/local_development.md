# 本地开发

从仓库根目录运行命令。锁文件覆盖 Python 3.10–3.13；使用专用克隆进行完整插件安装测试。

## 快速源码循环

安装锁定的完整开发环境：

```bash
uv sync --all-extras --dev --locked
```

直接从源码运行服务器：

```bash
uv run python -m open_pharma_plugins_territory_alignment --version
uv run python -m open_pharma_plugins_territory_alignment --check-system
```

代码更改在下次进程启动时生效。开发时运行针对性测试；详见 [测试](testing.md)。

如果只需要 MCP 连接，在 harness 中注册源码入口并在编辑后重新连接：

```bash
claude mcp add open-pharma-plugins-territory-alignment -- \
  uv run --directory "$(pwd)" python -m open_pharma_plugins_territory_alignment
# 清理: claude mcp remove open-pharma-plugins-territory-alignment
```

## 完整插件安装循环

使用此路径测试 marketplace 清单、Skill 发现、MCP 注册及 harness 的完整安装流程：

```bash
bash install.sh local
```

安装程序将选定的功能指向当前检出并添加 `uvx --refresh`。它会有意在跟踪的清单中写入绝对本地路径，
因此请使用专用克隆且安装期间不要移动它。

提交或离开本地模式前恢复发行来源：

```bash
bash install.sh local --restore
```
