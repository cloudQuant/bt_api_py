from __future__ import annotations

import pytest
import test_ctp_execution_owner as owner_fixtures
import test_ctp_managed_submit_port as submit_fixtures
from bt_api_execution import InvalidStateTransition

from bt_api_py.ctp_admission_authority import CtpNativeDispatchBindingV1
from bt_api_py.ctp_execution_owner import CtpExecutionOwner
from bt_api_py.ctp_managed_submit_port import CtpManagedSubmitPort


def _unbound_port(case) -> CtpManagedSubmitPort:
    intent = case.intent
    return CtpManagedSubmitPort(
        case.store,
        case.scope,
        case.writer_lease,
        case.trader,
        case.session_adapter,
        case.reconciliation_port,
        CtpNativeDispatchBindingV1(
            account_ref=case.scope.account_ref,
            scope_key=case.scope.key,
            investor_id="investor-1",
            intent_instrument=intent.instrument,
            provider_instrument=intent.instrument,
            exchange_id="SHFE",
        ),
        session_generation_id=case.session_binding.session_generation_id,
    )


@pytest.mark.unit
def test_execution_owner_accessor_reuses_exact_bound_owner_and_fails_closed_when_unbound(
    tmp_path, monkeypatch
):
    with owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        unbound = _unbound_port(case)
        api = case.trader._api
        with pytest.raises(InvalidStateTransition, match="not bound"):
            _ = unbound.execution_owner
        assert unbound._owner is None
        assert unbound._issuer is None
        assert unbound._gate is None
        assert case.trader._api is api

        port, gate, _facade, _context = submit_fixtures._compose(case, case.intent)
        owner = port._owner
        issuer = port._issuer
        assert type(owner) is CtpExecutionOwner
        assert owner is not None
        assert port.execution_owner is owner
        assert port.execution_owner is owner
        assert port._owner is owner
        assert port._issuer is issuer
        assert port._gate is gate
        assert port._trader is case.trader
        assert port._session_adapter is case.session_adapter
        assert port._reconciliation_port is case.reconciliation_port
        assert case.trader._api is api

        with pytest.raises(AttributeError):
            port.execution_owner = None
        assert port.execution_owner is owner
