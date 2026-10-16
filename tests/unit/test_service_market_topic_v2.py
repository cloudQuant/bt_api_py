"""Market wire-version subscriber factory dispatch unit tests."""

from __future__ import annotations

import sys
from dataclasses import replace
from types import ModuleType, SimpleNamespace

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py import service_market
from bt_api_py.service_market import (
    MARKET_WIRE_VERSION_LEGACY,
    MARKET_WIRE_VERSION_TOPIC_FILTERED,
    ServiceMarketClient,
    ServiceMarketError,
    ZmqMarketTransportFactory,
    _same_binding,
)
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceMarketBindingView,
)

_PEER = WireIdentity(WireRole.SERVICE, "market-service", "market-service")
_SCOPE = WireScope("paper:market-v2")
_KEY = MarketSubscriptionKey(
    source_ref="source:synthetic",
    entitlement_ref="entitlement:public",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="unit",
        environment="paper",
        product_type="spot",
        instrument_id="asset-a",
        native_symbol="ASSET-A",
    ),
    channel="trades",
)


class FakeSubscriber:
    def receive(self, *, timeout=None):
        del timeout
        return None

    def close(self):
        pass


def _binding(version: int = MARKET_WIRE_VERSION_LEGACY) -> ServiceMarketBindingView:
    # These tests inject a frozen binding view to isolate dispatch; they do
    # not prove the control-plane version negotiation.
    legacy = ServiceMarketBindingView(
        mount="store.market",
        service_id="market-service",
        instance_id="market-instance",
        scope_ref="scope:market",
        registration_id="registration:market",
        service_epoch="market-epoch-1",
        rpc_scope=_SCOPE,
        operations=frozenset({"market.subscribe"}),
        transport_ref="profile:market",
        timeout=1.0,
        peer=_PEER,
        market_endpoint="tcp://127.0.0.1:7712",
        security_profile_ref="keys:market",
        high_water_mark=64,
        max_message_bytes=65_536,
    )
    if version == MARKET_WIRE_VERSION_LEGACY:
        return legacy
    return replace(legacy, market_wire_version=version)


def _bare_client(factory):
    client = object.__new__(ServiceMarketClient)
    client._subscriber_factory = factory
    client.stream_scope = _SCOPE
    client.key = _KEY
    client._subscriber = None
    client._state = "new"
    return client


class LegacyFactory:
    def __init__(self):
        self.calls = []
        self.subscriber = FakeSubscriber()

    def __call__(self, binding, scope):
        self.calls.append((binding, scope))
        return self.subscriber


class TopicV2Factory:
    def __init__(self):
        self.calls = []
        self.subscriber = FakeSubscriber()

    def create_topic_v2(self, binding, scope, subscription_key):
        self.calls.append((binding, scope, subscription_key))
        return self.subscriber


def test_legacy_binding_keeps_two_argument_factory_protocol():
    binding = _binding()
    factory = LegacyFactory()

    subscriber = _bare_client(factory)._make_subscriber(binding)

    assert subscriber is factory.subscriber
    assert factory.calls == [(binding, _SCOPE)]
    assert getattr(binding, "market_wire_version", MARKET_WIRE_VERSION_LEGACY) == 1


def test_v2_binding_uses_only_typed_factory_and_exact_key():
    binding = _binding(MARKET_WIRE_VERSION_TOPIC_FILTERED)
    factory = TopicV2Factory()
    client = _bare_client(factory)

    subscriber = client._make_subscriber(binding)

    assert subscriber is factory.subscriber
    assert factory.calls == [(binding, _SCOPE, client.key)]
    assert factory.calls[0][2] is client.key


def test_facade_accepts_a_typed_factory_without_legacy_callability():
    factory = TopicV2Factory()
    client = ServiceMarketClient(
        object.__new__(ServiceClientRuntime),
        "store.market",
        _KEY,
        subscriber_factory=factory,
        stream_scope=_SCOPE,
    )

    assert client._subscriber_factory is factory


def test_v2_binding_rejects_legacy_factory_without_calling_it():
    binding = _binding(MARKET_WIRE_VERSION_TOPIC_FILTERED)
    factory = LegacyFactory()

    with pytest.raises(ServiceMarketError) as error:
        _bare_client(factory)._make_subscriber(binding)

    assert error.value.code == "TOPIC_V2_SUBSCRIBER_FACTORY_UNSUPPORTED"
    assert factory.calls == []


def test_binding_version_change_requires_runtime_recovery():
    assert _same_binding(_binding(1), _binding(1))
    assert not _same_binding(_binding(1), _binding(2))


@pytest.mark.parametrize("version", [0, 3, True, "2"])
def test_unknown_wire_version_fails_closed_before_factory(version):
    binding = replace(_binding(), market_wire_version=version)
    factory = LegacyFactory()

    with pytest.raises(ServiceMarketError) as error:
        _bare_client(factory)._make_subscriber(binding)

    assert error.value.code == "UNSUPPORTED_MARKET_WIRE_VERSION"
    assert factory.calls == []


def test_zmq_factory_forwards_v2_version_and_subscription_key(monkeypatch):
    created = []

    class Credentials:
        def __init__(self, server_key, public_key, secret_key):
            self.server_key = server_key
            self.public_key = public_key
            self.secret_key = secret_key

    def construct(*args, **kwargs):
        result = FakeSubscriber()
        created.append((args, kwargs, result))
        return result

    class ContextPool:
        pass

    market_module = ModuleType("bt_api_transport_zmq.market_v2")
    market_module.ZmqMarketContextPool = ContextPool
    market_module.ZmqMarketSubscriber = construct
    server_module = ModuleType("bt_api_transport_zmq.server")
    server_module.CurveClientCredentials = Credentials
    service_module = ModuleType("bt_api_transport_zmq.service_v2")

    class StartupCleanupError(RuntimeError):
        pass

    service_module.ServiceTransportStartupCleanupError = StartupCleanupError
    monkeypatch.setitem(sys.modules, "bt_api_transport_zmq.market_v2", market_module)
    monkeypatch.setitem(sys.modules, "bt_api_transport_zmq.server", server_module)
    monkeypatch.setitem(sys.modules, "bt_api_transport_zmq.service_v2", service_module)

    keys = ClientCurveKeys(b"server", b"public", b"secret")
    factory = ZmqMarketTransportFactory({"keys:market": keys})
    binding = _binding(MARKET_WIRE_VERSION_TOPIC_FILTERED)

    subscriber = factory.create_topic_v2(binding, _SCOPE, _KEY)

    assert subscriber is created[0][2]
    args, kwargs, _ = created[0]
    assert args == (binding.market_endpoint, _PEER, _SCOPE)
    assert kwargs["wire_version"] == MARKET_WIRE_VERSION_TOPIC_FILTERED
    assert kwargs["subscription_key"] is _KEY
    assert kwargs["credentials"].server_key == b"server"
    assert type(kwargs["context_pool"]) is ContextPool

    legacy_subscriber = factory(_binding(), _SCOPE)
    assert legacy_subscriber is created[1][2]
    assert set(created[1][1]) == {
        "credentials",
        "high_water_mark",
        "max_message_bytes",
        "context_pool",
    }
    assert created[1][1]["context_pool"] is kwargs["context_pool"]
    with pytest.raises(ServiceMarketError) as error:
        factory(binding, _SCOPE)
    assert error.value.code == "TOPIC_V2_FACTORY_METHOD_REQUIRED"
    assert len(created) == 2

    def legacy_transport(*args, **kwargs):
        if "wire_version" in kwargs or "subscription_key" in kwargs:
            raise TypeError("legacy constructor has no topic-v2 keywords")
        return FakeSubscriber()

    market_module.ZmqMarketSubscriber = legacy_transport
    legacy_only_factory = ZmqMarketTransportFactory({"keys:market": keys})
    assert legacy_only_factory(_binding(), _SCOPE) is not None
    with pytest.raises(ServiceMarketError) as error:
        legacy_only_factory.create_topic_v2(binding, _SCOPE, _KEY)
    assert error.value.code == "TOPIC_V2_TRANSPORT_UNSUPPORTED"


def test_zmq_factory_rejects_inherited_lock_before_acquiring_it(monkeypatch):
    factory = ZmqMarketTransportFactory({})
    owner_pid = factory._owner_pid

    class InheritedLockedLock:
        def __enter__(self):
            raise AssertionError("forked factory blocked on an inherited init lock")

        def __exit__(self, *_args):
            return None

    factory._context_pool_init_lock = InheritedLockedLock()
    monkeypatch.setattr(
        service_market,
        "os",
        SimpleNamespace(getpid=lambda: owner_pid + 1),
    )
    with pytest.raises(ServiceMarketError) as error:
        factory._create_subscriber(
            _binding(),
            _SCOPE,
            wire_version=MARKET_WIRE_VERSION_LEGACY,
            subscription_key=None,
        )

    assert error.value.code == "PROCESS_BOUNDARY"
