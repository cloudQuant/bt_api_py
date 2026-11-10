"""Synthetic asynchronous bootstrap and retention tests."""

from dataclasses import replace
import threading

import pytest

from bt_api_py.service_market import ServiceMarketClient, ServiceMarketError
from bt_api_py.gateway_strategy_market import GatewayMarketOwner
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime
from tests.unit.test_service_runtime_thread_owner import Raw
from tests.unit import test_service_market as old

ASYNC = frozenset({"market.bootstrap_begin", "market.bootstrap_status", "market.bootstrap_cancel"})


def setup(tmp_path):
    client, runtime, registry, market, transport, subscribers = old._make_client(
        tmp_path, operations=old._OPERATIONS | ASYNC
    )
    original = market.handle
    jobs = {}
    metadata = {}
    seen = []

    def handler(context, arguments, operation):
        if operation not in ASYNC:
            return original(context, arguments, operation)
        operation_id = arguments["operation_id"]
        seen.append((operation, operation_id))
        if operation == "market.bootstrap_begin":
            payload = (arguments["subscriptions"], arguments.get("refresh_existing", False))
            if operation_id in metadata and metadata[operation_id]["payload"] != payload:
                raise ValueError("OPERATION_ID_CONFLICT")
            if operation_id not in jobs:
                keys = [
                    old.MarketSubscriptionKey.from_dict(item["key"])
                    for item in arguments["subscriptions"]
                ]
                borrowed = {
                    k
                    for k in keys
                    if context.registration_id in market.registrations.get(k.stream_id, set())
                }
                if borrowed and not arguments.get("refresh_existing", False):
                    return {
                        "operation_id": operation_id,
                        "status": "rejected",
                        "code": "MARKET_ALREADY_SUBSCRIBED",
                    }
                result = original(
                    context, {"subscriptions": arguments["subscriptions"]}, "market.bootstrap_many"
                )
                jobs[operation_id] = result["items"]
                metadata[operation_id] = {"payload": payload, "owned": set(keys) - borrowed}
            return {"operation_id": operation_id, "status": "pending"}
        if operation == "market.bootstrap_cancel":
            for key in metadata.get(operation_id, {}).get("owned", ()):
                market.registrations[key.stream_id].discard(context.registration_id)
            return {"operation_id": operation_id, "status": "cancelled", "code": "CANCELLED"}
        if operation_id not in jobs:
            return {"operation_id": operation_id, "status": "not_found"}
        return {"operation_id": operation_id, "status": "active", "items": jobs[operation_id]}

    market.handle = handler
    return client, runtime, registry, market, transport, subscribers, jobs, seen


def test_subscribers_open_before_begin_and_one_rpc_per_step(tmp_path):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    second = old._same_runtime_client(runtime, subscribers, old._OTHER_KEY)
    try:
        operation = ServiceMarketClient.begin_start_many([client, second])
        assert len(subscribers.created) == 2 and seen == []
        assert operation.step() is None
        assert len(seen) == 1
        result = operation.step()
        assert len(result) == 2 and len(seen) == 2
        assert {call[1] for call in seen} == {operation.operation_id}
        assert client.healthy and second.healthy
    finally:
        old._close_many((client, second), runtime, registry)


def test_lost_begin_reply_recovers_same_id_and_not_found_retries(tmp_path):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    try:
        operation = client.begin_start()
        transport.fail_after_dispatch.add("market.bootstrap_begin")
        assert operation.step() is None
        assert operation.step() is not None
        assert len({item[1] for item in seen}) == 1
        assert [item[0] for item in seen] == ["market.bootstrap_begin", "market.bootstrap_status"]
    finally:
        old._close_many((client,), runtime, registry)


def test_not_found_never_declares_cleanup_and_reuses_id(tmp_path):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    try:
        operation = client.begin_start()
        transport.not_sent.add("market.bootstrap_begin")
        assert operation.step() is None
        assert not subscribers.created[0].closed
        operation._next = "status"
        assert operation.step() is None
        assert not subscribers.created[0].closed
        assert operation.step() is None
        assert operation.step() is not None
        assert len({item[1] for item in seen}) == 1
    finally:
        old._close_many((client,), runtime, registry)


def test_partial_capability_fails_before_subscriber_or_rpc(tmp_path, monkeypatch):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    binding = client._get_binding()
    monkeypatch.setattr(
        client,
        "_get_binding",
        lambda **kwargs: replace(
            binding, operations=binding.operations - {"market.bootstrap_cancel"}
        ),
    )
    try:
        with pytest.raises(ServiceMarketError, match="INCOMPLETE_ASYNC_BOOTSTRAP_CAPABILITY"):
            client.start()
        assert subscribers.created == [] and seen == []
    finally:
        monkeypatch.undo()
        old._close_many((client,), runtime, registry)


def test_invalid_operation_id_keeps_original_reference(tmp_path):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    previous = market.handle
    market.handle = lambda ctx, args, op: (
        {"operation_id": "wrong", "status": "pending"}
        if op == "market.bootstrap_begin"
        else previous(ctx, args, op)
    )
    try:
        operation = client.begin_start()
        with pytest.raises(ServiceMarketError, match="INVALID_BATCH_REPLY"):
            operation.step()
        assert client.unsubscription_uncertain and not subscribers.created[0].closed
        assert operation.cancel() is True
        assert subscribers.created[0].closed
    finally:
        old._close_many((client,), runtime, registry)


def test_cancel_pending_preserves_subscriber_until_terminal_reply(tmp_path):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    previous = market.handle
    calls = []

    def delayed(context, arguments, operation):
        if operation == "market.bootstrap_cancel":
            calls.append(1)
            if len(calls) == 1:
                return {
                    "operation_id": arguments["operation_id"],
                    "status": "cancelling",
                    "code": "CLEANUP_PENDING",
                }
        return previous(context, arguments, operation)

    market.handle = delayed
    try:
        operation = client.begin_start()
        assert operation.step() is None
        assert operation.cancel() is False
        assert not subscribers.created[0].closed
        assert operation.cancel() is True
        assert subscribers.created[0].closed
    finally:
        old._close_many((client,), runtime, registry)


def test_unknown_reply_preserves_operation_and_subscriber(tmp_path):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    previous = market.handle
    market.handle = lambda ctx, args, op: (
        {
            "operation_id": args["operation_id"],
            "status": "unknown",
            "code": "UNKNOWN",
            "uncertain_stream_ids": [old._KEY.stream_id],
        }
        if op == "market.bootstrap_status"
        else previous(ctx, args, op)
    )
    try:
        operation = client.begin_start()
        assert operation.step() is None
        with pytest.raises(ServiceMarketError, match="BATCH_START_OUTCOME_UNKNOWN"):
            operation.step()
        assert client.unsubscription_uncertain and not subscribers.created[0].closed
        with pytest.raises(ServiceMarketError, match="BOOTSTRAP_OPERATION_PENDING"):
            client.begin_recover()
        assert operation.cancel() is True
    finally:
        old._close_many((client,), runtime, registry)


def test_binding_change_preserves_old_uncertain_subscriber(tmp_path, monkeypatch):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    try:
        operation = client.begin_start()
        assert operation.step() is None
        monkeypatch.setattr(
            client,
            "_get_binding",
            lambda **kwargs: replace(operation.binding, service_epoch="other"),
        )
        with pytest.raises(ServiceMarketError, match="RUNTIME_BINDING_CHANGED"):
            operation.step()
        assert client.unsubscription_uncertain and not subscribers.created[0].closed
    finally:
        monkeypatch.undo()
        operation.cancel()
        old._close_many((client,), runtime, registry)


def test_binding_change_during_reply_is_rejected_before_consumption(tmp_path, monkeypatch):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    try:
        operation = client.begin_start()
        original = client._get_binding
        calls = []

        def changed(**kwargs):
            calls.append(True)
            binding = original(**kwargs)
            return replace(binding, service_epoch="new-epoch") if len(calls) > 1 else binding

        monkeypatch.setattr(client, "_get_binding", changed)
        with pytest.raises(ServiceMarketError, match="RUNTIME_BINDING_CHANGED"):
            operation.step()
        assert client.unsubscription_uncertain and not subscribers.created[0].closed
    finally:
        monkeypatch.undo()
        operation.cancel()
        old._close_many((client,), runtime, registry)


def test_before_send_subscriber_close_retried_once_without_duplicate_owner(tmp_path, monkeypatch):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)

    class Subscriber(old.FakeSubscriber):
        def __init__(self):
            super().__init__()
            self.closes = 0

        def close(self):
            self.closes += 1
            if self.closes == 1:
                raise ValueError("first close failed")
            if self.closes > 2:
                raise ValueError("duplicate close")
            self.closed = True

    subscriber = Subscriber()
    monkeypatch.setattr(client, "_subscriber_factory", lambda *_: subscriber)
    operation = client.begin_start()
    monkeypatch.setattr(
        client, "_get_binding", lambda **kwargs: replace(operation.binding, service_epoch="new")
    )
    try:
        with pytest.raises(ServiceMarketError, match="LOCAL_CLOSE_FAILED"):
            operation.step()
        with pytest.raises(ServiceMarketError, match="RUNTIME_BINDING_CHANGED"):
            client.close()
        client.close()
        assert subscriber.closes == 2 and subscriber.closed
        assert not client.unsubscription_uncertain and seen == []
    finally:
        monkeypatch.undo()
        old._close_many((client,), runtime, registry)


def test_recover_old_subscriber_close_failure_has_one_owner(tmp_path, monkeypatch):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)

    class Subscriber(old.FakeSubscriber):
        def __init__(self):
            super().__init__()
            self.closes = 0

        def close(self):
            self.closes += 1
            if self.closes == 1:
                raise ValueError("first close failed")
            if self.closes > 2:
                raise ValueError("duplicate close")
            self.closed = True

    original, replacement = Subscriber(), old.FakeSubscriber()
    available = [original, replacement]
    monkeypatch.setattr(client, "_subscriber_factory", lambda *_: available.pop(0))
    try:
        client.start()
        operation = client.begin_recover()
        assert operation.step() is None
        with pytest.raises(ServiceMarketError, match="LOCAL_CLOSE_FAILED"):
            operation.step()
        client.close()
        assert original.closes == 2 and original.closed and replacement.closed
    finally:
        old._close_many((client,), runtime, registry)


def test_all_subscriptions_validated_before_any_snapshot(tmp_path, monkeypatch):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    second = old._same_runtime_client(runtime, subscribers, old._OTHER_KEY)
    calls = []
    original = client._snapshot_from_reply
    monkeypatch.setattr(
        client, "_snapshot_from_reply", lambda item: (calls.append(True), original(item))[1]
    )
    try:
        operation = ServiceMarketClient.begin_start_many((client, second))
        operation.step()
        jobs[operation.operation_id][1]["subscription"]["stream_id"] = "wrong"
        with pytest.raises(ServiceMarketError, match="INVALID_BATCH_REPLY"):
            operation.step()
        assert calls == [] and all(c.unsubscription_uncertain for c in (client, second))
        operation.cancel()
    finally:
        old._close_many((client, second), runtime, registry)


def test_recover_and_regrant_use_async_and_keep_floor(tmp_path):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    try:
        assert client.start().stream_id == old._KEY.stream_id
        client._recovery_required = True
        assert client.recover().stream_id == old._KEY.stream_id
        assert not client.recovery_required
        client._interrupt_authorization("MARKET_KEY_DENIED")
        result = client.resync_after_regrant()
        assert result.resync_required and client.authorization_generation == 1
        assert client.authorization_interrupted is None
        assert "market.subscribe" not in transport.operations
        assert [op for op, _ in seen].count("market.bootstrap_begin") == 3
    finally:
        old._close_many((client,), runtime, registry)


def test_recovery_scope_denial_latches_authorization_until_regrant(tmp_path, monkeypatch):
    from bt_api_py.service_runtime import ServiceRuntimeError

    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    try:
        client.start()
        original = runtime.call
        monkeypatch.setattr(
            runtime,
            "call",
            lambda *args, **kwargs: (_ for _ in ()).throw(ServiceRuntimeError("SCOPE_DENIED")),
        )
        with pytest.raises(ServiceMarketError, match="MARKET_AUTHORIZATION_INTERRUPTED"):
            client.recover()
        assert client.authorization_interrupted == "MARKET_KEY_DENIED"
        monkeypatch.setattr(runtime, "call", original)
        with pytest.raises(ServiceMarketError, match="MARKET_AUTHORIZATION_INTERRUPTED"):
            client.recover()
        assert client.resync_after_regrant().resync_required
    finally:
        old._close_many((client,), runtime, registry)


@pytest.mark.parametrize("outcome", ["rejected", "cancelled"])
def test_regrant_can_retry_after_clean_job_termination(tmp_path, outcome):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    try:
        client.start()
        client._interrupt_authorization("MARKET_KEY_DENIED")
        if outcome == "rejected":
            previous = market.handle
            market.handle = lambda ctx, args, op: (
                {
                    "operation_id": args["operation_id"],
                    "status": "rejected",
                    "code": "MARKET_NOT_READY",
                }
                if op == "market.bootstrap_begin"
                else previous(ctx, args, op)
            )
            with pytest.raises(ServiceMarketError, match="BATCH_START_REJECTED"):
                client.resync_after_regrant()
            market.handle = previous
        else:
            operation = client.begin_resync_after_regrant()
            assert operation.step() is None
            assert operation.cancel() is True
        assert client.authorization_interrupted == "MARKET_KEY_DENIED"
        assert client.resync_after_regrant().resync_required
        assert client.authorization_interrupted is None
    finally:
        old._close_many((client,), runtime, registry)


def test_recover_can_retry_after_clean_cancel(tmp_path):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    try:
        client.start()
        previous = client._subscriber
        operation = client.begin_recover()
        assert operation.step() is None
        assert operation.cancel() is True
        assert previous.closed and client._subscriber is None
        assert client.recover().stream_id == old._KEY.stream_id
        assert client.healthy
    finally:
        old._close_many((client,), runtime, registry)


def test_real_hub_batch_member_recovers_and_closes_independently(tmp_path, monkeypatch):
    from bt_api_gateway.market_hub import (
        GatewayMarketHub,
        GatewayMarketRegistration,
        GatewayMarketService,
    )

    class Provider:
        def __init__(self):
            self.acquisitions = 0
            self.releases = 0

        def begin_subscribe(self, key, *, generation):
            self.acquisitions += 1
            return object()

        def subscribe(self, key, *, generation):
            raise AssertionError("legacy subscribe must not run")

        def subscription_ready(self, handle):
            return True

        def probe_source(self, handle, **kwargs):
            return None

        def unsubscribe(self, handle):
            self.releases += 1
            return True

        def poll(self, handle, *, limit):
            return ()

    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    second = old._same_runtime_client(runtime, subscribers, old._OTHER_KEY)
    provider = Provider()
    hub = GatewayMarketHub(
        provider,
        key_universe=(old._KEY, old._OTHER_KEY),
        principal_allowed_keys={
            old._CLIENT.principal_ref: (old._KEY, old._OTHER_KEY),
            "other-principal": (old._OTHER_KEY,),
        },
        service_identity=old._PEER,
        service_scope=old._PUBLIC_SCOPE,
    )
    hub.set_partitioned_publisher(
        lambda event: None,
        principal_stream_grants=hub.principal_stream_grants(),
        regrant=lambda grants: True,
    )
    service = GatewayMarketService(hub)
    service.register_registration(
        "background",
        GatewayMarketRegistration(
            principal_ref="other-principal",
            execution_domain_ref=old._PUBLIC_SCOPE.execution_domain_ref,
        ),
    )
    background_id = "b" * 32
    background = service.handle(
        "market.bootstrap_begin",
        principal_ref="other-principal",
        scope=old._PUBLIC_SCOPE,
        registration_id="background",
        arguments={
            "operation_id": background_id,
            "subscriptions": [{"key": old._OTHER_KEY.to_dict()}],
        },
        result_fits=lambda result: True,
    )
    for _ in range(20):
        if background["status"] != "pending":
            break
        background = service.handle(
            "market.bootstrap_status",
            principal_ref="other-principal",
            scope=old._PUBLIC_SCOPE,
            registration_id="background",
            arguments={"operation_id": background_id},
            result_fits=lambda result: True,
        )
    assert background["status"] == "active"
    registrations = []

    def handler(context, arguments, operation):
        if not registrations:
            scope = context.scope
            service.register_registration(
                context.registration_id,
                GatewayMarketRegistration(
                    principal_ref=old._CLIENT.principal_ref,
                    execution_domain_ref=scope.execution_domain_ref,
                    strategy_ref=scope.strategy_ref,
                    run_id=scope.run_id,
                    account_ref=scope.account_ref,
                ),
            )
            registrations.append(context.registration_id)
        return service.handle(
            operation,
            principal_ref=old._CLIENT.principal_ref,
            scope=context.scope,
            registration_id=context.registration_id,
            arguments=arguments,
            result_fits=lambda result: True,
        )

    market.handle = handler
    try:
        assert len(ServiceMarketClient.start_many((client, second))) == 2
        registration = registrations[0]
        assert provider.acquisitions == 2
        original_subscriber, original_factory = client._subscriber, client._subscriber_factory
        calls_before = len(transport.operations)
        monkeypatch.setattr(
            client,
            "_subscriber_factory",
            lambda *args: (_ for _ in ()).throw(ValueError("local failure")),
        )
        with pytest.raises(ValueError, match="local failure"):
            client.begin_recover()
        assert client._subscriber is original_subscriber and not original_subscriber.closed
        assert hub.registration_active(old._KEY, registration)
        assert hub.registration_active(old._OTHER_KEY, registration)
        assert len(transport.operations) == calls_before
        monkeypatch.setattr(client, "_subscriber_factory", original_factory)
        operation = client.begin_recover()
        replacement = client._subscriber
        original_binding = client._get_binding
        transport.not_sent.add("market.bootstrap_begin")
        assert operation.step() is None
        assert not client.unsubscription_uncertain
        monkeypatch.setattr(
            client,
            "_get_binding",
            lambda **kwargs: replace(operation.binding, service_epoch="new-epoch"),
        )
        with pytest.raises(ServiceMarketError, match="RUNTIME_BINDING_CHANGED"):
            operation.step()
        assert replacement.closed and client._subscriber is original_subscriber
        assert not client.unsubscription_uncertain
        assert hub.registration_active(old._KEY, registration) and hub.registration_active(
            old._OTHER_KEY, registration
        )
        monkeypatch.setattr(client, "_get_binding", original_binding)
        assert client.recover().stream_id == old._KEY.stream_id
        assert provider.acquisitions == 2
        hub.revoke_principal_keys(old._CLIENT.principal_ref, (old._KEY,))
        with pytest.raises(ServiceMarketError, match="MARKET_AUTHORIZATION_INTERRUPTED"):
            client.recover()
        assert client.authorization_interrupted == "MARKET_KEY_DENIED"
        assert hub.registration_active(old._OTHER_KEY, registration)
        hub.grant_principal_keys(old._CLIENT.principal_ref, (old._KEY,))
        assert client.resync_after_regrant().resync_required
        client.close()
        assert hub.registration_active(old._OTHER_KEY, registration)
        assert not hub.registration_active(old._KEY, registration)
        assert second.healthy
        hub.revoke_principal_keys(old._CLIENT.principal_ref, (old._OTHER_KEY,))
        second._interrupt_authorization("MARKET_KEY_DENIED")
        hub.grant_principal_keys(old._CLIENT.principal_ref, (old._OTHER_KEY,))
        floor = hub.grant_floor(old._CLIENT.principal_ref, old._OTHER_KEY)
        recovered = second.resync_after_regrant()
        assert recovered.resync_required
        assert (recovered.cursor_epoch, recovered.cursor_sequence) == floor
        assert provider.acquisitions == 3
    finally:
        old._close_many((client, second), runtime, registry)
        hub.close()


def test_owner_continuation_keeps_handle_and_serves_other_calls():
    proceed, stepped = threading.Event(), threading.Event()
    from bt_api_py.service_market import MarketBootstrap

    class Operation:
        def step(self):
            stepped.set()
            if not proceed.is_set():
                return None
            return (
                MarketBootstrap(
                    None,
                    "latest_event",
                    False,
                    False,
                    old._KEY.stream_id,
                    "epoch",
                    0,
                    "epoch",
                    0,
                    False,
                ),
            )

    class Extension:
        cleanup_uncertainties = 0

        def start(self, raw):
            pass

        def invoke(self, op, args):
            if op == "market":
                return GatewayMarketOwner._continue_bootstrap(None, Operation())
            return {"ok": True}

        def close(self):
            pass

    runtime = ThreadedServiceClientRuntime(lambda: Raw([]), owner_extension_factory=Extension)
    runtime.start()
    try:
        handle = runtime.submit_owner("market", {})
        assert stepped.wait(1)
        with pytest.raises(ThreadedRuntimeError, match="OWNER_TIMEOUT_RUNNING"):
            handle.result(timeout_seconds=0.02)
        assert runtime.submit_owner("other", {}).result() == {"ok": True}
        proceed.set()
        assert handle.result(timeout_seconds=1)["stream_id"] == old._KEY.stream_id
    finally:
        proceed.set()
        runtime.close()


def _lose_session(client, monkeypatch):
    from bt_api_py.service_runtime import ServiceRuntimeError

    def gone(*args, **kwargs):
        raise ServiceRuntimeError("SESSION_NOT_REGISTERED")

    monkeypatch.setattr(client._runtime, "market_binding_view", gone)


def test_close_ends_a_pending_recover_whose_session_is_gone(tmp_path, monkeypatch):
    # SimNow 2026-11-10: the Gateway was restarted while a strategy's market
    # recover was pending. The old session could never be closed (the cancel
    # cannot be asked through a session that no longer exists), so the strategy
    # never registered again: OLD_SESSION_CLOSE_FAILED:CLEANUP_FAILED for good.
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    try:
        client.start()
        previous = client._subscriber
        operation = client.begin_recover()
        assert operation.step() is None  # sent, still pending at the Gateway
        replacement = client._subscriber
        assert replacement is not previous and not previous.closed and not replacement.closed
        cancels_before = sum(1 for call in seen if call[0] == "market.bootstrap_cancel")
        with monkeypatch.context() as patch:
            _lose_session(client, patch)
            client.close()
            assert operation.done and client._state == "closed"
            assert previous.closed and replacement.closed and client._subscriber is None
            assert client.unsubscription_uncertain is False
            client.close()  # idempotent
        assert sum(1 for call in seen if call[0] == "market.bootstrap_cancel") == cancels_before
    finally:
        old._close_many((), runtime, registry)


@pytest.mark.parametrize("answer", ["STALE_EPOCH", "NOT_REGISTERED"])
def test_close_ends_a_pending_recover_the_gateway_no_longer_knows(tmp_path, monkeypatch, answer):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    try:
        client.start()
        previous = client._subscriber
        operation = client.begin_recover()
        assert operation.step() is None
        replacement = client._subscriber
        real_rpc = client._rpc

        def restarted(name, arguments, **kwargs):
            if name == "market.bootstrap_cancel":
                raise ServiceMarketError("CONTROL_CALL_FAILED", cause_code=answer)
            return real_rpc(name, arguments, **kwargs)

        monkeypatch.setattr(client, "_rpc", restarted)
        assert operation.cancel() is True and operation.done
        assert previous.closed and replacement.closed and client._subscriber is None
        assert client.unsubscription_uncertain is False and client._state == "failed"
        client.close()
        assert client._state == "closed"
    finally:
        old._close_many((), runtime, registry)


@pytest.mark.parametrize("error", [
    ServiceMarketError("CONTROL_CALL_FAILED", cause_code="TIMEOUT"),
    ServiceMarketError("CONTROL_CALL_FAILED", cause_code="SCOPE_DENIED_ELSEWHERE"),
    ServiceMarketError("CONTROL_OUTCOME_UNKNOWN"),
])
def test_an_unanswered_cancel_of_a_live_registration_still_keeps_the_subscriber(
        tmp_path, monkeypatch, error):
    # Only a registration that is gone ends the operation; a cancel that was
    # merely not answered stays open and is asked again at the next close.
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    try:
        client.start()
        operation = client.begin_recover()
        assert operation.step() is None
        replacement = client._subscriber
        real_rpc = client._rpc

        def unanswered(name, arguments, **kwargs):
            if name == "market.bootstrap_cancel":
                raise error
            return real_rpc(name, arguments, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(client, "_rpc", unanswered)
            with pytest.raises(ServiceMarketError):
                client.close()
            assert not operation.done and not replacement.closed
        client.close()  # the Gateway answers now
        assert operation.done and replacement.closed and client._state == "closed"
    finally:
        old._close_many((), runtime, registry)


def test_a_recover_never_sent_closes_when_the_session_is_gone(tmp_path, monkeypatch):
    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    try:
        client.start()
        previous = client._subscriber
        operation = client.begin_recover()
        replacement = client._subscriber
        with monkeypatch.context() as patch:
            _lose_session(client, patch)
            # Nothing was sent, so the step restored the started client. Its
            # close is then the ordinary one of a client whose session is gone:
            # closed locally, the unsubscription reported as not confirmed
            # (the owner counts that and does not keep the runtime open).
            with pytest.raises(ServiceMarketError, match="UNSUBSCRIPTION_UNCERTAIN"):
                client.close()
            assert operation.done and client._state == "closed"
            assert previous.closed and replacement.closed
        assert not any(call[0] == "market.bootstrap_cancel" for call in seen)
    finally:
        old._close_many((), runtime, registry)


def test_market_owner_close_is_not_held_open_by_a_recover_of_a_gone_session(tmp_path, monkeypatch):
    # The runtime worker keeps a session open (CLEANUP_FAILED) for as long as
    # its owner extension's close raises; this is the close that used to raise.
    from types import SimpleNamespace

    from bt_api_py.service_runtime_thread import OwnerOperationError

    client, runtime, registry, market, transport, subscribers, jobs, seen = setup(tmp_path)
    try:
        client.start()
        operation = client.begin_recover()
        assert operation.step() is None
        owner = SimpleNamespace(_clients={"main": client}, _uncertain=set(), _closed=False)
        real_rpc = client._rpc

        def unanswered(name, arguments, **kwargs):
            if name == "market.bootstrap_cancel":
                raise ServiceMarketError("CONTROL_CALL_FAILED", cause_code="TIMEOUT")
            return real_rpc(name, arguments, **kwargs)

        with monkeypatch.context() as patch:  # the Gateway is merely silent: keep waiting
            patch.setattr(client, "_rpc", unanswered)
            with pytest.raises(OwnerOperationError, match="LOCAL_CLOSE_FAILED"):
                GatewayMarketOwner.close(owner)
            assert owner._clients == {"main": client} and not operation.done
        with monkeypatch.context() as patch:  # the session is gone: nothing to wait for
            _lose_session(client, patch)
            GatewayMarketOwner.close(owner)
            assert owner._clients == {} and operation.done and client._state == "closed"
    finally:
        old._close_many((), runtime, registry)
