"""Guarded SQLite cancel composition with a synthetic prepared provider."""

import json
from dataclasses import replace

import pytest
from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
from bt_api_base.direct_write_protocol import (
    DirectSubmitResult,
    DirectWriteErrorPolicy,
    NativeOrderIdentity,
    WriteOperation,
)
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import DirectDispatchError
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.direct_write import DirectWriteCoordinator, project_direct_strong_identity_keys
from bt_api_py.gateway_direct_dispatch import (
    compose_gateway_direct_cancel_dispatcher,
    compose_gateway_direct_dispatcher,
)
from bt_api_py.write_admission import WriteAdmission
from tests.integration.test_iter63_ctp_gateway_return_ingestion import ACCOUNT, DAY, _command

ROUTE = "CTP___FUTURE"


class PreparedProvider:
    execution_ids_trading_day_scoped = True
    direct_write_error_policy = DirectWriteErrorPolicy(frozenset(), frozenset())

    def __init__(self):
        self.generation = 9
        self.day = "20261108"
        self.prepared = []
        self.sent = []
        self.released = []
        self.identity_override = None
        self.on_send = lambda: None

    def session_generation(self):
        return self.generation

    def trading_day(self):
        return self.day

    def prepare_cancel_order(self, target, symbol):
        number = len(self.prepared) + 1
        prepared = PreparedCancelRequest(
            f"cancel-prepared-{number}",
            target,
            symbol,
            self.day,
            self.generation,
            {"RequestID": number, "OrderActionRef": number},
            (f"cancel-native-{self.day}-{number}",),
        )
        self.prepared.append(prepared)
        return prepared

    def release_cancel_reservation(self, prepared):
        self.released.append(prepared)

    def submit_prepared_cancel(self, prepared, grant, verifier):
        verifier.consume(
            grant,
            exchange_name=ROUTE,
            operation=WriteOperation.CANCEL,
            session_generation=self.generation,
        )
        self.sent.append(prepared)
        self.on_send()
        return DirectSubmitResult(True, self.identity_override or prepared.target_identity)


@pytest.fixture
def system(tmp_path):
    (tmp_path / "guard").mkdir()
    guard = LocalAccountSessionGuard(tmp_path / "guard", ACCOUNT, "owner")
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "direct.sqlite", ACCOUNT, generation=generation
    )
    provider = PreparedProvider()
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _: provider)
    owner = compose_gateway_direct_dispatcher(
        coordinator, ROUTE, journal, guard, lambda _: True, expected_local_provider=provider
    )
    dispatcher = compose_gateway_direct_cancel_dispatcher(
        coordinator,
        ROUTE,
        journal,
        guard,
        owner,
        lambda _: True,
        expected_local_provider=provider,
    )
    cancel = GatewayDirectCancelJournal(journal)
    original = _command(journal)
    target = NativeOrderIdentity(
        ROUTE,
        "000000000001",
        {
            "trading_day": DAY,
            "order_ref": "000000000001",
            "front_id": 7,
            "session_id": 19,
        },
        None,
    )
    yield journal, cancel, dispatcher, provider, coordinator, guard, owner, original, target
    guard.release()


def _request(system, operation="cancel-a"):
    journal, cancel, _, _, _, _, _, original, target = system
    current = journal.get("strategy-a", "key-a", "principal-a")
    assert current.status == "DISPATCHING"
    return cancel.request(
        "strategy-a",
        operation,
        "principal-a",
        "key-a",
        original.reservation_id,
        original.digest,
        target_identity=target,
        symbol="rb2701",
    )


def test_prepared_cancel_commits_before_native_and_deduplicates(system):
    journal, cancel, dispatcher, provider, _, _, _, original, target = system
    row = _request(system)
    provider.on_send = lambda: (
        pytest.fail("commit marker missing")
        if cancel.get("strategy-a", "cancel-a", "principal-a").status != "DISPATCHING"
        else None
    )
    assert provider.sent == [] and provider.prepared == []
    result = dispatcher.dispatch(
        "strategy-a", "cancel-a", "principal-a", expected_version=row.version
    )
    assert result.status == "SENT" and len(provider.sent) == 1
    assert provider.sent[0].target_identity.to_dict() == target.to_dict()
    assert provider.sent[0].target_identity.fields["trading_day"] == DAY
    assert provider.sent[0].trading_day == "20261108"
    duplicate = dispatcher.dispatch(
        "strategy-a", "cancel-a", "principal-a", expected_version=row.version
    )
    assert duplicate == result and len(provider.sent) == 1
    assert journal.get("strategy-a", "key-a", "principal-a").status == "DISPATCHING"


def test_prepared_commit_failure_does_not_send_and_releases(system, monkeypatch):
    _, _, dispatcher, provider, _, _, _, _, _ = system
    row = _request(system)
    original = GatewayDirectCancelJournal.begin_dispatch

    def reject(self, *args, **kwargs):
        raise RuntimeError("synthetic precommit fault")

    monkeypatch.setattr(GatewayDirectCancelJournal, "begin_dispatch", reject)
    with pytest.raises(DirectDispatchError, match="PRE_DISPATCH_FAILED"):
        dispatcher.dispatch("strategy-a", "cancel-a", "principal-a", expected_version=row.version)
    assert provider.sent == [] and len(provider.released) == 1
    monkeypatch.setattr(GatewayDirectCancelJournal, "begin_dispatch", original)


def test_wrong_result_identity_is_unknown_and_preserves_original(system):
    journal, _, dispatcher, provider, _, _, _, _, target = system
    row = _request(system)
    provider.identity_override = NativeOrderIdentity(ROUTE, "other", dict(target.fields), None)
    result = dispatcher.dispatch(
        "strategy-a", "cancel-a", "principal-a", expected_version=row.version
    )
    assert result.status == "UNKNOWN" and len(provider.sent) == 1
    assert journal.get("strategy-a", "key-a", "principal-a").status == "DISPATCHING"


def test_lost_commit_reply_cannot_resend_prepared_attempt(system, monkeypatch):
    _, cancel, dispatcher, provider, _, _, _, _, _ = system
    row = _request(system)
    begin = GatewayDirectCancelJournal.begin_dispatch

    def lost_reply(self, *args, **kwargs):
        begin(self, *args, **kwargs)
        raise RuntimeError("synthetic lost commit reply")

    monkeypatch.setattr(GatewayDirectCancelJournal, "begin_dispatch", lost_reply)
    result = dispatcher.dispatch(
        "strategy-a", "cancel-a", "principal-a", expected_version=row.version
    )
    assert result.status == "UNKNOWN"
    assert provider.sent == [] and len(provider.released) == 1
    durable = cancel.get("strategy-a", "cancel-a", "principal-a")
    assert durable.status in {"DISPATCHING", "UNKNOWN"}
    monkeypatch.setattr(GatewayDirectCancelJournal, "begin_dispatch", begin)
    dispatcher.dispatch("strategy-a", "cancel-a", "principal-a", expected_version=row.version)
    assert provider.sent == [] and len(provider.prepared) == 1


def test_wrong_marker_after_native_is_unknown(system, monkeypatch):
    _, _, dispatcher, provider, coordinator, _, _, _, _ = system
    row = _request(system)
    actual = coordinator.cancel_prepared

    def wrong_marker(*args, **kwargs):
        return replace(actual(*args, **kwargs), marker_id="wrong-marker")

    monkeypatch.setattr(coordinator, "cancel_prepared", wrong_marker)
    result = dispatcher.dispatch(
        "strategy-a", "cancel-a", "principal-a", expected_version=row.version
    )
    assert result.status == "UNKNOWN" and len(provider.sent) == 1


def test_finish_failure_retains_marker_and_never_resends(system, monkeypatch):
    journal, cancel, dispatcher, provider, _, _, _, _, _ = system
    row = _request(system)
    finish = GatewayDirectCancelJournal.finish

    def reject(*args, **kwargs):
        raise RuntimeError("synthetic finish fault")

    monkeypatch.setattr(GatewayDirectCancelJournal, "finish", reject)
    with pytest.raises(DirectDispatchError, match="FINISH_FAILED"):
        dispatcher.dispatch("strategy-a", "cancel-a", "principal-a", expected_version=row.version)
    assert len(provider.sent) == 1
    assert cancel.get("strategy-a", "cancel-a", "principal-a").status == "DISPATCHING"
    assert journal.get("strategy-a", "key-a", "principal-a").status == "DISPATCHING"
    monkeypatch.setattr(GatewayDirectCancelJournal, "finish", finish)
    dispatcher.dispatch("strategy-a", "cancel-a", "principal-a", expected_version=row.version)
    assert len(provider.sent) == 1


def test_cancel_unknown_does_not_prevent_original_trade_projection(system):
    from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger

    from bt_api_py.ctp_gateway_return_ingestion import CtpGatewayReturnIngestor
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import (
        ACCOUNT_KEY,
        INSTRUMENT,
        _order,
        _reader,
        _trade,
    )

    journal, _, dispatcher, provider, _, guard, _, _, _ = system
    row = _request(system)

    def failure():
        raise OSError("synthetic native reply lost")

    provider.on_send = failure
    result = dispatcher.dispatch(
        "strategy-a", "cancel-a", "principal-a", expected_version=row.version
    )
    assert result.status == "UNKNOWN" and len(provider.sent) == 1
    reader, _, native = _reader()
    ledger = GatewayDirectExecutionLedger(journal, guard)
    ingestor = CtpGatewayReturnIngestor(
        reader, ledger, account_key=ACCOUNT_KEY, instruments=(INSTRUMENT,)
    )
    native.rows["orders"] = [_order()]
    ingestor.read_and_ingest("orders", timeout=1)
    native.rows["trades"] = [_trade()]
    ingestor.read_and_ingest("trades", timeout=1)
    assert ledger.position("strategy-a", INSTRUMENT) == "1"
    assert journal.get("strategy-a", "key-a", "principal-a").status == "DISPATCHING"


def test_denied_cancel_never_prepares_or_sends(system):
    journal, _, _, provider, coordinator, guard, owner, _, _ = system
    dispatcher = compose_gateway_direct_cancel_dispatcher(
        coordinator,
        ROUTE,
        journal,
        guard,
        owner,
        lambda _: False,
        expected_local_provider=provider,
    )
    row = _request(system)
    with pytest.raises(DirectDispatchError, match="ADMISSION_DENIED"):
        dispatcher.dispatch("strategy-a", "cancel-a", "principal-a", expected_version=row.version)
    assert provider.prepared == provider.sent == []


def test_missing_prepared_provider_never_falls_back_to_legacy_cancel(system):
    _, cancel, dispatcher, provider, _, _, _, _, _ = system
    row = _request(system)
    legacy = []
    provider.prepare_cancel_order = None
    provider.cancel_order = lambda *args: legacy.append(args)
    with pytest.raises(DirectDispatchError, match="PRE_DISPATCH_FAILED"):
        dispatcher.dispatch("strategy-a", "cancel-a", "principal-a", expected_version=row.version)
    assert legacy == [] and provider.sent == []
    assert cancel.get("strategy-a", "cancel-a", "principal-a").status == "REQUESTED"


@pytest.mark.parametrize("change", ["generation", "admission"])
def test_after_commit_changed_authority_does_not_send(system, monkeypatch, change):
    _, _, dispatcher, provider, coordinator, _, _, _, _ = system
    row = _request(system)
    begin = GatewayDirectCancelJournal.begin_dispatch

    def changed(self, *args, **kwargs):
        result = begin(self, *args, **kwargs)
        if change == "generation":
            provider.generation += 1
        else:
            from bt_api_base.direct_write_protocol import WriteAdmissionDenied

            def deny(*_args, **_kwargs):
                raise WriteAdmissionDenied("write_admission_disabled", ROUTE)

            monkeypatch.setattr(coordinator.admission, "issue", deny)
        return result

    monkeypatch.setattr(GatewayDirectCancelJournal, "begin_dispatch", changed)
    result = dispatcher.dispatch(
        "strategy-a", "cancel-a", "principal-a", expected_version=row.version
    )
    assert result.status == "NOT_SENT" and len(provider.released) == 1
    assert provider.sent == []


def test_padded_native_target_is_preserved_and_two_attempts_remain_distinct(system):
    journal, cancel, dispatcher, provider, _, _, _, _, _ = system
    target = NativeOrderIdentity(
        ROUTE,
        "000000000002",
        {
            "trading_day": DAY,
            "order_ref": "000000000002",
            "front_id": 7,
            "session_id": 19,
            "exchange_id": "SHFE",
            "order_sys_id": "sys-2",
            "order_sys_id_raw": "  sys-2",
        },
        None,
    )
    original = journal.reserve(
        "strategy-a",
        "key-pad",
        "principal-a",
        {
            "symbol": "rb2701",
            "side": "buy",
            "offset": "open",
            "quantity": "2",
        },
    )
    wire = {
        "exchange_name": ROUTE,
        "client_order_id": target.client_order_id,
        "session_generation": target.session_generation,
        "fields_json": json.dumps(dict(target.fields), sort_keys=True, separators=(",", ":")),
    }
    keys = project_direct_strong_identity_keys(target, day_scoped_ids=True)
    journal.begin_dispatch(
        "strategy-a",
        "key-pad",
        original.reservation_id,
        original.digest,
        "principal-a",
        original.version,
        wire,
        keys,
    )
    for operation in ("cancel-pad-a", "cancel-pad-b"):
        row = cancel.request(
            "strategy-a",
            operation,
            "principal-a",
            "key-pad",
            original.reservation_id,
            original.digest,
            target_identity=target,
            symbol="rb2701",
        )
        result = dispatcher.dispatch(
            "strategy-a", operation, "principal-a", expected_version=row.version
        )
        assert result.status == "SENT"
    assert len(provider.sent) == 2
    assert provider.sent[0].request_keys != provider.sent[1].request_keys
    assert all(
        item.target_identity.fields["order_sys_id_raw"] == "  sys-2" for item in provider.sent
    )
    assert journal.get("strategy-a", "key-pad", "principal-a").status == "DISPATCHING"


def test_composition_rejects_owner_or_provider_mismatch(system, tmp_path):
    journal, _, _, provider, coordinator, guard, owner, _, _ = system
    with pytest.raises(ValueError, match="local provider"):
        compose_gateway_direct_cancel_dispatcher(
            coordinator,
            ROUTE,
            journal,
            guard,
            owner,
            lambda _: True,
            expected_local_provider=object(),
        )
    other = GatewayDirectCommandJournal.create(
        tmp_path / "other.sqlite", ACCOUNT, generation=guard.acquire()
    )
    with pytest.raises(ValueError, match="owner dispatcher binding"):
        compose_gateway_direct_cancel_dispatcher(
            coordinator,
            ROUTE,
            other,
            guard,
            owner,
            lambda _: True,
            expected_local_provider=provider,
        )


@pytest.mark.parametrize("mode", ["success", "native_error", "revoked"])
def test_real_ctp_python_prepared_cancel_over_guarded_gateway_journal(tmp_path, monkeypatch, mode):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    from bt_api_base.account_protocol import AccountKey
    from bt_api_ctp.ctp import client as ctp_client
    from bt_api_ctp.ctp import ctp_structs_order
    from bt_api_ctp.direct_write_provider import CtpDirectWriteProvider
    from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture

    # Use an explicit unique module name; another installed test suite may use
    # the same basename. The helper builds a Trader with a synthetic native API.
    helper_path = (
        Path(__file__).resolve().parents[2]
        / "bt_api/bt_api_ctp/tests/test_iter63_prepared_cancel.py"
    )
    spec = importlib.util.spec_from_file_location("r15_ctp_prepared_cancel_helper", helper_path)
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    native_field_type = ctp_structs_order.CThostFtdcInputOrderActionField
    assert native_field_type is not SimpleNamespace
    # Keep the helper's synthetic Trader setup, but use a short account label
    # that fits CTP's fixed-width InvestorID and UserID fields.
    td, md, broker, user = (
        "tcp://182.254.243.31:30002",
        "tcp://182.254.243.31:30012",
        "3070",
        "synth001",
    )
    feed = CtpRequestDataFuture(
        broker_id=broker,
        user_id=user,
        password="unused",
        td_front=td,
        md_front=md,
        auto_settlement_confirm=False,
    )
    native = helper.Native()
    trader = ctp_client.TraderClient(td, broker, user, "unused")
    trader._api = native
    trader._session_native_api = native
    trader._session_native_front = td
    trader._connected = True
    trader._authentication_state = "authenticated"
    trader._login_state = "logging_in"
    trader._connection_generation = 7
    trader._login_request_id = 1
    trader._login_connection_generation = 7
    spi = ctp_client._TraderSpi(trader, native_api=native)
    trader._spi = spi
    spi.OnRspUserLogin(
        SimpleNamespace(
            BrokerID=broker,
            UserID=user,
            FrontID=1,
            SessionID=2,
            TradingDay="20261009",
            MaxOrderRef="7",
        ),
        SimpleNamespace(ErrorID=0),
        1,
        True,
    )
    trader._settlement_state = "confirmed"
    trader._settlement_readback_verified = True
    trader._settlement_account_fingerprint = trader._account_fingerprint
    trader._settlement_trading_day = trader._trading_day
    trader._settlement_connection_generation = 7
    trader._ready = True
    trader._has_current_settlement_readback_locked = lambda: True
    capability, _ = trader.arm_execution_for_registered_sim(
        instrument_id="SA2701",
        exchange_id="CZCE",
        md_front=md,
        strategy_identity_sha256="b" * 64,
        execution_cycle_id="test-cycle",
        preflight_sha256="c" * 64,
    )
    feed._trader = trader
    provider = CtpDirectWriteProvider(
        feed, background_settlement_readback=False, allow_implicit_connect=False
    )
    monkeypatch.setattr(provider, "_admitted_capability", lambda _: capability)
    monkeypatch.setattr(provider, "_ensure_armed", lambda *_: None)

    key = AccountKey("ctp", "simnow", "simnow", user, broker)
    account_scope = key.account_key_id
    (tmp_path / "guard").mkdir()
    guard = LocalAccountSessionGuard(tmp_path / "guard", account_scope, "ctp-synthetic-owner")
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "real-ctp.sqlite", account_scope, generation=generation
    )
    admission = WriteAdmission({ROUTE: True})
    consumes = []
    consume = admission.consume

    def track_final_consume(*args, **kwargs):
        consumes.append(kwargs["operation"])
        return consume(*args, **kwargs)

    monkeypatch.setattr(admission, "consume", track_final_consume)
    coordinator = DirectWriteCoordinator(admission, lambda _: provider)
    owner = compose_gateway_direct_dispatcher(
        coordinator, ROUTE, journal, guard, lambda _: True, expected_local_provider=provider
    )
    dispatcher = compose_gateway_direct_cancel_dispatcher(
        coordinator, ROUTE, journal, guard, owner, lambda _: True, expected_local_provider=provider
    )
    cancel = GatewayDirectCancelJournal(journal)
    target = helper.target()
    original = journal.reserve(
        "strategy-a",
        "real-ctp-order",
        "principal-a",
        {"symbol": "SA2701", "side": "buy", "offset": "open", "quantity": "1"},
    )
    wire = {
        "exchange_name": ROUTE,
        "client_order_id": target.client_order_id,
        "session_generation": target.session_generation,
        "fields_json": json.dumps(dict(target.fields), sort_keys=True, separators=(",", ":")),
    }
    journal.begin_dispatch(
        "strategy-a",
        "real-ctp-order",
        original.reservation_id,
        original.digest,
        "principal-a",
        original.version,
        wire,
        project_direct_strong_identity_keys(target, day_scoped_ids=True),
    )
    attempt = cancel.request(
        "strategy-a",
        "real-ctp-cancel",
        "principal-a",
        "real-ctp-order",
        original.reservation_id,
        original.digest,
        target_identity=target,
        symbol="SA2701",
    )
    observed = []

    def fake_native_action(field, request_id):
        current = cancel.get("strategy-a", "real-ctp-cancel", "principal-a")
        assert current.status == "DISPATCHING" and current.marker_id == current.attempt_id
        assert type(field) is native_field_type
        assert request_id == field.RequestID == field.OrderActionRef
        assert current.prepared_cancel.request_fields["RequestID"] == request_id
        observed.append(request_id)
        if mode == "native_error":
            raise RuntimeError("synthetic native call failure")
        return 0

    native.ReqOrderAction = fake_native_action
    if mode == "revoked":
        issue = admission.issue

        def issue_then_revoke(*args, **kwargs):
            grant = issue(*args, **kwargs)
            admission.revoke_grant(grant)
            return grant

        monkeypatch.setattr(admission, "issue", issue_then_revoke)
    try:
        result = dispatcher.dispatch(
            "strategy-a", "real-ctp-cancel", "principal-a", expected_version=attempt.version
        )
        assert (
            result.status
            == {"success": "SENT", "native_error": "UNKNOWN", "revoked": "NOT_SENT"}[mode]
        )
        assert consumes == [WriteOperation.CANCEL]
        assert len(observed) == (0 if mode == "revoked" else 1)
        duplicate = dispatcher.dispatch(
            "strategy-a", "real-ctp-cancel", "principal-a", expected_version=attempt.version
        )
        assert duplicate == result and len(observed) == (0 if mode == "revoked" else 1)
        assert journal.get("strategy-a", "real-ctp-order", "principal-a").status == "DISPATCHING"
    finally:
        guard.release()
