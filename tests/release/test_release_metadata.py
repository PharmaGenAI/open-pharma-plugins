"""Distribution metadata must match the supported public-beta install contract."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[2]


def test_every_github_action_is_pinned_to_an_exact_commit():
    pattern = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        for use in pattern.findall(workflow.read_text()):
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", use), f"{workflow.name}: unpinned action {use}"


def test_pypi_publish_requires_manual_protected_environment():
    workflow = (ROOT / ".github" / "workflows" / "publish-pypi.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "environment:\n      name: pypi" in workflow
    assert "id-token: write" in workflow
    assert "gh attestation verify" in workflow
    assert '--signer-workflow "$GITHUB_REPOSITORY/.github/workflows/release.yml"' in workflow
    assert '--source-ref "refs/tags/$RELEASE_TAG"' in workflow
    assert '--source-ref "refs/heads/main"' in workflow
    assert 'if ! gh attestation verify "$artifact"' in workflow
    assert "uv publish dist/*.whl dist/*.tar.gz" in workflow


def test_release_supports_a_protected_manual_backfill_without_changing_tag_build_inputs():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert "workflow_dispatch:" in workflow
    assert "description: Existing immutable release tag to backfill" in workflow
    assert "required: true" in workflow
    assert "group: release-${{ github.event_name == 'workflow_dispatch' && inputs.tag || github.ref_name }}" in workflow
    assert "RELEASE_TAG: ${{ github.event_name == 'workflow_dispatch' && inputs.tag || github.ref_name }}" in workflow
    assert "name: github-release" in workflow
    assert "ref: ${{ env.RELEASE_TAG }}" in workflow
    assert "path: release-tools" in workflow
    assert 'uv run python scripts/check_release_tag.py "$RELEASE_TAG"' in workflow
    assert "python3 release-tools/scripts/check_catalog_refs.py" in workflow
    assert "uv run python scripts/check_catalog_refs.py" not in workflow
    assert workflow.index("- name: Check out the selected tagged commit") < workflow.index(
        "- name: Check out trusted release tools"
    )
    assert 'if gh release view "$RELEASE_TAG"' in workflow
    assert 'gh release upload "$RELEASE_TAG" dist/* --clobber' in workflow
    assert 'gh release create "$RELEASE_TAG" dist/* --verify-tag' in workflow
    assert (
        'python3 release-tools/scripts/finalize_cyclonedx_sbom.py dist/open-pharma-plugins.cdx.json --seed "$RELEASE_TAG"'
        in workflow
    )


def test_release_and_publish_prove_tag_ancestry_before_tag_controlled_code():
    canonical_url = "https://github.com/PharmaGenAI/open-pharma-plugins.git"
    for workflow_name, release_job in (("release.yml", "release"), ("publish-pypi.yml", "verify")):
        workflow = (ROOT / ".github" / "workflows" / workflow_name).read_text()
        authority = workflow.split("  authorize:\n", 1)[1].split(f"\n  {release_job}:\n", 1)[0]

        assert canonical_url in authority
        assert "GIT_CONFIG_NOSYSTEM=1" in authority
        assert "GIT_CONFIG_GLOBAL=/dev/null" in authority
        assert "credential.helper=" in authority
        assert "core.askPass=" in authority
        assert "git merge-base --is-ancestor" in authority
        assert "scripts/" not in authority

        job = workflow.split(f"  {release_job}:\n", 1)[1]
        assert "needs: authorize" in job.split("steps:", 1)[0]


def test_pypi_publish_identity_is_limited_to_verified_artifact_upload():
    workflow = (ROOT / ".github" / "workflows" / "publish-pypi.yml").read_text()
    verify = workflow.split("  verify:\n", 1)[1].split("\n  publish:\n", 1)[0]
    publish = workflow.split("  publish:\n", 1)[1]

    assert "id-token: write" not in verify
    assert 'uv run python scripts/check_release_tag.py "$RELEASE_TAG"' in verify
    assert "uv run python scripts/check_catalog_refs.py" in verify
    assert "gh release download" in verify
    assert "sha256sum --check SHA256SUMS" in verify
    assert "uv run twine check dist/*.whl dist/*.tar.gz" in verify
    assert "uv run python scripts/smoke_wheel.py dist/*.whl" in verify
    assert "actions/upload-artifact@" in verify
    assert "id-token: write" in publish
    assert "actions/download-artifact@" in publish
    assert "uv publish dist/*.whl dist/*.tar.gz" in publish
    assert "actions/checkout@" not in publish
    assert "scripts/" not in publish
    assert "gh release" not in publish


def test_private_publish_keeps_checksum_gate_when_attestations_are_unavailable():
    workflow = (ROOT / ".github" / "workflows" / "publish-pypi.yml").read_text()
    checksum_step = "      - name: Download and verify GitHub release asset checksums\n"
    attestation_step = "      - name: Verify GitHub artifact attestations\n"
    private_guard = "        if: ${{ github.event.repository.visibility != 'private' }}\n"

    assert checksum_step in workflow
    assert attestation_step + private_guard in workflow
    assert workflow.index(checksum_step) < workflow.index(attestation_step)


def test_public_publish_can_read_github_artifact_attestations():
    workflow = (ROOT / ".github" / "workflows" / "publish-pypi.yml").read_text()
    assert "      attestations: read" in workflow


def test_private_release_tolerates_unavailable_github_attestations_only():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    private_only_guard = "continue-on-error: ${{ github.event.repository.visibility == 'private' }}"

    assert workflow.count("uses: actions/attest@") == 2
    assert workflow.count(private_only_guard) == 2


def test_release_notification_uses_trusted_default_branch_after_release_workflow_succeeds():
    release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    workflow = (ROOT / ".github" / "workflows" / "notify-site-release.yml").read_text()

    assert "Create immutable GitHub release" in release_workflow
    assert "OPEN_PHARMA_PAGES_DISPATCH_TOKEN" not in release_workflow
    assert "dispatch_site_release.py" not in release_workflow
    assert "workflow_run:" in workflow
    assert 'workflows: ["Tagged release"]' in workflow
    assert "types: [completed]" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "ref: ${{ github.event.repository.default_branch }}" in workflow
    assert "fetch-depth: 0" in workflow
    assert "persist-credentials: false" in workflow
    assert "python scripts/dispatch_site_release.py" in workflow
    for expected in (
        "OPEN_PHARMA_PAGES_DISPATCH_TOKEN: ${{ secrets.OPEN_PHARMA_PAGES_DISPATCH_TOKEN }}",
        "RELEASE_TAG: ${{ github.event.workflow_run.head_branch }}",
        "RELEASE_COMMIT: ${{ github.event.workflow_run.head_sha }}",
        "SOURCE_DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}",
    ):
        assert expected in workflow


def test_secret_bearing_dispatch_run_block_uses_env_not_inline_expressions():
    workflow = (ROOT / ".github" / "workflows" / "notify-site-release.yml").read_text()
    marker = "      - name: Notify public business site\n"
    tail = workflow.split(marker, 1)[1]
    block = tail.split("\n      - name:", 1)[0]

    assert "${{" not in block.split("        run:", 1)[1]


def _metadata() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _names(requirements: list[str]) -> set[str]:
    return {Requirement(item).name.lower().replace("_", "-") for item in requirements}


def test_supported_python_and_beta_maturity_are_explicit():
    project = _metadata()["project"]
    assert project["requires-python"] == ">=3.10,<3.14"
    assert "Development Status :: 4 - Beta" in project["classifiers"]
    assert "Development Status :: 5 - Production/Stable" not in project["classifiers"]


def test_runtime_declares_direct_pydantic_dependency():
    assert "pydantic" in _names(_metadata()["project"]["dependencies"])


def test_hcp_batch_entry_point_is_registered():
    scripts = _metadata()["project"]["scripts"]
    assert scripts["open-pharma-plugins-hcp-batch"] == ("open_pharma_plugins_hcp_intelligence.batch_cli:main")


def test_hcp_fixture_csv_is_registered_as_package_data():
    package_data = _metadata()["tool"]["setuptools"]["package-data"]
    assert "fixtures/*.csv" in package_data["open_pharma_plugins_hcp_intelligence"]


def test_all_extra_covers_every_optional_runtime():
    extras = _metadata()["project"]["optional-dependencies"]
    assert {
        "requests",
        "openai",
        "pypdfium2",
        "python-pptx",
        "python-docx",
        "reportlab",
        "jinja2",
    } <= _names(extras["all"])


def test_license_and_notice_preserve_the_apache_2_appendix_and_project_copyright():
    license_text = (ROOT / "LICENSE").read_bytes()
    assert (
        hashlib.sha256(license_text).hexdigest() == "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
    )
    assert (ROOT / "NOTICE").read_text() == "Copyright 2026 Jason Zhang\n"
    assert set(_metadata()["project"]["license-files"]) >= {"LICENSE", "NOTICE"}


def test_localized_release_docs_describe_live_catalog_ref_gates_without_stale_examples():
    for locale in ("jp", "zh"):
        document = (ROOT / "docs" / locale / "releasing.md").read_text()
        assert "https://github.com/PharmaGenAI/open-pharma-plugins.git" in document
        assert "1.1.0" not in document
        assert "2.0.1" not in document


def test_campaign_studio_110_keeps_merged_distribution_and_release_metadata_aligned():
    versions = json.loads((ROOT / "plugin-versions.json").read_text(encoding="utf-8"))
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    capability = ROOT / "src" / "capabilities" / "campaign-studio"
    claude = json.loads((capability / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    codex = json.loads((capability / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    mcp = json.loads((capability / ".mcp.json").read_text(encoding="utf-8"))
    tag = "open-pharma-plugins-campaign-studio-v1.1.0"

    assert versions["distribution_version"] == "2.4.1"
    assert marketplace["metadata"]["version"] == versions["distribution_version"]
    assert versions["plugins"]["campaign-studio"] == "1.1.0"
    assert {name: version for name, version in versions["plugins"].items() if name != "campaign-studio"} == {
        "hcp-intelligence": "1.0.2",
        "field-training": "1.1.1",
        "next-best-engagement": "1.0.2",
        "territory-alignment": "1.2.0",
        "competitive-intelligence": "1.1.0",
    }
    assert claude["version"] == codex["version"] == "1.1.0"
    assert tag in claude["mcpServers"]["open-pharma-plugins-campaign-studio"]["args"][-2]
    assert tag in mcp["mcpServers"]["open-pharma-plugins-campaign-studio"]["args"][-2]
    entry = next(item for item in marketplace["plugins"] if item["name"].endswith("campaign-studio"))
    assert entry["source"]["ref"] == tag
    init = (capability / "open_pharma_plugins_campaign_studio" / "__init__.py").read_text(encoding="utf-8")
    assert '__version__ = "1.1.0"' in init
    assert re.search(r"^\| \[Campaign Studio\]\([^\n]+\) \| 16 \|", (ROOT / "README.md").read_text(), re.MULTILINE)


def test_campaign_studio_ships_every_production_template():
    package_data = _metadata()["tool"]["setuptools"]["package-data"]["open_pharma_plugins_campaign_studio"]
    assert "templates/*" in package_data
    templates = ROOT / "src" / "capabilities" / "campaign-studio" / "open_pharma_plugins_campaign_studio" / "templates"
    assert {path.name for path in templates.glob("*.j2")} >= {
        "email.html.j2",
        "banner.svg.j2",
        "mlr-review.html.j2",
    }
