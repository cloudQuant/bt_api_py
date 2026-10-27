# Iteration 51 product-scoped query support

SDK `bt_api/bt_api_ctp` dev commit `8b3ce1f1f27aa29725a1891db07aa79db0126be8` adds typed ProductClass product queries, exact native filter readback, optional depth ProductClass, stop/deadline handling, and bounded catalog callbacks with per-request concurrency control.

Root accepted 16 distinct source cases: 15 passed in R2, then the corrected error-origin fixture passed alone in R3. Actual local CTP request structs and SPI/ingress were used with fake provider/lifecycle leaves; no provider/account acceptance is claimed. Each run retained one inherited pytest configuration warning. Full-market collector/028 integration and installed acceptance remain separate.

The SDK six-path scope and exact QA/source pins are in `bt_api/bt_api_ctp/docs/evidence/iteration51-product-query-scope-20261027/record.json`. Existing query-history retention remains; deleting caller results does not release every SDK-retained row.

This parent round changes only the CTP gitlink and this record. Repository/branch: `D:/bt_api_py`, `dev`; parent `db31d67f4f3c1632c3610b009101bd12aa5b8da6`. No push.
