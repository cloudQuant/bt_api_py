"""Pure-fake checks for the opt-in market owner cleanup policy."""

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireScope

from bt_api_py.gateway_strategy_market import (
    GatewayMarketClientSettings,
    GatewayMarketOwner,
    GatewayMarketSubscriptionSettings,
)
from bt_api_py.service_market import ServiceMarketError
from bt_api_py.service_runtime import ClientCurveKeys
from bt_api_py.service_runtime_thread import OwnerOperationError


class FakeClient:
    def __init__(self, name, calls, *, error=None, flag=False, on_close=None):
        self.name, self.calls, self.error, self.flag, self.on_close = name, calls, error, flag, on_close

    def close(self):
        self.calls.append(self.name)
        if self.on_close:
            self.on_close()
        if self.error:
            raise self.error

    @property
    def unsubscription_uncertain(self):
        if isinstance(self.flag, BaseException):
            raise self.flag
        return self.flag


def owner(*, strict=True, guard=None, notify=None):
    instrument = MarketInstrumentKey(provider="synthetic", venue="unit", environment="paper",
        product_type="spot", instrument_id="asset-a", native_symbol="ASSET-A")
    key = MarketSubscriptionKey(source_ref="unit-source", entitlement_ref="unit-entitlement",
        instrument=instrument, channel="trades")
    settings = GatewayMarketClientSettings({"main": GatewayMarketSubscriptionSettings(key)})
    return GatewayMarketOwner(settings, stream_scope=WireScope("paper:unit"),
        security_profiles={"unit": ClientCurveKeys(b"a" * 40, b"b" * 40, b"c" * 40)},
        strict_cleanup=strict, cleanup_guard=guard, cleanup_uncertain_callback=notify)


@pytest.mark.parametrize("options", [
    {"error": KeyboardInterrupt()},
    {"error": ServiceMarketError("UNSUBSCRIPTION_UNCERTAIN")},
    {"flag": True}, {"flag": None}, {"flag": 0}, {"flag": RuntimeError()},
])
def test_strict_first_failure_retains_all_and_stops(options):
    calls, notices = [], []
    target = owner(notify=lambda: notices.append("sticky"))
    first = FakeClient("first", calls, **options)
    second = FakeClient("second", calls)
    target._clients = {"first": first, "second": second}
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.close()
    assert calls == ["first"] and target._clients == {"first": first, "second": second}
    assert target.cleanup_uncertainties >= 1 and notices == ["sticky"]
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.close()
    assert calls == ["first"] and notices == ["sticky"]


@pytest.mark.parametrize("sequence,expected_calls,first_retained", [
    ([True], [], True), ([False, True], ["first"], True),
    ([False, False, None], ["first"], True),
    ([False, False, False, RuntimeError()], ["first"], False)])
def test_guard_before_and_after_each_client(sequence, expected_calls, first_retained):
    calls, notices = [], []
    values = iter(sequence)
    def guard():
        value = next(values)
        if isinstance(value, BaseException):
            raise value
        return value
    target = owner(guard=guard, notify=lambda: notices.append(1))
    first, second = FakeClient("first", calls), FakeClient("second", calls)
    target._clients = {"first": first, "second": second}
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.close()
    assert calls == expected_calls and notices == [1]
    assert target._clients == ({"first": first, "second": second} if first_retained else {"second": second})


def test_reentrant_close_and_notification_do_not_touch_remaining_clients():
    calls, notices = [], []
    target = owner()
    def reenter():
        with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
            target.close()
    def notify():
        notices.append(1)
        reenter()
    target._cleanup_uncertain_callback = notify
    first, second = FakeClient("first", calls, on_close=reenter), FakeClient("second", calls)
    target._clients = {"first": first, "second": second}
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.close()
    assert calls == ["first"] and notices == [1]
    assert target._clients == {"first": first, "second": second}


def test_success_and_default_legacy_behavior():
    calls, guards = [], []
    target = owner(guard=lambda: guards.append(1) or False)
    target._clients = {name: FakeClient(name, calls) for name in ("first", "second")}
    target.close()
    target.close()
    assert calls == ["first", "second"] and len(guards) == 6
    assert not target._clients and target.cleanup_uncertainties == 0

    calls = []
    legacy = owner(strict=False, guard=lambda: True)
    legacy._clients = {"first": FakeClient("first", calls, flag=True),
                       "second": FakeClient("second", calls)}
    legacy.close()
    assert calls == ["first", "second"] and not legacy._clients
    assert legacy.cleanup_uncertainties == 1


def test_replaced_resource_is_not_deleted():
    calls = []
    target = owner()
    replacement = FakeClient("replacement", calls)
    first = FakeClient("first", calls, on_close=lambda: target._clients.__setitem__("first", replacement))
    second = FakeClient("second", calls)
    target._clients = {"first": first, "second": second}
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.close()
    assert target._clients == {"first": replacement, "second": second} and calls == ["first"]


def test_guard_reentry_and_callback_exception_stay_sticky():
    calls, notices = [], []
    target = owner()
    def guard():
        with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
            target.close()
        return False
    def notify():
        notices.append(1)
        raise KeyboardInterrupt()
    target._cleanup_guard = guard
    target._cleanup_uncertain_callback = notify
    first, second = FakeClient("first", calls), FakeClient("second", calls)
    target._clients = {"first": first, "second": second}
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.close()
    assert not calls and notices == [1]
    assert target._clients == {"first": first, "second": second}


def test_concurrent_reentry_is_sticky_before_running_notification():
    import threading
    entered, release, notified = threading.Event(), threading.Event(), threading.Event()
    calls, errors = [], []
    target = owner(notify=notified.set)
    def wait_inside_close():
        entered.set()
        assert release.wait(2)
    first = FakeClient("first", calls, on_close=wait_inside_close)
    second = FakeClient("second", calls)
    target._clients = {"first": first, "second": second}
    def run():
        try:
            target.close()
        except OwnerOperationError as exc:
            errors.append(exc.code)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
            target.close()
        assert notified.is_set()
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and errors == ["LOCAL_CLOSE_FAILED"]
    assert calls == ["first"] and target._clients == {"first": first, "second": second}


def test_strict_start_reentrant_close_retains_constructed_client_and_stops(monkeypatch):
    from dataclasses import replace
    import gc
    import weakref
    from bt_api_py import gateway_strategy_market as market

    calls, notices, references = [], [], []
    target = owner()
    original = target._settings.subscriptions["main"].key
    target._settings = GatewayMarketClientSettings({
        name: GatewayMarketSubscriptionSettings(replace(original, source_ref="source-" + name))
        for name in ("first", "second", "third")
    })
    def notify():
        notices.append("sticky")
        with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
            target.close()
    target._cleanup_uncertain_callback = notify
    monkeypatch.setattr(market, "ZmqMarketTransportFactory", lambda profiles: object())
    def construct(raw, mount, key, **kwargs):
        name = key.source_ref.removeprefix("source-")
        calls.append("construct-" + name)
        client = FakeClient(name, calls)
        references.append(weakref.ref(client))
        if name == "second":
            with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
                target.close()
        return client
    monkeypatch.setattr(market, "ServiceMarketClient", construct)
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.start(object())
    gc.collect()
    assert calls == ["construct-first", "construct-second"] and notices == ["sticky"]
    assert references[0]() is target._clients["first"]
    assert references[1]() is target._cleanup_inflight[0][1]
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.close()
    assert calls == ["construct-first", "construct-second"] and notices == ["sticky"]


def test_strict_start_factory_reentrant_close_stops_before_client(monkeypatch):
    from bt_api_py import gateway_strategy_market as market
    calls, notices = [], []
    target = owner(notify=lambda: notices.append("sticky"))
    def factory(profiles):
        with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
            target.close()
        return object()
    monkeypatch.setattr(market, "ZmqMarketTransportFactory", factory)
    monkeypatch.setattr(market, "ServiceMarketClient", lambda *a, **kw: calls.append("construct"))
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.start(object())
    assert not calls and notices == ["sticky"]


def test_strict_start_success_then_close_uses_guard(monkeypatch):
    from bt_api_py import gateway_strategy_market as market
    calls, guards = [], []
    target = owner(guard=lambda: guards.append("guard") or False)
    monkeypatch.setattr(market, "ZmqMarketTransportFactory", lambda profiles: object())
    monkeypatch.setattr(market, "ServiceMarketClient", lambda *a, **kw: FakeClient("client", calls))
    target.start(object())
    target.close()
    assert calls == ["client"] and len(guards) == 5 and not target._clients


def test_concurrent_close_during_strict_constructor_retains_result(monkeypatch):
    import threading
    from bt_api_py import gateway_strategy_market as market
    entered, release = threading.Event(), threading.Event()
    calls, errors, notices = [], [], []
    target = owner(notify=lambda: notices.append("sticky"))
    client = FakeClient("client", calls)
    def construct(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return client
    monkeypatch.setattr(market, "ZmqMarketTransportFactory", lambda profiles: object())
    monkeypatch.setattr(market, "ServiceMarketClient", construct)
    def run():
        try:
            target.start(object())
        except OwnerOperationError as exc:
            errors.append(exc.code)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
            target.close()
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and errors == ["LOCAL_CLOSE_FAILED"]
    assert target._cleanup_inflight == [("main", client)] and not calls and notices == ["sticky"]


def test_close_after_last_registration_before_start_finalization(monkeypatch):
    import threading
    from bt_api_py import gateway_strategy_market as market

    registered, release = threading.Event(), threading.Event()
    calls, errors, notices = [], [], []
    target = owner(notify=lambda: notices.append("sticky"))
    client = FakeClient("client", calls)

    class GateLock:
        def __init__(self):
            self.lock = threading.Lock()
            self.gate_thread = None

        def __enter__(self):
            self.lock.acquire()
            return self

        def __exit__(self, *args):
            gate = self.gate_thread == threading.get_ident()
            if gate:
                self.gate_thread = None
            self.lock.release()
            if gate:
                registered.set()
                assert release.wait(2)

    gate = GateLock()
    class RegisteringClients(dict):
        def __setitem__(self, name, value):
            super().__setitem__(name, value)
            gate.gate_thread = threading.get_ident()

    target._cleanup_lock = gate
    target._clients = RegisteringClients()
    monkeypatch.setattr(market, "ZmqMarketTransportFactory", lambda profiles: object())
    monkeypatch.setattr(market, "ServiceMarketClient", lambda *a, **kw: client)
    def run():
        try:
            target.start(object())
        except OwnerOperationError as exc:
            errors.append(exc.code)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert registered.wait(2)
        with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
            target.close()
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and errors == ["LOCAL_CLOSE_FAILED"]
    assert target._clients == {"main": client} and not calls and notices == ["sticky"]
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.close()
    assert not calls and notices == ["sticky"]


def test_strict_start_global_unknown_during_constructor_retains_and_stops(monkeypatch):
    from dataclasses import replace
    from bt_api_py import gateway_strategy_market as market

    state, calls, notices = {"unknown": False}, [], []
    target = owner(guard=lambda: state["unknown"], notify=lambda: notices.append("sticky"))
    original = target._settings.subscriptions["main"].key
    target._settings = GatewayMarketClientSettings({
        name: GatewayMarketSubscriptionSettings(replace(original, source_ref="source-" + name))
        for name in ("first", "second")
    })
    first = FakeClient("first", calls)
    def construct(raw, mount, key, **kwargs):
        calls.append("construct-" + key.source_ref)
        state["unknown"] = True
        return first
    monkeypatch.setattr(market, "ZmqMarketTransportFactory", lambda profiles: object())
    monkeypatch.setattr(market, "ServiceMarketClient", construct)
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.start(object())
    assert calls == ["construct-source-first"] and notices == ["sticky"]
    assert target._cleanup_inflight == [("first", first)] and not target._clients
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.close()
    assert calls == ["construct-source-first"]


def test_strict_close_global_unknown_skips_client_property():
    state, calls, notices = {"unknown": False}, [], []
    target = owner(guard=lambda: state["unknown"], notify=lambda: notices.append("sticky"))
    class PublishingClient:
        def close(self):
            calls.append("close")
            state["unknown"] = True
        @property
        def unsubscription_uncertain(self):
            calls.append("property")
            return False
    first, second = PublishingClient(), FakeClient("second", calls)
    target._clients = {"first": first, "second": second}
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.close()
    assert calls == ["close"] and notices == ["sticky"]
    assert target._clients == {"first": first, "second": second}


@pytest.mark.parametrize("method,arguments", [
    ("start", {}), ("recover", {}),
    ("accept_resync", {"acknowledge_data_loss": True}),
    ("resync_after_regrant", {}),
])
def test_strict_factory_operations_rejected_without_client_access(method, arguments):
    class NoClientAccess:
        def __getattribute__(self, name):
            raise AssertionError("client accessed: " + name)
    target = owner()
    target._clients = {"main": NoClientAccess()}
    arguments = dict(arguments, _consumer_token="a" * 32)
    with pytest.raises(OwnerOperationError, match="STRICT_MARKET_FACTORY_UNAVAILABLE"):
        target.invoke("main." + method, arguments)
    assert not target._consumers
    target._closed = True
    with pytest.raises(OwnerOperationError, match="OWNER_CLOSED"):
        target.invoke("main." + method, arguments)


def test_strict_retains_poll_and_status_observation():
    calls = []
    class ReadClient:
        healthy = True
        recovery_required = False
        resync_required = False
        authorization_interrupted = None
        authorization_generation = 0
        queue_count = 0
        queue_bytes = 0
        unsubscription_uncertain = False
        last_bootstrap = None
        source_descriptor = None
        def poll(self, timeout_ms):
            calls.append("poll")
            return None
        def poll_with_receipt(self, timeout_ms):
            calls.append("poll_receipt")
            return None
    target = owner()
    target._clients = {"main": ReadClient()}
    for method in ("poll", "poll_receipt"):
        assert target.invoke("main." + method, {"_consumer_token": "a" * 32}) is None
    assert target.invoke("main.status", {})["healthy"] is True
    assert calls == ["poll", "poll_receipt"]


def test_strict_retains_clock_and_history_dispatch(monkeypatch):
    from bt_api_py import gateway_strategy_market as market
    calls = []
    class ReadClient:
        def clock_observation(self, **kwargs):
            calls.append("clock")
            raise ServiceMarketError("FAKE_CLOCK")
        def history(self, request):
            calls.append("history")
            raise ServiceMarketError("FAKE_HISTORY")
    class FakeHistoryRequest:
        @classmethod
        def from_dict(cls, value):
            return value
    target = owner()
    original = target._settings
    target._settings = GatewayMarketClientSettings(original.subscriptions,
        original.requested_operations | {"market.clock_observation", "market.history"})
    target._clients = {"main": ReadClient()}
    monkeypatch.setattr(market, "MarketHistoryRequest", FakeHistoryRequest)
    for method, arguments, code in (("clock_observation", {}, "FAKE_CLOCK"),
                                    ("history", {"request": {}}, "FAKE_HISTORY")):
        with pytest.raises(OwnerOperationError, match=code):
            target.invoke("main." + method, dict(arguments, _consumer_token="a" * 32))
    assert calls == ["clock", "history"]


def test_legacy_start_still_reaches_binding():
    calls = []
    class LegacyClient:
        def _get_binding(self):
            calls.append("binding")
            raise ServiceMarketError("FAKE_BINDING")
    target = owner(strict=False)
    target._clients = {"main": LegacyClient()}
    with pytest.raises(OwnerOperationError, match="FAKE_BINDING"):
        target.invoke("main.start", {"_consumer_token": "a" * 32})
    assert calls == ["binding"]


def test_actual_strict_client_empty_close_uses_no_binding_or_factory(monkeypatch):
    from bt_api_py import gateway_strategy_market as market
    from bt_api_py.service_runtime import ServiceClientRuntime
    from bt_api_py.service_market import ServiceMarketClient

    calls, notices = [], []
    guard = lambda: False
    notify = lambda: notices.append("sticky")
    target = owner(guard=guard, notify=notify)
    runtime = object.__new__(ServiceClientRuntime)
    def forbidden(*args, **kwargs):
        calls.append("external")
        raise AssertionError("external access")
    monkeypatch.setattr(market, "ZmqMarketTransportFactory", lambda profiles: forbidden)
    monkeypatch.setattr(ServiceMarketClient, "_get_binding", forbidden)
    monkeypatch.setattr(ServiceClientRuntime, "call", forbidden)
    target.start(runtime)
    client = target._clients["main"]
    assert type(client) is ServiceMarketClient and client._strict_cleanup is True
    assert client._cleanup_guard is guard and client._cleanup_uncertain_callback is notify
    for method, args in (("start", {}), ("recover", {}),
                         ("accept_resync", {"acknowledge_data_loss": True}),
                         ("resync_after_regrant", {})):
        with pytest.raises(OwnerOperationError, match="STRICT_MARKET_FACTORY_UNAVAILABLE"):
            target.invoke("main." + method, dict(args, _consumer_token="a" * 32))
    target.close()
    assert client._state == "closed" and not target._clients
    assert not calls and not notices


def test_actual_strict_client_nonempty_close_retains_subscriber(monkeypatch):
    from bt_api_py import gateway_strategy_market as market
    from bt_api_py.service_runtime import ServiceClientRuntime
    from bt_api_py.service_market import ServiceMarketClient

    calls, notices = [], []
    target = owner(guard=lambda: False, notify=lambda: notices.append("sticky"))
    runtime = object.__new__(ServiceClientRuntime)
    def forbidden(*args, **kwargs):
        calls.append("external")
        raise AssertionError("external access")
    class Subscriber:
        def close(self):
            calls.append("subscriber.close")
    subscriber = Subscriber()
    monkeypatch.setattr(market, "ZmqMarketTransportFactory", lambda profiles: forbidden)
    monkeypatch.setattr(ServiceMarketClient, "_get_binding", forbidden)
    monkeypatch.setattr(ServiceClientRuntime, "call", forbidden)
    target.start(runtime)
    client = target._clients["main"]
    # Synthetic contamination only: no subscriber has been acquired.
    client._state = "started"
    client._subscriber = subscriber
    client._remote_active = True
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.close()
    assert target._clients["main"] is client and client._subscriber is subscriber
    assert client._remote_active is True and calls == [] and notices == ["sticky"]
    assert target.cleanup_uncertainties >= 1
    with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
        target.close()
    assert calls == [] and notices == ["sticky"]


def test_legacy_constructor_receives_no_strict_options(monkeypatch):
    from bt_api_py import gateway_strategy_market as market
    received = []
    target = owner(strict=False)
    monkeypatch.setattr(market, "ZmqMarketTransportFactory", lambda profiles: object())
    def construct(*args, **kwargs):
        received.append(kwargs)
        return FakeClient("main", [])
    monkeypatch.setattr(market, "ServiceMarketClient", construct)
    target.start(object())
    assert len(received) == 1
    assert not {"strict_cleanup", "cleanup_guard", "cleanup_uncertain_callback"} & received[0].keys()
