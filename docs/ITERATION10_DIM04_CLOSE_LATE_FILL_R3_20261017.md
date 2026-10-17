# DIM04 CLOSE late-fill provider-order continuity — R3

- Repository: `D:/bt_api_py`, branch `dev`.
- Scope: selected CLOSE observation-history lineage checks in `service_risk_order_journal.py`, the CLOSE recovery hook in `service_risk_order_entry.py`, and the focused SQLite test `test_dim04_strategy_net_close_late_fill.py`.
- Behavior: repeated CLOSE ORDER/FILL observations remain bound to the first concrete provider order ID; an initial missing ID may bind once, but later switching IDs rejects the history. OPEN's one-pair grammar is unchanged.
- Review: R2 production semantics received `GO_STATIC` from `review_operator_lifecycle`; the R3 increment received `GO_STATIC` after changing only the fixture's `wire_account_ref` to the internal account identity expected by the existing Risk adapter.
- Acceptance: the selected late-partial-to-terminal test passed 1/1 on an export of accepted SDK HEAD `394eacc58e2c61769e0c9b807284f6468137c8af` plus its exact Base/Risk/Execution gitlinks. One existing `asyncio_default_fixture_loop_scope` warning; 2.27 seconds; exit 0. The negative provider-order-switch assertion is part of this selector.
- Raw output: `.codex-tmp/dim04-close-late-fill-verified-394e-r2-result.txt`, 550 bytes, SHA-256 `11A371F142C038CDADC6FE74E276B0648AD6AD777DDA1D916C1855F5FFAC0F0F`. The source-bound manifest and exact selected patch are retained under `.codex-tmp/dim04-close-late-fill-selected-r3-*`.
- Evidence limit: local synthetic SQLite/direct RiskOrderEntryService path only. No ServiceHost, exchange, or real-account acceptance is claimed.
- Commit: this record is included with the selected source changes; the containing commit hash is reported in the delivery message.