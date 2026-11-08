"""Account reads and direct command storage on the same synthetic Host."""

from __future__ import annotations

import queue
import threading
from datetime import UTC, datetime

import pytest
import zmq
from bt_api_base.account_protocol import (
    AccountBalance,
    AccountKey,
    AccountObservation,
    AccountResourceKey,
)
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.account_read_model import GatewayAccountReadModel
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.event_journal import GatewayEventJournal
from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
from bt_api_gateway.service_component import GatewayReadService, GatewayServiceAccountScope

from bt_api_py.gateway_direct_service import (
    ACCOUNT_READ_OPERATIONS,
    STORAGE_OPERATIONS,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceCallOutcomeUnknownError,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT,
    CLIENT_A,
    CLIENT_B,
    DOMAIN,
    OPS,
    SERVICE,
    _client_config,
    _service_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_direct_service import _order


def _dual_config(endpoint):
    data = _service_config(endpoint).to_public_dict()
    data["components"]["gateway"]["settings"]["bind_ports"].append("service.account_read")
    return parse_service_config(data)


def _dual_client(endpoint, identity):
    data = _client_config(endpoint, identity).to_public_dict()
    binding = dict(data["clients"]["gateway"]["settings"]["bindings"][0])
    binding["mount"] = "store.account"
    data["clients"]["gateway"]["settings"]["bindings"].append(binding)
    return parse_client_config(data)


def _resources(tmp_path):
    journal = GatewayEventJournal(tmp_path / "account.sqlite")
    model = GatewayAccountReadModel(
        journal,
        account_key=AccountKey("synthetic", "venue", "test", ACCOUNT),
        journal_account_scope=ACCOUNT,
        state_scope="state-a",
        required_coverage=(AccountResourceKey("future", "main"),),
        assert_current_generation=lambda _: None,
        max_staleness_seconds=30,
    )
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    resource = AccountResourceKey("future", "main")
    observation = AccountObservation(
        account_key=model.account_key,
        observation_id="offline-observation",
        coverage=(resource,),
        balances=(AccountBalance(resource, "USD", "100", "90", "10", now),),
        positions=(),
        valuation=None,
        started_at_utc=now,
        completed_at_utc=now,
        consistency="multi_query",
        balances_complete=True,
        positions_complete=True,
        open_orders=None,
        open_orders_complete=False,
    )
    model.start_generation("gen-1")
    model.finish_refresh(model.begin_refresh(), observation)
    account = GatewayServiceAccountScope(
        account_ref=ACCOUNT,
        execution_domain_ref=DOMAIN,
        journal_account_scope=ACCOUNT,
        account_read_strategy_scope="state-a",
        shared_account_facts_scope="state-a",
        shared_fact_strategy_refs=frozenset({"strategy-a", "strategy-b"}),
    )
    principal = GatewayPrincipal(
        CLIENT_A.principal_ref,
        frozenset({ACCOUNT}),
        frozenset({"state-a"}),
        frozenset({GatewayCommandKind.READ}),
    )
    read = GatewayReadService(
        journal,
        accounts={ACCOUNT: account},
        principals={CLIENT_A.principal_ref: principal},
        state_worker=model,
    )
    direct = GatewayDirectCommandJournal.create(
        tmp_path / "direct.sqlite", ACCOUNT, generation="g1"
    )
    binding = GatewayDirectServiceBinding(
        direct,
        {
            CLIENT_A.principal_ref: GatewayDirectGrant("strategy-a", frozenset({"rb"})),
            CLIENT_B.principal_ref: GatewayDirectGrant("strategy-b", frozenset({"rb"})),
        },
    )
    return binding, read, model


def test_real_host_two_ports_keep_read_acl_and_independent_command_status(tmp_path):
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    a_public, a_secret = zmq.curve_keypair()
    b_public, b_secret = zmq.curve_keypair()

    def grant(identity):
        scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref)
        return PrincipalGrant(
            identity,
            (
                ResourceGrant("gateway-a", "service.direct_write", "scope-a", scope, OPS),
                ResourceGrant(
                    "gateway-a", "service.account_read", "scope-a", scope, ACCOUNT_READ_OPERATIONS
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
        principals={a_public: CLIENT_A.principal_ref, b_public: CLIENT_B.principal_ref},
        grants={
            CLIENT_A.principal_ref: (grant(CLIENT_A),),
            CLIENT_B.principal_ref: (grant(CLIENT_B),),
        },
    )
    ready = queue.Queue()
    controls = queue.Queue()
    models = []
    stop = threading.Event()

    def serve():
        host = None
        try:

            def provider(*_):
                binding, read, model = _resources(tmp_path)
                models.append(model)
                return ComponentResources.borrowed(
                    {"binding": binding, "read_service": read}, ready=lambda: True
                )

            host = ServiceHost(
                _dual_config(endpoint), authority, tmp_path / "registry.sqlite", provider
            )
            host.start()
            ready.put(("ok", None))
            while not stop.is_set():
                try:
                    completed = controls.get_nowait()
                except queue.Empty:
                    completed = None
                if completed is not None:
                    models[0].invalidate()
                    completed.set()
                host.serve_once(10)
        except BaseException as exc:
            ready.put(("error", repr(exc)))
        finally:
            if host is not None and host.state == "started":
                host.close()

    thread = threading.Thread(target=serve)
    thread.start()
    clients = []
    try:
        assert ready.get(timeout=10) == ("ok", None)
        for identity, public, secret in (
            (CLIENT_A, a_public, a_secret),
            (CLIENT_B, b_public, b_secret),
        ):
            runtime = ThreadedServiceClientRuntime(
                lambda identity=identity, public=public, secret=secret: ServiceClientRuntime(
                    _dual_client(endpoint, identity),
                    identity=identity,
                    scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
                    peer_resolver=lambda _: SERVICE,
                    transport_factory=ZmqTransportFactory(
                        {"security-a": ClientCurveKeys(server_public, public, secret)}
                    ),
                    run_id="run-" + identity.actor_ref,
                    requested_operations={
                        "broker.direct_write": STORAGE_OPERATIONS,
                        "store.account": ACCOUNT_READ_OPERATIONS,
                    },
                )
            )
            runtime.start(timeout_seconds=10)
            clients.append(runtime)
        a, b = clients
        assert a.call("store.account", "account.head", {}, timeout_seconds=10)["latest_cursor"] > 0
        with pytest.raises(ServiceCallOutcomeUnknownError):
            a.call("store.account", "account.head", {"stream": "execution"}, timeout_seconds=10)
        with pytest.raises(ServiceCallOutcomeUnknownError):
            b.call("store.account", "account.head", {}, timeout_seconds=10)
        reserved = a.call(
            "broker.direct_write",
            "direct.reserve",
            {
                "request_key": "key-a",
                "payload": _order(key="key-a"),
                "ttl_seconds": 10,
            },
            timeout_seconds=10,
        )
        assert reserved["record"]["state"] == "RESERVED"
        typed = a.call("store.account", "account.snapshot.typed", {}, timeout_seconds=10)
        assert typed["snapshot"]["payload"]["schema_id"] == "sdk.account.state.typed"
        invalidated = threading.Event()
        controls.put(invalidated)
        assert invalidated.wait(3)
        with pytest.raises(ServiceCallOutcomeUnknownError):
            a.call("store.account", "account.snapshot.typed", {}, timeout_seconds=10)
        assert (
            a.call(
                "broker.direct_write",
                "direct.command_status",
                {"request_key": "key-a"},
                timeout_seconds=10,
            )["record"]["state"]
            == "RESERVED"
        )
    finally:
        for client in clients:
            client.close(timeout_seconds=10)
        stop.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
