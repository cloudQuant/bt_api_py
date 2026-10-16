"""DIM04 net CLOSE through the authenticated account owner and Risk journal.

The endpoint is the accepted DIM01 ServiceHost/SDK path and the provider leaf
is DIM01's synthetic SQLite feed.  The feed wrapper records and enforces a
test-owned net position; this is local owner evidence, not exchange or real
account acceptance.
"""

from __future__ import annotations

import json
import multiprocessing
import sqlite3
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest
import zmq
from bt_api_base.account_protocol import AccountResourceKey
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.integration import test_dim01_identity_isolation_service as dim01
from tests.integration import test_dim04_strategy_account_isolation as dim04
from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)


_CLOSE_INTENT = "dim04-net-close-strategy-b-btc"
_CLOSE_QUANTITY = Decimal("0.75")
_STARTING_NET_POSITION = Decimal("2")
_POSITION_TABLE = "dim04_synthetic_net_positions"
_CLOSE_CALL_TABLE = "dim04_synthetic_close_calls"


def _seed_synthetic_net_position(provider_path: str) -> None:
    with sqlite3.connect(provider_path) as connection:
        connection.execute(
            f"CREATE TABLE {_POSITION_TABLE} ("
            "instrument TEXT PRIMARY KEY, quantity TEXT NOT NULL, revision INTEGER NOT NULL)"
        )
        connection.execute(
            f"CREATE TABLE {_CLOSE_CALL_TABLE} ("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL, "
            "instrument TEXT NOT NULL, side TEXT NOT NULL, offset TEXT NOT NULL, "
            "order_type TEXT NOT NULL, quantity TEXT NOT NULL, before_quantity TEXT NOT NULL, "
            "after_quantity TEXT NOT NULL, provider_order_id TEXT NOT NULL)"
        )
        connection.execute(
            f"INSERT INTO {_POSITION_TABLE}(instrument,quantity,revision) VALUES (?,?,1)",
            (dim01._BTC, format(_STARTING_NET_POSITION, "f")),
        )


def _dim04_net_close_host_process(
    control, *host_arguments, expected_provider_close_calls: int = 1
) -> None:
    """Add one exact close-capable synthetic route to the real DIM01 owner."""

    original_contract = dim01._provider_contract
    original_make_order = dim01._Dim01SyntheticV2Feed.make_order
    original_target_type = dim01.RiskOrderTarget
    original_mapper_type = dim01.InstrumentRiskAdmissionMapper
    contract_calls = 0
    provider_close_calls = 0

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

    def _contract_with_one_close_route(exchange_name, profile_id, **kwargs):
        nonlocal contract_calls
        contract = original_contract(exchange_name, profile_id, **kwargs)
        if (
            exchange_name == dim01._EXCHANGE_SPOT_A_ALIAS
            and profile_id == dim01._PROFILE_SPOT_ROTATED
        ):
            contract_calls += 1
            if contract_calls != 1 or contract.supported_position_effects != ("OPEN",):
                raise AssertionError((contract_calls, contract))
            contract = replace(contract, supported_position_effects=("OPEN", "CLOSE"))
            control.send(
                (
                    "synthetic_close_contract",
                    exchange_name,
                    profile_id,
                    contract.supported_position_effects,
                )
            )
        return contract

    def _make_order_with_net_close_guard(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        *,
        client_order_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal provider_close_calls
        if symbol != dim01._BTC:
            raise AssertionError(("unexpected synthetic provider instrument", symbol))
        quantity = Decimal(str(volume))
        side = order_type.split("-", 1)[0].upper()
        offset = kwargs.get("offset")
        if side != "SELL" or offset != "close" or quantity <= 0:
            raise AssertionError((side, offset, quantity, order_type))
        with sqlite3.connect(self._ledger_path) as connection:
            row = connection.execute(
                f"SELECT quantity,revision FROM {_POSITION_TABLE} WHERE instrument=?",
                (symbol,),
            ).fetchone()
        if row is None:
            raise AssertionError(("synthetic position missing", symbol))
        before_quantity = Decimal(str(row[0]))
        revision = int(row[1])
        if quantity > before_quantity:
            raise AssertionError(("synthetic net position would flip", quantity, before_quantity))

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
        order = payload[0]
        if (
            order.get("client_order_id") != client_order_id
            or order.get("symbol") != symbol
            or order.get("side") != "sell"
            or order.get("status") != "filled"
            or Decimal(str(order.get("filled", "0"))) != quantity
        ):
            raise AssertionError(("synthetic provider fill differs from close request", order))
        after_quantity = before_quantity - quantity
        provider_close_calls += 1
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                f"UPDATE {_POSITION_TABLE} SET quantity=?,revision=revision+1 "
                "WHERE instrument=? AND quantity=? AND revision=?",
                (format(after_quantity, "f"), symbol, format(before_quantity, "f"), revision),
            )
            if updated.rowcount != 1:
                raise AssertionError("synthetic net position changed during close")
            connection.execute(
                f"INSERT INTO {_CLOSE_CALL_TABLE}(client_order_id,instrument,side,offset,"
                "order_type,quantity,before_quantity,after_quantity,provider_order_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    client_order_id,
                    symbol,
                    side,
                    offset,
                    order_type,
                    format(quantity, "f"),
                    format(before_quantity, "f"),
                    format(after_quantity, "f"),
                    str(order["order_id"]),
                ),
            )
        control.send(
            (
                "synthetic_close_written",
                client_order_id,
                str(order["order_id"]),
                format(quantity, "f"),
                format(after_quantity, "f"),
            )
        )
        return result

    dim01._provider_contract = _contract_with_one_close_route
    dim01._Dim01SyntheticV2Feed.make_order = _make_order_with_net_close_guard
    dim01.RiskOrderTarget = _target_with_complete_resources
    dim01.InstrumentRiskAdmissionMapper = _mapper_with_complete_resources
    try:
        dim04._dim04_host_process(control, *host_arguments)
        if contract_calls != 1 or provider_close_calls != expected_provider_close_calls:
            raise AssertionError(
                (contract_calls, provider_close_calls, expected_provider_close_calls)
            )
    finally:
        dim01._provider_contract = original_contract
        dim01._Dim01SyntheticV2Feed.make_order = original_make_order
        dim01.RiskOrderTarget = original_target_type
        dim01.InstrumentRiskAdmissionMapper = original_mapper_type


def _net_close_runtime_client(
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
                _, intent_id, side, position_effect, reduce_only = command
                result = runtime.call(
                    "broker.orders",
                    "orders.submit",
                    {
                        "intent_id": intent_id,
                        "signal_id": "signal-" + intent_id,
                        "instrument": dim01._BTC,
                        "side": side,
                        "position_effect": position_effect,
                        "order_type": "LIMIT",
                        "quantity": "0.75",
                        "price": "20",
                        "time_in_force": "GTC",
                        "reduce_only": reduce_only,
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
            raise AssertionError(("unknown net-close client command", command))
    except BaseException as exc:
        if runtime is not None:
            try:
                runtime.close()
            except BaseException:
                pass
        try:
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        except BaseException:
            pass
        raise
    finally:
        control.close()


def _runtime_result(record: dict[str, Any], *command: Any) -> dict[str, Any]:
    process = record["process"]
    assert process.is_alive(), f"{record['label']} exited before its request"
    record["pipe"].send(command)
    response = _receive(record["pipe"])
    assert response[0] != "error", response
    assert response[0] == "runtime_result", response
    value = response[1]
    assert type(value) is dict and frozenset(value) == {"ok", "code", "result"}, value
    assert type(value["result"]) is dict, value
    return value


def _read_rows(database_path: str, query: str, parameters: tuple[Any, ...] = ()) -> tuple[dict[str, Any], ...]:
    return dim04._read_rows(database_path, query, parameters)
