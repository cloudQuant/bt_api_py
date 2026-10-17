# MIG01 OKX legacy-adapter retirement

Date: 2026-10-17  
Parent repository: `D:\bt_api_py`, branch `dev`; parent HEAD before this round: `8c8131ccdcb0df817fdaaaaf583e756de0ec52c2`.

## Scoped package commits

- `bt_api/bt_api_okx`, branch `dev`: commit `bbb58d44d9c028a8f14aa1a968d0cab0a3f46b91`, parent `259208d6a1cf3301456603e8a5290052c9ec80b2`. Five paths remove `OkxGatewayAdapter` and its package export, replace the positive legacy-adapter test with a retired-module check, and describe the remaining direct and managed-market boundaries.
- `bt_api/bt_api_base`, branch `dev`: commit `e3c934d30951cc4dec7a8092371234cba15bc5ea`, parent `09d3ea8b07f8d3f724f2124271a2ac53a0be863e`. Two paths remove the lazy OKX adapter mapping, add `OkxGatewayAdapter` to the retired-name set before the Noop fallback, and extend the retired-export test.

The focused author run passed 22 tests with one existing pytest configuration warning. It was not repeated for delivery. A broader package smoke reported 18 passed and one unrelated existing failure: `test_okx_req_order` expects `fee_currency == ""`, while the current typed DTO returns `None`; this round did not change that contract. Independent static review returned GO_STATIC and confirmed no remaining production import or use of `OkxGatewayAdapter` or `bt_api_okx.gateway.adapter`.

The retired positive adapter test was the only focused test found for the wrapper's cancellation forwarding aliases (`instrument` and `order_ref`). Those wrapper-only aliases retire with the adapter; the direct `TradeMixin.cancel_order` path is unchanged, but has no focused direct-forwarding test in the current package suite. Existing tests continue to cover direct feed/container projections and the managed SWAP ticker/history providers.

The package docs state the supported boundary: direct OKX feeds and typed containers remain, and configured SWAP ticker/bar-history providers remain; no account-session or ServiceHost order-entry adapter is claimed. Child indexes were empty after their scoped commits. The SDK parent update records only the OKX and Base gitlinks plus this record; unrelated parent work remains unstaged.

This round retires the OKX legacy wrapper; it does not remove `bt_api_base.gateway`, prove a clean isolated install or wheel for these new package bytes, or complete formal MIG01 acceptance. IB Web and MT5 adapters remain and still reference Base Gateway; their retirement and final distribution/isolated-install/normal-call acceptance remain outstanding.
