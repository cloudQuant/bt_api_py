---
name: repository-scripts
description: Route maintenance, test, release, and diagnostic requests in bt_api_py to the canonical scripts under scripts/. Use when an agent needs to execute, extend, document, or retire repository scripts.
---

# Repository scripts runbook for AI agents

## Scope and source of truth

- Read `scripts/README.md` before selecting a script. It is the human-facing command catalog and records side effects.
- Treat the top-level `scripts/` path as canonical unless README explicitly identifies a subdirectory. Do not recreate removed `scripts/testing/` or `scripts/tools/` duplicates.
- `scripts/analysis/` is the sole exception: its eight files are tested compatibility wrappers for the matching top-level Python modules. Preserve their import and CLI forwarding behavior.
- `scripts/ci/` participates in CI contracts. Read its associated tests and workflow before changing it; never silently refresh ratchet/performance baselines.

## Selection and execution

1. Confirm the working tree and branch with read-only Git commands before a script that writes files, stages Gitlinks, changes branches, accesses credentials, or contacts a network service.
2. Prefer an available `--check`, `--help`, or read-only analysis mode before a mutating mode.
3. Run scripts from the repository root unless their help explicitly says otherwise. Use `.bat` on Windows PowerShell/cmd and `.sh` on Unix/Git Bash.
4. Capture the meaningful output, then inspect the resulting diff/status. Do not infer success solely from a printed summary.

## Side-effect classes

| Class | Examples | Agent rule |
| --- | --- | --- |
| Read-only local | analysis, `check_*`, `verify_*`, Gitlink `--check` | May run when relevant. |
| Local write/stage | doc generators, `fill_missing_docstrings.py`, `fix_plugin_entries.py`, `update_gitlinks`, branch switcher | Run only for an explicit implementation/release request; inspect diff before commit. |
| External/credentialed | `git_push_all`, IBKR helpers, OKX diagnosis, Playwright scraping, monitoring | Require scope that explicitly covers the remote system/account; never expose secrets, cookies, tokens, or account data. |
| Destructive/broad rewrite | log cleanup, CTP splitting, version bump orchestration, code optimization | Resolve exact targets first; preserve unrelated edits and report the scope. |

## Release path

For a requested release, use this order: child repositories are clean, tested, committed, and pushed; `switch_all_branches <branch>`; `update_gitlinks --check`; `update_gitlinks`; inspect `git diff --cached --submodule=log`; then commit the root release changes and run the existing CI/release workflow. `update_gitlinks` only stages Gitlinks; it never grants authority to commit, tag, publish, or push.

## Adding or retiring scripts

- First search for an existing canonical tool and extend it when the behavior is closely related.
- New scripts need a narrow purpose, safe default behavior, clear help/usage, and a documented side-effect boundary. Add a `.bat` launcher only when the command is intended for Windows users.
- Do not keep byte-for-byte copies in several directories. Use a tested wrapper only when a public historical path must remain compatible.
- Before deletion, prove no active workflow, Makefile target, test, operational guide, or supported CLI relies on the path. Update tests and `scripts/README.md` in the same change.
- Validate changed shell scripts with `bash -n`; validate Windows launchers through `cmd /c`; run focused tests for changed Python/CI behavior.
