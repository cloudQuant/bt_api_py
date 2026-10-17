# Iteration10 Risk wire-account alias repair

Repository: bt_api_py, existing dev. Base: f69f02fffbe293b01f6ea96e65d35ee338380590.

Risk admission previously substituted the provider ledger account ID for the trusted wire account alias. An alias such as `account-a` routed to ledger `provider-account-a` was rejected as ACCOUNT_SCOPE_MISMATCH. `_admission_order` now uses the existing strict journal-command binding resolver, requires its exact configured target, and forwards the trusted WireScope.account_ref. Ledger identity and all mapper scope checks remain unchanged.

Root fixed-source unit: `test_risk_order_maps_trusted_wire_alias_separately_from_provider_account`, 1 passed, pytest exit 0; 386 source hashes and seven dev HEADs unchanged. It exercises real journal preparation, order projection and InstrumentRiskAdmissionMapper with different alias/provider IDs, and verifies a foreign alias is rejected. Output: `D:/temp/dim05-risk-wire-alias-root-unit-r1-20261017/`. The candidate contains only the selected HEAD-based method change plus the new test; package dependencies use committed sources.

Only the two method hunks, the new regression, and this record are delivered. Other changes in the live source file remain unstaged. This is no-Host component evidence, not full DIM05 or account/provider acceptance. The failed DIM05 R8 integration remains preserved and will be rerun only with the committed repair.
