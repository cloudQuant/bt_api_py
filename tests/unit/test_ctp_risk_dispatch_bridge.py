"""Focused negative checks for the managed CTP/Risk bridge boundary."""

from __future__ import annotations

import pytest
import test_ctp_execution_runtime as runtime_fixtures
from bt_api_execution import ContractValidationError

from bt_api_py.ctp_risk_dispatch_bridge import CtpRiskDispatchBridge, CtpRiskDispatchError
from bt_api_py.service_risk_order_journal import RiskOrderJournal


@pytest.mark.unit
def test_bridge_rejects_plain_reserve_and_wrong_journal_before_fencing(
    tmp_path, monkeypatch
):
    with runtime_fixtures._runtime_case(tmp_path, monkeypatch) as case:
        resources = runtime_fixtures._resolve_resources(case)
        bridge = case.provider.last_runtime._owned_risk_bridge
        assert bridge is resources.values["admission"]["order_gate"]

        with pytest.raises(CtpRiskDispatchError) as reserve_error:
            bridge.reserve(object())
        assert reserve_error.value.code == "AUTHENTICATED_CONTEXT_REQUIRED"

        wrong_journal = RiskOrderJournal(
            tmp_path / "wrong-risk-order-journal.sqlite3", writer_id="wrong-authority"
        )
        original_writer_fence = RiskOrderJournal.writer_fence
        attempted_wrong_fence = []

        def reject_wrong_fence(journal, *args, **kwargs):
            if journal is wrong_journal:
                attempted_wrong_fence.append(True)
                raise AssertionError("authority validation must precede fence acquisition")
            return original_writer_fence(journal, *args, **kwargs)

        monkeypatch.setattr(RiskOrderJournal, "writer_fence", reject_wrong_fence)
        with pytest.raises(ContractValidationError):
            CtpRiskDispatchBridge(
                bridge._adapter,
                wrong_journal,
                account_scope=bridge.account_scope,
                exchange_name="SHFE",
            )
        assert attempted_wrong_fence == []
        assert resources.ready() is True
