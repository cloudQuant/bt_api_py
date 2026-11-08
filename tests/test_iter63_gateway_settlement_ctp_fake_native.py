"""Real CTP TraderClient and provider + synthetic native + Gateway SQLite.

All fronts and account values are test literals; no network or external account.
"""

from types import SimpleNamespace

import pytest
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.gateway_settlement import GatewaySettlementController
from bt_api_py.write_admission import WriteAdmission

ROUTE = "CTP___FUTURE"


@pytest.mark.parametrize("lost_reply", [False, True])
def test_real_trader_client_gateway_marker_and_restart_readback(tmp_path, monkeypatch, lost_reply):
    import importlib
    import sqlite3
    from pathlib import Path

    import bt_api_ctp.direct_write_provider as provider_module

    ctp_tests = Path(__file__).resolve().parents[1] / "bt_api" / "bt_api_ctp" / "tests"
    monkeypatch.syspath_prepend(str(ctp_tests))
    helper = importlib.import_module("test_iter62_settlement_readback_on_write")
    monkeypatch.setattr(provider_module, "_READBACK_MONITOR_INTERVAL", 3600.0)
    provider, trader, native = helper._real_setup()
    helper._login(trader, native, session_id=20)
    native.confirmation_day = "20261105"
    native.calls.clear()
    account_binding = trader.get_session_state()["account_fingerprint"]
    guard_dir = tmp_path / "guards"
    guard_dir.mkdir()
    guard = LocalAccountSessionGuard(guard_dir, "test-account", "test-owner")
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", "test-account", generation=generation
    )
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _: provider)
    coordinator.provider(ROUTE)
    dispatcher = GatewayDirectDispatcher(
        journal, ROUTE, guard, lambda *_: None, lambda _: ("unused",), lambda _: True
    )
    controller = GatewaySettlementController(
        coordinator,
        ROUTE,
        expected_local_provider=provider,
        expected_account_binding=account_binding,
        journal=journal,
        dispatcher=dispatcher,
    )
    seen = []
    original = provider.confirm_settlement

    def checked_confirm(grant, verifier, *, observation):
        row = journal.settlement_get(ROUTE, helper.TODAY)
        binding = grant.settlement_binding
        assert row.status == "ATTEMPTING" and row.marker_id == binding.marker_id
        assert row.account_binding == binding.observation.account_binding == account_binding
        assert row.trading_day == binding.observation.trading_day == helper.TODAY
        assert row.initial_session_generation == observation.session_generation
        assert (
            row.initial_observation_id
            == binding.observation.observation_id
            == observation.observation_id
        )
        seen.append((row.marker_id, observation.observation_id))
        return original(grant, verifier, observation=observation)

    monkeypatch.setattr(provider, "confirm_settlement", checked_confirm)

    def fake_confirm(_field, request_id):
        native.calls.append("confirm_settlement")
        if lost_reply:
            return 1  # native outcome is unclear; subsequent readback is still unconfirmed
        native.confirmation_day = helper.TODAY
        trader._spi.OnRspSettlementInfoConfirm(
            SimpleNamespace(BrokerID="9999", InvestorID="account", ConfirmDate=helper.TODAY),
            SimpleNamespace(ErrorID=0, ErrorMsg=""),
            request_id,
            True,
        )
        return 0

    native.ReqSettlementInfoConfirm = fake_confirm
    guard2 = None
    try:
        first = controller.tick()
        assert len(seen) == 1 and native.calls.count("confirm_settlement") == 1
        row = journal.settlement_get(ROUTE, helper.TODAY)
        assert row.marker_id == seen[0][0] and row.initial_observation_id == seen[0][1]
        if lost_reply:
            assert not first.observed_ready and row.status == "UNKNOWN"
        else:
            assert first.observed_ready and row.status == "CONFIRMED"
        guard.release()
        # Recreate the owner with the same durable journal and a new native session.
        native.confirmation_day = helper.TODAY
        helper._login(trader, native, session_id=21)
        guard2 = LocalAccountSessionGuard(guard_dir, "test-account", "test-owner-2")
        generation2 = guard2.acquire()
        with sqlite3.connect(tmp_path / "journal.sqlite") as db:
            journal_id, revision = db.execute(
                "SELECT journal_id,revision FROM direct_journal_meta"
            ).fetchone()
        reopened = GatewayDirectCommandJournal(
            tmp_path / "journal.sqlite",
            "test-account",
            generation=generation2,
            expected_journal_id=journal_id,
            minimum_revision=revision,
        )
        dispatcher2 = GatewayDirectDispatcher(
            reopened, ROUTE, guard2, lambda *_: None, lambda _: ("unused",), lambda _: True
        )
        controller2 = GatewaySettlementController(
            coordinator,
            ROUTE,
            expected_local_provider=provider,
            expected_account_binding=account_binding,
            journal=reopened,
            dispatcher=dispatcher2,
        )
        assert controller2.tick().observed_ready and controller2.ready
        assert native.calls.count("confirm_settlement") == 1
        restored = reopened.settlement_get(ROUTE, helper.TODAY)
        assert restored.status == "CONFIRMED" and restored.marker_id == seen[0][0]
        assert restored.initial_observation_id == seen[0][1]
    finally:
        if guard2 is not None:
            guard2.release()
        else:
            guard.release()
        provider.close()
