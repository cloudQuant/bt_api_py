# MIG01 market history provider round R265

Date: 2026-10-17
Repository: `D:\bt_api_py`, branch `dev`; SDK parent baseline `eeb1c4c8a08795ddf8a5a39519c5824905dfe117`.
Evidence level: local synthetic source and Host checks plus independent static review. This is not real OKX/account evidence and does not complete formal MIG01 acceptance.

## Change

This round connects the existing typed `MarketHistoryRequest` / `MarketHistoryPage` path through `ServiceMarketClient`, `GatewayOperatorDeployment`, and `GatewayMarketHub` to a bounded OKX history provider. History uses independently configured `bar` keys and principal ACLs. `market.history` is advertised only when a provider is configured; history-only clients need no subscriber factory and issue no SUB subscription. Explicitly subscribed `bar` stream clients remain supported.

The OKX adapter uses the existing fixed history-candles route and request machinery. Its calls, pagination, limits, and bar parsing are bounded. Tests use synthetic HTTP leaves. No credentials or real exchange/account calls were used.

## Commits and exact scope

The two child packages were committed first, with only the selected paths:

- `bt_api/bt_api_okx`, `dev`: `259208d6a1cf3301456603e8a5290052c9ec80b2` (parent `7691ab439438e827da104de501aea380f70e4d9c`); three paths: `request_base.py`, new `market_history_provider.py`, and its test.
- `bt_api/bt_api_gateway`, `dev`: `c2886f60b72544c5690248c450bb2bddb6660c61` (parent `e37b3288f07bc57bce89bcfad6f71277eb69d3dd`); two paths: `market_hub.py` and its history test. Concurrent account-session and CTP metadata changes remain outside this commit.

The SDK parent candidate is based on `eeb1c4c8a08795ddf8a5a39519c5824905dfe117`. Its five selected source/test paths and two child links are:

| Path | Bytes | SHA-256 |
|---|---:|---|
| `bt_api_py/gateway_operator.py` | 41,761 | `5D8494DAF062B921B26A2DB072C5174921ED193167003A11519790C2AB27EBB0` |
| `bt_api_py/gateway_operator_market.py` | 5,993 | `54BD396FDB70642A435504624D4D5A6B359EB12924261016D560087D05DAD903` |
| `bt_api_py/service_market.py` | 53,347 | `4C8211B55D19E009A157C53234E4714F4D753CE8F60899B314D5D890CB55F7BD` |
| `tests/unit/test_service_market_history.py` | 17,595 | `0444E6C7971E211981B249AA5A0DC628E41CB1A266EFE7F668CAEF8E857C9F80` |
| `tests/integration/test_gateway_operator_deployment.py` | 30,823 | `4C3D85CB39C6FBAF3ADDEF83B8FFBAFCFD19D6F1F37C4464DE853EFFBE80663F` |
| `bt_api/bt_api_okx` gitlink | — | `259208d6a1cf3301456603e8a5290052c9ec80b2` |
| `bt_api/bt_api_gateway` gitlink | — | `c2886f60b72544c5690248c450bb2bddb6660c61` |

The `gateway_operator.py` history hunks were reviewed separately from the previously committed quota-domain hunks. Unselected SDK, Gateway, and CTP work remains outside this round.

## Validation

Using `C:\anaconda3\python.exe` (Python 3.11.5), with plugin autoload disabled, `--noconftest`, and the Iteration 10 approved-target pytest configuration:

- OKX history provider tests: 8 passed.
- Gateway history service tests: 19 passed.
- SDK history unit tests after the compatibility correction: 8 passed.
- Existing DIM02 multi-key bar-stream integration node: 1 passed; this confirms an explicit subscriber still starts bar streams.
- SDK operator/Host/CURVE history and cleanup selectors: 2 passed with four existing warnings. This run predates SDK baseline `eeb1c4c8`; the current Host maintenance path received static review but was not rerun here.
- `ruff check`, `ruff format --check`, and scoped `git diff --check` passed on the final SDK history client and unit-test files.

The DIM02 node covers the previously identified bar-stream compatibility regression. The one-line `start()` guard change was exercised by the eight-test history file and the DIM02 node; the final subsequent formatter-only wrap did not change behavior.

Independent review `review_bt_v3_cpf` returned `GO_STATIC` on the final source pins. It checked history ACL separation, pagination and request limits, owner-thread provider creation and cleanup, optional capability advertisement, no-SUB history-only behavior, and preservation of explicit bar streaming. The reviewer did not run tests.

## Limits and remaining work

The Host integration uses local synthetic provider/HTTP leaves; no real OKX endpoint or account was accessed. The earlier two-selector Host/CURVE run predates SDK `eeb1c4c8`; the reviewer inspected that current Host path statically. The separate CTP native-stream-to-ServiceMarket factory remains unimplemented and is not claimed here. This round does not establish formal MIG01 completion.
