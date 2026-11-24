"""Owner-thread Execution -> Risk v2 -> Gateway child dispatcher for ParentServiceHost.

Drives durably committed ``execution_parent_records`` through:
1. ``RiskParentPort.reserve_parent`` (W2: parent is already committed in SQLite).
2. Deterministic single-child PASS planning persisted in ``execution_parent_children``
   (``PREPARED``) BEFORE calling Risk.
3. ``RiskParentPort.reserve_child`` (``risk.order.reserve.v2`` -> Risk PretradeEngine
   + R-05 self-trade cut + Gateway ``direct.delegated_reserve``).
4. ``RiskParentPort.dispatch_child`` (``risk.order.dispatch.v2`` -> Risk PretradeEngine
   L3 claim + Gateway ``direct.delegated_dispatch`` -> CTP TD ``ReqOrderInsert``).

Every uncertain or failed step is persisted durably as ``UNKNOWN`` / ``REJECTED``
and is never blindly retried.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.risk_service_protocol import RiskOrderView
from bt_api_base.service_protocol import WireIdentity, WireScope
from bt_api_execution.account_journal_host import AccountJournalHost
from bt_api_execution.contracts import ExecutionScope, canonical_json
from bt_api_execution.store import SqliteExecutionStore

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.execution_risk_client import ExecutionRiskClientConfig, ExecutionRiskServiceClient
from bt_api_py.gateway_order_contract import decode_gateway_order_request
from bt_api_py.monitor_service_config import read_bounded
from bt_api_py.risk_parent_port import RiskParentPort
from bt_api_py.service_runtime import ClientCurveKeys

_CLOSE_OFFSETS = frozenset({"close", "close_today", "close_yesterday"})
_UNKNOWN_RECONCILE_INTERVAL_SECONDS = 5.0
_UNKNOWN_NOT_FOUND_GRACE_NS = 30_000_000_000


@dataclass(frozen=True, slots=True)
class ParentDispatchConfig:
    """Explicit opt-in configuration for Execution -> Risk v2 child dispatch."""

    route: str
    risk_endpoint: str
    risk_service_id: str
    risk_instance_id: str
    risk_scope_ref: str
    risk_peer: WireIdentity
    risk_server_public: Path
    client_public: Path
    client_secret: Path
    poll_interval_seconds: float = 0.2


def _load_curve_key(path: Path) -> bytes:
    import zmq.utils.z85 as z85

    data = read_bounded(path, 4096, protected=True).strip()
    if len(data) != 40 or len(z85.decode(data)) != 32:
        raise ValueError("INVALID_DISPATCH_KEYS")
    return data


def _build_child_order(
    *,
    spec: dict[str, Any],
    parent_id: str,
    child_id: str,
    strategy_ref: str,
    route: str,
) -> tuple[str, OrderRequest, str, RiskOrderView]:
    """Construct a deterministic canonical PASS child OrderRequest and RiskOrderView."""
    instrument = MarketInstrumentKey.from_dict(spec["instrument"])
    digest = hashlib.sha256(f"{parent_id}:{child_id}".encode("utf-8")).hexdigest()[:24]
    request_key = f"exec-child-{digest}"
    side_str = str(spec["side"]).lower()
    side_enum = Side.BUY if side_str == "buy" else Side.SELL
    offset = str(spec["offset"]).lower()
    reduce_only = offset in _CLOSE_OFFSETS
    quantity = Decimal(str(spec["quantity"]))
    limit_price = Decimal(str(spec["limit_price"]))
    account_scope = str(spec["account_scope"])
    quantity_unit = str(spec["quantity_unit"])
    time_in_force = str(spec["time_in_force"])
    position_side = spec.get("position_side")

    order_req = OrderRequest(
        symbol=instrument.native_symbol,
        side=side_enum,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        account_id=account_scope,
        client_order_id=request_key,
        idempotency_key=request_key,
        price=limit_price,
        quantity_unit=quantity_unit,
        time_in_force=time_in_force,
        offset=offset,
        position_side=None if position_side is None else str(position_side),
        reduce_only=reduce_only,
        exchange_id=instrument.venue,
    )
    payload_dict = order_req.to_dict()
    canonical = canonical_gateway_payload(payload_dict)
    # Fail closed if canonical payload would not round-trip through Gateway's contract.
    decoded = decode_gateway_order_request(json.loads(canonical), account_scope)
    if decoded.idempotency_key != request_key:
        raise ValueError("CHILD_PAYLOAD_INVALID")

    child_view = RiskOrderView(
        account_scope=account_scope,
        route=route,
        strategy_ref=strategy_ref,
        origin="execution",
        request_key=request_key,
        instrument=instrument,
        side=side_str.upper(),
        offset=offset.upper(),
        position_side="NET" if position_side is None else str(position_side).upper(),
        quantity=quantity,
        quantity_unit=quantity_unit,
        order_type="LIMIT",
        limit_price=limit_price,
        time_in_force=time_in_force,
        parent_ref=parent_id,
        child_id=child_id,
    )
    return request_key, order_req, canonical, child_view


class ExecutionParentDispatcher:
    """Owner-thread dispatcher bridging ParentServiceHost's journal to Risk v2 -> Gateway."""

    def __init__(
        self,
        *,
        dispatch_config: ParentDispatchConfig,
        service_id: str,
        identity: WireIdentity,
        wire_scope: WireScope,
        local_scope: ExecutionScope,
        journal: AccountJournalHost,
    ) -> None:
        self._config = dispatch_config
        self._identity = identity
        self._wire_scope = wire_scope
        self._local_scope = local_scope
        self._journal = journal
        self._parent_scope_key = ExecutionScope(
            local_scope.provider,
            local_scope.environment,
            local_scope.account_ref,
            local_scope.strategy_id,
        ).key
        curve = ClientCurveKeys(
            _load_curve_key(dispatch_config.risk_server_public),
            _load_curve_key(dispatch_config.client_public),
            _load_curve_key(dispatch_config.client_secret),
        )
        client_cfg = ExecutionRiskClientConfig(
            service_id=service_id,
            identity=identity,
            scope=wire_scope,
            risk_service_id=dispatch_config.risk_service_id,
            risk_instance_id=dispatch_config.risk_instance_id,
            risk_scope_ref=dispatch_config.risk_scope_ref,
            risk_endpoint=dispatch_config.risk_endpoint,
            risk_peer=dispatch_config.risk_peer,
            curve_keys=curve,
        )
        self._client = ExecutionRiskServiceClient(client_cfg)
        self._risk_port = RiskParentPort(self._client, route=dispatch_config.route)
        self._next_poll = 0.0
        self._next_reconcile = 0.0
        self._started = False

    def start(self, *, timeout_seconds: float = 15.0) -> None:
        if self._started:
            return
        self._client.start(timeout_seconds=timeout_seconds)
        self._started = True

    def close(self, *, timeout_seconds: float = 5.0) -> None:
        self._started = False
        self._client.close(timeout_seconds=timeout_seconds)

    @property
    def risk_available(self) -> bool:
        return self._started and self._risk_port.available()

    def _emit_log(self, event: str, **fields: Any) -> None:
        payload = {"event": event, "strategy": self._wire_scope.strategy_ref, **fields}
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True), file=sys.stderr, flush=True)

    def _fetch_pending_parents(self) -> list[dict[str, Any]]:
        try:
            with self._journal.intake_scope(nonblocking=True) as store:
                if not store._lock.acquire(blocking=False):
                    return []
                try:
                    with SqliteExecutionStore._read_transaction(store) as cursor:
                        rows = cursor.execute(
                            "SELECT parent_id, request_json, risk_state, gateway_state, cancel_state "
                            "FROM execution_parent_records "
                            "WHERE scope_key=? AND cancel_state='NONE' "
                            "AND (risk_state='PENDING' OR (risk_state='ACCEPTED' AND gateway_state='PENDING')) "
                            "ORDER BY created_at_ns ASC LIMIT 16",
                            (self._parent_scope_key,),
                        ).fetchall()
                        return [dict(row) for row in rows]
                finally:
                    store._lock.release()
        except Exception:
            return []

    def _update_parent_risk(self, parent_id: str, *, risk_state: str, receipt: dict[str, Any]) -> None:
        now_ns = self._journal.intake_now_ns()
        with self._journal.intake_scope(nonblocking=False) as store:
            with store._lock:
                with SqliteExecutionStore._transaction(store) as cursor:
                    cursor.execute(
                        "UPDATE execution_parent_records "
                        "SET risk_state=?, risk_receipt_json=?, updated_at_ns=? "
                        "WHERE scope_key=? AND parent_id=? AND risk_state='PENDING'",
                        (
                            risk_state,
                            canonical_json(receipt),
                            now_ns,
                            self._parent_scope_key,
                            parent_id,
                        ),
                    )

    def _prepare_child_row(
        self, parent_id: str, child_id: str, child_record: dict[str, Any], quantity: str
    ) -> bool:
        """Persist child row in PREPARED state before contacting Risk; False if already present."""
        encoded = canonical_json(child_record)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now_ns = self._journal.intake_now_ns()
        with self._journal.intake_scope(nonblocking=False) as store:
            with store._lock:
                with SqliteExecutionStore._transaction(store) as cursor:
                    existing = cursor.execute(
                        "SELECT state FROM execution_parent_children "
                        "WHERE scope_key=? AND parent_id=? AND child_id=?",
                        (self._parent_scope_key, parent_id, child_id),
                    ).fetchone()
                    if existing is not None:
                        return False
                    cursor.execute(
                        "INSERT INTO execution_parent_children "
                        "(scope_key, parent_id, child_id, request_json, request_sha256, "
                        "quantity, filled_quantity, state, updated_at_ns) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            self._parent_scope_key,
                            parent_id,
                            child_id,
                            encoded,
                            digest,
                            quantity,
                            "0",
                            "PREPARED",
                            now_ns,
                        ),
                    )
                    return True

    def _update_child_and_parent(
        self,
        parent_id: str,
        child_id: str,
        *,
        child_state: str,
        gateway_state: str | None = None,
        gateway_receipt: dict[str, Any] | None = None,
    ) -> None:
        now_ns = self._journal.intake_now_ns()
        with self._journal.intake_scope(nonblocking=False) as store:
            with store._lock:
                with SqliteExecutionStore._transaction(store) as cursor:
                    cursor.execute(
                        "UPDATE execution_parent_children SET state=?, updated_at_ns=? "
                        "WHERE scope_key=? AND parent_id=? AND child_id=?",
                        (child_state, now_ns, self._parent_scope_key, parent_id, child_id),
                    )
                    if gateway_state is not None:
                        cursor.execute(
                            "UPDATE execution_parent_records "
                            "SET gateway_state=?, gateway_receipt_json=?, updated_at_ns=? "
                            "WHERE scope_key=? AND parent_id=? AND gateway_state='PENDING'",
                            (
                                gateway_state,
                                None if gateway_receipt is None else canonical_json(gateway_receipt),
                                now_ns,
                                self._parent_scope_key,
                                parent_id,
                            ),
                        )

    def _process_parent(self, row: dict[str, Any]) -> None:
        parent_id = str(row["parent_id"])
        spec = json.loads(row["request_json"])
        risk_state = str(row["risk_state"])
        gateway_state = str(row["gateway_state"])

        if risk_state == "PENDING":
            payload = {
                "spec": spec,
                "strategy": {
                    "strategy_ref": self._wire_scope.strategy_ref,
                    "run_id": self._wire_scope.run_id,
                },
            }
            reply = dict(self._risk_port.reserve_parent(payload))
            status = str(reply.get("status") or "UNKNOWN").upper()
            new_risk_state = (
                "ACCEPTED" if status == "ACCEPTED" else "REJECTED" if status == "REJECTED" else "UNKNOWN"
            )
            self._update_parent_risk(parent_id, risk_state=new_risk_state, receipt=reply)
            self._emit_log(
                "EXECUTION_PARENT_RISK_RESULT",
                parent_id=parent_id,
                risk_state=new_risk_state,
                code=reply.get("code"),
            )
            if new_risk_state != "ACCEPTED":
                return
            risk_state = "ACCEPTED"

        if risk_state != "ACCEPTED" or gateway_state != "PENDING":
            return

        if not self._risk_port._parent_reserved(parent_id):
            parent_status = self._client.parent_status(parent_id)
            if parent_status.outcome == "ANSWERED" and parent_status.parent_outcome == "RESERVED":
                self._risk_port.record_reserved_parent(parent_id)
            elif parent_status.outcome == "ANSWERED" and parent_status.parent_outcome in (
                "DENIED",
                "RELEASED",
            ):
                now_ns = self._journal.intake_now_ns()
                receipt = {
                    "stage": "parent_status_recheck",
                    "status": "REJECTED",
                    "code": parent_status.code or f"risk_parent_{parent_status.parent_outcome.lower()}",
                }
                with self._journal.intake_scope(nonblocking=False) as store:
                    with store._lock:
                        with SqliteExecutionStore._transaction(store) as cursor:
                            cursor.execute(
                                "UPDATE execution_parent_records "
                                "SET gateway_state='REJECTED', gateway_receipt_json=?, updated_at_ns=? "
                                "WHERE scope_key=? AND parent_id=? AND gateway_state='PENDING'",
                                (
                                    canonical_json(receipt),
                                    now_ns,
                                    self._parent_scope_key,
                                    parent_id,
                                ),
                            )
                self._emit_log(
                    "EXECUTION_PARENT_RECONCILED",
                    parent_id=parent_id,
                    gateway_state="REJECTED",
                    source_outcome=parent_status.parent_outcome,
                    code=receipt["code"],
                )
                return
            else:
                return

        child_id = f"{parent_id}:c0"
        try:
            request_key, _req, canonical_payload, child_view = _build_child_order(
                spec=spec,
                parent_id=parent_id,
                child_id=child_id,
                strategy_ref=self._wire_scope.strategy_ref,
                route=self._config.route,
            )
        except Exception as exc:
            self._update_child_and_parent(
                parent_id,
                child_id,
                child_state="TERMINAL",
                gateway_state="REJECTED",
                gateway_receipt={"status": "REJECTED", "code": "CHILD_BUILD_INVALID"},
            )
            self._emit_log(
                "EXECUTION_CHILD_REJECTED",
                parent_id=parent_id,
                child_id=child_id,
                code=f"CHILD_BUILD_INVALID:{type(exc).__name__}",
            )
            return

        child_record = {
            "child_id": child_id,
            "parent_ref": parent_id,
            "request_key": request_key,
            "quantity": str(spec["quantity"]),
            "canonical_payload": canonical_payload,
        }
        if not self._prepare_child_row(parent_id, child_id, child_record, str(spec["quantity"])):
            # Fail closed: a child row already existed (never resend an uncertain child).
            self._update_child_and_parent(
                parent_id,
                child_id,
                child_state="UNKNOWN",
                gateway_state="UNKNOWN",
                gateway_receipt={"status": "UNKNOWN", "code": "CHILD_ALREADY_EXISTS"},
            )
            return

        reserve_reply = dict(
            self._risk_port.reserve_child(
                {
                    "parent_ref": parent_id,
                    "child_view": child_view,
                    "canonical_payload": canonical_payload,
                    "run_id": self._wire_scope.run_id,
                }
            )
        )
        r_status = str(reserve_reply.get("status") or "UNKNOWN").upper()
        if r_status != "ACCEPTED":
            term_child = "TERMINAL" if r_status == "REJECTED" else "UNKNOWN"
            term_gw = "REJECTED" if r_status == "REJECTED" else "UNKNOWN"
            self._update_child_and_parent(
                parent_id,
                child_id,
                child_state=term_child,
                gateway_state=term_gw,
                gateway_receipt={"stage": "reserve_child", **reserve_reply},
            )
            self._emit_log(
                "EXECUTION_CHILD_REJECTED" if term_gw == "REJECTED" else "EXECUTION_CHILD_UNKNOWN",
                parent_id=parent_id,
                child_id=child_id,
                stage="reserve_child",
                code=reserve_reply.get("code"),
                symbol=child_view.instrument.native_symbol,
            )
            return

        permit_id = reserve_reply.get("permit_id")
        reservation_id = reserve_reply.get("reservation_id")
        view_digest = reserve_reply.get("view_digest")
        if not all(isinstance(v, str) and v for v in (permit_id, reservation_id, view_digest)):
            self._update_child_and_parent(
                parent_id,
                child_id,
                child_state="UNKNOWN",
                gateway_state="UNKNOWN",
                gateway_receipt={"stage": "reserve_child", "code": "RESERVE_FIELDS_MISSING"},
            )
            return

        # Durable in-flight marker BEFORE dispatch network call.
        self._update_child_and_parent(parent_id, child_id, child_state="UNKNOWN")

        dispatch_reply = dict(
            self._risk_port.dispatch_child(
                {
                    "parent_ref": parent_id,
                    "request_key": request_key,
                    "permit_id": permit_id,
                    "reservation_id": reservation_id,
                    "view_digest": view_digest,
                }
            )
        )
        d_status = str(dispatch_reply.get("status") or "UNKNOWN").upper()
        if d_status == "DISPATCHED":
            self._update_child_and_parent(
                parent_id,
                child_id,
                child_state="ACTIVE",
                gateway_state="ACCEPTED",
                gateway_receipt={
                    "stage": "dispatch_child",
                    "request_key": request_key,
                    "permit_id": permit_id,
                    "reservation_id": reservation_id,
                    **dispatch_reply,
                },
            )
            self._emit_log(
                "EXECUTION_CHILD_DISPATCHED",
                parent_id=parent_id,
                child_id=child_id,
                request_key=request_key,
                symbol=child_view.instrument.native_symbol,
                side=child_view.side,
                offset=child_view.offset,
                quantity=str(child_view.quantity),
                limit_price=str(child_view.limit_price),
                permit_id=permit_id,
                reservation_id=reservation_id,
            )
        elif d_status == "REJECTED":
            self._update_child_and_parent(
                parent_id,
                child_id,
                child_state="TERMINAL",
                gateway_state="REJECTED",
                gateway_receipt={"stage": "dispatch_child", **dispatch_reply},
            )
            self._emit_log(
                "EXECUTION_CHILD_REJECTED",
                parent_id=parent_id,
                child_id=child_id,
                stage="dispatch_child",
                code=dispatch_reply.get("code"),
                symbol=child_view.instrument.native_symbol,
            )
        else:
            self._update_child_and_parent(
                parent_id,
                child_id,
                child_state="UNKNOWN",
                gateway_state="UNKNOWN",
                gateway_receipt={"stage": "dispatch_child", **dispatch_reply},
            )
            self._emit_log(
                "EXECUTION_CHILD_UNKNOWN",
                parent_id=parent_id,
                child_id=child_id,
                stage="dispatch_child",
                code=dispatch_reply.get("code"),
                symbol=child_view.instrument.native_symbol,
            )

    def _fetch_unknown_parents(self) -> list[dict[str, Any]]:
        try:
            with self._journal.intake_scope(nonblocking=True) as store:
                if not store._lock.acquire(blocking=False):
                    return []
                try:
                    with SqliteExecutionStore._read_transaction(store) as cursor:
                        rows = cursor.execute(
                            "SELECT parent_id, risk_state, gateway_state, gateway_receipt_json, updated_at_ns "
                            "FROM execution_parent_records "
                            "WHERE scope_key=? AND cancel_state='NONE' "
                            "AND ((risk_state='UNKNOWN' AND gateway_state='PENDING') "
                            "OR (risk_state='ACCEPTED' AND gateway_state='UNKNOWN')) "
                            "ORDER BY created_at_ns ASC LIMIT 16",
                            (self._parent_scope_key,),
                        ).fetchall()
                        return [dict(row) for row in rows]
                finally:
                    store._lock.release()
        except Exception:
            return []

    def _reconcile_parent_risk_cas(
        self, parent_id: str, *, risk_state: str, receipt: dict[str, Any]
    ) -> bool:
        now_ns = self._journal.intake_now_ns()
        with self._journal.intake_scope(nonblocking=False) as store:
            with store._lock:
                with SqliteExecutionStore._transaction(store) as cursor:
                    cur = cursor.execute(
                        "UPDATE execution_parent_records "
                        "SET risk_state=?, risk_receipt_json=?, updated_at_ns=? "
                        "WHERE scope_key=? AND parent_id=? "
                        "AND risk_state='UNKNOWN' AND gateway_state='PENDING' AND cancel_state='NONE'",
                        (
                            risk_state,
                            canonical_json(receipt),
                            now_ns,
                            self._parent_scope_key,
                            parent_id,
                        ),
                    )
                    return cur.rowcount > 0

    def _reconcile_child_reserve_rejected_cas(
        self, parent_id: str, child_id: str, *, receipt: dict[str, Any]
    ) -> bool:
        now_ns = self._journal.intake_now_ns()
        with self._journal.intake_scope(nonblocking=False) as store:
            with store._lock:
                with SqliteExecutionStore._transaction(store) as cursor:
                    child = cursor.execute(
                        "SELECT state FROM execution_parent_children "
                        "WHERE scope_key=? AND parent_id=? AND child_id=?",
                        (self._parent_scope_key, parent_id, child_id),
                    ).fetchone()
                    if child is None or child["state"] != "UNKNOWN":
                        return False
                    cur = cursor.execute(
                        "UPDATE execution_parent_records "
                        "SET gateway_state='REJECTED', gateway_receipt_json=?, updated_at_ns=? "
                        "WHERE scope_key=? AND parent_id=? "
                        "AND risk_state='ACCEPTED' AND gateway_state='UNKNOWN' AND cancel_state='NONE'",
                        (
                            canonical_json(receipt),
                            now_ns,
                            self._parent_scope_key,
                            parent_id,
                        ),
                    )
                    if cur.rowcount <= 0:
                        return False
                    cursor.execute(
                        "UPDATE execution_parent_children SET state='TERMINAL', updated_at_ns=? "
                        "WHERE scope_key=? AND parent_id=? AND child_id=? AND state='UNKNOWN'",
                        (now_ns, self._parent_scope_key, parent_id, child_id),
                    )
                    return True

    def _reconcile_unknown_parents(self) -> int:
        rows = self._fetch_unknown_parents()
        if not rows:
            return 0
        reconciled = 0
        now_ns = self._journal.intake_now_ns()
        for row in rows:
            parent_id = str(row["parent_id"])
            risk_state = str(row["risk_state"])
            gateway_state = str(row["gateway_state"])
            updated_at_ns = int(row["updated_at_ns"])
            try:
                if risk_state == "UNKNOWN" and gateway_state == "PENDING":
                    res = self._client.parent_status(parent_id)
                    if res.outcome != "ANSWERED":
                        continue
                    if res.parent_outcome in ("DENIED", "RELEASED"):
                        receipt = {
                            "status": "REJECTED",
                            "code": res.code or f"risk_parent_{res.parent_outcome.lower()}_reconciled",
                            "reconciled_from": res.parent_outcome,
                        }
                        if self._reconcile_parent_risk_cas(
                            parent_id, risk_state="REJECTED", receipt=receipt
                        ):
                            reconciled += 1
                            self._emit_log(
                                "EXECUTION_PARENT_RECONCILED",
                                parent_id=parent_id,
                                risk_state="REJECTED",
                                source_outcome=res.parent_outcome,
                                code=receipt["code"],
                            )
                    elif res.parent_outcome == "RESERVED":
                        self._risk_port.record_reserved_parent(parent_id)
                        receipt = {"status": "ACCEPTED", "code": None, "reconciled_from": "RESERVED"}
                        if self._reconcile_parent_risk_cas(
                            parent_id, risk_state="ACCEPTED", receipt=receipt
                        ):
                            reconciled += 1
                            self._emit_log(
                                "EXECUTION_PARENT_RECONCILED",
                                parent_id=parent_id,
                                risk_state="ACCEPTED",
                                source_outcome="RESERVED",
                            )
                    elif (
                        res.parent_outcome == "NOT_FOUND"
                        and now_ns - updated_at_ns >= _UNKNOWN_NOT_FOUND_GRACE_NS
                    ):
                        receipt = {
                            "status": "REJECTED",
                            "code": "risk_parent_not_found_reconciled",
                            "reconciled_from": "NOT_FOUND",
                        }
                        if self._reconcile_parent_risk_cas(
                            parent_id, risk_state="REJECTED", receipt=receipt
                        ):
                            reconciled += 1
                            self._emit_log(
                                "EXECUTION_PARENT_RECONCILED",
                                parent_id=parent_id,
                                risk_state="REJECTED",
                                source_outcome="NOT_FOUND",
                                code=receipt["code"],
                            )
                elif risk_state == "ACCEPTED" and gateway_state == "UNKNOWN":
                    try:
                        gw_receipt = json.loads(row.get("gateway_receipt_json") or "{}")
                    except Exception:
                        gw_receipt = {}
                    if type(gw_receipt) is not dict or gw_receipt.get("stage") != "reserve_child":
                        continue
                    child_id = f"{parent_id}:c0"
                    digest = hashlib.sha256(f"{parent_id}:{child_id}".encode("utf-8")).hexdigest()[:24]
                    request_key = f"exec-child-{digest}"
                    res = self._client.order_status(request_key)
                    if res.outcome in ("DENIED", "RELEASED", "EXPIRED"):
                        receipt = {
                            "stage": "reserve_child",
                            "status": "REJECTED",
                            "code": res.code or f"risk_child_{res.outcome.lower()}_reconciled",
                            "reconciled_from": res.outcome,
                        }
                        if self._reconcile_child_reserve_rejected_cas(
                            parent_id, child_id, receipt=receipt
                        ):
                            reconciled += 1
                            self._emit_log(
                                "EXECUTION_CHILD_RECONCILED",
                                parent_id=parent_id,
                                child_id=child_id,
                                gateway_state="REJECTED",
                                source_outcome=res.outcome,
                                code=receipt["code"],
                            )
                    elif (
                        res.outcome == "NOT_FOUND"
                        and now_ns - updated_at_ns >= _UNKNOWN_NOT_FOUND_GRACE_NS
                    ):
                        receipt = {
                            "stage": "reserve_child",
                            "status": "REJECTED",
                            "code": "risk_child_not_found_reconciled",
                            "reconciled_from": "NOT_FOUND",
                        }
                        if self._reconcile_child_reserve_rejected_cas(
                            parent_id, child_id, receipt=receipt
                        ):
                            reconciled += 1
                            self._emit_log(
                                "EXECUTION_CHILD_RECONCILED",
                                parent_id=parent_id,
                                child_id=child_id,
                                gateway_state="REJECTED",
                                source_outcome="NOT_FOUND",
                                code=receipt["code"],
                            )
            except Exception as exc:
                self._emit_log(
                    "EXECUTION_RECONCILE_STEP_ERROR",
                    parent_id=parent_id,
                    error=type(exc).__name__,
                )
        return reconciled

    def step(self) -> int:
        """Process pending parents if Risk is connected; returns count processed."""
        if not self._started:
            return 0
        now = time.monotonic()
        if now < self._next_poll:
            return 0
        self._next_poll = now + self._config.poll_interval_seconds
        if not self._risk_port.available():
            return 0
        processed = 0
        if now >= self._next_reconcile:
            self._next_reconcile = now + _UNKNOWN_RECONCILE_INTERVAL_SECONDS
            processed += self._reconcile_unknown_parents()
        rows = self._fetch_pending_parents()
        for row in rows:
            try:
                self._process_parent(row)
                processed += 1
            except Exception as exc:
                self._emit_log(
                    "EXECUTION_DISPATCH_STEP_ERROR",
                    parent_id=str(row.get("parent_id")),
                    error=type(exc).__name__,
                )
        return processed


__all__ = ["ExecutionParentDispatcher", "ParentDispatchConfig"]
