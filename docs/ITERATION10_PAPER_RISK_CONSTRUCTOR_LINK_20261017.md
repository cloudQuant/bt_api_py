# Iteration 10 Paper Risk constructor integration

Repository/branch: `D:/bt_api_py`, existing `dev`.

The Execution constructor rejected every Risk admission gate when a Paper owner was selected. This prevented the supported Paper/Risk composition from starting through the normal SDK service entry. Execution commit `d5563ecb4f0bd057322bec3a72febdc90e1a2d93` (parent `4374237705478039d60760e5f68df0e6ed076907`) accepts only the canonical `PaperRiskAdmissionAdapter` bound to the exact owner, matching port, store and each execution scope. Basic admission and unprotected-mode guards remain in force. E-only construction does not import optional Risk.

Independent static review accepted this narrow change; the author's seven focused, non-Host regression cases passed. The child commit contains only the constructor hunk and `tests/test_paper_execution_v2.py`. Unrelated group/recovery changes remain unstaged. The accepted constructor must be consumed from the committed Git blob, not the larger dirty checkout: committed source is 60,247 bytes, Git blob `3ef479091f6671053cb14b20b45dfec9d851328a`.

This parent round changes only the Execution gitlink and this record. It does not repeat those tests and does not establish AFI08/AFI13 Host acceptance. The prior AFI13 R5 startup failure remains preserved; fresh manual, REDUCE and strategy acceptance runs will use the committed constructor.

The exact parent integration commit is recorded in `.codex-tmp/paper-risk-constructor-link-root-postcommit-20261017.json` and Git history.
