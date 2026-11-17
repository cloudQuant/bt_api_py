"""Independent SERVICE-only ProductID observations on the original CTP owner."""

from __future__ import annotations

import threading
from datetime import UTC, date

from bt_api_base.service_protocol import WireRole, WireScope
from bt_api_ctp.gateway_session import CtpGatewaySession

from bt_api_py.gateway_direct_owner import GatewayDirectOwner
from bt_api_py.gateway_instrument_product_codec import (
    BEGIN,
    CANCEL,
    OPERATIONS,
    SCHEMA,
    STATUS,
    validate_snapshot,
)
from bt_api_py.gateway_instrument_product_config import InstrumentProductGrant
from bt_api_py.gateway_instrument_service import GatewayInstrumentService
from bt_api_py.service_registry import ServiceCallContext


class GatewayInstrumentProductService(GatewayInstrumentService):
    """Separate job table and registration ACL, sharing only the query engine."""

    _begin_operation, _status_operation, _cancel_operation = BEGIN, STATUS, CANCEL
    _operations = OPERATIONS
    _registration_port = "service.instrument_product_read"

    def __init__(self, owner, session, journal, grants):
        if (
            type(owner) is not GatewayDirectOwner
            or type(session) is not CtpGatewaySession
            or not owner.is_bound_to(journal)
            or owner.binding() is None
            or owner.binding().account_scope != journal.account_scope
            or session._owner_thread is not owner._thread
            or session._account_key.account_key_id != journal.account_scope
            or session._service_generation != owner.binding().generation
            or type(grants) is not frozenset
            or not grants
            or len(grants) > 4096
            or any(
                type(g) is not InstrumentProductGrant or g.account_ref != journal.account_scope
                for g in grants
            )
            or len({g.principal_ref for g in grants}) != len(grants)
        ):
            raise ValueError("INVALID_INSTRUMENT_PRODUCT_SERVICE")
        self._owner, self._session, self._journal = owner, session, journal
        self._generation = owner.binding().generation
        self._grants = {g.principal_ref: g for g in grants}
        self._lock, self._jobs, self._closed, self._cursor = threading.RLock(), {}, False, 0
        owner.claim_service_port("instrument_product", self)

    def matches(self, binding):
        return (
            getattr(binding, "_owner", None) is self._owner
            and getattr(binding, "_journal", None) is self._journal
            and self._owner.is_bound_to(self._journal)
        )

    def _authorize(self, context):
        if type(context) is not ServiceCallContext:
            raise ValueError("SCOPE_DENIED")
        grant = self._grants.get(context.principal_ref)
        if (
            grant is None
            or context.identity.role is not WireRole.SERVICE
            or context.identity.principal_ref != grant.principal_ref
            or context.identity.actor_ref != grant.actor_ref
            or context.identity.delegate_ref is not None
            or context.scope != WireScope(grant.execution_domain_ref, account_ref=grant.account_ref)
            or not context.registration_id
        ):
            raise ValueError("SCOPE_DENIED")

    def _authorize_instrument(self, context, instrument):
        if instrument not in self._grants[context.principal_ref].instruments:
            raise ValueError("INSTRUMENT_DENIED")

    def _snapshot(self, job, obs, remaining):
        if obs.instrument != job.instrument or obs.completed_at_utc.utcoffset() is None:
            raise ValueError("SOURCE_UNAVAILABLE")
        value = {
            "schema": SCHEMA,
            "instrument": job.instrument.to_dict(),
            "product_id": obs.product_id,
            "session_generation": self._generation,
            "connection_generation": obs.connection_generation,
            "trading_day": date.fromisoformat(obs.trading_day).isoformat(),
            "completed_at_utc": obs.completed_at_utc.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "remaining_seconds": remaining,
        }
        return validate_snapshot(value, expected_instrument=job.instrument)
