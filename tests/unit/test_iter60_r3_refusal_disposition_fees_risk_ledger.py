"""Iteration 60 R3: refusal disposition, late terminal fees, Risk owner ledger, attribution.

* A host refusal made before the execution journal sees the order (strategy
  ledger admission, typed command gate) carries ``disposition=NOT_JOURNALED``;
  the client raises ``ServiceExecutionNotDispatchedError`` and the workflow
  maps it to ``REJECTED``. Every other refusal stays ``UNKNOWN``.
* A late fee for a terminal order is journaled as fee evidence and projected
  into the host strategy ledger (cumulative per currency, regression refused).
* ``RiskOrderEntryService`` uses the same ledger binding over the Risk journal:
  admission before the journal, projection from Risk journal events, recovery.
* Risk-generated provider client order ids carry the Base strategy prefix that
  Gateway execution routing derives; an order of strategy A yields events on
  A's execution stream only.

UNIT/FAKE: temp SQLite, real Execution/Risk journals, no provider or network.
"""

from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal

import pytest

pytest.importorskip("bt_api_execution")
pytest.importorskip("bt_api_risk")

from bt_api_base.account_protocol import AccountKey, AccountResourceKey  # noqa: E402
from bt_api_base.execution_query_protocol import ExecutionOrderRecord  # noqa: E402
from bt_api_base.execution_row_observation_protocol import (  # noqa: E402
    AccountExecutionOrderObservation,
    strategy_client_order_prefix,
)
from bt_api_base.market_protocol import MarketInstrumentKey  # noqa: E402
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope  # noqa: E402
from bt_api_execution import InvalidStateTransition  # noqa: E402

from bt_api_py.account_position_stream import StreamedAccountPositions  # noqa: E402
from bt_api_py.service_command_wire import build_submit_command, command_arguments  # noqa: E402
from bt_api_py.service_execution import (  # noqa: E402
    NOT_JOURNALED_DISPOSITION,
    ServiceExecutionError,
    ServiceExecutionNotDispatchedError,
    ServiceExecutionUnknownOutcomeError,
    _reply_payload,
    not_journaled_refusal_result,
)
from bt_api_py.service_execution_workflow import _exception_outcome  # noqa: E402
from bt_api_py.service_risk_order_entry import (  # noqa: E402
    RiskOrderEntryService,
    _parse_order,
    _RiskLedgerIntent,
    _wire_scope_payload,
)
from bt_api_py.service_risk_order_journal import sha256_text  # noqa: E402
from bt_api_py.service_strategy_ledger_host import (  # noqa: E402
    ServiceStrategyLedgerBinding,
    ServiceStrategyLedgerError,
)
from tests.unit import test_iter60_service_strategy_ledger_host as exe  # noqa: E402
from tests.unit import test_iter60_typed_risk_order_entry as risk  # noqa: E402

# ---------------------------------------------------------------------------
# Item 4: the refusal disposition
# ---------------------------------------------------------------------------


def _refusal(code="BUDGET_EXCEEDED", **result):
    return {"ok": False, "code": code, "result": result}


def test_client_maps_only_a_well_formed_not_journaled_refusal_to_not_dispatched():
    with pytest.raises(ServiceExecutionNotDispatchedError) as refused:
        _reply_payload(
            {"ok": False, "code": "BUDGET_EXCEEDED", "result": not_journaled_refusal_result()},
            identifier="i-1",
        )
    assert refused.value.code == "BUDGET_EXCEEDED"
    typed = {
        "ok": False,
        "code": "COMMAND_BINDING_MISMATCH",
        "result": not_journaled_refusal_result(),
    }
    with pytest.raises(ServiceExecutionNotDispatchedError):
        _reply_payload(typed, identifier="i-1", typed=True)
    for bad in (
        _refusal(found=True, record=None, disposition=NOT_JOURNALED_DISPOSITION),
        _refusal(found=False, record={}, disposition=NOT_JOURNALED_DISPOSITION),
        _refusal(found=False, record=None, disposition="NOT_DISPATCHED_MAYBE"),
        _refusal("OK", found=False, record=None, disposition=NOT_JOURNALED_DISPOSITION),
        {"ok": True, "code": "OK", "result": not_journaled_refusal_result()},
    ):
        with pytest.raises(ServiceExecutionError) as error:
            _reply_payload(bad, identifier="i-1")
        assert type(error.value) is ServiceExecutionError
        assert error.value.code == "INVALID_REPLY"
    # A plain refusal remains an ordinary (non-definitive) error.
    with pytest.raises(ServiceExecutionError) as plain:
        _reply_payload(_refusal(found=False, record=None), identifier="i-1")
    assert type(plain.value) is ServiceExecutionError


def test_workflow_rejects_only_not_dispatched_refusals():
    rejected = _exception_outcome(
        "submit", "REQUEST", ServiceExecutionNotDispatchedError("BUDGET_EXCEEDED"), expected_id="i"
    )
    assert (rejected.disposition, rejected.code) == ("REJECTED", "BUDGET_EXCEEDED")
    for error in (
        ServiceExecutionError("BUDGET_EXCEEDED"),
        ServiceExecutionError("INVALID_REPLY"),
        ServiceExecutionUnknownOutcomeError("BUSINESS_OUTCOME_UNKNOWN", "i"),
    ):
        assert _exception_outcome("submit", "REQUEST", error, expected_id="i").disposition == (
            "UNKNOWN"
        )
    assert (
        _exception_outcome(
            "submit", "REQUEST", ServiceExecutionError("REQUEST_NOT_SENT"), expected_id="i"
        ).disposition
        == "REJECTED"
    )


@pytest.fixture
def host(tmp_path):
    item = exe._Host(tmp_path)
    try:
        yield item
    finally:
        item.close()


def test_execution_owner_ledger_refusal_declares_not_journaled(host):
    refused = host.submit("too-big", quantity="5")
    assert (refused["ok"], refused["code"]) == (False, "BUDGET_EXCEEDED")
    assert refused["result"] == not_journaled_refusal_result()
    with pytest.raises(ServiceExecutionNotDispatchedError):
        _reply_payload(refused, identifier="too-big")
    # A journaled key is never declared "not journaled".
    assert host.submit("buy-1")["ok"] is True
    assert host.binding.journal_lacks(exe.WIRE, "buy-1") is False
    assert host.binding.journal_lacks(exe.WIRE, "too-big") is True
    unserved = WireScope(exe.DOMAIN, account_ref=exe.ACCOUNT, strategy_ref="nobody")
    assert host.binding.journal_lacks(unserved, "too-big") is False
    assert host.provider.submitted == ["buy-1"]


# ---------------------------------------------------------------------------
# Item 5/7: late fees after the terminal state, through the public entry
# ---------------------------------------------------------------------------


def test_late_terminal_fee_is_journaled_and_projected_into_the_ledger(host):
    assert host.submit("buy-1")["ok"] is True
    host.observe("buy-1", "FILLED", "2", fee="1")
    assert host.ledger.intent_state("buy-1") == "FILLED"
    record = host.observe("buy-1", "FILLED", "2", fee="1.5")
    assert record.state.value == "FILLED" and record.cumulative_commission == Decimal("1.5")
    _state, snapshot = host.ledger.reconciled_snapshot(exe.STRATEGY, host.stream.report())
    assert snapshot.fees_since_baseline == {"USDT": Decimal("1.5")}
    assert host.ledger.intent_state("buy-1") == "FILLED"
    # Idempotent; a regression or another currency is refused and books nothing.
    host.observe("buy-1", "FILLED", "2", fee="1.5")
    for fee, currency in (("1.2", "USDT"), ("3", "BTC")):
        with pytest.raises(InvalidStateTransition):
            host.observe("buy-1", "FILLED", "2", fee=fee, currency=currency)
    _state, snapshot = host.ledger.reconciled_snapshot(exe.STRATEGY, host.stream.report())
    assert snapshot.fees_since_baseline == {"USDT": Decimal("1.5")}
    assert host.binding.blocked is None


# ---------------------------------------------------------------------------
# Item 6: the Risk account owner's strategy ledger
# ---------------------------------------------------------------------------


def _risk_option(directory, limit="5"):
    return {
        "path": str(directory / "risk-strategy-ledger.sqlite3"),
        "instruments": {"BTC-USD": {"multiplier": "1", "quote_currency": "USD"}},
        "budgets": {risk._STRATEGY: {"limit": limit, "currency": "USD"}},
        "capital": {risk._STRATEGY: {"amount": "1000", "currency": "USD"}},
    }


@pytest.fixture
def risk_ledger(tmp_path):
    gen = risk.risk_service.__wrapped__(tmp_path)
    service, target, contract, metadata = next(gen)
    stream = StreamedAccountPositions(risk._ACCOUNT, "sim:risk-position-stream")
    stream.apply_snapshot({})
    binding = ServiceStrategyLedgerBinding.for_risk_journal(
        _risk_option(tmp_path),
        journal=service._journal,
        targets=dict(service._targets),
        account_positions=stream,
    )
    owner = RiskOrderEntryService(
        service._bt_api,
        dict(service._targets),
        dict(service._admission),
        service._journal,
        service._gate,
        dict(service._provider_contracts),
        command_bindings=dict(service._command_gate._bindings),
        strategy_ledger=binding,
    )
    owner.recover_strategy_ledger()
    try:
        yield owner, binding, target
    finally:
        owner.close()
        next(gen, None)


def _typed(owner, intent_id, **changes):
    command = build_submit_command(
        risk._binding(), risk._payload(intent_id, **changes), scope=risk._SCOPE
    )
    return risk._handle(owner, command_arguments(command))


def test_risk_owner_ledger_refusal_and_gate_refusal_are_not_journaled(risk_ledger, monkeypatch):
    owner, binding, _target = risk_ledger
    calls = []
    monkeypatch.setattr(owner, "_submit", lambda **kwargs: calls.append(kwargs))
    # 0.1 x 100 = 10 USD > budget 5: refused by the ledger before the journal.
    refused = _typed(owner, "risk-big")
    assert refused["code"] == "BUDGET_EXCEEDED"
    assert refused["result"] == not_journaled_refusal_result()
    generic = risk._handle(owner, dict(risk._payload("risk-generic")))
    assert generic["code"] == "COMMAND_TYPED_REQUIRED"
    assert generic["result"] == not_journaled_refusal_result()
    assert calls == []


def test_risk_owner_releases_a_reservation_the_journal_never_recorded(risk_ledger, monkeypatch):
    owner, binding, _target = risk_ledger
    seen = []

    def fake_submit(**kwargs):
        seen.append(binding.ledger.intent_state(kwargs["arguments"]["intent_id"]))
        return {
            "ok": False,
            "code": "CAPABILITY_UNAVAILABLE",
            "result": {"found": False, "record": None},
        }

    monkeypatch.setattr(owner, "_submit", fake_submit)
    reply = _typed(owner, "risk-small", quantity="0.01")
    assert reply["code"] == "CAPABILITY_UNAVAILABLE"
    assert seen == ["RESERVED"]  # admitted before the Risk submit ran
    assert binding.ledger.intent_state("risk-small") == "REJECTED"


def _journal_command(owner, target, intent_id, *, strategy=risk._STRATEGY, quantity="0.01"):
    journal = owner._journal
    scope = (
        risk._SCOPE if strategy == risk._STRATEGY else replace(risk._SCOPE, strategy_ref=strategy)
    )
    fence_cm = journal.writer_fence(target.account_scope)
    fence = fence_cm.__enter__()
    journal.prepare_or_get(
        fence,
        principal_ref=risk._PRINCIPAL,
        intent_id=intent_id,
        risk_intent_id="risk-order:" + sha256_text(intent_id),
        payload_sha256=sha256_text("payload-" + intent_id),
        canonical_payload={"intent_id": intent_id, "quantity": quantity},
        wire_scope=_wire_scope_payload(scope),
        exchange_name=target.exchange_name,
        provider_account_id=target.provider_account_id,
        client_order_id="client-" + intent_id,
        order_request={"symbol": "BTC-USD"},
        order_request_sha256=sha256_text("request-" + intent_id),
    )
    return fence_cm, fence


def _append(owner, fence, intent_id, event_type, payload=None):
    owner._journal.append_event(
        fence,
        principal_ref=risk._PRINCIPAL,
        intent_id=intent_id,
        event_type=event_type,
        payload=payload or {},
    )


def _fills(*rows):
    return {
        "operation": "get_deals",
        "records": [
            {"size": size, "price": price, "fee": fee, "fee_currency": currency}
            for size, price, fee, currency in rows
        ],
    }


def test_risk_ledger_projects_fills_fees_and_terminal_states_from_the_risk_journal(risk_ledger):
    owner, binding, target = risk_ledger
    for intent_id in ("risk-fill", "risk-denied"):
        assert (
            binding.admit(
                risk._SCOPE,
                _RiskLedgerIntent(
                    intent_id, "BTC-USD", "BUY", Decimal("0.01"), Decimal("100"), "OPEN"
                ),
            )
            is None
        )
    fence_cm, fence = _journal_command(owner, target, "risk-fill")
    try:
        for kind in ("RESERVED", "CLAIMED", "DISPATCHING"):
            _append(owner, fence, "risk-fill", kind)
        _append(owner, fence, "risk-fill", "ORDER_OBSERVATION", {"public_state": "ACKED_TRACKED"})
        binding.sync()
        assert binding.ledger.intent_state("risk-fill") == "SUBMITTED"
        _append(
            owner,
            fence,
            "risk-fill",
            "FILL_OBSERVATION",
            _fills(("0.004", "100", "0.01", "USD"), ("0.006", "101", "0.02", "USD")),
        )
        _append(owner, fence, "risk-fill", "PROOF_READY")
        _append(owner, fence, "risk-fill", "ACKED_TRACKED", {"public_state": "FILLED"})
    finally:
        fence_cm.__exit__(None, None, None)
    fence_cm, fence = _journal_command(owner, target, "risk-denied")
    try:
        _append(owner, fence, "risk-denied", "ADMISSION_DENIED")
    finally:
        fence_cm.__exit__(None, None, None)
    # Another strategy's command in the same account is not this ledger's.
    fence_cm, fence = _journal_command(owner, target, "foreign", strategy="risk-strategy-z")
    try:
        _append(owner, fence, "foreign", "ADMISSION_DENIED")
    finally:
        fence_cm.__exit__(None, None, None)
    binding.sync()
    assert binding.ledger.intent_state("risk-fill") == "FILLED"
    assert binding.ledger.intent_state("risk-denied") == "REJECTED"
    assert binding.ledger.positions(risk._STRATEGY) == {"BTC-USD": Decimal("0.010")}
    snapshot = binding.ledger.snapshot(risk._STRATEGY)
    assert snapshot.fees_since_baseline == {"USD": Decimal("0.03")}
    # Exactly once: a second sync applies nothing.
    assert binding.sync() == 0


def test_risk_ledger_blocks_on_unbookable_fees_and_recovers_unjournaled(risk_ledger, tmp_path):
    owner, binding, target = risk_ledger
    binding.admit(
        risk._SCOPE,
        _RiskLedgerIntent("risk-lost", "BTC-USD", "BUY", Decimal("0.01"), Decimal("1"), "OPEN"),
    )
    binding.recover()  # the Risk journal never saw it: provably not dispatched
    assert binding.ledger.intent_state("risk-lost") == "REJECTED"
    binding.admit(
        risk._SCOPE,
        _RiskLedgerIntent("risk-mixed", "BTC-USD", "BUY", Decimal("0.01"), Decimal("1"), "OPEN"),
    )
    fence_cm, fence = _journal_command(owner, target, "risk-mixed")
    try:
        for kind in ("RESERVED", "CLAIMED", "DISPATCHING", "ORDER_OBSERVATION"):
            _append(owner, fence, "risk-mixed", kind)
        _append(
            owner,
            fence,
            "risk-mixed",
            "FILL_OBSERVATION",
            _fills(("0.005", "1", "0.01", "USD"), ("0.005", "1", "0.01", "BTC")),
        )
    finally:
        fence_cm.__exit__(None, None, None)
    with pytest.raises(ServiceStrategyLedgerError, match="PROJECTION_BLOCKED"):
        binding.sync()
    assert binding.blocked == "LEDGER_FEE_CURRENCY_MIXED"
    assert (
        binding.admit(
            risk._SCOPE,
            _RiskLedgerIntent("risk-next", "BTC-USD", "BUY", Decimal("0.01"), Decimal("1"), "OPEN"),
        )
        == "STRATEGY_LEDGER_PROJECTION_BLOCKED"
    )


def test_risk_ledger_binding_refuses_foreign_journals_and_mixed_accounts(risk_ledger, tmp_path):
    owner, _binding, target = risk_ledger
    stream = StreamedAccountPositions(risk._ACCOUNT, "sim:risk-position-stream")
    with pytest.raises(ServiceStrategyLedgerError, match="JOURNAL_INVALID"):
        ServiceStrategyLedgerBinding.for_risk_journal(
            _risk_option(tmp_path / "x"),
            journal=object(),
            targets=dict(owner._targets),
            account_positions=stream,
        )


# ---------------------------------------------------------------------------
# Item 6: strategy client-order prefixes and Gateway attribution
# ---------------------------------------------------------------------------


def test_risk_client_order_ids_route_gateway_events_to_the_submitting_strategy(
    risk_ledger, tmp_path
):
    from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
    from bt_api_gateway.execution_event_publisher import (
        GatewayExecutionEventPublisher,
        GatewayExecutionScopePolicy,
    )

    owner, _binding, target = risk_ledger
    _command, _internal = owner._command_gate.decode(
        "orders.submit",
        risk._SCOPE,
        command_arguments(
            build_submit_command(risk._binding(), risk._payload("r-a"), scope=risk._SCOPE)
        ),
    )
    default = _parse_order(
        dict(_internal, position_side="NET", position_bucket="ALL"),
        target,
        scope=risk._SCOPE,
        principal_ref=risk._PRINCIPAL,
    )
    assert default["client_order_id"].startswith("risk-")
    prefixed_target = replace(target, strategy_client_order_prefix=True)
    parsed = _parse_order(
        dict(_internal, position_side="NET", position_bucket="ALL"),
        prefixed_target,
        scope=risk._SCOPE,
        principal_ref=risk._PRINCIPAL,
    )
    client_a = parsed["client_order_id"]
    assert client_a.startswith(strategy_client_order_prefix(risk._STRATEGY))
    # Venue-safe (Base prefix schema v2): 32 lowercase alphanumerics, valid as an
    # OKX clOrdId and a Binance newClientOrderId; the default id is unchanged.
    assert len(client_a) == 32 and client_a.isalnum() and client_a == client_a.lower()
    assert len(default["client_order_id"]) == 45

    account = AccountKey("fixture", "fixture", "simulation", risk._ACCOUNT)
    instrument = MarketInstrumentKey(
        "fixture", "fixture", "simulation", "spot", "BTC-USD", "BTC-USD"
    )

    def order(order_id, client):
        return AccountExecutionOrderObservation(
            account,
            ExecutionOrderRecord(
                AccountResourceKey("spot", "cash"),
                instrument,
                order_id,
                client,
                "verified",
                "buy",
                "unavailable",
                None,
                "unavailable",
                "limit",
                "filled",
                "0.1",
                "0.1",
                "base",
                "100",
                None,
                "unavailable",
                None,
                "unavailable",
                "2027-01-15T08:00:00Z",
            ),
        )

    client_b = strategy_client_order_prefix("risk-strategy-b") + "0" * 22
    journal = GatewayEventJournal(tmp_path / "gateway.sqlite3")
    publisher = GatewayExecutionEventPublisher(
        journal,
        account_key=account,
        journal_account_scope="journal:risk-account",
        policy=GatewayExecutionScopePolicy.for_strategies(
            {risk._STRATEGY: "exec:a", "risk-strategy-b": "exec:b"}
        ),
    )
    result = publisher.publish(
        [order("o-a", client_a), order("o-b", client_b), order("o-x", default["client_order_id"])]
    )
    assert (result.appended, result.unattributed_dropped) == (2, 1)

    def events(scope):
        principal = GatewayPrincipal(
            "reader",
            frozenset({"journal:risk-account"}),
            frozenset({scope}),
            frozenset({GatewayCommandKind.READ}),
        )
        return journal.read_after(
            principal, "journal:risk-account", scope, cursor=None, limit=10
        ).events

    stream_a, stream_b = events("exec:a"), events("exec:b")
    assert len(stream_a) == 1 and len(stream_b) == 1
    assert stream_a[0].payload["observation"]["order"]["client_order_id"] == client_a
    assert stream_b[0].payload["observation"]["order"]["client_order_id"] == client_b


def test_risk_option_parsing_accepts_ledger_and_prefix(tmp_path):
    from bt_api_py.service_risk_order_entry import validate_service_options

    options = validate_service_options(
        {
            "role": "account_owner",
            "account_policy_ref": "risk-policy-v1",
            "strategy_ledger": _risk_option(tmp_path),
            "strategy_client_order_prefix": True,
        },
        ("service.order_entry",),
    )
    assert options["strategy_client_order_prefix"] is True
    assert options["strategy_ledger"]["path"].endswith("risk-strategy-ledger.sqlite3")
    with pytest.raises(Exception):
        validate_service_options(
            {
                "role": "account_owner",
                "account_policy_ref": "risk-policy-v1",
                "strategy_client_order_prefix": "yes",
            },
            ("service.order_entry",),
        )


_ = (time, WireIdentity, WireRole)
