"""Shared loopback composition for the Risk crash matrix and the 64.11 measurements.

INTEGRATION_SIM support only: a spawned loopback Gateway (synthetic native
dispatcher and synthetic venue returns, adapted from the 64.09 process test),
the Risk host started through its CLI in its own process, and strategy-side
clients over real CURVE. Temporary SQLite files; no provider session, no
account, no network beyond loopback. Not collected by pytest (no ``test_``
prefix).

Differences from ``test_iter64_risk_service_process``: any number of
strategies (each delegated to Risk), the Gateway reopens its own journal when
restarted on the same root, it counts native sends per client order id, and
the Risk host can be started with a test-only fault point
(``BT_RISK_FAULT_POINT``).
"""

from __future__ import annotations

import json
import hashlib
import os
import queue
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing, nullcontext
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

ACCOUNT, DOMAIN, RUN = "account-a", "paper:demo", "strategy-run"
TRADING_DAY = "20300101"
GATEWAY = WireIdentity(WireRole.SERVICE, "gateway-principal", "gateway-service")
RISK = WireIdentity(WireRole.SERVICE, "risk-principal", "risk-service-delegate")
CONTROL = WireIdentity(WireRole.APPLICATION_CLIENT, "risk-control", "risk-control-actor")
OPERATOR = WireIdentity(WireRole.APPLICATION_CLIENT, "risk-operator", "risk-operator-actor")
RISK_HOST = WireIdentity(WireRole.SERVICE, "risk-host", "risk-service")
ACCOUNT_ONLY = WireScope(DOMAIN, account_ref=ACCOUNT)
SDK_ROOT = Path(__file__).resolve().parents[2]
SDK_PYTHONPATH = os.pathsep.join(
    str(SDK_ROOT / part)
    for part in (
        ".",
        "bt_api/bt_api_gateway/tests",
        "bt_api/bt_api_ctp/tests",
        "bt_api/bt_api_base/tests",
        "tests/unit",
        "tests",
    )
)
CONTROL_OPS = frozenset(
    {
        "direct.control.apply",
        "direct.control.status",
        "direct.control.list",
        "direct.control.cancel_scope",
        "direct.control.cancel_scope_status",
    }
)
RISK_ORDER_OPS = (
    "risk.order.reserve",
    "risk.order.dispatch",
    "risk.order.release",
    "risk.order.status",
    "risk.status",
)
EXECUTION_RISK_OPS = (
    "risk.parent.reserve",
    "risk.parent.release",
    "risk.parent.status",
    "risk.order.reserve.v2",
    "risk.order.dispatch.v2",
    "risk.order.status",
    "risk.status",
)
OPERATOR_OPS = ("risk.admin.status", "risk.unknown.list", "risk.audit.read", "risk.latch.list")


def strategy_identity(index: int) -> WireIdentity:
    suffix = "a" if index == 0 else str(index)
    return WireIdentity(WireRole.STRATEGY_CLIENT, "principal-" + suffix, "strategy-" + suffix)


def execution_identity() -> WireIdentity:
    return WireIdentity(WireRole.SERVICE, "execution-principal", "execution-service")


def instrument():
    from bt_api_base.market_protocol import MarketInstrumentKey

    return MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")


def synthetic_return_fact(item, instrument_key):
    """Build the legacy ORDER tuple or an explicitly identified synthetic TRADE.

    The caller must first establish that the original command was SENT. The
    trade's venue/binding key is the original command's native strong key;
    its stable trade key is scoped by trading day and supplied trade ID.
    """
    from bt_api_base.direct_write_protocol import (
        DirectExecutionEventKind,
        DirectOrderStatus,
        NativeOrderIdentity,
    )
    from bt_api_base.gateway_execution_protocol import GatewayReturnFact

    if len(item) == 5 and item[0] == "trade":
        _kind, cid, trade_id, quantity, price = item
        native_key = "native-" + cid
        return GatewayReturnFact(
            ACCOUNT, "CTP", instrument_key, TRADING_DAY, DirectExecutionEventKind.TRADE,
            "synthetic-trade-" + trade_id,
            identity=NativeOrderIdentity("CTP", cid, {"trading_day": TRADING_DAY}, 1),
            identity_keys=(native_key,), binding_keys=(native_key,), venue_key=native_key,
            trade_key=(TRADING_DAY, trade_id), side="buy", offset="open",
            trade_quantity=quantity, trade_price=price,
        )
    cid, status, quantity, filled = item
    return GatewayReturnFact(
        ACCOUNT, "CTP", instrument_key, TRADING_DAY, DirectExecutionEventKind.ORDER,
        "synthetic-" + status + "-" + cid,
        identity=NativeOrderIdentity("CTP", cid, {"trading_day": TRADING_DAY}, 1),
        identity_keys=("native-" + cid,), binding_keys=("native-" + cid,),
        status=DirectOrderStatus(status), side="buy", offset="open",
        order_quantity=quantity, filled_quantity=filled, limit_price="10",
    )


# ----------------------------------------------------------------------------- Gateway child


_GATEWAY_CLEANUP_RETAINED = {}
_GATEWAY_CHILD_RETAINED = {}


def retained_loopback_root(pytest_base):
    """Create a fresh root outside pytest's cleanup tree; never delete it here.

    Gateway cleanup alone does not establish that the Risk child, clients,
    and queues are closed. Until their close results are bound, even a
    successful Gateway close is insufficient to delete this shared root.
    """
    base = Path(tempfile.gettempdir()).resolve()
    pytest_root = Path(pytest_base).resolve()
    if base == pytest_root or pytest_root in base.parents:
        raise RuntimeError("RETAINED_ROOT_UNAVAILABLE")
    root = Path(tempfile.mkdtemp(prefix="iter64-risk-loopback-", dir=str(base))).resolve()
    if root == pytest_root or pytest_root in root.parents:
        raise RuntimeError("RETAINED_ROOT_UNAVAILABLE")
    return root


def _gateway_token(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{32}", value, flags=re.ASCII) is not None


def gateway_stop_command(run_id, generation, stop_id):
    """Bind one synthetic stop to the ready message and a fresh caller nonce."""
    if not all(_gateway_token(value) for value in (run_id, generation, stop_id)):
        raise ValueError("INVALID_GATEWAY_STOP_IDENTITY")
    return ("stop", run_id, generation, stop_id)


def _gateway_stop_id(command, run_id, generation):
    if (type(command) is tuple and len(command) == 4 and type(command[0]) is str
            and command[0] == "stop" and all(_gateway_token(value) for value in command[1:])
            and command[1:3] == (run_id, generation)):
        return command[3]
    return None


def gateway_close_receipt_matches(receipt, *, pid, run_id, generation, stop_id):
    """Check the child cleanup result, not process exit or client unregister."""
    return (
        type(pid) is int and pid > 0
        and all(_gateway_token(value) for value in (run_id, generation, stop_id))
        and type(receipt) is tuple
        and len(receipt) == 6 and type(receipt[1]) is int
        and all(type(receipt[index]) is str for index in (0, 2, 3, 4, 5))
        and receipt == ("gateway_close", pid, run_id, generation, stop_id, "COMPLETE")
    )


class SyntheticGatewayStop:
    """Retain the original child before startup and own one stop attempt."""

    def __init__(self, process, controls, messages):
        self.process, self.controls, self.messages = process, controls, messages
        self._process_id = os.getpid()
        self._owner_thread = threading.current_thread()
        self.pid = self.generation = self.run_id = None
        self._binding_attempted = False
        self._bound = False
        self._attempted = False
        self._closed = False
        _GATEWAY_CHILD_RETAINED[id(self)] = self

    def bind_ready(self, ready):
        if os.getpid() != self._process_id or threading.current_thread() is not self._owner_thread:
            raise RuntimeError("GATEWAY_OWNER_IDENTITY_CHANGED")
        if self._binding_attempted:
            raise RuntimeError("GATEWAY_READY_ALREADY_ATTEMPTED")
        self._binding_attempted = True
        if (type(ready) is not tuple or len(ready) != 5 or ready[0] != "ready"
                or type(ready[1]) is not int or ready[1] <= 0
                or type(ready[2]) is not str or not ready[2]
                or not _gateway_token(ready[3]) or not _gateway_token(ready[4])
                or type(self.process.pid) is not int or self.process.pid != ready[1]):
            raise ValueError("INVALID_GATEWAY_READY_IDENTITY")
        self.pid, self.generation, self.run_id = ready[1], ready[3], ready[4]
        self._bound = True

    def close(self):
        if os.getpid() != self._process_id or threading.current_thread() is not self._owner_thread:
            raise RuntimeError("GATEWAY_OWNER_IDENTITY_CHANGED")
        if self._closed:
            return
        if self._attempted:
            raise RuntimeError("GATEWAY_CLEANUP_ALREADY_ATTEMPTED")
        self._attempted = True
        if not self._bound:
            raise RuntimeError("GATEWAY_CLEANUP_UNKNOWN")
        stop_id = secrets.token_hex(16)
        try:
            self.controls.put(gateway_stop_command(self.run_id, self.generation, stop_id))
        except Exception:
            raise RuntimeError("GATEWAY_CLEANUP_UNKNOWN") from None
        deadline = time.monotonic() + 30
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("GATEWAY_CLEANUP_UNKNOWN")
            try:
                item = self.messages.get(timeout=remaining)
            except Exception:
                raise RuntimeError("GATEWAY_CLEANUP_UNKNOWN") from None
            if type(item) is not tuple or not item:
                raise RuntimeError("GATEWAY_CLEANUP_UNKNOWN")
            if item[0] in ("error", "gateway_close_unknown", "control_rejected"):
                raise RuntimeError("GATEWAY_CLEANUP_UNKNOWN")
            if item[0] == "gateway_close":
                if not gateway_close_receipt_matches(
                    item, pid=self.pid, run_id=self.run_id,
                    generation=self.generation, stop_id=stop_id,
                ):
                    raise RuntimeError("GATEWAY_CLEANUP_UNKNOWN")
                break
        self.process.join(20)
        if (self.process.is_alive() is not False or type(self.process.exitcode) is not int
                or self.process.exitcode != 0):
            raise RuntimeError("GATEWAY_CLEANUP_UNKNOWN")
        self._closed = True
        del _GATEWAY_CHILD_RETAINED[id(self)]

    def terminate_after_constructor_failure(self):
        """Reap only this fixture child when its constructor never returned.

        This is a test-harness fallback, not a normal Gateway close receipt.
        The caller retains the original constructor error and records this
        bounded termination separately.
        """
        if os.getpid() != self._process_id or threading.current_thread() is not self._owner_thread:
            raise RuntimeError("GATEWAY_OWNER_IDENTITY_CHANGED")
        if self._closed:
            return None
        pid = self.process.pid
        if pid is None:
            if self._bound or self._attempted:
                raise RuntimeError("GATEWAY_START_FAILURE_PID_UNAVAILABLE")
            self._closed = True
            del _GATEWAY_CHILD_RETAINED[id(self)]
            return None
        if type(pid) is not int or pid <= 0:
            raise RuntimeError("GATEWAY_START_FAILURE_PID_INVALID")
        try:
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(10)
                if self.process.is_alive():
                    self.process.kill()
                    self.process.join(10)
            else:
                self.process.join(0)
            exitcode = self.process.exitcode
            if (self.process.pid != pid or self.process.is_alive() is not False
                    or type(exitcode) is not int):
                raise RuntimeError("GATEWAY_START_FAILURE_REAP_UNCONFIRMED")
        except BaseException as error:
            if isinstance(error, RuntimeError) and str(error).startswith("GATEWAY_START_FAILURE_"):
                raise
            raise RuntimeError("GATEWAY_START_FAILURE_REAP_UNCONFIRMED") from None
        self._closed = True
        del _GATEWAY_CHILD_RETAINED[id(self)]
        return pid, exitcode


def _close_synthetic_gateway(host, owner, stream, read_service, read_source):
    """Close this fixture's owners once; failure retains its exact handles.

    The Host borrows the stream and read service. The fixture owns the stream
    and the synthetic account read model; the owner thread owns its guard.
    Completion covers only these local cleanup calls, not account authority,
    client unregister, or the child process being reaped by its parent.
    """
    retained = (host, owner, stream, read_service, read_source)
    key = id(retained)
    _GATEWAY_CLEANUP_RETAINED[key] = retained
    if any(value is None for value in retained):
        return "RESOURCES_UNAVAILABLE"
    try:
        if host.state != "started" or read_service.state_source is not read_source:
            return "IDENTITY_UNAVAILABLE"
        if host.close() is not None or host.state != "closed":
            return "HOST_UNCONFIRMED"
        if stream.close(5) is not True:
            return "STREAM_UNCONFIRMED"
        if read_service.state_source is not read_source or read_source.close() is not None:
            return "READ_MODEL_UNCONFIRMED"
        status = read_source.read_status()
        if (read_source._state != "closed" or read_source.session_generation is not None
                or status.get("session_generation") is not None or status.get("ready") is not False):
            return "READ_MODEL_UNCONFIRMED"
        if owner.close(5) is not True or owner.closed is not True:
            return "OWNER_UNCONFIRMED"
        from bt_api_py.gateway_direct_owner import GatewayDirectOwnerStatus

        owner_status = owner.status()
        if (type(owner_status) is not GatewayDirectOwnerStatus
                or owner_status.failure_category is not None
                or type(owner_status.queue_depth) is not int or owner_status.queue_depth != 0
                or type(owner_status.service_queue_depth) is not int
                or owner_status.service_queue_depth != 0
                or owner_status.running is not False or owner_status.accepting is not False):
            return "OWNER_UNCONFIRMED"
    except Exception:
        return "CLEANUP_EXCEPTION"
    del _GATEWAY_CLEANUP_RETAINED[key]
    return "COMPLETE"


def _retained_gateway_wait(controls):
    """Ignore commands and interruptions while an unclosed child owns resources."""
    try:
        controls.get()
    except BaseException:
        # A broken control queue must not make the holder exit or busy-spin.
        try:
            time.sleep(1)
        except BaseException:
            pass


def _hold_synthetic_gateway(controls):
    # No command grants permission to retry cleanup or release unknown owners.
    while True:
        _retained_gateway_wait(controls)


def _finish_synthetic_gateway(host, owner, stream, read_service, read_source,
                              messages, controls, run_id, generation, stop_id):
    # Local references remain live even if cleanup itself is interrupted before
    # it can register the tuple in _GATEWAY_CLEANUP_RETAINED.
    try:
        cleanup = _close_synthetic_gateway(host, owner, stream, read_service, read_source)
    except BaseException:
        cleanup = "CLEANUP_INTERRUPTED"
    complete = type(cleanup) is str and cleanup == "COMPLETE"
    try:
        if (complete and _gateway_token(run_id)
                and _gateway_token(generation) and _gateway_token(stop_id)):
            messages.put(("gateway_close", os.getpid(), run_id, generation, stop_id, "COMPLETE"))
        else:
            messages.put(("gateway_close_unknown", os.getpid(), run_id,
                          "UNBOUND_STOP" if complete else "CLEANUP_UNKNOWN"))
    except BaseException:
        # A failed report is not proof of cleanup and must not release a holder.
        pass
    if not complete:
        _hold_synthetic_gateway(controls)


def gateway_main(root, endpoint, server_keys, publics, strategy_count, messages, controls,
                 order_cut_enabled: bool = False):
    """Loopback Gateway in its own process; reopens its journal when restarted on ``root``."""
    if type(order_cut_enabled) is not bool:
        raise ValueError("SYNTHETIC_ORDER_CUT_FLAG_INVALID")
    sys.path.insert(0, str(SDK_ROOT / "bt_api/bt_api_gateway/tests"))
    owner = host = stream = read_service = read_source = None
    run_id = secrets.token_hex(16)
    generation = stop_id = None
    try:
        from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
        from bt_api_base.direct_write_protocol import (
            DirectExecutionEventKind,
            DirectOrderStatus,
            NativeOrderIdentity,
            PreDispatchRecord,
            WriteOperation,
        )
        from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
        from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
        from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
        from bt_api_gateway.direct_control import GatewayDirectControl
        from bt_api_gateway.direct_delegation import GatewayDelegateGrant
        from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome
        from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger
        from bt_api_gateway.session_guard import LocalAccountSessionGuard
        from iter63_claim_support import SyntheticClaimPort, claiming

        import bt_api_py.gateway_strategy_origin_source as source_module
        from bt_api_py.gateway_account_comparison import GatewayAccountComparison
        from bt_api_py.gateway_account_observer import (
            ACCOUNT_OBSERVER_OPERATIONS,
            GatewayAccountObserver,
        )
        if order_cut_enabled:
            from bt_api_base.gateway_execution_protocol import GatewayReturnFact
            from bt_api_py.ctp_gateway_order_cut_source import CtpGatewayOrderCutSource
            from bt_api_py.gateway_account_facts import GatewayAccountFactsObserver
            from bt_api_py.gateway_order_cut_codec import OPERATIONS as ORDER_CUT_OPERATIONS

            # This subprocess is an offline fixture. It publishes source records
            # through the real journal and authenticated Gateway service while
            # the risk-side client, page validation and coverage verifier remain
            # production code.
            CtpGatewayOrderCutSource.journal = property(lambda source: source._synthetic_journal)
            CtpGatewayOrderCutSource.guard = property(lambda source: source._synthetic_guard)
            CtpGatewayOrderCutSource.current = lambda source: source._synthetic_cut
            CtpGatewayOrderCutSource.current_binding = lambda source: source._synthetic_binding
        from bt_api_py.gateway_delegate_actor_service import OPERATIONS as ACTOR_OPERATIONS
        from bt_api_py.gateway_delegate_actor_service import GatewayDelegateActorService
        from bt_api_py.gateway_delegation_service import (
            GatewayDelegationService,
            GatewayDelegationSettings,
        )
        from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
        from bt_api_py.gateway_direct_service import (
            EVENT_OPERATIONS,
            GatewayControlGrant,
            GatewayDirectGrant,
            GatewayDirectServiceBinding,
        )
        from bt_api_py.gateway_execution_stream_service import GatewayExecutionStreamService
        from bt_api_py.service_components import ComponentResources
        from bt_api_py.service_config import parse_service_config
        from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
        from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
        from tests._synthetic_strategy_origin_source import SyntheticStrategyOriginSource
        from tests.integration.test_iter63_delegate_risk_execution_loopback import _read_service
        from tests.integration.test_iter63_gateway_direct_service import _service_config

        # Child-process synthetic source only; it does not establish claim coverage.
        source_module.GatewayStrategyOriginSource = SyntheticStrategyOriginSource

        key = instrument()
        root = Path(root)
        (root / "guard").mkdir(exist_ok=True)
        strategies = [strategy_identity(i) for i in range(strategy_count)]
        holder: dict = {"places": {}}
        database = root / "journal.sqlite"

        def factory():
            guard = LocalAccountSessionGuard(root / "guard", ACCOUNT, "owner")
            generation = guard.acquire()
            if database.exists():
                with closing(sqlite3.connect(database)) as connection:
                    journal_id, revision = connection.execute(
                        "SELECT journal_id, revision FROM direct_journal_meta"
                    ).fetchone()
                journal = GatewayDirectCommandJournal(
                    database, ACCOUNT, generation=generation, expected_journal_id=journal_id,
                    minimum_revision=revision, controls_enabled=True,
                    strategy_ledger_enabled=True, account_extensions_enabled=True,
                    parent_authority_enabled=True, account_facts_financial_enabled=True,
                    actor_stream_enabled=True, strategy_origin_enabled=True,
                    control_cancel_enabled=True, account_fact_recovery_enabled=True,
                    account_event_recovery_enabled=True,
                    observed_order_source_enabled=order_cut_enabled,
                )
            else:
                journal = GatewayDirectCommandJournal.create(database, ACCOUNT, generation=generation)
                journal.enable_controls(guard)
                journal.enable_strategy_ledger(guard)
                journal.enable_account_extensions(guard)
                journal.enable_parent_authority(guard)
                journal.enable_account_facts_financial(guard)
                journal.enable_actor_stream(guard)
                journal.enable_strategy_origin(guard)
                journal.enable_control_cancel(guard)
                journal.enable_account_fact_recovery(guard)
                journal.enable_account_event_recovery(guard)
                if order_cut_enabled:
                    journal.enable_observed_order_source(guard)
            if order_cut_enabled:
                source_ref = "synthetic-ctp-" + run_id
                session_ref = "synthetic-session-" + run_id
                journal.begin_observed_order_source(
                    guard, source_ref=source_ref, session_ref=session_ref,
                    trading_day=TRADING_DAY,
                )
                order_cut_source = object.__new__(CtpGatewayOrderCutSource)
                order_cut_source._synthetic_journal = journal
                order_cut_source._synthetic_guard = guard
                order_cut_source._synthetic_cut = None
                order_cut_source._synthetic_binding = SimpleNamespace(
                    source_ref=source_ref, generation=session_ref, trading_day=TRADING_DAY,
                )
                order_cut_source._principals = frozenset({RISK.principal_ref})
                order_cut_source._execution_domain_ref = DOMAIN
                holder["order_cut_source"] = order_cut_source
                holder["order_cut_source_ref"] = source_ref
                holder["order_cut_session_ref"] = session_ref
            holder["control"] = GatewayDirectControl(journal, guard)
            holder["ledger"] = GatewayDirectExecutionLedger(journal, guard)

            def place(payload, port, account):
                client_order_id = payload["client_order_id"]
                holder["places"][client_order_id] = holder["places"].get(client_order_id, 0) + 1
                identity = NativeOrderIdentity("CTP", client_order_id, {"trading_day": TRADING_DAY}, 1)
                marker = port.commit(
                    PreDispatchRecord(
                        WriteOperation.PLACE, account, "CTP", payload["symbol"], identity, None,
                        payload, TRADING_DAY, 1, "synthetic",
                    )
                )
                return NativeDispatchOutcome(True, identity, marker)

            dispatcher = GatewayDirectDispatcher(
                journal, "CTP", guard, claiming(place),
                lambda identity: ("native-" + identity.client_order_id,), lambda _: True,
                claim_port=SyntheticClaimPort(),
            )

            def cancel(target, symbol, port, account):
                prepared = PreparedCancelRequest(
                    "cancel-" + target.client_order_id, target, symbol, TRADING_DAY, 1,
                    {"request_id": 12}, ("cancel-" + target.client_order_id,),
                )
                marker = port.commit(
                    PreDispatchRecord(
                        WriteOperation.CANCEL, account, "CTP", symbol, None, target, {},
                        prepared.trading_day, prepared.session_generation, "synthetic", prepared,
                    )
                )
                return NativeDispatchOutcome(True, target, marker)

            cancel_dispatcher = GatewayDirectCancelDispatcher(
                GatewayDirectCancelJournal(journal), dispatcher, guard, "CTP", cancel, lambda _: True
            )
            source = SyntheticStrategyOriginSource(
                resolve=lambda _context, _order: key,
                recheck=lambda value: value == key,
            )
            holder["journal"] = journal
            holder["source"] = source

            def close():
                source.close()
                guard.release()

            return GatewayDirectOwnerResources(
                dispatcher, journal, close, cancel_dispatcher=cancel_dispatcher
            )

        owner = GatewayDirectOwner(
            factory, queue_size=64, max_queue_wait_seconds=5, busy_timeout_seconds=5
        )
        assert owner.start(10)
        journal, ledger = holder["journal"], holder["ledger"]

        def refresh_order_cut_on_owner():
            if not order_cut_enabled:
                return None
            source_ref = holder["order_cut_source_ref"]
            session_ref = holder["order_cut_session_ref"]
            guard = holder["order_cut_source"]._synthetic_guard
            with journal._transaction(read_only=True) as db:
                stored = db.execute(
                    "SELECT fact_digest, fact_json FROM direct_execution_facts "
                    "WHERE account=? ORDER BY rowid",
                    (ACCOUNT,),
                ).fetchall()
            facts = [
                (row[0], GatewayReturnFact.from_mapping(json.loads(row[1])))
                for row in stored
            ]
            for source_sequence, (fact_digest, _fact) in enumerate(facts, 1):
                source_record_digest = hashlib.sha256(
                    json.dumps(
                        [source_ref, session_ref, source_sequence, fact_digest],
                        separators=(",", ":"), ensure_ascii=True,
                    ).encode("ascii")
                ).hexdigest()
                journal.record_observed_order_result(
                    guard,
                    source_ref=source_ref,
                    session_ref=session_ref,
                    trading_day=TRADING_DAY,
                    source_sequence=source_sequence,
                    source_record_digest=source_record_digest,
                    fact_digest=fact_digest,
                    gap_reason=None,
                )
            latest_orders = {}
            for fact_digest, fact in facts:
                if fact.kind is DirectExecutionEventKind.ORDER and fact.identity is not None:
                    latest_orders[fact.identity.client_order_id] = (fact_digest, fact)
            terminal = {
                DirectOrderStatus.FILLED,
                DirectOrderStatus.CANCELLED,
                DirectOrderStatus.REJECTED,
            }
            query_fact_digests = tuple(
                fact_digest
                for fact_digest, fact in latest_orders.values()
                if fact.status not in terminal
            )
            query_digest = hashlib.sha256(
                json.dumps(
                    ["synthetic-ctp-account-orders-v1", TRADING_DAY, query_fact_digests],
                    separators=(",", ":"), ensure_ascii=True,
                ).encode("ascii")
            ).hexdigest()
            cut = journal.record_observed_order_query_cut(
                guard,
                source_ref=source_ref,
                session_ref=session_ref,
                trading_day=TRADING_DAY,
                begin_sequence=0,
                end_sequence=len(facts),
                query_digest=query_digest,
                query_scope="ctp.futures.account_orders.v1",
                query_row_count=len(query_fact_digests),
                query_fact_digests=query_fact_digests,
            )
            holder["order_cut_source"]._synthetic_cut = cut
            return cut

        if order_cut_enabled:
            owner.submit_service_call(refresh_order_cut_on_owner).result(timeout_seconds=5)
        stream = GatewayExecutionStreamService(
            ledger, journal, {s.principal_ref: s.actor_ref for s in strategies},
            execution_domain_ref=DOMAIN,
        )
        binding = GatewayDirectServiceBinding(
            journal,
            {s.principal_ref: GatewayDirectGrant(s.actor_ref, frozenset({"rb"})) for s in strategies},
            owner=owner,
            strategy_financial_enabled=order_cut_enabled,
            reservation_fence=lambda _context, _order: nullcontext(),
            cancel_enabled=True,
            execution_stream=stream,
            execution_domain_ref=DOMAIN,
            strategy_origin_source=holder["source"],
            control=holder["control"],
            control_grants={
                CONTROL.principal_ref: GatewayControlGrant(
                    CONTROL.actor_ref, True, frozenset(), CONTROL_OPS, kind="risk",
                    modes=frozenset({"STOP_NEW", "FREEZE"}), instrument_scope=True,
                )
            },
        )
        logical = frozenset(
            {"direct.reserve", "direct.dispatch", "direct.command_status", "direct.cancel",
             "direct.cancel_status", "direct.release"}
        )
        delegation = GatewayDelegationService(
            GatewayDelegationSettings(
                tuple(
                    GatewayDelegateGrant(RISK, ACCOUNT, s.actor_ref, DOMAIN, "risk", "risk-v1",
                                         frozenset({key}), logical)
                    for s in strategies
                ),
                lambda value: value == key,
            ),
            owner=owner, journal=journal, instance_id="gateway-a", scope_ref="scope-a",
        )
        actor = GatewayDelegateActorService(delegation)
        observers = frozenset({RISK.principal_ref})
        observer = GatewayAccountObserver(
            owner, journal, ledger, account_scope=ACCOUNT, execution_domain_ref=DOMAIN,
            principals=observers,
        )
        account_facts = (
            GatewayAccountFactsObserver(
                owner, journal, ledger, account_scope=ACCOUNT,
                execution_domain_ref=DOMAIN, principals=observers,
            )
            if order_cut_enabled else None
        )
        # The account read port is a Host requirement the observer never uses; its fixed
        # synthetic observation cannot be stored twice, so each Gateway start gets its own.
        read_root = root / ("read-" + str(time.time_ns()))
        read_root.mkdir()
        read_service = _read_service(read_root, observers)
        read_source = read_service.state_source
        comparison = GatewayAccountComparison(
            owner, journal, account_scope=ACCOUNT, principals=frozenset(),
            instruments=frozenset({key}),
        )
        strategy_ops = frozenset(
            {"direct.reserve", "direct.dispatch", "direct.command_status",
             "direct.delegated_command_status", "direct.delegated_cancel",
             "direct.delegated_cancel_status"}
        ) | EVENT_OPERATIONS
        delegate_ops = frozenset({"direct.delegated_reserve", "direct.delegated_dispatch"}) | ACTOR_OPERATIONS

        def direct(operations, scope):
            return ResourceGrant("gateway-a", "service.direct_write", "scope-a", scope, operations)

        observe_operations = ACCOUNT_OBSERVER_OPERATIONS
        if order_cut_enabled:
            # Risk's v2 observer registers only its page read, ACK, and the
            # authenticated query/receipt cut operations.  Keep the trusted
            # resource grant narrower than the Gateway's available facts API.
            observe_operations |= frozenset(
                {"account.facts.read_after.v2", "account.facts.ack"}
            ) | ORDER_CUT_OPERATIONS
        observe = ResourceGrant(
            "gateway-a", "service.account_read", "scope-a", ACCOUNT_ONLY, observe_operations
        )
        grants = {}
        for s in strategies:
            template = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=s.actor_ref)
            grants[s.principal_ref] = (PrincipalGrant(s, (direct(strategy_ops, template),)),)
        grants[RISK.principal_ref] = (PrincipalGrant(RISK, tuple(
            direct(delegate_ops, WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=s.actor_ref))
            for s in strategies) + (observe,)),)
        grants[CONTROL.principal_ref] = (PrincipalGrant(CONTROL, (direct(CONTROL_OPS, ACCOUNT_ONLY),)),)
        authority = TrustedServiceAuthority(
            identity=GATEWAY, scope=WireScope(DOMAIN), scope_ref="scope-a",
            scope_version="scope-v1", security_ref="security-a", security_version="security-v1",
            registration_policy_ref="direct-policy-v1",
            registration_policy_version="direct-policy-v1",
            service_requirements_ref="direct-requirements-v1",
            service_requirements_version="direct-requirements-v1",
            curve_public_key=server_keys[0], curve_secret_key=server_keys[1],
            principals=publics, grants=grants,
        )
        raw = _service_config(endpoint).to_public_dict()
        raw["service_id"] = GATEWAY.actor_ref
        raw["components"]["gateway"]["settings"]["bind_ports"].append("service.account_read")
        raw["components"]["gateway"]["settings"]["options"].update(
            dispatch_enabled=True, cancel_enabled=True, control_enabled=True,
            strategy_ledger_enabled=True, account_extensions_enabled=True, delegation_enabled=True,
        )
        if order_cut_enabled:
            raw["components"]["gateway"]["settings"]["options"][
                "account_facts_financial_enabled"
            ] = True
        host = ServiceHost(
            parse_service_config(raw), authority, root / "registry.sqlite",
            lambda *_: ComponentResources.borrowed(
                {"binding": binding, "delegation_service": delegation,
                 "delegate_actor_service": actor, "read_service": read_service,
                 "account_observer": observer, "account_comparison": comparison,
                 **({"account_facts": account_facts,
                     "order_cut_source": holder["order_cut_source"]}
                    if order_cut_enabled else {})},
                ready=lambda: True,
            ),
        )
        host.start()
        generation = journal.generation
        messages.put(("ready", os.getpid(), journal.journal_id, generation, run_id))
        while True:
            try:
                item = controls.get_nowait()
            except queue.Empty:
                item = None
            if item == "stop":
                break
            if type(item) is tuple and item and item[0] == "stop":
                candidate_stop_id = _gateway_stop_id(item, run_id, generation)
                if candidate_stop_id is not None:
                    stop_id = candidate_stop_id
                    break
                messages.put(("control_rejected", "INVALID_GATEWAY_STOP_IDENTITY"))
                continue
            if item == "stats":
                messages.put(("stats", dict(holder["places"])))
            elif item is not None:
                # One synthetic venue return for a dispatched order (never a provider call).
                fact = synthetic_return_fact(item, key)
                client_order_id = fact.identity.client_order_id
                def ingest_and_refresh(fact=fact):
                    result = ledger.ingest(fact)
                    if order_cut_enabled:
                        refresh_order_cut_on_owner()
                    return result

                result = owner.submit_service_call(ingest_and_refresh).result(timeout_seconds=5)
                reply = ("ingested", client_order_id, result.disposition, time.time_ns())
                if fact.kind is DirectExecutionEventKind.TRADE:
                    reply += ("trade", fact.trade_key[-1])
                messages.put(reply)
            host.serve_once(5)
    except BaseException as error:
        import traceback

        messages.put(("error", type(error).__name__, str(error), traceback.format_exc()))
    finally:
        _finish_synthetic_gateway(host, owner, stream, read_service, read_source,
                                  messages, controls, run_id, generation, stop_id)


# ----------------------------------------------------------------------------- files and clients


def keypairs(strategy_count: int, *, execution_enabled: bool = False) -> dict:
    if type(execution_enabled) is not bool:
        raise ValueError("SYNTHETIC_EXECUTION_IDENTITY_FLAG_INVALID")
    names = ["gateway_server", "risk_delegate", "risk_control", "risk_server", "operator"]
    names += ["strategy-" + str(i) for i in range(strategy_count)]
    if execution_enabled:
        names += ["execution_server", "execution_risk"]
        names += ["strategy-execution-" + str(i) for i in range(strategy_count)]
    return {name: zmq.curve_keypair() for name in names}


def gateway_publics(keys: dict, strategy_count: int) -> dict:
    publics = {keys["strategy-" + str(i)][0]: strategy_identity(i).principal_ref
               for i in range(strategy_count)}
    publics[keys["risk_delegate"][0]] = RISK.principal_ref
    publics[keys["risk_control"][0]] = CONTROL.principal_ref
    return publics


def _restrict_fixture_path(path: Path, *, mode: int) -> None:
    """Apply the local-private fixture contract on POSIX and Windows."""
    from bt_api_py import _win32_security as security

    if security.WINDOWS:
        if not security.has_privileges_available():
            raise RuntimeError("SYNTHETIC_PRIVATE_ACL_UNAVAILABLE")
        security._set_owner_only_acl(path)
    else:
        os.chmod(path, mode)


def _write_key(path: Path, key: bytes, *, secret: bool) -> None:
    path.write_bytes(key + b"\n")
    _restrict_fixture_path(path, mode=0o600 if secret else 0o644)


def _seed_synthetic_r05_v2_storage(config_path: Path) -> None:
    """Prepare the empty offline Risk journal selected by the v4 R05 fixture."""
    from bt_api_py.risk_service_config import ensure_private_directory, load_risk_service_config
    from bt_api_risk.core.admission import AccountScope
    from bt_api_risk.pretrade.ledger import PretradeLedger
    from bt_api_risk.risk_order_journal import RiskOrderJournal

    config = load_risk_service_config(config_path)
    ensure_private_directory(config.storage_directory)
    _restrict_fixture_path(config.storage_directory, mode=0o700)
    journal = RiskOrderJournal(config.risk_database, writer_id="synthetic-seed")
    scope = AccountScope(config.provider, config.account_scope, config.environment)
    with journal.writer_fence(scope) as fence:
        ledger = PretradeLedger(journal, fence, schema_version=4)
        ledger.enable_r05_observations_v2()


def risk_files(root: Path, keys: dict, strategy_count: int, gateway_endpoint: str,
               risk_endpoint: str, *, ttl_seconds: int = 20, max_quantity: str = "2",
               observation_poll: str = "0.1", maintain_seconds: str = "1",
               dispatch_recheck_seconds: int = 2,
               parent_contract_enabled: bool = False,
               execution_enabled: bool = False,
               order_cut_enabled: bool = False) -> Path:
    if (type(parent_contract_enabled) is not bool or type(execution_enabled) is not bool
            or type(order_cut_enabled) is not bool):
        raise ValueError("SYNTHETIC_PARENT_CONTRACT_OPT_IN_INVALID")
    if order_cut_enabled and not (parent_contract_enabled and execution_enabled):
        raise ValueError("SYNTHETIC_ORDER_CUT_REQUIRES_V2_EXECUTION_PARENT")
    _restrict_fixture_path(root, mode=0o700)
    key_dir = root / "keys"
    key_dir.mkdir(exist_ok=True)
    _restrict_fixture_path(key_dir, mode=0o700)
    for name, (public, secret) in keys.items():
        _write_key(key_dir / (name + ".public"), public, secret=False)
        _write_key(key_dir / (name + ".secret"), secret, secret=True)
    strategies = [strategy_identity(i) for i in range(strategy_count)]
    caps = ", ".join(s.actor_ref + ": {rb: \"" + max_quantity + "\"}" for s in strategies)
    (root / "risk_policy.yaml").write_text(
        "schema: bt_api_risk.policy.v2\n"
        "policy_id: it-64-recovery\n"
        "account: {route: CTP___FUTURE, account_scope: " + ACCOUNT + "}\n"
        "per_order:\n  max_quantity: {" + caps + "}\n"
        "self_trade: {enabled: false, on_unverifiable: deny}\n",
        encoding="utf-8",
    )
    principals = [
        {"principal_ref": s.principal_ref, "role": "strategy", "actor_ref": s.actor_ref,
         "strategy_refs": [s.actor_ref], "operations": list(RISK_ORDER_OPS),
         "public_key_file": "keys/strategy-" + str(i) + ".public"}
        for i, s in enumerate(strategies)
    ]
    if execution_enabled:
        identity = execution_identity()
        principals.append(
            {"principal_ref": identity.principal_ref, "role": "execution",
             "actor_ref": identity.actor_ref,
             "strategy_refs": [s.actor_ref for s in strategies],
             "operations": list(EXECUTION_RISK_OPS),
             "public_key_file": "keys/execution_risk.public"}
        )
    principals.append(
        {"principal_ref": OPERATOR.principal_ref, "role": "operator",
         "actor_ref": OPERATOR.actor_ref, "strategy_refs": [], "operations": list(OPERATOR_OPS),
         "public_key_file": "keys/operator.public"}
    )
    config = {
        "schema": "bt_api_risk.service.v1",
        "parent_contract_enabled": parent_contract_enabled,
        "service": {
            "service_id": RISK_HOST.actor_ref, "principal_ref": RISK_HOST.principal_ref,
            "instance_id": "risk-a", "scope_ref": "risk-scope", "rpc_bind": risk_endpoint,
            "public_key_file": "keys/risk_server.public", "secret_key_file": "keys/risk_server.secret",
        },
        "account": {
            "route": "CTP___FUTURE", "provider": "ctp", "environment": "simnow",
            "account_scope": ACCOUNT, "execution_domain_ref": DOMAIN,
            "instruments": {"rb": {"venue": "SHFE", "native_symbol": "rb", "product_type": "future",
                                   "multiplier": "10", "tick_size": "1"}},
        },
        "storage": {"directory": "state"},
        "policy": {"file": "risk_policy.yaml"},
        "principals": principals,
        "gateway": {
            "service_id": GATEWAY.actor_ref, "principal_ref": GATEWAY.principal_ref,
            "instance_id": "gateway-a", "scope_ref": "scope-a", "rpc_endpoint": gateway_endpoint,
            "server_public_key_file": "keys/gateway_server.public",
            "delegate": {"principal_ref": RISK.principal_ref, "actor_ref": RISK.actor_ref,
                         "public_key_file": "keys/risk_delegate.public",
                         "secret_key_file": "keys/risk_delegate.secret",
                         "grant_revisions": {s.actor_ref: "risk-v1" for s in strategies}},
            "control": {"principal_ref": CONTROL.principal_ref, "actor_ref": CONTROL.actor_ref,
                        "public_key_file": "keys/risk_control.public",
                        "secret_key_file": "keys/risk_control.secret"},
        },
        "deadlines": {"call_timeout_seconds": 5, "reservation_ttl_seconds": ttl_seconds,
                      "dispatch_recheck_seconds": dispatch_recheck_seconds},
        "refresh": {"loop_timeout_ms": 5, "observation_poll_seconds": observation_poll,
                    "maintain_seconds": maintain_seconds, "dispatch_recheck_limit": 64},
    }
    if order_cut_enabled:
        config["pretrade"] = {"schema_version": 4}
        config["account_facts_observation"] = {
            "poll_interval_seconds": 1,
            "timeout_seconds": "0.250",
            "page_size": 256,
            "order_cut_enabled": True,
            "order_cut_max_age_seconds": 60,
            "order_cut_timeout_seconds": 2,
            "order_cut_page_size": 256,
        }
    path = root / "risk_service.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    if order_cut_enabled:
        _seed_synthetic_r05_v2_storage(path)
    return path


def risk_client(risk_endpoint: str, keys: dict, index: int = 0, *, run_id: str = RUN):
    from bt_api_py.risk_service_client import RiskClientRoute, RiskServiceClient
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys

    identity = strategy_identity(index)
    config = parse_client_config(
        {
            "client_config_schema_version": 1, "process_role": "strategy_client",
            "registration": {"strategy_ref": identity.actor_ref},
            "clients": {"risk": {"config_schema_version": 1, "settings": {
                "bindings": [{"mount": "broker.risk_order", "service_id": RISK_HOST.actor_ref,
                              "instance_id": "risk-a", "scope_ref": "risk-scope",
                              "account_ref": ACCOUNT, "transport_ref": "risk-transport",
                              "required": True}],
                "options": {"request_timeout_ms": 20000}}}},
            "communication": {"config_schema_version": 1, "profiles": {"risk-transport": {
                "backend": "zmq", "config_schema_version": 1,
                "endpoints": {"rpc_connect": risk_endpoint}, "security_profile_ref": "risk-curve"}}},
        }
    )
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref, run_id=run_id)
    route = RiskClientRoute("broker.risk_order", RISK_HOST.actor_ref, "risk-a", "risk-scope",
                            scope, identity)
    return RiskServiceClient.compose(
        config, route=route, peer=RISK_HOST,
        security_profiles={"risk-curve": ClientCurveKeys(
            keys["risk_server"][0], *keys["strategy-" + str(index)])},
        requested_operations=frozenset(RISK_ORDER_OPS),
    )


def operator_runtime(risk_endpoint: str, keys: dict, *, account: str = ACCOUNT):
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

    config = parse_client_config(
        {
            "client_config_schema_version": 1, "process_role": "application_client",
            "registration": {"application_ref": OPERATOR.actor_ref},
            "clients": {"risk": {"config_schema_version": 1, "settings": {"bindings": [{
                "mount": "application.risk_admin", "service_id": RISK_HOST.actor_ref,
                "instance_id": "risk-a", "scope_ref": "risk-scope", "account_ref": account,
                "transport_ref": "risk-transport"}]}}},
            "communication": {"config_schema_version": 1, "profiles": {"risk-transport": {
                "backend": "zmq", "config_schema_version": 1,
                "endpoints": {"rpc_connect": risk_endpoint}, "security_profile_ref": "risk-curve"}}},
        }
    )
    factory = ZmqTransportFactory(
        {"risk-curve": ClientCurveKeys(keys["risk_server"][0], *keys["operator"])}
    )
    return ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(
        config, identity=OPERATOR, scope_resolver=lambda _: WireScope(DOMAIN, account_ref=account),
        peer_resolver=lambda _: RISK_HOST, transport_factory=factory,
        requested_operations={"application.risk_admin": frozenset(OPERATOR_OPS)}))


def admin(operator, operation, arguments=None, timeout_seconds=10):
    from bt_api_py.gateway_execution_stream_client import _detach_wire

    return _detach_wire(operator.call(
        "application.risk_admin", operation, arguments or {},
        account_ref=ACCOUNT, scope_ref="risk-scope", timeout_seconds=timeout_seconds))


def strategy_session(gateway_endpoint: str, keys: dict, journal_id: str, index: int = 0,
                     *, run_id: str = RUN, account: str = ACCOUNT, peer: WireIdentity = GATEWAY,
                     requested_operations: frozenset | None = None,
                     strict_cleanup: bool = False):
    if type(strict_cleanup) is not bool:
        raise ValueError("SYNTHETIC_STRATEGY_CLEANUP_FLAG_INVALID")
    from bt_api_base.gateway_direct_protocol import GatewayCommandBinding

    from bt_api_py.gateway_direct_client import GatewayClientRoute
    from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys
    from tests.integration.test_iter63_gateway_direct_service import _client_config
    from tests.unit.test_iter63_gateway_direct_client import MemoryHost
    from tests.unit.test_iter63_gateway_strategy_delegated import Host as CancelHost

    class StrategyMemoryHost(MemoryHost):
        def save_cancel_plan(self, plan):
            return plan

        def load_cancel_plan(self, *_args):
            return None

        def save_cancel_record(self, *_args):
            return None

        def load_cancel_record(self, *_args):
            return None

    identity = strategy_identity(index)
    scope = WireScope(DOMAIN, account_ref=account, strategy_ref=identity.actor_ref, run_id=run_id)
    raw = _client_config(gateway_endpoint, identity).to_public_dict()
    raw["clients"]["gateway"]["settings"]["bindings"][0]["account_ref"] = account
    raw["clients"]["gateway"]["settings"]["bindings"][0]["service_id"] = peer.actor_ref
    return GatewayStrategyClientSession(
        parse_client_config(raw),
        binding=GatewayCommandBinding(journal_id, account, identity.actor_ref, identity.principal_ref),
        route=GatewayClientRoute("broker.direct_write", peer.actor_ref, "gateway-a", "scope-a",
                                 scope, identity),
        peer=peer,
        security_profiles={"security-a": ClientCurveKeys(
            keys["gateway_server"][0], *keys["strategy-" + str(index)])},
        host=StrategyMemoryHost(), delegated_cancel_host=CancelHost(),
        requested_operations=requested_operations or frozenset(
            {"direct.reserve", "direct.dispatch", "direct.command_status",
             "direct.delegated_command_status"}),
        strict_cleanup=strict_cleanup,
    )


def order_request(key: str, client_order_id: str, quantity: str = "1"):
    from bt_api_py._contracts.models import OrderRequest, OrderType, Side

    return OrderRequest(symbol="rb", side=Side.BUY, order_type=OrderType.LIMIT,
                        quantity=Decimal(quantity), price=Decimal("10"), account_id=ACCOUNT,
                        client_order_id=client_order_id, idempotency_key=key, offset="open",
                        quantity_unit="lots", exchange_id="SHFE")


def until(read, done, seconds=15.0, interval=0.05):
    deadline = time.monotonic() + seconds
    while True:
        value = read()
        if done(value) or time.monotonic() >= deadline:
            return value
        time.sleep(interval)


def _await_risk_stop_announcement(process: subprocess.Popen, config_path: Path,
                                  timeout: float) -> None:
    """Bind one source-announced owner-stop marker without exposing its name."""
    events = queue.Queue()

    def pump_stdout():
        try:
            stream = process.stdout
            if stream is None:
                return
            while True:
                raw = stream.readline()
                if not raw:
                    break
                try:
                    line = raw.decode("utf-8", errors="strict").rstrip("\r\n")
                except UnicodeError:
                    events.put(("invalid", None))
                    continue
                events.put(("line", line))
        finally:
            events.put(("eof", None))

    reader = threading.Thread(target=pump_stdout, name="iter72-risk-stop-output", daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    marker = None
    while marker is None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("risk_stop_announcement_timeout")
        try:
            kind, line = events.get(timeout=min(remaining, 0.1))
        except queue.Empty:
            if process.poll() is not None:
                raise AssertionError("risk_stop_announcement_missing") from None
            continue
        if kind != "line" or type(line) is not str or not line.startswith("RISK_SERVICE_STOP_FILE "):
            raise AssertionError("risk_stop_announcement_invalid")
        name = line.removeprefix("RISK_SERVICE_STOP_FILE ")
        if re.fullmatch(r"risk-[0-9a-f]{32}\.stop", name) is None:
            raise AssertionError("risk_stop_announcement_invalid")
        marker = Path(config_path).parent / name
        if marker.exists():
            raise AssertionError("risk_stop_marker_preexisting")
    process._iter72_risk_stop = {
        "marker": marker,
        "events": events,
        "reader": reader,
        "stopped_observed": False,
    }


def start_risk(config_path: Path, ready_file: Path, *, fault_point: str | None = None,
               extra_env: dict | None = None, launcher: list[str] | None = None,
               timeout: float = 90.0, owner_stop_file: bool = False) -> subprocess.Popen:
    """Start the Risk host CLI in its own process and wait for its ready file."""
    if type(owner_stop_file) is not bool:
        raise ValueError("SYNTHETIC_RISK_STOP_MODE_INVALID")
    if ready_file.exists():
        ready_file.unlink()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = SDK_PYTHONPATH + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.pop("BT_RISK_FAULT_POINT", None)
    if fault_point is not None:
        env["BT_RISK_FAULT_POINT"] = fault_point
    env.update(extra_env or {})
    command = launcher or [sys.executable, "-m", "bt_api_py.risk_service_host"]
    arguments = command + ["run", "--config", str(config_path), "--ready-file", str(ready_file)]
    if owner_stop_file:
        arguments.append("--enable-owner-stop-file")
    process = subprocess.Popen(
        arguments,
        cwd=str(SDK_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + timeout
    while not ready_file.exists():
        if process.poll() is not None:
            raise AssertionError(("risk host exited", process.returncode,
                                  process.communicate(timeout=10)))
        if time.monotonic() > deadline:
            process.kill()
            raise AssertionError(("risk host not ready", process.communicate(timeout=10)))
        time.sleep(0.02)
    if owner_stop_file:
        _await_risk_stop_announcement(process, config_path, min(timeout, 10.0))
    return process


def stop_risk(process: subprocess.Popen | None, timeout: float = 30.0) -> int | None:
    if process is None:
        return None
    if process.poll() is None:
        process.terminate()
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
            return None
    return process.returncode


def stop_risk_cooperatively(process: subprocess.Popen, timeout: float = 45.0) -> dict[str, bool | int]:
    """Request owner-only CLI shutdown and require its post-cleanup receipt."""
    control = getattr(process, "_iter72_risk_stop", None)
    if (type(control) is not dict or set(control) != {
            "marker", "events", "reader", "stopped_observed"}):
        raise RuntimeError("SYNTHETIC_RISK_COOPERATIVE_STOP_NOT_ENABLED")
    marker, events, reader = control["marker"], control["events"], control["reader"]
    if process.poll() is not None or marker.exists():
        raise RuntimeError("SYNTHETIC_RISK_COOPERATIVE_STOP_NOT_READY")
    marker.touch(exist_ok=False)
    _restrict_fixture_path(marker, mode=0o600)
    from bt_api_py._owner_stop_file import marker_requested

    if marker.stat().st_size != 0 or marker_requested(marker) is not True:
        raise RuntimeError("SYNTHETIC_RISK_STOP_MARKER_NOT_PROTECTED")
    try:
        exit_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Reap only this fixture-owned child on a failed cooperative shutdown;
        # the failure remains explicit and cannot be mistaken for acceptance.
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=5)
        raise RuntimeError("SYNTHETIC_RISK_COOPERATIVE_STOP_TIMEOUT_FORCED_REAP") from None
    deadline = time.monotonic() + 5.0
    stopped = False
    unexpected_lines = 0
    eof = False
    while not eof:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            kind, line = events.get(timeout=remaining)
        except queue.Empty:
            break
        if kind == "eof":
            eof = True
        elif kind == "line" and line == "RISK_SERVICE_STOPPED":
            stopped = True
        else:
            unexpected_lines = min(unexpected_lines + 1, 100)
    reader.join(timeout=max(0.0, deadline - time.monotonic()))
    if process.stderr is not None:
        process.stderr.read()
    control["stopped_observed"] = stopped
    result = {
        "owner_stop_announcement_observed": True,
        "marker_owner_only_and_empty": marker.stat().st_size == 0 and marker_requested(marker),
        "risk_cli_exit_zero": type(exit_code) is int and exit_code == 0,
        "post_cleanup_stopped_observed": stopped,
        "stdout_closed": eof and not reader.is_alive(),
        "unexpected_stdout_lines": unexpected_lines,
        "marker_present_after_exit": marker.is_file(),
    }
    if (result["risk_cli_exit_zero"] is not True
            or result["post_cleanup_stopped_observed"] is not True
            or result["stdout_closed"] is not True
            or result["unexpected_stdout_lines"] != 0):
        raise RuntimeError("SYNTHETIC_RISK_COOPERATIVE_STOP_UNCONFIRMED")
    if process.stderr is not None:
        while process.stderr.read(65536):
            pass
        process.stderr.close()
    if process.stdout is not None:
        process.stdout.close()
    return result


def next_message(messages, kind: str, timeout: float = 10.0):
    """The next Gateway message of ``kind``; fails on a Gateway error report."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("no Gateway message " + kind)
        item = messages.get(timeout=remaining)
        if item[0] == "error":
            raise AssertionError("\n".join(map(str, item)))
        if item[0] == kind:
            return item


def gateway_command(gateway_root: Path, strategy: str, request_key: str):
    """Read-only view of the Gateway journal row of one command (status, version)."""
    database = Path(gateway_root) / "journal.sqlite"
    with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True, timeout=5) as connection:
        return connection.execute(
            "SELECT status, version, reservation_id FROM direct_journal_commands "
            "WHERE account=? AND strategy=? AND request_key=?",
            (ACCOUNT, strategy, request_key),
        ).fetchall()


def gateway_command_snapshot(gateway_root: Path):
    """Finite synthetic journal projection for a same-process Gateway restart."""
    database = Path(gateway_root) / "journal.sqlite"
    with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True, timeout=5) as connection:
        return connection.execute(
            "SELECT strategy, request_key, status, version, reservation_id, native_identity_json "
            "FROM direct_journal_commands WHERE account=? ORDER BY strategy, request_key",
            (ACCOUNT,),
        ).fetchall()


class _GatewayObserverSnapshot:
    """Private registration values used only to derive a finite restart receipt."""

    __slots__ = ("epoch", "active_bindings", "consumer_owner", "event_count")

    def __init__(self, epoch, active_bindings, consumer_owner, event_count):
        self.epoch = epoch
        self.active_bindings = active_bindings
        self.consumer_owner = consumer_owner
        self.event_count = event_count

    def __repr__(self):
        return "<GatewayObserverSnapshot>"


def gateway_observer_snapshot(gateway_root: Path) -> _GatewayObserverSnapshot:
    """Read only the synthetic Gateway epoch, Risk observer binding, and event-owner row."""
    from bt_api_py.gateway_account_observer import (
        ACCOUNT_OBSERVER_OPERATIONS,
        account_observer_consumer_ref,
    )

    root = Path(gateway_root)
    registry_path = root / "registry.sqlite"
    journal_path = root / "journal.sqlite"
    registry_uri = registry_path.resolve().as_uri() + "?mode=ro"
    journal_uri = journal_path.resolve().as_uri() + "?mode=ro"
    now = time.time()
    with closing(sqlite3.connect(registry_uri, uri=True, timeout=1.0)) as registry:
        registry.row_factory = sqlite3.Row
        epoch_row = registry.execute(
            "SELECT value FROM registry_meta WHERE key='service_epoch'"
        ).fetchone()
        epoch = None if epoch_row is None else epoch_row["value"]
        rows = registry.execute(
            "SELECT registration_id, service_epoch, expires_at, operations_json "
            "FROM sessions WHERE principal=? AND instance_id=? AND port=? AND scope_ref=?",
            (RISK.principal_ref, "gateway-a", "service.account_read", "scope-a"),
        ).fetchall()
        active_bindings = []
        for row in rows:
            try:
                operations = json.loads(row["operations_json"])
                operation_set = frozenset(operations) if type(operations) is list else frozenset()
            except (TypeError, ValueError):
                continue
            if (row["service_epoch"] == epoch and type(row["expires_at"]) in (int, float)
                    and row["expires_at"] > now and type(operations) is list
                    and len(operation_set) == len(operations)
                    and operation_set == ACCOUNT_OBSERVER_OPERATIONS):
                active_bindings.append((row["registration_id"], row["service_epoch"]))

    consumer = account_observer_consumer_ref(
        principal_ref=RISK.principal_ref,
        identity=RISK,
        scope=WireScope(DOMAIN, account_ref=ACCOUNT),
    )
    with closing(sqlite3.connect(journal_uri, uri=True, timeout=1.0)) as journal:
        journal.row_factory = sqlite3.Row
        owner = journal.execute(
            "SELECT registration_id, service_epoch FROM account_event_consumer_owners "
            "WHERE account=? AND principal=? AND consumer=?",
            (ACCOUNT, RISK.principal_ref, consumer),
        ).fetchone()
        event_count = journal.execute(
            "SELECT count(*) FROM direct_execution_events"
        ).fetchone()[0]
    consumer_owner = None if owner is None else (owner["registration_id"], owner["service_epoch"])
    return _GatewayObserverSnapshot(epoch, tuple(active_bindings), consumer_owner, event_count)


def gateway_observer_snapshot_facts(snapshot: _GatewayObserverSnapshot) -> dict[str, bool | int]:
    """Project one private snapshot to registration/owner booleans and event count."""
    if type(snapshot) is not _GatewayObserverSnapshot:
        raise TypeError("INVALID_SYNTHETIC_OBSERVER_SNAPSHOT")
    owner = snapshot.consumer_owner
    return {
        "observer_binding_active": bool(snapshot.active_bindings),
        "consumer_owner_present": owner is not None,
        "consumer_owner_epoch_current": (
            owner is not None and type(snapshot.epoch) is str and owner[1] == snapshot.epoch
        ),
        "consumer_owner_matches_active_binding": (
            owner is not None and owner in snapshot.active_bindings
        ),
        "persisted_event_count": min(max(snapshot.event_count, 0), 10000),
    }


def risk_permit(risk_root: Path, request_key: str):
    database = Path(risk_root) / "state" / "risk.sqlite"
    with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True, timeout=5) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT state, gateway_state, reservation_id, code FROM risk_pretrade_permits "
            "WHERE request_key=?", (request_key,)
        ).fetchall()
    return [dict(row) for row in rows]
