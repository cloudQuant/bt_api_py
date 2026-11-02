"""Public, read-first contract for the official SimNow set1 execution path.

This adapter deliberately does not create an order journal or mint execution
authority. It delegates durable order state to ``BtApi``'s existing execution
session. Native writes stay closed until that session can revalidate a sealed
private configuration, a non-public credential-version binding, and a durable
CTP OrderRef mapping on every arm and write.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

from bt_api_py._contracts.models import (
    CancelOrderRequest,
    OrderRequest,
    OrderType,
    QueryOrderRequest,
    Side,
)

CTP_FUTURE = "CTP___FUTURE"
OFFICIAL_SET1_PROFILES = frozenset({"set1_group1", "set1_group2"})
_ACCOUNT_FINGERPRINT = re.compile(r"^acct_[0-9a-f]{16}$")
_TRADING_DAY = re.compile(r"^[0-9]{8}$")

from bt_api_ctp.simnow_execution_projection import (
    CtpSimNowCancelResult,
    CtpSimNowExecutionError,
    CtpSimNowOrderIdentity,
    CtpSimNowOrderRequest,
    CtpSimNowOrderResult,
    CtpSimNowQueryResult,
    CtpSimNowReadObservation,
    CtpSimNowSessionIdentity,
    _CTP_ORDER_REF,
    _MANAGED_INTENT_ID,
    _MANAGED_RUNTIME_ORDER_ID,
    _map_ctp_simnow_order_result_with_ref,
    _map_order_row,
    build_ctp_simnow_order_request,
    build_ctp_simnow_query_request,
    collect_ctp_simnow_read_certificate,
    project_ctp_simnow_open_orders,
    build_ctp_simnow_cancel_request as _provider_cancel_request,
    map_ctp_simnow_cancel_result,
    map_ctp_simnow_order_result,
)


def build_ctp_simnow_cancel_request(
    identity: CtpSimNowOrderIdentity,
    *,
    account_id: str,
    action_id: str,
    idempotency_key: str = "",
    managed_cancel_intent_id: str | None = None,
) -> CancelOrderRequest:
    """Use the provider's native mapping with the SDK's unified request type."""
    return _provider_cancel_request(
        identity,
        account_id=account_id,
        action_id=action_id,
        idempotency_key=idempotency_key,
        managed_cancel_intent_id=managed_cancel_intent_id,
        cancel_request_type=CancelOrderRequest,
    )


class CtpSimNowExecutionAdapter:
    """Public set1-only facade over ``BtApi``; native writes remain fail-closed.

    ``selected_profile`` must come from the caller's sealed runtime
    configuration.  The adapter verifies that exact profile against current
    SDK session evidence and never reads environment variables or substitutes
    another SimNow family member.

    The durable binding helper is an adapter seam, not a complete
    ``CtpTraderClientPort`` integration.  A managed order must first pass
    through ``build_order_request`` so the SDK journal has both the runtime
    order ID and managed intent.  The normal BtApi order-intent journal then
    moves that reservation from ``reserved`` to ``unresolved``.  A direct
    native TraderClient submit bypasses those steps; this adapter does not
    synthesize either transition and keeps native writes disabled.

    This facade accepts only official ``set1_group*`` profiles. The managed
    Backtrader port currently uses ``config_front_pair``; that profile mismatch
    is another explicit integration blocker, so this resolver must not be
    wired into that port as-is.
    """

    def __init__(self, api: Any, *, selected_profile: str, exchange_name: str = CTP_FUTURE):
        if selected_profile not in OFFICIAL_SET1_PROFILES:
            raise CtpSimNowExecutionError("ctp_simnow_set1_profile_required")
        if exchange_name != CTP_FUTURE:
            raise CtpSimNowExecutionError("ctp_simnow_exchange_scope_invalid")
        self._api = api
        self.exchange_name = exchange_name
        self.selected_profile = selected_profile
        self._require_scope()

    @property
    def write_admitted(self) -> bool:
        """Real writes are disabled until the SDK binds private credential state."""
        return False

    @property
    def write_blockers(self) -> tuple[str, ...]:
        """Unresolved SDK prerequisites for any SimNow native write."""
        return (
            "ctp_execution_credential_binding_unavailable",
            "ctp_native_order_ref_mapping_unavailable",
        )

    def _require_scope(self) -> tuple[CtpSimNowSessionIdentity, Mapping[str, Any]]:
        try:
            environment = self._api.get_environment_info(self.exchange_name)
            session = self._api.get_ctp_session_state(self.exchange_name)
            ledger = self._api.get_execution_identity(self.exchange_name)
        except Exception as exc:
            raise CtpSimNowExecutionError("ctp_simnow_public_identity_unavailable") from exc
        if (
            not isinstance(environment, Mapping)
            or environment.get("verified") is not True
            or environment.get("environment") != "demo"
            or environment.get("transport_mode") != "direct"
        ):
            raise CtpSimNowExecutionError("ctp_simnow_official_demo_required")
        if (
            not isinstance(session, Mapping)
            or session.get("environment_profile") != self.selected_profile
            or session.get("read_only_ready") is not True
            or session.get("auto_settlement_confirm") is not False
        ):
            raise CtpSimNowExecutionError("ctp_simnow_selected_profile_mismatch")
        if not isinstance(ledger, Mapping) or ledger.get("mode") != "direct":
            raise CtpSimNowExecutionError("ctp_simnow_sdk_execution_journal_required")
        account = str(session.get("account_fingerprint") or "").lower()
        if account and not account.startswith("acct_"):
            account = f"acct_{account}"
        trading_day = str(session.get("trading_day") or "")
        generation = session.get("connection_generation")
        if (
            not _ACCOUNT_FINGERPRINT.fullmatch(account)
            or not _TRADING_DAY.fullmatch(trading_day)
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation <= 0
        ):
            raise CtpSimNowExecutionError("ctp_simnow_session_identity_invalid")
        if ledger.get("account_fingerprint") != account:
            raise CtpSimNowExecutionError("ctp_simnow_journal_account_mismatch")
        return (
            CtpSimNowSessionIdentity(
                environment="demo",
                profile=self.selected_profile,
                account_fingerprint=account,
                trading_day=trading_day,
                connection_generation=generation,
            ),
            session,
        )

    def get_execution_identity(self) -> CtpSimNowSessionIdentity:
        return self._require_scope()[0]

    def build_order_request(
        self,
        request: CtpSimNowOrderRequest,
        *,
        budget_capability: Any = None,
    ) -> OrderRequest:
        """Build the typed SDK intent and durable ref reservation, without dispatching.

        The reservation remains ``reserved`` until BtApi records its order
        intent; construction alone does not imply or authorize provider I/O.
        """
        identity, _session = self._require_scope()
        ledger = self._api.get_execution_identity(self.exchange_name)
        account_id = str(ledger.get("account_id") or identity.account_fingerprint)
        client_order_id = request.client_order_id
        if request.managed_intent_id is not None:
            binding = self._reserve_managed_order_reference(
                request,
                identity=identity,
                account_id=account_id,
                budget_capability=budget_capability,
            )
            client_order_id = str(binding["ctp_order_ref"])
        return build_ctp_simnow_order_request(
            request, account_id=account_id, client_order_id=client_order_id,
            order_request_type=OrderRequest, side_type=Side, limit_order_type=OrderType.LIMIT,
        )

    def _runtime_order_bindings(self, runtime_order_id: str) -> tuple[Mapping[str, Any], ...]:
        getter = getattr(self._api, "get_runtime_order_bindings", None)
        if not callable(getter):
            raise CtpSimNowExecutionError("ctp_runtime_order_binding_unavailable")
        try:
            rows = getter(
                self.exchange_name,
                unresolved_only=False,
                runtime_order_id=runtime_order_id,
            )
        except Exception as exc:
            raise CtpSimNowExecutionError("ctp_runtime_order_binding_unavailable") from exc
        if not isinstance(rows, (tuple, list)) or len(rows) > 1:
            raise CtpSimNowExecutionError("ctp_runtime_order_binding_unavailable")
        if not rows:
            return ()
        if not isinstance(rows[0], Mapping):
            raise CtpSimNowExecutionError("ctp_runtime_order_binding_unavailable")
        return (rows[0],)

    @staticmethod
    def _validate_managed_order_binding(
        binding: Mapping[str, Any],
        *,
        runtime_order_id: str,
        managed_intent_id: str,
        instrument_id: str | None,
        account_id: str,
        identity: CtpSimNowSessionIdentity,
        allowed_statuses: frozenset[str],
    ) -> str:
        order_ref = binding.get("ctp_order_ref")
        if (
            binding.get("runtime_order_id") != runtime_order_id
            or binding.get("managed_intent_id") != managed_intent_id
            or not isinstance(managed_intent_id, str)
            or not managed_intent_id.isascii()
            or not _MANAGED_INTENT_ID.fullmatch(managed_intent_id)
            or not _MANAGED_RUNTIME_ORDER_ID.fullmatch(runtime_order_id)
            or (instrument_id is not None and binding.get("symbol") != instrument_id)
            or binding.get("connection_generation") != identity.connection_generation
            or binding.get("trading_day") != identity.trading_day
            or binding.get("status") not in allowed_statuses
            or (binding.get("account_id") is not None and binding.get("account_id") != account_id)
            or not isinstance(order_ref, str)
            or not _CTP_ORDER_REF.fullmatch(order_ref)
            or binding.get("client_order_id") != order_ref
        ):
            raise CtpSimNowExecutionError("ctp_runtime_order_binding_scope_mismatch")
        return order_ref

    def _reserve_managed_order_reference(
        self,
        request: CtpSimNowOrderRequest,
        *,
        identity: CtpSimNowSessionIdentity,
        account_id: str,
        budget_capability: Any,
    ) -> Mapping[str, Any]:
        runtime_order_id = request.runtime_order_id
        managed_intent_id = request.managed_intent_id
        if not runtime_order_id or not managed_intent_id:
            raise CtpSimNowExecutionError("ctp_managed_order_identity_required")
        rows = self._runtime_order_bindings(runtime_order_id)
        if not rows:
            raise CtpSimNowExecutionError("ctp_order_identity_reservation_required")
        if len(rows) != 1:
            raise CtpSimNowExecutionError("ctp_runtime_order_binding_unavailable")
        self._validate_managed_order_binding(
            rows[0],
            runtime_order_id=runtime_order_id,
            managed_intent_id=managed_intent_id,
            instrument_id=request.instrument_id,
            account_id=account_id,
            identity=identity,
            allowed_statuses=frozenset({"reserved"}),
        )
        if identity != self.get_execution_identity():
            raise CtpSimNowExecutionError("ctp_runtime_order_binding_scope_mismatch")
        return rows[0]

    def resolve_runtime_order_binding(
        self, runtime_order_id: str, reserve: bool
    ) -> Mapping[str, Any]:
        """Resolve the SDK journal row in the shape required by the managed CTP port.

        The port callback does not carry ``managed_intent_id``, so
        ``build_order_request`` must first create the durable reservation from
        the full typed request. Even with ``reserve=True``, this resolver only
        validates and returns that already-fsynced row; it never guesses an
        intent, creates a second identity source, or allocates from the native
        client's in-memory counter.
        """
        if type(reserve) is not bool:
            raise CtpSimNowExecutionError("ctp_runtime_order_binding_scope_mismatch")
        identity, _session = self._require_scope()
        if not isinstance(runtime_order_id, str) or not _MANAGED_RUNTIME_ORDER_ID.fullmatch(
            runtime_order_id
        ):
            raise CtpSimNowExecutionError("ctp_runtime_order_binding_scope_mismatch")
        ledger = self._api.get_execution_identity(self.exchange_name)
        account_id = str(ledger.get("account_id") or identity.account_fingerprint)
        rows = self._runtime_order_bindings(runtime_order_id)
        if len(rows) != 1:
            raise CtpSimNowExecutionError("ctp_runtime_order_binding_unavailable")
        binding = rows[0]
        managed_intent_id = binding.get("managed_intent_id")
        allowed_statuses = frozenset({"reserved"}) if reserve else frozenset({"unresolved"})
        order_ref = self._validate_managed_order_binding(
            binding,
            runtime_order_id=runtime_order_id,
            managed_intent_id=managed_intent_id,
            instrument_id=None,
            account_id=account_id,
            identity=identity,
            allowed_statuses=allowed_statuses,
        )
        if identity != self.get_execution_identity():
            raise CtpSimNowExecutionError("ctp_runtime_order_binding_scope_mismatch")
        return {
            "runtime_order_id": runtime_order_id,
            "connection_generation": identity.connection_generation,
            "trading_day": identity.trading_day,
            "ctp_order_ref": order_ref,
            "client_order_id": order_ref,
            "managed_intent_id": managed_intent_id,
        }

    def map_order_result(self, row: Any, request: CtpSimNowOrderRequest) -> CtpSimNowOrderResult:
        """Map an SDK order row only through the active durable identity scope."""
        if type(request) is not CtpSimNowOrderRequest:
            raise CtpSimNowExecutionError("ctp_native_order_request_invalid")
        identity, _session = self._require_scope()
        if request.managed_intent_id is None:
            return map_ctp_simnow_order_result(row, request, identity)
        if not request.runtime_order_id:
            raise CtpSimNowExecutionError("ctp_managed_order_identity_required")
        ledger = self._api.get_execution_identity(self.exchange_name)
        account_id = str(ledger.get("account_id") or identity.account_fingerprint)
        rows = self._runtime_order_bindings(request.runtime_order_id)
        if len(rows) != 1:
            raise CtpSimNowExecutionError("ctp_runtime_order_binding_unavailable")
        order_ref = self._validate_managed_order_binding(
            rows[0],
            runtime_order_id=request.runtime_order_id,
            managed_intent_id=request.managed_intent_id,
            instrument_id=request.instrument_id,
            account_id=account_id,
            identity=identity,
            allowed_statuses=frozenset({"unresolved"}),
        )
        if identity != self.get_execution_identity():
            raise CtpSimNowExecutionError("ctp_runtime_order_binding_scope_mismatch")
        return _map_ctp_simnow_order_result_with_ref(row, request, identity, order_ref)

    def build_cancel_request(
        self,
        identity: CtpSimNowOrderIdentity,
        *,
        action_id: str | None = None,
        managed_cancel_intent_id: str | None = None,
    ) -> CancelOrderRequest:
        current, _session = self._require_scope()
        if identity.trading_day != current.trading_day:
            raise CtpSimNowExecutionError("ctp_cancel_trading_day_mismatch")
        ledger = self._api.get_execution_identity(self.exchange_name)
        account_id = str(ledger.get("account_id") or current.account_fingerprint)
        if identity.managed_intent_id is not None:
            if not identity.runtime_order_id or not managed_cancel_intent_id:
                raise CtpSimNowExecutionError("ctp_managed_cancel_identity_required")
            rows = self._runtime_order_bindings(identity.runtime_order_id)
            if len(rows) != 1:
                raise CtpSimNowExecutionError("ctp_runtime_order_binding_unavailable")
            bound_ref = self._validate_managed_order_binding(
                rows[0],
                runtime_order_id=identity.runtime_order_id,
                managed_intent_id=identity.managed_intent_id,
                instrument_id=identity.instrument_id,
                account_id=account_id,
                identity=current,
                allowed_statuses=frozenset({"unresolved"}),
            )
            if identity.order_ref != bound_ref or identity.client_order_id not in (
                None,
                bound_ref,
            ):
                raise CtpSimNowExecutionError("ctp_runtime_cancel_reference_mismatch")
            if action_id is not None and action_id != managed_cancel_intent_id:
                raise CtpSimNowExecutionError("ctp_runtime_action_binding_mismatch")
            # The managed action and cancel intent are one I9 identity. A
            # candidate-local next-action allocator cannot substitute for it.
            action_id = managed_cancel_intent_id
            if current != self.get_execution_identity():
                raise CtpSimNowExecutionError("ctp_runtime_cancel_scope_mismatch")
        elif managed_cancel_intent_id is not None:
            raise CtpSimNowExecutionError("ctp_managed_cancel_identity_mismatch")
        if not action_id:
            raise ValueError("action_id must be non-empty and trimmed")
        return build_ctp_simnow_cancel_request(
            identity,
            account_id=account_id,
            action_id=action_id,
            managed_cancel_intent_id=managed_cancel_intent_id,
        )

    def arm_from_approval(self, *_: Any, **__: Any) -> None:
        """Keep actual arming disabled until private config and key bindings exist."""
        self._require_scope()
        raise CtpSimNowExecutionError("ctp_execution_credential_binding_unavailable")

    def submit_order_insert(self, request: CtpSimNowOrderRequest) -> CtpSimNowOrderResult:
        """Keep native dispatch closed until private credentials are reviewed."""
        self._require_scope()
        if request.managed_intent_id is not None:
            raise CtpSimNowExecutionError("ctp_execution_credential_binding_unavailable")
        self.build_order_request(request)
        raise CtpSimNowExecutionError("ctp_native_order_ref_mapping_unavailable")

    def submit_order_action(
        self,
        identity: CtpSimNowOrderIdentity,
        action_id: str | None = None,
        *,
        managed_cancel_intent_id: str | None = None,
    ) -> CtpSimNowCancelResult:
        """Refuse dispatch while approval context omits private credential binding."""
        self._require_scope()
        self.build_cancel_request(
            identity,
            action_id=action_id,
            managed_cancel_intent_id=managed_cancel_intent_id,
        )
        raise CtpSimNowExecutionError("ctp_execution_credential_binding_unavailable")

    def query_order(self, identity: CtpSimNowOrderIdentity) -> CtpSimNowOrderResult:
        """Resolve an order through the SDK's public typed query and journal."""
        current, _session = self._require_scope()
        if identity.trading_day != current.trading_day:
            raise CtpSimNowExecutionError("ctp_order_query_trading_day_mismatch")
        ledger = self._api.get_execution_identity(self.exchange_name)
        request = build_ctp_simnow_query_request(
            identity,
            account_id=str(ledger.get("account_id") or current.account_fingerprint),
            query_request_type=QueryOrderRequest,
        )
        result = self._api.query_order(self.exchange_name, request, normalized=True)
        return _map_order_row(
            result,
            fallback=identity,
            identity=current,
            expected_client_order_id=identity.client_order_id,
        )

    def query_account_open_orders(self) -> CtpSimNowQueryResult:
        """Return observed rows without claiming account-wide completeness.

        A terminal response for one CTP query does not certify account-wide or
        pagination completeness. ``complete`` is therefore always false.
        """
        identity, _session = self._require_scope()
        result = self._api.query_ctp_result(self.exchange_name, "orders")
        return project_ctp_simnow_open_orders(result, identity)

    def query_native_read_observation(
        self,
        *,
        instrument_id: str,
        exchange_id: str,
        hedge_flag: str,
    ) -> CtpSimNowReadObservation:
        """Capture seven same-session native reads as non-authorizing evidence.

        The native certificate proves request provenance, terminal completion,
        explicit filters, and stable payloads for these reads. It does not prove
        an atomic snapshot or account-wide open-order coverage.
        """

        identity, _session = self._require_scope()
        feeds = getattr(self._api, "exchange_feeds", None)
        feed = feeds.get(self.exchange_name) if isinstance(feeds, Mapping) else None
        client = getattr(feed, "trader_client", None)
        if client is None:
            raise CtpSimNowExecutionError("ctp_native_query_client_unavailable")

        certificate = collect_ctp_simnow_read_certificate(
            client,
            instrument_id=instrument_id,
            exchange_id=exchange_id,
            hedge_flag=hedge_flag,
            query_ctp_result=lambda query_type, **kwargs: self._api.query_ctp_result(
                self.exchange_name, query_type, **kwargs
            ),
        )

        current_identity, _session = self._require_scope()
        expected_account_fingerprint = (
            identity.account_fingerprint[5:]
            if identity.account_fingerprint.startswith("acct_")
            else identity.account_fingerprint
        )
        if (
            current_identity != identity
            or certificate.connection_generation != identity.connection_generation
            or certificate.trading_day != identity.trading_day
            or certificate.account_fingerprint_sha256
            != sha256(expected_account_fingerprint.encode("utf-8")).hexdigest()
            or getattr(feeds.get(self.exchange_name), "trader_client", None) is not client
        ):
            raise CtpSimNowExecutionError("ctp_native_query_session_identity_mismatch")
        return CtpSimNowReadObservation(identity, certificate)


__all__ = [
    "CTP_FUTURE",
    "OFFICIAL_SET1_PROFILES",
    "CtpSimNowExecutionAdapter",
    "CtpSimNowExecutionError",
    "CtpSimNowSessionIdentity",
    "CtpSimNowOrderRequest",
    "CtpSimNowOrderIdentity",
    "CtpSimNowOrderResult",
    "CtpSimNowCancelResult",
    "CtpSimNowQueryResult",
    "CtpSimNowReadObservation",
    "build_ctp_simnow_cancel_request",
    "map_ctp_simnow_cancel_result",
    "map_ctp_simnow_order_result",
]
