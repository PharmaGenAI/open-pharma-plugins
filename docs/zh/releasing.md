# 插件发布

open-pharma-plugins 发布一个 Python 发行版，但各功能独立版本化。功能发布涵盖其 Skill、
清单、MCP 配置、服务器代码以及该标签可见的共享代码。

## 版本模型

| 版本 | 范围 | 权威来源 |
|---|---|---|
| 插件版本 | 单个功能 | [`plugin-versions.json`](../../plugin-versions.json) → `plugins.<cap>` |
| 发行版本 | 仓库快照和共享 Python 发行版 | 同一文件中的 `distribution_version` |
| Marketplace 元数据版本 | 目录快照；不表示每个插件都已更改 | 发行版本 |
| 插件标签 | 单个功能的不可变源码快照 | `open-pharma-plugins-<cap>-v<semver>` |

Claude 与 GitHub Copilot Marketplace 条目和 MCP `uvx --from` 规格都必须锁定同一插件标签。`main` 仅用于开发。

各功能使用 SemVer：patch 用于兼容修复，minor 用于新增工具或行为，major 用于破坏性模式更改。
共享运行时更改需要为所有受影响的功能发布版本。

## 发布清单

1. 在 PR 分支上准备所有受影响的功能：

   ```bash
   git fetch origin --tags --prune
   uv run python scripts/prepare_plugin_release.py <cap> <version> --distribution-version <distribution-version>
   uv run python scripts/check_manifests.py
   uv run pytest -m "not reachability"
   uv run ruff format --check . && uv run ruff check .
   uv run zizmor --pedantic .github/workflows
   uv build && uv run twine check dist/*
   uv run python scripts/smoke_wheel.py dist/*.whl
   uv run pip-audit
   ```

2. 将代码和生成的发布元数据一起提交，开启 PR 并等待合并。

3. 在 `origin/main` 上当前存在的提交上创建注释标签：

   ```bash
   uv run python scripts/tag_plugin_release.py territory-alignment --dry-run
   uv run python scripts/tag_plugin_release.py territory-alignment --push
   ```

4. 标签工作流会先确认标签提交是匿名获取的权威 `origin/main` 的祖先。随后它会验证
   `https://github.com/PharmaGenAI/open-pharma-plugins.git` 上两个已发布 Marketplace 目录中的每个 ref，
   在构建 wheel/sdist、生成 `SHA256SUMS`、CycloneDX SBOM、GitHub 构建来源证明及创建 GitHub Release 前，
   拒绝缺失标签或 Claude/Copilot 目录漂移。此实时验证仅在标签存在后运行，因此准备新版本的 PR CI 保持
   离线且可通过。PyPI 使用独立的手动 workflow；受保护的 `pypi` 环境批准后，无发布身份的验证作业会核验
   标签元数据、目录 ref、校验和及适用的来源证明，trusted publishing 作业只下载已验证的构件并上传，
   不会执行标签控制的仓库代码。如需为失败的发布执行回填，请指定现有不可变标签手动触发
   **Tagged release**；该路径受保护的 `github-release` 环境控制。构建和验证使用所选标签，但添加
   GitHub SBOM attestation 所需的确定性 CycloneDX `serialNumber` 时使用受信任默认分支的发布工具，
   绝不移动或重新创建标签。PyPI 来源验证要求准确的 release workflow 来自正常标签 ref 或受信任的
   `refs/heads/main` 手动回填 ref；两者均无法验证时发布失败。

5. 使用 [安装指南](installation.md) 对发布标签及下载校验和进行冒烟测试。绝不移动已发布标签；应发布
   新的 patch 版本。

## 发布节奏

大约每周批量发布就绪的更改，空周跳过，关键修复随时发布。多个功能标签可以指向同一合并提交。
