# AFI financial projection review delta — 2026-10-16

**Decision: GO for the requested focused QA.** The author’s delta closes the earlier route-wiring gap for the unavailable projection and improves the client fixtures/types. This remains synthetic local-component evidence only; it is not AFI02/03 full acceptance. No QA or tests were run for this review.

Reviewed the delta at `D:\source_code\ai-for-investor`, branch `dev`, HEAD `e9c7ca1d6149ddae176f090f98dba4cee52dfe58`. The production composition service hash remains `6fd54ec025f9450c7c55a865e34a987792fd2ac63b4320661ed930ee33cd5196`. The four revised files match the supplied hashes:

- backend test `src/backend/tests/test_portfolio_compositions_api.py`: `e6f96b14ce11206d779ee89a5c5df3727cc89831c96e08cfc0d534bdced8e558`
- frontend API type `src/frontend/src/api/portfolio.ts`: `0c0ab91cb0a72498fed642aa97185e2d979a0bbfaef2ce682be5b638b22b6b75`
- frontend view `src/frontend/src/views/PortfolioPage.vue`: `e79a53a208cd8ba25f3c5de8608fb72ce489a6e6b3ad9980f774b5fcc22987cf`
- frontend component test `src/frontend/src/__tests__/views/PortfolioPage.test.ts`: `3beb33153cb31d7258f693c4c2236333952ad144ac3ae0b57a0ef1026e9a48e2`

## Delta review

The new backend test at `test_portfolio_compositions_api.py:222-377` exercises the actual composition router path. The fixture constructs the real `AccountBookReadView` contract class with an allowed unavailable financial payload (`:242-285`), supplies it through `_SyntheticReadSession.read_account_book` (`:70-73`, `:286-294`), grants both account and account-book access and binds the strategy selector (`:329-345`), then requests `/api/v1/portfolio/compositions/.../projection` over `ASGITransport` (`:348-355`). The aggregate `portfolio_router` includes the real compositions router, whose prefix and projection route match that path (`app/api/portfolio/__init__.py:7-11`; `app/api/portfolio/compositions.py:17`, `:102-113`). The test asserts the unavailable child in HTTP JSON, an unchanged provider total of 950, an actual account-book read, and absence of the private book/account identifiers (`test_portfolio_compositions_api.py:356-371`). The `ServiceReadWorker` dispatches this call to `session.read_account_book` (`app/services/service_read/worker.py:207-208`, `:390-396`). This resolves the previous concern that the only backend test called the helper directly.

The new HTTP node intentionally uses the `unavailable` branch. The available branch remains covered at the mapper helper and mocked-component levels, not by this HTTP fixture. That is an evidence boundary rather than a static defect: the mapper helper test covers the available shape and key omission, and this new route test proves the real service/HTTP wiring for the unavailable branch.

The frontend component fixture now uses nonzero `123.45 USDT` cash and retains an independent zero-fee assertion (`PortfolioPage.test.ts:713-731`, `:827-835`); the component still expects the provider total to display as 950 (`:794`, `:839-841`). This demonstrates the view displays financial cash separately and does not recalculate the mocked total. It does not establish backend available-branch HTTP behavior, which is why the backend route test remains limited to unavailable status.

The unavailable `code` type is now the same five-literal set enforced by the backend contract (`portfolio.ts:165-176`; `service_read/contracts.py:274-282`, `:459-468`). The earlier type drift is resolved. Optional `financial` and missing/explicit-absent rendering remain unchanged, preserving older response compatibility.

## Evidence level and remaining scope

The intended selected nodes are suitable for Root’s one-time QA: the backend helper node plus the synthetic FastAPI/worker boundary node, and the two affected frontend component nodes with the requested typecheck. If they pass, the evidence is helper-level source-fake, one synthetic FastAPI route through a synthetic `ServiceReadWorker` and `AccountBookReadView`, and mocked frontend component behavior. The test does not call a provider or establish AFI02/03 full acceptance. This review is static only; no selected test or typecheck was executed here.
