# DIM01 phase-2 independent review + fixed-source QA status (review agent, p3 2026-10-16)
Scope: independent static review of uncommitted DIM01 p2 work + fixed-source QA re-runs A (integration) and
B (owned units). Read-only elsewhere; created only loose specs under .codex-tmp and fresh outputs under D:/temp.
## Verdict: GO
Spec A green (exit 0, 1 passed); spec B green (exit 0, 20 passed) with the 6 restart-registration nodes
explicitly excluded; the 6-failure claim reproduced on a fresh basetemp and judged a genuine registry/runtime
product gap outside DIM01's case text. Commit round may pin this evidence.
## 1. Source pins (live hashes, re-verified after both runs; HEAD D:\bt_api_py 466977392985ef1d7fa9b282a220096c8d19dff8, branch dev)
1. bt_api_py/service_risk_order_entry.py = 67c98d5be424c108c0c6988ac178214fca93e27612fdf1b33a80773670777890 (= status sha256-final)
2. bt_api_py/service_risk_order_journal.py = 37d89c3394b86245eae578540303707b15287a66f36de112bb18fc3556e6c3fe (= status)
3. tests/integration/test_dim01_identity_isolation_service.py = 733b85a8d480b94482f38c07bd68bb5b2d5bf4638e3170bbcda89056189f39df (= status, untracked)
4. tests/unit/test_service_risk_only_order_integration.py = 9d29b5884dfeada810a0c0c4885965ef04d3bd49c91da8091f7ce51d1412f552 (= status)
5. bt_api/bt_api_risk/src/bt_api_risk/core/admission.py = cc595bc72c5eb35ae8857e817d8173ae5457e2056fa363be1295f5459a5b5c58 (= status)
6. bt_api/bt_api_risk/src/bt_api_risk/core/exposure.py = 0d1584fe1a4809c27335e2d29268c692db9e59a9747c25adb115c489788d2da0 (= status)
7. tests/unit/test_service_order_entry_components.py = 164089a18c403e81cf35a6aea89bab356d7f440f8a665eff497b848d38895c8a
   (ADDED pin: status doc never pinned it; round in-scope parent file and spec-B node; hash recorded here. Pins
   1-6 replicate sha256-final.txt byte-for-byte.) Both specs guard repos {'D:\bt_api_py': 466977392985...} only.
## 2. Spec A (integration, canonical options)
Spec .codex-tmp/it10dim01-p3-r2a-spec.json; nodes = tests/integration/test_dim01_identity_isolation_service.py;
opts --noconftest --import-mode=importlib -q --no-header -p no:cacheprovider; PYTHONPATH =
D:\bt_api_py;D:\bt_api_py\bt_api\bt_api_base\src;D:\bt_api_py\bt_api\bt_api_risk\src (default pattern + risk src);
8-key unset list; PYTEST_DISABLE_PLUGIN_AUTOLOAD=1.
Out D:/temp/it10dim01-p3-20261016-r2a: runner exit 0; result.json pytest_exit 0, source_count 7,
sources_unchanged true, heads_unchanged true; junit tests=1 failures=0 errors=0 skipped=0; stdout sha256
2903f1ec20416829d6017d34a296d968c6336c1426511af22a2daad3c894014d matches result.json; "1 passed in 12.35s".
## 3. Spec B (owned units, normal options)
Pre-step full-file clean run (fresh basetemp D:/temp/it10dim01-p3-20261016-r2b-full-tmp, junit r2b-full-junit.xml):
6 failed / 14 passed, exit 1 - EXACTLY the 6 nodes the status doc names.
Spec .codex-tmp/it10dim01-p3-r2b-spec.json; opts -q --no-header -p no:cacheprovider (conftest active);
out D:/temp/it10dim01-p3-20261016-r2b: runner exit 0; result.json pytest_exit 0, sources_unchanged true,
heads_unchanged true; junit tests=20 failures=0 errors=0 skipped=0; stdout sha256
5c5046d130f9cbc5637a600ab51986cd39ce4e37f213971456e155e173eb8ba0 matches result.json; "20 passed in 10.91s".
Included nodes: all 6 tests of test_service_order_entry_components.py + the 14 passing ids:
  test_strategy_scope_template_varies_only_by_run_id; test_order_entry_accepts_service_registry_readonly_arguments;
  test_recovery_observation_timestamp_is_server_bound_and_strict; test_recursive_wire_freeze_matches_service_client_submit_contract;
  test_journal_appends_a_mapping_payload_after_prepared_command;
  test_terminal_source_binds_unknown_to_reserved_permit_without_command_column;
  test_real_typed_terminal_claim_accepts_only_exact_fill_history_without_provider_calls[fill-False-trailing_events0-True];
  idem [fill_then_one_unknown-False-trailing_events1-True]; [unknown_before_fill-True-trailing_events2-False];
  [two_trailing_unknowns-False-trailing_events3-False]; [event_after_fill-False-trailing_events4-False];
  test_false_dispatch_receipt_projects_ack_as_unknown_and_keeps_history;
  test_journal_records_normalized_observations_through_proof_ready; test_ambiguous_query_cardinality_cannot_be_tracked_as_one_order
  (exact ids + timings in D:/temp/it10dim01-p3-20261016-r2b/junit.xml).
Explicitly EXCLUDED (same 6 as status doc, reproduced on fresh basetemp):
  test_spawned_risk_service_gates_idempotent_orders_and_metadata_scope; test_claimed_but_unjournaled_order_is_unknown_after_service_restart;
  test_provider_written_order_is_reconciled_after_restart_without_resend;
  test_proof_ready_crash_recovers_from_exact_risk_receipt_without_redispatch[after_proof_ready-RISK_PROOF_READY_PROCESS_LOSS-1-process_loss];
  idem [after_risk_resolution-RISK_RESOLUTION_PROCESS_LOSS-0-process_loss]; [ack_journal_error_after_risk_resolution-None-0-unknown].
## 4. Static review
- Digest fix verified: test envelope {exchange_name, "BtApi.make_order(normalized=True)", request} equals
  production entry:927-935 and journal verification:3455-3464; test-side only, production untouched.
- No production contract weakening: entry adds fail-closed trade-domain validation, fingerprint-preserving
  optional fields, identity-conflict latch (suppress-wrapped best effort; still _mark_unknown); journal
  strengthens (reference average_price equality on 2 verification paths, terminal ACK refuses absent price,
  trade-identity declaration match in both source-match paths); _command_reply average_price now projects only
  the journal-validated ACK value (was hardcoded None) - strictly more evidence, not less.
- New/changed tests contain real assertions (state counts, permit/trade rows, latch freeze_active, receipt
  status, provider_write_count, replay equality, per-dimension positive+negative cases, dedup); no skip/xfail.
- Components test edit updates exact-equality expected op sets to live production (ops exist in
  service_components.py:489-504, 1706-1708); not a weakening.
- Case coverage: spec A exercises 同策略多品种/同账户多策略/同 venue 双账户/同名不同产品环境 + trade/order-ID
  collisions (UNKNOWN + latch, no double credit) + dedup via real SDK ServiceHost + owner SQLite stores.
## 5. 6-failure judgment: genuine product gap; NOT test isolation; outside DIM01 case text
Fresh-basetemp reproduction rules out leftover state. service_registry.py _claim_strategy_write:1940-1948
refuses a new run when the durable row.run_id differs; unregister:2050-2057 NULLs only active_registration_id,
never run_id. service_runtime.py:332 mints a fresh uuid run_id per start (injected at :1012), and the tests
assert a NEW run_id on restart + success (e.g. line 2212) - demanding takeover-on-release/run-adoption semantics
no code path implements. Registry session lifecycle is a different ownership area than ID-collision isolation;
excluding these 6 from this commit round is justified.
Caveat: in isolation, proof_ready params [after_risk_resolution-...] and [ack_journal_error-...] fail earlier
with ServiceHostError ORDER_RECOVERY_FAILED (service_host.py:711) at host restart; full-file context gives
DUPLICATE_ACTIVE_RUN for all 6. Same restart/recovery family; crash-injection timing sensitivity noted
(single-run transcripts D:/temp/it10dim01-p3-20261016-r2b-proof-single*.txt).
## 6. Caveats for the commit round
- Concurrent churn persists (service_execution_group_authority.py, service_execution_group_proofs.py, tests/unit/
  _service_group_provider_fixtures.py, test_service_account_integration.py, test_load01_...; untracked test_service_group_policy_control_plane.py).
  Re-verify before pinning; the 7 pins above were unchanged across both runs on this tree.
- tests/unit/test_service_risk_order_journal_decimal_match.py (untracked) counted in status run16 but not in
  this round's in-scope list and not pinned here.
- Re-runnable specs kept at .codex-tmp/it10dim01-p3-r2a-spec.json and ...-r2b-spec.json.
