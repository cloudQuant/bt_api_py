"""Synthetic direct RPC with real CURVE, service and separate owner threads."""

import json
import queue
import sqlite3
import threading
import time

import pytest
import zmq
from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord, WriteOperation
from bt_api_base.service_protocol import WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceCallOutcomeUnknownError,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT,
    CLIENT_A,
    CLIENT_B,
    DOMAIN,
    SERVICE,
    _client_config,
    _service_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_direct_service import _order

OPS = frozenset({"direct.reserve", "direct.release", "direct.command_status", "direct.dispatch"})


def _until(condition, seconds=5):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def _dispatch_args(result):
    row = result["record"]
    return {
        "request_key": row["request_key"],
        "reservation_id": row["reservation_id"],
        "digest": row["digest"],
        "expected_version": row["version"],
    }


@pytest.mark.parametrize("close_while_blocked", [False, True])
def test_curve_dispatch_queue_lost_reply_revocation_and_status(tmp_path, close_while_blocked):
    endpoint = _free_endpoint()
    server_pub, server_secret = zmq.curve_keypair()
    a_pub, a_secret = zmq.curve_keypair()
    b_pub, b_secret = zmq.curve_keypair()
    config = _service_config(endpoint).to_public_dict()
    config["components"]["gateway"]["settings"]["options"]["dispatch_enabled"] = True
    config = parse_service_config(config)
    gate_dir = tmp_path / "guards"
    gate_dir.mkdir()
    journal_holder = []
    native_entered, unblock = threading.Event(), threading.Event()
    native_sends = []

    def owner_factory():
        guard = LocalAccountSessionGuard(gate_dir, ACCOUNT, "synthetic-owner")
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal.create(
            tmp_path / "orders.sqlite", ACCOUNT, generation=generation
        )
        journal_holder.append(journal)

        def native(payload, commit, account):
            key = payload["idempotency_key"]
            if key == "hold":
                native_entered.set()
                if not unblock.wait(10):
                    raise RuntimeError("synthetic timeout")
            identity = NativeOrderIdentity(
                "TEST",
                "native-" + key,
                {"trading_day": "20300101", "front_id": 1, "inst_id": "rb"},
                1,
            )
            record = PreDispatchRecord(
                operation=WriteOperation.PLACE,
                host_scope=account,
                exchange_name="TEST",
                symbol=payload["symbol"],
                identity=identity,
                target_identity=None,
                request_summary=payload,
                trading_day="20300101",
                session_generation=1,
                created_at_utc="synthetic",
            )
            marker = commit.commit(record)
            if marker is None:
                return NativeDispatchOutcome(False, identity, None)
            native_sends.append(key)
            return NativeDispatchOutcome(True, identity, marker)

        dispatcher = GatewayDirectDispatcher(
            journal,
            "TEST",
            guard,
            native,
            lambda identity: ("synthetic:" + identity.client_order_id,),
            lambda row: True,
        )
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

    owner = GatewayDirectOwner(
        owner_factory, queue_size=1, max_queue_wait_seconds=30, busy_timeout_seconds=30
    )
    assert owner.start(5)
    journal = journal_holder[0]
    grants = {
        CLIENT_A.principal_ref: GatewayDirectGrant("strategy-a", frozenset({"rb"})),
        CLIENT_B.principal_ref: GatewayDirectGrant("strategy-b", frozenset({"sa"})),
    }
    binding = GatewayDirectServiceBinding(journal, grants, owner=owner)

    def grant(identity):
        return PrincipalGrant(
            identity,
            (
                ResourceGrant(
                    "gateway-a",
                    "service.direct_write",
                    "scope-a",
                    WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref),
                    OPS,
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
        curve_public_key=server_pub,
        curve_secret_key=server_secret,
        principals={a_pub: CLIENT_A.principal_ref, b_pub: CLIENT_B.principal_ref},
        grants={
            CLIENT_A.principal_ref: (grant(CLIENT_A),),
            CLIENT_B.principal_ref: (grant(CLIENT_B),),
        },
    )
    controls, ready, renewals, dropped = queue.Queue(), queue.Queue(), queue.Queue(), queue.Queue()
    stop = threading.Event()

    def serve():
        host = None
        try:
            host = ServiceHost(
                config,
                authority,
                tmp_path / "registry.sqlite",
                lambda *_: ComponentResources.borrowed({"binding": binding}, ready=lambda: True),
            )
            host.start()
            ready.put(("ready", None))
            while not stop.is_set():
                try:
                    work = controls.get_nowait()
                except queue.Empty:
                    work = None
                if work is not None:
                    work(host)
                host.serve_once(10)
        except BaseException as exc:
            ready.put(("error", getattr(exc, "code", type(exc).__name__)))
        finally:
            if host is not None and host.state == "started":
                host.close()

    host_thread = threading.Thread(target=serve, daemon=True)
    host_thread.start()
    a = b = None
    try:
        assert ready.get(timeout=10) == ("ready", None)

        def runtime(identity, public, secret, drop_key=None):
            def factory():
                raw_config = _client_config(endpoint, identity).to_public_dict()
                raw_config["clients"]["gateway"]["settings"]["options"][
                    "heartbeat_interval_seconds"
                ] = 1
                keys = ZmqTransportFactory(
                    {"security-a": ClientCurveKeys(server_pub, public, secret)}
                )

                class Transport:
                    def __init__(self, wrapped):
                        self.wrapped, self.dropped = wrapped, False

                    def request(self, request, *, timeout):
                        response = self.wrapped.request(request, timeout=timeout)
                        if request.header.operation == "service.renew":
                            renewals.put(identity.principal_ref)
                        if (
                            request.header.operation == "direct.dispatch"
                            and not self.dropped
                            and request.payload["arguments"]["request_key"] == drop_key
                        ):
                            self.dropped = True
                            dropped.put(response.payload)
                            raise TimeoutError("synthetic lost dispatch reply")
                        return response

                    def close(self):
                        self.wrapped.close()

                return ServiceClientRuntime(
                    parse_client_config(raw_config),
                    identity=identity,
                    scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
                    peer_resolver=lambda _: SERVICE,
                    transport_factory=lambda *args: Transport(keys(*args)),
                    run_id="run-" + identity.actor_ref,
                )

            return ThreadedServiceClientRuntime(factory)

        a = runtime(CLIENT_A, a_pub, a_secret)
        b = runtime(CLIENT_B, b_pub, b_secret, drop_key="queued")
        a.start(timeout_seconds=10)
        b.start(timeout_seconds=10)

        def call(client, op, args):
            return client.call("broker.direct_write", op, args, timeout_seconds=10)

        def reserve(client, key, symbol):
            return call(
                client,
                "direct.reserve",
                {"request_key": key, "payload": _order(key=key, symbol=symbol), "ttl_seconds": 30},
            )

        first = _dispatch_args(reserve(a, "first", "rb"))
        response = call(a, "direct.dispatch", first)
        assert response["status"] == "OK" and response["dispatch_queue_state"] == "QUEUED"
        _until(lambda: journal.get("strategy-a", "first", CLIENT_A.principal_ref).status == "SENT")
        duplicate = call(a, "direct.dispatch", first)
        assert duplicate["status"] == "OK"
        _until(lambda: not owner.status().running and owner.status().queue_depth == 0)
        assert native_sends == ["first"]
        done = call(a, "direct.command_status", {"request_key": "first"})["record"]
        assert done["native_identity"]["fields"] == {
            "front_id": 1,
            "inst_id": "rb",
            "trading_day": "20300101",
        }
        assert len(done["strong_identity_key_digests"]) == 1
        assert "synthetic:native-first" not in str(done)
        # A compromised durable JSON string cannot smuggle a secret-shaped field.
        with sqlite3.connect(tmp_path / "orders.sqlite") as db:
            (saved_identity,) = db.execute(
                "SELECT native_identity_json FROM direct_journal_commands WHERE request_key='first'"
            ).fetchone()
            corrupt = json.loads(saved_identity)
            corrupt["fields_json"] = json.dumps({"secret_token": "synthetic-do-not-return"})
            db.execute(
                "UPDATE direct_journal_commands SET native_identity_json=? WHERE request_key='first'",
                (json.dumps(corrupt),),
            )
        with pytest.raises(ServiceCallOutcomeUnknownError, match="BUSINESS_OUTCOME_UNKNOWN"):
            call(a, "direct.command_status", {"request_key": "first"})
        with sqlite3.connect(tmp_path / "orders.sqlite") as db:
            db.execute(
                "UPDATE direct_journal_commands SET native_identity_json=? WHERE request_key='first'",
                (saved_identity,),
            )

        while not renewals.empty():
            renewals.get_nowait()
        held = _dispatch_args(reserve(a, "hold", "rb"))
        assert call(a, "direct.dispatch", held)["dispatch_queue_state"] == "QUEUED"
        assert native_entered.wait(5)
        queued = _dispatch_args(reserve(b, "queued", "sa"))
        with pytest.raises(ThreadedRuntimeError, match="CALL_OUTCOME_UNKNOWN"):
            call(b, "direct.dispatch", queued)
        assert dropped.get(timeout=3)["result"]["dispatch_queue_state"] == "QUEUED"
        assert (
            call(b, "direct.command_status", {"request_key": "queued"})["record"]["state"]
            == "RESERVED"
        )
        assert call(b, "direct.dispatch", queued)["dispatch_queue_state"] == "ALREADY_QUEUED"
        full = _dispatch_args(reserve(b, "full", "sa"))
        assert call(b, "direct.dispatch", full) == {
            "status": "REQUEST_REJECTED",
            "code": "DISPATCH_QUEUE_FULL",
        }
        assert (
            call(b, "direct.command_status", {"request_key": "full"})["record"]["state"]
            == "RESERVED"
        )
        renew_deadline = time.monotonic() + 5
        while True:
            who = renewals.get(timeout=max(0.1, renew_deadline - time.monotonic()))
            if who == CLIENT_B.principal_ref:
                break
            assert time.monotonic() < renew_deadline
        if close_while_blocked:
            assert owner.close(0.05) is False
            assert call(b, "direct.dispatch", queued) == {
                "status": "REQUEST_REJECTED",
                "code": "DISPATCH_QUEUE_CLOSING",
            }
            assert (
                call(b, "direct.command_status", {"request_key": "queued"})["record"]["state"]
                == "RESERVED"
            )
        else:
            revoked = threading.Event()

            def revoke(host):
                host._registry._grants = {}
                revoked.set()

            controls.put(revoke)
            assert revoked.wait(4)
        unblock.set()
        _until(lambda: not owner.status().running and owner.status().queue_depth == 0)
        assert native_sends == (["first", "hold"] if close_while_blocked else ["first"])
        assert journal.get("strategy-a", "hold", CLIENT_A.principal_ref).status == (
            "SENT" if close_while_blocked else "RESERVED"
        )
        assert journal.get("strategy-b", "queued", CLIENT_B.principal_ref).status == "RESERVED"
        assert journal.get("strategy-b", "full", CLIENT_B.principal_ref).status == "RESERVED"
    finally:
        unblock.set()
        for runtime in (b, a):
            if runtime is not None:
                runtime.close(timeout_seconds=10)
        stop.set()
        host_thread.join(10)
        assert not host_thread.is_alive()
        assert owner.close(10)


def test_dispatch_enabled_requires_attached_started_owner(tmp_path):
    endpoint = _free_endpoint()
    server_pub, server_secret = zmq.curve_keypair()
    client_pub, _ = zmq.curve_keypair()
    config = _service_config(endpoint).to_public_dict()
    config["components"]["gateway"]["settings"]["options"]["dispatch_enabled"] = True
    config = parse_service_config(config)
    principal_grant = PrincipalGrant(
        CLIENT_A,
        (
            ResourceGrant(
                "gateway-a",
                "service.direct_write",
                "scope-a",
                WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref="strategy-a"),
                OPS,
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
        curve_public_key=server_pub,
        curve_secret_key=server_secret,
        principals={client_pub: CLIENT_A.principal_ref},
        grants={CLIENT_A.principal_ref: (principal_grant,)},
    )
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "orders.sqlite", ACCOUNT, generation="g1"
    )
    binding = GatewayDirectServiceBinding(
        journal, {CLIENT_A.principal_ref: GatewayDirectGrant("strategy-a", frozenset({"rb"}))}
    )
    host = ServiceHost(
        config,
        authority,
        tmp_path / "registry.sqlite",
        lambda *_: ComponentResources.borrowed({"binding": binding}, ready=lambda: True),
    )
    with pytest.raises(ServiceHostError, match="DIRECT_OWNER_REQUIRED"):
        host.start()
