"""DIM04 hedge-side and dated-close ownership through the real local owner.

The test uses the normal authenticated ServiceHost, Risk admission/journal,
BT API CTP order mapper, and query route.  Only the provider leaf and its
initial account positions are synthetic SQLite fixtures; this is local
owner evidence, not CTP or real-account acceptance.
"""

from __future__ import annotations

import json
import multiprocessing
import sqlite3
from collections import Counter
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
import zmq
from bt_api_base.account_protocol import AccountResourceKey
from bt_api_risk import AccountScope
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.integration import test_dim01_identity_isolation_service as dim01
from tests.integration import test_dim04_strategy_account_isolation as dim04
from tests.integration import test_dim04_strategy_net_close_owner_path as net_close
from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)


_CTP_EXCHANGE_A = "CTP___DIM04_FUTURE_A"
_CTP_EXCHANGE_A_ALIAS = "CTP___DIM04_FUTURE_A_ALIAS"
_POSITION_TABLE = "dim04_synthetic_dated_positions"
_CLOSE_CALLS_TABLE = "dim04_synthetic_dated_close_calls"
_DIMENSIONS = (
    ("LONG", "TODAY", "SELL", "CLOSE_TODAY", "close_today"),
    ("SHORT", "YESTERDAY", "BUY", "CLOSE_YESTERDAY", "close_yesterday"),
)
_STRATEGY_POSITION = Decimal("1")
_ACCOUNT_POSITION = Decimal("2")
_QUANTITY_TEXT = "1"
_CLOSE_PRICE = "20"
_IDS = {
    (dim01._STRATEGY_A, "LONG", "TODAY"): "dim04-hedge-a-long-today",
    (dim01._STRATEGY_A, "SHORT", "YESTERDAY"): "dim04-hedge-a-short-yesterday",
    (dim01._STRATEGY_B, "LONG", "TODAY"): "dim04-hedge-b-long-today",
    (dim01._STRATEGY_B, "SHORT", "YESTERDAY"): "dim04-hedge-b-short-yesterday",
}
_A_LONG_OVERDRAW = "dim04-hedge-a-long-today-overdraw"
_A_SHORT_OVERDRAW = "dim04-hedge-a-short-yesterday-overdraw"
_UNSUPPORTED_DIMENSION = "dim04-hedge-a-long-yesterday-unsupported"


def _slice_version(strategy_ref: str, position_side: str, position_bucket: str) -> str:
    return f"dim04-slice-{strategy_ref}-{position_side.lower()}-{position_bucket.lower()}-v1"


def _slice_signature(
    gate: dim04.DurableRiskGate,
    account: AccountScope,
    strategy_ref: str,
    position_side: str,
    position_bucket: str,
) -> tuple[Any, ...]:
    snapshot = gate.get_closable_position(
        account,
        dim01._BTC,
        strategy_ref,
        position_side=position_side,
        position_bucket=position_bucket,
    )
    return (
        snapshot.revision,
        snapshot.declared_quantity,
        snapshot.used_quantity,
        snapshot.available_quantity,
        snapshot.quantity_unit,
        snapshot.completeness,
        snapshot.source,
    )


def _seed_risk_slices(risk_path: str, journal_path: str, account: AccountScope) -> None:
    journal = dim04.RiskOrderJournal(
        journal_path, writer_id=dim04._OWNER_JOURNAL_ID
    )
    gate = dim04.DurableRiskGate(
        risk_path,
        dim04._risk_policy(journal),
        execution_journal_authority=journal,
        gross_exposure_authority=journal,
    )
    try:
        for strategy_ref in (dim01._STRATEGY_A, dim01._STRATEGY_B):
            gate.set_strategy_allocation(
                account,
                strategy_ref,
                _slice_version(strategy_ref, "NET", "ALL"),
                max_notional=Decimal("100000"),
                notional_unit="USD",
                max_gross_occupancy_notional=Decimal("100000"),
                max_gross_occupancy_intent_count=100,
            )
            for position_side, position_bucket, *_ in _DIMENSIONS:
                gate.set_closable_position(
                    account,
                    dim01._BTC,
                    strategy_ref,
                    _slice_version(strategy_ref, position_side, position_bucket),
                    closable_quantity=_STRATEGY_POSITION,
                    quantity_unit="BTC",
                    position_side=position_side,
                    position_bucket=position_bucket,
                )
        for strategy_ref in (dim01._STRATEGY_A, dim01._STRATEGY_B):
            for position_side, position_bucket, *_ in _DIMENSIONS:
                assert _slice_signature(
                    gate, account, strategy_ref, position_side, position_bucket
                ) == (
                    _slice_version(strategy_ref, position_side, position_bucket),
                    _STRATEGY_POSITION,
                    Decimal("0"),
                    _STRATEGY_POSITION,
                    "BTC",
                    "LOCAL_LEDGER_COMPLETE",
                    "local_closable_position_ledger",
                )
    finally:
        gate.close()


def _seed_synthetic_positions(provider_path: str) -> None:
    """Seed two aggregate contracts per dimension, one per strategy share."""

    with sqlite3.connect(provider_path) as connection:
        connection.execute(
            f"CREATE TABLE {_POSITION_TABLE} ("
            "instrument TEXT NOT NULL, position_side TEXT NOT NULL, "
            "position_bucket TEXT NOT NULL, quantity TEXT NOT NULL, revision INTEGER NOT NULL, "
            "PRIMARY KEY(instrument,position_side,position_bucket))"
        )
        connection.execute(
            f"CREATE TABLE {_CLOSE_CALLS_TABLE} ("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL, "
            "instrument TEXT NOT NULL, side TEXT NOT NULL, position_side TEXT NOT NULL, "
            "position_bucket TEXT NOT NULL, offset TEXT NOT NULL, quantity TEXT NOT NULL, "
            "before_quantity TEXT NOT NULL, after_quantity TEXT NOT NULL, "
            "provider_order_id TEXT NOT NULL)"
        )
        for position_side, position_bucket, *_ in _DIMENSIONS:
            connection.execute(
                f"INSERT INTO {_POSITION_TABLE} "
                "(instrument,position_side,position_bucket,quantity,revision) "
                "VALUES (?,?,?,?,1)",
                (
                    dim01._BTC,
                    position_side,
                    position_bucket,
                    format(_ACCOUNT_POSITION, "f"),
                ),
            )


def _dated_close_host_process(control, *host_arguments) -> None:
    """Use CTP's real request mapper with an exact local synthetic provider leaf."""

    original_account = dim01._ACCOUNT_SPOT_A
    original_exchange_a = dim01._EXCHANGE_SPOT_A
    original_exchange_alias = dim01._EXCHANGE_SPOT_A_ALIAS
    original_route_exchange = {
        route_ref: dim01._ROUTES[route_ref]["exchange_name"]
        for route_ref in (
            dim01._ROUTE_SPOT_A,
            dim01._ROUTE_SPOT_A_STRATEGY_B,
            dim01._ROUTE_SPOT_A_ACCOUNT_MODE,
        )
    }
    original_contract = dim01._provider_contract
    original_make_order = dim01._Dim01SyntheticV2Feed.make_order
    original_feed_query_order = dim01._Dim01SyntheticV2Feed.query_order
    original_target_type = dim01.RiskOrderTarget
    original_mapper_type = dim01.InstrumentRiskAdmissionMapper
    dim01._ACCOUNT_SPOT_A = AccountScope(
        "ctp", original_account.account_id, original_account.environment
    )
    dim01._EXCHANGE_SPOT_A = _CTP_EXCHANGE_A
    dim01._EXCHANGE_SPOT_A_ALIAS = _CTP_EXCHANGE_A_ALIAS
    dim01._ROUTES[dim01._ROUTE_SPOT_A]["exchange_name"] = _CTP_EXCHANGE_A
    dim01._ROUTES[dim01._ROUTE_SPOT_A_STRATEGY_B]["exchange_name"] = _CTP_EXCHANGE_A_ALIAS
    dim01._ROUTES[dim01._ROUTE_SPOT_A_ACCOUNT_MODE]["exchange_name"] = _CTP_EXCHANGE_A

    def _resources_for_scope(scope, registry):
        product_type = (
            "perpetual" if scope.environment == "simulation-perpetual" else "spot"
        )
        resource = AccountResourceKey(
            product_type,
            f"{scope.account_id}-{scope.environment}",
        )
        return {instrument: resource for instrument in registry.instruments}

    def _target_with_complete_resources(*, account_scope, registry, **kwargs):
        return original_target_type(
            account_scope=account_scope,
            registry=registry,
            resource_by_instrument=_resources_for_scope(account_scope, registry),
            **kwargs,
        )

    def _mapper_with_complete_resources(scope, registry, **kwargs):
        return original_mapper_type(
            scope,
            registry,
            resource_by_instrument=_resources_for_scope(scope, registry),
            **kwargs,
        )

    def _contract_with_hedged_dated_close(exchange_name, profile_id, **kwargs):
        contract = original_contract(exchange_name, profile_id, **kwargs)
        if exchange_name not in {_CTP_EXCHANGE_A, _CTP_EXCHANGE_A_ALIAS}:
            return contract
        key = (exchange_name, profile_id)
        if contract.supported_position_effects != ("OPEN",):
            raise AssertionError((key, contract.supported_position_effects))
        dimensions = (("NET", "ALL"), ("LONG", "TODAY"), ("SHORT", "YESTERDAY"))
        contract = replace(
            contract,
            supported_position_effects=("OPEN", "CLOSE"),
            supported_position_dimensions=dimensions,
        )
        control.send(("dated_close_contract", exchange_name, profile_id, dimensions))
        return contract

    def _make_order_with_dated_position_guard(
        self: Any,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        *,
        client_order_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        quantity = Decimal(str(volume))
        side = order_type.split("-", 1)[0].upper()
        offset = kwargs.get("offset")
        expected = {
            ("SELL", "close_today"): ("LONG", "TODAY"),
            ("BUY", "close_yesterday"): ("SHORT", "YESTERDAY"),
        }.get((side, offset))
        if symbol != dim01._BTC or expected is None or quantity <= 0:
            raise AssertionError((symbol, side, offset, quantity, order_type))
        position_side, position_bucket = expected
        with sqlite3.connect(self._ledger_path) as connection:
            row = connection.execute(
                f"SELECT quantity,revision FROM {_POSITION_TABLE} "
                "WHERE instrument=? AND position_side=? AND position_bucket=?",
                (symbol, position_side, position_bucket),
            ).fetchone()
        if row is None:
            raise AssertionError(("synthetic position slice missing", expected))
        before = Decimal(str(row[0]))
        revision = int(row[1])
        if quantity > before:
            raise AssertionError(("synthetic dated position would overclose", expected, quantity, before))
        quantity_text = format(quantity.normalize(), "f")
        before_text = format(before.normalize(), "f")

        result = original_make_order(
            self,
            symbol,
            volume,
            price,
            order_type,
            client_order_id=client_order_id,
            **kwargs,
        )
        payload = result.get("data") if type(result) is dict else None
        if type(payload) is not list or len(payload) != 1 or type(payload[0]) is not dict:
            raise AssertionError(("synthetic provider returned no unique order", result))
        order = dict(payload[0])
        if (
            order.get("client_order_id") != client_order_id
            or order.get("symbol") != symbol
            or order.get("side") != side.lower()
            or order.get("status") != "filled"
            or Decimal(str(order.get("filled", "0"))) != quantity
        ):
            raise AssertionError(("synthetic fill differs from CTP mapped order", order))
        order.update(
            offset=offset,
            position_side=position_side,
            position_bucket=position_bucket,
            quantity=quantity_text,
            filled=quantity_text,
        )
        with sqlite3.connect(self._ledger_path) as connection:
            fills_row = connection.execute(
                "SELECT fills_json FROM provider_orders "
                "WHERE id_namespace=? AND client_order_id=?",
                (self._id_namespace(), client_order_id),
            ).fetchone()
        if fills_row is None:
            raise AssertionError(("synthetic provider fill row missing", client_order_id))
        fills = json.loads(fills_row[0])
        if type(fills) is not list or len(fills) != 1 or type(fills[0]) is not dict:
            raise AssertionError(("synthetic provider returned no unique fill", fills))
        fill = dict(fills[0])
        fill.update(
            offset=offset,
            position_side=position_side,
            position_bucket=position_bucket,
            quantity=quantity_text,
        )
        after = before - quantity
        after_text = format(after.normalize(), "f")
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                f"UPDATE {_POSITION_TABLE} SET quantity=?,revision=revision+1 "
                "WHERE instrument=? AND position_side=? AND position_bucket=? "
                "AND quantity=? AND revision=?",
                (
                    after_text,
                    symbol,
                    position_side,
                    position_bucket,
                    before_text,
                    revision,
                ),
            )
            if updated.rowcount != 1:
                raise AssertionError("synthetic position slice changed during close")
            stored = connection.execute(
                "UPDATE provider_orders SET order_json=?,fills_json=? "
                "WHERE id_namespace=? AND client_order_id=?",
                (
                    json.dumps(order, sort_keys=True, separators=(",", ":")),
                    json.dumps([fill], sort_keys=True, separators=(",", ":")),
                    self._id_namespace(),
                    client_order_id,
                ),
            )
            if stored.rowcount != 1:
                raise AssertionError("synthetic source rows could not be enriched")
            connection.execute(
                f"INSERT INTO {_CLOSE_CALLS_TABLE} "
                "(client_order_id,instrument,side,position_side,position_bucket,offset,quantity,"
                "before_quantity,after_quantity,provider_order_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    client_order_id,
                    symbol,
                    side,
                    position_side,
                    position_bucket,
                    offset,
                    quantity_text,
                    before_text,
                    after_text,
                    str(order["order_id"]),
                ),
            )
        result["data"] = [order]
        control.send(
            (
                "dated_close_written",
                client_order_id,
                str(order["order_id"]),
                side,
                position_side,
                position_bucket,
                offset,
                quantity_text,
                before_text,
                after_text,
            )
        )
        return result

    def _query_ctp_order_by_order_ref(
        self: Any,
        symbol: str,
        order_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        # DirectBackend's supported CTP query path carries the stable client
        # identifier as OrderRef, not as Binance-style client_order_id.
        if order_id is None:
            order_ref = kwargs.get("order_ref")
            if type(order_ref) is not str or not order_ref:
                raise AssertionError("CTP synthetic query omitted its OrderRef")
            order_id = order_ref
        return original_feed_query_order(self, symbol, order_id, **kwargs)

    dim01._provider_contract = _contract_with_hedged_dated_close
    dim01._Dim01SyntheticV2Feed.make_order = _make_order_with_dated_position_guard
    dim01._Dim01SyntheticV2Feed.query_order = _query_ctp_order_by_order_ref
    dim01.RiskOrderTarget = _target_with_complete_resources
    dim01.InstrumentRiskAdmissionMapper = _mapper_with_complete_resources

    try:
        dim04._dim04_host_process(control, *host_arguments)
    finally:
        dim01._provider_contract = original_contract
        dim01._Dim01SyntheticV2Feed.make_order = original_make_order
        dim01._Dim01SyntheticV2Feed.query_order = original_feed_query_order
        dim01.RiskOrderTarget = original_target_type
        dim01.InstrumentRiskAdmissionMapper = original_mapper_type
        dim01._ACCOUNT_SPOT_A = original_account
        dim01._EXCHANGE_SPOT_A = original_exchange_a
        dim01._EXCHANGE_SPOT_A_ALIAS = original_exchange_alias
        for route_ref, exchange_name in original_route_exchange.items():
            dim01._ROUTES[route_ref]["exchange_name"] = exchange_name


def _dated_close_runtime_client(
    control,
    endpoint: str,
    strategy_public: bytes,
    strategy_secret: bytes,
    server_public: bytes,
    strategy_ref: str,
    principal_ref: str,
    route_ref: str,
) -> None:
    runtime = None
    try:
        route = dim01._ROUTES[route_ref]
        scope_ref = route["scope_ref"]
        scope_map = {scope_ref: dim01._SCOPES[scope_ref]}
        config = dim01._client_config(
            endpoint,
            dim01._TRANSPORT_CONNECT,
            strategy_ref,
            (route_ref,),
        )
        runtime = ServiceClientRuntime(
            config,
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref),
            scope_resolver=lambda selected: scope_map[selected],
            peer_resolver=lambda _service_id: WireIdentity(
                WireRole.SERVICE,
                dim01._SERVICE_PRINCIPAL,
                dim01._SERVICE_ID,
            ),
            transport_factory=ZmqTransportFactory(
                {
                    dim01._TRANSPORT_CONNECT: ClientCurveKeys(
                        server_public,
                        strategy_public,
                        strategy_secret,
                    )
                }
            ),
        )
        startup = runtime.start()
        assert startup.degraded_count == 0
        control.send(("ready", runtime.run_id))
        while True:
            command = control.recv()
            if command == ("close",):
                runtime.close()
                control.send(("closed", runtime.state))
                return
            if command[0] == "submit":
                (
                    _,
                    intent_id,
                    side,
                    position_effect,
                    position_side,
                    position_bucket,
                    quantity,
                ) = command
                result = runtime.call(
                    "broker.orders",
                    "orders.submit",
                    {
                        "intent_id": intent_id,
                        "signal_id": "signal-" + intent_id,
                        "instrument": dim01._BTC,
                        "side": side,
                        "position_effect": position_effect,
                        "position_side": position_side,
                        "position_bucket": position_bucket,
                        "order_type": "LIMIT",
                        "quantity": quantity,
                        "price": _CLOSE_PRICE,
                        "time_in_force": "GTC",
                        "reduce_only": True,
                        "metadata_version": dim01._VERSION_BTC_SPOT,
                        "deadline_ns": None,
                        "tags": {},
                    },
                    scope_ref=scope_ref,
                )
                control.send(("runtime_result", dim04._strict_json_thaw(result)))
                continue
            if command[0] == "query":
                _, intent_id = command
                result = runtime.call(
                    "broker.order_query",
                    "orders.query",
                    {"intent_id": intent_id},
                    scope_ref=scope_ref,
                )
                control.send(("runtime_result", dim04._strict_json_thaw(result)))
                continue
            raise AssertionError(("unknown dated-close client command", command))
    except BaseException as exc:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(BaseException):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _runtime_result(client: dict[str, Any], *command: Any) -> dict[str, Any]:
    assert client["process"].is_alive(), client["label"]
    if command:
        client["pipe"].send(command)
    response = _receive(client["pipe"])
    assert response[0] == "runtime_result", response
    result = response[1]
    assert type(result) is dict and frozenset(result) == {"ok", "code", "result"}, result
    assert type(result["result"]) is dict, result
    return result


def _send_close(
    client: dict[str, Any],
    intent_id: str,
    *,
    side: str,
    position_side: str,
    position_bucket: str,
    quantity: str = _QUANTITY_TEXT,
) -> None:
    client["pipe"].send(
        (
            "submit",
            intent_id,
            side,
            "CLOSE_TODAY" if position_bucket == "TODAY" else "CLOSE_YESTERDAY",
            position_side,
            position_bucket,
            quantity,
        )
    )


def _assert_filled(
    reply: dict[str, Any],
    intent_id: str,
    account: AccountScope,
    *,
    journal_path: str,
    principal_ref: str,
) -> dict[str, Any]:
    if reply.get("ok") is not True or reply.get("code") != "OK":
        reason_code = dim04._denial_reason(journal_path, principal_ref, intent_id)
        raise AssertionError(
            "expected a terminal fill; authenticated Runtime.call returned "
            + json.dumps(
                {"admission_reason_code": reason_code, "reply": reply},
                sort_keys=True,
                indent=2,
            )
        )
    payload = reply["result"]
    assert payload.get("found") is True, payload
    record = payload["record"]
    assert record["intent_id"] == intent_id, record
    assert record["scope_key"] == account.key, record
    if record["state"] != "FILLED":
        events = net_close._read_rows(
            journal_path,
            "SELECT revision,event_type,payload_json FROM risk_order_events "
            "WHERE scope_key=? AND principal_ref=? AND intent_id=? ORDER BY revision",
            (account.key, principal_ref, intent_id),
        )
        raise AssertionError(
            "expected a terminal fill; authenticated query returned "
            + json.dumps(
                {
                    "record": record,
                    "events": [
                        {
                            "revision": event["revision"],
                            "event_type": event["event_type"],
                            "payload": json.loads(event["payload_json"]),
                        }
                        for event in events
                    ],
                },
                sort_keys=True,
                indent=2,
            )
        )
    assert record["filled_quantity"] == _QUANTITY_TEXT, record
    assert record["average_price"] == _CLOSE_PRICE, record
    assert record["dispatch_attempts"] == 1, record
    assert type(record["provider_order_id"]) is str and record["provider_order_id"], record
    assert type(record["permit_reference"]) is str and record["permit_reference"], record
    return record


def _assert_slice_signature(
    risk_path: str,
    journal_path: str,
    account: AccountScope,
    strategy_ref: str,
    position_side: str,
    position_bucket: str,
    *,
    used: Decimal,
    available: Decimal,
) -> tuple[Any, ...]:
    journal = dim04.RiskOrderJournal(
        journal_path, writer_id=dim04._OWNER_JOURNAL_ID
    )
    gate = dim04.DurableRiskGate(
        risk_path,
        dim04._risk_policy(journal),
        execution_journal_authority=journal,
        gross_exposure_authority=journal,
    )
    try:
        result = _slice_signature(
            gate, account, strategy_ref, position_side, position_bucket
        )
        assert result == (
            _slice_version(strategy_ref, position_side, position_bucket),
            _STRATEGY_POSITION,
            used,
            available,
            "BTC",
            "LOCAL_LEDGER_COMPLETE",
            "local_closable_position_ledger",
        ), result
        return result
    finally:
        gate.close()


def _position_rows(provider_path: str) -> tuple[dict[str, Any], ...]:
    return net_close._read_rows(
        provider_path,
        f"SELECT instrument,position_side,position_bucket,quantity,revision "
        f"FROM {_POSITION_TABLE} ORDER BY position_side,position_bucket",
    )


def _assert_no_provider_writes(host: dict[str, Any], expected: int) -> None:
    host["pipe"].send(("provider_write_count",))
    assert _receive(host["pipe"]) == ("provider_write_count", expected)


def test_dim04_hedged_dated_close_reservations_are_owner_and_bucket_scoped(tmp_path) -> None:
    """Exercise both hedged dated slices and deny A borrowing B's remaining lots."""

    original_account = dim01._ACCOUNT_SPOT_A
    account = AccountScope("ctp", original_account.account_id, original_account.environment)
    # The existing fixture's account scope must match the CTP mapper's provider
    # identity. Restore it even if fixture setup or the Host fails.
    dim01._ACCOUNT_SPOT_A = account
    context = multiprocessing.get_context("spawn")
    endpoint = dim01._endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_a_public, strategy_a_secret = zmq.curve_keypair()
    strategy_b_public, strategy_b_secret = zmq.curve_keypair()
    key_material = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_a_public": strategy_a_public,
        "strategy_b_public": strategy_b_public,
    }
    paths = {
        "registry": str(tmp_path / "dim04-dated-registry.sqlite3"),
        "risk": str(tmp_path / "dim04-dated-risk.sqlite3"),
        "journal": str(tmp_path / "dim04-dated-journal.sqlite3"),
        "provider": str(tmp_path / "dim04-dated-provider.sqlite3"),
    }
    _seed_synthetic_positions(paths["provider"])
    _seed_risk_slices(paths["risk"], paths["journal"], account)

    host = None
    clients: list[dict[str, Any]] = []
    forced: list[str] = []
    failures: list[str] = []
    records: dict[str, dict[str, Any]] = {}
    try:
        host = _start_process(
            context,
            _dated_close_host_process,
            (
                dim01._service_config(endpoint),
                paths["registry"],
                key_material,
                paths["risk"],
                paths["journal"],
                paths["provider"],
            ),
            "dim04-hedged-dated-owner-host",
        )
        host_messages: list[tuple[Any, ...]] = []
        while True:
            message = _receive(host["pipe"])
            if message[0] == "error":
                pytest.fail(f"dated-close ServiceHost startup failed: {message!r}")
            if message[0] == "ready":
                host_ready = message
                break
            host_messages.append(message)
        dimensions = (("NET", "ALL"), ("LONG", "TODAY"), ("SHORT", "YESTERDAY"))
        expected_host_messages = Counter(
            (
                (
                    "dated_close_contract",
                    _CTP_EXCHANGE_A,
                    dim01._PROFILE_SPOT,
                    dimensions,
                ),
                (
                    "dated_close_contract",
                    _CTP_EXCHANGE_A_ALIAS,
                    dim01._PROFILE_SPOT_ROTATED,
                    dimensions,
                ),
                (
                    "dated_close_contract",
                    _CTP_EXCHANGE_A,
                    dim01._PROFILE_SPOT,
                    dimensions,
                ),
                ("synthetic_provider_fixture", "bt_api.plugins", "empty"),
            )
        )
        assert Counter(host_messages) == expected_host_messages, host_messages
        assert host_ready == ("ready", endpoint, "started"), host_ready

        for strategy_ref, principal_ref, public_key, secret_key, route_ref in (
            (
                dim01._STRATEGY_A,
                dim01._PRINCIPAL_A,
                strategy_a_public,
                strategy_a_secret,
                dim01._ROUTE_SPOT_A,
            ),
            (
                dim01._STRATEGY_B,
                dim01._PRINCIPAL_B,
                strategy_b_public,
                strategy_b_secret,
                dim01._ROUTE_SPOT_A_STRATEGY_B,
            ),
        ):
            client = _start_process(
                context,
                _dated_close_runtime_client,
                (
                    endpoint,
                    public_key,
                    secret_key,
                    server_public,
                    strategy_ref,
                    principal_ref,
                    route_ref,
                ),
                "dim04-hedged-dated-client-" + strategy_ref,
            )
            clients.append(client)
            ready = _receive(client["pipe"])
            assert ready[0] == "ready" and ready[1], ready

        client_by_strategy = {
            dim01._STRATEGY_A: clients[0],
            dim01._STRATEGY_B: clients[1],
        }
        # Cross a trusted unsupported combination. The parser must reject it
        # before writing a Risk reservation or invoking the provider.
        unsupported = _runtime_result(
            client_by_strategy[dim01._STRATEGY_A],
            "submit",
            _UNSUPPORTED_DIMENSION,
            "BUY",
            "CLOSE_YESTERDAY",
            "LONG",
            "YESTERDAY",
            _QUANTITY_TEXT,
        )
        assert unsupported["ok"] is False
        assert unsupported["code"] == "POSITION_DIMENSIONS_UNSUPPORTED", unsupported
        _assert_no_provider_writes(host, 0)
        assert net_close._read_rows(
            paths["journal"],
            "SELECT intent_id FROM risk_order_commands WHERE intent_id=?",
            (_UNSUPPORTED_DIMENSION,),
        ) == ()

        # A owns LONG/TODAY=1. Its exact CTP close maps to SELL/close_today.
        intent_a_long = _IDS[(dim01._STRATEGY_A, "LONG", "TODAY")]
        _send_close(
            client_by_strategy[dim01._STRATEGY_A],
            intent_a_long,
            side="SELL",
            position_side="LONG",
            position_bucket="TODAY",
        )
        record_a_long = _assert_filled(
            _runtime_result(client_by_strategy[dim01._STRATEGY_A]),
            intent_a_long,
            account,
            journal_path=paths["journal"],
            principal_ref=dim01._PRINCIPAL_A,
        )
        records[intent_a_long] = record_a_long
        long_event = _receive(host["pipe"])
        assert long_event[0] == "dated_close_written", long_event
        assert long_event[2:] == (
            record_a_long["provider_order_id"],
            "SELL",
            "LONG",
            "TODAY",
            "close_today",
            _QUANTITY_TEXT,
            "2",
            "1",
        ), long_event
        assert long_event[1] == net_close._read_rows(
            paths["journal"],
            "SELECT client_order_id FROM risk_order_commands WHERE intent_id=?",
            (intent_a_long,),
        )[0]["client_order_id"]
        _assert_slice_signature(
            paths["risk"], paths["journal"], account,
            dim01._STRATEGY_A, "LONG", "TODAY",
            used=_STRATEGY_POSITION, available=Decimal("0"),
        )
        _assert_slice_signature(
            paths["risk"], paths["journal"], account,
            dim01._STRATEGY_B, "LONG", "TODAY",
            used=Decimal("0"), available=_STRATEGY_POSITION,
        )
        long_position = [row for row in _position_rows(paths["provider"]) if row["position_bucket"] == "TODAY"]
        assert len(long_position) == 1 and long_position[0]["quantity"] == "1", long_position

        # A cannot spend B's still-open LONG/TODAY allocation even though the
        # shared synthetic account still holds one contract in that slice.
        overdraw_long = _runtime_result(
            client_by_strategy[dim01._STRATEGY_A],
            "submit",
            _A_LONG_OVERDRAW,
            "SELL",
            "CLOSE_TODAY",
            "LONG",
            "TODAY",
            "0.01",
        )
        dim04._assert_denied_without_dispatch(overdraw_long, _A_LONG_OVERDRAW)
        assert dim04._denial_reason(
            paths["journal"], dim01._PRINCIPAL_A, _A_LONG_OVERDRAW
        ) == "CLOSE_QUANTITY_EXHAUSTED"
        overdraw_query = net_close._runtime_result(
            client_by_strategy[dim01._STRATEGY_A], "query", _A_LONG_OVERDRAW
        )
        dim04._assert_rejected_query_without_dispatch(overdraw_query, _A_LONG_OVERDRAW)
        assert overdraw_query["result"]["record"]["permit_reference"] is None
        _assert_no_provider_writes(host, 1)

        # Same account, independent SHORT/YESTERDAY allocation. A's BUY close
        # must map to close_yesterday and leave B's corresponding slice intact.
        intent_a_short = _IDS[(dim01._STRATEGY_A, "SHORT", "YESTERDAY")]
        _send_close(
            client_by_strategy[dim01._STRATEGY_A],
            intent_a_short,
            side="BUY",
            position_side="SHORT",
            position_bucket="YESTERDAY",
        )
        record_a_short = _assert_filled(
            _runtime_result(client_by_strategy[dim01._STRATEGY_A]),
            intent_a_short,
            account,
            journal_path=paths["journal"],
            principal_ref=dim01._PRINCIPAL_A,
        )
        records[intent_a_short] = record_a_short
        short_event = _receive(host["pipe"])
        assert short_event[0] == "dated_close_written", short_event
        assert short_event[2:] == (
            record_a_short["provider_order_id"],
            "BUY",
            "SHORT",
            "YESTERDAY",
            "close_yesterday",
            _QUANTITY_TEXT,
            "2",
            "1",
        ), short_event
        assert short_event[1] == net_close._read_rows(
            paths["journal"],
            "SELECT client_order_id FROM risk_order_commands WHERE intent_id=?",
            (intent_a_short,),
        )[0]["client_order_id"]
        _assert_slice_signature(
            paths["risk"], paths["journal"], account,
            dim01._STRATEGY_A, "SHORT", "YESTERDAY",
            used=_STRATEGY_POSITION, available=Decimal("0"),
        )
        _assert_slice_signature(
            paths["risk"], paths["journal"], account,
            dim01._STRATEGY_B, "SHORT", "YESTERDAY",
            used=Decimal("0"), available=_STRATEGY_POSITION,
        )
        overdraw_short = _runtime_result(
            client_by_strategy[dim01._STRATEGY_A],
            "submit",
            _A_SHORT_OVERDRAW,
            "BUY",
            "CLOSE_YESTERDAY",
            "SHORT",
            "YESTERDAY",
            "0.01",
        )
        dim04._assert_denied_without_dispatch(overdraw_short, _A_SHORT_OVERDRAW)
        assert dim04._denial_reason(
            paths["journal"], dim01._PRINCIPAL_A, _A_SHORT_OVERDRAW
        ) == "CLOSE_QUANTITY_EXHAUSTED"
        _assert_no_provider_writes(host, 2)

        # B closes its exact two remaining slices through its own principal and
        # Risk reservations; neither operation touches A's other dimension.
        for position_side, position_bucket, side, position_effect, offset in _DIMENSIONS:
            intent_id = _IDS[(dim01._STRATEGY_B, position_side, position_bucket)]
            _send_close(
                client_by_strategy[dim01._STRATEGY_B],
                intent_id,
                side=side,
                position_side=position_side,
                position_bucket=position_bucket,
            )
            record = _assert_filled(
                _runtime_result(client_by_strategy[dim01._STRATEGY_B]),
                intent_id,
                account,
                journal_path=paths["journal"],
                principal_ref=dim01._PRINCIPAL_B,
            )
            records[intent_id] = record
            event = _receive(host["pipe"])
            assert event[0] == "dated_close_written", event
            assert event[1] == net_close._read_rows(
                paths["journal"],
                "SELECT client_order_id FROM risk_order_commands WHERE intent_id=?",
                (intent_id,),
            )[0]["client_order_id"]
            assert event[2:] == (
                record["provider_order_id"],
                side,
                position_side,
                position_bucket,
                offset,
                _QUANTITY_TEXT,
                "1",
                "0",
            ), event
            _assert_slice_signature(
                paths["risk"], paths["journal"], account,
                dim01._STRATEGY_B, position_side, position_bucket,
                used=_STRATEGY_POSITION, available=Decimal("0"),
            )

        # Re-query terminal orders through the normal read-only owner mount.
        for strategy_ref, intent_ids in (
            (
                dim01._STRATEGY_A,
                (_IDS[(dim01._STRATEGY_A, "LONG", "TODAY")],
                 _IDS[(dim01._STRATEGY_A, "SHORT", "YESTERDAY")]),
            ),
            (
                dim01._STRATEGY_B,
                (_IDS[(dim01._STRATEGY_B, "LONG", "TODAY")],
                 _IDS[(dim01._STRATEGY_B, "SHORT", "YESTERDAY")]),
            ),
        ):
            for intent_id in intent_ids:
                query = net_close._runtime_result(
                    client_by_strategy[strategy_ref], "query", intent_id
                )
                assert query["ok"] is True and query["result"]["record"] == records[intent_id]

        reservations = net_close._read_rows(
            paths["risk"],
            "SELECT status,scope_key,strategy_id,instrument AS instrument_id,action,quantity,"
            "quantity_unit,position_side,position_bucket,close_position_version "
            "FROM risk_reservations ORDER BY strategy_id,position_side",
        )
        assert len(reservations) == 4, reservations
        expected_reservations = {
            (strategy, side, bucket): {
                "status": "settled",
                "scope_key": account.key,
                "strategy_id": strategy,
                "instrument_id": dim01._BTC,
                "action": "reduce",
                "quantity": _QUANTITY_TEXT,
                "quantity_unit": "BTC",
                "position_side": side,
                "position_bucket": bucket,
                "close_position_version": _slice_version(strategy, side, bucket),
            }
            for strategy in (dim01._STRATEGY_A, dim01._STRATEGY_B)
            for side, bucket, *_ in _DIMENSIONS
        }
        assert {
            (row["strategy_id"], row["position_side"], row["position_bucket"]): row
            for row in reservations
        } == expected_reservations, reservations

        commands = net_close._read_rows(
            paths["journal"],
            "SELECT intent_id,principal_ref,client_order_id,canonical_payload_json "
            "FROM risk_order_commands WHERE scope_key=? ORDER BY intent_id",
            (account.key,),
        )
        assert len(commands) == 6, commands
        for command in commands:
            if command["intent_id"] in {_A_LONG_OVERDRAW, _A_SHORT_OVERDRAW}:
                payload = json.loads(command["canonical_payload_json"])
                assert payload["position_side"] in {"LONG", "SHORT"}, payload
                assert payload["position_bucket"] in {"TODAY", "YESTERDAY"}, payload
                continue
            strategy_ref = (
                dim01._STRATEGY_A
                if command["intent_id"] in {
                    _IDS[(dim01._STRATEGY_A, "LONG", "TODAY")],
                    _IDS[(dim01._STRATEGY_A, "SHORT", "YESTERDAY")],
                }
                else dim01._STRATEGY_B
            )
            expected_principal = (
                dim01._PRINCIPAL_A if strategy_ref == dim01._STRATEGY_A else dim01._PRINCIPAL_B
            )
            assert command["principal_ref"] == expected_principal, command
            payload = json.loads(command["canonical_payload_json"])
            assert payload["position_effect"] == "CLOSE", payload
            assert payload["reduce_only"] is True, payload
            assert payload["quantity"] == _QUANTITY_TEXT, payload
            assert payload["position_side"] in {"LONG", "SHORT"}, payload
            assert payload["position_bucket"] in {"TODAY", "YESTERDAY"}, payload

        provider_calls = net_close._read_rows(
            paths["provider"],
            f"SELECT client_order_id,instrument,side,position_side,position_bucket,offset,"
            f"quantity,before_quantity,after_quantity,provider_order_id "
            f"FROM {_CLOSE_CALLS_TABLE} ORDER BY sequence",
        )
        assert len(provider_calls) == 4, provider_calls
        assert [
            (row["side"], row["position_side"], row["position_bucket"], row["offset"])
            for row in provider_calls
        ] == [
            ("SELL", "LONG", "TODAY", "close_today"),
            ("BUY", "SHORT", "YESTERDAY", "close_yesterday"),
            ("SELL", "LONG", "TODAY", "close_today"),
            ("BUY", "SHORT", "YESTERDAY", "close_yesterday"),
        ], provider_calls
        for row, intent_id in zip(
            provider_calls,
            (
                _IDS[(dim01._STRATEGY_A, "LONG", "TODAY")],
                _IDS[(dim01._STRATEGY_A, "SHORT", "YESTERDAY")],
                _IDS[(dim01._STRATEGY_B, "LONG", "TODAY")],
                _IDS[(dim01._STRATEGY_B, "SHORT", "YESTERDAY")],
            ),
            strict=True,
        ):
            assert row["instrument"] == dim01._BTC, row
            assert row["quantity"] == _QUANTITY_TEXT, row
            assert row["client_order_id"] == net_close._read_rows(
                paths["journal"],
                "SELECT client_order_id FROM risk_order_commands WHERE intent_id=?",
                (intent_id,),
            )[0]["client_order_id"]
        final_positions = _position_rows(paths["provider"])
        assert len(final_positions) == 2, final_positions
        assert all(row["quantity"] == "0" and row["revision"] == 3 for row in final_positions)
        source_rows = net_close._read_rows(
            paths["provider"],
            "SELECT client_order_id,order_json,fills_json FROM provider_orders "
            "ORDER BY client_order_id",
        )
        assert len(source_rows) == 4, source_rows
        for row in source_rows:
            order = json.loads(row["order_json"])
            fills = json.loads(row["fills_json"])
            assert len(fills) == 1, fills
            assert order["position_side"] == fills[0]["position_side"], row
            assert order["position_bucket"] == fills[0]["position_bucket"], row
            assert order["offset"] == fills[0]["offset"], row
        assert dim04._provider_evidence(paths["provider"])[1] == {
            "fill_queries": 4,
            "order_queries": 4,
        }
        _assert_no_provider_writes(host, 4)

        for client in reversed(clients):
            assert _close_normally(client, expected_tag="closed") == ("closed", "closed")
        assert _close_normally(host, expected_tag="closed") == ("closed", "closed")
    finally:
        for client in reversed(clients):
            _bounded_reap_spawned_process(client, forced, failures)
        if host is not None:
            _bounded_reap_spawned_process(host, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
        dim01._ACCOUNT_SPOT_A = original_account
