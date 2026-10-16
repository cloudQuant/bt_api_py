# Iteration 10 AFI05 manual-account policy scope — SDK R3

- Repository: `D:/bt_api_py`, branch `dev`.
- Base HEAD: `5850ce04d1fd0dafdf49f99b1a25dbd5894ba0a8`.
- Risk dependency: committed `5434184d35a10418b2fc53e5ec7f805ee5edc884`.
- Scope: `bt_api_py/service_components.py` selected manual-scope validation hunk and `tests/unit/test_service_risk_policy_admission_resolution.py` selected regression additions; update the Risk gitlink and this record.
- Behavior: a manual-book account policy must exactly match the singleton manual target scope before binding. A multi-manual-target admission map is rejected without changing the existing gate binding. Legacy account-wide policy behavior for strategy targets remains covered and unchanged.
- Acceptance: the fixed-source candidate ran the two legacy tests and two new scope tests under the AFI Python 3.12 environment; `4 passed`, exit 0, `--noconftest`. Source/test candidate hashes are recorded in `.codex-tmp/afi05-manual-account-policy-sdk-r3/manifest.json`.
- Evidence boundary: local synthetic SDK/Risk unit coverage only. No Host/HTTP, real account, or formal AFI05/06 acceptance is claimed. The separate first-startup worktree/test changes are excluded.
