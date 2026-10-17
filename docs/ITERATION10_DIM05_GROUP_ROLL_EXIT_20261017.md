# Iteration 10 DIM05 group roll-exit recovery

Repository/branch: `D:/bt_api_py`, existing `dev`; delivery parent `55c05166709aa538f43fb78805085468724d023d`.

The coordinator now uses a registered Risk owner to prepare, submit and query a group roll-exit. A lost submit response leaves the action UNKNOWN; querying the same action recovers its durable result without submitting another close. Server-owned quote/source bindings, allocation scope, replay checks and peer-strategy isolation remain enforced. The already accepted wire-account-alias correction is retained.

Scope: 12 production paths and four test paths, selected by `.codex-tmp/dim05-roll-exit-r10-delivery/selection-manifest.json` (SHA-256 `3e26b9aadc7970699ea700414d32d06d72a238013e86c3b7b269c2459793ce5b`). The exact HEAD-to-postimage patch is `daddce4c31712bc0ce16650a4686bf8332969ca7bf784ee20524dca96fa1c2fa`. Mixed working-tree changes outside this selection remain unstaged.

Root R10 acceptance: `D:/temp/dim05-roll-exit-sdk-r10-origin-root-20261017`, one integration test passed in 9.21 seconds, pytest exit 0. The real local coordinator, ServiceClientRuntime, registered Risk Host and SQLite journals used a synthetic provider. A committed submit response was deliberately lost; the same action recovered to RESOLVED/FILLED, quantity 1.5, with PREPARE/SUBMIT/QUERY RPC order, exactly one provider close and the peer allocation still 0.75. All 760 public source pins and seven repository HEADs were unchanged. A runtime-origin assertion verified the selected Risk implementation. One known Windows PyZMQ Proactor warning was recorded; stderr was empty.

This accepts local synthetic recovery integration, not real-venue execution. R10 ran only the named integration selector; it did not run the three delivered unit files. The earlier R7 quote/tamper unit passed against its earlier test bytes, not the different R10 unit-test snapshot. R9 remains a failed run that imported an older Risk module because its cwd took precedence over PYTHONPATH; its result is preserved. Formal DIM05 adjudication is recorded separately after this source delivery.

The resulting commit hash and exact committed-blob verification are recorded in `.codex-tmp/dim05-roll-exit-r10-postcommit-20261017.json`.
