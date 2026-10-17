"""A late strategy CLOSE fill remains bound to its original Risk reservation.

The accepted DIM04 close fixtures seed NET/ALL strategy-owned close credit.
This no-Host node drives the ordinary RiskOrderEntryService and durable Risk
gate directly; only the BtApi provider feed is synthetic. It is local owner
evidence, not exchange or real-account acceptance.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType
from typing import Any

import pytest

from bt_api_base.account_protocol import AccountResourceKey
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_risk import (
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskRegistry,
)
from bt_api_py.bt_api import BtApi
from bt_api_py.service_risk_order_entry import RiskOrderEntryService, RiskOrderTarget
from bt_api_py.service_risk_order_journal import (
    RiskOrderJournal,
    RiskOrderJournalError,
    _fence_digest,
    _terminal_close_observation_history,
    _terminal_close_source_fences,
    _terminal_exposure_lifecycle,
    _terminal_exposure_source_projection,
    canonical_json,
    sha256_text,
    _is_v2_fill_event,
    _is_v2_order_event,
)
from tests.integration import test_dim01_identity_isolation_service as dim01
from tests.integration import test_dim04_strategy_account_isolation as dim04
from tests.integration import test_dim04_strategy_net_close_crash_recovery as crash_recovery
from tests.unit import test_service_risk_order_exposure_integration as exposure
from tests.unit import test_service_risk_only_order_integration as order_contract


_INTENT_ID = "dim04-late-fill-close-strategy-a"
_QUANTITY = Decimal("0.75")


@contextmanager
def _synthetic_provider_catalog():
    """Run the real BtApi constructor without unrelated installed providers."""

    from bt_api_base.plugins.loader import PluginLoader

    bt_api_module = importlib.import_module("bt_api_py.bt_api")
    original_discover = PluginLoader.__dict__["_discover_entry_points"]
    original_plugins_loaded = bt_api_module._plugins_loaded
    discovered: list[str] = []

    def discover(self, group: str):
        discovered.append(group)
        if group == "bt_api.plugins":
            return []
        return original_discover(self, group)

    setattr(PluginLoader, "_discover_entry_points", discover)
    bt_api_module._plugins_loaded = False
    try:
        yield discovered
    finally:
        setattr(PluginLoader, "_discover_entry_points", original_discover)
        bt_api_module._plugins_loaded = original_plugins_loaded


class _LateCloseAckLostFeed(exposure._V2SyntheticBtApiFeed):
    """Persist one accepted close, then lose its acknowledgement once."""

    def __init__(self, ledger_path: str) -> None:
        super().__init__(ledger_path, ("open",))
        self.write_client_order_ids: list[str] = []
        self.query_client_order_ids: list[str] = []
        self.fill_query_client_order_ids: list[str] = []

    def make_order(self, *args: Any, client_order_id: str, **kwargs: Any) -> dict[str, Any]:
        result = super().make_order(*args, client_order_id=client_order_id, **kwargs)
        self.write_client_order_ids.append(client_order_id)
        # The provider has durably accepted the original client ID, but the
        # dispatch caller loses the response and must retain its Risk permit.
        raise RuntimeError("synthetic acknowledgement lost after provider write")

    def query_order(self, symbol: str, client_order_id: str, **kwargs: Any) -> dict[str, Any]:
        self.query_client_order_ids.append(client_order_id)
        return super().query_order(symbol, client_order_id, **kwargs)

    def get_deals(
        self, symbol: str | None, *, client_order_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        self.fill_query_client_order_ids.append(client_order_id)
        return super().get_deals(symbol, client_order_id=client_order_id, **kwargs)


def _frozen_arguments(arguments: Any) -> MappingProxyType:
    frozen = dict(arguments)
    if "tags" in frozen:
        frozen["tags"] = MappingProxyType(dict(frozen["tags"]))
    return MappingProxyType(frozen)


def _rehash_provider_observation(payload: dict[str, Any]) -> dict[str, Any]:
    projected = dict(payload)
    projected.pop("provider_observation_sha256", None)
    # These fields are added by the Risk journal after the provider digest is
    # computed and therefore are intentionally outside its source projection.
    projected.pop("dispatch_attempt_count", None)
    projected.pop("updated_at_ns", None)
    return {
        **payload,
        "provider_observation_sha256": sha256_text(canonical_json(projected)),
    }


def _assert_close_replay_source_guards(
    journal: RiskOrderJournal,
    *,
    journal_path: str,
    scope,
    principal_ref: str,
    intent_id: str,
    claim,
    tampered_path,
) -> None:
    """Exercise lease, identity, and hash-chain rejection on the real source rows."""

    command = journal.get_command(scope, principal_ref, intent_id)
    assert command is not None and command.state == "ACKED_TRACKED", command
    with closing(sqlite3.connect(journal_path)) as connection:
        connection.row_factory = sqlite3.Row
        command_row = connection.execute(
            "SELECT * FROM risk_order_commands WHERE scope_key=? AND principal_ref=? AND intent_id=?",
            (scope.key, principal_ref, intent_id),
        ).fetchone()
        event_rows = connection.execute(
            "SELECT * FROM risk_order_events WHERE scope_key=? AND principal_ref=? AND intent_id=? ORDER BY revision",
            (scope.key, principal_ref, intent_id),
        ).fetchall()
        head = connection.execute(
            "SELECT revision FROM risk_order_events WHERE scope_key=? ORDER BY revision DESC LIMIT 1",
            (scope.key,),
        ).fetchone()
        assert command_row is not None and event_rows and head is not None
        events = tuple(journal._event_from_row(row, scope) for row in event_rows)
        assert RiskOrderJournal._verify_event_chains(
            connection,
            command_row,
            scope,
            int(head["revision"]),
            target_revision=command.latest_revision,
        )

    # A fresh current lease must accept the persisted CLOSE source and bind its
    # terminal proof to the final ORDER/FILL pair, not the first partial pair.
    with journal.writer_fence(scope) as current_fence:
        source_command, source_events, _source_fence, ack_event = (
            _terminal_exposure_source_projection(
                command, command_row, events, current_fence
            )
        )
        assert ack_event is not None and ack_event.event_type == "ACKED_TRACKED"
        order_event, fill_event, unknown_event = _terminal_close_observation_history(
            source_command, command_row, source_events
        )
        assert order_event.revision < fill_event.revision
        assert fill_event.payload["filled_quantity"] == "0.75"
        assert unknown_event is None
        _terminal_close_source_fences(source_events, current_fence)
        _terminal_exposure_lifecycle(
            source_command,
            command_row,
            source_events,
            current_fence,
            expected_claim=claim,
        )

        # Reusing the prior query epoch after its UNKNOWN boundary is invalid,
        # even if the test recomputes the epoch digest for the forged pair.
        fills = [event for event in source_events if event.event_type == "FILL_OBSERVATION"]
        assert len(fills) == 2, fills
        prior_token = fills[-2].writer_fencing_token
        latest_order = source_events[-2]
        latest_fill = source_events[-1]
        reused_epoch = tuple(
            replace(
                event,
                writer_fencing_token=prior_token,
                writer_fence_sha256=_fence_digest(
                    scope, event.writer_owner_id, prior_token
                ),
            )
            if event in (latest_order, latest_fill)
            else event
            for event in source_events
        )
        with pytest.raises(RiskOrderJournalError, match="TERMINAL_EXPOSURE_SOURCE_FENCE_INVALID"):
            _terminal_close_source_fences(reused_epoch, current_fence)

        # ORDER and FILL must be written under the same exact owner lease.
        wrong_owner = list(source_events)
        wrong_owner[-1] = replace(
            wrong_owner[-1],
            writer_owner_id="different-risk-writer",
            writer_fence_sha256=_fence_digest(
                scope,
                "different-risk-writer",
                wrong_owner[-1].writer_fencing_token,
            ),
        )
        with pytest.raises(RiskOrderJournalError, match="TERMINAL_EXPOSURE_SOURCE_FENCE_INVALID"):
            _terminal_close_source_fences(tuple(wrong_owner), current_fence)

        # A source event from another business intent cannot be spliced into
        # this order even when its payload is otherwise identical.
        wrong_intent = list(source_events)
        wrong_intent[-1] = replace(wrong_intent[-1], intent_id="other-close-intent")
        with pytest.raises(RiskOrderJournalError, match="TERMINAL_EXPOSURE_EVENT_SEQUENCE_INVALID"):
            _terminal_exposure_lifecycle(
                source_command,
                command_row,
                tuple(wrong_intent),
                current_fence,
                expected_claim=claim,
            )

        # A later query must remain on the first provider order identity. Keep
        # its normalized order/fill rows mutually consistent and recompute the
        # provider observation digests so the cross-query lineage check is the
        # only reason this otherwise equivalent replay is refused.
        changed_provider_order_id = "provider-rebound-" + command.client_order_id
        later_order_event, later_fill_event = source_events[-2:]
        changed_order_payload = dict(later_order_event.payload)
        changed_order_record = dict(changed_order_payload["record"])
        changed_order_record["order_id"] = changed_provider_order_id
        changed_order_payload["record"] = changed_order_record
        changed_order_payload["provider_order_id"] = changed_provider_order_id
        changed_order_payload = _rehash_provider_observation(changed_order_payload)

        changed_fill_payload = dict(later_fill_event.payload)
        changed_fill_payload["records"] = [
            {**record, "order_id": changed_provider_order_id}
            for record in changed_fill_payload["records"]
        ]
        changed_fill_payload = _rehash_provider_observation(changed_fill_payload)
        assert _is_v2_order_event(changed_order_payload)
        assert _is_v2_fill_event(changed_fill_payload)
        changed_provider_order_events = list(source_events)
        changed_provider_order_events[-2] = replace(
            later_order_event, payload=changed_order_payload
        )
        changed_provider_order_events[-1] = replace(
            later_fill_event, payload=changed_fill_payload
        )
        with pytest.raises(
            RiskOrderJournalError, match="TERMINAL_EXPOSURE_SOURCE_INVALID"
        ):
            _terminal_close_observation_history(
                source_command, command_row, tuple(changed_provider_order_events)
            )

    # An edited observation without a matching record hash must fail the
    # account/command hash-chain verifier; the original journal stays intact.
    source_connection = sqlite3.connect(journal_path)
    tampered_connection = sqlite3.connect(str(tampered_path))
    try:
        source_connection.backup(tampered_connection)
    finally:
        source_connection.close()
        tampered_connection.close()
    tampered_connection = sqlite3.connect(str(tampered_path))
    try:
        tampered_connection.row_factory = sqlite3.Row
        immutable_guards = tampered_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='risk_order_events'"
        ).fetchall()
        assert immutable_guards, "Risk event append-only guards were not installed"
        # This is an isolated cloned database used only to model external
        # corruption; remove its append-only trigger so the test can inject
        # bytes without changing the live source journal.
        for guard in immutable_guards:
            name = str(guard["name"]).replace('"', '""')
            tampered_connection.execute(f'DROP TRIGGER "{name}"')
        first_fill = tampered_connection.execute(
            "SELECT revision,payload_json FROM risk_order_events "
            "WHERE scope_key=? AND principal_ref=? AND intent_id=? AND event_type='FILL_OBSERVATION' "
            "ORDER BY revision LIMIT 1",
            (scope.key, principal_ref, intent_id),
        ).fetchone()
        assert first_fill is not None
        payload = json.loads(first_fill["payload_json"])
        payload["filled_quantity"] = "0.26"
        tampered_connection.execute(
            "UPDATE risk_order_events SET payload_json=? WHERE scope_key=? AND principal_ref=? AND intent_id=? AND revision=?",
            (
                canonical_json(payload),
                scope.key,
                principal_ref,
                intent_id,
                int(first_fill["revision"]),
            ),
        )
        tampered_connection.commit()
        tampered_command_row = tampered_connection.execute(
            "SELECT * FROM risk_order_commands WHERE scope_key=? AND principal_ref=? AND intent_id=?",
            (scope.key, principal_ref, intent_id),
        ).fetchone()
        head = tampered_connection.execute(
            "SELECT revision FROM risk_order_events WHERE scope_key=? ORDER BY revision DESC LIMIT 1",
            (scope.key,),
        ).fetchone()
        assert tampered_command_row is not None and head is not None
        assert not RiskOrderJournal._verify_event_chains(
            tampered_connection,
            tampered_command_row,
            scope,
            int(head["revision"]),
            target_revision=command.latest_revision,
        )
    finally:
        tampered_connection.close()


def test_dim04_close_late_partial_then_terminal_settles_original_owner_once(tmp_path) -> None:
    """UNKNOWN and partial observations retain A's reservation; B is untouched."""

    # Reuse the accepted crash/recovery fixture's exact synthetic NET position
    # seed and DIM04's shared-account strategy-specific close-credit setup.
    provider_path = str(tmp_path / "dim04-late-close-provider.sqlite3")
    crash_recovery.net_close._seed_synthetic_net_position(provider_path)
    risk_path = str(tmp_path / "dim04-late-close-risk.sqlite3")
    journal_path = str(tmp_path / "dim04-late-close-journal.sqlite3")
    _, before_a, before_b = dim04._seed_shared_account_close(risk_path, journal_path)

    account_scope = dim01._ACCOUNT_SPOT_A
    strategy_scope = dim01._SCOPES[dim01._SCOPE_SPOT_A]
    principal_ref = dim01._PRINCIPAL_A
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, dim01._STRATEGY_A)
    before_a_b = {dim01._STRATEGY_A: before_a, dim01._STRATEGY_B: before_b}

    # The Risk owner uses a second journal/gate handle over the same durable
    # stores, as it does in the ServiceHost composition.
    journal = RiskOrderJournal(journal_path, writer_id="dim04-late-close-order-owner")
    gate = DurableRiskGate(
        risk_path,
        dim04._risk_policy(journal),
        execution_journal_authority=journal,
        gross_exposure_authority=journal,
    )
    bt_api = None
    try:
        metadata = dim01._metadata(
            dim01._BTC, dim01._VERSION_BTC_SPOT, multiplier="1"
        )
        registry = InstrumentRiskRegistry((metadata,))
        resource = AccountResourceKey(
            "spot", f"{account_scope.account_id}-{account_scope.environment}"
        )
        resource_by_instrument = {dim01._BTC: resource}
        target = RiskOrderTarget(
            account_scope=account_scope,
            strategy_id=dim01._STRATEGY_A,
            exchange_name=dim01._EXCHANGE_SPOT_A,
            provider_account_id=account_scope.account_id,
            registry=registry,
            resource_by_instrument=resource_by_instrument,
        )
        mapper = InstrumentRiskAdmissionMapper(
            account_scope,
            registry,
            clock_ns=time.time_ns,
            allocation_reader=lambda execution_scope: gate.get_strategy_allocation(
                account_scope, execution_scope.strategy_id
            ),
            resource_by_instrument=resource_by_instrument,
            wire_account_ref=account_scope.account_id,
        )
        admission = DurableRiskAdmissionAdapter(gate, mapper)
        route = dim01._ROUTES[dim01._ROUTE_SPOT_A]
        provider_contract = dim01._provider_contract(
            target.exchange_name,
            route["profile_id"],
            trade_id_domain_ref=route["trade_id_domain_ref"],
            trade_id_scope=route["trade_id_scope"],
        )
        provider_contract = replace(
            provider_contract,
            order_schema=exposure._V2_ORDER_SCHEMA,
            fill_schema=exposure._V2_FILL_SCHEMA,
            supported_position_effects=("OPEN", "CLOSE"),
        )
        provider = _LateCloseAckLostFeed(provider_path)
        with _synthetic_provider_catalog() as catalog_groups:
            bt_api = BtApi(debug=False)
        assert catalog_groups == ["bt_api.plugins"], catalog_groups
        bt_api.exchange_feeds[target.exchange_name] = provider
        service = RiskOrderEntryService(
            bt_api,
            {strategy_scope: target},
            {strategy_scope: admission},
            journal,
            gate,
            {strategy_scope: provider_contract},
        )

        def call(operation: str, arguments: Any):
            return service.handle(
                operation,
                principal_ref=principal_ref,
                identity=identity,
                scope=strategy_scope,
                arguments=_frozen_arguments(arguments),
            )

        submitted = call(
            "orders.submit",
            order_contract._submit_arguments(
                intent_id=_INTENT_ID,
                signal_id="signal-" + _INTENT_ID,
                instrument=dim01._BTC,
                side="SELL",
                position_effect="CLOSE",
                order_type="LIMIT",
                quantity=format(_QUANTITY, "f"),
                price="20",
                time_in_force="GTC",
                reduce_only=True,
                metadata_version=dim01._VERSION_BTC_SPOT,
                deadline_ns=None,
                tags={},
            ),
        )
        submitted_record = submitted["result"]["record"]
        assert submitted["ok"] is True and submitted_record["state"] == "UNKNOWN", submitted
        assert submitted_record["dispatch_attempts"] == 1, submitted_record
        assert submitted_record["permit_reference"], submitted_record
        assert len(provider.write_client_order_ids) == 1, provider.write_client_order_ids
        client_order_id = provider.write_client_order_ids[0]
        assert submitted_record["provider_order_id"] is None, submitted_record

        owner_after_unknown = dim04._snapshot_signature(
            gate, dim01._STRATEGY_A
        )
        other_after_unknown = dim04._snapshot_signature(
            gate, dim01._STRATEGY_B
        )
        assert owner_after_unknown[2] == before_a_b[dim01._STRATEGY_A][2] + _QUANTITY
        assert owner_after_unknown[3] == before_a_b[dim01._STRATEGY_A][3] - _QUANTITY
        assert other_after_unknown == before_a_b[dim01._STRATEGY_B]

        partial_fill = exposure._V2SyntheticBtApiFeed._fill(
            account_id=order_contract._PROVIDER_ACCOUNT,
            client_order_id=client_order_id,
            order_id="provider-" + client_order_id,
            symbol=dim01._BTC,
            side="sell",
            trade_id="late-fill-" + client_order_id + "-1",
            quantity="0.25",
            price="20",
            revision=2,
            fee="-0.01",
        )
        exposure._store_provider_observation(
            provider_path,
            client_order_id,
            status="partial",
            filled="0.25",
            order_revision=2,
            fills=[partial_fill],
        )
        partial = call("orders.query", {"intent_id": _INTENT_ID})
        assert partial["ok"] is True and partial["result"]["record"]["state"] == "UNKNOWN", partial
        assert provider.query_client_order_ids == [client_order_id]
        assert provider.fill_query_client_order_ids == [client_order_id]
        assert len(provider.snapshot()[0]) == 1
        owner_after_partial = dim04._snapshot_signature(gate, dim01._STRATEGY_A)
        assert owner_after_partial[2:] == owner_after_unknown[2:]
        assert dim04._snapshot_signature(gate, dim01._STRATEGY_B) == before_a_b[dim01._STRATEGY_B]
        assert dim04._read_rows(
            risk_path,
            "SELECT permit_id FROM risk_close_order_resolutions WHERE permit_id=?",
            (submitted_record["permit_reference"],),
        ) == ()

        fills = [
            partial_fill,
            exposure._V2SyntheticBtApiFeed._fill(
                account_id=order_contract._PROVIDER_ACCOUNT,
                client_order_id=client_order_id,
                order_id="provider-" + client_order_id,
                symbol=dim01._BTC,
                side="sell",
                trade_id="late-fill-" + client_order_id + "-2",
                quantity="0.5",
                price="20",
                revision=3,
                fee="-0.02",
            ),
        ]
        exposure._store_provider_observation(
            provider_path,
            client_order_id,
            status="filled",
            filled="0.75",
            order_revision=3,
            fills=fills,
        )
        terminal = call("orders.query", {"intent_id": _INTENT_ID})
        terminal_record = terminal["result"]["record"]
        assert terminal["ok"] is True and terminal_record["state"] == "FILLED", terminal
        assert terminal_record["filled_quantity"] == "0.75", terminal_record
        assert terminal_record["provider_order_id"] == "provider-" + client_order_id
        assert terminal_record["dispatch_attempts"] == 1, terminal_record
        assert provider.query_client_order_ids == [client_order_id, client_order_id]
        assert provider.fill_query_client_order_ids == [client_order_id, client_order_id]
        assert len(provider.snapshot()[0]) == 1

        close_rows = dim04._read_rows(
            risk_path,
            "SELECT permit_id,strategy_id,position_effect,terminal_state,requested_quantity,filled_quantity "
            "FROM risk_close_order_resolutions WHERE permit_id=?",
            (submitted_record["permit_reference"],),
        )
        assert close_rows == (
            {
                "permit_id": submitted_record["permit_reference"],
                "strategy_id": dim01._STRATEGY_A,
                "position_effect": "CLOSE",
                "terminal_state": "FILLED",
                "requested_quantity": "0.75",
                "filled_quantity": "0.75",
            },
        ), close_rows
        close_receipt = gate.dispatch_close_resolution_receipt(
            submitted_record["permit_reference"]
        )
        assert close_receipt.status.value == "TERMINAL_CLOSE_RESOLVED", close_receipt
        assert close_receipt.position_effect == "CLOSE", close_receipt
        assert close_receipt.dispatch_latch_active is False, close_receipt
        assert close_receipt.filled_quantity == close_receipt.requested_quantity == _QUANTITY
        _assert_close_replay_source_guards(
            journal,
            journal_path=journal_path,
            scope=account_scope,
            principal_ref=principal_ref,
            intent_id=_INTENT_ID,
            claim=close_receipt.claim,
            tampered_path=tmp_path / "dim04-late-close-tampered.sqlite3",
        )

        owner_after_terminal = dim04._snapshot_signature(gate, dim01._STRATEGY_A)
        other_after_terminal = dim04._snapshot_signature(gate, dim01._STRATEGY_B)
        assert owner_after_terminal[2:] == owner_after_unknown[2:]
        assert other_after_terminal == before_a_b[dim01._STRATEGY_B]

        query_counts = provider.snapshot()[1]
        repeat = call("orders.query", {"intent_id": _INTENT_ID})
        assert repeat == terminal, (repeat, terminal)
        assert provider.snapshot()[1] == query_counts
        assert provider.query_client_order_ids == [client_order_id, client_order_id]
        assert provider.fill_query_client_order_ids == [client_order_id, client_order_id]
        assert len(provider.snapshot()[0]) == 1
        assert dim04._read_rows(
            risk_path,
            "SELECT permit_id FROM risk_close_order_resolutions WHERE permit_id=?",
            (submitted_record["permit_reference"],),
        ) == ({"permit_id": submitted_record["permit_reference"]},)
        assert dim04._snapshot_signature(gate, dim01._STRATEGY_A) == owner_after_terminal
        assert dim04._snapshot_signature(gate, dim01._STRATEGY_B) == before_a_b[dim01._STRATEGY_B]
    finally:
        gate.close()
        if bt_api is not None:
            bt_api.close()
