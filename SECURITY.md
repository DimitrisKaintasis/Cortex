# Security

Cortex currently supports trusted local use. The CLI API binds to `127.0.0.1` and
has no authentication or tenant authorization. Do not expose it through a public
reverse proxy, port forwarding, or a tunnel. Publishing this source repository
does not make the API suitable for public deployment.

Keep credentials in environment variables and keep private source data, runtime
stores, and backups outside version control. Model providers receive the text
sent to them when their optional enrichment features are enabled.

Use a fresh virtual environment for installations and upgrades. CI audits the
installed optional dependencies with `pip-audit`; Dependabot checks for updates
weekly. The vendored Temporal History runtime is scanned as Cortex source, while
its PyPI-hosted runtime dependencies are included in the dependency audit.

The optional Mem0 graph bridge pins `mem0ai==1.0.1` because Mem0 2.x removed the
graph interface it uses. Advisory `PYSEC-2026-2636` affects that release's FAISS
pickle persistence. Cortex rejects missing or non-Qdrant vector-store configuration
before Mem0 is initialized, and CI carries a named exception for this unreachable
backend. Do not weaken that Qdrant-only boundary. The exception should be removed
when the graph bridge is migrated to a supported Mem0 2.x interface.

## Reporting a vulnerability

Use this repository's GitHub **Security → Report a vulnerability** feature for
sensitive reports when available. Do not put credentials, private payloads, or
exploit details into a public issue. If private reporting is unavailable, open
an issue requesting a private reporting channel without sensitive details.
