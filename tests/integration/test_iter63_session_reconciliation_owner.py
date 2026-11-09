"""Iteration 63 E095: the trading owner reports an abandoned session record by its code.

Before this, a restart after an unclean exit failed with the generic
RESOURCE_CONSTRUCTION_FAILED, so the operator could not tell that
``bt-gateway-operator --reconcile-session`` was the next step.
"""

import multiprocessing
import os

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.ctp_gateway_trading import (
    CtpGatewayTradingSettings,
    reconcile_ctp_gateway_account_session,
)
from tests.integration.test_iter63_ctp_gateway_trading import _server
from tests.integration.test_service_account_instrument_metadata import _free_endpoint

pytestmark = pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX lock semantics")


def _start_owner(tmp_path, journal_id):
    ctx = multiprocessing.get_context("spawn")
    events, controls = ctx.Queue(), ctx.Queue()
    child = ctx.Process(
        target=_server,
        args=(str(tmp_path), _free_endpoint(), zmq.curve_keypair(), zmq.curve_keypair()[0],
              journal_id, events, controls),
    )
    child.start()
    return child, events, controls


def _first_outcome(events):
    while True:
        event = events.get(timeout=30)
        if event[0] in {"ready", "error"}:
            return event


def test_restart_after_unclean_exit_names_the_reconciliation_then_recovers(tmp_path):
    key = AccountKey("ctp", "simnow", "simnow", "00001", "09999")
    scope = key.account_key_id
    (tmp_path / "guard").mkdir()
    (tmp_path / "flow").mkdir(mode=0o700)
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", scope, generation="provisioned"
    )
    crashed = LocalAccountSessionGuard(tmp_path / "guard", scope, "owner")
    crashed.acquire()
    os.close(crashed._fd)  # the old owner died holding an active record
    crashed._fd = None

    child, events, controls = _start_owner(tmp_path, journal.journal_id)
    try:
        outcome = _first_outcome(events)
        assert outcome[0] == "error" and outcome[2] == "SESSION_RECONCILIATION_REQUIRED", outcome
    finally:
        child.join(30)
        if child.is_alive():
            child.kill()

    settings = CtpGatewayTradingSettings(
        key, scope, tmp_path / "guard", "owner", tmp_path / "journal.sqlite",
        journal.journal_id, 0, tmp_path / "flow", "source", "entitlement",
    )
    result = reconcile_ctp_gateway_account_session(
        settings, tmp_path / "registry.sqlite", operator_ref="ops", reason="test crash"
    )
    assert result["status"] == "RECONCILED"

    child, events, controls = _start_owner(tmp_path, journal.journal_id)
    try:
        assert _first_outcome(events)[0] == "ready"
    finally:
        controls.put("stop")
        child.join(30)
        if child.is_alive():
            child.kill()
