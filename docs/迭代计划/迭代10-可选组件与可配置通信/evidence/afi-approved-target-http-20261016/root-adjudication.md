# Root record: AFI approved-target HTTP → SDK → Execution host round

AFI repository `D:/source_code/ai-for-investor`, branch `dev`, head `a421354b38740f26531645accd8e13338fadbf4d` unchanged before and after the accepted run. Fixed SDK export `D:/temp/afi_iter10_approved_target_sdk_sources_20261016_r6_fixed` (SDK `3ac75d1d` and its sub-packages at that revision). Runner `D:/bt_api_py/.codex-tmp/iteration10-focused-qa.py` (`8b7f470be77270adeb9d72a83776b827c4653004b39835eeca0f43bf63cf24ed`, 3,204 bytes).

Inputs of this round are the 15 uncommitted AFI candidate paths listed as `afi_approved_target` in the handoff state (API, permissions, ORM, migration, service-read wiring, trusted authority, HTTP operations service and the integration test). The accepted run pinned 2,147 inputs; every pin outside the edited test file already matched the handoff SHA-256 values, and all 2,147 pins were unchanged before and after the run.

## R6 (preserved failure, not re-run)

`D:/bt_api_py/.codex-tmp/iteration10-approved-target-http-qa-root-r6.json` (`c0a197ef44411b4426181b5ca4ed3cd1de844003fa94ca80f673b18f89f8e4bf`) ran the node once: 1 failed, 2 warnings in 23.58s, `assert len(synthetic_risk_permits) == 1` at `tests/test_service_read_authorized_http_integration.py:6370` (`assert 2 == 1`, ids `cf55e93e30e8458fa308866e8be7465b`, `ef3a72cedc35417f8c64c3e722ae8a10`), exit 1, output preserved in `D:/temp/afi_iter10_approved_target_http_root_20261016_r6`. R4/R5 outputs and the earlier registry blocker are preserved as recorded in rounds 146–147.

Diagnosis: not a product defect. The committed accepted design reserves the Risk permit inside `_admit` before the durable dispatch claim and the approval consume (`facade.py` `submit_approved_target` docstring, `service_order_entry.py` reservation ordering, round 137/141 lineage). Both admitted intents therefore appear in the synthetic gate's `reserved` list; the stale-policy intent is then denied by `APPROVAL_POLICY_STALE` (DEFINITIVE_DENIAL) and `_block_before_dispatch` releases exactly that permit. The Execution journal confirms the release succeeded: the stale intent record ended `BLOCKED` (not `REVIEW_REQUIRED`) with `dispatch_blocked(APPROVAL_POLICY_STALE)`, while the dispatched intent kept its permit until `settle`. SQLite outbox evidence: `cf55e93e…` intent_recorded → intent_admitted → dispatch_claimed → provider_observation(ACKED); `ef3a72ce…` intent_recorded → intent_admitted → dispatch_claimed → dispatch_blocked. The R6 test expectation (`== 1`) contradicted the accepted semantics.

## Fix (test-only)

Only `src/backend/tests/test_service_read_authorized_http_integration.py` changed (`f7ca663a138828ff6c3830cbfeefd041f1f8b4b4056fa2c2c1ffa7091a905db3` → `a7c44613ba3ab646172d70734b9052bab7d9158b99343ad3174a36b7c9266cfe`). No production source changed. The synthetic Risk gate now records `release(permit_id, reason)` calls; the facts/close payloads carry the released entries; assertions now require 2 reserved distinct intents, exactly one release `(permit-<denied-intent>, APPROVAL_POLICY_STALE)`, the dispatched intent's permit not released, and the same accounting again at graceful close. The stale comment was rewritten to describe the accepted reserve→block→release semantics. No previously accepted node was re-run.

## R7 (accepted)

Spec `D:/bt_api_py/.codex-tmp/iteration10-approved-target-http-qa-root-r7.json` (`5a1d07fa48475699a6c911f78f44caaa1309db2ffc1586187adce9bdeb059abb`, built by `build-approved-target-http-r7-spec.py`, `a12ccbfb043dc1ab1dbbdec5acfbf0d7818fe17d88433ab226d59417958ea10f`). Command: `C:/Anaconda3/python.exe D:\bt_api_py\.codex-tmp\iteration10-focused-qa.py D:\bt_api_py\.codex-tmp\iteration10-approved-target-http-qa-root-r7.json`. Output `D:/temp/afi_iter10_approved_target_http_root_20261016_r7`.

Result: exit 0, `1 passed, 2 warnings in 23.84s` (both warnings pre-existing: Starlette 422 deprecation and a quandl import warning), stdout sha256 `977f4eb64403697175d2835e54dd115f17d77163aa8314fc6b343cbbb44655df`, stderr empty, 2,147 source pins unchanged, AFI head/branch unchanged. Raw command/spec/source-before/after/stdout/stderr/junit/exit archived in the output directory.

Covered by the accepted node (real AFI lifespan, real HTTP via ASGI client, real SDK service-read deployment with owner thread, real spawned Execution ServiceHost child process with CURVE registration, SQLite journal, synthetic provider/Risk leaves):

- 201 policy publish (v1) and 201 approved-target creation under `_APPROVED_TARGET_POLICY_REF` / `_APPROVED_TARGET_SELECTOR`.
- First submit 200 `ACKED`; single provider dispatch (`submitted == 1`, `unauthorized == ()`); idempotent replay 200 with the identical `intent_id` (dispatch still once).
- Same key different content (`target_sha256` changed): 409 `IDEMPOTENCY_CONFLICT`.
- Digest/version bridge: approval created under policy v1, v2 published, submit → 409 `APPROVAL_POLICY_STALE` reaching Execution (DEFINITIVE_DENIAL path, permit released, zero provider calls for that intent).
- Forged authority fields (`owner_subject`, `actor_ref`, `intent_id` supplied by client): 422 `VALIDATION_ERROR`.
- Other subject token: 403 `SERVICE_READ_ACCESS_DENIED`.
- Revoked approval: revoke 200, then submit → 409 `APPROVAL_REVOKED` (HTTP reservation preflight).
- Expired approval: submit → 409 `APPROVAL_EXPIRED` (HTTP reservation preflight).
- Identity/ownership facts: exactly 3 approved-target service calls on the owner thread, all as `APPLICATION_CLIENT` with the exact principal/actor/delegate/account/strategy tuple; authority resources ready during the run, closed exactly once, host graceful close with zero cleanup failures.

## Limits (strict)

Per the handoff boundary, this node uses synthetic Risk and provider leaves; revocation and expiry are HTTP reservation preflight checks (the stale case is the Execution bridge denial). This is **not** full AFI07: no approved-target cross-process concurrency/restart dispatch-once, no strategy-order or group-revocation/leg evidence, no real account or venue. AFI07 stays partial; the formal count remains 25/68. The uncommitted AFI scripts/README group and the three new `scripts/windows` files are outside this candidate and remain untouched; the AFI `.codex-tmp` scratch directories are not part of the commit.
