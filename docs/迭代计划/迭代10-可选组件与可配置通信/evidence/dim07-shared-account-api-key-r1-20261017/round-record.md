# DIM07 two-account shared quota-domain node

Status: one local synthetic integration node passed once and received independent `GO_STATIC`. This is source-test support evidence, not formal DIM07 acceptance.

Repository: `D:\bt_api_py`, branch `dev`. Commit parent for this delivery: `20980e23421b809786b24b50146430c4557fac76`.

## Scope

This round adds only `tests/integration/test_gateway_quota_session_owner.py`, with the new test `test_two_account_scopes_share_one_api_key_ip_ctp_quota`. The frozen candidate is 23,021 bytes, SHA-256 `036E4BB4DFF1394299965B6C3CFA2D9848514DD31C03C0E5AEB6FDABCF6EE2C7`. Its committed parent blob was `888d1faa57f52dfa7369b866a96cceebc5ca572d`.

Two test-owned spawned processes use different account refs, the same explicit synthetic quota-domain ref and SQLite database, and capacity one. Each builds a `GatewayQuotaDomainBinding` and calls `GatewayQuotaProviderSession.read_instrument_metadata`, which is admitted as `CTP_QUERY`. Assertions require one successful synthetic provider read, one `DEADLINE_EXCEEDED` without provider dispatch, one durable CTP-query grant, zero pending waiters, and both children exiting with code zero.

## Verification and limits

The node was run once with:

```text
C:\Anaconda3\python.exe -m pytest -q -p no:asyncio tests/integration/test_gateway_quota_session_owner.py::test_two_account_scopes_share_one_api_key_ip_ctp_quota
```

Observed console result: `1 passed in 1.57s`, exit 0, with the existing warning `Unknown config option: asyncio_default_fixture_loop_scope`. No separate stdout or JUnit file was retained; this record preserves the result without presenting it as a raw archived log. The test source hash matches the frozen candidate above. The accepted run was not repeated.

Independent reviewer `mig03_partial_delivery_review` returned `GO_STATIC` for the two-account node, confirming the shared database/domain/config, distinct account-scoped subjects, CTP operation mapping, one-grant/no-dispatch result, and waiter cleanup. The reviewer noted one fixture limitation: its failure-path finalizer terminates and joins a child for five seconds but does not surface a child that remains alive; that path was not taken by the recorded passing run and should be hardened before a future rerun.

Evidence is local synthetic SQLite contention. It does not prove a production API-key/IP resolver, real provider/account behavior, cross-machine allocation, or a formal DIM07 case. The separate 100-process fairness node in `tests/integration/test_gateway_quota_hot_wave.py` was skipped before child creation because available memory `5,971,202,048` bytes was below the unchanged LOAD01 gate `10,603,200,512` bytes (`112 × 72 MiB + 2 GiB`). It remains unaccepted and outside this commit. Its current candidate has independent static HOLD findings on a reserve-check retry path, sequential result waits, and child-cleanup reporting; do not treat that node or skip as a pass.
