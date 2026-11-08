"""Offline coverage of the explicit Gateway strategy session assembly."""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.gateway_direct_client import GatewayClientRoute
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import ClientCurveKeys
from bt_api_py.service_runtime_thread import ThreadedRuntimeError


class Host:
    def save_intent(self, *args):
        pass

    def load_intent(self, *args):
        pass

    def save_record(self, *args):
        pass

    def load_record(self, *args):
        pass

    def commit_pre_dispatch(self, *args):
        pass

    def save_release(self, *args):
        pass

    def load_release(self, *args):
        pass

    def save_cancel_plan(self, *args):
        pass

    def load_cancel_plan(self, *args):
        pass

    def save_cancel_record(self, *args):
        pass

    def load_cancel_record(self, *args):
        pass


def config_data():
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": "strategy"},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "broker.direct_write",
                            "service_id": "gateway",
                            "instance_id": "instance",
                            "scope_ref": "scope",
                            "account_ref": "account",
                            "transport_ref": "transport",
                        }
                    ]
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "transport": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": "tcp://127.0.0.1:12345"},
                    "security_profile_ref": "security",
                }
            },
        },
    }


def parts():
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, "principal", "strategy")
    scope = WireScope("domain", account_ref="account", strategy_ref="strategy", run_id="run")
    return {
        "config": parse_client_config(config_data()),
        "binding": GatewayCommandBinding("journal", "account", "strategy", "principal"),
        "route": GatewayClientRoute(
            "broker.direct_write", "gateway", "instance", "scope", scope, identity
        ),
        "peer": WireIdentity(WireRole.SERVICE, "service-principal", "gateway"),
        "security_profiles": {"security": ClientCurveKeys(b"a" * 40, b"b" * 40, b"c" * 40)},
        "host": Host(),
        "requested_operations": frozenset({"direct.events.progress"}),
    }


def test_constructor_is_inert_and_properties_are_read_only(monkeypatch):
    import bt_api_py.gateway_strategy_client as module

    def forbidden(*_args, **_kwargs):
        pytest.fail("runtime or transport constructed before start")

    monkeypatch.setattr(module, "ServiceClientRuntime", forbidden)
    monkeypatch.setattr(module, "ZmqTransportFactory", forbidden)
    data = parts()
    session = GatewayStrategyClientSession(**data)
    assert session.status().state == "new"
    assert session.status().thread_alive is False
    assert session.binding == data["binding"]
    assert session.route == data["route"]
    for name in ("runtime", "command_client", "execution_stream_client", "binding", "route"):
        with pytest.raises(AttributeError):
            setattr(session, name, None)
    assert session.close().state == "closed"


def test_owner_factory_receives_frozen_inputs_and_matching_scope(monkeypatch):
    import bt_api_py.gateway_strategy_client as module

    observed = {}
    main_thread = threading.get_ident()

    class FakeRuntime:
        def __init__(self, config, **kwargs):
            observed.update(
                config=config.to_public_dict(), kwargs=kwargs, thread=threading.get_ident()
            )
            self.identity = kwargs["identity"]
            self.run_id = kwargs["run_id"]
            self.bindings = (
                SimpleNamespace(
                    registration_id="registration",
                    service_epoch="epoch",
                    active=True,
                    mount="broker.direct_write",
                    service_id="gateway",
                    instance_id="instance",
                    scope_ref="scope",
                    scope=WireScope(
                        "domain", account_ref="account", strategy_ref="strategy", run_id="run"
                    ),
                    operations=frozenset({"direct.events.progress"}),
                    required=True,
                    lease_deadline=time.monotonic() + 60,
                ),
            )
            self.healthy = True
            self.cleanup_failures = self.unregister_uncertainties = 0

        def start(self):
            pass

        def maintain(self):
            pass

        def next_maintenance_delay(self):
            return 1

        def close(self):
            pass

    def transport(profiles):
        observed["profiles"] = dict(profiles)
        return object()

    monkeypatch.setattr(module, "ServiceClientRuntime", FakeRuntime)
    monkeypatch.setattr(module, "ZmqTransportFactory", transport)
    data = parts()
    session = GatewayStrategyClientSession(**data)
    data["security_profiles"].clear()
    object.__setattr__(data["route"].identity, "actor_ref", "changed")
    exposed_route = session.route
    exposed_binding = session.binding
    object.__setattr__(exposed_route.scope, "run_id", "other-run")
    object.__setattr__(exposed_route.identity, "actor_ref", "other-strategy")
    object.__setattr__(exposed_binding, "principal", "other-principal")
    snapshot = session.start()
    assert snapshot.healthy
    assert observed["thread"] != main_thread
    assert observed["profiles"]["security"].secret_key == b"c" * 40
    kwargs = observed["kwargs"]
    assert kwargs["identity"].actor_ref == "strategy"
    assert kwargs["run_id"] == "run"
    assert session.binding.principal == "principal"
    assert session.route.scope.run_id == "run"
    assert kwargs["requested_operations"] == {
        "broker.direct_write": frozenset({"direct.events.progress"})
    }
    assert kwargs["scope_resolver"]("scope") == WireScope("domain", account_ref="account")
    assert kwargs["peer_resolver"]("gateway") == data["peer"]
    assert session.execution_stream_client._route_ready("direct.events.progress")
    object.__setattr__(snapshot.identity, "actor_ref", "tampered")
    assert session.route.identity.actor_ref == "strategy"
    assert not session.execution_stream_client._route_ready("direct.events.progress")
    with pytest.raises(ValueError, match="INVALID_SCOPE"):
        kwargs["scope_resolver"]("other")
    handle = session.runtime
    assert session.close().state == "closed"
    assert session.runtime is handle
    with pytest.raises(ThreadedRuntimeError, match="NOT_NEW"):
        session.start()


@pytest.mark.parametrize(
    "operations",
    [
        frozenset(),
        frozenset({"direct.reserve"}),
        frozenset({"direct.release"}),
        frozenset({"direct.dispatch"}),
        frozenset({"direct.cancel"}),
        frozenset({"direct.not_real"}),
        {"direct.command_status"},
    ],
)
def test_invalid_requested_operations_rejected(operations):
    data = parts()
    data["requested_operations"] = operations
    with pytest.raises(ValueError, match="INVALID_REQUESTED_OPERATIONS"):
        GatewayStrategyClientSession(**data)


def test_independent_event_and_cancel_status_permissions():
    for operation in ("direct.events.read_after", "direct.events.progress", "direct.events.ack"):
        data = parts()
        data["requested_operations"] = frozenset({operation})
        assert GatewayStrategyClientSession(**data).status().state == "new"
    data = parts()
    data["requested_operations"] = frozenset({"direct.cancel_status"})
    with pytest.raises(ValueError, match="INVALID_REQUESTED_OPERATIONS"):
        GatewayStrategyClientSession(**data)
    data["cancel_host"] = Host()
    assert GatewayStrategyClientSession(**data).status().state == "new"


@pytest.mark.parametrize(
    "change",
    [
        "registration",
        "service",
        "instance",
        "scope",
        "account",
        "extra_binding",
        "extra_profile",
        "market_endpoint",
        "peer_role",
        "peer_actor",
        "peer_delegate",
        "route_account",
        "route_strategy",
        "route_principal",
        "route_delegate",
        "security_extra",
        "security_missing",
        "security_mutable",
        "binding_type",
    ],
)
def test_configuration_mismatches_rejected_without_sensitive_values(change):
    data = parts()
    raw = config_data()
    cfg_binding = raw["clients"]["gateway"]["settings"]["bindings"][0]
    if change == "registration":
        raw["registration"]["strategy_ref"] = "other"
    elif change in {"service", "instance", "scope", "account"}:
        cfg_binding[
            {
                "service": "service_id",
                "instance": "instance_id",
                "scope": "scope_ref",
                "account": "account_ref",
            }[change]
        ] = "other"
    elif change == "extra_binding":
        other = copy.deepcopy(cfg_binding)
        other["mount"] = "store.account"
        raw["clients"]["gateway"]["settings"]["bindings"].append(other)
    elif change == "extra_profile":
        raw["communication"]["profiles"]["other"] = copy.deepcopy(
            raw["communication"]["profiles"]["transport"]
        )
    elif change == "market_endpoint":
        raw["communication"]["profiles"]["transport"]["endpoints"]["market_connect"] = (
            "tcp://127.0.0.1:12346"
        )
    elif change == "peer_role":
        data["peer"] = replace(data["peer"], role=WireRole.STRATEGY_CLIENT)
    elif change == "peer_actor":
        data["peer"] = replace(data["peer"], actor_ref="other")
    elif change == "peer_delegate":
        data["peer"] = replace(data["peer"], delegate_ref="other")
    elif change == "route_account":
        data["route"] = replace(
            data["route"], scope=replace(data["route"].scope, account_ref="other")
        )
    elif change == "route_strategy":
        data["route"] = replace(
            data["route"], scope=replace(data["route"].scope, strategy_ref="other")
        )
    elif change == "route_principal":
        data["route"] = replace(
            data["route"], identity=replace(data["route"].identity, principal_ref="other")
        )
    elif change == "route_delegate":
        data["route"] = replace(
            data["route"], identity=replace(data["route"].identity, delegate_ref="other")
        )
    elif change == "security_extra":
        data["security_profiles"]["other"] = data["security_profiles"]["security"]
    elif change == "security_missing":
        data["security_profiles"].clear()
    elif change == "security_mutable":
        data["security_profiles"]["security"] = ClientCurveKeys(
            bytearray(b"private-marker"), b"public", b"secret"
        )
    elif change == "binding_type":
        data["binding"] = object()
    data["config"] = parse_client_config(raw)
    with pytest.raises((ValueError, TypeError)) as exc:
        GatewayStrategyClientSession(**data)
    assert "private-marker" not in str(exc.value)
    assert "127.0.0.1" not in str(exc.value)


def test_close_timeout_keeps_handle_until_worker_finishes(monkeypatch):
    import bt_api_py.gateway_strategy_client as module

    started = threading.Event()
    release = threading.Event()

    class SlowCloseRuntime:
        def __init__(self, _config, **kwargs):
            self.identity, self.run_id = kwargs["identity"], kwargs["run_id"]
            self.bindings = ()
            self.healthy = True
            self.cleanup_failures = self.unregister_uncertainties = 0

        def start(self):
            pass

        def maintain(self):
            pass

        def next_maintenance_delay(self):
            return 1

        def close(self):
            started.set()
            assert release.wait(5)

    monkeypatch.setattr(module, "ServiceClientRuntime", SlowCloseRuntime)
    session = GatewayStrategyClientSession(**parts())
    handle = session.runtime
    session.start()
    try:
        with pytest.raises(ThreadedRuntimeError, match="CLOSE_TIMEOUT_WORKER_ALIVE"):
            session.close(timeout_seconds=0.02)
        assert started.wait(1)
        assert session.runtime is handle
        assert session.status().thread_alive
    finally:
        release.set()
    assert session.close().state == "closed"
    assert session.runtime is handle


def test_cleanup_failure_retains_handle_and_explicit_close_retries(monkeypatch):
    import bt_api_py.gateway_strategy_client as module

    attempts = []

    class RetryCloseRuntime:
        def __init__(self, _config, **kwargs):
            self.identity, self.run_id = kwargs["identity"], kwargs["run_id"]
            self.bindings = ()
            self.healthy = True
            self.cleanup_failures = self.unregister_uncertainties = 0

        def start(self):
            pass

        def maintain(self):
            pass

        def next_maintenance_delay(self):
            return 1

        def close(self):
            attempts.append(threading.get_ident())
            if len(attempts) == 1:
                raise RuntimeError("private-marker")

    monkeypatch.setattr(module, "ServiceClientRuntime", RetryCloseRuntime)
    session = GatewayStrategyClientSession(**parts())
    handle = session.runtime
    session.start()
    with pytest.raises(ThreadedRuntimeError, match="CLEANUP_FAILED") as exc:
        session.close()
    assert "private-marker" not in str(exc.value)
    assert session.status().state == "close_failed"
    assert session.runtime is handle
    assert session.close().state == "closed"
    assert session.runtime is handle
    assert len(attempts) == 2 and attempts[0] == attempts[1]


def test_invalid_queue_host_and_corrupted_binding_are_rejected():
    data = parts()
    with pytest.raises(ValueError, match="queue_size"):
        GatewayStrategyClientSession(**data, queue_size=True)
    data["host"] = object()
    with pytest.raises(TypeError, match="INVALID_HOST_PORT"):
        GatewayStrategyClientSession(**data)
    data = parts()
    object.__setattr__(data["binding"], "journal_id", "")
    with pytest.raises(ValueError):
        GatewayStrategyClientSession(**data)


def test_oversized_runtime_run_id_rejected_before_start():
    data = parts()
    data["route"] = replace(data["route"], scope=replace(data["route"].scope, run_id="x" * 129))
    with pytest.raises(ValueError, match="INVALID_CLIENT_CONFIGURATION"):
        GatewayStrategyClientSession(**data)


def test_start_timeout_preserves_same_runtime_for_cleanup(monkeypatch):
    import bt_api_py.gateway_strategy_client as module

    entered, release = threading.Event(), threading.Event()

    class SlowStartRuntime:
        def __init__(self, _config, **kwargs):
            self.identity, self.run_id = kwargs["identity"], kwargs["run_id"]
            self.bindings = ()
            self.healthy = True
            self.cleanup_failures = self.unregister_uncertainties = 0

        def start(self):
            entered.set()
            assert release.wait(5)

        def close(self):
            pass

    monkeypatch.setattr(module, "ServiceClientRuntime", SlowStartRuntime)
    session = GatewayStrategyClientSession(**parts())
    handle = session.runtime
    try:
        with pytest.raises(ThreadedRuntimeError, match="START_TIMEOUT_CLEANUP_PENDING"):
            session.start(timeout_seconds=0.02)
        assert entered.is_set()
        assert session.runtime is handle
        assert session.status().thread_alive
    finally:
        release.set()
    assert session.close().state in {"closed", "failed"}
    assert session.runtime is handle
