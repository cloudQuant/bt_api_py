# ARB05 Risk account-owner lifecycle lock

Parent: SDK `dev` at `2c2bb6abe4c83b21d77f8884ab7d2d589f47721d`.

This scoped round adds the existing execution-owner sidecar lock to the Risk `account_owner` `service.order_entry` Host lifecycle. It accepts `os.PathLike` journal paths, acquires the lock before recovery/registration/provider work, and transfers resource plus lock cleanup together so a failed resource close retains the exclusion. The helper implementation was already committed in the parent; only three selected `service_components.py` hunks are included here. The mixed worktree file’s unrelated hunks are excluded.

The one new local spawned-Host regression passed (`1 passed, 1 warning, 9.94s`) with synthetic provider leaves. Two Hosts using the same Risk instance/account/journal and different RPC endpoints reject the competitor with `ORDER_OWNER_ALREADY_ACTIVE` before recovery, handler registration, or provider calls; after normal close the second Host starts. This is local synthetic lifecycle evidence, not real-account or formal ARB05 acceptance. The existing lock is advisory and keyed by resolved journal pathname; hard-link aliases and sidecar replacement/unlink are outside its exclusion guarantee.

Selected files: `bt_api_py/service_components.py` (three hunks only), `tests/unit/test_service_risk_owner_lifecycle_lock.py` (7522 bytes, SHA-256 `B8752A2445CD37C915C0D80390A62F6DEA24F65EDF0701EA0F584E28FA45DC43`), and this record.