# DIM06 status — 2026-10-16 (final; increment: fixed-source QA + clause→test mapping)

Scope: DIM06 (正式迭代计划.md line 778) = CTP 交易日/结算/重连与 OrderRef 恢复；各 venue 持仓/保证金模式、
订单类型/TIF 不兼容拒绝；已支持的账户管理动作与其他策略隔离、UNKNOWN 阻断恢复；未实现管理能力明确拒绝。
Acceptance vehicle per clause: "adapter 合同与本地故障验证，provider 行为另按 VENUE02 记录".
Gapmap basis: .codex-tmp/arbdim-gapmap2.md DIM06 section (8 sub-items + E).

## Environment / provenance
- Parent repo D:\bt_api_py on dev @ 4e46ff01a68edb15a4f4803fea45ee9da5a7f2c6 (docs commit landed mid-task;
  working tree still has MIG01-R1 uncommitted edits). Base b4aabd1e…, ctp a497bab6… (both dev, clean).
- python C:\anaconda3\python.exe (3.11); PYTHONPATH=D:\bt_api_py;…bt_api_base\src;…bt_api_ctp\src.
  NOTE: installed site-packages bt_api_ctp 2.0.2 is stale (SDK core-reference requires >=2.0.4);
  ctp worktree src 2.0.4+iteration41.i9 shadows it (pinned).
- Runner: .codex-tmp/iteration10-focused-qa.py (unchanged, runner format); builder: .codex-tmp/iteration10-dim06-spec-build.py;
  specs: .codex-tmp/iteration10-dim06-r1-spec.json / -r2-spec.json. 30 source pins each.
- Extra pytest plugin load: `-p xdist` only (strict-markers + autoload-disabled requires the marker registration;
  no -n parallelism). pytest-asyncio NOT loaded: 0.23.0 crashes collection with pytest 8.0.0 --import-mode=importlib
  ('Package' object has no attribute 'obj') → the one async node stays skipped (environment limitation, recorded below).

## Exclusions (mission)
- tests/bt_api_contract/test_direct_zmq_bt_api_contract.py, test_normalized_public_api.py — modified by MIG01-R1.
- tests/test_bt_api_quality.py — modified by MIG01-R1 (outside subset).
- r2 additionally --deselects the 3 execution_session nodes broken by MIG01-R1's now-live ZMQ retirement
  (its own breakage matrix documents them; MT5-ZMQ wire nodes, not CTP).

## Run results (fixed-source, no host, no network)
- r1 (full subset, 13 modules, 519 nodes, no deselect): pytest_exit=1; 513 passed / 3 failed / 3 skipped, 21.89s.
  The only 3 failures = MIG01-R1-caused LegacyForwardingRetiredError in
  test_execution_session.py::test_session_uses_real_gateway_command_roundtrip_with_native_mt5_intent[result0-True/result1-False/result2-False].
  sources_unchanged=true, heads_unchanged=true; stdout_sha256 8bfd63a653010e0947bc8580aaf9f41fe7b03200e83205d06971f0062f4f1527; stderr empty.
  Dir: D:\temp\it10dim06-20261016-r1
- r2 (same subset, 3 deselects): pytest_exit=0; 513 passed / 3 skipped / 3 deselected, 31.36s; sources/heads unchanged;
  stdout_sha256 ac04c31821aaa16612271b868e3566faf84d027043cf338c03c9643fe72b8cdc; stderr empty.
  Dir: D:\temp\it10dim06-20261016-r2
- 3 skips (both runs): 2 POSIX directory-fsync nodes (platform contract on win32) + 1 async node (asyncio plugin incompat, above).
- Per-module (r2): orderref_consume 41p; execution_session 181p/2s (3 deselected in r2; 3 failed in r1);
  execution_arming 82p; execution_recovery 63p; position_mode_mutation 37p; position_intent_contract 19p;
  ctp_order_mapping 7p; cross_venue 17p; simnow_public_adapter 42p; entry_approval_arm 11p;
  simnow_read_observation 3p; entry_write_guard 8p/1s; ctp_dependency_version_pin 2p.
- These runs are increment evidence only — NOT a formal DIM06 acceptance row change (汇总 stays 25/68; vehicle question stays with PM).

## Clause → test mapping (status at r2 fixed-source level; node names)

| # | 子要求 (clause line 778) | Status | Key nodes (module::test — all pass in r2 unless noted) |
|---|---|---|---|
| 1 | CTP 交易日 | COVERED (contract) | orderref_consume::test_exact_i9_reservation_still_requires_matching_fields[bad_reservation1] (day mirror bound); orderref_consume::test_managed_ctp_request_identity_must_match_exact_mirror[trading_day]; arming::test_arm_rejects_current_session_mismatch[trading_day-20260910-execution_arm_trading_day_mismatch]; entry_write_guard::test_entry_submit_rejects_candidate_binding_before_intent_or_native_write[trading_day]; recovery::test_old_day_terminal_zero_fill_cycle_does_not_block_new_day; recovery::test_old_day_reconciled_absent_cycle_does_not_block_new_day; recovery::test_old_day_unresolved_unknown_or_exposed_cycle_remains_fail_closed[active/unknown/exposure]; read_observation::test_public_read_observation_binds_native_certificate_without_authority (scope.trading_day) |
| 2 | 结算 (readback) | COVERED (contract) | arming::test_public_arm_requires_current_settlement_query_readback[False/None]; arming::test_controlled_settlement_token_invalidates_unconsumed_arm_grant; entry_approval_arm::test_confirm_ctp_settlement_from_approval_confirms_once; ::..._rejects_identity_mismatch; ::..._rejects_plain_capability_missing. Code: auto_settlement_confirm forced False (bt_api.py:300–327), _ctp_settlement_context (:4179) |
| 3 | 重连 (generations) | COVERED (contract) | arming::test_generation_change_consumes_and_rejects_pending_arm_grant; arming::test_arm_rejects_current_session_mismatch[connection_generation-4-execution_arm_connection_generation_mismatch]; recovery::test_prepare_authorization_fences_old_generation_but_allows_new_proof; recovery::test_public_recovery_can_reuse_generation_after_ordinary_arm_requires_recovery; entry_write_guard::test_entry_submit_rejects_candidate_binding_before_intent_or_native_write[generation]; simnow_public_adapter::test_managed_order_request_rejects_binding_from_another_active_scope (identity carries connection_generation). Native epoch/lease lives in Execution ctp_callback_* (outside this subset) |
| 4 | OrderRef 恢复 | COVERED (contract) | orderref_consume (41p; e.g. ::test_bound_managed_ctp_identity_rejects_allocator_before_local_side_effects; ::test_exact_mirror_binding_matches_every_order_identity_field; ::test_i9_cancel_readback_echo_is_exact_and_never_authorizes; ::test_cancel_public_sync_async_paths_reject_echo_before_dispatch); recovery::test_ctp_runtime_orderref_binding_is_fsynced_and_orphan_retry_freezes; ::test_managed_intent_cannot_allocate_a_second_sdk_orderref; ::test_managed_cancel_cannot_use_durable_orderref_as_dispatch_handoff; ::test_ctp_runtime_orderref_journal_collision_or_truncation_fails_closed; ::test_ctp_runtime_orderref_reverse_reservation_collision_fails_closed; ::test_session_async_ctp_cancel_does_not_journal_unclaimed_action; ::test_unclaimed_ctp_cancel_attempt_is_not_rebuilt_as_sent_after_restart; session::test_allocated_id_is_durable_before_return_and_survives_restart; position_intent_contract::test_ctp_local_order_ref_is_not_exchange_order_sys_id[query_order/cancel_order]; simnow_public_adapter::test_native_order_ref_candidates_over_12_ascii_bytes_are_rejected[3]; ::test_managed_order_request_uses_the_durable_ref_and_exposes_port_binding; ::test_managed_order_request_rejects_a_recovered_orphan_reservation_state; ::test_managed_order_request_cannot_supply_its_own_reference |
| 5 | 各 venue 持仓/保证金模式 | PARTIAL | COVERED: position_mode_mutation (37p: ::test_verified_mode_change_maps_native_value_and_updates_cache[4]; ::test_other_venues_remain_explicitly_unsupported[CTP___FUTURE/MT5___FX/OKX___SPOT]; ::test_write_timeout_is_unknown_and_does_not_read_back_or_update_cache; ::test_unknown_mode_blocks_every_public_crypto_placement[5 paths]; ::test_fresh_verified_mode_read_clears_latch_and_restores_placement[2]; ::test_every_inflight_public_crypto_placement_blocks_mode_mutation[5]); position_intent_contract::test_four_hedge_actions_reach_native_feed; ::test_ctp_dated_close_and_integer_contracts_are_preserved[close_today/close_yesterday]; ::test_ctp_does_not_round_fractional_lots; read_observation margin_rate query bridge (7-query certificate). MISSING: margin-mode (cross/isolated pool) write/mutation contract; 保证金池维度属 DIM03 AccountResourceKey 线 |
| 6 | 订单类型/TIF 不兼容拒绝 | PARTIAL | COVERED (order type): ctp_order_mapping::test_market_order_rejected_before_exchange_call (CTP limit-only pre-exchange reject); ::test_managed_ctp_order_rejects_incomplete_or_unsupported_identity[unsupported-managed-hedge]. MISSING (TIF): no node asserts any venue×TIF incompatibility; ctp container hardcodes GFD (bt_api_ctp/src/bt_api_ctp/containers/ctp/ctp_order.py:152–153); tests only pass TIF through (IOC). Gap recorded, no test added (no in-subset fix that is truthful: would need a new contract) |
| 7 | 账户管理动作与其他策略隔离 | COVERED (contract) | arming::test_public_state_hash_mapping_cannot_self_authorize_ctp_arm; ::test_private_controlled_issuer_rejects_forged_test_authority; ::test_private_ingress_revocation_invalidates_sibling_arm_grant; ::test_concurrent_disarm_cannot_be_undone_after_session_arm; ::test_opaque_arm_grant_binds_the_authorized_strategy_cycle; ::test_armed_ctp_does_not_expand_account_level_or_bulk_write_capabilities; ::test_dynamic_session_fence_revokes_an_existing_arm; ::test_dynamic_runtime_identity_fence_revokes_an_existing_arm; ::test_different_proof_fails_closed_after_arm; entry_approval_arm (11p: ::test_arm_from_approval_is_one_shot; ::..._rejects_runtime_material_drift; ::..._rejects_context_mismatch; ::..._rejects_base_schema_capability; ::..._rejects_expired_capability); recovery::test_concurrent_ordinary_arm_cannot_borrow_recovery_capability; ::test_custom_ctp_instance_injects_capability_for_sync_and_async_writes; entry_write_guard::test_managed_ctp_without_sealed_entry_guard_and_caller_lambda_stay_closed; read_observation NON_AUTHORIZING bridge. Cross-strategy service isolation accepted separately (REG01); CTP-specific Host/service-stack node absent (PM vehicle question) |
| 8 | UNKNOWN 阻断恢复 | COVERED (contract) | recovery::test_fully_frozen_nonzero_exposure_is_manual_and_cannot_arm; ::test_v2_bundle_recovery_requires_manual_intervention_for_unknown_leg; ::test_caller_stable_flag_cannot_bypass_sdk_query_barrier; ::test_managed_ctp_order_without_consumed_mirror_stops_before_fake_transport; ::test_old_day_unresolved_unknown_or_exposed_cycle_remains_fail_closed[unknown]; session::test_async_cancel_cancellation_persists_unknown_before_reraising; session::test_failed_query_of_known_unknown_order_preserves_blocking_state; simnow_public_adapter::test_typed_action_ack_is_never_promoted_to_cancelled_and_redacted_dict_is_unknown; ::test_cancel_normalization_and_single_execution_update_keep_unknown; position_mode latch nodes (#5). No multi-process Host combo node (R150/EXE02/MON02 cover other surfaces) |
| E | 未实现管理能力明确拒绝 | COVERED (contract) | position_mode_mutation::test_other_venues_remain_explicitly_unsupported[CTP___FUTURE…]; ::test_missing_provider_mutation_fails_before_readback; ::test_zmq_and_raw_result_opt_out_fail_before_feed_access; ctp_order_mapping unsupported orders; simnow_public_adapter::test_adapter_write_calls_fail_closed_without_issuing_approval_or_native_calls; entry_write_guard candidate-binding/guard rejections; session::test_configured_session_rejects_unmanaged_async_writes; session::test_configured_session_has_no_unmanaged_public_write_escape |

## What a future DIM06 claim would still need
1. PM confirmation of the acceptance vehicle (direct-BtApi contract vs service-stack adapter) — gapmap flag; if service stack, +large.
2. Resolve/attribute the MIG01-R1-broken 3 session nodes (D2/D3 gateway-wire round) so a full-subset run can go green without deselects.
3. Optional: margin-mode contract (DIM03 line) and a venue×TIF incompatibility matrix (1 small contract) to lift #5/#6 to COVERED.
4. Environment hygiene: reinstall bt_api_ctp >=2.0.4 consistently (or keep PYTHONPATH pin), and a pytest-asyncio version compatible with pytest 8.0.0+importlib so the async write-guard node runs.

## Milestones
- [x] r1 status written early
- [x] node enumeration / collect (517 nodes; +2 version-pin module = 519)
- [x] specs built with 30 source pins + 3 repo head guards
- [x] r1 + r2 QA runs completed (junit/exit/result.json, sources/heads unchanged)
- [x] clause→test mapping table (COVERED/PARTIAL/MISSING)
- [x] final report
