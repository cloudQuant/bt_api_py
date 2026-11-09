"""Gateway settlement decision A with the real CTP trader and a synthetic native."""
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


def _native_confirm(helper, native, trader, calls, journal, *, succeed=True):
    from types import SimpleNamespace

    def confirm(_field, request_id):
        row = journal.settlement_get(ROUTE, helper.TODAY)
        # Decision A: the durable attempt marker precedes the native request.
        calls.append(('native', None if row is None else row.status))
        if not succeed:
            return 1
        native.confirmation_day = helper.TODAY
        trader._spi.OnRspSettlementInfoConfirm(
            SimpleNamespace(BrokerID='9999', InvestorID='account', ConfirmDate=helper.TODAY),
            SimpleNamespace(ErrorID=0, ErrorMsg=''), request_id, True)
        return 0
    return confirm


@pytest.mark.parametrize('grant_revoked_after_observation', [False, True])
def test_new_gateway_confirmation_claims_after_marker_and_confirms_by_readback(
        settlement, monkeypatch, grant_revoked_after_observation):
    helper, provider, native, journal, admission, controller = settlement
    trader = provider._trader()
    calls = []
    monkeypatch.setattr(native, 'ReqSettlementInfoConfirm',
                        _native_confirm(helper, native, trader, calls, journal))
    original_observe = provider.observe_settlement
    revoked = []

    def observe_then_revoke():
        observation = original_observe()
        if grant_revoked_after_observation and not revoked and observation is not None:
            grant = admission.issue(ROUTE, WriteOperation.SETTLEMENT_CONFIRM,
                                    observation.session_generation,
                                    settlement_binding=SettlementWriteBinding(observation, 'other'))
            # revoke() clears outstanding grants only; it is not an
            # account-level revocation of settlement authority.
            admission.revoke(ROUTE)
            revoked.append(grant)
        return observation

    monkeypatch.setattr(provider, 'observe_settlement', observe_then_revoke)
    result = controller.tick()
    assert bool(revoked) is grant_revoked_after_observation
    assert result.state == 'CONFIRMED' and result.observed_ready and controller.ready
    assert calls == [('native', 'ATTEMPTING')]
    row = journal.settlement_get(ROUTE, helper.TODAY)
    assert row.status == 'CONFIRMED' and row.marker_id is not None
    # A later tick reads back only; it never sends again.
    assert controller.tick().observed_ready and calls == [('native', 'ATTEMPTING')]


def test_account_freeze_before_claim_removes_unsent_attempt(settlement, monkeypatch):
    helper, provider, native, journal, _admission, controller = settlement
    trader = provider._trader()
    calls = []
    monkeypatch.setattr(native, 'ReqSettlementInfoConfirm',
                        _native_confirm(helper, native, trader, calls, journal))
    frozen = {'after_marker': True}
    original_begin = journal.settlement_begin

    def begin_then_freeze(*args, **kwargs):
        start = original_begin(*args, **kwargs)
        # An account FREEZE committed between the marker and the final claim.
        monkeypatch.setattr(journal, 'settlement_account_frozen',
                            lambda: frozen['after_marker'])
        return start

    monkeypatch.setattr(journal, 'settlement_begin', begin_then_freeze)
    result = controller.tick()
    assert result.state == 'CONFIRMATION_REVOKED' and not result.observed_ready
    assert calls == [] and journal.settlement_get(ROUTE, helper.TODAY) is None
    # Freeze before the attempt: refused with no marker either.
    assert controller.tick().state == 'ACCOUNT_FROZEN' and calls == []
    assert journal.settlement_get(ROUTE, helper.TODAY) is None
    frozen['after_marker'] = False
    confirmed = controller.tick()
    assert confirmed.state == 'CONFIRMED' and calls == [('native', 'ATTEMPTING')]


def test_unclear_native_result_stays_unknown_and_is_never_resent(settlement, monkeypatch):
    helper, provider, native, journal, _admission, controller = settlement
    trader = provider._trader()
    calls = []
    monkeypatch.setattr(native, 'ReqSettlementInfoConfirm',
                        _native_confirm(helper, native, trader, calls, journal, succeed=False))
    first = controller.tick()
    assert first.state == 'CONFIRMATION_UNKNOWN' and not first.observed_ready
    assert calls == [('native', 'ATTEMPTING')]
    assert journal.settlement_get(ROUTE, helper.TODAY).status == 'UNKNOWN'
    assert controller.tick().state == 'READBACK_REQUIRED' and len(calls) == 1
    native.confirmation_day = helper.TODAY  # e.g. the request did reach the venue
    assert controller.tick().observed_ready and len(calls) == 1
    assert journal.settlement_get(ROUTE, helper.TODAY).status == 'CONFIRMED'


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
