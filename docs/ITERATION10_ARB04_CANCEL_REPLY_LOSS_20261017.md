# Iteration 10: durable group cancellation after reply loss

SDK dev delivery parent: `8aa1c3406f3085e06bb625a1cdd1dc976bee4d91`. The selected group-cancel code retains one durable cancellation identity and original-order binding. UNKNOWN/replay queries the original attempt; ACKED/PARTIAL alone cannot release its hold. Complete source-bound terminal order/fill evidence is required for resolution. The normal runtime still defaults to `groups.submit` and `groups.query`; callers must explicitly request and be granted `groups.cancel`.

Acceptance: the author console (tool/session 69070) reported one actual local synthetic Host/CURVE cancellation reply-loss node passed in 12.15s. A separate default-operation compatibility node passed once with one existing pytest configuration warning. There is no separate saved raw/JUnit archive for these console results. Earlier failed r2/r3 attempts remain historical. Independent static review accepted the exact selected closure; Root did not repeat accepted QA for delivery. This is not formal ARB04 completion: actual socket-disconnection recovery remains a separate case gap. No real provider/account operation was performed.

The ten source paths are selected from mixed work in progress and composed on unchanged committed base blobs; DIM09, roll-exit, ordinary AFI08 controls, Paper CLOSE and migration work are excluded. The patch is 149735 bytes, SHA-256 `176aff4928ce5e3825792a2f4781435a3f2c99a473961e68de073b66f9f60508`.

Selected source scope:

- `bt_api_py/service_execution_group.py`
- `bt_api_py/service_execution_group_proofs.py`
- `bt_api_py/service_execution_group_cancel.py`
- `bt_api_py/service_execution_group_client.py`
- `bt_api_py/service_registry.py`
- `bt_api_py/service_components.py`
- `bt_api_py/service_runtime.py`
- `tests/integration/_arb04_group_reply_loss_support.py`
- `tests/integration/test_arb04_group_cancel_reply_loss_service.py`
- `tests/unit/test_service_runtime_group_operations.py`

The parent also updates the Execution gitlink from `5b83e7e584ff70f851e4b655ace781ab6b68fbf7` to the tested checkout `55ce1942d5760b2f79a0929c57d9ba0b34f18ec2`. Its committed ancestors include `d9180ee` (Paper Risk admission adapter, prior focused synthetic node and static review) and `55ce194` (exact-run cancellation owner). This fixes the source dependency recorded by independent review; current uncommitted Execution changes are excluded. The newer Paper CLOSE/recovery and ordinary cancel terminal-history rounds remain unaccepted work in progress.
