# Releasing

The repository publishes one Python distribution and independently tagged capability plugins. `plugin-versions.json` is the release index; every plugin manifest, MCP launch spec, and entry in the Claude and GitHub Copilot marketplace catalogs must pin the matching immutable tag.

## Prepare

Use SemVer: patch for compatible fixes, minor for additive behavior, and major for breaking contracts. Shared runtime changes require a release for every affected capability.

```bash
git fetch origin --tags --prune
uv run python scripts/prepare_plugin_release.py <cap> <version> --distribution-version <version>
uv run python scripts/check_manifests.py
uv run pytest -m "not reachability"
uv run ruff format --check .
uv run ruff check .
uv run zizmor --pedantic .github/workflows
bash -n install.sh
uv build
uv run twine check dist/*
uv run python scripts/smoke_wheel.py dist/*.whl
uv run pip-audit
```

Commit code and generated release metadata together, open a pull request, and wait for required CI. Do not publish from an unreviewed working tree.

## Tag and publish

After the exact commit is on `origin/main`:

```bash
uv run python scripts/tag_plugin_release.py <cap> --dry-run
uv run python scripts/tag_plugin_release.py <cap> --push
```

Before either release path executes repository code from a selected tag, an unprivileged job anonymously fetches the tag and canonical `origin/main` from `https://github.com/PharmaGenAI/open-pharma-plugins.git` and rejects a tag commit that is not an ancestor of `main`. The tag-triggered release workflow then rebuilds from the authorized tag and verifies every ref in both published marketplace catalogs. It rejects missing tags and Claude/Copilot catalog drift before building artifacts or creating a GitHub release. This live check intentionally runs only after a tag exists, so ordinary feature-branch CI remains offline and can pass while preparing a new capability release.

For a failed release that needs a backfill, dispatch **Tagged release** manually with the existing immutable tag. The protected `github-release` environment gates that path. It checks out the selected tag for build and validation, but uses trusted default-branch release tooling to add the deterministic CycloneDX `serialNumber` required by GitHub SBOM attestation; it never moves or recreates the tag. PyPI repeats the tag-metadata and catalog-ref gates in its unprivileged verification job, verifies checksums and applicable provenance, then gives the protected trusted-publishing job only those verified artifacts to upload. Artifact provenance must be signed by the exact release workflow from either the normal tag ref or the trusted `refs/heads/main` manual-backfill ref; if neither verifies, publication fails.

After the immutable GitHub release is created and the tagged release workflow succeeds, a separate `workflow_run` notifier checks out the trusted default branch and notifies the public business site repository with a `repository_dispatch` event of type `open-pharma-plugins-release`. The notifier accepts the completed run only when its tag commit is on `origin/main`, then reads release metadata from that exact commit. The event payload is fixed to `repository`, `capability`, `tag`, `commit`, `distribution_version`, and `plugin_version`, so the site workflow can open or update its own review PR from exact tagged metadata. Keeping this secret-bearing notifier on the default branch prevents arbitrary tag contents from receiving the cross-repository credential.

Keep the website credentials split by repository and purpose:

- Canonical repository secret `OPEN_PHARMA_PAGES_DISPATCH_TOKEN`: a fine-grained token scoped only to `PharmaGenAI/pharmagenai.github.io` with the minimum permission GitHub currently requires for `repository_dispatch` (`Contents: write`, per the GitHub REST API docs). The canonical release workflow uses it only to send the dispatch notification.
- Site repository secret `OPEN_PHARMA_PAGES_SYNC_TOKEN`: a separate fine-grained token scoped only to `PharmaGenAI/open-pharma-plugins` with read-only contents access. The public site workflow uses it only to read the canonical tagged source and generated metadata it needs for its review branch.
- Site repository `GITHUB_TOKEN`: used only to push the synchronized branch inside `PharmaGenAI/pharmagenai.github.io`.
- Site repository secret `OPEN_PHARMA_PAGES_PR_TOKEN`: a separate fine-grained token scoped only to `PharmaGenAI/pharmagenai.github.io` with `Pull requests: write`. The site workflow uses it only to find or create its review PR. This is required because the organization currently disables PR creation by the Actions `GITHUB_TOKEN`.

Never reuse one broad token across both repositories or both directions of access. If `OPEN_PHARMA_PAGES_DISPATCH_TOKEN` is absent, the separate notifier emits an Actions notice and skips the website notification without changing the already-successful release. If that secret is configured but rejected by the API or GitHub returns a non-`204` response, only the notifier fails visibly after the release artifacts and immutable GitHub release have already been created. Re-run the notifier after correcting a transient or credential failure; do not rerun release creation and never move tags to recover.

GitHub artifact attestation may be unavailable for a private organization on its current plan. The
workflow still attempts both provenance and SBOM attestations, but an attestation failure is
non-blocking only while the repository is private so the checksum- and SBOM-backed GitHub release
can still be created. For private repositories, the PyPI workflow verifies the tagged release's
checksums and exact artifacts before protected-environment trusted publishing; for other
visibilities, it additionally requires GitHub artifact attestations.

Never move a published tag. Issue a patch release instead. Verify the GitHub release artifacts and install the published tag before announcing availability.
