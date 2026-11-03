"""Iteration 60 R2-E: the execution account owner writes and serves the strategy ledger.

``ExecutionAccountOwnerService`` with a ``ServiceStrategyLedgerBinding``:
strategy submits are reserved in the account's ``SqliteStrategyLedger`` before
the execution journal sees them; fills, fees and terminal states are projected
from the journal's own events, exactly once (journal sequence written in the
ledger transaction); strategy books are served from one ledger transaction
with subscription-fed position evidence. Restarts are real spawned processes
that die with ``os._exit`` at each crash window. Evidence: UNIT/FAKE and
INTEGRATION_SIM (temp SQLite, synthetic provider; no network, no account).
"""

from __future__ import annotations

import multiprocessing
import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("bt_api_execution")

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope  # noqa: E402
from bt_api_execution import (  # noqa: E402
    CancelObservation,
    ExecutionScope,
    ExecutionState,
    ProviderObservation,
    SqliteExecutionStore,
)

from bt_api_py.account_position_stream import StreamedAccountPositions  # noqa: E402
from bt_api_py.service_command_wire import (  # noqa: E402
    build_cancel_command,
    build_submit_command,
    command_arguments,
)
from bt_api_py.service_execution import ServiceExecutionClient  # noqa: E402
from bt_api_py.service_config import ServiceConfigError, parse_service_config  # noqa: E402
from bt_api_py.service_execution_account_books import (  # noqa: E402
    ExecutionAccountOwnerService,
    validate_service_options,
)
from bt_api_py.service_strategy_ledger_host import (  # noqa: E402
    ServiceStrategyLedgerBinding,
    ServiceStrategyLedgerError,
    parse_strategy_ledger_option,
)
from bt_api_py.strategy_ledger import (  # noqa: E402
    InstrumentTerms,
    LedgerIntent,
    SqliteStrategyLedger,
    StrategyBudget,
    StrategyLedgerError,
)
from tests.unit._typed_command_bindings import (  # noqa: E402
    client_binding,
    declaration,
    host_bindings,
)

DOMAIN = "sim:r2e-ledger"
ACCOUNT = "acct-r2e"
STRATEGY = "strategy-r2e"
OTHER = "strategy-r2e-other"
PRINCIPAL = "strategy-r2e-principal"
WIRE = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=STRATEGY)
OTHER_WIRE = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=OTHER)
IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, PRINCIPAL, STRATEGY)
OTHER_IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, "other-principal", OTHER)
INSTRUMENT = "BTC-USDT"
SERVICE_ID = "r2e-execution"
INSTANCE_ID = "r2e-owner"
RUN_ID = "r2e-run-1"
_DECLARATION = declaration(
    provider="sim",
    venue="sim",
    environment="simulation",
    account_id=ACCOUNT,
    instruments=(INSTRUMENT,),
    price_currency="USDT",
)


def _client_binding():
    return client_binding(_DECLARATION, service_id=SERVICE_ID, instance_id=INSTANCE_ID)


def _run(scope: WireScope) -> WireScope:
    """Typed commands carry a run; the host resolves it to the run-free template."""

    return scope if scope.run_id is not None else replace(scope, run_id=RUN_ID)


def _targets() -> dict[WireScope, ExecutionScope]:
    return {
        WIRE: ExecutionScope("SIM", "simulation", ACCOUNT, STRATEGY, "20261104"),
        OTHER_WIRE: ExecutionScope("SIM", "simulation", ACCOUNT, OTHER, "20261104"),
    }


def _option(directory: Path, **changes: Any) -> dict[str, Any]:
    option: dict[str, Any] = {
        "path": str(directory / "strategy-ledger.sqlite3"),
        "instruments": {INSTRUMENT: {"multiplier": "1", "quote_currency": "USDT"}},
        "budgets": {
            STRATEGY: {"limit": "200000", "currency": "USDT"},
            OTHER: {"limit": "200000", "currency": "USDT"},
        },
        "capital": {STRATEGY: {"amount": "200000", "currency": "USDT"}},
    }
    option.update(changes)
    return option


class _Provider:
    """Synthetic dispatch port: accepts every order; counts provider calls."""

    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit(self, intent: Any) -> Any:
        self.submitted.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, "provider-" + intent.intent_id)

    def cancel(self, intent: Any) -> Any:
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


class _Host:
    def __init__(self, directory: Path, *, stream: StreamedAccountPositions | None = None):
        self.directory = directory
        self.store = SqliteExecutionStore(str(directory / "execution.sqlite3"))
        self.stream = stream or StreamedAccountPositions(ACCOUNT, "sim:position-stream")
        if stream is None:
            self.stream.apply_snapshot({})
        self.provider = _Provider()
        self.binding = ServiceStrategyLedgerBinding(
            _option(directory),
            store=self.store,
            targets=_targets(),
            account_positions=self.stream,
        )
        # Typed orders.submit/orders.cancel (iteration 60 R3 fixture migration):
        # host and client bindings come from one deployment declaration.
        self.service = ExecutionAccountOwnerService(
            self.store,
            _targets(),
            self.provider,
            allow_unprotected=True,
            strategy_ledger=self.binding,
            command_bindings=host_bindings(
                _targets(), [_DECLARATION], service_id=SERVICE_ID, instance_id=INSTANCE_ID
            ),
        )
        self.service.recover_interrupted_dispatches()

    @property
    def ledger(self) -> SqliteStrategyLedger:
        return self.binding.ledger

    def submit(self, intent_id: str, *, scope=WIRE, identity=IDENTITY, **changes: Any):
        arguments: dict[str, Any] = dict(
            intent_id=intent_id,
            signal_id="signal-" + intent_id,
            instrument=INSTRUMENT,
            side="BUY",
            position_effect="OPEN",
            order_type="LIMIT",
            quantity="2",
            price="50000",
            time_in_force="GTC",
            reduce_only=False,
            metadata_version="r2e-metadata-v1",
            deadline_ns=None,
            tags={},
        )
        arguments.update(changes)
        run_scope = _run(scope)
        command = build_submit_command(
            _client_binding(), ServiceExecutionClient.build_submit_payload(**arguments),
            scope=run_scope,
        )
        return self.service.handle(
            "orders.submit",
            principal_ref=identity.principal_ref,
            identity=identity,
            scope=run_scope,
            arguments=command_arguments(command),
        )

    def cancel(self, cancel_id: str, target_intent_id: str, *, scope=WIRE, identity=IDENTITY):
        run_scope = _run(scope)
        payload = ServiceExecutionClient.build_cancel_payload(
            cancel_id=cancel_id,
            target_intent_id=target_intent_id,
            provider_order_id="provider-" + target_intent_id,
            metadata_version="r2e-metadata-v1",
            tags={},
        )
        command = build_cancel_command(
            _client_binding(), payload, instrument=INSTRUMENT, scope=run_scope
        )
        return self.service.handle(
            "orders.cancel",
            principal_ref=identity.principal_ref,
            identity=identity,
            scope=run_scope,
            arguments=command_arguments(command),
        )

    def observe(self, intent_id: str, state: str, filled: str, *, fee=None, currency="USDT"):
        observation = ProviderObservation(
            intent_id=intent_id,
            state=ExecutionState(state),
            provider_order_id="provider-" + intent_id,
            filled_quantity=Decimal(filled),
            average_price=None if filled == "0" else Decimal("50000"),
            cumulative_commission=None if fee is None else Decimal(fee),
            cumulative_commission_currency=None if fee is None else currency,
        )
        return self.binding.record_provider_observation(WIRE, observation)

    def book(self):
        return self.service.handle(
            "execution.account_books.query",
            principal_ref=PRINCIPAL,
            identity=IDENTITY,
            scope=WIRE,
            arguments={},
        )

    def close(self) -> None:
        self.service.close()
        self.store.close()


@pytest.fixture
def host(tmp_path):
    item = _Host(tmp_path)
    try:
        yield item
    finally:
        item.close()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_option_requires_an_explicit_absolute_path_and_decimal_strings(tmp_path):
    parsed = parse_strategy_ledger_option(_option(tmp_path))
    assert parsed["path"] == str(tmp_path / "strategy-ledger.sqlite3")
    assert parsed["instruments"][INSTRUMENT]["product_model"] == "spot"
    for broken, code in (
        ({k: v for k, v in _option(tmp_path).items() if k != "path"}, "OPTION_INVALID"),
        (_option(tmp_path, path="relative/ledger.sqlite3"), "PATH_NOT_ABSOLUTE"),
        (_option(tmp_path, budgets={STRATEGY: {"limit": 5.0, "currency": "USDT"}}), "INVALID"),
        (_option(tmp_path, unknown=True), "OPTION_INVALID"),
        (_option(tmp_path, reconciliation_max_age_seconds=0), "OPTION_INVALID"),
        (
            _option(tmp_path, instruments={INSTRUMENT: {"multiplier": "1"}}),
            "OPTION_INVALID",
        ),
    ):
        with pytest.raises(ServiceStrategyLedgerError) as error:
            parse_strategy_ledger_option(broken)
        assert error.value.code.endswith(code)


def test_service_config_and_component_validator_share_the_parser(tmp_path):
    def config(options):
        return {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": "r2e-service",
            "scope_ref": "r2e-scope-v1",
            "transport_ref": "r2e-listener-v1",
            "registration_policy_ref": "r2e-registration-v1",
            "service_requirements_ref": "r2e-requirements-v1",
            "components": {
                "execution": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": "r2e-owner",
                        "bind_ports": ["service.order_entry", "service.order_query"],
                        "options": options,
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "r2e-listener-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": "tcp://127.0.0.1:1"},
                        "security_profile_ref": "r2e-server-v1",
                    }
                },
            },
        }

    parsed = parse_service_config(
        config({"role": "account_owner", "strategy_ledger": _option(tmp_path)})
    )
    options = parsed.components["execution"]["settings"]["options"]
    assert options["strategy_ledger"]["path"] == str(tmp_path / "strategy-ledger.sqlite3")
    with pytest.raises(ServiceConfigError) as error:
        parse_service_config(
            config({"role": "account_owner", "strategy_ledger": _option(tmp_path, path="x")})
        )
    assert error.value.code == "STRATEGY_LEDGER_PATH_NOT_ABSOLUTE"

    validated = validate_service_options(
        {"role": "account_owner", "strategy_ledger": _option(tmp_path)},
        ("service.order_entry", "service.order_query"),
    )
    assert validated["strategy_ledger"]["budgets"][STRATEGY]["limit"] == "200000"
    with pytest.raises(ValueError):
        validate_service_options(
            {"role": "account_owner", "strategy_ledger": _option(tmp_path)},
            ("service.order_query",),
        )


def test_binding_accepts_only_subscription_fed_position_evidence(tmp_path):
    store = SqliteExecutionStore(str(tmp_path / "execution.sqlite3"))
    try:
        for evidence, code in (
            (lambda: None, "STRATEGY_LEDGER_POSITIONS_INVALID"),
            (
                StreamedAccountPositions("another-account", "sim:position-stream"),
                "STRATEGY_LEDGER_POSITIONS_ACCOUNT_MISMATCH",
            ),
        ):
            with pytest.raises(ServiceStrategyLedgerError) as error:
                ServiceStrategyLedgerBinding(
                    _option(tmp_path), store=store, targets=_targets(), account_positions=evidence
                )
            assert error.value.code == code
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Admission and projection
# ---------------------------------------------------------------------------


def test_admission_refusals_never_reach_the_journal_or_provider(host):
    # Budget 200000 USDT: 5 x 50000 exceeds it.
    refused = host.submit("too-big", quantity="5")
    assert (refused["ok"], refused["code"]) == (False, "BUDGET_EXCEEDED")
    # A SELL without a strategy position opens a short: not modelled for a
    # strategy with its own spot cash.
    refused = host.submit("bad-close", side="SELL", position_effect="CLOSE", reduce_only=True)
    assert (refused["ok"], refused["code"]) == (False, "STRATEGY_CAPITAL_MODEL_UNSUPPORTED")
    # Without a strategy position the ledger's effect is OPEN; a declared
    # CLOSE contradicts it and is refused.
    mismatch = host.submit("declared-close", position_effect="CLOSE", reduce_only=True)
    assert (mismatch["ok"], mismatch["code"]) == (False, "LEDGER_POSITION_EFFECT_MISMATCH")
    # The mismatched intent was released and its key cannot be reused.
    assert host.ledger.intent_state("declared-close") == "REJECTED"
    again = host.submit("declared-close")
    assert again["code"] == "LEDGER_INTENT_FINISHED"
    assert host.provider.submitted == []
    assert host.store.get("too-big", scope=_targets()[WIRE]) is None


def test_fills_fees_and_terminal_states_are_projected_from_the_journal(host):
    first = host.submit("buy-1")
    assert first["ok"] is True and first["result"]["record"]["state"] == "ACKED"
    assert host.ledger.intent_state("buy-1") == "SUBMITTED"

    host.observe("buy-1", "PARTIALLY_FILLED", "1")
    assert host.ledger.positions(STRATEGY) == {INSTRUMENT: Decimal("1")}
    # The fee arrives later for the same quantity (journal fee-only evidence).
    host.observe("buy-1", "PARTIALLY_FILLED", "1", fee="1")
    # Redelivery of the same evidence: the journal appends nothing, the
    # ledger is unchanged.
    host.observe("buy-1", "PARTIALLY_FILLED", "1", fee="1")
    _state, snapshot = host.ledger.reconciled_snapshot(STRATEGY, host.stream.report())
    assert snapshot.positions == {INSTRUMENT: Decimal("1")}
    assert snapshot.fees_since_baseline == {"USDT": Decimal("1")}
    host.observe("buy-1", "FILLED", "2", fee="2")
    assert host.ledger.intent_state("buy-1") == "FILLED"
    _state, snapshot = host.ledger.reconciled_snapshot(STRATEGY, host.stream.report())
    assert snapshot.positions == {INSTRUMENT: Decimal("2")}
    assert snapshot.fees_since_baseline == {"USDT": Decimal("2")}

    # Close capacity: the strategy's own position bounds its closes.
    over = host.submit(
        "sell-3", side="SELL", position_effect="CLOSE", reduce_only=True, quantity="3"
    )
    assert over["code"] == "POSITION_FLIP_REFUSED"
    close = host.submit(
        "sell-2", side="SELL", position_effect="CLOSE", reduce_only=True, quantity="2", price="1"
    )
    assert close["ok"] is True
    pending = host.submit(
        "sell-1", side="SELL", position_effect="CLOSE", reduce_only=True, quantity="1", price="1"
    )
    assert pending["code"] == "CLOSE_CAPACITY_EXCEEDED"

    # Cancel releases the remaining close capacity; a partial fill stays.
    host.observe("sell-2", "PARTIALLY_FILLED", "1")
    cancel = host.cancel("cancel-sell-2", "sell-2")
    assert cancel["ok"] is True, cancel
    assert host.ledger.positions(STRATEGY) == {INSTRUMENT: Decimal("1")}
    host.observe("sell-2", "CANCELLED", "1")
    assert host.ledger.intent_state("sell-2") == "CANCELLED"
    assert host.ledger.positions(STRATEGY) == {INSTRUMENT: Decimal("1")}
    reuse = host.submit(
        "sell-1b", side="SELL", position_effect="CLOSE", reduce_only=True, quantity="1", price="1"
    )
    assert reuse["ok"] is True


def test_strategy_book_query_reconciles_against_the_subscription(host):
    host.submit("buy-1")
    host.observe("buy-1", "FILLED", "2", fee="2")

    # The subscription has not shown the fill yet: IN_FLIGHT, no financials.
    reply = host.book()
    assert reply["ok"] is True
    assert reply["result"]["reconciliation"]["state"] == "IN_FLIGHT"
    assert reply["result"]["financial"]["available"] is False

    host.stream.apply_position(INSTRUMENT, "net", Decimal("2"))
    reply = host.book()
    assert reply["result"]["reconciliation"]["state"] == "BALANCED"
    assert reply["result"]["source"] == "STRATEGY_LEDGER"

    host.stream.apply_position(INSTRUMENT, "net", Decimal("7"))  # foreign activity
    reply = host.book()
    assert reply["result"]["strategy_account_book"]["error_code"] == (
        "STRATEGY_BOOK_RECONCILIATION_RESIDUAL"
    )
    host.stream.mark_interrupted("DISCONNECTED")
    reply = host.book()
    assert reply["result"]["strategy_account_book"]["error_code"] == (
        "STRATEGY_BOOK_RECONCILIATION_UNVERIFIED"
    )


def test_unprojectable_journal_event_blocks_admission_and_reads(host):
    host.submit("buy-1")
    # Fee without a currency cannot be booked: the projection stops.
    host.observe("buy-1", "FILLED", "2", fee="2", currency=None)
    assert host.binding.blocked == "LEDGER_FEE_CURRENCY_UNKNOWN"
    refused = host.submit("buy-2")
    assert refused["code"] == "STRATEGY_LEDGER_PROJECTION_BLOCKED"
    assert host.book()["code"] == "UNAVAILABLE"
    assert host.provider.submitted == ["buy-1"]


def test_strategies_share_one_account_ledger_without_crossing(host):
    host.submit("buy-a")
    host.observe("buy-a", "FILLED", "2")
    other = host.submit("buy-b", scope=OTHER_WIRE, identity=OTHER_IDENTITY, quantity="1")
    assert other["ok"] is True
    # OTHER cannot close STRATEGY's position.
    steal = host.submit(
        "steal",
        scope=OTHER_WIRE,
        identity=OTHER_IDENTITY,
        side="SELL",
        position_effect="CLOSE",
        reduce_only=True,
        quantity="1",
    )
    assert steal["code"] == "LEDGER_POSITION_EFFECT_MISMATCH"
    assert host.ledger.positions(OTHER) == {}
    assert host.ledger.positions(STRATEGY) == {INSTRUMENT: Decimal("2")}


# ---------------------------------------------------------------------------
# Ledger API used by the projection
# ---------------------------------------------------------------------------


def test_apply_journal_event_is_exactly_once_and_atomic(tmp_path):
    ledger = SqliteStrategyLedger(
        tmp_path / "ledger.sqlite3",
        account_ref=ACCOUNT,
        instruments={INSTRUMENT: InstrumentTerms(Decimal("1"), "USDT")},
        budgets={STRATEGY: StrategyBudget(Decimal("1000000"), "USDT")},
    )
    try:
        ledger.reserve(LedgerIntent("i", STRATEGY, INSTRUMENT, "BUY", Decimal("2"), Decimal("1")))
        assert ledger.apply_journal_event("journal:a", 3, intent_id="i", step="SUBMITTED")
        assert not ledger.apply_journal_event("journal:a", 3, intent_id="i", step="SUBMITTED")
        assert ledger.apply_journal_event(
            "journal:a",
            5,
            intent_id="i",
            step="EXECUTION",
            cumulative_quantity="1",
            average_price="1",
            fee="0.1",
            fee_currency="USDT",
        )
        # A FINISH whose final state contradicts the fill raises and applies
        # nothing: neither the fill nor the cursor moves.
        with pytest.raises(StrategyLedgerError):
            ledger.apply_journal_event(
                "journal:a",
                7,
                intent_id="i",
                step="FINISH",
                final_state="CANCELLED",
                cumulative_quantity="2",
                average_price="1",
            )
        assert ledger.journal_cursor("journal:a") == 5
        assert ledger.positions(STRATEGY) == {INSTRUMENT: Decimal("1")}
        assert ledger.intent_state("i") == "PARTIALLY_FILLED"
        with pytest.raises(StrategyLedgerError) as error:
            ledger.journal_cursor("journal:b")
        assert error.value.code == "LEDGER_JOURNAL_SOURCE_MISMATCH"
        with pytest.raises(StrategyLedgerError) as error:
            ledger.apply_journal_event("journal:a", 9, intent_id="ghost", step="SUBMITTED")
        assert error.value.code == "INTENT_NOT_FOUND"
        assert ledger.reserved_intents() == ()
    finally:
        ledger.close()


# ---------------------------------------------------------------------------
# Crash windows (real spawned processes, os._exit)
# ---------------------------------------------------------------------------


def _crash_child(directory: str, window: str) -> None:
    from bt_api_execution.service_order_entry import ExecutionOrderEntryService as Entry

    directory_path = Path(directory)
    if window == "after_reserve":

        def die(*_args: Any, **_kwargs: Any) -> Any:
            os._exit(0)

        Entry.handle = die  # type: ignore[method-assign]
    host = _Host(directory_path)
    if window == "after_reserve":
        host.submit("crash-1")
        raise AssertionError("unreachable")
    host.submit("crash-1")
    original = SqliteStrategyLedger.apply_journal_event
    original_finish = SqliteStrategyLedger.finish

    if window == "after_journal_commit":

        def stop(self, source, sequence, **kwargs):
            if kwargs.get("step") == "FINISH":
                os._exit(0)  # the journal committed FILLED; the ledger has not
            return original(self, source, sequence, **kwargs)

        SqliteStrategyLedger.apply_journal_event = stop  # type: ignore[method-assign]
    elif window == "inside_ledger_transaction":

        def stop_finish(self, intent_id, state):
            os._exit(0)  # apply_execution ran in this transaction; never committed

        SqliteStrategyLedger.finish = stop_finish  # type: ignore[method-assign]
    host.observe("crash-1", "FILLED", "2", fee="2")
    SqliteStrategyLedger.apply_journal_event = original  # type: ignore[method-assign]
    SqliteStrategyLedger.finish = original_finish  # type: ignore[method-assign]
    raise AssertionError("unreachable")


def _crash(directory: Path, window: str) -> None:
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_crash_child, args=(str(directory), window))
    process.start()
    process.join(120)
    assert process.exitcode == 0, process.exitcode


@pytest.mark.integration
def test_crash_after_reservation_before_journal_releases_on_restart(tmp_path):
    _crash(tmp_path, "after_reserve")
    host = _Host(tmp_path)
    try:
        # Never journaled, so never dispatched: released at start.
        assert host.ledger.intent_state("crash-1") == "REJECTED"
        assert host.store.get("crash-1", scope=_targets()[WIRE]) is None
        assert host.submit("crash-1")["code"] == "LEDGER_INTENT_FINISHED"
        assert host.submit("crash-2")["ok"] is True
    finally:
        host.close()


@pytest.mark.integration
@pytest.mark.parametrize("window", ["after_journal_commit", "inside_ledger_transaction"])
def test_crash_between_journal_and_ledger_applies_the_fill_exactly_once(tmp_path, window):
    _crash(tmp_path, window)
    host = _Host(tmp_path)
    try:
        assert host.store.get("crash-1", scope=_targets()[WIRE]).state is ExecutionState.FILLED
        assert host.ledger.intent_state("crash-1") == "FILLED"
        assert host.ledger.positions(STRATEGY) == {INSTRUMENT: Decimal("2")}
        host.stream.apply_position(INSTRUMENT, "net", Decimal("2"))
        _state, snapshot = host.ledger.reconciled_snapshot(STRATEGY, host.stream.report())
        assert snapshot.fees_since_baseline == {"USDT": Decimal("2")}
        assert snapshot.cash_by_currency == {"USDT": Decimal("99998")}
        cursor = host.ledger.journal_cursor(host.binding.journal_source)
        # A second restart applies nothing again.
        host.close()
        host = _Host(tmp_path)
        assert host.ledger.journal_cursor(host.binding.journal_source) == cursor
        assert host.ledger.positions(STRATEGY) == {INSTRUMENT: Decimal("2")}
    finally:
        host.close()
