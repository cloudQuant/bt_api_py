# Mac test checkpoint — 2026-10-19

This is a user-requested snapshot of the current development code for testing on another machine. It includes useful unfinished implementation and regression tests; it is not a new functional, real-account, dual-host, load, or macOS acceptance result. Previous acceptance records retain their original scope and outcome.

## Scope and checks

The SDK snapshot includes pending service configuration, registration, lifecycle, execution-group, risk recovery/journal and normalization changes; associated DIM04/DIM05/DIM07, AFI13, quota and service tests; and existing development notes. The matching Execution, Monitor and Risk package snapshots are recorded by the parent gitlinks after the package commits are pushed.

All 35 changed or new Python source/test files in this SDK snapshot passed AST parsing on Python 3.11. No account, native CTP, service Host, load or previously accepted test suite was run for this checkpoint. Static review and syntax checks do not establish runtime correctness. Existing source bytes and intentional line endings are preserved, including an existing final blank line in the quality test.

Temporary source exports, diagnostic runners, caches, local runtime state, SQLite WAL/SHM sidecars, duplicate root-level source files and unrelated historical raw QA artifacts remain local. No credentials or machine-local account configuration are included. The previously prepared borrowed-writer-lease report and its small public QA archive are retained together so its evidence links remain usable.

## Pushed development commits

These branches were pushed to their configured `origin/dev` remotes. The parent repository records the three SDK package revisions as submodule commits:

- `backtrader`: `6235c67d20651e309d7f9d8fe1f7ac1f5c9d70be`
- `ai-for-investor`: `91cb89b882f9c4a2751abb05dce3d1824d4cdf9e`
- `bt_api_py`: `b0808267b7ceda1320070d9ad4af4b421e116020` plus the parent submodule update commit
- `bt_api_execution`: `6134fb40e441980a05960e65200a91996d1ffb74`
- `bt_api_monitor`: `fcc092ebfd87c5f67041565d369c492422c91cf1`
- `bt_api_risk`: `b2634ea2d5c0d12c7e9b62f7f1f96fcdce518851`

## Retrieve on the Mac

For each existing clone, switch to `dev` and pull it without rewriting local history:

```sh
git switch dev
git pull --ff-only origin dev
```

In the `bt_api_py` clone, retrieve the exact package revisions selected by the parent:

```sh
git submodule sync --recursive
git submodule update --init --recursive
```

Use the repository installation instructions to build a new Mac environment; do not copy the Windows virtual environment or native binaries. This snapshot does not certify every test or native provider dependency for macOS. Configure machine-specific paths and private credentials separately. Real-account testing remains distinct from local synthetic tests and must preserve the existing session and order-recovery restrictions.
