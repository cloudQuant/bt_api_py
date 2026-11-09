"""Gateway settlement remains read-only until final authorization is proven."""
import importlib
from pathlib import Path

import pytest
from bt_api_base.direct_write_protocol import SettlementWriteBinding, WriteOperation
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.gateway_settlement import GatewaySettlementController
from bt_api_py.write_admission import WriteAdmission

ROUTE = 'CTP___FUTURE'


@pytest.fixture
def settlement(tmp_path, monkeypatch):
    import bt_api_ctp.direct_write_provider as provider_module
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'bt_api/bt_api_ctp/tests'))
    helper = importlib.import_module('test_iter62_settlement_readback_on_write')
    monkeypatch.setattr(provider_module, '_READBACK_MONITOR_INTERVAL', 3600.0)
    provider, trader, native = helper._real_setup()
    helper._login(trader, native, session_id=20)
    native.confirmation_day = '20261105'
    native.calls.clear()
    directory = tmp_path / 'guard'
    directory.mkdir()
    guard = LocalAccountSessionGuard(directory, 'test-account', 'owner')
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(tmp_path / 'journal', 'test-account', generation=generation)
    admission = WriteAdmission({ROUTE: True})
    coordinator = DirectWriteCoordinator(admission, lambda _: provider)
    coordinator.provider(ROUTE)
    dispatcher = GatewayDirectDispatcher(journal, ROUTE, guard, lambda *_: None, lambda _: ('unused',), lambda _: True)
    controller = GatewaySettlementController(coordinator, ROUTE, expected_local_provider=provider,
        expected_account_binding=trader.get_session_state()['account_fingerprint'], journal=journal, dispatcher=dispatcher)
    yield helper, provider, native, journal, admission, controller
    guard.release()
    provider.close()


@pytest.mark.parametrize('revoke_after_observation', [False, True])
def test_new_gateway_confirmation_never_reaches_native_and_readback_remains_available(
        settlement, monkeypatch, revoke_after_observation):
    helper, provider, native, journal, admission, controller = settlement
    calls = []
    def confirm(*_args, **_kwargs):
        calls.append('native')
        raise AssertionError('Gateway settlement confirmation must not enter native')
    monkeypatch.setattr(native, 'ReqSettlementInfoConfirm', confirm)
    original_observe = provider.observe_settlement
    revoked = []
    def observe_then_revoke():
        observation = original_observe()
        if revoke_after_observation and not revoked:
            grant = admission.issue(ROUTE, WriteOperation.SETTLEMENT_CONFIRM, observation.session_generation,
                settlement_binding=SettlementWriteBinding(observation, 'synthetic-marker'))
            admission.revoke(ROUTE)
            revoked.append(grant)
        return observation
    monkeypatch.setattr(provider, 'observe_settlement', observe_then_revoke)
    result = controller.tick()
    assert result.state == 'CONFIRMATION_AUTHORITY_UNAVAILABLE'
    assert bool(revoked) is revoke_after_observation
    assert result.observed_ready is False and calls == []
    assert journal.settlement_get(ROUTE, helper.TODAY) is None
    assert controller.tick().state == result.state and calls == []
    # Another trusted actor may already have confirmed; reading that fact
    # does not need or issue a Gateway confirmation grant.
    native.confirmation_day = helper.TODAY
    observed = controller.tick()
    assert observed.observed_ready and observed.state == 'CONFIRMED' and controller.ready
    assert journal.settlement_get(ROUTE, helper.TODAY).marker_id is None
    assert calls == []


def test_existing_unknown_marker_is_not_retried_and_confirmed_readback_resolves(settlement, monkeypatch):
    helper, provider, native, journal, _admission, controller = settlement
    calls = []
    monkeypatch.setattr(native, 'ReqSettlementInfoConfirm', lambda *_: calls.append('native'))
    observation = provider.observe_settlement()
    journal.settlement_begin(observation, 'old-marker', before_begin=lambda: None, before_commit=lambda: None)
    journal.settlement_mark_unknown(observation, 'old-marker')
    assert controller.tick().state == 'READBACK_REQUIRED'
    assert journal.settlement_get(ROUTE, helper.TODAY).status == 'UNKNOWN'
    assert calls == []
    native.confirmation_day = helper.TODAY
    assert controller.tick().observed_ready
    row = journal.settlement_get(ROUTE, helper.TODAY)
    assert row.status == 'CONFIRMED' and row.marker_id == 'old-marker'
    assert calls == []
