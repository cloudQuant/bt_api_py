# Iteration 50: CTP event-waker component

This accepted local component uses the existing `dev` checkouts. It adds a
bounded, token-based event-waker registry to `BtApi` and connects the production
CTP plugin's market and trade streams to it. Producers notify after a successful
queue insertion or an actual connection-state transition. Successful client
close notifies once and removes registrations; a failed close does not claim
terminal state. Callback errors are isolated and diagnosed once. External
unregistration waits for an in-flight callback; callback-context unregistration
does not wait on its own dispatch loop.

The CTP child was accepted and committed first:
`0ecf3104ad74777c3e66dae3c4c3ba4f7c74efe5` in
`D:/bt_api_py/bt_api/bt_api_ctp`, branch `dev`. Its scope is
`src/bt_api_ctp/feeds/base_stream.py`, `src/bt_api_ctp/plugin.py`, and
`tests/test_base_stream_event_waker.py`.

The parent scope is `bt_api_py/bt_api.py`,
`tests/unit/test_bt_api_event_waker.py`, the CTP gitlink, and this record.
Unrelated gateway changes and the Iteration 10 round record are excluded.

Root reviewed the final source and ran both focused suites using
`C:/anaconda3/python.exe`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, and
`PYTHONUTF8=1`:

- Child: `python -m pytest -q -p no:asyncio tests/test_base_stream_event_waker.py`
  — 5 passed in 0.60 seconds, exit 0.
- Parent: `python -m pytest -q -p no:asyncio tests/unit/test_bt_api_event_waker.py`
  — 5 passed in 0.93 seconds, exit 0.

Each run reported the existing unknown
`asyncio_default_fixture_loop_scope` pytest configuration warning. Tests use the
actual `BtApi`, base producer, and plugin assembly with fake native stream leaves;
they cover enqueue ordering, visible ERROR/DISCONNECTED transitions, callback
failure isolation, bounded registrations, close failure, and concurrent
unregistration. They are local component evidence, not provider/account, native
CTP lifecycle, Backtrader engine-wait, or full AC50-12 acceptance. No credentials,
provider session, or account operation was used.
