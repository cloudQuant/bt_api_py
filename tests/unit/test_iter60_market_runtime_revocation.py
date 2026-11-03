"""Iteration 60 G2: revoke a RUNNING market principal through the real Host XPUB.

ServiceHost binds the Gateway hub to the CURVE XPUB publisher with a
``regrant`` callback. ``revoke_principal_keys`` on the running hub rebuilds the
enforcing socket: the revoked raw subscriber stops receiving the stream while
the other principal keeps it; a re-grant restores delivery from a floor.
Frames already queued in the revoked peer's socket are not recallable (they
are drained here and are the receiver's job to drop). INTEGRATION_SIM: local
loopback sockets and a synthetic provider only.
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_gateway.market_hub import GatewayMarketHub
from bt_api_transport_zmq import market_v2

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost
from bt_api_py.service_registry import PrincipalGrant
from tests.unit.test_iter60_market_partition_host import _config
from tests.unit.test_service_host import _market_authority, _market_key


class _Provider:
    def __init__(self):
        self.generations = {}

    def subscribe(self, key, *, generation):
        self.generations[key] = generation
        return key

    def unsubscribe(self, handle):
        return True


def _socket(context, authority, public, secret, topic, endpoint):
    socket = context.socket(zmq.SUB)
    socket.linger = 0
    socket.curve_serverkey = authority.curve_public_key
    socket.curve_publickey = public
    socket.curve_secretkey = secret
    socket.setsockopt(zmq.SUBSCRIBE, topic)
    socket.connect(endpoint)
    return socket


def _drain(socket):
    count = 0
    while socket.poll(1, zmq.POLLIN):
        socket.recv_multipart()
        count += 1
    return count


def test_revoking_a_running_principal_stops_its_delivery_and_regrant_restores(tmp_path):
    config = _config(2)
    original, registration_scope = _market_authority(config)
    public_a, secret_a = zmq.curve_keypair()
    public_b, secret_b = zmq.curve_keypair()
    grant_a = original.grants["curve-principal"][0]
    grant_b = replace(grant_a.resources[0], scope=replace(registration_scope, strategy_ref="b"))
    identity_b = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-b", "b")
    authority = replace(
        original,
        principals={public_a: "curve-principal", public_b: "principal-b"},
        grants={
            "curve-principal": (grant_a,),
            "principal-b": (PrincipalGrant(identity_b, (grant_b,)),),
        },
    )
    key_a = _market_key()
    shared = replace(key_a, source_ref="shared-source")
    provider = _Provider()
    hub = GatewayMarketHub(
        provider,
        key_universe=(key_a, shared),
        principal_allowed_keys={"curve-principal": {key_a, shared}, "principal-b": {shared}},
        service_identity=authority.identity,
        service_scope=authority.scope,
    )
    resources = ComponentResources.owned({"hub": hub}, ready=lambda: True, close=hub.close)
    host = ServiceHost(config, authority, tmp_path / "registry.sqlite", lambda *_: resources)
    context = zmq.Context()
    sockets = []
    value = [0]

    def publish_round():
        value[0] += 1
        stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        hub.publish(shared, {"last": value[0]}, stamp, generation=provider.generations[shared])

    try:
        host.start()
        hub.subscribe("curve-principal", shared, "reg-a")
        hub.subscribe("principal-b", shared, "reg-b")
        topic = market_v2._market_topic_for_stream_id(shared.stream_id)
        a = _socket(context, authority, public_a, secret_a, topic, host.market_endpoint)
        b = _socket(context, authority, public_b, secret_b, topic, host.market_endpoint)
        sockets += [a, b]
        received = [0, 0]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not all(received):
            publish_round()
            received[0] += _drain(a)
            received[1] += _drain(b)
        assert all(received), received

        generation = hub.authorization_generation
        result = hub.revoke_principal_keys("principal-b")
        assert result["transport_enforced"] is True
        assert hub.authorization_generation == generation + 1
        assert host._market_publisher.reconfigurations == 1
        assert host._market_publisher.enforced_stream_grants["principal-b"] == frozenset()
        _drain(b)  # queued before the rebuild: not recallable by the publisher
        after = [0, 0]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            publish_round()
            after[0] += _drain(a)
            after[1] += _drain(b)
            if after[0] >= 20 and host._market_publisher.denied_subscriptions >= 1:
                break
        assert after[0] > 0, "the other principal keeps the shared stream"
        assert after[1] == 0, "the revoked principal must not receive new frames"
        assert host._market_publisher.denied_subscriptions >= 1

        head = hub.head(shared)
        regrant = hub.grant_principal_keys("principal-b", {shared})
        assert regrant["transport_enforced"] is True
        assert hub.grant_floor("principal-b", shared) == (head["epoch"], head["head"])
        restored = 0
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not restored:
            publish_round()
            _drain(a)
            restored += _drain(b)
        assert restored > 0
    finally:
        for socket in sockets:
            socket.close()
        context.term()
        host.close()
