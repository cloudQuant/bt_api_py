"""Synthetic trade-date propagation across a spawned CURVE Gateway."""

from __future__ import annotations

import multiprocessing
import os
import queue
from dataclasses import replace
from pathlib import Path

import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.gateway_direct_owner import (
    GatewayDirectOwner,
    GatewayDirectOwnerResources,
)
from bt_api_py.gateway_direct_service import (
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ServiceRuntimeError,
)
from tests.integration.test_iter63_gateway_direct_service import (
    ACCOUNT,
    CLIENT_A,
    CLIENT_B,
    DOMAIN,
    SERVICE,
    _service_config,
)
from tests.integration.test_service_account_instrument_metadata import _free_endpoint

EVENTS = frozenset({"direct.events.read_after", "direct.events.progress", "direct.events.ack"})
CLIENT_C = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-c", "strategy-c")
INSTRUMENT = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2701", "rb2701")


from tests.integration.test_iter63_gateway_execution_stream_processes import _client, _observed


def _facts_from_sealed_query():
    from bt_api_ctp.gateway_return_projection import project_gateway_return
    from bt_api_py.direct_write import project_direct_strong_identity_keys
    from tests.integration.test_iter63_ctp_gateway_return_ingestion import DAY, _reader, _trade

    reader, _, native = _reader()
    facts = []
    for row in (_trade(), _trade(TradeDate="20261105")):
        native.rows["trades"] = [row]
        batch = reader.read(kind="trades", timeout=1)
        reader.assert_current(batch)
        projection = project_gateway_return(
            "trade",
            batch.rows[0],
            account_scope=ACCOUNT,
            source_ref=batch.source_ref,
            expected_trading_day=DAY,
            expected_broker="9999",
            expected_investor="account",
            instrument=INSTRUMENT,
            source_generation=batch.connection_generation,
        )
        keys = project_direct_strong_identity_keys(projection.venue_identity, day_scoped_ids=True)
        assert len(keys) == 1
        facts.append(replace(projection.fact, identity_keys=keys, venue_key=keys[0]))
    return replace(facts[0], schema_version=1, trade_date=None), facts[1]


def _seed_one(journal, ledger):
    legacy, dated = _facts_from_sealed_query()
    row = journal.reserve(
        CLIENT_A.actor_ref,
        "original-request",
        CLIENT_A.principal_ref,
        {"symbol": "rb2701", "side": "buy", "offset": "open", "quantity": "2"},
    )
    journal.begin_dispatch(
        CLIENT_A.actor_ref,
        "original-request",
        row.reservation_id,
        row.digest,
        CLIENT_A.principal_ref,
        1,
        {
            "exchange_name": "CTP___FUTURE",
            "client_order_id": "000000000001",
            "session_generation": 1,
            "fields_json": "{}",
        },
        legacy.identity_keys,
    )
    assert ledger.ingest(legacy).disposition == "ATTRIBUTED"
    return dated


def _gateway(root, endpoint, server_keys, keys, messages, controls, enabled):
    from bt_api_py.gateway_execution_stream_service import GatewayExecutionStreamService

    host = owner = stream = None
    try:
        root = Path(root)
        directory = root / "guard"
        directory.mkdir()
        resources = []

        def owner_factory():
            guard = LocalAccountSessionGuard(directory, ACCOUNT, "synthetic-owner")
            journal = GatewayDirectCommandJournal.create(
                root / "journal.sqlite", ACCOUNT, generation=guard.acquire()
            )
            ledger = GatewayDirectExecutionLedger(journal, guard)
            dated_fact = _seed_one(journal, ledger)
            dispatcher = GatewayDirectDispatcher(
                journal, "TEST", guard, lambda *_: None, lambda _: (), lambda _: True
            )
            resources.append((journal, ledger, dated_fact))
            return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

        owner = GatewayDirectOwner(
            owner_factory,
            queue_size=4,
            max_queue_wait_seconds=20,
            busy_timeout_seconds=20,
        )
        assert owner.start(10)
        journal, ledger, dated_fact = resources[0]
        grants = {
            i.principal_ref: GatewayDirectGrant(i.actor_ref, frozenset({"X"}))
            for i in (CLIENT_A, CLIENT_B, CLIENT_C)
        }
        if enabled:
            stream = GatewayExecutionStreamService(
                ledger,
                journal,
                {i.principal_ref: i.actor_ref for i in (CLIENT_A, CLIENT_B, CLIENT_C)},
                execution_domain_ref=DOMAIN,
            )
        binding = GatewayDirectServiceBinding(
            journal,
            grants,
            owner=owner,
            execution_stream=stream,
            execution_domain_ref=DOMAIN,
        )

        def grant(identity):
            operations = (
                EVENTS if enabled and identity != CLIENT_C else frozenset({"direct.command_status"})
            )
            return PrincipalGrant(
                identity,
                (
                    ResourceGrant(
                        "gateway-a",
                        "service.direct_write",
                        "scope-a",
                        WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref),
                        operations,
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
            curve_public_key=server_keys[0],
            curve_secret_key=server_keys[1],
            principals={
                key[0]: identity.principal_ref
                for key, identity in zip(keys, (CLIENT_A, CLIENT_B, CLIENT_C), strict=True)
            },
            grants={
                identity.principal_ref: (grant(identity),)
                for identity in (CLIENT_A, CLIENT_B, CLIENT_C)
            },
        )
        host = ServiceHost(
            _service_config(endpoint),
            authority,
            root / "registry.sqlite",
            lambda *_: ComponentResources.borrowed({"binding": binding}, ready=lambda: True),
        )
        host.start()
        operations = frozenset(
            op
            for component in host._components
            for capability in component.capabilities
            for op in capability.operations
        )
        messages.put(("ready", os.getpid(), journal.journal_id, operations))
        while True:
            try:
                command = controls.get_nowait()
            except queue.Empty:
                command = None
            if command == "stop":
                break
            if command == "date":

                def ingest_date():
                    result = ledger.ingest(dated_fact)
                    return result.disposition, ledger.position(CLIENT_A.actor_ref, INSTRUMENT)

                disposition, position = owner.submit_service_call(ingest_date).result(
                    timeout_seconds=10
                )
                messages.put(("ingested", disposition, position))
            host.serve_once(10)
    except BaseException as error:
        import traceback

        messages.put(("error", type(error).__name__, str(error), traceback.format_exc()))
    finally:
        try:
            if host is not None and host.state == "started":
                host.close()
            if stream is not None:
                stream.close()
            if owner is not None and not owner.close(10):
                raise RuntimeError("OWNER_CLOSE_FAILED")
            messages.put(("closed", os.getpid()))
        except BaseException as error:
            messages.put(("cleanup_error", type(error).__name__, str(error)))


@pytest.fixture
def date_service(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    messages, controls = ctx.Queue(), ctx.Queue()
    server_keys, keys = zmq.curve_keypair(), tuple(zmq.curve_keypair() for _ in range(3))
    endpoint = _free_endpoint()
    process = ctx.Process(
        target=_gateway, args=(str(tmp_path), endpoint, server_keys, keys, messages, controls, True)
    )
    process.start()
    try:
        ready = messages.get(timeout=20)
        assert ready[0] == "ready", ready
        assert ready[1] == process.pid != os.getpid()
        yield (endpoint, server_keys[0], keys, ready[2]), messages, controls
    finally:
        controls.put("stop")
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join(5)
            pytest.fail("synthetic Gateway required termination")
        assert process.exitcode == 0
        assert messages.get(timeout=3) == ("closed", process.pid)
        controls.close()
        messages.close()


def test_date_enrichment_over_curve_preserves_quantity_and_cursor(date_service):
    service, messages, controls = date_service
    runtime, client = _client(service, CLIENT_A, "date-run")
    other_runtime, other = _client(service, CLIENT_B, "foreign-run")
    resumed_runtime = None
    try:
        runtime.start(timeout_seconds=10)
        other_runtime.start(timeout_seconds=10)
        first = _observed(client.read_after(), "page")
        assert len(first.events) == 1
        original = first.events[0]
        assert original.request_key == "original-request"
        assert original.observation.trade_date is None
        assert original.observation.trade_metadata_only is False
        assert original.observation.trade_quantity == "1"
        assert (
            _observed(client.ack(original.sequence, first.epoch), "progress").acknowledged_sequence
            == original.sequence
        )
        assert _observed(other.read_after(), "page").events == ()

        controls.put("date")
        ingested = messages.get(timeout=10)
        assert ingested[0] == "ingested", ingested
        assert ingested[2] == "1"
        with pytest.raises(ServiceRuntimeError, match="REPLY_TOO_LARGE"):
            runtime.call(
                "broker.direct_write",
                "direct.events.read_after",
                {"after_sequence": original.sequence, "expected_epoch": first.epoch, "limit": 1},
                max_reply_message_bytes=512,
                timeout_seconds=5,
            )
        progress = _observed(client.progress(), "progress")
        assert (progress.offered_sequence, progress.acknowledged_sequence) == (
            original.sequence,
            original.sequence,
        )
        dated = _observed(client.read_after(original.sequence, first.epoch), "page")
        assert len(dated.events) == 1
        enrichment = dated.events[0]
        assert enrichment.request_key == original.request_key
        assert enrichment.observation.trade_date == "20261105"
        assert enrichment.observation.trade_metadata_only is True
        assert enrichment.observation.trade_quantity == original.observation.trade_quantity
        assert dated.to_mapping()["schema_version"] == 3
        assert enrichment.observation.to_mapping()["schema_version"] == 2
        assert _observed(other.read_after(), "page").events == ()

        controls.put("date")
        duplicate = messages.get(timeout=10)
        assert duplicate[0] == "ingested", duplicate
        assert duplicate[2] == "1"
        assert _observed(client.read_after(enrichment.sequence, dated.epoch), "page").events == ()
        assert (
            _observed(
                client.ack(enrichment.sequence, dated.epoch), "progress"
            ).acknowledged_sequence
            == enrichment.sequence
        )
        runtime.close(timeout_seconds=10)
        resumed_runtime, resumed = _client(service, CLIENT_A, "date-run-restarted")
        resumed_runtime.start(timeout_seconds=10)
        progress = _observed(resumed.progress(), "progress")
        assert progress.acknowledged_sequence == enrichment.sequence
        assert progress.epoch == dated.epoch
    finally:
        for current in (resumed_runtime, other_runtime, runtime):
            if current is not None:
                current.close(timeout_seconds=10)
