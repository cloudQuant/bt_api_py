"""Trusted Host wiring for partitioned market audiences (local fakes)."""

import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
import zmq
from bt_api_base.service_protocol import (
    WIRE_PROTOCOL_VERSION,
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
)
from bt_api_gateway.market_hub import GatewayMarketHub
from bt_api_transport_zmq import market_v2

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError
from bt_api_py.service_registry import PrincipalGrant
from tests.unit.test_service_host import (
    _endpoint,
    _market_authority,
    _market_hub_component,
    _market_key,
    _service_config,
)


class Provider:
    def subscribe(self, key, *, generation):
        return key

    def unsubscribe(self, handle):
        return True


def _config(version):
    config = _service_config(
        {"gateway": _market_hub_component()},
        service_id="market-service",
        market_endpoint=_endpoint(),
    )
    raw = config.to_public_dict()
    raw["communication"]["profiles"]["listener-v1"]["options"]["market_wire_version"] = version
    return parse_service_config(raw)


def _resources(authority):
    first = _market_key()
    second = replace(first, source_ref="another-source")
    hub = GatewayMarketHub(
        Provider(),
        key_universe=(first, second),
        principal_allowed_keys={"curve-principal": {first}},
        service_identity=authority.identity,
        service_scope=authority.scope,
    )
    return ComponentResources.owned({"hub": hub}, ready=lambda: True, close=hub.close), first


def test_partitioned_host_wires_exact_server_owned_grants(tmp_path, monkeypatch):
    config = _config(2)
    authority, _ = _market_authority(config)
    resources, key = _resources(authority)
    calls = []

    class Publisher:
        def __init__(self, endpoint, *_args, **kwargs):
            self.endpoint = endpoint
            self.enforced_stream_grants = kwargs.get("stream_grants")
            calls.append(kwargs)

        def publish(self, _event):
            pass

        def close(self):
            pass

    monkeypatch.setattr(market_v2, "ZmqMarketPublisher", Publisher)
    host = ServiceHost(config, authority, tmp_path / "registry.sqlite", lambda *_: resources)
    try:
        host.start()
        assert len(calls) == 1
        assert calls[0]["wire_version"] == 2
        assert calls[0]["stream_grants"] == {"curve-principal": frozenset({key.stream_id})}
    finally:
        host.close()


def test_partitioned_audience_rejected_before_listener_for_legacy_wire(tmp_path, monkeypatch):
    config = _config(1)
    authority, _ = _market_authority(config)
    resources, _ = _resources(authority)
    calls = []
    monkeypatch.setattr(market_v2, "ZmqMarketPublisher", lambda *_a, **_kw: calls.append(True))
    host = ServiceHost(config, authority, tmp_path / "registry.sqlite", lambda *_: resources)
    try:
        with pytest.raises(ServiceHostError, match="MARKET_HUB_AUDIENCE_INVALID"):
            host.start()
        assert not calls
    finally:
        host.close()


def test_partitioned_host_rejects_publisher_without_enforced_grants(tmp_path, monkeypatch):
    config = _config(2)
    authority, _ = _market_authority(config)
    resources, _ = _resources(authority)
    closed = []

    class NonEnforcingPublisher:
        def __init__(self, endpoint, *_args, **_kwargs):
            self.endpoint = endpoint
            self.enforced_stream_grants = None

        def publish(self, _event):
            raise AssertionError("a non-enforcing publisher must never be bound")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(market_v2, "ZmqMarketPublisher", NonEnforcingPublisher)
    host = ServiceHost(config, authority, tmp_path / "registry.sqlite", lambda *_: resources)
    try:
        with pytest.raises(ServiceHostError, match="MARKET_HUB_BINDING_FAILED"):
            host.start()
        assert closed == [True]
    finally:
        host.close()


def test_partitioned_host_actual_curve_publisher_filters_raw_subscribers(tmp_path):
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
    key_b = replace(key_a, source_ref="another-source")
    hub = GatewayMarketHub(
        Provider(),
        key_universe=(key_a, key_b),
        principal_allowed_keys={"curve-principal": {key_a}, "principal-b": {key_b}},
        service_identity=authority.identity,
        service_scope=authority.scope,
    )
    resources = ComponentResources.owned({"hub": hub}, ready=lambda: True, close=hub.close)
    host = ServiceHost(config, authority, tmp_path / "registry.sqlite", lambda *_: resources)
    context = zmq.Context()
    sockets = []
    try:
        host.start()
        topic_a = market_v2._market_topic_for_stream_id(key_a.stream_id)
        topic_b = market_v2._market_topic_for_stream_id(key_b.stream_id)
        for public, secret in ((public_a, secret_a), (public_b, secret_b)):
            subscriber = context.socket(zmq.SUB)
            subscriber.linger = 0
            subscriber.curve_serverkey = authority.curve_public_key
            subscriber.curve_publickey = public
            subscriber.curve_secretkey = secret
            # Both raw subscribers request both streams. Receiver-side
            # filtering must not be the source of authorization.
            subscriber.setsockopt(zmq.SUBSCRIBE, topic_a)
            subscriber.setsockopt(zmq.SUBSCRIBE, topic_b)
            subscriber.connect(host.market_endpoint)
            sockets.append(subscriber)
        received = [set(), set()]
        deadline = time.monotonic() + 4
        sequence = 0
        while time.monotonic() < deadline:
            sequence += 1
            for key in (key_a, key_b):
                event = WireEnvelope(
                    header=WireHeader(
                        protocol_version=WIRE_PROTOCOL_VERSION,
                        kind=WireMessageKind.EVENT,
                        message_id=uuid.uuid4().hex,
                        correlation_id=None,
                        identity=authority.identity,
                        scope=authority.scope,
                        operation="market.event",
                        schema_id="sdk.market.event",
                        schema_version=1,
                        sent_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        stream_id=key.stream_id,
                        stream_epoch="test-epoch",
                        sequence=sequence,
                    ),
                    payload={"key": key.to_dict(), "data": {"last": 10}},
                )
                assert host._market_publisher.try_publish(event)
            for socket, seen in zip(sockets, received, strict=True):
                while socket.poll(1, zmq.POLLIN):
                    topic, _ = socket.recv_multipart()
                    seen.add(topic)
            if received[0] and received[1] and host._market_publisher.denied_subscriptions >= 2:
                break
        assert received == [{topic_a}, {topic_b}]
        assert host._market_publisher.denied_subscriptions >= 2
    finally:
        for socket in sockets:
            socket.close()
        context.term()
        host.close()
