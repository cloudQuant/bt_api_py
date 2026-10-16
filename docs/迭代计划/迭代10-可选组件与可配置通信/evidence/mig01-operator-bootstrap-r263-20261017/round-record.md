# R263: MIG01 operator-owned ServiceHost deployment composition

Date: 2026-10-17  
Repository: `D:\bt_api_py` / `dev`  
Current parent for this exact scope: `7b6f82c3916dd632fddeb53a000da0c7c2bdc902`  
Gateway dependency: `bt_api_gateway` / `dev` at `2671fe714d1aad70c40b33255b50f354222e59b7`  
Evidence: `docs/迭代计划/迭代10-可选组件与可配置通信/evidence/mig01-operator-bootstrap-r263-20261017/`

## Scope

This round adds `bt_api_py.gateway_operator.GatewayOperatorDeployment`, one focused integration module, and a gitlink to the already committed `GatewaySessionOwner`. It composes trusted `ServiceConfig`, `TrustedServiceAuthority`, the existing service-v2 `ServiceHost`, and local resources; it adds no second router or writer.

The `market_hub` test uses a real loopback ServiceHost and authenticated CURVE `ServiceMarketClient` to consume the OKX ticker-queue projection using the existing `sdk.market.event` / `market.event` `{key,data}` contract. The `account_session_worker` path calls `GatewaySessionOwner.create_provider(account_session_ref)` inside the Host resource callback; credentials and providers remain local. The operator exposes finite status (`state`, `session_generation`, `ready`, `fresh_for_seconds`) and owner-thread `recover_account_session()`. If worker construction fails before ownership transfer, the operator attempts provider close and retains unconfirmed cleanup as explicit uncertainty.

Creator-specific thread contract: create `LocalAccountSessionGuard` and the account binding/deployment on the same process and thread that starts, serves, recovers, and closes the service. The guard records creator PID/TID; `GatewayAccountSessionWorker` records construction thread; the deployment binds its lifecycle to the Host owner thread. Constructing the guard on another thread and transferring it is unsupported.

## Verification

The author reports 3 passed / 0 failed / 0 skipped in 1.19 seconds: one ServiceHost market-projection integration and two parameterized provider-cleanup cases. `py_compile` passed. SDK pytest stdout/JUnit and exact command were not retained; the raw ZIP labels its structured result record as non-raw.

Independent reviewer `review_bt_v3_cpf` returned `GO_STATIC` on these source pins; no standalone review document was supplied. Supporting AFI09 captures show the local synthetic browser/HTTP/SDK/operator path, two AFI clients sharing one owner, and owner-thread recovery without a second resolver/factory call. This is not formal AFI09 acceptance. The AFI archive notes its browser output is not a standalone exit receipt or invocation-time source binding.

After the author run, SDK R238 was committed at `7b6f82c3916dd632fddeb53a000da0c7c2bdc902`. Its delta adds a `TrustedPaperOwnerWritePolicy` only for `execution/account_owner` and `service.order_entry/orders.submit`. R263 uses `market_hub` and a worker-construction-failure case; it does not construct a Paper owner or invoke `orders.submit`. Candidate pins are unchanged and this descendant was not retested.

## Limits and next work

No Root QA, live account/provider/exchange call, wheel build, wheel inspection, or isolated install was performed. `setup.py` package discovery includes `bt_api_py.*`, but this is not wheel verification. The new API is a trusted operator composition entry over the existing Host; it does not establish that every application deployment command/configuration invokes it. Formal MIG01 remains incomplete.

Continue consumer retirement and provider binding through a real application entry, coordinate Binance adapter work with SCN, then perform required package/installation checks. The exact six-path parent scope is listed in `manifest.json`; no unrelated changes are included.
