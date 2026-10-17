# DIM07 Gateway cancel quota R5

This round accepts one local synthetic `ServiceHost` integration node for the
reserved Gateway quota lane used by authenticated `owner.cancel`. It does not
accept DIM07 as a whole.

## Scope and dependencies

The only new test payload is
`tests/integration/test_dim07_gateway_quota_cancel_owner.py` (21,161 bytes,
SHA-256 `2d49ef97c1b1fc8af8180cc3c61afecc4ac5cb900d11d2f183ee6b9d795e31d4`).
The test uses the accepted account-owner cancel closure from SDK commit
`74a5094fcfcb2995f5a79f75b423e1b5103864b7` and the builder capability-filter
fix from SDK commit `f590be3bfe5f5fb4a010315e931f81ef300393d4`. Those production
changes are dependencies, not changes in this round. The test and this record
have exact `-text` rules in `.gitattributes`.

The R5 overlay was built from pinned package archives, so it is a selected
source snapshot rather than a copy of every package at SDK parent HEAD
`7173211d63099a3adcc09fdc6fc9090a2b6641ad`. A normalized-newline comparison
against that parent and its package gitlinks found two additional Python
source differences beyond the accepted `service_components.py` change. The
accepted `service_components.py` override from `f590be3b` has the same Git blob
in parent `7173211`; it is not an additional dependency difference:

- Execution `service_order_entry.py`: the R5 archive is from child commit
  `4374237705478039d60760e5f68df0e6ed076907`; current parent gitlink
  `d5563ecb4f0bd057322bec3a72febdc90e1a2d93` adds optional Paper Risk adapter
  checks. R5 file SHA-256 is
  `e476c6631544445a92efa8e1320342c8aedd9ec18165f8da21de45e418d611e3`;
  current file SHA-256 is
  `1592610271f2ebde71b5dc307e74d3ec223dfcc81d88fad8e8db57f4a4d8ad6f`.
- Risk `service_component.py`: the R5 archive is from child commit
  `beb1939dd379b519ef18d0ecb83d1f1ff217ac47`; current parent gitlink
  `b6e4a7bce74a5ae5dee4c8f514a9d95fd68073d9` adds Policy Catalog approver
  and publish authorization checks. R5 file SHA-256 is
  `bfca1e082cb70eba2b144f3fd7e0e5b8f50bbcac8c9e374db52daafd6ab4d3a2`;
  current file SHA-256 is
  `eae1c7ea9b3ca2832922f3cb223b8b41c492c6f4bd33f35b57105bf73151bc2d`.

All remaining package Python files match their current parent gitlink after
newline normalization. The SDK-owned Python files in the overlay likewise
match parent `7173211` after newline normalization, except for the selected
new test (not present in that commit). Therefore this round accepts the test
against its exact pinned R5 source snapshot; it does not claim that the two
newer package hunks above were exercised or that the complete Host dependency
tree matches current parent HEAD. No production source from those differences
is included in this delivery.

## Focused result

The exact frozen R5 specification is
`.codex-tmp/iteration10-dim07-cancel-owner-focused-root-spec-r5-20261017.json`
(83,153 bytes, SHA-256
`d8d827b50508c1fd70ab4064ac2fd872eebf23b05e8d55ec86195c87ba5b0e80`). Root
authorized one invocation; the frozen runner executed the selector once using
`C:\Anaconda3\python.exe .codex-tmp\iteration10-focused-qa.py
.codex-tmp\iteration10-dim07-cancel-owner-focused-root-spec-r5-20261017.json`.
The result is preserved at
`D:\temp\iteration10-dim07-gateway-quota-cancel-owner-focused-root-20261017-r5`.

Pytest exited 0: 1 passed, 0 failed, 0 errors, 0 skipped, with one existing
ZMQ Proactor warning. Runtime was 3.80 seconds. The runner verified 380 pinned
source inputs unchanged and no repository HEAD bindings. `result.json` is 459
bytes, SHA-256 `e4e3ebfe8e71b8bf490ba06e86768ad1f36648603234699ea33933972a3fc003`;
`junit.xml` is 387 bytes, SHA-256
`fa1b03ccc9734f48e0733d0c1e54841354a8c7ab96b0635b7242dcf57ec3834d`;
`stdout.log` is 864 bytes, SHA-256
`1107c02c4b7f5d2b286b21760bedeb430817c7c7143664cff429cf9e350696b7`;
`stderr.log` is empty (SHA-256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`). The
before/after source snapshots are each 67,397 bytes and have identical SHA-256
`c1982695abd234f88de59edce3f7da6014588730fbea108f5e204bdf5cd98362`.

The passing assertions establish that ordinary OPEN quota capacity is consumed
and a subsequent OPEN admission is denied at its deadline, while CANCEL uses
the reserved slot. The local provider receives one cancel; replaying either
the original or a new command ID returns the same cancel ID with one dispatch
attempt. The SQLite grant rows bind both operations to the expected account,
strategy, instrument subject digest, and run digest. The target order itself is
created by the local Host submit path; this test does not prove that every OPEN
submit is quota-metered.

The `finally` path independently closes each constructed runtime, Host, Risk
gate, and execution store. Its assertions require the Host thread to finish,
not remain alive, report no thread error, and leave `host.state == "closed"`.
The test passed those cleanup assertions. On success the test does not emit the
individual cleanup fact values, so this record does not claim a separately
serialized Host cleanup receipt.

## Acceptance boundary

Evidence level: `LOCAL_SYNTHETIC_GATEWAY_RESERVED_CANCEL_QUOTA_ONLY`. This is
not distributed quota evidence, provider/account acceptance, 100-strategy
LOAD01 readiness, or formal DIM07 acceptance. The distinct 100-process
strategy-wave node remains unaccepted because its required memory gate was not
met. Earlier R4's `BUILTIN_COMPONENT_INVALID` failure remains preserved and is
not replaced by this result.
