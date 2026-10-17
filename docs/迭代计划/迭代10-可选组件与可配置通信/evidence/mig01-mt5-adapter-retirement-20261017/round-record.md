# MIG01 MT5 legacy adapter retirement

Date: 2026-10-17

The MT5 child package retired its unsupported `Mt5GatewayAdapter` and removed the `bt_api_mt5.gateway` package. The Base lazy export no longer imports it and classifies the name as retired, raising `AttributeError` before the generic no-op fallback. Adapter-only gateway tests were removed; a module-absence test replaces them. The existing plugin contract test remains and verifies that MT5 registers no exchanges/assets and is reported unavailable.

Child commits:

- `bt_api_mt5` `dev`: `32e1772136bdc3deca71c23df9d272559468d964` (parent `b704dc94ff20c4ae9b7d715ae0e70afa4e2ba66d`), nine paths.
- `bt_api_base` `dev`: `1813f1464aa04fbc3ed425a72e7db768cd1e7bab` (parent `e3c934d30951cc4dec7a8092371234cba15bc5ea`), two paths.

Author-focused checks on Python 3.11.5 / pytest 8.0.0 passed: MT5 retirement plus plugin registration, 3/3; Base retired exports, 3/3. Each run emitted the existing unknown `asyncio_default_fixture_loop_scope` pytest configuration warning. A wheel built from a temporary copy contains `bt_api_mt5/plugin.py` and no `bt_api_mt5/gateway/*` members. Independent static review returned GO_STATIC and verified both child commits and the exact source boundary.

This is package/source retirement evidence only. MT5 remains unavailable: this round adds no direct feed, account-session provider, managed market provider, ServiceHost order entry, terminal connectivity, or real-account acceptance. MIG01 remains incomplete.
