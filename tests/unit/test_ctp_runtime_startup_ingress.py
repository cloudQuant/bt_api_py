"""Fail closed when the real startup callback owner cannot drain ingress.

The runtime and Store are production implementations. Only the generated CTP
native API is synthetic, through the shared runtime fixture. This regression
blocks the real owner's callback pump during bootstrap, then restores it so
the fixture can perform normal owner-thread cleanup.
"""

from __future__ import annotations

import pytest
import test_ctp_execution_runtime as runtime_fixtures

from bt_api_py.ctp_execution_owner import CtpExecutionOwner
from bt_api_py.service_components import ServiceResourceConstructionError


@pytest.mark.unit
def test_startup_does_not_publish_ready_when_callback_pump_makes_no_progress(
    tmp_path, monkeypatch
):
    startup_pump_limits = []
    cleanup_pump_batches = []

    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        def no_progress(owner, max_events=32):
            startup_pump_limits.append(max_events)
            return ()

        # The temporary patch ends before _runtime_case performs cleanup, so
        # cleanup uses the same real owner pump and drains the persisted event.
        with monkeypatch.context() as blocked_pump:
            blocked_pump.setattr(CtpExecutionOwner, "pump_callbacks", no_progress)
            with pytest.raises(ServiceResourceConstructionError) as caught:
                runtime_fixtures._resolve_resources(
                    case, allow_construction_failure=True
                )

        runtime = case.provider.last_runtime
        assert runtime is not None
        assert caught.value.resources is case.resources
        assert runtime.status.ready is False
        assert runtime.status.phase == "BOOTSTRAP_FAILED"
        assert runtime.status.code == "STARTUP_CALLBACK_DRAIN_INCOMPLETE"
        assert case.bootstrap_diagnostic is not None
        assert case.bootstrap_diagnostic[1:3] == (
            "BOOTSTRAP_FAILED",
            "STARTUP_CALLBACK_DRAIN_INCOMPLETE",
        )
        assert startup_pump_limits == [case.config.callback_pump_events]

        owner = runtime._execution_owner
        assert owner is not None
        drain = runtime._store.read_ctp_callback_ingress_drain_status(
            runtime._callback_owner,
            writer_lease=runtime._writer_lease,
        )
        assert drain.owner_handle is runtime._callback_owner
        assert drain.owner_state == "ACTIVE"
        assert drain.last_source_sequence > drain.applied_source_sequence
        assert drain.pending_count == 1
        assert drain.drained is False
        pending = runtime._store.read_next_ctp_callback_ingress(
            runtime._callback_owner
        )
        assert pending is not None
        assert pending.source_sequence == drain.last_source_sequence
        assert pending.callback_name == "OnRspQrySettlementInfoConfirm"

        api = case.factory.instances[0]
        call_names = [call[0] for call in api.calls]
        assert call_names.count("ReqQrySettlementInfoConfirm") == 1
        assert not any(
            name in {"ReqOrderInsert", "ReqOrderAction"} for name in call_names
        )

        real_pump = CtpExecutionOwner.pump_callbacks

        def record_cleanup_pump(owner, max_events=32):
            applied = real_pump(owner, max_events)
            cleanup_pump_batches.append((owner, applied))
            return applied

        monkeypatch.setattr(CtpExecutionOwner, "pump_callbacks", record_cleanup_pump)

    # The shared fixture stopped the native leaf, drained the pending startup
    # event through the restored real pump, and closed the Store normally.
    assert runtime.status.closed is True
    assert all(owner is runtime._execution_owner for owner, _ in cleanup_pump_batches)
    assert sum(len(batch) for _, batch in cleanup_pump_batches) == 1
