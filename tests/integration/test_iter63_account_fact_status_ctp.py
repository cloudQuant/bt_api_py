"""Real CTP maintenance/SQLite with synthetic native callbacks, no account connection."""
from types import SimpleNamespace

from tests.integration.test_iter63_ctp_gateway_return_ingestion import _trade
from tests.integration.test_iter63_ctp_gateway_return_maintenance import real_owner  # noqa: F401


def test_fact_status_tracks_local_scan_and_disconnect_as_unavailable(real_owner):  # noqa: F811
    maintenance, _session, native, journal, _instrument, helper = real_owner
    guard = maintenance._ledger._guard
    journal.enable_controls(guard)
    journal.enable_strategy_ledger(guard)
    journal.enable_account_extensions(guard)
    assert maintenance.fact_status() == {'status': 'UNAVAILABLE', 'unattributed': None,
                                         'conflict': None, 'verified_external': None}
    maintenance.poll()
    status = maintenance.fact_status()
    assert status == {'status': 'NO_UNRESOLVED_OBSERVED', 'unattributed': 0,
                      'conflict': 0, 'verified_external': 0}
    # This is only a local fact scan, not an account reconciliation or cut proof.
    assert 'reconciled' not in status and 'provider_cut' not in status
    native.client._spi.OnRtnTrade(SimpleNamespace(**_trade(TradingDay=helper.TODAY)))
    maintenance.poll()
    status = maintenance.fact_status()
    assert status['status'] == 'INCOMPLETE' and status['unattributed'] == 1
    maintenance.pause_for_reconnect()
    assert maintenance.fact_status() == {'status': 'UNAVAILABLE', 'unattributed': None,
                                         'conflict': None, 'verified_external': None}
