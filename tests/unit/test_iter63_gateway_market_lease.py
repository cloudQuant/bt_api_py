"""Explicit per-name Gateway market registration leases."""

import threading
from types import SimpleNamespace

import pytest
from bt_api_base.service_protocol import WireScope

from bt_api_py import gateway_strategy_market as module
from bt_api_py.gateway_strategy_market import (
    GatewayMarketClientSettings,
    GatewayMarketOwner,
    GatewayMarketSubscriptionSettings,
    GatewayStrategyMarketClient,
)
from bt_api_py.service_market import ServiceMarketError
from bt_api_py.service_runtime import ClientCurveKeys
from bt_api_py.service_runtime_thread import (
    OwnerOperationError,
    ThreadedRuntimeError,
    ThreadedServiceClientRuntime,
)
from tests.unit.test_gateway_strategy_market import _KEY, _OPERATIONS
from tests.unit.test_service_market import (
    _OTHER_KEY,
    FakeSubscriberFactory,
    _make_client,
)

_PROFILES = {"profile": ClientCurveKeys(b"a" * 40, b"b" * 40, b"c" * 40)}


def _settings(*, two=False):
    subscriptions = {"main": GatewayMarketSubscriptionSettings(_KEY)}
    if two:
        subscriptions["other"] = GatewayMarketSubscriptionSettings(_OTHER_KEY)
    return GatewayMarketClientSettings(subscriptions, _OPERATIONS)


class _FakeServiceMarketClient:
    made = []

    def __init__(self, *_args, **_kwargs):
        self.close_calls = 0
        self.close_error = None
        self.unsubscription_uncertain = False
        self.operations = frozenset()
        self.cursor = object()
        type(self).made.append(self)

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error

    def _get_binding(self):
        return SimpleNamespace(operations=self.operations)

    def start(self):
        return object()


def _owner(monkeypatch, settings=None, *, strict_cleanup=False):
    _FakeServiceMarketClient.made.clear()
    monkeypatch.setattr(module, "ServiceMarketClient", _FakeServiceMarketClient)
    monkeypatch.setattr(module, "ZmqMarketTransportFactory", lambda _profiles: object())
    monkeypatch.setattr(module, "_bootstrap_to_wire", lambda _bootstrap: {"started": True})
    owner = GatewayMarketOwner(
        settings or _settings(two=True),
        stream_scope=WireScope("paper:market-unit"),
        security_profiles=_PROFILES,
        strict_cleanup=strict_cleanup,
    )
    owner.start(object())
    return owner


def _claim(owner, name, token):
    return owner.invoke(f"{name}.claim", {"_consumer_token": token})


def _release(owner, name, token, generation):
    return owner.invoke(
        f"{name}.release",
        {"_consumer_token": token, "_lease_generation": generation},
    )


def test_owner_releases_only_exact_registration_and_allows_gap_cleanup(monkeypatch):
    owner = _owner(monkeypatch)
    main, other = owner._clients["main"], owner._clients["other"]
    generation = _claim(owner, "main", "a" * 32)["generation"]
    owner._gaps.add("main")

    with pytest.raises(OwnerOperationError, match="MARKET_LEASE_OWNER_MISMATCH"):
        _release(owner, "main", "b" * 32, generation)
    assert main.close_calls == 0

    assert _release(owner, "main", "a" * 32, generation) == {"released": True}
    assert main.close_calls == 1
    assert "main" not in owner._clients
    assert owner._clients["other"] is other
    assert "main" not in owner._consumers
    assert "main" not in owner._gaps


def test_confirmed_release_rebuilds_fresh_client_for_next_generation(monkeypatch):
    owner = _owner(monkeypatch, _settings())
    first = owner._clients["main"]
    token1, token2 = "a" * 32, "b" * 32
    generation1 = _claim(owner, "main", token1)["generation"]
    assert generation1 == 1
    assert _release(owner, "main", token1, generation1) == {"released": True}

    generation2 = _claim(owner, "main", token2)["generation"]
    assert generation2 == generation1 + 1
    assert owner.invoke(
        "main.start",
        {"_consumer_token": token2, "_lease_generation": generation2},
    ) == {"started": True}
    second = owner._clients["main"]
    assert second is not first
    assert second.cursor is not first.cursor
    assert first.close_calls == 1

    with pytest.raises(OwnerOperationError, match="MARKET_LEASE_OWNER_MISMATCH"):
        owner.invoke(
            "main.status",
            {"_consumer_token": token1, "_lease_generation": generation1},
        )


def test_release_uncertainty_retains_registration_and_blocks_reopen(monkeypatch):
    owner = _owner(monkeypatch, _settings())
    client = owner._clients["main"]
    token = "a" * 32
    generation = _claim(owner, "main", token)["generation"]
    client.unsubscription_uncertain = True
    client.close_error = ServiceMarketError("UNSUBSCRIPTION_UNCERTAIN")

    with pytest.raises(OwnerOperationError, match="UNSUBSCRIPTION_UNCERTAIN"):
        _release(owner, "main", token, generation)
    assert owner._clients["main"] is client
    assert owner._consumers["main"] == token
    assert owner._lease_tokens["main"] == token
    assert owner.cleanup_uncertainties == 1
    with pytest.raises(OwnerOperationError, match="MARKET_LEASE_ALREADY_CLAIMED"):
        _claim(owner, "main", "b" * 32)
    assert client.close_calls == 1


class _Handle:
    def __init__(self, result=None, errors=()):
        self.value = result
        self.errors = list(errors)
        self.calls = 0

    def result(self, *, timeout_seconds):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.value


def _strategy_client(settings, handles):
    runtime = ThreadedServiceClientRuntime(lambda: None)
    runtime.calls = []

    def submit(operation, arguments):
        runtime.calls.append((operation, arguments.copy()))
        handle = handles.pop(0)
        if isinstance(handle, Exception):
            raise handle
        return handle

    runtime.submit_owner = submit
    return GatewayStrategyMarketClient(runtime, settings), runtime


def test_legacy_accessor_stays_stable_and_lease_has_independent_client():
    handles = [
        _Handle({"generation": 1}),
        _Handle({"released": True}),
        _Handle({"generation": 2}),
    ]
    client, runtime = _strategy_client(_settings(), handles)
    legacy = client.subscription("main")
    lease1 = client.acquire_subscription("main")
    assert type(lease1) is type(legacy)
    assert lease1 is not legacy
    assert client.subscription("main") is legacy
    with pytest.raises(ThreadedRuntimeError, match="MARKET_LEASE_ALREADY_ACQUIRED"):
        client.acquire_subscription("main")

    assert lease1.close() is True
    with pytest.raises(ThreadedRuntimeError, match="MARKET_LEASE_RELEASED"):
        lease1.poll()
    with pytest.raises(ThreadedRuntimeError, match="MARKET_LEASE_RELEASED"):
        legacy.poll()

    lease2 = client.acquire_subscription("main")
    assert lease2 is not lease1
    assert lease2._lease_generation == 2
    assert lease2._consumer_token != lease1._consumer_token
    assert runtime.calls == [
        ("market.main.claim", {"_consumer_token": lease1._consumer_token}),
        ("market.main.release", {
            "_consumer_token": lease1._consumer_token, "_lease_generation": 1
        }),
        ("market.main.claim", {"_consumer_token": lease2._consumer_token}),
    ]


def test_claim_timeout_reuses_same_handle_and_keeps_name_reserved():
    handle = _Handle(
        {"generation": 4}, [ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING")]
    )
    client, runtime = _strategy_client(_settings(), [handle])
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        client.acquire_subscription("main", timeout_seconds=0.01)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_OPERATION_PENDING"):
        client.subscription("main").poll()

    lease = client.acquire_subscription("main")
    assert lease._lease_generation == 4
    assert handle.calls == 2
    assert len(runtime.calls) == 1


def test_definitive_claim_rejection_restores_legacy_proxy():
    client, runtime = _strategy_client(
        _settings(),
        [OwnerOperationError("MARKET_LEASE_UNAVAILABLE"), _Handle(None)],
    )
    legacy = client.subscription("main")
    with pytest.raises(OwnerOperationError, match="MARKET_LEASE_UNAVAILABLE"):
        client.acquire_subscription("main")
    assert legacy._legacy_blocked is False
    assert legacy.poll() is None
    assert [call[0] for call in runtime.calls] == [
        "market.main.claim", "market.main.poll"
    ]


@pytest.mark.parametrize(
    "code", ["OWNER_UNAVAILABLE", "NOT_READY", "QUEUE_FULL", "OWNER_THREAD_REENTRY"]
)
def test_claim_submit_rejection_rolls_back_without_leaking_reservation(code):
    client, runtime = _strategy_client(
        _settings(),
        [ThreadedRuntimeError(code), _Handle({"generation": 1})],
    )
    legacy = client.subscription("main")
    with pytest.raises(ThreadedRuntimeError, match=code):
        client.acquire_subscription("main")
    assert "main" not in client._leases
    assert legacy._legacy_blocked is False

    lease = client.acquire_subscription("main")
    assert lease._lease_state == "active"
    assert len(runtime.calls) == 2
    assert runtime.calls[0][1]["_consumer_token"] != runtime.calls[1][1]["_consumer_token"]


@pytest.mark.parametrize(
    "code", ["OWNER_UNAVAILABLE", "NOT_READY", "QUEUE_FULL", "OWNER_THREAD_REENTRY"]
)
def test_release_submit_rejection_keeps_active_lease_for_safe_retry(code):
    client, runtime = _strategy_client(
        _settings(),
        [
            _Handle({"generation": 1}),
            ThreadedRuntimeError(code),
            _Handle({"released": True}),
        ],
    )
    lease = client.acquire_subscription("main")
    with pytest.raises(ThreadedRuntimeError, match=code):
        lease.close()
    assert lease._lease_state == "active"
    assert client._leases["main"] is lease

    assert lease.close() is True
    assert [call[0] for call in runtime.calls] == [
        "market.main.claim", "market.main.release", "market.main.release"
    ]
    assert runtime.calls[1][1] == runtime.calls[2][1]


def test_no_send_classification_survives_concurrent_status_call(monkeypatch):
    rejected = threading.Event()
    continue_close = threading.Event()
    status_result = {
        "healthy": True,
        "recovery_required": False,
        "resync_required": False,
        "authorization_interrupted": None,
        "authorization_generation": 0,
        "queue_count": 0,
        "queue_bytes": 0,
        "unsubscription_uncertain": False,
        "local_result_gap": False,
        "last_bootstrap": None,
        "source_descriptor": None,
    }
    client, runtime = _strategy_client(
        _settings(),
        [
            _Handle({"generation": 1}),
            ThreadedRuntimeError("QUEUE_FULL"),
            _Handle(status_result),
            _Handle({"released": True}),
        ],
    )
    lease = client.acquire_subscription("main")
    original_call = lease._call

    def interleaved_call(method, *args, **kwargs):
        try:
            return original_call(method, *args, **kwargs)
        except ThreadedRuntimeError as exc:
            if method == "release" and exc.code == "QUEUE_FULL":
                rejected.set()
                assert continue_close.wait(2)
            raise

    monkeypatch.setattr(lease, "_call", interleaved_call)
    outcomes = []

    def close_first():
        try:
            lease.close()
        except Exception as exc:
            outcomes.append(exc)

    thread = threading.Thread(target=close_first)
    thread.start()
    assert rejected.wait(2)
    assert lease.status()["healthy"] is True
    continue_close.set()
    thread.join(2)
    assert not thread.is_alive()
    assert len(outcomes) == 1 and outcomes[0].code == "QUEUE_FULL"
    assert lease._lease_state == "active"

    monkeypatch.setattr(lease, "_call", original_call)
    assert lease.close() is True
    assert [call[0] for call in runtime.calls] == [
        "market.main.claim",
        "market.main.release",
        "market.main.status",
        "market.main.release",
    ]


def test_no_send_owner_reentry_is_recoverable():
    client, runtime = _strategy_client(
        _settings(),
        [
            ThreadedRuntimeError("OWNER_THREAD_REENTRY"),
            _Handle({"generation": 1}),
        ],
    )
    with pytest.raises(ThreadedRuntimeError, match="OWNER_THREAD_REENTRY"):
        client.acquire_subscription("main")
    assert client._leases == {}
    assert client.acquire_subscription("main")._lease_state == "active"
    assert [call[0] for call in runtime.calls] == [
        "market.main.claim", "market.main.claim"
    ]


def test_later_generation_claim_no_send_restores_released_legacy_state():
    client, runtime = _strategy_client(
        _settings(),
        [
            _Handle({"generation": 1}),
            _Handle({"released": True}),
            ThreadedRuntimeError("QUEUE_FULL"),
            _Handle({"generation": 2}),
        ],
    )
    legacy = client.subscription("main")
    first = client.acquire_subscription("main")
    assert first.close() is True
    with pytest.raises(ThreadedRuntimeError, match="QUEUE_FULL"):
        client.acquire_subscription("main")
    with pytest.raises(ThreadedRuntimeError, match="MARKET_LEASE_RELEASED"):
        legacy.poll()

    second = client.acquire_subscription("main")
    assert second._lease_generation == 2
    assert [call[0] for call in runtime.calls] == [
        "market.main.claim",
        "market.main.release",
        "market.main.claim",
        "market.main.claim",
    ]


def test_pending_market_operation_blocks_release_without_replay(monkeypatch):
    monkeypatch.setattr(module, "_bootstrap_from_wire", lambda raw: raw)
    claim = _Handle({"generation": 1})
    start = _Handle(
        {"bootstrap": True}, [ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING")]
    )
    release = _Handle({"released": True})
    client, runtime = _strategy_client(_settings(), [claim, start, release])
    lease = client.acquire_subscription("main")
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        lease.start(timeout_seconds=0.01)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_OPERATION_PENDING"):
        lease.close()
    assert [call[0] for call in runtime.calls] == [
        "market.main.claim", "market.main.start"
    ]

    assert lease.start() is not None
    assert lease.close() is True
    assert [call[0] for call in runtime.calls] == [
        "market.main.claim", "market.main.start", "market.main.release"
    ]
    assert start.calls == 2


def test_release_timeout_reuses_exact_handle_then_confirms():
    release = _Handle(
        {"released": True}, [ThreadedRuntimeError("OWNER_TIMEOUT_RUNNING")]
    )
    client, runtime = _strategy_client(
        _settings(), [_Handle({"generation": 1}), release]
    )
    lease = client.acquire_subscription("main")
    with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
        lease.close(timeout_seconds=0.01)
    assert lease.close() is True
    assert len(runtime.calls) == 2
    assert release.calls == 2


@pytest.mark.parametrize("timeout", [0, float("nan"), "invalid", True])
def test_invalid_release_timeout_is_rejected_before_submit(timeout):
    client, runtime = _strategy_client(
        _settings(), [_Handle({"generation": 1}), _Handle({"released": True})]
    )
    lease = client.acquire_subscription("main")
    with pytest.raises(ValueError, match="timeout_seconds"):
        lease.close(timeout_seconds=timeout)
    assert lease._lease_state == "active"
    assert client._leases["main"] is lease
    assert [call[0] for call in runtime.calls] == ["market.main.claim"]
    assert lease.close() is True


def test_concurrent_close_serializes_through_local_confirmation():
    entered = threading.Event()
    finish = threading.Event()

    class BlockingHandle(_Handle):
        def result(self, *, timeout_seconds):
            self.calls += 1
            entered.set()
            assert finish.wait(2)
            return self.value

    release = BlockingHandle({"released": True})
    client, runtime = _strategy_client(
        _settings(), [_Handle({"generation": 1}), release]
    )
    lease = client.acquire_subscription("main")
    outcomes = []

    def close_first():
        try:
            outcomes.append(("returned", lease.close()))
        except Exception as exc:  # pragma: no cover - reported in the assertion below
            outcomes.append(("error", exc))

    thread = threading.Thread(target=close_first)
    thread.start()
    assert entered.wait(2)
    with pytest.raises(ThreadedRuntimeError, match="OWNER_OPERATION_PENDING"):
        lease.close()
    assert lease._lease_state == "closing"
    finish.set()
    thread.join(2)
    assert not thread.is_alive()
    assert outcomes == [("returned", True)]
    assert lease._lease_state == "released"

    assert lease.close() is True
    assert [call[0] for call in runtime.calls] == [
        "market.main.claim", "market.main.release"
    ]
    assert release.calls == 1


def test_unknown_release_is_sticky_and_never_replayed():
    client, runtime = _strategy_client(
        _settings(),
        [
            _Handle({"generation": 1}),
            _Handle(errors=[OwnerOperationError("UNSUBSCRIPTION_UNCERTAIN")]),
        ],
    )
    lease = client.acquire_subscription("main")
    with pytest.raises(OwnerOperationError, match="UNSUBSCRIPTION_UNCERTAIN"):
        lease.close()
    with pytest.raises(ThreadedRuntimeError, match="MARKET_LEASE_UNCERTAIN"):
        lease.close()
    with pytest.raises(ThreadedRuntimeError, match="MARKET_LEASE_UNCERTAIN"):
        client.acquire_subscription("main")
    assert [call[0] for call in runtime.calls] == [
        "market.main.claim", "market.main.release"
    ]


def test_result_gap_still_allows_explicit_registration_release():
    client, runtime = _strategy_client(
        _settings(),
        [
            _Handle({"generation": 1}),
            _Handle(errors=[OwnerOperationError("LOCAL_RESULT_GAP")]),
            _Handle({"released": True}),
        ],
    )
    lease = client.acquire_subscription("main")
    with pytest.raises(OwnerOperationError, match="LOCAL_RESULT_GAP"):
        lease.poll()
    assert lease.close() is True
    assert [call[0] for call in runtime.calls] == [
        "market.main.claim", "market.main.poll", "market.main.release"
    ]


def test_gateway_api_adds_lease_accessor_and_preserves_legacy_accessor(monkeypatch):
    from bt_api_py.gateway_api import GatewayBtApi
    from tests.unit.test_iter63_gateway_api import ROUTE, session_with_reads

    session = session_with_reads()
    api = GatewayBtApi(session, exchange_name=ROUTE)
    legacy = session.market_client.subscription("main")
    leased = object()
    seen = []
    monkeypatch.setattr(
        session.market_client,
        "acquire_subscription",
        lambda name, *, timeout_seconds: seen.append((name, timeout_seconds)) or leased,
    )
    assert api.market_subscription(ROUTE, "main") is legacy
    assert api.acquire_market_subscription(ROUTE, "main", timeout_seconds=0.75) is leased
    assert seen == [("main", 0.75)]
    with pytest.raises(RuntimeError, match="MARKET_SUBSCRIPTION_UNAVAILABLE"):
        api.acquire_market_subscription(ROUTE, "missing")
    with pytest.raises(ValueError, match="GATEWAY_ROUTE_MISMATCH"):
        api.acquire_market_subscription("other", "main")
    api.close()
    session.close()


def test_gateway_api_checks_a_locked_lease_snapshot_under_concurrent_mutation(
    monkeypatch,
):
    from bt_api_py.gateway_api import GatewayBtApi
    from bt_api_py.gateway_strategy_market import GatewayMarketSubscriptionClient
    from tests.unit.test_iter63_gateway_api import ROUTE, session_with_reads

    session = session_with_reads()
    api = GatewayBtApi(session, exchange_name=ROUTE)
    market_client = session.market_client
    lease = GatewayMarketSubscriptionClient(
        market_client._runtime, "main", _lease_manager=market_client
    )
    snapshot_entered = threading.Event()
    finished = threading.Event()
    observed = []
    original_snapshot = market_client._lease_snapshot

    def snapshot():
        snapshot_entered.set()
        return original_snapshot()

    monkeypatch.setattr(market_client, "_lease_snapshot", snapshot)

    def check_binding():
        try:
            observed.append(api.list_exchanges())
        except Exception as exc:  # pragma: no cover - reported in the assertion below
            observed.append(exc)
        finally:
            finished.set()

    with market_client._lease_lock:
        market_client._leases["main"] = lease
        thread = threading.Thread(target=check_binding)
        thread.start()
        assert snapshot_entered.wait(2)
        assert not finished.wait(0.05)
        market_client._leases.clear()
    thread.join(2)
    assert not thread.is_alive()
    assert observed == [[ROUTE]]
    api.close()
    session.close()


def test_lease_manager_rejects_foreign_pid_before_touching_inherited_lock():
    client, runtime = _strategy_client(_settings(), [])
    lease = module.GatewayMarketSubscriptionClient(
        runtime, "main", _lease_manager=client
    )

    class ForbiddenLock:
        def __enter__(self):
            raise AssertionError("lease lock must not be acquired in a forked process")

        def __exit__(self, *_args):
            return False

    client._owner_pid += 1
    client._lease_lock = ForbiddenLock()
    with pytest.raises(ThreadedRuntimeError, match="WRONG_PROCESS"):
        client.acquire_subscription("main")
    with pytest.raises(ThreadedRuntimeError, match="WRONG_PROCESS"):
        client._lease_snapshot()
    with pytest.raises(ThreadedRuntimeError, match="WRONG_PROCESS"):
        client._release_confirmed(lease)
    with pytest.raises(ThreadedRuntimeError, match="WRONG_PROCESS"):
        client._release_uncertain(lease)

    assert client._leases == {}
    assert client._claim_inflight == set()
    assert runtime.calls == []


def test_real_service_market_client_close_and_rebuild_use_fresh_cursor(
    tmp_path, monkeypatch
):
    from bt_api_py.service_market import ServiceMarketClient

    subscribers = FakeSubscriberFactory()
    holder = {}
    monkeypatch.setattr(module, "ZmqMarketTransportFactory", lambda _profiles: subscribers)
    original_start = ServiceMarketClient.start
    starts = []

    def observed_start(client):
        starts.append(client)
        return original_start(client)

    monkeypatch.setattr(ServiceMarketClient, "start", observed_start)

    def runtime_factory():
        _client, raw, registry, market, _transport, _subscribers = _make_client(tmp_path)
        raw.start = lambda: None
        holder.update(raw=raw, registry=registry, market=market)
        return raw

    class Composite:
        def __init__(self):
            self.market_owner = GatewayMarketOwner(
                _settings(),
                stream_scope=WireScope("paper:market-unit"),
                security_profiles=_PROFILES,
            )

        def start(self, raw):
            self.market_owner.start(raw)

        def invoke(self, operation, arguments):
            assert operation.startswith("market.")
            return self.market_owner.invoke(operation.removeprefix("market."), arguments)

        def close(self):
            self.market_owner.close()

        @property
        def cleanup_uncertainties(self):
            return self.market_owner.cleanup_uncertainties

    runtime = ThreadedServiceClientRuntime(
        runtime_factory, owner_extension_factory=Composite, monotonic_clock=lambda: 0.0
    )
    try:
        runtime.start()
        client = GatewayStrategyMarketClient(runtime, _settings())
        first_lease = client.acquire_subscription("main")
        first_lease.start()
        first_service_client = starts[-1]
        assert holder["market"].registrations[_KEY.stream_id] == {
            first_service_client._binding.registration_id
        }

        first_event = holder["market"].publish(_KEY, "first-cursor")
        subscribers.created[0].events.append(first_event)
        first_received = first_lease.poll_with_receipt()
        assert first_received.event.header.sequence == 1
        assert first_lease._issued_received_event is not None

        # A second registration keeps the upstream stream active. Releasing
        # this lease must depend on this registration's reply, not on the
        # provider stream being fully stopped.
        shared_registration = "registration:other-consumer"
        holder["market"].registrations[_KEY.stream_id].add(shared_registration)
        assert first_lease.close() is True
        assert holder["market"].registrations[_KEY.stream_id] == {shared_registration}
        assert subscribers.created[0].closed

        second_lease = client.acquire_subscription("main")
        second_lease.start()
        second_service_client = starts[-1]
        assert second_service_client is not first_service_client
        assert second_service_client._subscriber is subscribers.created[1]
        assert second_lease._issued_received_event is None
        assert second_service_client._cursor_sequence == 1

        second_event = holder["market"].publish(_KEY, "second-cursor")
        subscribers.created[1].events.append(second_event)
        received = second_lease.poll_with_receipt()
        assert received.event.header.sequence == 2
        assert received.event.payload["data"]["marker"] == "second-cursor"
        assert second_lease.close() is True
        assert holder["market"].registrations[_KEY.stream_id] == {shared_registration}
    finally:
        runtime.close()
        if "registry" in holder:
            holder["registry"].close()
