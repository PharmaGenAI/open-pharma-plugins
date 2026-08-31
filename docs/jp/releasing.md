# プラグインリリース

open-pharma-plugins は1つの Python ディストリビューションを公開しますが、各機能は独立して
バージョン管理されます。機能リリースは、Skill、マニフェスト、MCP 設定、サーバーコード、
およびそのタグで参照可能な共有コードを含みます。

## バージョンモデル

| バージョン | スコープ | 信頼できるソース |
|---|---|---|
| プラグインバージョン | 単一機能 | [`plugin-versions.json`](../../plugin-versions.json) → `plugins.<cap>` |
| ディストリビューションバージョン | リポジトリスナップショットと共有 Python ディストリビューション | 同ファイルの `distribution_version` |
| マーケットプレイスメタデータバージョン | カタログスナップショット、すべてのプラグインが変更されたことを意味しない | ディストリビューションバージョン |
| プラグインタグ | 単一機能の不変ソーススナップショット | `open-pharma-plugins-<cap>-v<semver>` |

Claude と GitHub Copilot のマーケットプレイスエントリ、および MCP `uvx --from` 仕様は同じプラグインタグに固定されます。
`main` は開発専用です。

各機能は SemVer を使用します：patch は互換性のある修正、minor は追加ツールまたは動作、
major は破壊的スキーマ変更用です。共有ランタイムの変更は、影響を受けるすべての機能の
リリースが必要です。

## リリースチェックリスト

1. PR ブランチですべての影響を受ける機能を準備します：

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

2. コードと生成されたリリースメタデータを一緒にコミットし、PR を開いてマージを待ちます。

3. `origin/main` 上の現在のコミットにアノテーションタグを作成します：

   ```bash
   uv run python scripts/tag_plugin_release.py territory-alignment --dry-run
   uv run python scripts/tag_plugin_release.py territory-alignment --push
   ```

4. タグワークフローは、タグのコミットが匿名で取得した正規 `origin/main` の祖先であることを先に
   確認します。次に `https://github.com/PharmaGenAI/open-pharma-plugins.git` 上の両方の公開
   Marketplace カタログのすべての ref を検証し、タグ欠落または Claude/Copilot のカタログ不整合を
   拒否してから、wheel/sdist、`SHA256SUMS`、CycloneDX SBOM、GitHub ビルド来歴と GitHub Release を
   作成します。このライブ検証はタグ作成後だけに実行されるため、リリース準備中の PR CI はオフラインの
   まま通過できます。PyPI は別の手動ワークフローです。保護された `pypi` 環境で承認後、権限のない
   検証ジョブがタグメタデータ、カタログ ref、チェックサムと該当する来歴を確認し、trusted publishing
   ジョブは検証済み成果物だけをダウンロードして公開します。失敗したリリースをバックフィルする必要が
   ある場合は、既存の不変タグを指定して **Tagged release** を手動実行します。この経路は保護された
   `github-release` 環境でゲートされます。ビルドと検証には選択したタグを使いますが、GitHub SBOM
   attestation が必要とする決定的な CycloneDX `serialNumber` の追加には、信頼できる既定ブランチの
   リリースツールを使います。タグの移動や再作成はしません。PyPI の来歴検証は、正確な release
   workflow による通常のタグ ref または信頼できる `refs/heads/main` の手動バックフィル ref の
   どちらかを要求し、どちらも検証できなければ公開は失敗します。

5. [インストールガイド](installation.md) で公開タグとダウンロードチェックサムをスモークテストします。
   公開済みタグを移動せず、新しい patch リリースを発行してください。

## リリース頻度

準備ができた変更をおおよそ毎週バッチリリースし、空の週はスキップし、
重要な修正は必要に応じてリリースします。複数の機能タグが同じマージコミットを指すことがあります。
