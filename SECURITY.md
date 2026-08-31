# Security Policy

## Supported versions

Security fixes target the latest public-beta release and current `main`. Older releases may not receive fixes.

## Reporting a vulnerability

Do not open a public issue with vulnerability details. Use [GitHub private vulnerability reporting](https://github.com/PharmaGenAI/open-pharma-plugins/security/advisories/new) and include:

- the affected version or commit;
- impact and prerequisites;
- a minimal proof of concept with secrets and personal data removed; and
- a suggested mitigation, if known.

If private reporting is unavailable, open a public issue without technical details and request a private contact channel. Allow maintainers time to investigate and coordinate disclosure.

## Security expectations

- Treat HCP, employee, territory, campaign, and source-document data according to applicable privacy, consent, access, and retention requirements.
- Keep runtime directories private and do not place credentials in tool inputs, filenames, logs, reports, or search queries.
- Verify downloaded installers and release artifacts before execution. The project publishes SHA-256 checksums, an SBOM, and build provenance with tagged releases.
- Automated claim and source checks are defense-in-depth. They do not replace qualified human medical/legal/regulatory review.

See [Data security and compliance boundaries](docs/en/data_security.md) for storage locations, deletion, provider transmission, and demo-data limitations.
