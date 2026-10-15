"""Risk-gated orders through spawned ServiceHost, CURVE and the real BtApi."""

from __future__ import annotations

import json
import multiprocessing
import socket
import sqlite3
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import asdict, replace
from decimal import Decimal
from types import MappingProxyType
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
    decode_envelope,
    encode_envelope,
)
from bt_api_risk import (
    AccountScope,
    DispatchClaimBinding,
    DispatchEvidenceClass,
    DispatchResolutionReceiptStatus,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    IntentAction,
    RiskIntent,
    RiskPolicy,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_execution import (
    ServiceExecutionClient,
    ServiceExecutionError,
    _submit_arguments,
)
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_risk_order_entry import (
    _ORDER_FIELDS,
    _RECOVERY_FILL_OBSERVATION_FIELDS,
    _RECOVERY_ORDER_OBSERVATION_FIELDS,
    RiskBtApiProviderContract,
    RiskOrderEntryError,
    RiskOrderEntryService,
    RiskOrderTarget,
    _has_exact_argument_fields,
    _matches_run_wildcard,
    _parse_order,
    _validate_recovery_observation_fields,
    _verify_observation_digest,
)
from bt_api_py.service_risk_order_journal import (
    RiskOrderJournal,
    RiskOrderJournalError,
    canonical_json,
    sha256_text,
)
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_DOMAIN = "paper:risk-order-integration"
_SERVICE_ID = "synthetic-risk-order-service"
_SERVICE_PRINCIPAL = "synthetic-risk-order-principal"
_SERVICE_INSTANCE = "risk-order-owner"
_SERVICE_SCOPE_REF = "risk-order-service-root-v1"
_SERVER_SECURITY_REF = "risk-order-server-curve-v1"
_STRATEGY = "strategy-risk-a"
_PRINCIPAL = "risk-order-strategy-principal"
_ACCOUNT = "account-a"
_ORDER_SCOPE_REF = "strategy-risk-orders-a-v1"
_QUERY_SCOPE_REF = "strategy-risk-query-a-v1"
_FOREIGN_SCOPE_REF = "strategy-risk-orders-foreign-v1"
_FOREIGN_ACCOUNT = "account-b"
_REQUEST_TIMEOUT_MS = 2_000
_MAX_MESSAGE_BYTES = 65_536
_EXCHANGE = "FIXTURE___SPOT"
_PROVIDER_ACCOUNT = "provider-account-a"
_INSTRUMENT = "BTC-USDT"
_METADATA_VERSION = "fixture-metadata-v1"
_METADATA_AS_OF_NS = 1_700_000_000_000_000_000
_METADATA_EXPIRES_AT_NS = 4_000_000_000_000_000_000

_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
_FOREIGN_SCOPE = WireScope(_DOMAIN, account_ref=_FOREIGN_ACCOUNT, strategy_ref=_STRATEGY)
_ACCOUNT_SCOPE = AccountScope("fixture", _PROVIDER_ACCOUNT, "simulation")


class _SyntheticBtApiFeed:
    """Provider-neutral test feed called by BtApi's existing DirectBackend."""

    def __init__(self, ledger_path: str) -> None:
        self._ledger_path = ledger_path
        self.order_queries: list[str] = []
        self.fill_queries: list[str] = []
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_writes ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_orders ("
                "client_order_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)"
            )
            connection.execute("CREATE TABLE IF NOT EXISTS provider_faults (name TEXT PRIMARY KEY)")

    @property
    def writes(self) -> list[str]:
        with sqlite3.connect(self._ledger_path) as connection:
            return [
                str(row[0])
                for row in connection.execute(
                    "SELECT client_order_id FROM provider_writes ORDER BY sequence"
                )
            ]

    def make_order(
        self,
        symbol: str,
        volume: float,
        _price: float,
        order_type: str,
        *,
        client_order_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        row = {
            "account_id": _PROVIDER_ACCOUNT,
            "client_order_id": client_order_id,
            "filled": 0,
            "order_id": "provider-" + client_order_id,
            "quantity": volume,
            "side": order_type.split("-", 1)[0],
            "status": "open",
            "symbol": symbol,
        }
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute(
                "INSERT INTO provider_writes(client_order_id) VALUES (?)",
                (client_order_id,),
            )
            connection.execute(
                "INSERT INTO provider_orders(client_order_id, record_json) VALUES (?, ?) "
                "ON CONFLICT(client_order_id) DO UPDATE SET record_json=excluded.record_json",
                (client_order_id, json.dumps(row, sort_keys=True, separators=(",", ":"))),
            )
        return {
            "account_id": _PROVIDER_ACCOUNT,
            "client_order_id": client_order_id,
            "complete": True,
            "schema_version": "fixture.order.v1",
            "watermark": len(self.writes),
            "data": [dict(row)],
        }

    def query_order(
        self,
        _symbol: str,
        order_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.order_queries.append(order_id)
        with sqlite3.connect(self._ledger_path) as connection:
            result = connection.execute(
                "SELECT record_json FROM provider_orders WHERE client_order_id=?",
                (order_id,),
            ).fetchone()
            fault = connection.execute(
                "SELECT name FROM provider_faults WHERE name='duplicate_next_query'"
            ).fetchone()
            if fault is not None:
                connection.execute("DELETE FROM provider_faults WHERE name='duplicate_next_query'")
        row = None if result is None else json.loads(result[0])
        rows = [dict(row)] if row is not None else []
        if fault is not None and row is not None:
            duplicate = dict(row)
            duplicate["order_id"] = str(duplicate["order_id"]) + "-duplicate"
            rows.append(duplicate)
        return {
            "account_id": _PROVIDER_ACCOUNT,
            "client_order_id": order_id,
            "complete": True,
            "schema_version": "fixture.order.v1",
            "watermark": len(self.order_queries),
            "data": rows,
        }

    def arm_ambiguous_query(self) -> None:
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO provider_faults(name) VALUES ('duplicate_next_query')"
            )

    def get_deals(
        self,
        _symbol: str | None,
        *,
        client_order_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.fill_queries.append(client_order_id)
        return {
            "account_id": _PROVIDER_ACCOUNT,
            "client_order_id": client_order_id,
            "complete": True,
            "schema_version": "fixture.fills.v1",
            "watermark": len(self.fill_queries),
            "data": [],
        }


class _CrashAfterRiskClaim(BaseException):
    """Model abrupt process loss after Risk claims, before journal CLAIMED."""


class _CrashAfterProviderWrite(BaseException):
    """Model abrupt process loss after BtApi reaches the synthetic provider."""


class _CrashAfterProofReady(BaseException):
    """Model process loss after PROOF_READY, before Risk consumes its proof."""


class _CrashAfterRiskResolution(BaseException):
    """Model process loss after Risk resolves, before journal ACKED_TRACKED."""


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def test_strategy_scope_template_varies_only_by_run_id() -> None:
    concrete = replace(_SCOPE, run_id="runtime-generated-run")
    assert _matches_run_wildcard(_SCOPE, concrete)
    assert not _matches_run_wildcard(_SCOPE, _SCOPE)
    assert not _matches_run_wildcard(replace(_SCOPE, run_id="configured-run"), concrete)
    assert not _matches_run_wildcard(
        _SCOPE, replace(concrete, execution_domain_ref="paper:other-domain")
    )
    assert not _matches_run_wildcard(_SCOPE, replace(concrete, account_ref=_FOREIGN_ACCOUNT))
    assert not _matches_run_wildcard(_SCOPE, replace(concrete, strategy_ref="strategy-other"))
    assert not _matches_run_wildcard(_SCOPE, replace(concrete, portfolio_ref="portfolio-other"))
    group_template = WireScope(
        _DOMAIN,
        account_refs=("account-a", "account-b"),
        strategy_ref=_STRATEGY,
        portfolio_ref="portfolio-group",
        group_id="group-a",
    )
    group_run = replace(group_template, run_id="runtime-group-run")
    assert _matches_run_wildcard(group_template, group_run)
    assert not _matches_run_wildcard(
        group_template, replace(group_run, account_refs=("account-c", "account-d"))
    )
    assert not _matches_run_wildcard(group_template, replace(group_run, group_id="group-b"))
    leg_template = WireScope(
        _DOMAIN,
        account_ref=_ACCOUNT,
        strategy_ref=_STRATEGY,
        portfolio_ref="portfolio-group",
        group_id="group-a",
        leg_id="leg-a",
    )
    leg_run = replace(leg_template, run_id="runtime-leg-run")
    assert _matches_run_wildcard(leg_template, leg_run)
    assert not _matches_run_wildcard(leg_template, replace(leg_run, leg_id="leg-b"))
    manual_scope = WireScope(_DOMAIN, account_ref=_ACCOUNT, manual_book_ref="book-a")
    assert not _matches_run_wildcard(manual_scope, replace(concrete, manual_book_ref=None))


def test_order_entry_accepts_service_registry_readonly_arguments() -> None:
    arguments = MappingProxyType(_order_arguments("proxy-shape-intent"))
    expected_fields = frozenset(arguments)

    assert _has_exact_argument_fields(arguments, expected_fields)
    assert not _has_exact_argument_fields(
        MappingProxyType({**arguments, "unexpected": "field"}), expected_fields
    )
    assert not _has_exact_argument_fields(list(arguments), expected_fields)


def test_recovery_observation_timestamp_is_server_bound_and_strict() -> None:
    order = dict.fromkeys(_RECOVERY_ORDER_OBSERVATION_FIELDS)
    fills = dict.fromkeys(_RECOVERY_FILL_OBSERVATION_FIELDS)
    order["updated_at_ns"] = 1
    fills["updated_at_ns"] = 2
    _validate_recovery_observation_fields(order, fills)

    for invalid_timestamp in (True, False, 0, -1, 1.0, "1", None):
        invalid_order = dict(order)
        invalid_order["updated_at_ns"] = invalid_timestamp
        with pytest.raises(RiskOrderEntryError) as rejected:
            _validate_recovery_observation_fields(invalid_order, fills)
        assert rejected.value.code == "RECOVERY_PROVIDER_OBSERVATION_INVALID"

        invalid_fills = dict(fills)
        invalid_fills["updated_at_ns"] = invalid_timestamp
        with pytest.raises(RiskOrderEntryError) as rejected:
            _validate_recovery_observation_fields(order, invalid_fills)
        assert rejected.value.code == "RECOVERY_PROVIDER_OBSERVATION_INVALID"

    for incomplete_order, incomplete_fills in (
        ({key: value for key, value in order.items() if key != "updated_at_ns"}, fills),
        (order, {key: value for key, value in fills.items() if key != "updated_at_ns"}),
    ):
        with pytest.raises(RiskOrderEntryError) as rejected:
            _validate_recovery_observation_fields(incomplete_order, incomplete_fills)
        assert rejected.value.code == "RECOVERY_PROVIDER_OBSERVATION_INVALID"

    with_extra = {**order, "caller_supplied_timestamp": 3}
    with pytest.raises(RiskOrderEntryError) as rejected:
        _validate_recovery_observation_fields(with_extra, fills)
    assert rejected.value.code == "RECOVERY_PROVIDER_OBSERVATION_INVALID"

    provider_payload = {"record": {"status": "open", "filled": 0}}
    provider_payload["provider_observation_sha256"] = sha256_text(canonical_json(provider_payload))
    journal_payload = {**provider_payload, "updated_at_ns": 3}
    _verify_observation_digest(journal_payload)
    tampered_provider_payload = {
        **journal_payload,
        "record": {"status": "open", "filled": 1},
    }
    with pytest.raises(RiskOrderEntryError) as rejected:
        _verify_observation_digest(tampered_provider_payload)
    assert rejected.value.code == "RECOVERY_PROVIDER_OBSERVATION_INVALID"


def test_recursive_wire_freeze_matches_service_client_submit_contract() -> None:
    scope = replace(_SCOPE, run_id="wire-generated-run")
    client_arguments = _submit_arguments(
        intent_id="wire-path-intent",
        signal_id="wire-path-signal",
        instrument=_INSTRUMENT,
        side="BUY",
        position_effect="OPEN",
        order_type="LIMIT",
        quantity="1",
        price="20",
        time_in_force="GTC",
        reduce_only=False,
        metadata_version=_METADATA_VERSION,
        deadline_ns=None,
        tags={},
    )
    header = WireHeader(
        protocol_version=2,
        kind=WireMessageKind.REQUEST,
        message_id="risk-order-submit-message",
        correlation_id=None,
        identity=WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY),
        scope=scope,
        operation="orders.submit",
        schema_id="sdk.service.call",
        schema_version=1,
        sent_at_utc="2026-10-15T00:00:00Z",
    )

    def registry_arguments(arguments: Mapping[str, Any]) -> MappingProxyType:
        request = WireEnvelope(header, {"arguments": dict(arguments)})
        decoded = decode_envelope(encode_envelope(request))
        return MappingProxyType(dict(decoded.payload["arguments"]))

    frozen_arguments = registry_arguments(client_arguments)
    assert type(frozen_arguments["tags"]) is type(MappingProxyType({}))
    assert frozen_arguments["tags"] == {}
    assert _has_exact_argument_fields(frozen_arguments, _ORDER_FIELDS)

    now_ns = time.time_ns()
    metadata = InstrumentRiskMetadata(
        instrument=_INSTRUMENT,
        metadata_version=_METADATA_VERSION,
        as_of_ns=now_ns - 1_000_000_000,
        expires_at_ns=now_ns + 86_400_000_000_000,
        tick_size=Decimal("1"),
        quantity_step=Decimal("1"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("1000"),
        taker_fee_bps=Decimal("0"),
        fixed_fee=Decimal("0"),
        max_slippage_bps=Decimal("0"),
        quantity_unit="BTC",
        valuation_unit="USD",
    )
    target = RiskOrderTarget(
        account_scope=_ACCOUNT_SCOPE,
        strategy_id=_STRATEGY,
        exchange_name=_EXCHANGE,
        provider_account_id=_PROVIDER_ACCOUNT,
        registry=InstrumentRiskRegistry((metadata,)),
    )
    parsed = _parse_order(
        frozen_arguments,
        target,
        scope=scope,
        principal_ref=_PRINCIPAL,
    )
    assert parsed["side"] == "BUY"
    assert parsed["quantity"] == Decimal("1")
    assert parsed["price"] == Decimal("20")

    nonempty_client_arguments = _submit_arguments(
        intent_id="wire-tagged-intent",
        signal_id="wire-tagged-signal",
        instrument=_INSTRUMENT,
        side="BUY",
        position_effect="OPEN",
        order_type="LIMIT",
        quantity="1",
        price="20",
        time_in_force="GTC",
        reduce_only=False,
        metadata_version=_METADATA_VERSION,
        deadline_ns=None,
        tags={"client-label": "still-unsupported"},
    )
    with pytest.raises(RiskOrderEntryError) as denied:
        _parse_order(
            registry_arguments(nonempty_client_arguments),
            target,
            scope=scope,
            principal_ref=_PRINCIPAL,
        )
    assert denied.value.code == "CLIENT_TAGS_UNSUPPORTED"


def test_journal_appends_a_mapping_payload_after_prepared_command(tmp_path) -> None:
    journal = RiskOrderJournal(
        str(tmp_path / "journal-mapping-contract.sqlite3"),
        writer_id="risk-order-mapping-contract",
    )
    intent_id = "journal-mapping-intent"
    canonical_payload = {"intent_id": intent_id}
    order_request = {"symbol": _INSTRUMENT, "client_order_id": "risk-order-mapping"}
    payload_sha256 = sha256_text(canonical_json(canonical_payload))
    order_request_sha256 = sha256_text(canonical_json(order_request))

    with journal.writer_fence(_ACCOUNT_SCOPE) as fence:
        command, created = journal.prepare_or_get(
            fence,
            principal_ref=_PRINCIPAL,
            intent_id=intent_id,
            risk_intent_id="risk-order-mapping-intent",
            payload_sha256=payload_sha256,
            canonical_payload=canonical_payload,
            wire_scope={"run_id": "journal-mapping-run", "strategy_ref": _STRATEGY},
            exchange_name=_EXCHANGE,
            provider_account_id=_PROVIDER_ACCOUNT,
            client_order_id="risk-order-mapping",
            order_request=order_request,
            order_request_sha256=order_request_sha256,
        )
        assert created
        assert command.state == "PREPARED"

        event = journal.append_event(
            fence,
            principal_ref=_PRINCIPAL,
            intent_id=intent_id,
            event_type="RESERVED",
            payload=MappingProxyType(
                {
                    "permit_id": "synthetic-permit-mapping",
                    "risk_intent_id": "risk-order-mapping-intent",
                }
            ),
        )

        current = journal.get_command(_ACCOUNT_SCOPE, _PRINCIPAL, intent_id)
        assert event.event_type == "RESERVED"
        assert event.revision == 2
        assert current is not None
        assert current.state == "RESERVED"
        assert current.permit_id == "synthetic-permit-mapping"


def test_false_dispatch_receipt_projects_ack_as_unknown_and_keeps_history(tmp_path) -> None:
    """A stale ACK cannot stay queryable when Risk's exact receipt is unresolved."""

    journal = RiskOrderJournal(
        str(tmp_path / "journal-false-receipt.sqlite3"),
        writer_id="risk-order-false-receipt",
    )
    gate = DurableRiskGate(
        str(tmp_path / "gate-false-receipt.sqlite3"),
        RiskPolicy(
            "false-receipt-policy",
            Decimal("1000"),
            max_increase_count=5,
            notional_unit="USD",
        ),
        execution_journal_authority=journal,
    )
    intent_id = "risk-order:false-receipt"
    permit_id = None
    try:
        intent = RiskIntent(
            intent_id=intent_id,
            scope=_ACCOUNT_SCOPE,
            action=IntentAction.INCREASE,
            notional=Decimal("20"),
            payload_fingerprint="f" * 64,
            notional_unit="USD",
        )
        journal_intent_id = "false-receipt-intent"
        client_order_id = "risk-order-false-receipt"
        request = {
            "account_id": _PROVIDER_ACCOUNT,
            "client_order_id": client_order_id,
            "price": "20",
            "quantity": "1",
            "symbol": _INSTRUMENT,
        }
        canonical_payload = {"intent_id": journal_intent_id}
        with journal.writer_fence(_ACCOUNT_SCOPE) as fence:
            command, created = journal.prepare_or_get(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=journal_intent_id,
                risk_intent_id=intent_id,
                payload_sha256=sha256_text(canonical_json(canonical_payload)),
                canonical_payload=canonical_payload,
                wire_scope={
                    "account_ref": _ACCOUNT,
                    "account_refs": [],
                    "execution_domain_ref": _DOMAIN,
                    "group_id": None,
                    "leg_id": None,
                    "manual_book_ref": None,
                    "portfolio_ref": None,
                    "run_id": "false-receipt-run",
                    "strategy_ref": _STRATEGY,
                },
                exchange_name=_EXCHANGE,
                provider_account_id=_PROVIDER_ACCOUNT,
                client_order_id=client_order_id,
                order_request=request,
                order_request_sha256=sha256_text(canonical_json(request)),
            )
            assert created and command.state == "PREPARED"
            permit = gate.reserve(intent)
            permit_id = permit.permit_id
            gate.claim_for_dispatch(permit_id, intent)
            claim = gate.dispatch_claim_binding(permit_id)
            gate.settle(permit_id)
            receipt = gate.dispatch_resolution_receipt(permit_id)
            assert receipt.status is DispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE
            assert receipt.dispatch_latch_active is True

            journal.append_event(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=journal_intent_id,
                event_type="RESERVED",
                payload={"permit_id": permit_id, "risk_intent_id": intent_id},
            )
            journal.append_event(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=journal_intent_id,
                event_type="CLAIMED",
                payload={
                    "claim": {
                        "cause_id": claim.cause_id,
                        "claim_digest": claim.claim_digest,
                        "claimed_at": claim.claimed_at,
                        "intent_hash": claim.intent_hash,
                        "intent_id": claim.intent_id,
                        "permit_id": claim.permit_id,
                        "scope_key": claim.scope.key,
                    },
                    "permit_id": permit_id,
                    "risk_intent_id": intent_id,
                },
            )
            journal.append_event(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=journal_intent_id,
                event_type="DISPATCHING",
                payload={
                    "dispatch_attempt_count": 1,
                    "permit_id": permit_id,
                    "risk_intent_id": intent_id,
                },
            )
            for event_type in ("ORDER_OBSERVATION", "FILL_OBSERVATION", "PROOF_READY"):
                journal.append_event(
                    fence,
                    principal_ref=_PRINCIPAL,
                    intent_id=journal_intent_id,
                    event_type=event_type,
                    payload={"permit_id": permit_id, "risk_intent_id": intent_id},
                )
            ack = journal.append_event(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=journal_intent_id,
                event_type="ACKED_TRACKED",
                payload={
                    "dispatch_attempt_count": 1,
                    "filled_quantity": "0",
                    "permit_id": permit_id,
                    "provider_order_id": "provider-false-receipt",
                    "public_state": "ACKED_TRACKED",
                },
            )
            assert ack.event_type == "ACKED_TRACKED"

        service = object.__new__(RiskOrderEntryService)
        service._journal = journal
        service._gate = gate
        target = RiskOrderTarget(
            account_scope=_ACCOUNT_SCOPE,
            strategy_id=_STRATEGY,
            exchange_name=_EXCHANGE,
            provider_account_id=_PROVIDER_ACCOUNT,
            registry=InstrumentRiskRegistry(
                (
                    InstrumentRiskMetadata(
                        instrument=_INSTRUMENT,
                        metadata_version=_METADATA_VERSION,
                        as_of_ns=time.time_ns() - 1_000_000,
                        expires_at_ns=time.time_ns() + 60_000_000_000,
                        tick_size=Decimal("1"),
                        quantity_step=Decimal("1"),
                        contract_multiplier=Decimal("1"),
                        max_gross_notional=Decimal("1000"),
                        quantity_unit="BTC",
                        valuation_unit="USD",
                    ),
                )
            ),
        )

        with journal.writer_fence(_ACCOUNT_SCOPE) as fence:
            command = journal.get_command(_ACCOUNT_SCOPE, _PRINCIPAL, journal_intent_id)
            assert command is not None and command.state == "ACKED_TRACKED"
            unchanged = service._mark_unknown(fence, command, "UNRELATED_FAILURE")
            assert unchanged.state == "ACKED_TRACKED"

        result = service._query(
            principal_ref=_PRINCIPAL,
            scope=_SCOPE,
            target=target,
            contract=None,
            arguments={"intent_id": journal_intent_id},
        )
        record = result["result"]["record"]
        assert record["state"] == "UNKNOWN"
        assert record["unknown_reason"] == "DISPATCH_PROOF_RECOVERY_UNCERTAIN"
        assert record["review_required"] is True
        current = journal.get_command(_ACCOUNT_SCOPE, _PRINCIPAL, journal_intent_id)
        assert current is not None and current.state == "UNKNOWN"
        assert current.latest_event["updated_at_ns"] > 0
        with sqlite3.connect(journal.path) as connection:
            event_types = connection.execute(
                "SELECT event_type FROM risk_order_events WHERE scope_key=? "
                "AND principal_ref=? AND intent_id=? ORDER BY revision",
                (_ACCOUNT_SCOPE.key, _PRINCIPAL, journal_intent_id),
            ).fetchall()
        assert [row[0] for row in event_types].count("ACKED_TRACKED") == 1
        assert [row[0] for row in event_types][-1] == "UNKNOWN"
        resolved = gate.dispatch_resolution_receipt(permit_id)
        assert resolved.status is DispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE
        assert resolved.dispatch_latch_active is True
    finally:
        gate.close()


def test_journal_records_normalized_observations_through_proof_ready(tmp_path) -> None:
    journal = RiskOrderJournal(
        str(tmp_path / "journal-normalized-observation-contract.sqlite3"),
        writer_id="risk-order-normalized-observation-contract",
    )
    intent_id = "journal-normalized-observation-intent"
    risk_intent_id = "risk-order:journal-normalized-observation"
    permit_id = "synthetic-permit-normalized-observation"
    client_order_id = "risk-journal-normalized-observation"
    request = {
        "account_id": _PROVIDER_ACCOUNT,
        "client_order_id": client_order_id,
        "symbol": _INSTRUMENT,
        "quantity": "1",
        "price": "20",
        "side": "buy",
    }
    canonical_payload = {"intent_id": intent_id, "instrument": _INSTRUMENT}
    payload_sha256 = sha256_text(canonical_json(canonical_payload))
    request_sha256 = sha256_text(
        canonical_json(
            {
                "exchange_name": _EXCHANGE,
                "operation": "BtApi.make_order(normalized=True)",
                "request": request,
            }
        )
    )
    contract_sha256 = "a" * 64
    source_sha256 = "b" * 64
    empty_provenance: dict[str, Any] = {
        "source_fields": {},
        "source_values": {},
        "envelope_fields": {},
        "envelope_values": {},
        "source_sha256": source_sha256,
        "schema": "bt-api-order-source-provenance-v1",
    }
    order_observation = {
        "account_id": _PROVIDER_ACCOUNT,
        "client_order_id": client_order_id,
        "collection_provenance": {},
        "complete": True,
        "filled_quantity": 0,
        "instrument": _INSTRUMENT,
        "provider_contract_sha256": contract_sha256,
        "provider_order_id": "provider-journal-normalized-observation",
        "quantity": "1",
        "record": {"status": "open", "filled": 0},
        "source_alignment_valid": True,
        "source_row_count": 1,
        "schema": "fixture.order.v1",
        "side": "BUY",
        "status": "open",
        "watermark": 1,
        "normalization_provenance": empty_provenance,
        "operation": "query_order",
        "source_fields": {},
        "source_values": {},
        "envelope_fields": {},
        "envelope_values": {},
        "source_sha256": source_sha256,
        "normalizer_schema": "bt-api-order-source-provenance-v1",
        "acknowledgement": None,
    }
    fill_observation = {
        "account_id": _PROVIDER_ACCOUNT,
        "client_order_id": client_order_id,
        "complete": True,
        "normalization_provenance": empty_provenance,
        "operation": "get_deals",
        "record_count": 0,
        "records": [],
        "provider_contract_sha256": contract_sha256,
        "schema": "fixture.fills.v1",
        "source_alignment_valid": True,
        "source_row_count": 0,
        "watermark": 1,
        "source_fields": {},
        "source_values": {},
        "envelope_fields": {},
        "envelope_values": {},
        "source_sha256": source_sha256,
        "normalizer_schema": "bt-api-order-source-provenance-v1",
        "filled_quantity": 0,
        "trade_count": 0,
    }
    order_observation["provider_observation_sha256"] = sha256_text(
        canonical_json(order_observation)
    )
    fill_observation["provider_observation_sha256"] = sha256_text(canonical_json(fill_observation))
    claim = DispatchClaimBinding(
        scope=_ACCOUNT_SCOPE,
        permit_id=permit_id,
        intent_id=risk_intent_id,
        intent_hash="c" * 64,
        cause_id="dispatch-inflight:" + risk_intent_id,
        claimed_at=time.time(),
        claim_digest="d" * 64,
    )

    with journal.writer_fence(_ACCOUNT_SCOPE) as fence:
        command, created = journal.prepare_or_get(
            fence,
            principal_ref=_PRINCIPAL,
            intent_id=intent_id,
            risk_intent_id=risk_intent_id,
            payload_sha256=payload_sha256,
            canonical_payload=canonical_payload,
            wire_scope={"run_id": "journal-observation-run", "strategy_ref": _STRATEGY},
            exchange_name=_EXCHANGE,
            provider_account_id=_PROVIDER_ACCOUNT,
            client_order_id=client_order_id,
            order_request=request,
            order_request_sha256=request_sha256,
        )
        assert created and command.state == "PREPARED"
        journal.append_event(
            fence,
            principal_ref=_PRINCIPAL,
            intent_id=intent_id,
            event_type="RESERVED",
            payload={"permit_id": permit_id, "risk_intent_id": risk_intent_id},
        )
        journal.append_event(
            fence,
            principal_ref=_PRINCIPAL,
            intent_id=intent_id,
            event_type="CLAIMED",
            payload={
                "claim": {
                    "cause_id": claim.cause_id,
                    "claim_digest": claim.claim_digest,
                    "claimed_at": claim.claimed_at,
                    "intent_hash": claim.intent_hash,
                    "intent_id": claim.intent_id,
                    "permit_id": claim.permit_id,
                    "scope_key": claim.scope.key,
                },
                "permit_id": permit_id,
                "risk_intent_id": risk_intent_id,
            },
        )
        journal.append_event(
            fence,
            principal_ref=_PRINCIPAL,
            intent_id=intent_id,
            event_type="DISPATCHING",
            payload={
                "dispatch_attempt_count": 1,
                "permit_id": permit_id,
                "risk_intent_id": risk_intent_id,
            },
        )

        malformed_observation = {**order_observation, "client_supplied_extra": True}
        with pytest.raises(RiskOrderJournalError) as rejected:
            journal.append_resolution_observations(
                fence,
                principal_ref=_PRINCIPAL,
                intent_id=intent_id,
                order_observation=malformed_observation,
                fill_observation=fill_observation,
            )
        assert rejected.value.code == "OBSERVATION_FIELDS_INVALID"
        current = journal.get_command(_ACCOUNT_SCOPE, _PRINCIPAL, intent_id)
        assert current is not None and current.state == "DISPATCHING"

        order_event, fill_event, reconciliation_sha256 = journal.append_resolution_observations(
            fence,
            principal_ref=_PRINCIPAL,
            intent_id=intent_id,
            order_observation=order_observation,
            fill_observation=fill_observation,
        )
        assert (order_event.event_type, fill_event.event_type) == (
            "ORDER_OBSERVATION",
            "FILL_OBSERVATION",
        )
        assert order_event.revision + 1 == fill_event.revision
        assert type(order_event.payload["updated_at_ns"]) is int
        assert order_event.payload["updated_at_ns"] > 0
        assert type(fill_event.payload["updated_at_ns"]) is int
        assert fill_event.payload["updated_at_ns"] > 0
        observed = journal.get_command(_ACCOUNT_SCOPE, _PRINCIPAL, intent_id)
        assert observed is not None and observed.state == "FILL_OBSERVATION"

        exposure_id, exposure_sha256, _reference = journal.store_exposure_reference(
            fence,
            principal_ref=_PRINCIPAL,
            intent_id=intent_id,
            claim=claim,
            evidence_class=DispatchEvidenceClass.SIMULATION_JOURNAL,
            permit_id=permit_id,
            provider_order_id="provider-journal-normalized-observation",
            accepted_request_sha256=request_sha256,
            reconciliation_evidence_sha256=reconciliation_sha256,
            observation_revision=fill_event.revision,
            observation_record_sha256=fill_event.record_sha256,
        )
        ready = journal.get_command(_ACCOUNT_SCOPE, _PRINCIPAL, intent_id)
        assert ready is not None and ready.state == "PROOF_READY"
        assert ready.latest_revision > fill_event.revision
        assert ready.latest_event["exposure_reference_id"] == exposure_id
        assert ready.latest_event["exposure_reference_sha256"] == exposure_sha256

    with journal.writer_fence(_ACCOUNT_SCOPE) as recovery_fence:
        source = journal.load_dispatch_proof_for_recovery(
            recovery_fence,
            principal_ref=_PRINCIPAL,
            intent_id=intent_id,
            claim=claim,
        )
        assert source is not None
        assert source.proof.journal_revision == ready.latest_revision
        assert source.proof.writer_fence_sha256 != recovery_fence.fence_sha256
        attestation = journal.append_proof_recovery_attestation(recovery_fence, source)
        assert attestation.proof_sha256 == source.proof.fingerprint
        assert attestation.original_writer_fencing_token < recovery_fence.fencing_token
        assert attestation.recovery_event_id == sha256_text(
            canonical_json(
                [
                    _ACCOUNT_SCOPE.key,
                    _PRINCIPAL,
                    intent_id,
                    attestation.recovery_event_revision,
                ]
            )
        )
        recovered_source = journal.load_dispatch_proof_for_recovery(
            recovery_fence,
            principal_ref=_PRINCIPAL,
            intent_id=intent_id,
            claim=claim,
        )
        assert recovered_source is not None
        assert recovered_source.recovery_attestations == (attestation,)
        with journal.recovered_dispatch_resolution_guard(
            source.proof, attestation, claim=claim
        ) as verified:
            assert verified is not None
            assert verified.proof_sha256 == source.proof.fingerprint
            assert verified.attestation_sha256 == attestation.fingerprint
            assert verified.recovery_event_sha256 == attestation.recovery_event_sha256


def _service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": "risk-order-listener-v1",
        "registration_policy_ref": "risk-order-registration-v1",
        "service_requirements_ref": "risk-order-requirements-v1",
        "components": {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": ["service.order_entry", "service.order_query"],
                    "options": {
                        "role": "account_owner",
                        "account_policy_ref": "service-risk-policy-v1",
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "risk-order-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SERVER_SECURITY_REF,
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _client_config(endpoint: str, security_ref: str, *, foreign: bool = False):
    bindings = [
        {
            "mount": "broker.orders",
            "service_id": _SERVICE_ID,
            "instance_id": _SERVICE_INSTANCE,
            "scope_ref": _ORDER_SCOPE_REF,
            "transport_ref": "risk-order-connect-v1",
            "account_ref": _ACCOUNT,
        },
        {
            "mount": "broker.order_query",
            "service_id": _SERVICE_ID,
            "instance_id": _SERVICE_INSTANCE,
            "scope_ref": _QUERY_SCOPE_REF,
            "transport_ref": "risk-order-connect-v1",
            "account_ref": _ACCOUNT,
        },
    ]
    if foreign:
        bindings = [
            {
                "mount": "broker.orders",
                "service_id": _SERVICE_ID,
                "instance_id": _SERVICE_INSTANCE,
                "scope_ref": _FOREIGN_SCOPE_REF,
                "transport_ref": "risk-order-connect-v1",
                "account_ref": _FOREIGN_ACCOUNT,
            }
        ]
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": _STRATEGY},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": bindings,
                        "options": {"request_timeout_ms": _REQUEST_TIMEOUT_MS},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "risk-order-connect-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": security_ref,
                        "options": {
                            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                            "high_water_mark": 32,
                            "max_message_bytes": _MAX_MESSAGE_BYTES,
                        },
                    }
                },
            },
        }
    )


def _order_arguments(
    intent_id: str,
    *,
    quantity: str = "1",
    price: str = "20",
    metadata_version: str = _METADATA_VERSION,
) -> dict[str, Any]:
    return {
        "intent_id": intent_id,
        "signal_id": "signal-" + intent_id,
        "instrument": _INSTRUMENT,
        "side": "BUY",
        "position_effect": "OPEN",
        "order_type": "LIMIT",
        "quantity": quantity,
        "price": price,
        "time_in_force": "GTC",
        "reduce_only": False,
        "metadata_version": metadata_version,
        "deadline_ns": None,
        "tags": {},
    }


def _authority(config, keys):
    identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    grants = {
        _PRINCIPAL: (
            PrincipalGrant(
                WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY),
                (
                    ResourceGrant(
                        _SERVICE_INSTANCE,
                        "service.order_entry",
                        _ORDER_SCOPE_REF,
                        _SCOPE,
                        frozenset({"orders.submit"}),
                    ),
                    ResourceGrant(
                        _SERVICE_INSTANCE,
                        "service.order_query",
                        _QUERY_SCOPE_REF,
                        _SCOPE,
                        frozenset({"orders.query"}),
                    ),
                ),
            ),
        )
    }
    return TrustedServiceAuthority(
        identity=identity,
        scope=WireScope(_DOMAIN),
        scope_ref=config.scope_ref,
        scope_version="risk-order-scope-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="risk-order-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="risk-order-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals={keys["strategy_public"]: _PRINCIPAL},
        grants=grants,
    )


def _host_process(
    control,
    raw_config,
    registry_path,
    keys,
    gate_path,
    journal_path,
    provider_path,
    crash_mode: str | None = None,
):
    config = parse_service_config(raw_config)
    provider = _SyntheticBtApiFeed(provider_path)
    journal = None
    gate = None
    bt_api = None
    host = None
    try:
        metadata = InstrumentRiskMetadata(
            instrument=_INSTRUMENT,
            metadata_version=_METADATA_VERSION,
            # Keep this trusted source digest identical across host restarts.
            as_of_ns=_METADATA_AS_OF_NS,
            expires_at_ns=_METADATA_EXPIRES_AT_NS,
            tick_size=Decimal("1"),
            quantity_step=Decimal("1"),
            contract_multiplier=Decimal("1"),
            max_gross_notional=Decimal("1000"),
            taker_fee_bps=Decimal("0"),
            fixed_fee=Decimal("0"),
            max_slippage_bps=Decimal("0"),
            quantity_unit="BTC",
            valuation_unit="USD",
        )
        registry = InstrumentRiskRegistry((metadata,))
        target = RiskOrderTarget(
            account_scope=_ACCOUNT_SCOPE,
            strategy_id=_STRATEGY,
            exchange_name=_EXCHANGE,
            provider_account_id=_PROVIDER_ACCOUNT,
            registry=registry,
        )
        bt_api = BtApi(debug=False)
        bt_api.exchange_feeds[_EXCHANGE] = provider
        journal = RiskOrderJournal(journal_path, writer_id="risk-order-service-owner")
        if crash_mode == "after_risk_claim":
            append_event = journal.append_event

            def crash_on_claim(fence, **kwargs):
                if kwargs.get("event_type") == "CLAIMED":
                    raise _CrashAfterRiskClaim()
                return append_event(fence, **kwargs)

            journal.append_event = crash_on_claim
        elif crash_mode == "after_provider_write":

            def crash_after_provider_write(*_args, **_kwargs):
                raise _CrashAfterProviderWrite()

            journal.append_resolution_observations = crash_after_provider_write
        gate = DurableRiskGate(
            gate_path,
            RiskPolicy(
                "service-risk-order-test-policy",
                Decimal("45"),
                max_increase_count=10,
                notional_unit="USD",
            ),
            execution_journal_authority=journal,
        )
        mapper = InstrumentRiskAdmissionMapper(
            _ACCOUNT_SCOPE,
            registry,
            clock_ns=time.time_ns,
        )
        adapter = DurableRiskAdmissionAdapter(gate, mapper)
        if crash_mode == "after_proof_ready":

            def crash_before_risk_resolution(_proof):
                raise _CrashAfterProofReady()

            gate.resolve_dispatch_freeze = crash_before_risk_resolution
        elif crash_mode == "after_risk_resolution":
            resolve_dispatch_freeze = gate.resolve_dispatch_freeze

            def crash_after_risk_resolution(proof):
                resolve_dispatch_freeze(proof)
                raise _CrashAfterRiskResolution()

            gate.resolve_dispatch_freeze = crash_after_risk_resolution
        elif crash_mode == "ack_journal_error_after_risk_resolution":
            append_event = journal.append_event
            ack_failure_seen = {"value": False}

            def fail_ack_once(fence, **kwargs):
                if kwargs.get("event_type") == "ACKED_TRACKED" and not ack_failure_seen["value"]:
                    ack_failure_seen["value"] = True
                    raise RuntimeError("injected_ack_persist_failure")
                return append_event(fence, **kwargs)

            journal.append_event = fail_ack_once
        contract = RiskBtApiProviderContract(
            exchange_name=_EXCHANGE,
            profile_id="fixture-provider-contract-v1",
            order_schema="fixture.order.v1",
            fill_schema="fixture.fills.v1",
            client_order_id_query=True,
            complete_order_snapshot=True,
            complete_fill_history=True,
            provider_accepts_client_order_id=True,
            client_order_id_query_unique=True,
            provider_status_mapping=(
                ("open", "accepted"),
                ("filled", "completed"),
                ("cancelled", "canceled"),
                ("expired", "expired"),
                ("rejected", "rejected"),
            ),
            supported_order_types=("LIMIT",),
            supported_time_in_force=("GTC",),
            supported_position_effects=("OPEN",),
        )

        def resource_provider(component, _instance_id, _options, _scope):
            if component != "risk":
                raise AssertionError("unexpected component")
            return ComponentResources.borrowed(
                {
                    "bt_api": bt_api,
                    "targets": {_SCOPE: target},
                    "admission": {_SCOPE: adapter},
                    "journal": journal,
                    "gate": gate,
                    "provider_contracts": {_SCOPE: contract},
                },
                ready=lambda: True,
            )

        authority = _authority(config=config, keys=keys)
        host = ServiceHost(config, authority, registry_path, resource_provider)
        host.start()
        control.send(("ready", host.endpoint, host.state))
        while True:
            host.serve_once(timeout_ms=25)
            if not control.poll():
                continue
            command = control.recv()
            if command == ("close",):
                host.close()
                gate.close()
                bt_api.close()
                control.send(("closed", host.state))
                return
            if command == ("arm_ambiguous_query",):
                provider.arm_ambiguous_query()
                control.send(("ambiguous_query_armed",))
                continue
            if command == ("snapshot",):
                control.send(
                    (
                        "snapshot",
                        tuple(provider.writes),
                        tuple(provider.order_queries),
                        tuple(provider.fill_queries),
                        gate.snapshot(_ACCOUNT_SCOPE),
                    )
                )
                continue
            if command[0] == "command":
                item = journal.get_command(_ACCOUNT_SCOPE, _PRINCIPAL, command[1])
                control.send(
                    (
                        "command",
                        None if item is None else item.state,
                        None if item is None else item.permit_id,
                        None if item is None else item.client_order_id,
                        None if item is None else dict(item.wire_scope),
                        None if item is None else item.risk_intent_id,
                    )
                )
                continue
            raise AssertionError("unknown risk-order host control")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed", "close_failed"}:
            with suppress(BaseException):
                host.close()
        if gate is not None:
            with suppress(BaseException):
                gate.close()
        if bt_api is not None:
            with suppress(BaseException):
                bt_api.close()
        with suppress(Exception):
            if isinstance(exc, _CrashAfterRiskClaim):
                code = "RISK_CLAIMED_PROCESS_LOSS"
            elif isinstance(exc, _CrashAfterProviderWrite):
                code = "RISK_PROVIDER_WRITE_PROCESS_LOSS"
            elif isinstance(exc, _CrashAfterProofReady):
                code = "RISK_PROOF_READY_PROCESS_LOSS"
            elif isinstance(exc, _CrashAfterRiskResolution):
                code = "RISK_RESOLUTION_PROCESS_LOSS"
            else:
                code = getattr(exc, "code", type(exc).__name__)
            control.send(("error", code))
        raise
    finally:
        control.close()


def _strategy_process(control, endpoint, strategy_public, strategy_secret, server_public):
    runtime = None
    try:
        config = _client_config(endpoint, "risk-order-strategy-curve-v1")
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY)
        peer = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda _ref: _SCOPE,
            peer_resolver=lambda _service: peer,
            transport_factory=ZmqTransportFactory(
                {
                    "risk-order-strategy-curve-v1": ClientCurveKeys(
                        server_public, strategy_public, strategy_secret
                    )
                }
            ),
        )
        startup = runtime.start()
        assert startup.degraded_count == 0
        operations = {binding.mount: binding.operations for binding in runtime.bindings}
        client = ServiceExecutionClient(
            runtime,
            "broker.orders",
            scope_ref=_ORDER_SCOPE_REF,
            query_mount="broker.order_query",
            query_scope_ref=_QUERY_SCOPE_REF,
        )
        control.send(("ready", operations, runtime.run_id))
        while True:
            command = control.recv()
            if command == ("close",):
                runtime.close()
                control.send(("closed",))
                return
            if command[0] == "submit":
                try:
                    view = client.submit(
                        **_order_arguments(
                            command[1],
                            quantity=command[2],
                            price=command[3],
                            metadata_version=command[4],
                        )
                    )
                except ServiceExecutionError as error:
                    record = error.record
                    control.send(
                        (
                            "submit_error",
                            error.code,
                            getattr(record, "state", None),
                            getattr(record, "permit_reference", None),
                        )
                    )
                else:
                    control.send(("submitted", asdict(view)))
                continue
            if command[0] == "query":
                try:
                    view = client.query(command[1])
                except ServiceExecutionError as error:
                    control.send(("query_error", error.code))
                else:
                    control.send(("queried", None if view is None else asdict(view)))
                continue
            if command[0] == "cancel":
                try:
                    client.cancel(
                        cancel_id="cancel-unsupported",
                        target_intent_id=command[1],
                        provider_order_id=command[2],
                    )
                except ServiceExecutionError as error:
                    control.send(("cancel_error", error.code))
                else:
                    control.send(("cancel_accepted",))
                continue
            if command[0] == "foreign_scope":
                foreign = _client_config(endpoint, "risk-order-strategy-curve-v1", foreign=True)
                foreign_runtime = ServiceClientRuntime(
                    foreign,
                    identity=identity,
                    scope_resolver=lambda _ref: _FOREIGN_SCOPE,
                    peer_resolver=lambda _service: peer,
                    transport_factory=ZmqTransportFactory(
                        {
                            "risk-order-strategy-curve-v1": ClientCurveKeys(
                                server_public, strategy_public, strategy_secret
                            )
                        }
                    ),
                )
                try:
                    foreign_startup = foreign_runtime.start()
                    try:
                        foreign_runtime.call(
                            "broker.orders",
                            "orders.submit",
                            _order_arguments("foreign-account-intent"),
                            scope_ref=_FOREIGN_SCOPE_REF,
                        )
                    except ServiceRuntimeError as error:
                        control.send(("foreign_denied", error.code, foreign_startup.degraded_count))
                    else:
                        control.send(("foreign_accepted", foreign_startup.degraded_count))
                except ServiceRuntimeError as error:
                    control.send(("foreign_startup_denied", error.code))
                finally:
                    with suppress(BaseException):
                        foreign_runtime.close()
                continue
            raise AssertionError("unknown strategy control")
    except BaseException as exc:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _plain_record(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: str(value) if isinstance(value, Decimal) else value for key, value in payload.items()
    }


def test_spawned_risk_service_gates_idempotent_orders_and_metadata_scope(tmp_path) -> None:
    from tests.unit._spawned_process_helpers import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _client_command,
        _close_normally,
        _receive,
        _start_process,
    )

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
    }
    service_raw = _service_config(endpoint)
    registry_path = str(tmp_path / "risk-order-registry.sqlite3")
    gate_path = str(tmp_path / "risk-order-gate.sqlite3")
    journal_path = str(tmp_path / "risk-order-journal.sqlite3")
    provider_path = str(tmp_path / "risk-order-provider.sqlite3")
    host_record = None
    strategy_record = None
    records = []
    forced = []
    failures = []
    try:
        host_record = _start_process(
            context,
            _host_process,
            (service_raw, registry_path, keys, gate_path, journal_path, provider_path),
            "risk-order-owner-host",
        )
        records.append(host_record)
        ready = _receive(host_record["pipe"])
        assert ready[0] == "ready", ready
        assert ready[1] == endpoint

        strategy_record = _start_process(
            context,
            _strategy_process,
            (endpoint, strategy_public, strategy_secret, server_public),
            "risk-order-strategy-client",
        )
        records.append(strategy_record)
        client_ready = _receive(strategy_record["pipe"])
        assert client_ready[0] == "ready", client_ready
        operations = client_ready[1]
        run_id = client_ready[2]
        assert type(run_id) is str and run_id
        assert operations["broker.orders"] == frozenset({"orders.submit"})
        assert operations["broker.order_query"] == frozenset({"orders.query"})
        assert _client_command(
            strategy_record, "cancel", "order-a", "provider-order-untrusted"
        ) == ("cancel_error", "AMBIGUOUS_OR_MISSING_ROUTE")

        first = _client_command(strategy_record, "submit", "order-a", "1", "20", _METADATA_VERSION)
        assert first[0] == "submitted", first
        first_record = _plain_record(first[1])
        assert first_record["state"] == "ACKED_TRACKED"
        assert first_record["provider_order_id"].startswith("provider-risk-")
        assert first_record["dispatch_attempts"] == 1
        first_command = _client_command(host_record, "command", "order-a")
        assert first_command[0:3] == (
            "command",
            "ACKED_TRACKED",
            first_record["permit_reference"],
        )
        assert first_command[3].startswith("risk-")
        assert first_command[4]["run_id"] == run_id
        assert first_command[4]["account_ref"] == _ACCOUNT
        assert first_command[4]["strategy_ref"] == _STRATEGY
        assert first_command[5].startswith("risk-order:")
        first_snapshot = _client_command(host_record, "snapshot")
        assert first_snapshot[1] == (first_command[3],)

        duplicate = _client_command(
            strategy_record, "submit", "order-a", "1", "20", _METADATA_VERSION
        )
        assert duplicate[0] == "submitted", duplicate
        assert _plain_record(duplicate[1]) == first_record

        queried = _client_command(strategy_record, "query", "order-a")
        assert queried[0] == "queried", queried
        assert queried[1]["state"] == "ACKED_TRACKED"
        after_first_query = _client_command(host_record, "snapshot")
        assert after_first_query[1] == (first_command[3],)
        assert len(after_first_query[2]) == 2
        assert len(after_first_query[3]) == 2

        _close_normally(strategy_record, expected_tag="closed")
        strategy_record = _start_process(
            context,
            _strategy_process,
            (endpoint, strategy_public, strategy_secret, server_public),
            "risk-order-restarted-strategy-client",
        )
        records.append(strategy_record)
        restarted_client_ready = _receive(strategy_record["pipe"])
        assert restarted_client_ready[0] == "ready", restarted_client_ready
        assert restarted_client_ready[2] != run_id
        cross_run_duplicate = _client_command(
            strategy_record, "submit", "order-a", "1", "20", _METADATA_VERSION
        )
        assert cross_run_duplicate[0] == "submitted", cross_run_duplicate
        cross_run_record = _plain_record(cross_run_duplicate[1])
        assert cross_run_record["state"] == "ACKED_TRACKED"
        assert cross_run_record["provider_order_id"] == first_record["provider_order_id"]
        assert cross_run_record["permit_reference"] == first_record["permit_reference"]
        assert cross_run_record["dispatch_attempts"] == 1
        cross_run_command = _client_command(host_record, "command", "order-a")
        assert cross_run_command[3] == first_command[3]
        assert cross_run_command[4]["run_id"] == run_id
        assert cross_run_command[5] == first_command[5]
        after_cross_run_duplicate = _client_command(host_record, "snapshot")
        assert after_cross_run_duplicate[1] == (first_command[3],)
        assert after_cross_run_duplicate[2] == after_first_query[2]
        assert after_cross_run_duplicate[3] == after_first_query[3]
        queried_after_restart = _client_command(strategy_record, "query", "order-a")
        assert queried_after_restart[0] == "queried", queried_after_restart
        assert queried_after_restart[1]["state"] == "ACKED_TRACKED"
        after_cross_run_retry = _client_command(host_record, "snapshot")
        assert after_cross_run_retry[1] == (first_command[3],)
        assert len(after_cross_run_retry[2]) == 3
        assert len(after_cross_run_retry[3]) == 3
        assert after_cross_run_retry[4]["increase_count"] == 1

        second = _client_command(strategy_record, "submit", "order-b", "1", "20", _METADATA_VERSION)
        assert second[0] == "submitted", second
        assert second[1]["state"] == "ACKED_TRACKED"

        before_denial = _client_command(host_record, "snapshot")
        assert before_denial[0] == "snapshot"
        assert len(before_denial[1]) == 2
        assert before_denial[4]["increase_count"] == 2
        assert before_denial[4]["increase_notional"] == Decimal("40")

        denied = _client_command(
            strategy_record, "submit", "order-over-budget", "1", "10", _METADATA_VERSION
        )
        assert denied == ("submit_error", "ADMISSION_DENIED", "REJECTED", None)
        denied_command = _client_command(host_record, "command", "order-over-budget")
        assert denied_command[1:3] == ("ADMISSION_DENIED", None)

        mismatched_metadata = _client_command(
            strategy_record, "submit", "order-wrong-metadata", "1", "10", "untrusted-v2"
        )
        assert mismatched_metadata == ("submit_error", "METADATA_VERSION_MISMATCH", None, None)

        foreign = _client_command(strategy_record, "foreign_scope")
        assert foreign == ("foreign_startup_denied", "SCOPE_DENIED"), foreign
        after_denial = _client_command(host_record, "snapshot")
        assert after_denial[1] == before_denial[1]
        assert len(after_denial[2]) == 4
        assert after_denial[4]["increase_count"] == 2
        assert after_denial[4]["increase_notional"] == Decimal("40")

        _close_normally(strategy_record, expected_tag="closed")
        _close_normally(host_record, expected_tag="closed")
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)


def test_claimed_but_unjournaled_order_is_unknown_after_service_restart(tmp_path) -> None:
    from tests.unit._spawned_process_helpers import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _client_command,
        _close_normally,
        _receive,
        _start_process,
    )

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
    }
    service_raw = _service_config(endpoint)
    registry_path = str(tmp_path / "risk-order-restart-registry.sqlite3")
    gate_path = str(tmp_path / "risk-order-restart-gate.sqlite3")
    journal_path = str(tmp_path / "risk-order-restart-journal.sqlite3")
    provider_path = str(tmp_path / "risk-order-restart-provider.sqlite3")
    records = []
    forced = []
    failures = []
    try:
        host_record = _start_process(
            context,
            _host_process,
            (
                service_raw,
                registry_path,
                keys,
                gate_path,
                journal_path,
                provider_path,
                "after_risk_claim",
            ),
            "risk-order-crash-host",
        )
        records.append(host_record)
        assert _receive(host_record["pipe"])[0] == "ready"
        strategy_record = _start_process(
            context,
            _strategy_process,
            (endpoint, strategy_public, strategy_secret, server_public),
            "risk-order-crash-client",
        )
        records.append(strategy_record)
        initial_client_ready = _receive(strategy_record["pipe"])
        assert initial_client_ready[0] == "ready", initial_client_ready
        initial_run_id = initial_client_ready[2]

        strategy_record["pipe"].send(("submit", "claim-gap", "1", "20", _METADATA_VERSION))
        crashed = _receive(host_record["pipe"], timeout=10.0)
        assert crashed == ("error", "RISK_CLAIMED_PROCESS_LOSS")
        host_record["process"].join(timeout=10.0)
        assert not host_record["process"].is_alive()
        lost_reply = _receive(strategy_record["pipe"], timeout=10.0)
        assert lost_reply[0] == "submit_error", lost_reply
        _close_normally(strategy_record, expected_tag="closed")

        restarted_host = _start_process(
            context,
            _host_process,
            (service_raw, registry_path, keys, gate_path, journal_path, provider_path),
            "risk-order-recovery-host",
        )
        records.append(restarted_host)
        assert _receive(restarted_host["pipe"])[0] == "ready"
        recovered = _client_command(restarted_host, "snapshot")
        assert recovered[0] == "snapshot"
        assert recovered[1] == ()
        assert recovered[4]["increase_count"] == 1
        assert recovered[4]["increase_notional"] == Decimal("20")
        assert recovered[4]["active_freeze_reasons"]

        new_strategy = _start_process(
            context,
            _strategy_process,
            (endpoint, strategy_public, strategy_secret, server_public),
            "risk-order-recovery-client",
        )
        records.append(new_strategy)
        recovered_client_ready = _receive(new_strategy["pipe"])
        assert recovered_client_ready[0] == "ready", recovered_client_ready
        recovered_run_id = recovered_client_ready[2]
        assert recovered_run_id != initial_run_id
        recovered_command = _client_command(restarted_host, "command", "claim-gap")
        assert recovered_command[4]["run_id"] == initial_run_id
        assert recovered_command[5].startswith("risk-order:")
        replay = _client_command(new_strategy, "submit", "claim-gap", "1", "20", _METADATA_VERSION)
        assert replay[0] == "submitted", replay
        assert replay[1]["state"] == "UNKNOWN"
        assert replay[1]["review_required"] is True
        assert replay[1]["dispatch_attempts"] == 0
        with sqlite3.connect(provider_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM provider_writes").fetchone()[0] == 0
        queried = _client_command(new_strategy, "query", "claim-gap")
        assert queried[0] == "queried", queried
        assert queried[1]["state"] == "UNKNOWN"
        with sqlite3.connect(provider_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM provider_writes").fetchone()[0] == 0
        final = _client_command(restarted_host, "snapshot")
        assert final[1] == ()
        assert final[4]["increase_count"] == 1
        assert final[4]["active_freeze_reasons"]

        _close_normally(new_strategy, expected_tag="closed")
        _close_normally(restarted_host, expected_tag="closed")
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)


def test_provider_written_order_is_reconciled_after_restart_without_resend(tmp_path) -> None:
    from tests.unit._spawned_process_helpers import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _client_command,
        _close_normally,
        _receive,
        _start_process,
    )

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
    }
    service_raw = _service_config(endpoint)
    registry_path = str(tmp_path / "risk-order-write-gap-registry.sqlite3")
    gate_path = str(tmp_path / "risk-order-write-gap-gate.sqlite3")
    journal_path = str(tmp_path / "risk-order-write-gap-journal.sqlite3")
    provider_path = str(tmp_path / "risk-order-write-gap-provider.sqlite3")
    records = []
    forced = []
    failures = []
    try:
        crashed_host = _start_process(
            context,
            _host_process,
            (
                service_raw,
                registry_path,
                keys,
                gate_path,
                journal_path,
                provider_path,
                "after_provider_write",
            ),
            "risk-order-write-gap-host",
        )
        records.append(crashed_host)
        assert _receive(crashed_host["pipe"])[0] == "ready"
        first_client = _start_process(
            context,
            _strategy_process,
            (endpoint, strategy_public, strategy_secret, server_public),
            "risk-order-write-gap-client",
        )
        records.append(first_client)
        first_client_ready = _receive(first_client["pipe"])
        assert first_client_ready[0] == "ready", first_client_ready
        first_run_id = first_client_ready[2]

        first_client["pipe"].send(("submit", "provider-write-gap", "1", "20", _METADATA_VERSION))
        crash = _receive(crashed_host["pipe"], timeout=10.0)
        assert crash == ("error", "RISK_PROVIDER_WRITE_PROCESS_LOSS")
        crashed_host["process"].join(timeout=10.0)
        assert not crashed_host["process"].is_alive()
        assert _receive(first_client["pipe"], timeout=10.0)[0] == "submit_error"
        _close_normally(first_client, expected_tag="closed")

        with sqlite3.connect(provider_path) as connection:
            provider_writes = connection.execute(
                "SELECT client_order_id FROM provider_writes ORDER BY sequence"
            ).fetchall()
        assert len(provider_writes) == 1
        assert provider_writes[0][0].startswith("risk-")

        recovered_host = _start_process(
            context,
            _host_process,
            (service_raw, registry_path, keys, gate_path, journal_path, provider_path),
            "risk-order-write-gap-recovery-host",
        )
        records.append(recovered_host)
        assert _receive(recovered_host["pipe"])[0] == "ready"
        recovered = _client_command(recovered_host, "snapshot")
        assert recovered[0] == "snapshot"
        assert len(recovered[1]) == 1
        assert recovered[2] == ()
        assert recovered[3] == ()
        assert recovered[4]["increase_count"] == 1
        assert recovered[4]["active_freeze_reasons"]

        recovered_client = _start_process(
            context,
            _strategy_process,
            (endpoint, strategy_public, strategy_secret, server_public),
            "risk-order-write-gap-recovery-client",
        )
        records.append(recovered_client)
        recovered_client_ready = _receive(recovered_client["pipe"])
        assert recovered_client_ready[0] == "ready", recovered_client_ready
        recovered_run_id = recovered_client_ready[2]
        assert recovered_run_id != first_run_id
        persisted_before_reconcile = _client_command(
            recovered_host, "command", "provider-write-gap"
        )
        assert persisted_before_reconcile[4]["run_id"] == first_run_id
        assert persisted_before_reconcile[3] == provider_writes[0][0]

        replay = _client_command(
            recovered_client,
            "submit",
            "provider-write-gap",
            "1",
            "20",
            _METADATA_VERSION,
        )
        assert replay[0] == "submitted", replay
        assert replay[1]["state"] == "UNKNOWN"
        assert replay[1]["review_required"] is True
        assert replay[1]["dispatch_attempts"] == 1
        with sqlite3.connect(provider_path) as connection:
            writes_after_replay = connection.execute(
                "SELECT client_order_id FROM provider_writes ORDER BY sequence"
            ).fetchall()
        assert writes_after_replay == provider_writes

        queried = _client_command(recovered_client, "query", "provider-write-gap")
        assert queried[0] == "queried", queried
        assert queried[1]["state"] == "ACKED_TRACKED"
        with sqlite3.connect(provider_path) as connection:
            writes_after_query = connection.execute(
                "SELECT client_order_id FROM provider_writes ORDER BY sequence"
            ).fetchall()
        assert writes_after_query == provider_writes
        final = _client_command(recovered_host, "snapshot")
        assert len(final[1]) == 1
        assert final[1] == (provider_writes[0][0],)
        assert final[4]["increase_count"] == 1
        assert not final[4]["active_freeze_reasons"]

        _close_normally(recovered_client, expected_tag="closed")
        _close_normally(recovered_host, expected_tag="closed")
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)


def test_ambiguous_query_cardinality_cannot_be_tracked_as_one_order(tmp_path) -> None:
    from tests.unit._spawned_process_helpers import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _client_command,
        _close_normally,
        _receive,
        _start_process,
    )

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
    }
    service_raw = _service_config(endpoint)
    registry_path = str(tmp_path / "risk-order-ambiguous-registry.sqlite3")
    gate_path = str(tmp_path / "risk-order-ambiguous-gate.sqlite3")
    journal_path = str(tmp_path / "risk-order-ambiguous-journal.sqlite3")
    provider_path = str(tmp_path / "risk-order-ambiguous-provider.sqlite3")
    records = []
    forced = []
    failures = []
    try:
        host_record = _start_process(
            context,
            _host_process,
            (service_raw, registry_path, keys, gate_path, journal_path, provider_path),
            "risk-order-ambiguous-host",
        )
        records.append(host_record)
        assert _receive(host_record["pipe"])[0] == "ready"
        strategy_record = _start_process(
            context,
            _strategy_process,
            (endpoint, strategy_public, strategy_secret, server_public),
            "risk-order-ambiguous-client",
        )
        records.append(strategy_record)
        assert _receive(strategy_record["pipe"])[0] == "ready"

        host_record["pipe"].send(("arm_ambiguous_query",))
        assert _receive(host_record["pipe"]) == ("ambiguous_query_armed",)
        submitted = _client_command(
            strategy_record, "submit", "ambiguous-result", "1", "20", _METADATA_VERSION
        )
        assert submitted[0] == "submitted", submitted
        assert submitted[1]["state"] == "UNKNOWN"
        assert submitted[1]["dispatch_attempts"] == 1
        assert submitted[1]["unknown_reason"] == "PROVIDER_OBSERVATION_INCOMPLETE"

        duplicate = _client_command(
            strategy_record, "submit", "ambiguous-result", "1", "20", _METADATA_VERSION
        )
        assert duplicate[0] == "submitted", duplicate
        assert duplicate[1]["state"] == "UNKNOWN"
        assert duplicate[1]["dispatch_attempts"] == 1
        snapshot = _client_command(host_record, "snapshot")
        assert len(snapshot[1]) == 1
        assert snapshot[2] == snapshot[1]
        assert snapshot[3] == snapshot[1]
        assert snapshot[4]["increase_count"] == 1
        assert snapshot[4]["increase_notional"] == Decimal("20")
        assert snapshot[4]["active_freeze_reasons"]

        _close_normally(strategy_record, expected_tag="closed")
        _close_normally(host_record, expected_tag="closed")
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)


@pytest.mark.parametrize(
    ("crash_mode", "expected_crash", "expected_recovery_events", "expected_first_state"),
    (
        ("after_proof_ready", "RISK_PROOF_READY_PROCESS_LOSS", 1, "process_loss"),
        ("after_risk_resolution", "RISK_RESOLUTION_PROCESS_LOSS", 0, "process_loss"),
        ("ack_journal_error_after_risk_resolution", None, 0, "unknown"),
    ),
)
def test_proof_ready_crash_recovers_from_exact_risk_receipt_without_redispatch(
    tmp_path, crash_mode, expected_crash, expected_recovery_events, expected_first_state
) -> None:
    from tests.unit._spawned_process_helpers import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _client_command,
        _close_normally,
        _receive,
        _start_process,
    )

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
    }
    service_raw = _service_config(endpoint)
    registry_path = str(tmp_path / f"risk-order-{crash_mode}-registry.sqlite3")
    gate_path = str(tmp_path / f"risk-order-{crash_mode}-gate.sqlite3")
    journal_path = str(tmp_path / f"risk-order-{crash_mode}-journal.sqlite3")
    provider_path = str(tmp_path / f"risk-order-{crash_mode}-provider.sqlite3")
    records = []
    forced = []
    failures = []
    try:
        crashed_host = _start_process(
            context,
            _host_process,
            (
                service_raw,
                registry_path,
                keys,
                gate_path,
                journal_path,
                provider_path,
                crash_mode,
            ),
            f"risk-order-{crash_mode}-host",
        )
        records.append(crashed_host)
        assert _receive(crashed_host["pipe"])[0] == "ready"
        first_client = _start_process(
            context,
            _strategy_process,
            (endpoint, strategy_public, strategy_secret, server_public),
            f"risk-order-{crash_mode}-client",
        )
        records.append(first_client)
        assert _receive(first_client["pipe"])[0] == "ready"

        if expected_first_state == "process_loss":
            first_client["pipe"].send(("submit", "proof-ready-gap", "1", "20", _METADATA_VERSION))
            crash = _receive(crashed_host["pipe"], timeout=10.0)
            assert crash == ("error", expected_crash)
            crashed_host["process"].join(timeout=10.0)
            assert not crashed_host["process"].is_alive()
            assert _receive(first_client["pipe"], timeout=10.0)[0] == "submit_error"
            _close_normally(first_client, expected_tag="closed")
        else:
            first_submit = _client_command(
                first_client, "submit", "proof-ready-gap", "1", "20", _METADATA_VERSION
            )
            assert first_submit[0] == "submitted", first_submit
            assert first_submit[1]["state"] == "UNKNOWN"
            assert first_submit[1]["review_required"] is True
            assert first_submit[1]["dispatch_attempts"] == 1
            unknown = _client_command(crashed_host, "command", "proof-ready-gap")
            assert unknown[1] == "UNKNOWN"
            assert unknown[2] is not None
            _close_normally(first_client, expected_tag="closed")
            _close_normally(crashed_host, expected_tag="closed")

        with sqlite3.connect(provider_path) as connection:
            writes_before_recovery = connection.execute(
                "SELECT client_order_id FROM provider_writes ORDER BY sequence"
            ).fetchall()
        assert len(writes_before_recovery) == 1

        recovered_host = _start_process(
            context,
            _host_process,
            (service_raw, registry_path, keys, gate_path, journal_path, provider_path),
            "risk-order-proof-ready-recovery-host",
        )
        records.append(recovered_host)
        assert _receive(recovered_host["pipe"])[0] == "ready"
        recovered = _client_command(recovered_host, "snapshot")
        assert len(recovered[1]) == 1
        assert recovered[1] == tuple(row[0] for row in writes_before_recovery)
        assert recovered[2] == ()
        assert recovered[3] == ()
        assert recovered[4]["increase_count"] == 1
        assert recovered[4]["increase_notional"] == Decimal("20")
        assert not recovered[4]["active_freeze_reasons"]

        with sqlite3.connect(journal_path) as connection:
            event_rows = connection.execute(
                "SELECT event_type, payload_json, revision, writer_fence_sha256 "
                "FROM risk_order_events WHERE scope_key=? AND principal_ref=? AND intent_id=? "
                "ORDER BY revision",
                (_ACCOUNT_SCOPE.key, _PRINCIPAL, "proof-ready-gap"),
            ).fetchall()
        event_types = [str(row[0]) for row in event_rows]
        assert event_types.count("PROOF_READY") == 1
        assert event_types.count("PROOF_RECOVERY_ATTESTED") == expected_recovery_events
        assert event_types.count("ACKED_TRACKED") == 1
        assert event_types[-1] == "ACKED_TRACKED"
        ack_payload = json.loads(next(row[1] for row in event_rows if row[0] == "ACKED_TRACKED"))
        assert type(ack_payload["updated_at_ns"]) is int
        assert ack_payload["updated_at_ns"] > 0
        with sqlite3.connect(journal_path) as connection:
            ack_row = connection.execute(
                "SELECT scope_key, principal_ref, intent_id, revision, payload_json, "
                "previous_account_sha256, previous_command_sha256, record_sha256, "
                "writer_owner_id, writer_fencing_token, writer_fence_sha256 "
                "FROM risk_order_events WHERE scope_key=? AND principal_ref=? "
                "AND intent_id=? AND event_type='ACKED_TRACKED'",
                (_ACCOUNT_SCOPE.key, _PRINCIPAL, "proof-ready-gap"),
            ).fetchone()
        assert ack_row is not None
        assert (
            sha256_text(
                canonical_json(
                    {
                        "event_type": "ACKED_TRACKED",
                        "intent_id": ack_row[2],
                        "payload": json.loads(ack_row[4]),
                        "previous_account_sha256": ack_row[5],
                        "previous_command_sha256": ack_row[6],
                        "principal_ref": ack_row[1],
                        "revision": ack_row[3],
                        "scope_key": ack_row[0],
                        "writer_fence_sha256": ack_row[10],
                        "writer_fencing_token": ack_row[9],
                        "writer_owner_id": ack_row[8],
                    }
                )
            )
            == ack_row[7]
        )
        if expected_recovery_events:
            recovery_row = next(row for row in event_rows if row[0] == "PROOF_RECOVERY_ATTESTED")
            recovery_payload = json.loads(recovery_row[1])
            assert recovery_payload["recovery_event_revision"] == recovery_row[2]
            assert recovery_payload["current_writer_fence_sha256"] == recovery_row[3]
            assert recovery_payload["proof_ready_revision"] < recovery_row[2]

        retry_client = _start_process(
            context,
            _strategy_process,
            (endpoint, strategy_public, strategy_secret, server_public),
            f"risk-order-{crash_mode}-retry-client",
        )
        records.append(retry_client)
        assert _receive(retry_client["pipe"])[0] == "ready"
        duplicate = _client_command(
            retry_client, "submit", "proof-ready-gap", "1", "20", _METADATA_VERSION
        )
        assert duplicate[0] == "submitted", duplicate
        assert duplicate[1]["state"] == "ACKED_TRACKED"
        assert duplicate[1]["dispatch_attempts"] == 1
        after_duplicate = _client_command(recovered_host, "snapshot")
        assert len(after_duplicate[1]) == 1
        assert after_duplicate[2] == ()
        assert after_duplicate[3] == ()

        queried = _client_command(retry_client, "query", "proof-ready-gap")
        assert queried[0] == "queried", queried
        assert queried[1]["state"] == "ACKED_TRACKED"

        final = _client_command(recovered_host, "snapshot")
        assert len(final[1]) == 1
        assert final[4]["increase_count"] == 1
        assert final[2] == final[1]
        assert final[3] == final[1]
        assert not final[4]["active_freeze_reasons"]
        with sqlite3.connect(journal_path) as connection:
            final_event_types = connection.execute(
                "SELECT event_type FROM risk_order_events WHERE scope_key=? "
                "AND principal_ref=? AND intent_id=? ORDER BY revision",
                (_ACCOUNT_SCOPE.key, _PRINCIPAL, "proof-ready-gap"),
            ).fetchall()
        assert [row[0] for row in final_event_types].count("PROVIDER_QUERY") == 1
        _close_normally(retry_client, expected_tag="closed")
        _close_normally(recovered_host, expected_tag="closed")
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
