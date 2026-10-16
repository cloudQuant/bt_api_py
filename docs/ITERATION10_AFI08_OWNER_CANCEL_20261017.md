# Iteration 10 AFI08: owner-scoped cancellation

SDK `dev` delivery: `5da28414ab8cc9af59bd478633a740d8c302b604`, parent
`1dfc99601b027e1cce3e112ffffa6d96a161f212`. The commit contains seven
selected source/test paths plus the Execution and Risk gitlinks. The reviewed
selected patch is 111,778 bytes, SHA-256
`bba547d3d1baace35006a6030aa25cb42ec128db757bf2af612fecba3d86faf4`, based on
the stated parent. The paths are:

- `bt_api_py/service_components.py`
- `bt_api_py/service_registry.py`
- `bt_api_py/service_risk_order_entry.py`
- `bt_api_py/service_risk_order_journal.py`
- `bt_api_py/service_execution_risk_cancel.py`
- `tests/integration/test_service_execution_risk_cancel_owner_control.py`
- `tests/integration/test_service_execution_risk_cancel_terminal_history.py`

The index was empty after the commit; unrelated working-tree changes were
preserved.

The selected service path grants `owner.cancel` only through the authenticated
`SERVICE` / `service.owner_control` route with an exact owner-bound account,
strategy, and run scope. It persists the cancellation identity and run
attribution, bounds the target set, claims before provider dispatch, and
requires the injected trusted CANCEL quota admission. Replays and UNKNOWN
results query the existing cancellation identity rather than dispatching again.
An ACK is not treated as terminal cancellation: the Risk latch remains held
until the persisted, source-bound terminal order/fill history is validated and
resolved under the existing Risk journal writer fence. Resolution removes only
the cancel latch; original order exposure remains represented.

Child dependencies pinned by this commit:

- Execution `aff8d67c3ceea11cf730d7f07fc06de97bb6af0a`; required owner-cancel
  commit `e7ba5cb74910f5c0a15ed79ecde7a1f99c77a4c4` is an ancestor. The later
  Paper snapshot source round is transitively present in this fixed pointer and
  is not counted as AFI08 acceptance. The newer Execution tip was deliberately
  not selected.
- Risk `355b26331cae5a302f1d27a6fbf65c465cd1a6e8`, parent
  `ae97a4b8b4691ff560ed0d0912bf4475510e1902`; it contains the selected
  cancellation-receipt scope guard and regression.
- Monitor remains at `02ee0c42a1e63a9cecbb666376e70c6f72b0d6e8`; Base remains at
  `6f314acda4cec224843d63ca5bc07d8cea5bdf46`.

Evidence is local synthetic only. The earlier AFI08 author run passed
`test_owner_cancel_ack_keeps_risk_latched_until_persisted_terminal_history`
once (1 passed, 1 known ZMQ Proactor warning; raw result SHA-256
`d6e6fb891526e67d07040825dd4e6998f2296771e559447834a9900926add1a2`, stdout
SHA-256 `435a549869f53d7ae98908ff7cc30454d4f8a5e1e6b941b5129db2120404322f`).
The later isolated Risk candidate regression
`test_cancel_dispatch_resolution_receipt_rejects_scope_key_mismatch` passed
1/1; output SHA-256
`3f9e4181641a1530cd89ed23a080b4335ab8f2594c702d38226dec0694b865a2`.
Independent static review returned GO_SOURCE_PATCH for the selected SDK and
Risk overlays. The committed SDK blobs were compared with all seven reviewed
candidate SHA-256 pins after normalizing the checkout's CRLF conversion; each
matched exactly. Scratch patch application and Python compilation passed.
This round did not run a new SDK Host test and does not establish provider,
account, or formal AFI08 acceptance.

The SDK commit's sole whitespace exception is one blank line at EOF in the
reviewed `test_service_execution_risk_cancel_owner_control.py` candidate. The
preservation-aware whitespace check passed with only that check disabled. No
source bytes were altered to remove it.
