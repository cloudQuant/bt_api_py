"""Offline instrument rules through a spawned CTP profile and CURVE Registry.

TD callbacks are synthetic; neither a real account nor a native connection is
used. These observations do not establish trading readiness or venue truth.
"""

from __future__ import annotations

import multiprocessing
import os
import queue
import sys
import threading
from decimal import Decimal
from pathlib import Path

import pytest
import zmq
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

from bt_api_py._contracts.models import OrderRequest
from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_strategy_account import GatewayAccountClientSettings
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.gateway_strategy_instrument import (
    GatewayInstrumentClientSettings,
    INSTRUMENT_OPERATIONS,
)
from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime
from tests.integration import test_iter63_ctp_gateway_joint_service as joint
from tests.integration.test_iter63_gateway_direct_service import (
    CLIENT_A,
    CLIENT_B,
    DOMAIN,
    OPS,
    SERVICE,
    _client_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_direct_client import MemoryHost
from tests.unit.test_iter63_gateway_direct_service import _order

KEY = MarketInstrumentKey("ctp", "CFFEX", "simnow", "future", "IF2612", "IF2612")
OTHER_KEY = MarketInstrumentKey("ctp", "CFFEX", "simnow", "future", "IH2612", "IH2612")


def _server(
    root,
    rpc,
    market,
    server_keys,
    client_keys,
    journal_id,
    issuer_id,
    events,
    controls,
    native_events,
    terminal,
    requests,
    reconnect,
    reconnected,
    exercise_relogin,
):
    """Patch only a test-native boundary inside this newly spawned process."""
    test_dir = Path(joint.__file__).resolve().parents[2] / "bt_api" / "bt_api_ctp" / "tests"
    sys.path.insert(0, str(test_dir))
    from test_iter62_settlement_readback_on_write import _NativeApi
    from bt_api_ctp.ctp.ctp_structs_common import (
        CThostFtdcRspInfoField,
        CThostFtdcRspUserLoginField,
    )
    from bt_api_ctp.ctp.ctp_structs_market import CThostFtdcInstrumentField

    joint.ACCOUNT_OPS = joint.ACCOUNT_OPS | INSTRUMENT_OPERATIONS

    def authenticate(self, field, request_id):
        info = CThostFtdcRspInfoField()
        info.ErrorID = 0
        info.ErrorMsg = ""
        self.client._spi.OnRspAuthenticate(None, info, request_id, True)
        return 0

    def login(self, field, request_id):
        info = CThostFtdcRspInfoField()
        info.ErrorID = 0
        info.ErrorMsg = ""
        response = CThostFtdcRspUserLoginField()
        response.BrokerID = "9999"
        response.UserID = "account"
        response.TradingDay = "20261106"
        response.FrontID = 7
        response.SessionID = 20
        response.MaxOrderRef = "0"
        self.client._spi.OnRspUserLogin(response, info, request_id, True)
        return 0

    if exercise_relogin:
        _NativeApi.ReqAuthenticate = authenticate
        _NativeApi.ReqUserLogin = login

    def request(self, field, request_id):
        with requests.get_lock():
            requests.value += 1
            count = requests.value
        native_events.put(("query", count, field.InstrumentID, field.ExchangeID))
        if exercise_relogin and count == 1:

            def relogin():
                if reconnect.wait(8):
                    spi = self.client._spi
                    spi.OnFrontDisconnected(0x1001)
                    spi.OnFrontConnected()
                    reconnected.set()

            threading.Thread(target=relogin, name="synthetic-trader-relogin", daemon=True).start()
        row = {
            "InstrumentID": field.InstrumentID,
            "ExchangeID": field.ExchangeID,
            "ProductID": "IF" if field.InstrumentID == "IF2612" else "IH",
            "ProductClass": "1",
            "VolumeMultiple": 300,
            "PriceTick": 0.2,
            "MinLimitOrderVolume": 3,
            "MaxLimitOrderVolume": 100,
            "IsTrading": 1,
        }
        native_row = CThostFtdcInstrumentField()
        for name, value in row.items():
            setattr(native_row, name, value)
        response = CThostFtdcRspInfoField()
        response.ErrorID = 0
        response.ErrorMsg = ""
        # Retain the exact SPI source for the in-flight callback; a relogin
        # must not silently retag an older vendor request.
        spi = self.client._spi
        spi.OnRspQryInstrument(native_row, response, request_id, False)

        def finish():
            if terminal.wait(8):
                spi.OnRspQryInstrument(None, response, request_id, True)
                native_events.put(("terminal", count))

        threading.Thread(target=finish, name="synthetic-query-terminal", daemon=True).start()
        return 0

    _NativeApi.ReqQryInstrument = request
    joint._gateway(
        root, rpc, market, server_keys, client_keys, journal_id, issuer_id, events, controls
    )


@pytest.fixture
def gateway(tmp_path, request):
    (tmp_path / "guard").mkdir()
    (tmp_path / "flow").mkdir(mode=0o700)
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "orders.sqlite", joint.SCOPE, generation="provisioned"
    )
    issuer = DurableOrderCorrelationIssuer(tmp_path / "identity.sqlite")
    issuer_id = issuer.issuer_id
    issuer.close()
    rpc, market = _free_endpoint(), _free_endpoint()
    server_keys, client_keys = zmq.curve_keypair(), tuple(zmq.curve_keypair() for _ in range(3))
    ctx = multiprocessing.get_context("spawn")
    terminal, requests = ctx.Event(), ctx.Value("i", 0)
    reconnect, reconnected = ctx.Event(), ctx.Event()
    exercise_relogin = getattr(request, "param", False)
    events, controls, native_events = ctx.Queue(), ctx.Queue(), ctx.Queue()
    child = ctx.Process(
        target=_server,
        args=(
            str(tmp_path),
            rpc,
            market,
            server_keys,
            client_keys,
            journal.journal_id,
            issuer_id,
            events,
            controls,
            native_events,
            terminal,
            requests,
            reconnect,
            reconnected,
            exercise_relogin,
        ),
    )
    child.start()
    sessions = []
    try:
        ready = events.get(timeout=12)
        assert ready == ("ready", child.pid) and child.pid != os.getpid(), ready
        yield dict(
            root=tmp_path,
            rpc=rpc,
            market=market,
            server_keys=server_keys,
            client_keys=client_keys,
            journal_id=journal.journal_id,
            events=events,
            controls=controls,
            native_events=native_events,
            terminal=terminal,
            requests=requests,
            reconnect=reconnect,
            reconnected=reconnected,
            sessions=sessions,
            child=child,
        )
    finally:
        terminal.set()
        errors = []
        for session in reversed(sessions):
            try:
                closed = session.close(timeout_seconds=8)
                if closed.state != "closed" or closed.thread_alive:
                    errors.append((closed.state, closed.thread_alive))
            except Exception as error:
                errors.append((type(error).__name__, str(error)))
        controls.put("stop")
        child.join(12)
        if child.is_alive():
            child.terminate()
            child.join(3)
            errors.append("synthetic child did not close")
        remaining = []
        while True:
            try:
                remaining.append(events.get_nowait())
            except queue.Empty:
                break
        assert child.exitcode == 0, (child.exitcode, remaining)
        assert ("closed", 1) in remaining, remaining
        assert not any(item[0] in {"error", "cleanup_error"} for item in remaining), remaining
        assert not errors, errors


def _session(gateway, identity):
    index = (CLIENT_A, CLIENT_B).index(identity)
    raw = _client_config(gateway["rpc"], identity).to_public_dict()
    settings = raw["clients"]["gateway"]["settings"]
    settings["bindings"][0]["account_ref"] = joint.SCOPE
    settings["bindings"].append(dict(settings["bindings"][0], mount="store.account"))
    settings["options"]["heartbeat_interval_seconds"] = 1
    scope = WireScope(
        DOMAIN,
        account_ref=joint.SCOPE,
        strategy_ref=identity.actor_ref,
        run_id="instrument-" + identity.actor_ref,
    )
    session = GatewayStrategyClientSession(
        parse_client_config(raw),
        binding=GatewayCommandBinding(
            gateway["journal_id"], joint.SCOPE, identity.actor_ref, identity.principal_ref
        ),
        route=GatewayClientRoute(
            "broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", scope, identity
        ),
        peer=SERVICE,
        security_profiles={
            "security-a": ClientCurveKeys(gateway["server_keys"][0], *gateway["client_keys"][index])
        },
        host=MemoryHost(),
        requested_operations=OPS,
        account_settings=GatewayAccountClientSettings(
            joint.ACCOUNT, (joint.RESOURCE,), frozenset({"account.snapshot.typed"})
        ),
        instrument_settings=GatewayInstrumentClientSettings(
            (KEY, OTHER_KEY), INSTRUMENT_OPERATIONS
        ),
    )
    gateway["sessions"].append(session)
    session.start(timeout_seconds=8)
    return session


def _stats(gateway):
    gateway["controls"].put("stats")
    value = gateway["events"].get(timeout=4)
    assert value[0] == "stats", value
    return value


def test_real_shared_td_instrument_spec_yields_while_terminal_pending(gateway, monkeypatch):
    renewals, calls = [], []
    renewed = threading.Event()
    in_query = threading.Event()
    lock = threading.Lock()
    original_maintain, original_call = (
        ServiceClientRuntime._maintain_bindings,
        ServiceClientRuntime.call,
    )

    def maintain(self, *args, **kwargs):
        before = {item.registration_id: item.lease_deadline for item in self.bindings}
        result = original_maintain(self, *args, **kwargs)
        if in_query.is_set() and not gateway["terminal"].is_set():
            for item in self.bindings:
                previous = before.get(item.registration_id)
                if (
                    item.mount == "store.account"
                    and previous is not None
                    and item.lease_deadline > previous
                ):
                    with lock:
                        renewals.append((item.registration_id, previous, item.lease_deadline))
                    renewed.set()
        return result

    def call(self, mount, operation, arguments, **kwargs):
        if operation.startswith("account.instrument_spec."):
            with lock:
                calls.append((operation, arguments["operation_id"], threading.get_ident()))
        return original_call(self, mount, operation, arguments, **kwargs)

    monkeypatch.setattr(ServiceClientRuntime, "_maintain_bindings", maintain)
    monkeypatch.setattr(ServiceClientRuntime, "call", call)
    session = _session(gateway, CLIENT_A)
    baseline = joint._until(
        lambda: (
            result
            if (
                result := session.command_client.reserve(
                    OrderRequest.from_dict(
                        _order(key="spec-status", symbol="IF2612", account=joint.SCOPE)
                    ),
                    timeout_seconds=3,
                )
            )
            and result.outcome == "OBSERVED"
            else None
        ),
        timeout=5,
    )
    assert baseline.record.state == "RESERVED"
    registration = next(
        item.registration_id for item in session.status().bindings if item.mount == "store.account"
    )
    replies = queue.Queue()

    def lookup():
        try:
            replies.put(
                ("ok", session.get_instrument_spec("CTP___FUTURE", "IF2612", timeout_seconds=5))
            )
        except Exception as error:
            replies.put(("error", error))

    thread = threading.Thread(target=lookup, name="instrument-lookup")
    thread.start()
    try:
        assert gateway["native_events"].get(timeout=5) == ("query", 1, "IF2612", "CFFEX")
        in_query.set()
        assert thread.is_alive() and not gateway["terminal"].is_set()
        result = session.command_client.status("spec-status", timeout_seconds=0.7)
        assert result.outcome == "OBSERVED" and result.record.state == "RESERVED"
        assert renewed.wait(2.5), "same account registration must renew during native query"
        assert thread.is_alive() and not gateway["terminal"].is_set()
        with lock:
            assert any(ref == registration and after > before for ref, before, after in renewals), (
                renewals
            )
            begin = [op_id for op, op_id, _ in calls if op.endswith(".begin")]
            assert len(begin) == 1 and {op_id for _, op_id, _ in calls} == set(begin)
        assert _stats(gateway)[:5] == ("stats", 1, 1, 0, 0)
        gateway["terminal"].set()
        thread.join(6)
        assert not thread.is_alive()
        outcome, value = replies.get(timeout=1)
        assert outcome == "ok", (outcome, value)
        assert value.exchange_name == "CTP___FUTURE" and value.symbol == "IF2612"
        assert value.min_quantity == Decimal("3") and value.max_quantity == Decimal("100")
        assert value.quantity_step == Decimal("1") and value.quantity_unit == "lots"
        assert value.price_tick == Decimal("0.2") and value.contract_value == Decimal("300")
        assert value.freshness.source == "live" and not value.freshness.stale
        assert value.freshness.observed_at.utcoffset().total_seconds() == 0
        assert value.raw == {} and value.source == "ctp_gateway_instrument_query"
        assert gateway["native_events"].get(timeout=3) == ("terminal", 1)
        assert gateway["requests"].value == 1
        assert (
            next(
                item.registration_id
                for item in session.status().bindings
                if item.mount == "store.account"
            )
            == registration
        )
        assert _stats(gateway)[:5] == ("stats", 1, 1, 0, 0)
    finally:
        gateway["terminal"].set()
        thread.join(6)
        assert not thread.is_alive()


def test_job_id_is_idempotent_and_scoped_to_its_registration(gateway):
    a, b = _session(gateway, CLIENT_A), _session(gateway, CLIENT_B)
    operation_id = "b" * 32
    args = {"operation_id": operation_id, "instrument": KEY.to_dict()}
    first = a.runtime.call(
        "store.account", "account.instrument_spec.begin", args, timeout_seconds=3
    )
    assert first["operation_id"] == operation_id and first["status"] == "pending"
    assert gateway["native_events"].get(timeout=5) == ("query", 1, "IF2612", "CFFEX")
    again = a.runtime.call(
        "store.account", "account.instrument_spec.begin", args, timeout_seconds=3
    )
    assert again["operation_id"] == operation_id and again["status"] == "pending"
    different_scope = b.runtime.call(
        "store.account",
        "account.instrument_spec.status",
        {"operation_id": operation_id},
        timeout_seconds=3,
    )
    assert (
        different_scope["operation_id"] == operation_id and different_scope["status"] == "not_found"
    )
    assert gateway["requests"].value == 1
    gateway["terminal"].set()
    assert gateway["native_events"].get(timeout=3) == ("terminal", 1)

    def ready():
        result = a.runtime.call(
            "store.account",
            "account.instrument_spec.status",
            {"operation_id": operation_id},
            timeout_seconds=2,
        )
        assert result["status"] in {"pending", "ready"}, result
        return result if result["status"] == "ready" else None

    final = joint._until(ready, timeout=5)
    assert final["operation_id"] == operation_id
    assert final["snapshot"]["spec"]["spec"]["min_quantity"] == "3"
    assert gateway["requests"].value == 1 and _stats(gateway)[:5] == ("stats", 1, 1, 0, 0)


@pytest.mark.parametrize("gateway", [True], indirect=True)
def test_old_query_source_rejected_after_relogin_and_new_job_uses_new_generation(gateway):
    session = _session(gateway, CLIENT_A)
    runtime = session.runtime
    original_id, next_id = "c" * 32, "d" * 32

    def begin(operation_id):
        response = runtime.call(
            "store.account",
            "account.instrument_spec.begin",
            {"operation_id": operation_id, "instrument": KEY.to_dict()},
            timeout_seconds=3,
        )
        assert response["operation_id"] == operation_id and response["status"] == "pending"

    def ready(operation_id):
        response = runtime.call(
            "store.account",
            "account.instrument_spec.status",
            {"operation_id": operation_id},
            timeout_seconds=2,
        )
        assert response["operation_id"] == operation_id
        assert response["status"] in {"pending", "ready"}, response
        return response if response["status"] == "ready" else None

    begin(original_id)
    assert gateway["native_events"].get(timeout=5) == ("query", 1, "IF2612", "CFFEX")
    gateway["terminal"].set()
    assert gateway["native_events"].get(timeout=3) == ("terminal", 1)
    original = joint._until(lambda: ready(original_id), timeout=5)
    old_generation = original["snapshot"]["connection_generation"]
    assert old_generation == 1
    gateway["reconnect"].set()
    assert gateway["reconnected"].wait(5)

    def old_rejected():
        response = runtime.call(
            "store.account",
            "account.instrument_spec.status",
            {"operation_id": original_id},
            timeout_seconds=2,
        )
        assert response["operation_id"] == original_id
        assert response["status"] != "ready", "old source was served after Trader relogin"
        assert response["status"] in {"pending", "failed"}, response
        return response if response["status"] == "failed" else None

    try:
        failure = joint._until(old_rejected, timeout=5)
    except Exception:
        print("SYNTHETIC_RELOGIN_RUNTIME", runtime.status(), flush=True)
        try:
            print("SYNTHETIC_RELOGIN_HOST", _stats(gateway), flush=True)
        except Exception as exc:
            print("SYNTHETIC_RELOGIN_HOST_ERROR", type(exc).__name__, flush=True)
        raise
    assert failure["snapshot"] is None and failure["code"]
    begin(next_id)
    assert gateway["native_events"].get(timeout=5) == ("query", 2, "IF2612", "CFFEX")
    assert gateway["native_events"].get(timeout=3) == ("terminal", 2)
    current = joint._until(lambda: ready(next_id), timeout=5)
    assert current["snapshot"]["connection_generation"] > old_generation
    assert current["snapshot"]["spec"]["spec"]["min_quantity"] == "3"
    assert gateway["requests"].value == 2 and _stats(gateway)[:5] == ("stats", 1, 1, 0, 0)
