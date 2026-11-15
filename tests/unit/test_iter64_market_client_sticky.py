"""In-memory checks for the unavailable strict market producer contract."""

import pytest

from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireScope
from bt_api_py.service_market import (
    ServiceMarketBootstrapOperation,
    ServiceMarketClient,
    ServiceMarketError,
)
from bt_api_py.service_runtime import ServiceClientRuntime


KEY = MarketSubscriptionKey(
    source_ref="synthetic",
    entitlement_ref="public",
    instrument=MarketInstrumentKey(
        provider="synthetic", venue="unit", environment="paper", product_type="spot",
        instrument_id="asset", native_symbol="ASSET",
    ),
    channel="trades",
)
SCOPE = WireScope("paper:unit")


class Subscriber:
    def __init__(self, calls):
        self.calls = calls

    def close(self):
        self.calls.append("old close")

    def receive(self, **kwargs):
        self.calls.append("receive")


def make_client(*, strict=True):
    calls = []

    def factory(*args):
        calls.append("factory")
        return Subscriber(calls)

    def guard():
        calls.append("guard")
        return False

    def callback():
        calls.append("callback")

    client = ServiceMarketClient(
        object.__new__(ServiceClientRuntime), "market", KEY,
        subscriber_factory=factory, stream_scope=SCOPE, strict_cleanup=strict,
        cleanup_guard=guard, cleanup_uncertain_callback=callback,
    )
    client._get_binding = lambda **kwargs: calls.append("binding")
    client._get_history_binding = lambda: calls.append("history binding")
    client._rpc = lambda *args, **kwargs: calls.append("rpc")
    return client, calls


@pytest.mark.parametrize("operation", [
    lambda c: c.start(),
    lambda c: c.begin_start(),
    lambda c: c.recover(),
    lambda c: c.begin_recover(),
    lambda c: c.resync_after_regrant(),
    lambda c: c.begin_resync_after_regrant(),
    lambda c: c.accept_resync(acknowledge_data_loss=True),
    lambda c: ServiceMarketBootstrapOperation((c,), mode="start"),
    lambda c: ServiceMarketBootstrapOperation((c,), mode="recover"),
    lambda c: ServiceMarketBootstrapOperation((c,), mode="regrant"),
])
def test_strict_operations_reject_before_external_calls(operation):
    client, calls = make_client()
    subscriber = Subscriber(calls)
    client._subscriber = subscriber
    with pytest.raises(ServiceMarketError, match="^STRICT_MARKET_FACTORY_UNAVAILABLE$"):
        operation(client)
    assert calls == []
    assert client._subscriber is subscriber


@pytest.mark.parametrize("batch", ["strict-first", "strict-last", "both-strict"])
@pytest.mark.parametrize("entry", [ServiceMarketClient.start_many, ServiceMarketClient.begin_start_many])
def test_batch_with_strict_client_rejects_before_any_binding(batch, entry):
    first, first_calls = make_client(strict=batch != "strict-last")
    second, second_calls = make_client(strict=batch != "strict-first")
    with pytest.raises(ServiceMarketError, match="^STRICT_MARKET_FACTORY_UNAVAILABLE$"):
        entry((first, second))
    assert first_calls == second_calls == []


def test_empty_strict_close_is_idempotent_without_external_calls():
    client, calls = make_client()
    client.close()
    client.close()
    assert client._state == "closed"
    assert calls == []


@pytest.mark.parametrize("field,value", [
    ("_subscriber", "subscriber"), ("_binding", object()),
    ("_bootstrap_operation", object()), ("_bootstrap_extra_subscribers", [object()]),
    ("_remote_active", True), ("_remote_unknown", True),
    ("_unsubscription_uncertain", True), ("_uncertain_registration_id", "old"),
    ("_source_descriptor", object()), ("_state", "failed"),
    ("_pending_poll_replay", ("epoch", 1)), ("_last_bootstrap", object()),
    ("_cursor_epoch", "old"), ("_cursor_sequence", 1),
    ("_queue_bytes", 1), ("_authorization_generation", 1),
])
def test_strict_close_retains_every_existing_or_uncertain_resource(field, value):
    client, calls = make_client()
    if value == "subscriber":
        value = Subscriber(calls)
    setattr(client, field, value)
    with pytest.raises(ServiceMarketError, match="^STRICT_MARKET_CLEANUP_UNAVAILABLE$"):
        client.close()
    assert getattr(client, field) is value
    assert calls == []


def test_default_legacy_start_reaches_existing_binding_path():
    calls = []
    client = ServiceMarketClient(
        object.__new__(ServiceClientRuntime), "market", KEY,
        subscriber_factory=lambda *args: None, stream_scope=SCOPE,
    )
    client._get_binding = lambda **kwargs: calls.append("binding")
    with pytest.raises((AttributeError, ServiceMarketError)):
        client.start()
    assert calls == ["binding"]


@pytest.mark.parametrize("method", ["step", "cancel"])
def test_retained_operation_cannot_send_or_abandon_in_strict_mode(method):
    client, calls = make_client()
    operation = object.__new__(ServiceMarketBootstrapOperation)
    operation.clients = (client,)
    operation.done = False
    with pytest.raises(ServiceMarketError, match="^STRICT_MARKET_FACTORY_UNAVAILABLE$"):
        getattr(operation, method)()
    assert operation.done is False
    assert calls == []


def test_untrusted_guard_and_callback_are_not_a_factory_contract():
    client, calls = make_client()
    with pytest.raises(ServiceMarketError, match="^STRICT_MARKET_FACTORY_UNAVAILABLE$"):
        client.start()
    assert calls == []


@pytest.mark.parametrize("operation,code", [
    (lambda c: c.poll(), "CLIENT_NOT_STARTED"),
    (lambda c: c.poll_with_receipt(), "CLIENT_NOT_STARTED"),
    (lambda c: c.clock_observation(), "CLOCK_STREAM_UNAVAILABLE"),
    (lambda c: c.history(None), "INVALID_HISTORY_REQUEST"),
    (lambda c: c.drain(), "CLIENT_NOT_STARTED"),
])
def test_strict_observation_keeps_existing_entry_behavior(operation, code):
    client, calls = make_client()
    with pytest.raises(ServiceMarketError, match=f"^{code}$"):
        operation(client)
    assert calls == []


@pytest.mark.parametrize("operation", [lambda c: c.poll(), lambda c: c.drain()])
def test_strict_existing_stream_observation_reaches_maintenance(operation):
    client, calls = make_client()
    client._state = "started"

    def maintenance():
        calls.append("maintenance")
        raise RuntimeError("test sentinel")

    client._maintain_runtime = maintenance
    with pytest.raises(RuntimeError, match="test sentinel"):
        operation(client)
    assert calls == ["maintenance"]
