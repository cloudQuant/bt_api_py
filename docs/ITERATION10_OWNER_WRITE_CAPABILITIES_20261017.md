# Iteration 10 account-owner optional write capabilities

Repository/branch: `D:/bt_api_py`, existing `dev`.

The account-owner builder advertised `owner.reduce` as a write operation even when the configured adapter had no approved-target authority and therefore did not support that operation. The resulting write/operation mismatch stopped normal Host startup with `BUILTIN_COMPONENT_INVALID`. The builder now intersects optional write operations with actual supported operations for every execution account-owner port. `owner.cancel` remains advertised, and `owner.reduce` remains available when its authority is present.

Independent review accepted the two-path patch `.codex-tmp/dim07-owner-write-capability-r3.patch` (SHA-256 `1970ae73ce50598575cfeacb095cde880323d5cd302e3f43d8946b2d9e331046`). A fixed-source run at SDK `05298c98` with its exact package gitlinks passed both parameter cases in 1.37 seconds, exit zero and empty stderr. The fixture uses the actual typed Basic/approved-target construction requirements and fails if any provider dispatch or reservation occurs.

Evidence is in `.codex-tmp/dim07-owner-write-capability-focused-unit-r5/`: raw stdout SHA-256 `42c81d3fb9f9942c11b4a32b38e3f8cb519ae3b8b2844fb58fbb87d6a12579b1`; structured result `88611c72665fbbd8dc481d013f8488e91cb096178159ae3dd3abe5fa45db789b`. Earlier export preparation errors ran no tests and remain recorded there.

This round accepts only the builder correction and focused regression. The prior DIM07 R3/R4 Host failures are preserved; a fresh Host run remains required. No formal case is added. This commit stages only the selected production hunk, test additions and this record; unrelated work stays unstaged. The exact integration commit is recorded in `.codex-tmp/owner-write-capabilities-root-postcommit-20261017.json` and Git history.
