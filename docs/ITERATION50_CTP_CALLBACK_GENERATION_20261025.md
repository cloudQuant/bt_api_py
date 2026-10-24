# Iteration 50 CTP callback source generation

The CTP package dev commit `48b476ea90665791c7d2f86935720ce238931520`
(parent `0ecf3104ad74777c3e66dae3c4c3ba4f7c74efe5`) adds optional
`on_tick_with_generation(payload, generation)` and
`on_disconnect_with_generation(reason, generation)` callbacks. The production
SPI captures the callback and generation under its current-SPI identity lock,
then invokes it after releasing the lock. If configured, it replaces the
legacy callback for that event; otherwise the existing one-argument callback
and exception propagation remain unchanged.

Only `src/bt_api_ctp/ctp/client.py` and
`tests/test_ctp_md_disconnect_observability.py` changed in the package. Their
accepted checkout SHA-256 values are
`8a596b707e2b7498cb6a9707a28f037969a33d70d484be74c0b2c7311222adbb`
and `fa3e0564d0505fb814731cb50b8a8828e56cb490dcdb89af0951c0b33e599839`.

The focused command was `python -m pytest -q tests/test_ctp_md_disconnect_observability.py tests/test_ctp_md_diagnostics.py tests/test_md_client_identity.py tests/test_ctp_native_callback_events.py`.
It passed 62 tests with the existing unknown pytest configuration warning.
Ruff, test-file Black, compilation and diff whitespace checks passed. Full
client-file Black also reports unrelated formatting on the committed baseline,
which this round preserves. Independent source review returned GO without
repeating QA. Tests invoke the actual SPI with synthetic native leaves and
deterministically advance the generation after callback admission releases the
lock. They do not prove native/provider or account operation.

This parent dev round updates only the CTP gitlink and this record. Backtrader
Store consumption, disconnect health propagation and epoch rebuilding remain
the next separately accepted round. Unrelated parent changes are preserved.
