"""R23 spawned Gateway over CURVE with real CTP Python and synthetic native.

No native connection, account I/O, or Backtrader Broker/Store is exercised.
"""
from __future__ import annotations

import multiprocessing
import queue
import threading

import pytest
import zmq
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

from bt_api_py._contracts.models import OrderRequest
from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.order_correlation_issuer import DurableOrderCorrelationIssuer
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime
from bt_api_py.service_runtime_thread import ThreadedRuntimeError
from tests.integration.test_iter63_ctp_gateway_joint_service import (
    CLIENT_C, DOMAIN, OPS, SERVICE, SCOPE, _gateway, _key, _session, _until,
)
from tests.integration.test_iter63_gateway_direct_service import CLIENT_A, _client_config
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_direct_client import MemoryHost
from tests.unit.test_iter63_gateway_direct_service import _order


@pytest.fixture
def async_gateway(tmp_path):
    (tmp_path / "guard").mkdir()
    (tmp_path / "flow").mkdir(mode=0o700)
    journal = GatewayDirectCommandJournal.create(tmp_path / "orders.sqlite", SCOPE, generation="provisioned")
    issuer = DurableOrderCorrelationIssuer(tmp_path / "identity.sqlite")
    issuer_id = issuer.issuer_id
    issuer.close()
    rpc, market = _free_endpoint(), _free_endpoint()
    server_keys = zmq.curve_keypair()
    client_keys = tuple(zmq.curve_keypair() for _ in range(3))
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    events, controls = context.Queue(), context.Queue()
    child = context.Process(target=_gateway, args=(str(tmp_path), rpc, market, server_keys, client_keys,
        journal.journal_id, issuer_id, events, controls, (entered, release)))
    child.start()
    sessions = []
    state = dict(root=tmp_path, rpc=rpc, market=market, server_keys=server_keys,
        client_keys=client_keys, journal_id=journal.journal_id, entered=entered, release=release,
        events=events, controls=controls, sessions=sessions, child=child)
    try:
        ready = events.get(timeout=8)
        assert ready == ("ready", child.pid), ready
        yield state
    finally:
        release.set()
        errors = []
        for session in reversed(sessions):
            try:
                closed = session.close(timeout_seconds=5)
                if closed.state != "closed" or closed.thread_alive:
                    errors.append((closed.state, closed.thread_alive))
            except Exception as exc:
                errors.append((type(exc).__name__, str(exc)))
        controls.put("stop")
        child.join(8)
        if child.is_alive():
            child.terminate()  # Only the synthetic child created by this fixture.
            child.join(2)
            errors.append(("child_required_termination",))
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


def strategy(state):
    session = _session(CLIENT_A, state["client_keys"][0], state["server_keys"][0],
        state["rpc"], state["market"], state["journal_id"], "IF2612", state["root"],
        heartbeat_interval_seconds=1)
    state["sessions"].append(session)
    session.start(timeout_seconds=6)
    return session


def direct_only(state):
    raw = _client_config(state["rpc"], CLIENT_C).to_public_dict()
    raw["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = SCOPE
    scope = WireScope(DOMAIN, account_ref=SCOPE, strategy_ref=CLIENT_C.actor_ref, run_id="async-c")
    session = GatewayStrategyClientSession(parse_client_config(raw),
        binding=GatewayCommandBinding(state["journal_id"], SCOPE, CLIENT_C.actor_ref, CLIENT_C.principal_ref),
        route=GatewayClientRoute("broker.direct_write", SERVICE.actor_ref, "gateway-a", "scope-a", scope, CLIENT_C),
        peer=SERVICE, security_profiles={"security-a": ClientCurveKeys(state["server_keys"][0], *state["client_keys"][2])},
        host=MemoryHost(), requested_operations=OPS)
    state["sessions"].append(session)
    session.start(timeout_seconds=6)
    return session


def reserve(session, key):
    payload = _order(key=key, symbol="IF2612", account=SCOPE)
    request = OrderRequest.from_dict(payload)
    return _until(lambda: (result if (result := session.command_client.reserve(request, timeout_seconds=2))
        and result.outcome == "OBSERVED" else None), timeout=5)


def stats(state):
    state["controls"].put("stats")
    result = state["events"].get(timeout=4)
    assert result[0] == "stats", result
    return result


def test_pending_native_start_keeps_host_and_strategy_owner_live(async_gateway, monkeypatch):
    trace = []
    renewals = []
    renewed = threading.Event()
    lock = threading.Lock()
    call = ServiceClientRuntime.call
    maintain = ServiceClientRuntime._maintain_bindings

    def observed_maintenance(self, *args, **kwargs):
        before = {binding.registration_id: binding.lease_deadline for binding in self.bindings}
        result = maintain(self, *args, **kwargs)
        if async_gateway["entered"].is_set() and not async_gateway["release"].is_set():
            for binding in self.bindings:
                previous = before.get(binding.registration_id)
                if (binding.mount == "store.market" and previous is not None
                    and binding.lease_deadline > previous):
                    with lock:
                        renewals.append((binding.registration_id, previous, binding.lease_deadline))
                    renewed.set()
        return result

    monkeypatch.setattr(ServiceClientRuntime, "_maintain_bindings", observed_maintenance)

    def traced(self, mount, operation, arguments, **kwargs):
        if operation in {"market.bootstrap_begin", "market.bootstrap_status", "market.bootstrap_cancel"}:
            with lock:
                trace.append((operation, arguments["operation_id"], threading.get_ident()))
        return call(self, mount, operation, arguments, **kwargs)

    monkeypatch.setattr(ServiceClientRuntime, "call", traced)
    a, c = strategy(async_gateway), direct_only(async_gateway)
    assert reserve(a, "same-owner").record.state == "RESERVED"
    assert reserve(c, "other-owner").record.state == "RESERVED"
    assert _until(lambda: a.account_client.refresh(timeout_seconds=2), timeout=5).observation.account_key
    market_registration = next(x for x in a.status().bindings if x.mount == "store.market").registration_id
    c_registration = c.status().bindings[0].registration_id
    subscription = a.market_client.subscription("ticks")
    responses = queue.Queue()

    def start():
        try:
            responses.put(("result", subscription.start(timeout_seconds=4)))
        except Exception as exc:
            responses.put(("error", exc))

    thread = threading.Thread(target=start, name="waiting-market-start")
    thread.start()
    try:
        assert async_gateway["entered"].wait(5)
        assert thread.is_alive() and not async_gateway["release"].is_set()
        first = c.command_client.status("other-owner", timeout_seconds=0.7)
        assert first.outcome == "OBSERVED" and first.record.state == "RESERVED"
        second = a.command_client.status("same-owner", timeout_seconds=0.7)
        assert second.outcome == "OBSERVED" and second.record.state == "RESERVED"
        account = a.account_client.refresh(timeout_seconds=0.7)
        assert account.observation.account_key.account_id == "account"
        assert thread.is_alive(), "market.start must still be waiting while same-owner calls complete"
        assert renewed.wait(2.5), "market registration must really renew during blocked start"
        assert thread.is_alive() and not async_gateway["release"].is_set()
        with lock:
            assert any(registration == market_registration and after > before
                for registration, before, after in renewals), renewals
        assert c.status().bindings[0].registration_id == c_registration
        assert next(x for x in a.status().bindings if x.mount == "store.market").registration_id == market_registration
        thread.join(5)
        outcome, error = responses.get(timeout=1)
        assert outcome == "error" and isinstance(error, ThreadedRuntimeError)
        assert error.code == "OWNER_TIMEOUT_RUNNING"
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
            subscription.start(timeout_seconds=0.2)
        with lock:
            begin_ids = [operation_id for op, operation_id, _ in trace if op == "market.bootstrap_begin"]
            pending_ids = [operation_id for _, operation_id, _ in trace]
        assert len(begin_ids) == 1 and set(pending_ids) == set(begin_ids)
        before = stats(async_gateway)
        assert before[:5] == ("stats", 1, 1, 1, 1) and before[5] == ("IF2612",)
        async_gateway["release"].set()
        bootstrap = subscription.start(timeout_seconds=6)
        assert bootstrap.source_descriptor.key == _key("IF2612")
        assert bootstrap.source_descriptor.connection_generation == 1
        assert bootstrap.source_descriptor.epoch == bootstrap.snapshot_epoch
        with lock:
            assert sum(op == "market.bootstrap_begin" for op, _, _ in trace) == 1
            assert {operation_id for _, operation_id, _ in trace} == set(begin_ids)
        after = stats(async_gateway)
        assert after[:5] == ("stats", 1, 1, 1, 1) and after[5] == ("IF2612",)
        assert next(x for x in a.status().bindings if x.mount == "store.market").registration_id == market_registration
    finally:
        async_gateway["release"].set()
        thread.join(6)
        assert not thread.is_alive()


def test_cancel_pending_retains_operation_and_cleans_late_acquisition(async_gateway):
    session = strategy(async_gateway)
    runtime = session.runtime
    operation_id = "a" * 32
    arguments = {"operation_id": operation_id, "subscriptions": [
        {"key": _key("IF2612").to_dict(), "require_source_descriptor": True}]}
    registration = next(x for x in session.status().bindings if x.mount == "store.market").registration_id
    begin = runtime.call("store.market", "market.bootstrap_begin", arguments, timeout_seconds=3)
    assert begin["operation_id"] == operation_id and begin["status"] == "pending"
    assert async_gateway["entered"].wait(5)
    repeated = runtime.call("store.market", "market.bootstrap_begin", arguments, timeout_seconds=2)
    assert repeated["operation_id"] == operation_id and repeated["status"] == "pending"
    cancel = runtime.call("store.market", "market.bootstrap_cancel", {"operation_id": operation_id}, timeout_seconds=2)
    assert cancel["operation_id"] == operation_id and cancel["status"] in {"cancelling", "cancelled"}
    retry = runtime.call("store.market", "market.bootstrap_begin", arguments, timeout_seconds=2)
    assert retry.get("operation_id") == operation_id and retry.get("status") in {"cancelling", "cancelled"}, retry
    assert stats(async_gateway)[:5] == ("stats", 1, 1, 1, 1)
    async_gateway["release"].set()

    def cancelled():
        result = runtime.call("store.market", "market.bootstrap_status", {"operation_id": operation_id}, timeout_seconds=2)
        assert result["operation_id"] == operation_id
        assert result["status"] in {"cancelling", "cancelled"}, result
        return result if result["status"] == "cancelled" else None

    assert _until(cancelled, timeout=6)["status"] == "cancelled"
    assert next(x for x in session.status().bindings if x.mount == "store.market").registration_id == registration
    assert _until(lambda: (s if (s := stats(async_gateway))[6] == 1 and s[12] else None), timeout=5)[:5] == ("stats", 1, 1, 1, 1)
