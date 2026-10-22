# Full-history secret scan, 2026-10-21

## Result

No secrets were found in the full history of either repository. A second, separate pattern search of the current files for secret-shaped values also found none.

| Repository                                         | Commits scanned | Findings | Exit code | Label    |
| -------------------------------------------------- | --------------- | -------- | --------- | -------- |
| `rcarlson-devops/self-service-idp`                 | 19              | 0        | 0         | Verified |
| `rcarlson-devops/remediation-test-1` (tenant repo) | 2               | 0        | 0         | Verified |

## How it was run

- Tool: `gitleaks` from the Ubuntu 24.04 apt package, version `8.16.0-1ubuntu0.24.04.3`. The binary itself reports "version is set by build process", so the version is taken from the package manager.
- Rules: the tool's default rule set.
- Command: `gitleaks detect --source . --redact --report-path <file outside the repo>`. Without `--no-git`, `detect` reads the git history. The "N commits scanned" line confirms it did.
- `--redact` masks any finding in the screen output and in the report. The report files were written outside the repository and deleted afterwards.
- The tenant repo was cloned into a temporary directory, scanned the same way, and the clone was deleted.

## Scrub check of current files

A pattern search of `docs/`, `scripts/`, `tests/`, the `Makefile` and `.github/` looked for token, password, private-key and unseal-key keywords and for long base64-style strings. Matches were reviewed without printing values.

- Every `GH_TOKEN` line in the workflows is a `${{ secrets.NAME }}` reference, not a value (Verified).
- The matches in `docs/setup.md` are prose, empty values, command substitutions that fetch a value at run time, and one single-character placeholder (Observed, from masked line shapes).
- No run of 32 or more base64-style characters appears in `docs/setup.md` (Observed).

## Limits of this result

- A default-rules scan on an older release can miss newer token formats or unusual secret shapes. The pattern search proves that nothing matched, not that nothing exists.
- This scan covers the two repositories listed. It does not cover Actions logs, issues, pull request comments or other places a secret could have been pasted.
- The full-history scan is run again in P10.7 with a current release, and the pull request scan arrives with the platform CI in P0.3.