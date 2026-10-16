"""Permit TTL boundaries on the normal Risk order-entry service path."""

from __future__ import annotations

import multiprocessing
import sqlite3
import time
from contextlib import suppress
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_risk import PermitInvalidError

from bt_api_py import service_risk_order_entry as entry
from bt_api_py.service_execution import ServiceExecutionClient, ServiceExecutionError
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from tests.unit import test_service_risk_only_order_integration as base
from tests.unit import test_service_risk_order_fx_restart_integration as fx
from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _client_command,
    _close_normally,
    _receive,
    _start_process,
)


def _metadata_args(now_ns: int) -> dict[str, Any]:
    return {
        "instrument": fx._INSTRUMENT,
        "metadata_version": fx._METADATA_VERSION,
        "as_of_ns": now_ns - 1_000_000_000,
        "expires_at_ns": now_ns + 86_400_000_000_000,
        "tick_size": Decimal("0.01"),
        "quantity_step": Decimal("0.001"),
        "contract_multiplier": fx._MULTIPLIER,
        "max_gross_notional": Decimal("1000"),
        "taker_fee_bps": Decimal("0"),
        "fixed_fee": Decimal("0"),
        "max_slippage_bps": Decimal("0"),
        "quantity_unit": "ETH",
        "valuation_unit": fx._VALUATION,
        "quote_currency": fx._QUOTE,
        "fx_source_ref": fx._FX_SOURCE,
    }


def _fx_args(now_ns: int) -> dict[str, Any]:
    return fx._fx_fact(
        "1.25",
        "eurusd-ttl-v1",
        now_ns - 100_000_000,
        now_ns + 86_400_000_000_000,
    )


class _PermitTTLClock:
    """Synthetic permit clock that advances only at selected gate boundaries."""

    def __init__(self, mode: str, ttl_seconds: float = 30.0) -> None:
        self.mode = mode
        self.ttl_seconds = ttl_seconds
        self.base = time.time()
        self.calls = 0
        self.expired = False
        self.expire_on_call: int | None = None
        self.expired_at_call: int | None = None

    def host_started(self) -> None:
        self.base = time.time()
        self.calls = 0
        self.expire_on_call = None
        self.expired_at_call = 0 if self.mode == "expired" else None
        self.expired = self.mode == "expired"

    def arm_pre_dispatch_expiry(self) -> None:
        if self.mode != "pre_dispatch_ttl" or self.calls != 0 or self.expired:
            raise AssertionError("pre-dispatch TTL clock was armed outside its fixture boundary")
        # Two mapper allocation reads, three reserve reads, one validation
        # mapper read, and three validation reads put the final LIVE_GROSS
        # permit check on call nine. The gate expires the active reservation
        # there before any dispatch claim can be installed.
        self.expire_on_call = 9

    def expire(self) -> None:
        if not self.expired:
            self.expired = True
            self.expired_at_call = self.calls

    def __call__(self) -> float:
        self.calls += 1
        if self.expire_on_call is not None and self.calls >= self.expire_on_call:
            self.expire()
        return self.base + self.ttl_seconds + 1.0 if self.expired else self.base

    def snapshot(self) -> dict[str, Any]:
        now = self.base + self.ttl_seconds + 1.0 if self.expired else self.base
        return {
            "base": self.base,
            "calls": self.calls,
            "expired": self.expired,
            "expired_at_call": self.expired_at_call,
            "now": now,
            "ttl_seconds": self.ttl_seconds,
        }


class _DeadlineExpiryClock:
    """Let parsing pass, then expire the same absolute deadline before claim."""

    def __init__(self, deadline_ns: int) -> None:
        self.deadline_ns = deadline_ns
        self.calls = 0

    def time_ns(self) -> int:
        self.calls += 1
        if self.calls == 1:
            return self.deadline_ns - 1
        if self.calls == 2:
            return self.deadline_ns
        raise AssertionError("unexpected deadline clock read")


def _ttl_risk_state(handles: dict[str, Any], clock: _PermitTTLClock, intent_id: str):
    command = handles["journal"].get_command(fx._ACCOUNT_SCOPE, base._PRINCIPAL, intent_id)
    risk = handles["gate"].gross_exposure_snapshot(fx._ACCOUNT_SCOPE)
    allocation = handles["gate"].get_strategy_allocation(fx._ACCOUNT_SCOPE, base._STRATEGY)
    writes, counters = handles["provider"].snapshot()
    claim_code = "PERMIT_MISSING"
    if command is not None and command.permit_id is not None:
        try:
            handles["gate"].dispatch_claim_binding(command.permit_id)
        except PermitInvalidError as error:
            claim_code = error.code
        else:
            claim_code = "CLAIMED"
    used = allocation.used_notional or Decimal(0)
    reserved = allocation.reserved_notional or Decimal(0)
    clock_state = clock.snapshot()
    return {
        "command_state": None if command is None else command.state,
        "permit_id": None if command is None else command.permit_id,
        "client_order_id": None if command is None else command.client_order_id,
        "risk_intent_id": None if command is None else command.risk_intent_id,
        "claim_code": claim_code,
        "increase_count": int(used + reserved > 0),
        "increase_notional": str(used + reserved),
        "active_freeze_reasons": tuple(risk["active_freeze_reasons"]),
        "applied_order_count": len(risk["orders"]),
        "provider_writes": writes,
        "provider_counters": counters,
        "permit_clock_base": clock_state["base"],
        "permit_clock_now": clock_state["now"],
        "permit_clock_ttl_seconds": clock_state["ttl_seconds"],
        "permit_clock_expired": clock_state["expired"],
        "permit_clock_expired_at_call": clock_state["expired_at_call"],
        "expiry_sweeps": int(handles.get("expiry_sweeps", 0)),
        "release_error_codes": tuple(handles.get("release_error_codes", ())),
        "recovery_clock_observations": tuple(handles.get("recovery_clock_observations", ())),
        "deadline_clock_calls": (
            0 if handles.get("deadline_clock") is None else handles["deadline_clock"].calls
        ),
    }


class _TTLControlProxy:
    """Add fixture-only clock inspection without changing the ServiceHost API."""

    def __init__(self, pipe, clock: _PermitTTLClock, handles: dict[str, Any]) -> None:
        self._pipe = pipe
        self._clock = clock
        self._handles = handles
        self._discard_next_send = False

    def poll(self, timeout: float = 0.0) -> bool:
        # Mirror multiprocessing.Connection.poll: the host loop calls this
        # without arguments and must never block forever on the fixture pipe.
        return self._pipe.poll(timeout)

    def send(self, value) -> None:
        if self._discard_next_send:
            self._discard_next_send = False
            if type(value) is tuple and value and value[0] == "snapshot":
                return
        self._pipe.send(value)

    def recv(self):
        while True:
            command = self._pipe.recv()
            if command == ("arm_pre_dispatch_expiry",):
                self._clock.arm_pre_dispatch_expiry()
                self._pipe.send(("clock_armed",))
                self._discard_next_send = True
                return ("snapshot",)
            if type(command) is tuple and command and command[0] == "risk_state":
                self._pipe.send(
                    ("risk_state", _ttl_risk_state(self._handles, self._clock, command[1]))
                )
                self._discard_next_send = True
                return ("snapshot",)
            if type(command) is tuple and command and command[0] == "order_request":
                item = self._handles["journal"].get_command(
                    fx._ACCOUNT_SCOPE, base._PRINCIPAL, command[1]
                )
                request = None if item is None else fx.v2._plain(dict(item.order_request))
                deadline = None if item is None else item.canonical_payload.get("deadline_ns")
                self._pipe.send(("order_request", request, deadline))
                self._discard_next_send = True
                return ("snapshot",)
            return command

    def close(self) -> None:
        self._pipe.close()


def _risk_order_ttl_host(
    control,
    raw_config,
    registry_path: str,
    keys: dict[str, bytes],
    gate_path: str,
    journal_path: str,
    provider_path: str,
    metadata_args: dict[str, Any],
    fx_args: dict[str, Any],
    initial_states: tuple[str, ...],
    install_baseline: bool,
    clock_mode: str,
    ambiguous_after_write: bool = False,
    deadline_expire_after_parse: bool = False,
) -> None:
    """Run the accepted FX ServiceHost with a TTL clock and synthetic provider leaf."""

    clock = _PermitTTLClock(clock_mode)
    handles: dict[str, Any] = {}
    real_gate = fx.DurableRiskGate
    real_admission = fx.DurableRiskAdmissionAdapter
    real_journal = fx.RiskOrderJournal
    real_feed = fx._FxSyntheticBtApiFeed
    real_host = fx.ServiceHost
    real_entry_service = fx.RiskOrderEntryService
    real_entry_time = entry.time
    if deadline_expire_after_parse:
        deadline_clock = _DeadlineExpiryClock(1_000_000_000)
        handles["deadline_clock"] = deadline_clock
        entry.time = SimpleNamespace(time_ns=deadline_clock.time_ns)
    original_recover = real_entry_service.recover_interrupted_dispatches

    def gate_factory(*args, **kwargs):
        gate = real_gate(*args, clock=clock, clock_ns=time.time_ns, **kwargs)
        clock.ttl_seconds = gate.policy.permit_ttl_seconds
        handles["gate"] = gate
        return gate

    def journal_factory(*args, **kwargs):
        journal = real_journal(*args, **kwargs)
        handles["journal"] = journal
        return journal

    original_release = real_admission.release

    def sweeping_release(self, permit_reference: str, reason: str) -> None:
        # Model a separate Risk transaction winning the expiry race after
        # validate() has rolled back its own failed expiry transaction.
        # The real gate's snapshot commits the TTL sweep; the delegated
        # real release then reports PERMIT_NOT_ACTIVE. The entry service
        # admits only the exact adapter class, so the race hooks the class
        # method instead of substituting a subclass.
        if clock.mode == "pre_dispatch_ttl" and clock.expired:
            handles["expiry_sweeps"] = int(handles.get("expiry_sweeps", 0)) + 1
            handles["gate"].snapshot(fx._ACCOUNT_SCOPE)
        try:
            return original_release(self, permit_reference, reason)
        except PermitInvalidError as error:
            handles.setdefault("release_error_codes", []).append(error.code)
            raise

    class TTLFeed(real_feed):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            handles["provider"] = self

        def make_order(self, *args, **kwargs):
            result = super().make_order(*args, **kwargs)
            if ambiguous_after_write:
                clock.expire()
                raise TimeoutError("synthetic lost acknowledgement after committed write")
            return result

    class ClockResetServiceHost(real_host):
        def start(self):
            clock.host_started()
            return super().start()

    def record_recovery_clock(self, *args, **kwargs):
        handles.setdefault("recovery_clock_observations", []).append(clock.snapshot())
        return original_recover(self, *args, **kwargs)

    fx.DurableRiskGate = gate_factory
    real_admission.release = sweeping_release
    fx.RiskOrderJournal = journal_factory
    fx._FxSyntheticBtApiFeed = TTLFeed
    fx.ServiceHost = ClockResetServiceHost
    real_entry_service.recover_interrupted_dispatches = record_recovery_clock
    proxy = _TTLControlProxy(control, clock, handles)
    try:
        fx._risk_order_fx_host(
            proxy,
            raw_config,
            registry_path,
            keys,
            gate_path,
            journal_path,
            provider_path,
            metadata_args,
            fx_args,
            initial_states,
            install_baseline,
        )
    finally:
        fx.DurableRiskGate = real_gate
        real_admission.release = original_release
        fx.RiskOrderJournal = real_journal
        fx._FxSyntheticBtApiFeed = real_feed
        fx.ServiceHost = real_host
        real_entry_service.recover_interrupted_dispatches = original_recover
        entry.time = real_entry_time


def _start_host(
    context,
    *,
    records: list[dict[str, Any]],
    endpoint: str,
    keys: dict[str, bytes],
    paths: dict[str, str],
    metadata_args: dict[str, Any],
    fx_args: dict[str, Any],
    label: str,
    clock_mode: str,
    ambiguous_after_write: bool = False,
    deadline_expire_after_parse: bool = False,
):
    host_keys = {name: keys[name] for name in ("server_public", "server_secret", "strategy_public")}
    record = _start_process(
        context,
        _risk_order_ttl_host,
        (
            base._service_config(endpoint),
            paths["registry"],
            host_keys,
            paths["gate"],
            paths["journal"],
            paths["provider"],
            metadata_args,
            fx_args,
            ("open",),
            label.endswith("initial"),
            clock_mode,
            ambiguous_after_write,
            deadline_expire_after_parse,
        ),
        label,
    )
    records.append(record)
    ready = _receive(record["pipe"])
    assert ready == ("ready", endpoint, "started"), ready
    return record


def _start_strategy(
    context,
    *,
    records: list[dict[str, Any]],
    endpoint: str,
    keys: dict[str, bytes],
    label: str,
    logical_run_id: str | None = None,
):
    record = _start_process(
        context,
        fx._risk_order_fx_strategy,
        (
            endpoint,
            keys["strategy_public"],
            keys["strategy_secret"],
            keys["server_public"],
            logical_run_id,
        ),
        label,
    )
    records.append(record)
    ready = _receive(record["pipe"])
    assert (
        type(ready) is tuple
        and len(ready) == 3
        and ready[0] == "ready"
        and type(ready[1]) is str
        and ready[1]
        and type(ready[2]) is str
        and ready[2]
    ), ready
    return record, ready[1], ready[2]


def _start_deadline_strategy(
    context,
    *,
    records: list[dict[str, Any]],
    endpoint: str,
    keys: dict[str, bytes],
    label: str,
):
    record = _start_process(
        context,
        _risk_order_ttl_deadline_strategy,
        (
            endpoint,
            keys["strategy_public"],
            keys["strategy_secret"],
            keys["server_public"],
        ),
        label,
    )
    records.append(record)
    ready = _receive(record["pipe"])
    assert (
        type(ready) is tuple
        and len(ready) == 3
        and ready[0] == "ready"
        and type(ready[1]) is str
        and ready[1]
        and type(ready[2]) is str
        and ready[2]
    ), ready
    return record, ready[1], ready[2]


def _risk_order_ttl_deadline_strategy(
    control,
    endpoint: str,
    strategy_public: bytes,
    strategy_secret: bytes,
    server_public: bytes,
    logical_run_id: str | None = None,
) -> None:
    """Use the public normal client with an explicit absolute deadline."""

    runtime = None
    try:
        config = base._client_config(endpoint, "risk-order-strategy-curve-v1")
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, base._PRINCIPAL, base._STRATEGY)
        peer = WireIdentity(WireRole.SERVICE, base._SERVICE_PRINCIPAL, base._SERVICE_ID)
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda _ref: base._SCOPE,
            peer_resolver=lambda _service: peer,
            run_id=logical_run_id,
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
        client = ServiceExecutionClient(
            runtime,
            "broker.orders",
            scope_ref=base._ORDER_SCOPE_REF,
            query_mount="broker.order_query",
            query_scope_ref=base._QUERY_SCOPE_REF,
        )
        control.send(("ready", runtime.run_id, runtime.client_session_id))
        while True:
            command = control.recv()
            if command == ("close",):
                runtime.close()
                control.send(("closed",))
                return
            if command[0] == "submit_deadline":
                try:
                    view = client.submit(
                        intent_id=command[1],
                        signal_id="signal-" + command[1],
                        instrument=fx._INSTRUMENT,
                        side="BUY",
                        position_effect="OPEN",
                        order_type="LIMIT",
                        quantity=command[2],
                        price=command[3],
                        time_in_force="GTC",
                        reduce_only=False,
                        metadata_version=fx._METADATA_VERSION,
                        deadline_ns=command[4],
                        tags={},
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
                    control.send(("submitted", fx.v2._plain(fx.asdict(view))))
                continue
            raise AssertionError("unknown TTL strategy control")
    except BaseException as exc:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _risk_state(host, intent_id: str) -> dict[str, Any]:
    result = _client_command(host, "risk_state", intent_id)
    assert result[0] == "risk_state" and type(result[1]) is dict, result
    return result[1]


def _event_rows(host, intent_id: str):
    result = _client_command(host, "events", intent_id)
    assert result[0] == "events", result
    return result[1]


def test_spawned_service_risk_order_ttl_expiry_and_unknown_restart(tmp_path) -> None:
    """TTL denies before claim and preserves ambiguous dispatch across restart."""

    context = multiprocessing.get_context("spawn")
    metadata_args = _metadata_args(time.time_ns())
    fx_args = _fx_args(time.time_ns())
    # Generate each CURVE pair once and retain the host/client key material.
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
        "strategy_secret": strategy_secret,
    }
    records: list[dict[str, Any]] = []
    forced: list[str] = []
    failures: list[str] = []

    pre_endpoint = base._endpoint()
    pre_paths = {
        name: str(tmp_path / "pre-dispatch" / f"{name}.sqlite3")
        for name in ("registry", "gate", "journal", "provider")
    }
    (tmp_path / "pre-dispatch").mkdir(parents=True)

    deadline_endpoint = base._endpoint()
    deadline_paths = {
        name: str(tmp_path / "intent-deadline" / f"{name}.sqlite3")
        for name in ("registry", "gate", "journal", "provider")
    }
    (tmp_path / "intent-deadline").mkdir(parents=True)

    post_endpoint = base._endpoint()
    post_paths = {
        name: str(tmp_path / "post-dispatch" / f"{name}.sqlite3")
        for name in ("registry", "gate", "journal", "provider")
    }
    (tmp_path / "post-dispatch").mkdir(parents=True)

    try:
        pre_host = _start_host(
            context,
            records=records,
            endpoint=pre_endpoint,
            keys=keys,
            paths=pre_paths,
            metadata_args=metadata_args,
            fx_args=fx_args,
            label="risk-ttl-pre-dispatch-initial",
            clock_mode="pre_dispatch_ttl",
        )
        pre_strategy, _pre_run, _pre_session = _start_strategy(
            context,
            records=records,
            endpoint=pre_endpoint,
            keys=keys,
            label="risk-ttl-pre-dispatch-strategy",
        )
        assert _client_command(pre_host, "arm_pre_dispatch_expiry") == ("clock_armed",)
        pre_result = _client_command(pre_strategy, "submit", "ttl-before-claim", "2", "20")
        assert pre_result[0:3] == ("submit_error", "ADMISSION_DENIED", "REJECTED"), pre_result

        pre_state = _risk_state(pre_host, "ttl-before-claim")
        assert pre_state["command_state"] == "ADMISSION_DENIED", pre_state
        assert pre_state["permit_id"] is not None
        assert pre_state["claim_code"] == "PERMIT_NOT_DISPATCH_CLAIMED", pre_state
        assert pre_state["expiry_sweeps"] == 1, pre_state
        assert pre_state["release_error_codes"] == ("PERMIT_NOT_ACTIVE",), pre_state
        assert pre_state["increase_count"] == 0
        assert Decimal(pre_state["increase_notional"]) == Decimal("0")
        assert pre_state["active_freeze_reasons"] == ()
        assert pre_state["applied_order_count"] == 0
        assert pre_state["provider_writes"] == ()
        assert pre_state["provider_counters"] == {"fill_queries": 0, "order_queries": 0}
        assert pre_state["permit_clock_expired"] is True
        assert pre_state["permit_clock_expired_at_call"] == 9, pre_state
        assert pre_state["permit_clock_now"] > (
            pre_state["permit_clock_base"] + pre_state["permit_clock_ttl_seconds"]
        ), pre_state
        pre_events = _event_rows(pre_host, "ttl-before-claim")
        pre_event_types = tuple(event_type for event_type, _revision, _payload in pre_events)
        assert pre_event_types.count("RESERVED") == 1, pre_event_types
        assert pre_event_types[-1] == "ADMISSION_DENIED", pre_event_types
        assert not {
            "CLAIMED",
            "DISPATCHING",
            "ORDER_OBSERVATION",
            "FILL_OBSERVATION",
            "UNKNOWN",
        }.intersection(pre_event_types), pre_event_types
        assert pre_events[-1][2]["reason_code"] == "PERMIT_NOT_ACTIVE", pre_events[-1]
        _close_normally(pre_strategy, expected_tag="closed")
        _close_normally(pre_host, expected_tag="closed")

        deadline_host = _start_host(
            context,
            records=records,
            endpoint=deadline_endpoint,
            keys=keys,
            paths=deadline_paths,
            metadata_args=metadata_args,
            fx_args=fx_args,
            label="risk-ttl-intent-deadline-initial",
            clock_mode="normal",
            deadline_expire_after_parse=True,
        )
        deadline_strategy, _deadline_run, _deadline_session = _start_deadline_strategy(
            context,
            records=records,
            endpoint=deadline_endpoint,
            keys=keys,
            label="risk-ttl-intent-deadline-strategy",
        )
        deadline = 1_000_000_000
        deadline_result = _client_command(
            deadline_strategy,
            "submit_deadline",
            "ttl-intent-deadline",
            "2",
            "20",
            deadline,
        )
        assert deadline_result[0:3] == (
            "submit_error",
            "ADMISSION_DENIED",
            "REJECTED",
        ), deadline_result
        deadline_state = _risk_state(deadline_host, "ttl-intent-deadline")
        assert deadline_state["command_state"] == "ADMISSION_DENIED", deadline_state
        assert deadline_state["permit_id"] is not None
        assert deadline_state["claim_code"] == "PERMIT_NOT_DISPATCH_CLAIMED", deadline_state
        assert deadline_state["deadline_clock_calls"] == 2, deadline_state
        assert deadline_state["permit_clock_expired"] is False, deadline_state
        assert deadline_state["increase_count"] == 0, deadline_state
        assert Decimal(deadline_state["increase_notional"]) == Decimal("0")
        assert deadline_state["active_freeze_reasons"] == ()
        assert deadline_state["applied_order_count"] == 0
        assert deadline_state["provider_writes"] == ()
        assert deadline_state["provider_counters"] == {"fill_queries": 0, "order_queries": 0}
        persisted_request = _client_command(deadline_host, "order_request", "ttl-intent-deadline")
        assert persisted_request[0] == "order_request", persisted_request
        assert "deadline_ns" not in persisted_request[1], persisted_request
        assert persisted_request[2] == deadline, persisted_request
        deadline_events = _event_rows(deadline_host, "ttl-intent-deadline")
        deadline_event_types = tuple(
            event_type for event_type, _revision, _payload in deadline_events
        )
        assert deadline_event_types.count("RESERVED") == 1, deadline_event_types
        assert deadline_event_types[-1] == "ADMISSION_DENIED", deadline_event_types
        assert "CLAIMED" not in deadline_event_types, deadline_event_types
        assert "DISPATCHING" not in deadline_event_types, deadline_event_types
        assert deadline_events[-1][2]["reason_code"] == "DEADLINE_EXPIRED", deadline_events[-1]
        _close_normally(deadline_strategy, expected_tag="closed")
        _close_normally(deadline_host, expected_tag="closed")

        post_host1 = _start_host(
            context,
            records=records,
            endpoint=post_endpoint,
            keys=keys,
            paths=post_paths,
            metadata_args=metadata_args,
            fx_args=fx_args,
            label="risk-ttl-post-dispatch-initial",
            clock_mode="expire_on_write",
            ambiguous_after_write=True,
        )
        post_strategy1, logical_run_id, session1 = _start_strategy(
            context,
            records=records,
            endpoint=post_endpoint,
            keys=keys,
            label="risk-ttl-post-dispatch-strategy-1",
        )
        intent_id = "ttl-unknown-after-write"
        first_unknown = _client_command(post_strategy1, "submit", intent_id, "2", "20")
        assert first_unknown[0] == "submitted", first_unknown
        assert first_unknown[1]["state"] == "UNKNOWN", first_unknown
        assert first_unknown[1]["review_required"] is True, first_unknown
        command1 = _client_command(post_host1, "command", intent_id)
        assert command1[1] == "UNKNOWN", command1
        permit_id = command1[2]
        client_order_id = command1[3]
        risk_intent_id = command1[4]
        assert permit_id and client_order_id and risk_intent_id
        before_restart = _risk_state(post_host1, intent_id)
        assert before_restart["permit_clock_expired"] is True, before_restart
        assert before_restart["permit_clock_now"] > (
            before_restart["permit_clock_base"] + before_restart["permit_clock_ttl_seconds"]
        ), before_restart
        assert before_restart["increase_count"] == 1, before_restart
        assert Decimal(before_restart["increase_notional"]) == Decimal("150"), before_restart
        assert before_restart["active_freeze_reasons"], before_restart
        assert before_restart["applied_order_count"] == 0, before_restart
        assert before_restart["provider_writes"] == (client_order_id,), before_restart
        assert before_restart["provider_counters"] == {"fill_queries": 0, "order_queries": 0}
        first_events = _event_rows(post_host1, intent_id)
        first_event_types = tuple(event_type for event_type, _revision, _payload in first_events)
        assert "CLAIMED" in first_event_types and "DISPATCHING" in first_event_types
        assert first_events[-1][0] == "UNKNOWN", first_events[-1]
        assert first_events[-1][2]["reason_code"] == "PROVIDER_WRITE_OUTCOME_UNKNOWN"
        _close_normally(post_strategy1, expected_tag="closed")
        _close_normally(post_host1, expected_tag="closed")

        with sqlite3.connect(post_paths["provider"]) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO provider_faults(name) VALUES ('incomplete_next_fills')"
            )

        post_host2 = _start_host(
            context,
            records=records,
            endpoint=post_endpoint,
            keys=keys,
            paths=post_paths,
            metadata_args=metadata_args,
            fx_args=fx_args,
            label="risk-ttl-post-dispatch-restart",
            clock_mode="expired",
        )
        after_restart = _risk_state(post_host2, intent_id)
        assert after_restart["command_state"] == "UNKNOWN", after_restart
        assert after_restart["permit_id"] == permit_id
        assert after_restart["risk_intent_id"] == risk_intent_id
        assert after_restart["claim_code"] == "CLAIMED", after_restart
        assert after_restart["permit_clock_expired"] is True, after_restart
        assert after_restart["permit_clock_expired_at_call"] == 0, after_restart
        assert after_restart["recovery_clock_observations"], after_restart
        recovery_clock = after_restart["recovery_clock_observations"][-1]
        assert recovery_clock["expired"] is True, recovery_clock
        assert recovery_clock["now"] > (
            recovery_clock["base"] + after_restart["permit_clock_ttl_seconds"]
        ), recovery_clock
        assert after_restart["permit_clock_now"] > (
            after_restart["permit_clock_base"] + after_restart["permit_clock_ttl_seconds"]
        ), after_restart
        assert after_restart["permit_clock_now"] > (
            before_restart["permit_clock_base"] + before_restart["permit_clock_ttl_seconds"]
        ), after_restart
        assert after_restart["increase_count"] == 1, after_restart
        assert Decimal(after_restart["increase_notional"]) == Decimal("150"), after_restart
        assert after_restart["active_freeze_reasons"], after_restart
        assert after_restart["applied_order_count"] == 0, after_restart
        assert after_restart["provider_writes"] == (client_order_id,), after_restart
        assert after_restart["provider_counters"] == before_restart["provider_counters"]

        post_strategy2, run2, session2 = _start_strategy(
            context,
            records=records,
            endpoint=post_endpoint,
            keys=keys,
            label="risk-ttl-post-dispatch-strategy-2",
            logical_run_id=logical_run_id,
        )
        assert run2 == logical_run_id
        assert session2 != session1
        duplicate = _client_command(post_strategy2, "submit", intent_id, "2", "20")
        assert duplicate[0] == "submitted", duplicate
        assert duplicate[1]["state"] == "UNKNOWN", duplicate
        after_duplicate = _risk_state(post_host2, intent_id)
        assert after_duplicate["provider_writes"] == (client_order_id,), after_duplicate
        assert after_duplicate["provider_counters"] == before_restart["provider_counters"]
        assert after_duplicate["increase_count"] == 1, after_duplicate
        assert Decimal(after_duplicate["increase_notional"]) == Decimal("150"), after_duplicate
        assert after_duplicate["active_freeze_reasons"], after_duplicate

        queried = _client_command(post_strategy2, "query", intent_id)
        assert queried[0] == "queried" and queried[1]["state"] == "UNKNOWN", queried
        after_query = _risk_state(post_host2, intent_id)
        assert after_query["provider_writes"] == (client_order_id,), after_query
        assert after_query["provider_counters"] == {"fill_queries": 1, "order_queries": 1}
        assert after_query["increase_count"] == 1, after_query
        assert Decimal(after_query["increase_notional"]) == Decimal("150"), after_query
        assert after_query["active_freeze_reasons"], after_query
        assert after_query["applied_order_count"] == 0, after_query
        final_events = _event_rows(post_host2, intent_id)
        assert final_events[-1][0] == "UNKNOWN", final_events[-1]
        assert final_events[-1][2]["public_state"] == "UNKNOWN", final_events[-1]
        assert final_events[-1][2]["reason_code"] == "PROVIDER_OBSERVATION_INCOMPLETE"
        _close_normally(post_strategy2, expected_tag="closed")
        _close_normally(post_host2, expected_tag="closed")
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
