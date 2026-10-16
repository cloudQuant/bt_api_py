# AFI financial public projection review — 2026-10-16

**Decision: GO for the specified focused source-fake QA only.** Static review found no blocking projection or rendering defect in the five pinned files. This is not a claim that QA passed, that the HTTP integration was exercised, or that provider/account acceptance occurred. No tests were run for this review.

## Scope and pins

Reviewed `D:\source_code\ai-for-investor` on branch `dev`, HEAD `e9c7ca1d6149ddae176f090f98dba4cee52dfe58`. The five changed files match the requested SHA-256 pins:

- `src/backend/app/services/portfolio_composition.py` — `6fd54ec025f9450c7c55a865e34a987792fd2ac63b4320661ed930ee33cd5196`
- `src/backend/tests/test_portfolio_compositions_api.py` — `faf8272e8e73a618d92dd3c9b05a58a73b8c9d4dc4b2ef0a393f397993b2f144`
- `src/frontend/src/api/portfolio.ts` — `3b0cad63e319f27704eaf73964b4449b17ec0b60a80252dcd5a92b82cfafe4d5`
- `src/frontend/src/views/PortfolioPage.vue` — `35be4f99640cc6b34ae29e991e4cf5a501f0fa5bd777fd8e5fc9ff643042f0ab`
- `src/frontend/src/__tests__/views/PortfolioPage.test.ts` — `60c9437972a1e29c7a2fbf71718df2f0a7e712fe1c364749a86dc41a916435fb`

The repo `AGENTS.md` references `.joyincode/rules/backend.md` and `.joyincode/rules/frontend.md`, but `.joyincode` is absent in this checkout. I followed the available `AGENTS.md` and did not infer additional rules.

## Findings

- **The input contract and public projection are narrowly bounded.** The existing `AccountBookReadView` accepts either the legacy exact root field set or that set plus `financial` (`src/backend/app/services/service_read/contracts.py:90-109`). Its financial validator requires exact root and nested keys, fixed source/freshness/cut relationship, `complete is True`, known states, and a finite unavailable-code set (`:455-493`; code allowlist at `src/backend/app/services/service_read/contracts.py:274-282`). Money, position, ledger-cut, and owner shapes are validated before `to_dict()` reaches the new mapper. The mapper then constructs a fresh public shape, omits ledger `account_key` and owner `execution_scope_key`, keeps the cut relationship explicit, and stringifies nanosecond timestamps (`src/backend/app/services/portfolio_composition.py:905-979`). This gives the required absent/unavailable/available distinction and preserves decimal zero strings separately from `None` cost basis.

- **The projection stays separate from provider equity.** Provider `account_total` is computed from `AccountReadView` before account-book financial data is attached (`src/backend/app/services/portfolio_composition.py:263-264`, `:359-370`). The new payload is a sibling under `account_book_sources`; it introduces no valuation, FX, or total calculation. The UI also states that persisted cash/cost are not live equity and are not added to the total (`src/frontend/src/views/PortfolioPage.vue:565-568`). Its timestamp is shown under an explicitly independent ledger cut with persisted-cut freshness (`:586-620`), so it is not presented as the provider observation time.

- **Old responses remain renderable.** The TS property is optional (`src/frontend/src/api/portfolio.ts:195-219`), and the component treats a missing property or explicit `absent` status as “not provided” before rendering the available variant (`src/frontend/src/views/PortfolioPage.vue:570-585`). The second extended frontend node supplies a legacy account-book source without `financial` and expects the absent message (`src/frontend/src/__tests__/views/PortfolioPage.test.ts:842-927`).

- **The targeted tests stop short of the HTTP boundary.** The nominated backend node, `test_public_account_book_projection_preserves_financial_states_and_hides_internal_keys`, calls `_public_financial_projection` directly (`src/backend/tests/test_portfolio_compositions_api.py:83-201`). It covers the three statuses, exact nanosecond string conversion, zero versus missing cost basis, and omission of the internal key fields. It does not construct an `AccountBookReadView`, call `PortfolioCompositionService.project`, or assert the route JSON contains the new child. The route returns the service dictionary directly (`src/backend/app/api/portfolio/compositions.py:102-113`). The existing HTTP fixture only implements `read_account` (`test_portfolio_compositions_api.py:43-67`), so it does not exercise this account-book path.

- **The two frontend extensions are mock-backed component checks.** The available/unavailable case injects the projection through `portfolioApi.getCompositionProjection` (`src/frontend/src/__tests__/views/PortfolioPage.test.ts:777-788`) and checks rendering plus the displayed account total (`:820-839`). The available fixture's financial cash is `0 USDT` (`:713-727`), so that assertion would still pass if a future faulty integration added this zero amount to the total. Static code is separate today, but this fixture is not a regression proof for excluding non-zero financial cash. The old-response case is likewise a component compatibility check, not an HTTP test.

## Type and evidence notes

The TypeScript union mirrors the three statuses, available literals, arrays, nullable cost fields, and nanosecond strings. One type is looser than the backend contract: `PortfolioFinancialProjection` uses `code: string` for unavailable results (`src/frontend/src/api/portfolio.ts:165-171`), while backend input validation permits only the finite constants at `service_read/contracts.py:274-282` and `:459-468`. This is non-blocking for rendering, but a literal union would keep the client contract aligned. `cursor` remains a JS `number` (`portfolio.ts:129-135`); the backend currently accepts an unbounded non-negative Python integer (`contracts.py:359-364`), so exactness above JS safe integer range is not guaranteed. The nanosecond fields are correctly serialized as decimal strings.

The source implementation is suitable to proceed to the three specified focused tests. If their execution passes, record the evidence as helper-level source-fake plus mocked component coverage, not HTTP-boundary behavior. The current test fixture does not validate non-zero execution cash exclusion from `account_total`; add that case only if the round's acceptance requires a regression assertion for that invariant.


