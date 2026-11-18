"""Execution-to-Risk parent envelope adapter.

The Execution parent service owns the journal and the Gateway child path.  This
adapter translates its neutral parent payload into the Risk service's
``RiskParentView`` and maps the Risk parent result back to the Execution
service's parent-outcome vocabulary.  It grants nothing: the Risk service
re-authorizes the exact execution principal, strategy, account and route.
"""
from __future__ import annotations

import threading
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.risk_service_protocol import RiskParentView

from bt_api_py.risk_service_client import RiskParentCallResult, RiskServiceClient

# A child is released only after the parent's own Risk reservation was formally
# acknowledged (RESERVED). These finite codes keep a "never reserved" parent
# distinct from a reserved parent whose child forward path is not (yet) wired;
# both stay fail-closed UNKNOWN and never fabricate a Gateway result.
_PARENT_NOT_RESERVED = "risk_parent_not_reserved"
_CHILD_UNAVAILABLE = "risk_parent_child_unavailable"


class RiskParentPort:
    """The methods ``ParentSubmitService`` requires for a risk-selected parent.

    ``reserve_parent`` translates the neutral parent payload into a versioned
    ``RiskParentView`` (schema v2 with the absolute lease ``valid_until_ns``
    when the parent carries ``expires_at_ns``) and maps the Risk answer to
    ACCEPTED/REJECTED/UNKNOWN.  A RESERVED answer is recorded so child
    reserve/dispatch can be gated on it; anything else stays fail-closed.

    Child reserve/dispatch never fabricate a Gateway result.  They stay UNKNOWN
    both when the parent was not formally reserved and when the reserved
    parent's execution-origin child order path is unavailable; the codes above
    distinguish the two but neither is a dispatch authority.
    """

    def __init__(self, client: RiskServiceClient, *, route: str) -> None:
        if (not callable(getattr(client, "available", None))
                or not callable(getattr(client, "parent_reserve", None))
                or type(route) is not str or not route):
            raise ValueError("INVALID_RISK_PARENT_PORT")
        self._client, self._route = client, route
        # Formally acknowledged reservations (RESERVED) in this process, keyed
        # by parent_ref.  A REJECTED or UNKNOWN answer is never recorded.
        self._reserved: set[str] = set()
        self._reserved_lock = threading.Lock()

    def _parent_reserved(self, parent_ref: object) -> bool:
        with self._reserved_lock:
            return type(parent_ref) is str and parent_ref in self._reserved

    def available(self) -> bool:
        try:
            return self._client.available() is True
        except Exception:
            return False

    def _view(self, payload: Mapping) -> RiskParentView:
        if type(payload) is not dict:
            raise ValueError("INVALID_PARENT_PAYLOAD")
        spec, strategy = payload.get("spec"), payload.get("strategy")
        if type(spec) is not dict or type(strategy) is not dict:
            raise ValueError("INVALID_PARENT_PAYLOAD")
        instrument = MarketInstrumentKey.from_dict(spec.get("instrument"))
        days = spec.get("allowed_trading_days")
        if type(days) is not list or not days or any(type(day) is not str for day in days):
            raise ValueError("INVALID_PARENT_PAYLOAD")
        limit = spec.get("limit_price")
        try:
            total = Decimal(spec["quantity"])
            limit_value = None if limit is None else Decimal(limit)
        except (KeyError, InvalidOperation, TypeError):
            raise ValueError("INVALID_PARENT_PAYLOAD") from None
        # Versioned request: an absolute parent deadline (the intake lease
        # bound) upgrades the envelope to schema v2 and carries ``valid_until_ns``.
        expires_at_ns = spec.get("expires_at_ns")
        if expires_at_ns is not None and (
            type(expires_at_ns) is not int or not 0 < expires_at_ns < 2**63
        ):
            raise ValueError("INVALID_PARENT_PAYLOAD")
        view = RiskParentView(
            account_scope=spec["account_scope"],
            route=self._route,
            strategy_ref=strategy["strategy_ref"],
            parent_ref=spec["parent_ref"],
            instrument=instrument,
            side=str(spec["side"]).upper(),
            offset=None if spec.get("offset") is None else str(spec["offset"]).upper(),
            position_side=str(spec.get("position_side", "NET")).upper(),
            total_quantity=total,
            quantity_unit=spec["quantity_unit"],
            limit_price=limit_value,
            created_trading_day=spec["creation_trading_day"],
            valid_until_trading_day=max(days),
            schema_version=1 if expires_at_ns is None else 2,
            valid_until_ns=expires_at_ns,
        )
        run_id = strategy.get("run_id")
        client_run = getattr(getattr(self._client, "_route", None), "scope", None)
        if type(run_id) is str and client_run is not None and run_id != getattr(client_run, "run_id", run_id):
            raise ValueError("PARENT_RUN_MISMATCH")
        return view

    def reserve_parent(self, payload: Mapping) -> Mapping:
        try:
            view = self._view(payload)
        except Exception:
            return {"status": "REJECTED", "code": "risk_request_invalid"}
        result = self._client.parent_reserve(view)
        if type(result) is not RiskParentCallResult:
            return {"status": "UNKNOWN", "code": "risk_parent_unavailable"}
        if result.outcome == "ANSWERED" and result.parent_outcome == "RESERVED":
            with self._reserved_lock:
                self._reserved.add(view.parent_ref)
            return {"status": "ACCEPTED", "code": None}
        if result.outcome == "ANSWERED" and result.parent_outcome == "DENIED":
            return {"status": "REJECTED", "code": result.code or "risk_parent_denied"}
        return {"status": "UNKNOWN", "code": result.code or "risk_parent_unavailable"}

    def reserve_child(self, payload: Mapping) -> Mapping:
        if type(payload) is not dict or not self._parent_reserved(payload.get("parent_ref")):
            return {"status": "UNKNOWN", "code": _PARENT_NOT_RESERVED}
        # A formally reserved parent is not itself child dispatch authority:
        # the execution-origin risk.order.* child path is not part of this
        # adapter and must not fabricate a Gateway result.
        return {"status": "UNKNOWN", "code": _CHILD_UNAVAILABLE}

    def dispatch_child(self, payload: Mapping) -> Mapping:
        if type(payload) is not dict or not self._parent_reserved(payload.get("parent_ref")):
            return {"status": "UNKNOWN", "code": _PARENT_NOT_RESERVED}
        return {"status": "UNKNOWN", "code": _CHILD_UNAVAILABLE}
