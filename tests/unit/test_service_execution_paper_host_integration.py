"""Paper orders through the normal Execution ServiceHost and CURVE client path."""

from __future__ import annotations

import importlib.abc
import multiprocessing
import socket
import sys
import time
from contextlib import suppress
from decimal import Decimal
from typing import Any

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)

_DOMAIN_REF = "paper-host-domain-v1"
_OTHER_DOMAIN_REF = "paper-host-other-domain-v1"
_SERVICE_ID = "paper-host-service"
_SERVICE_PRINCIPAL = "paper-host-service-principal"
_SERVICE_INSTANCE = "paper-host-owner"
_SERVICE_SCOPE_REF = "paper-host-service-scope-v1"
_SERVER_SECURITY_REF = "paper-host-server-curve-v1"
_CLIENT_SECURITY_REF = "paper-host-client-curve-v1"
_ACCOUNT_REF = "paper-host-simulation-account"
_STRATEGY_REF = "paper-host-strategy"
_STRATEGY_PRINCIPAL = "paper-host-strategy-principal"
_ORDER_SCOPE_REF = "paper-host-orders-scope-v1"
_INTENT_ID = "paper-host-buy-once"
_MAX_MESSAGE_BYTES = 65_536

_WIRE_SCOPE = WireScope(
    _DOMAIN_REF,
    account_ref=_ACCOUNT_REF,
    strategy_ref=_STRATEGY_REF,
)
_STRATEGY_IDENTITY = WireIdentity(
    WireRole.STRATEGY_CLIENT,
    _STRATEGY_PRINCIPAL,
    _STRATEGY_REF,
)


class _ForbiddenOptionalPackageImport(importlib.abc.MetaPathFinder):
    """Keep this Execution-only service path independent of Risk and Gateway."""

    _roots = frozenset({"bt_api_risk", "bt_api_gateway"})

    def __init__(self) -> None:
        self.attempts: list[str] = []

    def find_spec(self, fullname: str, path=None, target=None):
        root = fullname.partition(".")[0]
        if root in self._roots:
            self.attempts.append(root)
            raise AssertionError(f"unexpected optional-package import: {root}")
        return None


def _install_optional_package_guard() -> _ForbiddenOptionalPackageImport:
    guard = _ForbiddenOptionalPackageImport()
    sys.meta_path.insert(0, guard)
    return guard


def _optional_packages_absent(guard: _ForbiddenOptionalPackageImport) -> bool:
    return not guard.attempts and not any(
        name == root or name.startswith(f"{root}.")
        for root in guard._roots
        for name in sys.modules
    )


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": "paper-host-listener-v1",
        "registration_policy_ref": "paper-host-registration-v1",
        "service_requirements_ref": "paper-host-requirements-v1",
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": ["service.order_entry"],
                    "options": {"role": "account_owner"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "paper-host-listener-v1": {
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


def _client_config(endpoint: str):
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": _STRATEGY_REF},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "broker.orders",
                                "service_id": _SERVICE_ID,
                                "instance_id": _SERVICE_INSTANCE,
                                "scope_ref": _ORDER_SCOPE_REF,
                                "transport_ref": "paper-host-connect-v1",
                                "account_ref": _ACCOUNT_REF,
                            }
                        ],
                        "options": {"request_timeout_ms": 2_000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "paper-host-connect-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": _CLIENT_SECURITY_REF,
                        "options": {
                            "request_timeout_ms": 2_000,
                            "high_water_mark": 32,
                            "max_message_bytes": _MAX_MESSAGE_BYTES,
                        },
                    }
                },
            },
        }
    )


def _curve_keys() -> dict[str, bytes]:
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    return {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
        "strategy_secret": strategy_secret,
    }


def _authority(config, keys: dict[str, bytes], scope: WireScope) -> TrustedServiceAuthority:
    identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    grant = ResourceGrant(
        _SERVICE_INSTANCE,
        "service.order_entry",
        _ORDER_SCOPE_REF,
        scope,
        frozenset({"orders.submit", "orders.cancel"}),
    )
    return TrustedServiceAuthority(
        identity=identity,
        scope=WireScope(scope.execution_domain_ref),
        scope_ref=config.scope_ref,
        scope_version="paper-host-scope-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="paper-host-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="paper-host-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals={keys["strategy_public"]: _STRATEGY_PRINCIPAL},
        grants={
            _STRATEGY_PRINCIPAL: (
                PrincipalGrant(_STRATEGY_IDENTITY, (grant,)),
            )
        },
    )


def _paper_domain(execution_domain_ref: str = _DOMAIN_REF):
    from bt_api_execution import PAPER_MODEL_REF, PaperInstrumentV1, PaperOwnerDomainV1

    return PaperOwnerDomainV1(
        execution_domain_ref=execution_domain_ref,
        simulation_account_ref=_ACCOUNT_REF,
        experiment_ref="paper-host-experiment-v1",
        strategy_ref=_STRATEGY_REF,
        model_ref=PAPER_MODEL_REF,
        model_version="1",
        quote_source_ref="paper-host-synthetic-quotes-v1",
        cash_currency="USDT",
        initial_cash=Decimal("100"),
        initial_fees=Decimal("0"),
        fee_rate=Decimal("0.001"),
        fee_quantum=Decimal("0.0001"),
        max_quote_age_ns=30_000_000_000,
        instruments=(
            PaperInstrumentV1(
                "BTC-USDT",
                "paper-instrument-v1",
                "BTC",
                "USDT",
                Decimal("0.25"),
                Decimal("0.01"),
            ),
        ),
        opening_positions=(),
    )


class _SyntheticQuoteSource:
    """Server-owned deterministic quote leaf; never reads a live provider."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def read_quote(self, instrument: str):
        from bt_api_execution import PaperQuoteV1

        self.calls.append(instrument)
        if instrument != "BTC-USDT":
            return None
        return PaperQuoteV1(
            source_ref="paper-host-synthetic-quotes-v1",
            generation="paper-host-quote-generation-v1",
            cursor=1,
            instrument=instrument,
            metadata_version="paper-instrument-v1",
            quote_currency="USDT",
            bid=Decimal("10.00"),
            ask=Decimal("10.02"),
            bid_quantity=Decimal("5"),
            ask_quantity=Decimal("5"),
            as_of_ns=time.time_ns(),
        )


def _paper_resources(
    store,
    *,
    wire_scope: WireScope,
    execution_scope,
    close_calls: list[int],
    domain_ref: str = _DOMAIN_REF,
):
    from bt_api_execution import (
        PAPER_PROVIDER,
        ExecutionAdmissionValidatorRegistry,
        ExecutionBasicAdmissionGate,
        ExecutionProviderValidatorV1,
        OrderType,
        PaperMatchingPort,
        PositionEffect,
        Side,
        TimeInForce,
    )

    domain = _paper_domain(domain_ref)
    quote_source = _SyntheticQuoteSource()
    dispatch = PaperMatchingPort(domain, quote_source)
    validator = ExecutionProviderValidatorV1(
        provider_id=PAPER_PROVIDER,
        validator_id="paper-host-market-policy-v1",
        version="1",
        dispatch_port=dispatch,
        instrument_map={"BTC-USDT": "BTC-USDT"},
        quantity_steps={"BTC-USDT": Decimal("0.25")},
        max_quantities={"BTC-USDT": Decimal("10")},
        price_ticks={"BTC-USDT": Decimal("0.01")},
        supported_order_types=(OrderType.MARKET,),
        supported_time_in_force=(TimeInForce.FOK,),
        supported_actions=((Side.BUY, PositionEffect.OPEN),),
        supports_reduce_only=False,
    )
    admission = ExecutionBasicAdmissionGate(
        store,
        ExecutionAdmissionValidatorRegistry((validator,)),
        permit_ttl_ns=60_000_000_000,
    )

    def close_resources() -> None:
        store.close()
        close_calls[0] += 1

    resources = ComponentResources.owned(
        {
            "store": store,
            "targets": {wire_scope: execution_scope},
            "dispatch": dispatch,
            "admission": {"basic_admission_gate": admission},
            "paper_owner_domain": domain,
        },
        ready=lambda: True,
        close=close_resources,
    )
    return resources, quote_source, dispatch, domain


def _invalid_target_startup(
    tmp_path,
    *,
    label: str,
    wire_scope: WireScope,
    execution_scope,
) -> None:
    from bt_api_execution import SqliteExecutionStore

    endpoint = _endpoint()
    keys = _curve_keys()
    config = parse_service_config(_service_config(endpoint))
    store = SqliteExecutionStore(tmp_path / f"{label}-store.sqlite3")
    close_calls = [0]
    quote_sources: list[_SyntheticQuoteSource] = []

    def resource_provider(component, _instance_id, _options, _service_scope):
        assert component == "execution"
        resources, quote_source, dispatch, domain = _paper_resources(
            store,
            wire_scope=wire_scope,
            execution_scope=execution_scope,
            close_calls=close_calls,
        )
        assert dispatch.owner_domain is domain
        assert set(resources.values) == {
            "store",
            "targets",
            "dispatch",
            "admission",
            "paper_owner_domain",
        }
        quote_sources.append(quote_source)
        return resources

    host = ServiceHost(
        config,
        _authority(config, keys, wire_scope),
        str(tmp_path / f"{label}-registry.sqlite3"),
        resource_provider,
    )
    error = None
    try:
        host.start()
    except ServiceHostError as caught:
        error = caught
    finally:
        if host.state == "started":
            host.close()
        if close_calls[0] == 0:
            store.close()
            close_calls[0] += 1
    assert error is not None and error.code == "START_FAILED"
    assert host.state == "failed"
    assert host.summary["cleanup_failures"] == ()
    assert close_calls == [1]
    assert len(quote_sources) == 1
    assert quote_sources[0].calls == []


def _plain_host_components(host: ServiceHost) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            row["component"],
            row["instance_id"],
            tuple(row["declared_ports"]),
            tuple(
                (capability["port"], tuple(capability["operations"]))
                for capability in row["capabilities"]
            ),
        )
        for row in host.summary["components"]
    )


def _paper_host_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    store_path: str,
    keys: dict[str, bytes],
) -> None:
    guard = _install_optional_package_guard()
    from bt_api_execution import ExecutionScope, SqliteExecutionStore

    config = parse_service_config(raw_config)
    store = SqliteExecutionStore(store_path)
    close_calls = [0]
    quote_sources: list[_SyntheticQuoteSource] = []
    wire_scope = _WIRE_SCOPE
    execution_scope = ExecutionScope(
        "paper",
        "paper",
        _ACCOUNT_REF,
        _STRATEGY_REF,
        "paper-host-trading-day-v1",
    )
    host = None

    def resource_provider(component, _instance_id, _options, _service_scope):
        assert component == "execution"
        resources, quote_source, dispatch, domain = _paper_resources(
            store,
            wire_scope=wire_scope,
            execution_scope=execution_scope,
            close_calls=close_calls,
        )
        assert dispatch.owner_domain is domain
        assert set(resources.values) == {
            "store",
            "targets",
            "dispatch",
            "admission",
            "paper_owner_domain",
        }
        quote_sources.append(quote_source)
        return resources

    def metrics() -> dict[str, Any]:
        record = store.get(_INTENT_ID, scope=execution_scope)
        receipt = store.get_paper_match_receipt(_INTENT_ID, scope=execution_scope)
        book = store.read_paper_book(execution_scope)
        events = store.read_outbox(scope=execution_scope, after_sequence=0, limit=100)
        provider_events = tuple(event for event in events if event.event_type == "provider_observation")
        if record is None or receipt is None or len(quote_sources) != 1:
            raise AssertionError("paper order evidence is incomplete")
        return {
            "record_state": record.state.value,
            "dispatch_attempts": record.dispatch_attempts,
            "filled_quantity": str(record.filled_quantity),
            "average_price": None if record.average_price is None else str(record.average_price),
            "cumulative_commission": (
                None
                if record.cumulative_commission is None
                else str(record.cumulative_commission)
            ),
            "receipt_state": receipt.state.value,
            "receipt_domain": receipt.execution_domain_ref,
            "receipt_account": receipt.simulation_account_ref,
            "receipt_experiment": receipt.experiment_ref,
            "receipt_model": (receipt.model_ref, receipt.model_version),
            "receipt_quote": (
                receipt.quote.source_ref,
                receipt.quote.generation,
                receipt.quote.cursor,
                receipt.quote.as_of_ns,
            ),
            "receipt_price": str(receipt.fill_price),
            "receipt_fee": str(receipt.fee_amount),
            "receipt_cash_delta": str(receipt.cash_delta),
            "receipt_position_delta": str(receipt.position_delta),
            "receipt_event": (receipt.event_id, receipt.event_sequence),
            "book_cash": str(book.cash),
            "book_currency": book.cash_currency,
            "book_fees": str(book.cumulative_fees),
            "book_positions": tuple(
                (row.instrument, str(row.quantity), str(row.average_cost))
                for row in book.positions
            ),
            "book_receipts": book.receipt_count,
            "book_cursor": book.execution_cursor,
            "book_generation": book.execution_journal_generation,
            "quote_calls": tuple(quote_sources[0].calls),
            "provider_event_count": len(provider_events),
            "provider_event_matches_receipt": (
                len(provider_events) == 1
                and provider_events[0].event_id == receipt.event_id
                and provider_events[0].sequence == receipt.event_sequence
            ),
        }

    try:
        host = ServiceHost(
            config,
            _authority(config, keys, wire_scope),
            registry_path,
            resource_provider,
        )
        try:
            host.start()
        except ServiceHostError as error:
            if _optional_packages_absent(guard):
                control.send(
                    (
                        "startup_failed",
                        error.code,
                        host.state,
                        tuple(host.summary["cleanup_failures"]),
                        close_calls[0],
                        tuple(quote_sources[0].calls) if quote_sources else (),
                    )
                )
                return
            raise AssertionError("Risk or Gateway import was attempted") from None
        if not _optional_packages_absent(guard):
            raise AssertionError("Risk or Gateway package was imported")
        control.send(
            (
                "ready",
                host.endpoint,
                host.state,
                _plain_host_components(host),
                _optional_packages_absent(guard),
            )
        )
        while True:
            if control.poll():
                command = control.recv()
                if command == ("metrics",):
                    control.send(("metrics", metrics()))
                    continue
                if command == ("close",):
                    host.close()
                    control.send(
                        (
                            "closed",
                            host.state,
                            tuple(host.summary["cleanup_failures"]),
                            close_calls[0],
                            tuple(quote_sources[0].calls),
                            _optional_packages_absent(guard),
                        )
                    )
                    return
                raise AssertionError("unknown paper Host command")
            host.serve_once(timeout_ms=20)
    except BaseException as error:
        if host is not None and host.state == "started":
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(
                (
                    "host_error",
                    getattr(error, "code", type(error).__name__),
                    close_calls[0],
                    tuple(quote_sources[0].calls) if quote_sources else (),
                )
            )
        raise
    finally:
        if close_calls[0] == 0:
            with suppress(Exception):
                store.close()
            close_calls[0] += 1
        control.close()


def _plain_order_record(record) -> tuple[object, ...]:
    return (
        record.intent_id,
        record.state,
        record.provider_order_id,
        str(record.filled_quantity),
        None if record.average_price is None else str(record.average_price),
        None if record.cumulative_commission is None else str(record.cumulative_commission),
        record.dispatch_attempts,
        record.is_terminal,
    )


def _paper_strategy_process(
    control,
    endpoint: str,
    keys: dict[str, bytes],
) -> None:
    guard = _install_optional_package_guard()
    from bt_api_py.service_execution import ServiceExecutionClient

    runtime = None
    try:
        config = _client_config(endpoint)
        peer = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        runtime = ServiceClientRuntime(
            config,
            identity=_STRATEGY_IDENTITY,
            scope_resolver=lambda _scope_ref: _WIRE_SCOPE,
            peer_resolver=lambda _service_id: peer,
            transport_factory=ZmqTransportFactory(
                {
                    _CLIENT_SECURITY_REF: ClientCurveKeys(
                        keys["server_public"],
                        keys["strategy_public"],
                        keys["strategy_secret"],
                    )
                }
            ),
            requested_operations={"broker.orders": frozenset({"orders.submit"})},
        )
        startup = runtime.start()
        execution = ServiceExecutionClient(
            runtime,
            "broker.orders",
            scope_ref=_ORDER_SCOPE_REF,
        )
        if not _optional_packages_absent(guard):
            raise AssertionError("Risk or Gateway package was imported by the client")
        control.send(("ready", runtime.state, startup.degraded_count))
        while True:
            command = control.recv()
            if command == ("close",):
                runtime.close()
                control.send(
                    (
                        "closed",
                        runtime.state,
                        runtime.cleanup_failures,
                        _optional_packages_absent(guard),
                    )
                )
                return
            if command == ("submit_twice",):
                arguments = {
                    "intent_id": _INTENT_ID,
                    "signal_id": "paper-host-signal-v1",
                    "instrument": "BTC-USDT",
                    "side": "BUY",
                    "position_effect": "OPEN",
                    "order_type": "MARKET",
                    "quantity": "1.25",
                    "price": None,
                    "time_in_force": "FOK",
                    "reduce_only": False,
                    "metadata_version": "paper-instrument-v1",
                }
                first = execution.submit(**arguments)
                replay = execution.submit(**arguments)
                control.send(
                    (
                        "submitted",
                        _plain_order_record(first),
                        _plain_order_record(replay),
                        first == replay,
                        _optional_packages_absent(guard),
                    )
                )
                continue
            raise AssertionError("unknown paper strategy command")
    except BaseException as error:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("client_error", getattr(error, "code", type(error).__name__)))
        raise
    finally:
        control.close()


def test_e_only_paper_market_submit_uses_trusted_domain_and_quote_over_curve(tmp_path) -> None:
    from bt_api_execution import ExecutionScope, SqliteExecutionStore

    from tests.unit._spawned_process_helpers import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _client_command,
        _close_normally,
        _receive,
        _start_process,
    )

    # A mismatched trusted domain and a live target are rejected during real
    # Host composition; neither may reach the paper quote leaf or any provider.
    _invalid_target_startup(
        tmp_path,
        label="wrong-paper-domain",
        wire_scope=WireScope(
            _OTHER_DOMAIN_REF,
            account_ref=_ACCOUNT_REF,
            strategy_ref=_STRATEGY_REF,
        ),
        execution_scope=ExecutionScope(
            "paper",
            "paper",
            _ACCOUNT_REF,
            _STRATEGY_REF,
            "paper-host-trading-day-v1",
        ),
    )
    _invalid_target_startup(
        tmp_path,
        label="paper-live-target",
        wire_scope=_WIRE_SCOPE,
        execution_scope=ExecutionScope(
            "exchange-live",
            "live",
            _ACCOUNT_REF,
            _STRATEGY_REF,
            "paper-host-trading-day-v1",
        ),
    )

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    keys = _curve_keys()
    raw_config = _service_config(endpoint)
    assert set(raw_config["components"]) == {"execution"}
    assert raw_config["components"]["execution"]["settings"]["options"] == {
        "role": "account_owner"
    }
    registry_path = str(tmp_path / "paper-host-registry.sqlite3")
    store_path = str(tmp_path / "paper-host-execution.sqlite3")
    records = []
    forced: list[str] = []
    failures: list[str] = []
    try:
        host_record = _start_process(
            context,
            _paper_host_process,
            (raw_config, registry_path, store_path, keys),
            "paper-host-owner",
        )
        records.append(host_record)
        host_ready = _receive(host_record["pipe"])
        assert host_ready[0] == "ready", host_ready
        assert host_ready[1] == endpoint
        assert host_ready[2] == "started"
        assert host_ready[3] == (
            (
                "execution",
                _SERVICE_INSTANCE,
                ("service.order_entry",),
                (("service.order_entry", ("orders.cancel", "orders.submit")),),
            ),
        )
        assert host_ready[4] is True

        strategy_record = _start_process(
            context,
            _paper_strategy_process,
            (endpoint, keys),
            "paper-host-strategy-client",
        )
        records.append(strategy_record)
        strategy_ready = _receive(strategy_record["pipe"])
        assert strategy_ready == ("ready", "registered", 0)

        strategy_record["pipe"].send(("submit_twice",))
        submitted = _receive(strategy_record["pipe"])
        assert submitted[0] == "submitted", submitted
        first, replay = submitted[1], submitted[2]
        assert first == replay
        assert submitted[3] is True
        assert submitted[4] is True
        assert first == (
            _INTENT_ID,
            "FILLED",
            first[2],
            "1.25",
            "10.02",
            "0.0125",
            1,
            True,
        )
        assert type(first[2]) is str and first[2]

        host_metrics = _client_command(host_record, "metrics")
        assert host_metrics[0] == "metrics"
        metrics = host_metrics[1]
        assert metrics["record_state"] == "FILLED"
        assert metrics["dispatch_attempts"] == 1
        assert metrics["filled_quantity"] == "1.25"
        assert metrics["average_price"] == "10.02"
        assert metrics["cumulative_commission"] == "0.0125"
        assert metrics["receipt_state"] == "FILLED"
        assert metrics["receipt_domain"] == _DOMAIN_REF
        assert metrics["receipt_account"] == _ACCOUNT_REF
        assert metrics["receipt_experiment"] == "paper-host-experiment-v1"
        assert metrics["receipt_model"] == ("spot_market_top_of_book", "1")
        assert metrics["receipt_quote"][:3] == (
            "paper-host-synthetic-quotes-v1",
            "paper-host-quote-generation-v1",
            1,
        )
        assert metrics["receipt_price"] == "10.02"
        assert metrics["receipt_fee"] == "0.0125"
        assert metrics["receipt_cash_delta"] == "-12.5375"
        assert metrics["receipt_position_delta"] == "1.25"
        assert metrics["book_cash"] == "87.4625"
        assert metrics["book_currency"] == "USDT"
        assert metrics["book_fees"] == "0.0125"
        assert metrics["book_positions"] == (("BTC-USDT", "1.25", "10.02"),)
        assert metrics["book_receipts"] == 1
        assert metrics["book_cursor"] > 0
        assert type(metrics["book_generation"]) is str and metrics["book_generation"]
        assert metrics["quote_calls"] == ("BTC-USDT",)
        assert metrics["provider_event_count"] == 1
        assert metrics["provider_event_matches_receipt"] is True

        client_closed = _close_normally(strategy_record, expected_tag="closed")
        assert client_closed == ("closed", "closed", 0, True)
        host_closed = _close_normally(host_record, expected_tag="closed")
        assert host_closed == (
            "closed",
            "closed",
            (),
            1,
            ("BTC-USDT",),
            True,
        )

        reopened = SqliteExecutionStore(store_path)
        try:
            execution_scope = ExecutionScope(
                "paper",
                "paper",
                _ACCOUNT_REF,
                _STRATEGY_REF,
                "paper-host-trading-day-v1",
            )
            receipt = reopened.get_paper_match_receipt(_INTENT_ID, scope=execution_scope)
            book = reopened.read_paper_book(execution_scope)
            assert receipt is not None and receipt.state.value == "FILLED"
            assert receipt.execution_domain_ref == _DOMAIN_REF
            assert receipt.experiment_ref == "paper-host-experiment-v1"
            assert receipt.event_sequence == metrics["receipt_event"][1]
            assert book.cash == Decimal("87.4625")
            assert book.cumulative_fees == Decimal("0.0125")
            assert book.receipt_count == 1
            assert tuple(
                (item.instrument, item.quantity, item.average_cost)
                for item in book.positions
            ) == (("BTC-USDT", Decimal("1.25"), Decimal("10.02")),)
        finally:
            reopened.close()
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
