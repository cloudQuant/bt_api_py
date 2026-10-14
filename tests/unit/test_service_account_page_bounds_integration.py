"""Real CURVE coverage for bounded pages and complete reply byte budgets."""

from __future__ import annotations

import multiprocessing
from contextlib import suppress
from types import MethodType
from uuid import uuid4

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_gateway import GatewayEventJournal

from bt_api_py.service_account_consumer import (
    ServiceAccountConsumer,
    ServiceAccountConsumerError,
)
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.unit.test_service_account_consumer_crash_integration import (
    _event_reducer,
    _snapshot_reducer,
)
from tests.unit.test_service_account_consumer_integration import (
    _ACCOUNT_REF,
    _ACTOR_A,
    _CLIENT_PRINCIPAL,
    _CONSUMER_OPERATIONS,
    _JOURNAL_ACCOUNT_SCOPE,
    _LISTENER_REF,
    _SECURITY_REF,
    _SERVICE_ID,
    _SERVICE_PRINCIPAL,
    _SHARED_FACT_SCOPE,
    _available_tcp_endpoint,
    _await_host,
    _cleanup_host,
    _client_config,
    _close_host_normally,
    _close_runtime,
    _progress,
    _service_config,
    _service_process,
    _wire_scope,
)

_SMALL_CAP = 12 * 1024
_LARGE_CAP = 64 * 1024
_EVENT_PAYLOAD_BYTES = 7_000
_OVERSIZED_PAYLOAD_BYTES = 80 * 1024
_PROJECTION_PATH_VERSION = "account-consumer-page-bounds-v1"
_INITIAL_STATE = {"applied_count": 0, "event_ids": [], "last_sequence": 0}
_EXPECTED_EVENT_IDS = tuple(f"bounded-event-{sequence}" for sequence in range(1, 4))


def _append_events(journal_path: str) -> None:
    journal = GatewayEventJournal(journal_path, clock=lambda: 1_800_000_000.0)
    for sequence in range(1, 4):
        journal.append(
            _JOURNAL_ACCOUNT_SCOPE,
            _SHARED_FACT_SCOPE,
            f"bounded-event-{sequence}",
            "position_update",
            {
                "sequence": sequence,
                "source": "synthetic-page-boundary",
                "padding": "x" * _EVENT_PAYLOAD_BYTES,
            },
            observed_at=1_800_000_000.0 + sequence,
        )


def _new_bounded_runtime(endpoint, keys: ClientCurveKeys, client_cap: int):
    raw_config = _client_config(endpoint, _ACTOR_A).to_public_dict()
    raw_config["communication"]["profiles"]["client-curve-v1"]["options"]["max_message_bytes"] = (
        client_cap
    )
    config = parse_client_config(raw_config)
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, _CLIENT_PRINCIPAL, _ACTOR_A)
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    return ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda _scope_ref: _wire_scope(_ACTOR_A),
        peer_resolver=lambda _service_id: service_identity,
        transport_factory=ZmqTransportFactory({_SECURITY_REF: keys}),
        run_id=uuid4().hex,
        requested_operations={"store.account": _CONSUMER_OPERATIONS},
    )


def _new_consumer(runtime, projection_path: str) -> ServiceAccountConsumer:
    return ServiceAccountConsumer(
        runtime,
        "store.account",
        account_ref=_ACCOUNT_REF,
        path=projection_path,
        projection_version=_PROJECTION_PATH_VERSION,
        initial_state=_INITIAL_STATE,
        expected_account_scope=_JOURNAL_ACCOUNT_SCOPE,
        expected_strategy_scope=_SHARED_FACT_SCOPE,
        snapshot_reducer=_snapshot_reducer,
        event_reducer=_event_reducer,
        limit=100,
    )


def _spawn_bounded_host(
    context,
    endpoint: str,
    registry_path: str,
    journal_path: str,
    server_keys: tuple[bytes, bytes],
    client_public: bytes,
    server_cap: int,
):
    raw_config = _service_config(endpoint)
    raw_config["communication"]["profiles"][_LISTENER_REF]["options"]["max_message_bytes"] = (
        server_cap
    )
    parsed_config = parse_service_config(raw_config)
    assert (
        parsed_config.communication["profiles"][_LISTENER_REF]["options"]["max_message_bytes"]
        == server_cap
    )
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=_service_process,
        args=(
            child,
            raw_config,
            registry_path,
            journal_path,
            server_keys[0],
            server_keys[1],
            client_public,
        ),
        name=f"synthetic-account-page-bounds-{server_cap}",
    )
    process.start()
    child.close()
    return {"process": process, "pipe": parent, "closed": False}


def _projection_view(checkpoint) -> dict[str, object]:
    return {
        "applied_count": checkpoint.projection["applied_count"],
        "event_ids": list(checkpoint.projection["event_ids"]),
        "last_sequence": checkpoint.projection["last_sequence"],
    }


@pytest.mark.parametrize(
    ("server_cap", "client_cap"),
    ((_LARGE_CAP, _SMALL_CAP), (_SMALL_CAP, _LARGE_CAP)),
    ids=("client-cap-smaller", "server-cap-smaller"),
)
def test_curve_consumer_bounds_pages_and_rejects_unfit_event(
    tmp_path, server_cap: int, client_cap: int
) -> None:
    context = multiprocessing.get_context("spawn")
    endpoint = _available_tcp_endpoint()
    registry_path = str(tmp_path / "service-registry.sqlite3")
    journal_path = str(tmp_path / "account-journal.sqlite3")
    projection_path = str(tmp_path / "account-projection.sqlite3")
    _append_events(journal_path)

    server_keys = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    client_keys = ClientCurveKeys(server_keys[0], client_public, client_secret)
    host_record = None
    runtime = None
    consumer = None
    cleanup_failures: list[str] = []
    try:
        host_record = _spawn_bounded_host(
            context,
            endpoint,
            registry_path,
            journal_path,
            server_keys,
            client_public,
            server_cap,
        )
        assert _await_host(host_record) == endpoint

        runtime = _new_bounded_runtime(endpoint, client_keys, client_cap)
        runtime.start()
        binding_view = runtime.account_binding_view(
            "store.account",
            _ACCOUNT_REF,
            required_operations=_CONSUMER_OPERATIONS,
        )
        assert binding_view.max_message_bytes == client_cap
        consumer = _new_consumer(runtime, projection_path)
        reads: list[tuple[dict[str, object], tuple[str, ...], int | None]] = []
        rejected_reads: list[str | None] = []
        rejected_cap_hints: list[int | None] = []
        original_call = runtime.call

        def observe_real_calls(self, mount, operation, arguments, **kwargs):
            try:
                result = original_call(mount, operation, arguments, **kwargs)
            except Exception as exc:
                if operation == "account.read_after":
                    rejected_reads.append(getattr(exc, "code", None))
                    rejected_cap_hints.append(kwargs.get("max_reply_message_bytes"))
                raise
            if operation == "account.read_after" and arguments.get("track_delivery") is True:
                reads.append(
                    (
                        dict(arguments),
                        tuple(event["event_id"] for event in result["events"]),
                        kwargs.get("max_reply_message_bytes"),
                    )
                )
            return result

        runtime.call = MethodType(observe_real_calls, runtime)
        checkpoints = [consumer.poll() for _ in range(3)]
        assert [checkpoint.cursor for checkpoint in checkpoints] == [1, 2, 3]
        assert all(checkpoint.pending_ack is False for checkpoint in checkpoints)
        assert [page[1] for page in reads] == [(event_id,) for event_id in _EXPECTED_EVENT_IDS]
        assert all(page[0]["limit"] == 100 for page in reads)
        assert [page[0]["cursor"] for page in reads] == [None, 1, 2]
        assert all(page[0]["track_delivery"] is True for page in reads)
        assert all(page[2] == client_cap for page in reads)
        assert _projection_view(checkpoints[-1]) == {
            "applied_count": 3,
            "event_ids": list(_EXPECTED_EVENT_IDS),
            "last_sequence": 3,
        }
        epoch = checkpoints[-1].stream_epoch
        assert isinstance(epoch, str) and epoch
        before_oversize = _progress(runtime.call("store.account", "account.progress", {}))
        assert before_oversize == {
            "stream_epoch": epoch,
            "offered_cursor": 3,
            "acknowledged_cursor": 3,
        }

        oversized = GatewayEventJournal(journal_path, clock=lambda: 1_800_000_100.0).append(
            _JOURNAL_ACCOUNT_SCOPE,
            _SHARED_FACT_SCOPE,
            "bounded-event-4-oversized",
            "position_update",
            {
                "sequence": 4,
                "source": "synthetic-page-boundary",
                "padding": "y" * _OVERSIZED_PAYLOAD_BYTES,
            },
            observed_at=1_800_000_104.0,
        )
        assert oversized.sequence == 4
        with pytest.raises(ServiceAccountConsumerError) as too_large:
            consumer.poll()
        assert too_large.value.code == "REPLY_TOO_LARGE"
        assert rejected_reads == ["REPLY_TOO_LARGE"]
        assert rejected_cap_hints == [client_cap]

        after_oversize = consumer.checkpoint()
        assert after_oversize.stream_epoch == epoch
        assert after_oversize.cursor == 3
        assert after_oversize.pending_ack is False
        assert _projection_view(after_oversize) == _projection_view(checkpoints[-1])
        unchanged_progress = _progress(runtime.call("store.account", "account.progress", {}))
        assert unchanged_progress == before_oversize
        assert host_record["process"].is_alive()

        consumer.close()
        consumer = None
        runtime.close()
        assert runtime.state == "closed"
        assert runtime.cleanup_failures == 0
        assert runtime.unregister_uncertainties == 0
        _close_host_normally(host_record)
    finally:
        if consumer is not None:
            with suppress(Exception):
                consumer.close()
        _close_runtime(runtime, cleanup_failures)
        if host_record is not None:
            _cleanup_host(host_record, cleanup_failures)
        if cleanup_failures:
            raise AssertionError(
                f"local page-boundary integration cleanup failed: {tuple(cleanup_failures)}"
            )
