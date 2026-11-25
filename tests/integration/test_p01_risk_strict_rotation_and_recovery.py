"""Regression tests for P0-1: Risk strict account-facts rotation, timeout recovery, and timeout propagation."""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from dataclasses import replace
from types import SimpleNamespace

import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py import risk_service_host as host_module
from bt_api_py.gateway_account_comparison import GatewayAccountComparison
from bt_api_py.gateway_account_facts import ACCOUNT_FACTS_OPERATIONS, GatewayAccountFactsObserver
from bt_api_py.gateway_account_observer import GatewayAccountObserver
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import (
    GatewayControlGrant,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.risk_service_host import (
    OBSERVER_OPERATIONS,
    RiskServiceHost,
    _control_client_config,
    _gateway_client_config,
    _wait_for_cleanup,
)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ServiceClientRuntime
from tests.integration.test_iter63_account_facts_curve import command, fact
from tests.integration.test_iter63_delegate_risk_execution_loopback import (
    ACCOUNT_ONLY,
    RISK,
    _read_service,
)
from tests.integration.test_iter63_gateway_direct_service import ACCOUNT, CLIENT_A, DOMAIN, SERVICE
from tests.integration.test_iter63_gateway_dual_ports import _dual_config
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_risk_account_facts_host import seed_v4, selected_config


@pytest.fixture(autouse=True)
def _clean_probe_state():
    def reset():
        host_module._RETAINED_HOSTS.clear()
        host_module._PROBE_STATE["active"] = False
        host_module._PROBE_STATE["uncertain"].clear()

    reset()
    yield
    reset()


def test_gateway_and_control_configs_propagate_call_timeout_seconds(tmp_path):
    base = selected_config(tmp_path)
    small = replace(base, call_timeout_seconds=1.0)
    gw_small = _gateway_client_config(small, "upstream.account").to_public_dict()
    ctrl_small = _control_client_config(small, ["application.direct_write"]).to_public_dict()
    assert gw_small["communication"]["profiles"]["gateway-client"]["options"]["request_timeout_ms"] == 2000
    assert gw_small["clients"]["gateway"]["settings"]["options"]["request_timeout_ms"] == 2000
    assert ctrl_small["communication"]["profiles"]["gateway"]["options"]["request_timeout_ms"] == 2000
    assert ctrl_small["clients"]["gateway"]["settings"]["options"]["request_timeout_ms"] == 2000

    large = replace(base, call_timeout_seconds=7.5)
    gw_large = _gateway_client_config(large, "upstream.account").to_public_dict()
    ctrl_large = _control_client_config(large, ["application.direct_write"]).to_public_dict()
    assert gw_large["communication"]["profiles"]["gateway-client"]["options"]["request_timeout_ms"] == 7500
    assert gw_large["clients"]["gateway"]["settings"]["options"]["request_timeout_ms"] == 7500
    assert ctrl_large["communication"]["profiles"]["gateway"]["options"]["request_timeout_ms"] == 7500
    assert ctrl_large["clients"]["gateway"]["settings"]["options"]["request_timeout_ms"] == 7500


def test_wait_for_cleanup_updates_ready_file_to_cleanup_uncertain(tmp_path):
    host = RiskServiceHost(selected_config(tmp_path))
    ready_file = tmp_path / "risk.ready.json"
    ready_file.write_text(json.dumps({"pid": 1, "state": "serving"}), encoding="utf-8")
    host._ready_file = ready_file
    host._record_cleanup_uncertain(host, "RISK_RUNTIME_CLEANUP_UNKNOWN")

    class _StopWait(BaseException):
        pass

    with pytest.raises(_StopWait):
        _wait_for_cleanup(host, _wait=lambda: (_ for _ in ()).throw(_StopWait()))

    payload = json.loads(ready_file.read_text(encoding="utf-8"))
    assert payload["state"] == "cleanup_uncertain"
    assert "RISK_RUNTIME_CLEANUP_UNKNOWN" in payload["reasons"]
    assert "HOST" in payload["resources"]


def _start_curve_gateway(tmp_path):
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    grants = PrincipalGrant(
        RISK,
        (
            ResourceGrant(
                "gateway-a",
                "service.account_read",
                "scope-a",
                ACCOUNT_ONLY,
                ACCOUNT_FACTS_OPERATIONS | OBSERVER_OPERATIONS,
            ),
        ),
    )
    authority = TrustedServiceAuthority(
        identity=SERVICE,
        scope=WireScope(DOMAIN),
        scope_ref="scope-a",
        scope_version="scope-v1",
        security_ref="security-a",
        security_version="security-v1",
        registration_policy_ref="direct-policy-v1",
        registration_policy_version="direct-policy-v1",
        service_requirements_ref="direct-requirements-v1",
        service_requirements_version="direct-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: RISK.principal_ref},
        grants={RISK.principal_ref: (grants,)},
    )
    ready, stop = queue.Queue(), threading.Event()
    (tmp_path / "guard").mkdir()

    def serve():
        owner = host = None
        try:
            state = {}

            def factory():
                guard = LocalAccountSessionGuard(tmp_path / "guard", ACCOUNT, "owner")
                journal = GatewayDirectCommandJournal.create(
                    tmp_path / "gateway.sqlite", ACCOUNT, generation=guard.acquire()
                )
                ledger = GatewayDirectExecutionLedger(journal, guard)
                command(journal, strategy=CLIENT_A.actor_ref, key="old", anchor="old-anchor")
                event = replace(
                    fact(keys=("old-anchor",), binding=("old-anchor",), venue="old-anchor"),
                    account_scope=ACCOUNT,
                )
                assert ledger.ingest(event).disposition == "ATTRIBUTED"
                control = journal.enable_controls(guard)
                journal.enable_strategy_ledger(guard)
                journal.enable_account_extensions(guard)
                journal.enable_parent_authority(guard)
                journal.enable_account_facts_financial(guard)
                journal.enable_actor_stream(guard)
                journal.enable_strategy_origin(guard)
                journal.enable_control_cancel(guard)
                journal.enable_account_fact_recovery(guard)
                journal.enable_account_event_recovery(guard)
                dispatcher = GatewayDirectDispatcher(
                    journal, "CTP", guard, lambda *_: None, lambda _: (), lambda _: True
                )
                cancel = GatewayDirectCancelDispatcher(
                    GatewayDirectCancelJournal(journal),
                    dispatcher,
                    guard,
                    "CTP",
                    lambda *_: None,
                    lambda _: True,
                )
                state.update(journal=journal, ledger=ledger, control=control)
                return GatewayDirectOwnerResources(
                    dispatcher, journal, guard.release, cancel_dispatcher=cancel
                )

            owner = GatewayDirectOwner(
                factory, queue_size=4, max_queue_wait_seconds=5, busy_timeout_seconds=5
            )
            assert owner.start(10)
            journal, ledger = state["journal"], state["ledger"]
            read_service = _read_service(tmp_path, {RISK.principal_ref, CLIENT_A.principal_ref})
            binding = GatewayDirectServiceBinding(
                journal,
                {CLIENT_A.principal_ref: GatewayDirectGrant(CLIENT_A.actor_ref, frozenset({"rb"}))},
                owner=owner,
                strategy_financial_enabled=True,
                cancel_enabled=True,
                control=state["control"],
                control_grants={
                    CLIENT_A.principal_ref: GatewayControlGrant(
                        CLIENT_A.actor_ref,
                        True,
                        frozenset(),
                        frozenset({"direct.control.status"}),
                    )
                },
            )
            facts = GatewayAccountFactsObserver(
                owner,
                journal,
                ledger,
                account_scope=ACCOUNT,
                execution_domain_ref=DOMAIN,
                principals=frozenset({RISK.principal_ref}),
            )
            observer = GatewayAccountObserver(
                owner,
                journal,
                ledger,
                account_scope=ACCOUNT,
                execution_domain_ref=DOMAIN,
                principals=frozenset({RISK.principal_ref}),
            )
            comparison = GatewayAccountComparison(
                owner,
                journal,
                account_scope=ACCOUNT,
                principals=frozenset({CLIENT_A.principal_ref}),
                instruments=frozenset(
                    {MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2601", "rb2601")}
                ),
            )
            config = _dual_config(endpoint).to_public_dict()
            config["components"]["gateway"]["settings"]["options"].update(
                dispatch_enabled=True,
                control_enabled=True,
                strategy_ledger_enabled=True,
                account_extensions_enabled=True,
                account_facts_financial_enabled=True,
                cancel_enabled=True,
                strategy_origin_enabled=True,
                control_cancel_enabled=True,
                account_fact_recovery_enabled=True,
                account_event_recovery_enabled=True,
            )
            host = ServiceHost(
                parse_service_config(config),
                authority,
                tmp_path / "registry",
                lambda *_: ComponentResources.borrowed(
                    {
                        "binding": binding,
                        "read_service": read_service,
                        "account_facts": facts,
                        "account_observer": observer,
                        "account_comparison": comparison,
                    },
                    ready=lambda: True,
                ),
            )
            host.start()
            ready.put(("ready",))
            while not stop.is_set():
                host.serve_once(10)
        except BaseException as error:
            ready.put(("error", type(error).__name__, str(error), traceback.format_exc()))
        finally:
            if host is not None and host.state == "started":
                host.close()
            if owner is not None:
                owner.close(10)

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    started = ready.get(timeout=20)
    assert started[0] == "ready", started
    return SimpleNamespace(
        endpoint=endpoint,
        server_public=server_public,
        client_public=client_public,
        client_secret=client_secret,
        stop=stop,
        server=server,
    )


def _build_host(tmp_path, gw, monkeypatch):
    import bt_api_py.risk_service_gateway as gateway_module

    risk_root = tmp_path / "risk-config"
    risk_root.mkdir()
    risk_config = selected_config(risk_root)
    delegate = replace(
        risk_config.gateway.delegate,
        principal_ref=RISK.principal_ref,
        actor_ref=RISK.actor_ref,
        public_key=gw.client_public,
        secret_key=gw.client_secret,
    )
    gateway = replace(
        risk_config.gateway,
        service_id=SERVICE.actor_ref,
        principal_ref=SERVICE.principal_ref,
        instance_id="gateway-a",
        scope_ref="scope-a",
        rpc_endpoint=gw.endpoint,
        server_public_key=gw.server_public,
        delegate=delegate,
    )
    risk_config = replace(
        risk_config,
        gateway=gateway,
        account_scope=ACCOUNT,
        execution_domain_ref=DOMAIN,
        call_timeout_seconds=3.0,
        maintain_seconds=0.1,
    )
    seed_v4(risk_config, extension=2)
    host = RiskServiceHost(risk_config)

    class FakeRuntime:
        def __init__(self, mount, operations):
            self.mount, self.operations = mount, operations

        def start(self, **_kwargs):
            binding = SimpleNamespace(active=True, mount=self.mount, operations=self.operations)
            return SimpleNamespace(state="running", healthy=True, bindings=(binding,))

        def status(self):
            return SimpleNamespace(state="running", healthy=True)

        def close(self, **_kwargs):
            return SimpleNamespace(
                state="closed",
                thread_alive=False,
                error_code=None,
                unregister_uncertainties=0,
                owner_cleanup_uncertainties=0,
                unknown_registration_count=0,
            )

    class FakeEngine:
        def recover(self):
            pass

        def maintain(self):
            pass

    class FakePump:
        def __init__(self, *_args, **_kwargs):
            pass

        def poll(self):
            pass

    class FakeService:
        state = "started"

        def serve_once(self, _timeout):
            pass

        def close(self):
            self.state = "closed"

    def build_engine():
        host.engine = FakeEngine()
        host.facts = object()
        host.delegate_storage = object()
        host.control_runtime = FakeRuntime("application.direct_write", gateway_module.CONTROL_OPERATIONS)

    host.build_engine = build_engine
    host.start_service_host = lambda: setattr(host, "service_host", FakeService())
    monkeypatch.setattr(host_module, "bootstrap_policy", lambda *_: None)
    monkeypatch.setattr(host_module, "fault_point_from_environment", lambda *_: None)
    monkeypatch.setattr(gateway_module, "ObservationPump", FakePump)
    monkeypatch.setattr(gateway_module, "payload_reader", lambda *_: lambda *_: None)
    return host


def test_proactive_strict_account_facts_rotation_before_socket_cap(tmp_path, monkeypatch):
    gw = _start_curve_gateway(tmp_path)
    host = _build_host(tmp_path, gw, monkeypatch)
    try:
        host.start()
        host.run_once()
        assert host.account_facts_observation_status() == "APPLIED"
        old_runtime = host._account_facts_runtime
        old_reg = old_runtime.status().bindings[0].registration_id
        assert old_runtime.status().min_socket_message_ids_remaining is not None
        assert old_runtime.status().min_socket_message_ids_remaining > 4000

        # Simulate approaching the 4096 socket message-id cap cleanly.
        monkeypatch.setattr(host, "_ACCOUNT_FACTS_ROTATION_HEADROOM", 4096)
        host._next["account_facts"] = 0.0
        host.run_once()

        assert host._cleanup_uncertain is False
        new_runtime = host._account_facts_runtime
        assert new_runtime is not None and new_runtime is not old_runtime
        new_snap = new_runtime.status()
        assert new_snap.state == "running" and new_snap.healthy is True
        assert new_snap.bindings[0].registration_id != old_reg
        assert host.account_facts_observation_status() in {"READY", "APPLIED", "EMPTY"}
    finally:
        assert host.close() is True
        gw.stop.set()
        gw.server.join(timeout=10)


def test_retired_strict_socket_unregisters_cleanly_and_reconnects_without_bricking_risk(
    tmp_path, monkeypatch
):
    gw = _start_curve_gateway(tmp_path)
    original_config = host_module._gateway_client_config

    def fast_heartbeat(config, mount, **kwargs):
        result = original_config(config, mount, **kwargs).to_public_dict()
        result["clients"]["gateway"]["settings"]["options"] = {"heartbeat_interval_seconds": 1}
        return parse_service_config(result)

    monkeypatch.setattr(host_module, "_gateway_client_config", fast_heartbeat)
    raw_facts_runtimes: list[ServiceClientRuntime] = []
    original_construct = RiskServiceHost._construct_risk_raw_runtime

    def capture_raw(self, factory, *args, **kwargs):
        rt = original_construct(self, factory, *args, **kwargs)
        req = kwargs.get("requested_operations") or {}
        if req == {"upstream.account": host_module.ACCOUNT_FACTS_OBSERVATION_OPERATIONS}:
            raw_facts_runtimes.append(rt)
        return rt

    monkeypatch.setattr(RiskServiceHost, "_construct_risk_raw_runtime", capture_raw)
    host = _build_host(tmp_path, gw, monkeypatch)
    try:
        host.start()
        host.run_once()
        assert host.account_facts_observation_status() == "APPLIED"
        assert len(raw_facts_runtimes) == 1
        old_threaded = host._account_facts_runtime
        old_reg = old_threaded.status().bindings[0].registration_id

        # Simulate a transient timeout retiring the strict ZMQ socket on the owner thread.
        raw_rt = raw_facts_runtimes[0]
        retired = threading.Event()
        original_maintain = ServiceClientRuntime.maintain

        def retire_once(rt):
            if rt is raw_rt and not retired.is_set():
                retired.set()
                transport = next(iter(rt._transports.values()))
                transport._transport._retire_socket()
            return original_maintain(rt)

        monkeypatch.setattr(ServiceClientRuntime, "maintain", retire_once)

        # Wait for the next renewal on the retired strict socket to fail the old runtime.
        deadline = time.monotonic() + 10.0
        while old_threaded.status().state == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert old_threaded.status().state == "failed"
        # Crucially, unregister on the retired strict socket succeeded via the ephemeral
        # unregister-only transport, so RiskServiceHost was NOT poisoned into sticky cleanup!
        assert host._cleanup_uncertain is False
        assert host._cleanup_state == "ready"

        # Next run_once() reconnects a fresh strict account_facts runtime cleanly.
        host._next["observation"] = 0.0
        host._next["account_facts"] = 0.0
        host.run_once()

        assert host._cleanup_uncertain is False
        new_threaded = host._account_facts_runtime
        assert new_threaded is not None and new_threaded is not old_threaded
        new_snap = new_threaded.status()
        assert new_snap.state == "running" and new_snap.healthy is True
        assert new_snap.bindings[0].registration_id != old_reg
        assert host.account_facts_observation_status() in {"READY", "APPLIED", "EMPTY"}
    finally:
        assert host.close() is True
        gw.stop.set()
        gw.server.join(timeout=10)


def _build_host_with_real_pump(tmp_path, gw, monkeypatch):
    import bt_api_py.risk_service_gateway as gateway_module

    risk_root = tmp_path / "risk-config"
    risk_root.mkdir()
    risk_config = selected_config(risk_root)
    delegate = replace(
        risk_config.gateway.delegate,
        principal_ref=RISK.principal_ref,
        actor_ref=RISK.actor_ref,
        public_key=gw.client_public,
        secret_key=gw.client_secret,
    )
    gateway = replace(
        risk_config.gateway,
        service_id=SERVICE.actor_ref,
        principal_ref=SERVICE.principal_ref,
        instance_id="gateway-a",
        scope_ref="scope-a",
        rpc_endpoint=gw.endpoint,
        server_public_key=gw.server_public,
        delegate=delegate,
    )
    risk_config = replace(
        risk_config,
        gateway=gateway,
        account_scope=ACCOUNT,
        execution_domain_ref=DOMAIN,
        call_timeout_seconds=3.0,
        maintain_seconds=0.1,
    )
    seed_v4(risk_config, extension=2)
    host = RiskServiceHost(risk_config)

    class FakeControlRuntime:
        def start(self, **_kwargs):
            binding = SimpleNamespace(
                active=True,
                mount="application.direct_write",
                operations=gateway_module.CONTROL_OPERATIONS,
            )
            return SimpleNamespace(state="running", healthy=True, bindings=(binding,))

        def status(self):
            return SimpleNamespace(state="running", healthy=True)

        def close(self, **_kwargs):
            return SimpleNamespace(
                state="closed",
                thread_alive=False,
                error_code=None,
                unregister_uncertainties=0,
                owner_cleanup_uncertainties=0,
                unknown_registration_count=0,
            )

    class FakeService:
        state = "started"

        def serve_once(self, _timeout):
            pass

        def close(self):
            self.state = "closed"

    host._control_runtime = lambda: FakeControlRuntime()
    host.start_service_host = lambda: setattr(host, "service_host", FakeService())
    monkeypatch.setattr(host_module, "fault_point_from_environment", lambda *_: None)
    return host


def test_proactive_observer_rotation_before_socket_cap_preserves_verified_continuity(
    tmp_path, monkeypatch
):
    gw = _start_curve_gateway(tmp_path)
    host = _build_host_with_real_pump(tmp_path, gw, monkeypatch)
    try:
        host.start()
        host.run_once()
        assert host.pump.verified_epoch is not None
        assert host.ready() is True
        assert host.account_facts_observation_status() == "APPLIED"
        old_observer = host._observer_runtime
        old_snap = old_observer.status()
        old_reg = old_snap.bindings[0].registration_id
        assert old_snap.min_socket_message_ids_remaining is not None
        assert old_snap.min_socket_message_ids_remaining > 4000

        # Simulate approaching the 4096 per-socket CURVE message-id cap cleanly.
        monkeypatch.setattr(host, "_OBSERVER_ROTATION_HEADROOM", 4096)
        host._next["observation"] = 0.0
        host.run_once()

        assert host._cleanup_uncertain is False
        new_observer = host._observer_runtime
        assert new_observer is not None and new_observer is not old_observer
        new_snap = new_observer.status()
        assert new_snap.state == "running" and new_snap.healthy is True
        assert new_snap.bindings[0].registration_id != old_reg
        assert new_snap.min_socket_message_ids_remaining is not None
        assert new_snap.min_socket_message_ids_remaining > 4000
        # Same run_once() tick re-verifies pump continuity and keeps observer epoch valid.
        assert host.pump.verified_epoch is not None
        assert host.ready() is True
        assert host._verified_observer_epoch() is not None
    finally:
        assert host.close() is True
        gw.stop.set()
        gw.server.join(timeout=10)


def test_observer_lost_authenticated_socket_binding_reconnects_and_recovers_verified(
    tmp_path, monkeypatch
):
    gw = _start_curve_gateway(tmp_path)
    raw_observer_runtimes: list[ServiceClientRuntime] = []
    original_construct = RiskServiceHost._construct_risk_raw_runtime

    def capture_raw(self, factory, *args, **kwargs):
        rt = original_construct(self, factory, *args, **kwargs)
        req = kwargs.get("requested_operations") or {}
        if req == {"upstream.account": OBSERVER_OPERATIONS}:
            raw_observer_runtimes.append(rt)
        return rt

    monkeypatch.setattr(RiskServiceHost, "_construct_risk_raw_runtime", capture_raw)
    host = _build_host_with_real_pump(tmp_path, gw, monkeypatch)
    try:
        host.start()
        host.run_once()
        assert host.pump.verified_epoch is not None
        assert host.ready() is True
        assert len(raw_observer_runtimes) == 1
        old_observer = host._observer_runtime
        old_reg = old_observer.status().bindings[0].registration_id

        # Retire the observer's underlying non-strict ZMQ socket (exact soak 09:05:16 failure:
        # socket silently rolls over, state stays "running", call_bound fails BOUND_ROUTE_UNAVAILABLE).
        raw_obs = raw_observer_runtimes[0]
        retired = threading.Event()
        original_call = ServiceClientRuntime.call

        def retire_then_call(rt, *args, **kwargs):
            if rt is raw_obs and not retired.is_set():
                retired.set()
                transport = next(iter(rt._transports.values()))
                transport._transport._retire_socket()
            return original_call(rt, *args, **kwargs)

        monkeypatch.setattr(ServiceClientRuntime, "call", retire_then_call)

        # One poll triggers BOUND_ROUTE_UNAVAILABLE -> UNVERIFIED while observer stays "running".
        host.pump.poll()
        assert retired.is_set()
        assert host.pump.verified_epoch is None
        assert host.pump.last_code == "observation_read_failed"
        degraded_snap = old_observer.status()
        assert degraded_snap.state == "running"
        assert degraded_snap.bindings[0].authenticated_peer is None

        # Next run_once() detects lost authenticated binding, replaces observer cleanly,
        # and replays from 0 to restore VERIFIED continuity in the same tick.
        host._next["observation"] = 0.0
        host.run_once()

        assert host._cleanup_uncertain is False
        new_observer = host._observer_runtime
        assert new_observer is not None and new_observer is not old_observer
        new_snap = new_observer.status()
        assert new_snap.state == "running" and new_snap.healthy is True
        assert new_snap.bindings[0].registration_id != old_reg
        assert new_snap.bindings[0].authenticated_peer is not None
        assert host.pump.verified_epoch is not None
        assert host.ready() is True
        assert host._verified_observer_epoch() is not None
    finally:
        assert host.close() is True
        gw.stop.set()
        gw.server.join(timeout=10)


def test_transient_account_facts_read_timeout_recovers_strict_runtime_after_backoff(
    tmp_path, monkeypatch
):
    """A single read-only timeout on account.facts.read_after.v2 must latch UNKNOWN
    with backoff (no immediate retry on the next tick), then after backoff cleanly
    unregister and recreate the strict runtime once Gateway is confirmed reachable."""
    from bt_api_py.gateway_account_facts import GatewayAccountFactsObserver

    gw = _start_curve_gateway(tmp_path)
    host = _build_host(tmp_path, gw, monkeypatch)
    try:
        host.start()
        host.run_once()
        assert host.account_facts_observation_status() == "APPLIED"
        old_runtime = host._account_facts_runtime
        old_reg = old_runtime.status().bindings[0].registration_id

        # Inject a single transient read timeout (> 0.1s) on account.facts.read_after.v2.
        original_handle = GatewayAccountFactsObserver.handle
        timed_out_once = [False]

        def slow_once(observer, operation, context, arguments):
            if operation == "account.facts.read_after.v2" and not timed_out_once[0]:
                timed_out_once[0] = True
                time.sleep(0.25)
            return original_handle(observer, operation, context, arguments)

        monkeypatch.setattr(GatewayAccountFactsObserver, "handle", slow_once)
        host._next["account_facts"] = 0.0
        host.run_once()
        assert timed_out_once[0] is True
        assert host.account_facts_observation_status() == "UNKNOWN"
        assert host._account_facts_runtime is old_runtime
        assert old_runtime.status().state == "running"

        # Immediately before backoff elapses: stays UNKNOWN and does NOT replace yet.
        host._next["observation"] = 0.0
        host.run_once()
        assert host.account_facts_observation_status() == "UNKNOWN"
        assert host._account_facts_runtime is old_runtime

        # Once backoff elapses and observer confirms Gateway is live: cleanly replaces
        # strict runtime (authoritative unregister ACK) and resumes polling to EMPTY.
        host._account_facts_retry_at = 0.0
        host._next["observation"] = 0.0
        host.run_once()
        assert host._cleanup_uncertain is False
        new_runtime = host._account_facts_runtime
        assert new_runtime is not None and new_runtime is not old_runtime
        assert new_runtime.status().bindings[0].registration_id != old_reg
        assert host.account_facts_observation_status() in {"READY", "EMPTY", "APPLIED"}

        host._next["account_facts"] = 0.0
        host.run_once()
        assert host.account_facts_observation_status() == "EMPTY"
    finally:
        assert host.close() is True
        gw.stop.set()
        gw.server.join(timeout=10)
