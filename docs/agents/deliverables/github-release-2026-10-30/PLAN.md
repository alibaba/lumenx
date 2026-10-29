# GitHub release preparation

Status: preparation in progress; no merge or release approval is implied.

## Fixed inputs and preservation

- Public base: `f2a02e23171447c939e7d8e1386b24d17049bbf1` (`github/main`).
- Reviewed development snapshot: `7765f9e816088a88f8489a737ad70eb7211de051`.
- Preserve the development branch, its complete evidence, other worktrees, and user data.
- Publish from a new branch rooted in the public base, without importing the development branch's 783-commit ancestry.
- Carry the final implementation and regression tests together. Split independent release tooling and public documentation into separate commits; do not introduce temporary compatibility code merely to divide the migration.

## Cleanup plan and checks

1. Import the final tracked implementation, tests, catalog artifacts and required browser-test tools. Exclude raw execution archives and personal runtime data. Verify every imported path against the fixed source blob; preserve executable modes and binary resources.
2. Prepare a compact public contract, architecture/decision references and migration/validation notes. Preserve the original full ledger in the development branch. Check retained links and test-tool dependencies; do not replace missing historical evidence with fabricated success records.
3. Reproduce installations in a new Python 3.11 environment and a clean frontend dependency tree using the CI Node version. Run isolated backend tests, frontend logic/component tests, type checking, build, catalog consistency and workflow parity. Record failures, skips and environment versions.
4. Strengthen CI timeouts, explicit media-tool prerequisites, result retention and an enforceable no-new-lint-diagnostics baseline. Add focused regression checks for the new gate itself. Preserve all product regressions.
5. Review the actual outgoing commit range for credentials, internal/private material and oversized objects. Record the source-to-public file mapping and any intentional differences. Review the final candidate independently.
6. Push only the prepared feature branch to the GitHub remote and create a draft PR. Verify the exact pushed commit through GitHub CI; investigate and repair failures. Keep main unchanged. Present any repository protection or merge decision with the tested candidate.

## Acceptance boundaries

### Public documentation cleanup

- Keep the accepted product contract, six ADRs, architecture and phase dependencies, 24 journey specifications, and 44 feature-to-source/test mappings. Preserve product decisions while removing machine-specific source links and private session history.
- Replace the full development ledger with a public candidate ledger that separates reported source-snapshot results from checks actually rerun on this release candidate. Preserve all original records in `7765f9e816088a88f8489a737ad70eb7211de051`; do not publish broken links or imply that omitted evidence is publicly reviewable.
- Retain a compact historical finding registry so the documentation checker can still verify phase/feature/scenario ownership without requiring the unpublished review archives. Check every retained local link, ADR and mapping. Verify checker failures with temporary copies containing missing documents and invalid mappings.
- Publish portable headless operation and migration instructions. Do not carry private ECS host details, local home directories, credentials, screenshots, raw logs, or the original `SESSION.md` into this branch.

The previous snapshot has backend 9,460 passing tests and frontend 2,104 logic plus 1,844 component tests. These are historical results, not fresh candidate results. The 416 existing frontend lint diagnostics are debt, not a passing raw lint command. Formal macOS cold start/signing/notarization, Windows, public HTTPS sessions and paid-provider quality are separate acceptance work; this preparation does not claim them.

## Rollback

Publication whitespace check: normalize only the ten paths reported by `git diff --check github/main...HEAD` (five public documents, two implementation files and three tests). Remove trailing whitespace and redundant final blank lines only; verify each change equals that mechanical transformation. Existing full suites protect behavior, and final Linux checks must use the resulting commit.

Discarding this separate release candidate leaves the original development branch and public main intact. No live data migration, deployment, history rewrite or cleanup of another worktree is part of this task.
