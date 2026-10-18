# Iteration10 evidenced observation hook — 2026-10-18

Repository/branch: bt_api_py / dev. Parent: `9a897bbba66c127c4868372d304e14713358daac`.
Execution package / dev: `1abd6166a7394d714acb74382eed97080b92c187`; parent `e34d5aa425f9169a28fc0692daf29ed6df2df626`.

Execution now offers the admission gate an `after_evidenced_observation(record, event, writer_lease=...)` hook for each newly persisted provider observation, including terminal cancellation after the initial acknowledgement. Exact duplicate observations do not notify again or resend an order. The existing first-settlement hook remains unchanged. A reconciliation failure preserves the event and records a manual-review latch instead of losing the evidence.

Root fixed-source QA: 2 passed / 0.67 seconds, exit zero, no warnings or stderr, no listed external-operation denials. All 427 source pins and five repository HEADs remained unchanged; the facade and Store origins matched. The tests use the real SQLite Store and facade with synthetic admission/provider leaves, covering late terminal notification, duplicate replay, and durable failure state after reopening SQLite. Independent static review: GO_STATIC. New-test Ruff passed.

The tested facade checkout uses CRLF. Delivery restores the repository's LF-only style; the complete AST including source locations and compiled code are equal, and the tested checkout bytes remain unchanged. The archive retains both exact images and the normalization receipt. Accepted QA was not repeated for this newline-only delivery adjustment.

Evidence level: LOCAL_COMPONENT_ONLY. This round does not establish SimNow order/cancel/fill or complete risk reconciliation. Formal acceptance remains 62/68. Runtime assembly, the same-order risk bridge, venue acceptance, load acceptance and the deferred two-machine cases remain outstanding.

[Raw QA and exact source archive](evidence/iteration10-evidenced-observation-hook-20261018/root-qa.zip).

Archive SHA-256: `fc18cc2eb042dd44c37971d420d6d97ff3d3dfff9aa08237f8330c2e7e71d9af`.

The commit containing this record identifies parent delivery. Root postcommit metadata is retained under `.codex-tmp/ctp-observation-hook-root-20261018/sdk-postcommit.json`.
