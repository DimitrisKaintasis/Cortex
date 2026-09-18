# Cortex pre-public security review

## Cleanup status

The owner approved rewriting branch history. Author and committer emails on both branches
were replaced with the account's GitHub noreply address, and repository-local Git configuration
now uses that address for future commits. A verified backup bundle is stored outside the
repository and retains the original metadata; keep it private.

The generated Qdrant lock and metadata files were removed from the index while preserving
local copies. Ignore rules now cover Qdrant artifact directories, common private-key formats,
database dumps, and local JSON configuration. The original two harmless markers remain in
historical commits. The README clone-directory command was also corrected to `cd Cortex`.

The findings below describe the original review. PUB-PRIVACY-001 and REPO-HYGIENE-001
are addressed by this cleanup; dependency auditing remains follow-up work.

Scope limitations: signature scans cannot prove absence of all private data. The repository
size reported below is compressed storage, not an exhaustive bound on historical blob sizes.
Pydantic field limits do not limit the raw request body before parsing or bound arbitrary
metadata. The application review was targeted, not a penetration test or production certification.
The shared Python environment audit reported advisories including Starlette 0.50.0; their
applicability and clean-install dependency resolution still require review.

## Executive summary

No credentials, private keys, database files, backup dumps, or high-confidence secret patterns
were found in the current tree or reachable Git history. Gitleaks 8.30.1 scanned all 40 commits
and approximately 2.12 MB of history with full output redaction and reported no leaks. The local
FastAPI transport preserves its documented trust boundary: the supported CLI hard-codes the
listener to `127.0.0.1`, PostgreSQL is published only on loopback, request bodies use explicit
Pydantic models that reject unknown fields, and subprocess calls use argument vectors rather than
a shell.

No Critical or High findings were identified. One Medium privacy finding requires an explicit
decision because fixing it rewrites Git history. Two Low findings are repository-hygiene and
dependency-maintenance improvements rather than evidence of an active vulnerability.

## Critical findings

None.

## High findings

None.

## Medium findings

### PUB-PRIVACY-001 — Personal email remains in Git commit metadata

- **Rule ID:** PUB-PRIVACY-001
- **Severity:** Medium
- **Location:** Author and committer metadata across the reachable Git history (40 commits); this
  is Git object metadata rather than a source-file line.
- **Evidence:** The history contains one unique personal Gmail address. The address is deliberately
  not reproduced in this report. No email addresses remain in current tracked file content; the
  apparent historical content matches were metric notation such as `hit@10`, not real addresses.
- **Impact:** Publishing the repository exposes the address to cloning, scraping, spam, phishing,
  and correlation with other accounts.
- **Fix:** Rewrite author and committer email fields to GitHub's account-specific `noreply` address,
  then force-push every public branch and tag. Configure Git to use the `noreply` address for future
  commits.
- **Mitigation:** Enable GitHub's email-privacy and command-line push-protection settings before
  future work. Keeping the repository private prevents immediate exposure but does not clean the
  metadata.
- **False-positive notes:** This is not a secret credential and does not enable account access, but
  it is genuine personal information embedded in every cloneable commit object.

## Low findings

### SUPPLY-CHAIN-001 — Optional environments have no reproducible security audit

- **Rule ID:** SUPPLY-CHAIN-001 / FASTAPI-SUPPLY-001
- **Severity:** Low
- **Location:** `pyproject.toml` lines 17–62; `.github/workflows/ci.yml` lines 24–37 and 39–66.
- **Evidence:** Optional dependency groups use bounded direct dependencies but do not lock their
  transitive dependency graph. CI installs the current resolver result and runs tests, linting, and
  typing, but does not run a vulnerability audit. The default SQLite installation has no runtime
  dependencies. A local whole-environment `pip-audit` found stale vulnerable packages, but that
  Python installation contains unrelated software and is not treated as a reproducible Cortex
  result.
- **Impact:** A developer can retain a previously resolved optional dependency after a security fix
  is available, and a newly disclosed advisory will not automatically fail CI.
- **Fix:** Add a dedicated dependency-audit CI job after the repository is public and CI is
  available. Audit a clean resolved environment rather than the developer's shared Python
  installation. Consider a constraints file for the hosted deployment when that product exists;
  do not force application-style lock files on library consumers.
- **Mitigation:** Keep the existing lower/upper bounds, regularly recreate local environments, and
  enable Dependabot security updates after publication.
- **False-positive notes:** This is maintenance risk, not proof that a clean installation currently
  resolves to a vulnerable package.

### REPO-HYGIENE-001 — Generated Qdrant state markers are tracked

- **Rule ID:** REPO-HYGIENE-001
- **Severity:** Low
- **Location:** `artifacts/longmemeval-dev20-joint-mem0-qdrant/.lock` and
  `artifacts/longmemeval-dev20-joint-mem0-qdrant/meta.json`.
- **Evidence:** The directory contains a transient lock marker and generated collection metadata.
  It contains no vector payloads or secrets today and is not referenced by active documentation.
- **Impact:** Keeping runtime state under version control creates a path for later commits to
  accidentally include private embeddings, payload metadata, or machine-specific state.
- **Fix:** Remove this generated directory from version control and ignore Qdrant runtime artifact
  directories while continuing to commit intentionally curated JSON benchmark reports.
- **Mitigation:** Review `git status` before commits and keep executable benchmark evidence
  separate from runtime stores.
- **False-positive notes:** The two currently tracked files are not sensitive; the finding is about
  preventing future accidental disclosure.

## Verified controls and non-findings

- Gitleaks 8.30.1: 40 commits scanned, no leaks found.
- Manual high-confidence history scan: no private-key headers, GitHub tokens, OpenAI-style tokens,
  AWS access keys, or Slack tokens.
- Sensitive filename scan: only `.env.example` is tracked; it contains blank or documented example
  values. Real `.env`, database, dump, key, and backup patterns are ignored.
- Repository size: approximately 2.57 MiB of loose Git objects; no oversized tracked artifacts.
- Local API: supported startup binds only to `127.0.0.1`; no public host option, CORS middleware,
  cookies, sessions, uploads, static-file serving, redirects, or WebSockets.
- Input handling: explicit Pydantic request schemas use `extra="forbid"` and bounded primary text,
  tag, query, identifier, and feedback fields.
- Command execution: backup operations pass fixed argument tuples with `shell=False` behavior and
  validate variable PostgreSQL identifiers.
- PostgreSQL Compose exposure: port `5432` is published only to `127.0.0.1`, and the password must
  be supplied outside Git.

## Recommended cleanup order

1. Verify and push the approved metadata rewrite and artifact cleanup.
2. Resolve optional dependency audit findings in a clean environment.
3. After the owner's publication decision, make the repository public and rerun CI.
