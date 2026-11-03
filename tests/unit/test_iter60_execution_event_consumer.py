"""Iteration 60 R2-C: SDK consumer of the Gateway execution event stream.

The consumer reads one strategy's ``stream="execution"`` page, lets a sink
apply it durably, commits the cursor locally, and only then ACKs the Gateway.
In-process tests drive the real ``GatewayReadService`` and journal through
``over_call``; the integration test runs a spawned CURVE ``ServiceHost`` and
kills a consumer process between its sink and its cursor commit.
"""

from __future__ import annotations

import multiprocessing
import os
from contextlib import suppress
from pathlib import Path

import pytest
import zmq
from bt_api_base.account_protocol import AccountKey, AccountResourceKey
from bt_api_base.execution_query_protocol import ExecutionOrderRecord, ExecutionTradeRecord
from bt_api_base.execution_row_observation_protocol import (
    AccountExecutionOrderObservation,
    AccountExecutionTradeObservation,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
from bt_api_gateway.execution_event_publisher import (
    GatewayExecutionEventPublisher,
    GatewayExecutionScopePolicy,
)
from bt_api_gateway.service_component import GatewayReadService, GatewayServiceAccountScope

from bt_api_py.service_execution_event_consumer import (
    ServiceExecutionEventConsumer,
    ServiceExecutionEventConsumerError,
)

ACCOUNT_SCOPE = "journal:execution-consumer"
EXEC_A = "exec:strategy-a"
EXEC_B = "exec:strategy-b"
STATE = "journal:execution-consumer-facts"
DOMAIN = "paper:execution-consumer"
ACCOUNT_REF = "execution-consumer-account"
KEY = AccountKey("synthetic", "paper-exchange", "paper", "acct-x")


def _instrument():
    return MarketInstrumentKey(
        "synthetic", "paper-exchange", "paper", "futures", "SA2701", "SA2701"
    )


def order(order_id="o-1", client="sa-1", status="open", filled="0", quantity="2"):
    return AccountExecutionOrderObservation(
        KEY,
        ExecutionOrderRecord(
            AccountResourceKey("futures", "cross"),
            _instrument(),
            order_id,
            client,
            "verified",
            "sell",
            "unavailable",
            None,
            "unavailable",
            "limit",
            status,
            quantity,
            filled,
            "contracts",
            "1500",
            None,
            "unavailable",
            None,
            "unavailable",
            "2027-01-15T08:00:00Z",
        ),
    )


def trade(trade_id="t-1", order_id="o-1", quantity="1", price="1500"):
    return AccountExecutionTradeObservation(
        KEY,
        ExecutionTradeRecord(
            AccountResourceKey("futures", "cross"),
            _instrument(),
            trade_id,
            order_id,
            "provider_order_identity",
            "sell",
            "unavailable",
            None,
            "unavailable",
            quantity,
            "contracts",
            price,
            None,
            "unavailable",
            None,
            "unavailable",
            "2027-01-15T08:00:01Z",
        ),
    )


def publish(path, *observations):
    GatewayExecutionEventPublisher(
        GatewayEventJournal(path),
        account_key=KEY,
        journal_account_scope=ACCOUNT_SCOPE,
        policy=GatewayExecutionScopePolicy({"sa-": EXEC_A, "sb-": EXEC_B}),
    ).publish(observations)


def _accounts():
    return {
        ACCOUNT_REF: GatewayServiceAccountScope(
            account_ref=ACCOUNT_REF,
            execution_domain_ref=DOMAIN,
            journal_account_scope=ACCOUNT_SCOPE,
            account_read_strategy_scope=STATE,
            execution_scope_map={"strategy-a": EXEC_A, "strategy-b": EXEC_B},
        )
    }


def _principal():
    return GatewayPrincipal(
        "client",
        frozenset({ACCOUNT_SCOPE}),
        frozenset({EXEC_A, EXEC_B, STATE}),
        frozenset({GatewayCommandKind.READ}),
    )


def local_call(journal_path, strategy="strategy-a", calls=None):
    service = GatewayReadService(
        GatewayEventJournal(journal_path),
        accounts=_accounts(),
        principals={"client": _principal()},
    )

    def call(operation, arguments):
        if calls is not None:
            calls.append((operation, dict(arguments)))
        return service.handle(
            operation,
            principal_ref="client",
            scope=WireScope(DOMAIN, account_ref=ACCOUNT_REF, strategy_ref=strategy),
            arguments=arguments,
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, "client", strategy),
        )

    return call


def consumer(tmp_path, call, *, scope=EXEC_A, key=KEY, limit=100):
    return ServiceExecutionEventConsumer.over_call(
        call,
        binding_ref="local-gateway:strategy-a",
        path=tmp_path / "cursor.sqlite3",
        account_key=key,
        expected_account_scope=ACCOUNT_SCOPE,
        expected_strategy_scope=scope,
        limit=limit,
    )


def test_poll_delivers_validated_typed_events_then_acks(tmp_path):
    journal = tmp_path / "gw.db"
    publish(journal, order(), trade(), order(status="filled", filled="2"), order("o-2", "sb-2"))
    calls = []
    reader = consumer(tmp_path, local_call(journal, calls=calls))
    seen = []
    assert reader.poll(seen.extend) == 3
    assert [e.cursor for e in seen] == [1, 2, 3]
    assert seen[1].observation == trade()
    assert [op for op, _ in calls] == ["account.read_after", "account.ack"]
    assert reader.checkpoint().cursor == 3 and not reader.checkpoint().pending_ack
    assert reader.poll(seen.extend) == 0 and len(seen) == 3


def test_sink_failure_or_crash_before_commit_redelivers_the_same_ids(tmp_path):
    journal = tmp_path / "gw.db"
    publish(journal, order(), trade())
    reader = consumer(tmp_path, local_call(journal))

    def failing(_events):
        raise RuntimeError("sink not durable")

    with pytest.raises(RuntimeError):
        reader.poll(failing)
    assert reader.checkpoint().cursor == 0
    first = reader.read()  # read, then "crash" before commit
    reader.close()
    again = consumer(tmp_path, local_call(journal))
    page = again.read()
    assert [e.event_id for e in page.events] == [e.event_id for e in first.events]
    again.commit(page)
    again.acknowledge()
    assert again.checkpoint().cursor == 2


def test_crash_between_commit_and_ack_repeats_only_the_ack(tmp_path):
    journal = tmp_path / "gw.db"
    publish(journal, order(), trade())
    reader = consumer(tmp_path, local_call(journal))
    reader.commit(reader.read())
    assert reader.checkpoint().pending_ack
    reader.close()
    calls = []
    again = consumer(tmp_path, local_call(journal, calls=calls))
    page = again.read()
    assert page.events == () and page.requested_cursor == 2
    assert [op for op, _ in calls] == ["account.ack", "account.read_after"]


def test_events_are_rejected_for_foreign_scope_account_or_tampering(tmp_path):
    journal = tmp_path / "gw.db"
    publish(journal, order())
    (tmp_path / "s").mkdir()
    with pytest.raises(ServiceExecutionEventConsumerError) as scope:
        consumer(tmp_path / "s", local_call(journal), scope=EXEC_B).read()
    assert scope.value.code == "EXECUTION_EVENT_SCOPE_MISMATCH"
    other = AccountKey("synthetic", "paper-exchange", "paper", "other")
    (tmp_path / "k").mkdir()
    with pytest.raises(ServiceExecutionEventConsumerError) as account:
        consumer(tmp_path / "k", local_call(journal), key=other).read()
    assert account.value.code == "EXECUTION_EVENT_ACCOUNT_MISMATCH"
    inner = local_call(journal)

    def tamper(operation, arguments):
        reply = dict(inner(operation, arguments))
        if operation == "account.read_after" and reply["events"]:
            event = dict(reply["events"][0])
            event["event_id"] = "xo-forged"
            reply["events"] = (event,)
        return reply

    (tmp_path / "t").mkdir()
    with pytest.raises(ServiceExecutionEventConsumerError) as forged:
        consumer(tmp_path / "t", tamper).read()
    assert forged.value.code == "INVALID_EXECUTION_EVENT"

    def gap(operation, arguments):
        reply = dict(inner(operation, arguments))
        if operation == "account.read_after" and reply["events"]:
            event = dict(reply["events"][0])
            event["sequence"] = 5
            reply["events"] = (event,)
        return reply

    (tmp_path / "g").mkdir()
    with pytest.raises(ServiceExecutionEventConsumerError) as hole:
        consumer(tmp_path / "g", gap).read()
    assert hole.value.code == "EXECUTION_STREAM_GAP"


def test_cursor_store_is_bound_to_its_stream(tmp_path):
    journal = tmp_path / "gw.db"
    publish(journal, order())
    consumer(tmp_path, local_call(journal)).close()
    with pytest.raises(ServiceExecutionEventConsumerError) as mismatch:
        consumer(tmp_path, local_call(journal), scope=EXEC_B)
    assert mismatch.value.code == "CURSOR_STORE_BINDING_MISMATCH"


# -- spawned CURVE ServiceHost -------------------------------------------------------

_EXIT_AFTER_SINK = 81


def _host_resources(journal_path):
    from bt_api_py.service_components import ComponentResources

    def provider(component, _instance_id, _options, _scope):
        assert component == "gateway"
        from tests.unit import test_service_account_consumer_integration as base

        account = GatewayServiceAccountScope(
            account_ref=base._ACCOUNT_REF,
            execution_domain_ref=base._DOMAIN_REF,
            journal_account_scope=base._JOURNAL_ACCOUNT_SCOPE,
            shared_account_facts_scope=base._SHARED_FACT_SCOPE,
            shared_fact_strategy_refs=frozenset({base._ACTOR_B}),
            execution_scope_map={base._ACTOR_A: EXEC_A},
        )
        principal = GatewayPrincipal(
            principal_id=base._CLIENT_PRINCIPAL,
            account_scopes=frozenset({base._JOURNAL_ACCOUNT_SCOPE}),
            strategy_scopes=frozenset({base._SHARED_FACT_SCOPE, EXEC_A}),
            allowed_kinds=frozenset({GatewayCommandKind.READ}),
        )
        return ComponentResources.borrowed(
            {
                "journal": GatewayEventJournal(journal_path),
                "accounts": {base._ACCOUNT_REF: account},
                "principals": {base._CLIENT_PRINCIPAL: principal},
            },
            ready=lambda: True,
        )

    return provider


def _host_process(
    control, endpoint, registry_path, journal_path, server_public, server_secret, client_public
):
    from tests.unit import test_service_account_consumer_integration as base

    base._resource_provider = _host_resources  # this spawned host only
    base._service_process(
        control,
        base._service_config(endpoint),
        registry_path,
        journal_path,
        server_public,
        server_secret,
        client_public,
    )


def _remote_consumer(runtime, path):
    from tests.unit import test_service_account_consumer_integration as base

    return ServiceExecutionEventConsumer(
        runtime,
        "store.account",
        account_ref=base._ACCOUNT_REF,
        path=path,
        account_key=KEY,
        expected_account_scope=base._JOURNAL_ACCOUNT_SCOPE,
        expected_strategy_scope=EXEC_A,
        limit=10,
    )


def _crash_after_sink(control, endpoint, keys, cursor_path, log_path):
    from tests.unit import test_service_account_consumer_integration as base

    runtime = base._new_runtime(endpoint, keys, base._ACTOR_A)
    runtime.start()
    reader = _remote_consumer(runtime, cursor_path)
    page = reader.read()
    with open(log_path, "a", encoding="utf-8") as stream:
        for event in page.events:
            stream.write(event.event_id + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    control.send(("applied", len(page.events)))
    os._exit(_EXIT_AFTER_SINK)  # the sink's effects are durable; no commit, no ACK


def test_spawned_curve_host_redelivers_after_a_consumer_crash_before_commit(tmp_path):
    from tests.unit import test_service_account_consumer_integration as base

    context = multiprocessing.get_context("spawn")
    endpoint = base._available_tcp_endpoint()
    journal_path = str(tmp_path / "account-journal.sqlite3")
    GatewayExecutionEventPublisher(
        GatewayEventJournal(journal_path),
        account_key=KEY,
        journal_account_scope=base._JOURNAL_ACCOUNT_SCOPE,
        policy=GatewayExecutionScopePolicy({"sa-": EXEC_A}),
    ).publish([order(), trade(), order(status="filled", filled="2")])
    server = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    keys = base.ClientCurveKeys(server[0], client_public, client_secret)
    parent, child = context.Pipe(duplex=True)
    host_process = context.Process(
        target=_host_process,
        args=(
            child,
            endpoint,
            str(tmp_path / "registry.sqlite3"),
            journal_path,
            server[0],
            server[1],
            client_public,
        ),
    )
    host_process.start()
    child.close()
    host = {"process": host_process, "pipe": parent, "closed": False}
    failures: list[str] = []
    runtime = None
    try:
        assert base._await_host(host) == endpoint
        crash_parent, crash_child = context.Pipe(duplex=True)
        crasher = context.Process(
            target=_crash_after_sink,
            args=(
                crash_child,
                endpoint,
                keys,
                str(tmp_path / "cursor.sqlite3"),
                str(tmp_path / "applied.log"),
            ),
        )
        crasher.start()
        crash_child.close()
        assert crash_parent.poll(30)
        assert crash_parent.recv() == ("applied", 3)
        crasher.join(30)
        assert crasher.exitcode == _EXIT_AFTER_SINK
        first_ids = Path(tmp_path / "applied.log").read_text().split()

        runtime = base._new_runtime(endpoint, keys, base._ACTOR_A)
        runtime.start()
        reader = _remote_consumer(runtime, str(tmp_path / "cursor.sqlite3"))
        assert reader.checkpoint().cursor == 0  # the crash left no commit
        seen = []
        assert reader.poll(seen.extend) == 3
        assert [e.event_id for e in seen] == first_ids  # same stable ids: sink dedupes
        assert reader.checkpoint().cursor == 3 and not reader.checkpoint().pending_ack
        assert reader.poll(seen.extend) == 0
        reader.close()
    finally:
        base._close_runtime(runtime, failures)
        base._cleanup_host(host, failures)
    assert failures == []
