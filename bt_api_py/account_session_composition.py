"""Provider-neutral composition for account-session SDK capabilities.

Provider packages register exact-route factories here. BT receives forwarding
facades that expose only ``bt_api_base`` DTOs; selected provider port objects,
client sessions, authorization values, and native rows stay inside the SDK.
Missing or failed optional capabilities return finite unavailable projections
and never fall back to another provider or a direct client method.
"""

from __future__ import annotations

import threading
import weakref
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import metadata as importlib_metadata

from bt_api_base.account_session_protocol import (
    AccountPreflightRequest,
    AccountPreflightSnapshot,
    AccountSessionFacade,
    AccountSessionPort,
    AccountSessionSnapshot,
    SettlementConfirmationFacade,
    SettlementConfirmationPort,
    SettlementConfirmationReceipt,
    SettlementConfirmationRequest,
    SettlementConfirmationOutcome,
    SettlementVerificationReceipt,
)
from bt_api_base.account_session_registry_protocol import (
    AccountSessionFactory,
    SelectedAccountSessionRoute,
    SettlementConfirmationFactory,
)


class AccountSessionCompositionError(RuntimeError):
    """Finite error for invalid account-session route registration or claims."""

    _CODES = frozenset(
        {
            "ACCOUNT_SESSION_DISCOVERY_FAILED",
            "ACCOUNT_SESSION_ENTRY_POINT_CONFLICT",
            "ACCOUNT_SESSION_ENTRY_POINT_INVALID",
            "ACCOUNT_SESSION_ENTRY_POINT_SCOPE_INVALID",
            "ACCOUNT_SESSION_FACTORY_INVALID",
            "ACCOUNT_SESSION_IMPORT_FAILED",
            "ACCOUNT_SESSION_PORT_UNAVAILABLE",
            "ACCOUNT_SESSION_PROVIDER_CONFLICT",
            "ACCOUNT_SESSION_REGISTRATION_FAILED",
            "ACCOUNT_SESSION_REGISTRY_INVALID",
            "ACCOUNT_SESSION_ROUTE_INVALID",
            "ACCOUNT_SESSION_ROUTE_ALREADY_REGISTERED",
            "ACCOUNT_SESSION_ROUTE_RESOLUTION_FAILED",
            "SETTLEMENT_CONFIRMATION_AUTHORIZATION_REJECTED",
            "SETTLEMENT_CONFIRMATION_CAPABILITY_UNAVAILABLE",
            "SETTLEMENT_CONFIRMATION_CLAIM_CAPACITY",
            "SETTLEMENT_CONFIRMATION_CLAIM_INVALID",
            "SETTLEMENT_CONFIRMATION_FACTORY_INVALID",
            "SETTLEMENT_CONFIRMATION_PORT_UNAVAILABLE",
            "SETTLEMENT_CONFIRMATION_ROUTE_MISMATCH",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self._CODES else "ACCOUNT_SESSION_PORT_UNAVAILABLE"
        super().__init__(self.code)


AccountSessionRouteResolver = Callable[
    [str], SelectedAccountSessionRoute | None
]
ACCOUNT_SESSION_PROVIDER_ENTRY_POINT_GROUP = "bt_api.account_session_providers"


@dataclass(frozen=True, slots=True)
class _RegisteredFactories:
    account_session: AccountSessionFactory | None
    settlement_confirmation: SettlementConfirmationFactory | None


class AccountSessionRouteRegistry:
    """Immutable exact-route factory registry for optional session features."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._factories: dict[str, _RegisteredFactories] = {}
        self._entry_point_sources: dict[str, tuple[str, str, str] | None] = {}

    def register(
        self,
        exchange_name: str,
        account_session_factory: AccountSessionFactory | None,
        settlement_factory: SettlementConfirmationFactory | None = None,
    ) -> None:
        """Register optional account and settlement ports per exact route."""

        _require_route_text(exchange_name)
        if account_session_factory is not None and not callable(account_session_factory):
            raise AccountSessionCompositionError("ACCOUNT_SESSION_FACTORY_INVALID")
        if settlement_factory is not None and not callable(settlement_factory):
            raise AccountSessionCompositionError("SETTLEMENT_CONFIRMATION_FACTORY_INVALID")
        if account_session_factory is None and settlement_factory is None:
            raise AccountSessionCompositionError("ACCOUNT_SESSION_FACTORY_INVALID")
        with self._lock:
            self._register_factory(
                exchange_name,
                _RegisteredFactories(
                    account_session=account_session_factory,
                    settlement_confirmation=settlement_factory,
                ),
                entry_point_source=None,
            )

    def has_route(self, exchange_name: str) -> bool:
        _require_route_text(exchange_name)
        with self._lock:
            return exchange_name in self._factories

    def _get(self, exchange_name: str) -> _RegisteredFactories | None:
        with self._lock:
            return self._factories.get(exchange_name)

    def _register_factory(
        self,
        exchange_name: str,
        factories: _RegisteredFactories,
        *,
        entry_point_source: tuple[str, str, str] | None,
    ) -> None:
        if exchange_name in self._factories:
            raise AccountSessionCompositionError(
                "ACCOUNT_SESSION_ROUTE_ALREADY_REGISTERED"
            )
        self._factories[exchange_name] = factories
        self._entry_point_sources[exchange_name] = entry_point_source

    def _register_entry_point_factory(
        self,
        exchange_name: str,
        factories: _RegisteredFactories,
        source: tuple[str, str, str],
    ) -> None:
        with self._lock:
            self._register_factory(
                exchange_name,
                factories,
                entry_point_source=source,
            )

    def _entry_point_source(
        self,
        exchange_name: str,
    ) -> tuple[str, str, str] | None:
        with self._lock:
            return self._entry_point_sources.get(exchange_name)

    def _registered_routes(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._factories))


class AccountSessionRouter(AccountSessionFacade, SettlementConfirmationFacade):
    """Resolve one selected SDK route and expose only neutral port wrappers."""

    def __init__(
        self,
        registry: AccountSessionRouteRegistry | None,
        route_resolver: AccountSessionRouteResolver,
    ) -> None:
        registry = _select_registry(registry)
        if not callable(route_resolver):
            raise AccountSessionCompositionError("ACCOUNT_SESSION_ROUTE_RESOLUTION_FAILED")
        self._registry = registry
        self._route_resolver = route_resolver
        self._lock = threading.RLock()
        self._cache: dict[
            str,
            tuple[
                SelectedAccountSessionRoute,
                _AccountSessionPortView | _UnavailableAccountSessionPort,
                _SettlementConfirmationPortView | _UnavailableSettlementConfirmationPort,
                _RegisteredFactories,
            ],
        ] = {}
        # Keep only a weak reference to the exact session DTO returned by this
        # router. The selected route and wrapper are retained only for the
        # latest snapshot on each route; they contain no account rows or
        # credentials. Query-facade validation uses this issuance record to
        # reject caller-constructed DTOs and stale route/session bindings.
        self._issued_session_snapshots: dict[
            str,
            tuple[
                object,
                SelectedAccountSessionRoute,
                _AccountSessionPortView,
            ],
        ] = {}
    def account_session(self, exchange_name: str) -> AccountSessionPort:
        """Return a neutral read-only account-session port for an exact route."""

        return self._ports(exchange_name)[0]

    def settlement_confirmation(
        self,
        exchange_name: str,
    ) -> SettlementConfirmationPort:
        """Return a separate neutral write port, unavailable by default."""

        return self._ports(exchange_name)[1]

    def account_session_snapshot(self, exchange_name: str) -> AccountSessionSnapshot:
        """Read one exact route's account-session snapshot inside the SDK."""

        _require_route_text(exchange_name)
        port = self.account_session(exchange_name)
        try:
            snapshot = port.session_snapshot()
        except Exception:
            return _unavailable_session_snapshot(
                exchange_name, "account_session_read_outcome_unknown"
            )
        if (
            type(snapshot) is not AccountSessionSnapshot
            or snapshot.exchange_name != exchange_name
        ):
            return _unavailable_session_snapshot(
                exchange_name, "account_session_projection_invalid"
            )
        if (
            snapshot.observed_at is not None
            and snapshot.observed_at > datetime.now(timezone.utc)
        ):
            return _unavailable_session_snapshot(
                exchange_name, "account_session_observation_time_invalid"
            )
        if not self._account_session_port_is_current(exchange_name, port):
            return _unavailable_session_snapshot(
                exchange_name, "account_session_route_stale"
            )
        if snapshot.available is True and not self._record_issued_session_snapshot(
            exchange_name, port, snapshot
        ):
            return _unavailable_session_snapshot(
                exchange_name, "account_session_snapshot_binding_unavailable"
            )
        return snapshot

    def validate_account_session_snapshot_binding(
        self,
        snapshot: AccountSessionSnapshot,
        *,
        expected_client: object,
        expected_environment: str | None,
    ) -> bool:
        """Validate an exact router-issued session DTO against one selected route.

        This is an SDK currentness check, not provider authentication or write
        authority. It requires the same exact route client and account-session
        port that issued ``snapshot``, re-reads the current neutral session
        observation, and rejects a replaced route, port, or session state.
        """

        if (
            type(snapshot) is not AccountSessionSnapshot
            or expected_client is None
            or (expected_environment is not None and type(expected_environment) is not str)
        ):
            return False
        exchange_name = snapshot.exchange_name
        with self._lock:
            binding = self._issued_session_snapshots.get(exchange_name)
        if binding is None:
            return False
        snapshot_ref, issued_route, issued_port = binding
        try:
            if snapshot_ref() is not snapshot:
                return False
        except Exception:
            return False
        if (
            issued_route.client is not expected_client
            or issued_route.environment != expected_environment
            or type(issued_port) is not _AccountSessionPortView
        ):
            return False

        try:
            current_route = self._route_resolver(exchange_name)
        except Exception:
            return False
        if (
            type(current_route) is not SelectedAccountSessionRoute
            or not _same_route(issued_route, current_route)
        ):
            return False
        try:
            current_port = self.account_session(exchange_name)
        except Exception:
            return False
        if current_port is not issued_port:
            return False

        try:
            current_snapshot = issued_port.session_snapshot()
        except Exception:
            return False
        if not _same_session_snapshot_state(snapshot, current_snapshot):
            return False
        if not self._account_session_port_is_current(exchange_name, issued_port):
            return False
        try:
            final_route = self._route_resolver(exchange_name)
        except Exception:
            return False
        if type(final_route) is not SelectedAccountSessionRoute or not _same_route(
            issued_route, final_route
        ):
            return False
        with self._lock:
            return self._issued_session_snapshots.get(exchange_name) is binding

    def _record_issued_session_snapshot(
        self,
        exchange_name: str,
        port: AccountSessionPort,
        snapshot: AccountSessionSnapshot,
    ) -> bool:
        if type(port) is not _AccountSessionPortView:
            return False
        try:
            route = self._route_resolver(exchange_name)
        except Exception:
            return False
        if type(route) is not SelectedAccountSessionRoute:
            return False
        try:
            snapshot_ref = weakref.ref(snapshot)
        except TypeError:
            return False
        with self._lock:
            cached = self._cache.get(exchange_name)
            if (
                cached is None
                or cached[1] is not port
                or not _same_route(cached[0], route)
            ):
                return False
            self._issued_session_snapshots[exchange_name] = (
                snapshot_ref,
                cached[0],
                port,
            )
            return True

    def account_preflight(
        self,
        request: AccountPreflightRequest,
    ) -> AccountPreflightSnapshot:
        """Return route-bound readiness evidence after SDK lease validation."""

        if type(request) is not AccountPreflightRequest:
            raise TypeError("request must be an AccountPreflightRequest")
        now = datetime.now(timezone.utc)
        if request.deadline <= now:
            return _unavailable_preflight(request, "account_preflight_deadline_elapsed")
        port = self.account_session(request.exchange_name)
        try:
            snapshot = port.preflight(request)
        except Exception:
            return _unavailable_preflight(
                request, "account_preflight_outcome_unknown"
            )
        if (
            type(snapshot) is not AccountPreflightSnapshot
            or snapshot.request != request
            or snapshot.request.exchange_name != request.exchange_name
        ):
            return _unavailable_preflight(
                request, "account_preflight_projection_invalid"
            )
        if snapshot.observed_at is not None and snapshot.observed_at > datetime.now(
            timezone.utc
        ):
            return _unavailable_preflight(
                request, "account_preflight_observation_time_invalid"
            )
        if snapshot.ready is True:
            try:
                valid = port.validate_preflight(
                    snapshot,
                    now=datetime.now(timezone.utc),
                )
            except Exception:
                valid = False
            if valid is not True:
                return _unavailable_preflight(
                    request, "account_preflight_evidence_stale_or_invalid"
                )
        if not self._account_session_port_is_current(request.exchange_name, port):
            return _unavailable_preflight(
                request, "account_preflight_route_stale"
            )
        return snapshot

    def verify_account_settlement(
        self,
        exchange_name: str,
        *,
        deadline: datetime,
    ) -> SettlementVerificationReceipt:
        """Perform and validate one route-bound read-only settlement readback."""

        _require_route_text(exchange_name)
        _require_utc(deadline, "deadline")
        if deadline <= datetime.now(timezone.utc):
            raise ValueError("deadline must be in the future")
        port = self.account_session(exchange_name)
        try:
            receipt = port.verify_settlement(deadline=deadline)
        except Exception:
            return _unavailable_settlement_verification(
                exchange_name, "settlement_readback_outcome_unknown"
            )
        if (
            type(receipt) is not SettlementVerificationReceipt
            or receipt.exchange_name != exchange_name
        ):
            return _unavailable_settlement_verification(
                exchange_name, "settlement_readback_projection_invalid"
            )
        now = datetime.now(timezone.utc)
        if receipt.complete and (
            receipt.observed_at is None
            or receipt.observed_at > deadline
            or receipt.observed_at > now
        ):
            return _unavailable_settlement_verification(
                exchange_name, "settlement_readback_observation_time_invalid"
            )
        if not self._account_session_port_is_current(exchange_name, port):
            return _unavailable_settlement_verification(
                exchange_name, "settlement_readback_route_stale"
            )
        return receipt

    def confirm_settlement_once(
        self,
        exchange_name: str,
        request: SettlementConfirmationRequest,
        *,
        authorization: object,
    ) -> SettlementConfirmationOutcome:
        """Keep the exact route's one-shot settlement workflow in the SDK."""

        _require_route_text(exchange_name)
        if type(request) is not SettlementConfirmationRequest:
            raise TypeError("request must be a SettlementConfirmationRequest")
        if request.exchange_name != exchange_name:
            raise AccountSessionCompositionError(
                "SETTLEMENT_CONFIRMATION_ROUTE_MISMATCH"
            )
        if authorization is None:
            raise AccountSessionCompositionError(
                "SETTLEMENT_CONFIRMATION_AUTHORIZATION_REJECTED"
            )

        port = self.settlement_confirmation(exchange_name)
        try:
            claim = port.claim_settlement_confirmation(
                request, authorization=authorization
            )
        except AccountSessionCompositionError:
            raise
        except Exception:
            raise AccountSessionCompositionError(
                "SETTLEMENT_CONFIRMATION_AUTHORIZATION_REJECTED"
            ) from None
        if claim is None:
            raise AccountSessionCompositionError(
                "SETTLEMENT_CONFIRMATION_CLAIM_INVALID"
            )

        blockers: list[str] = []
        try:
            receipt = port.confirm_settlement(
                request,
                claim=claim,
                authorization=authorization,
            )
        except Exception:
            receipt = None

        valid_receipt = (
            type(receipt) is SettlementConfirmationReceipt
            and receipt.request is request
        )
        if not valid_receipt:
            receipt = _unknown_settlement_confirmation(
                request, "settlement_confirmation_outcome_unknown"
            )
            blockers.append("settlement_confirmation_receipt_unavailable")

        try:
            sdk_verified = (
                port.verify_confirmation(
                    receipt,
                    now=datetime.now(timezone.utc),
                )
                is True
            )
        except Exception:
            sdk_verified = False

        verified = bool(
            valid_receipt
            and receipt.disposition == "accepted"
            and sdk_verified is True
        )
        if not verified and not blockers:
            blockers.append(
                "settlement_confirmation_readback_unverified"
                if receipt.disposition == "accepted"
                else "settlement_confirmation_not_accepted"
            )
        return SettlementConfirmationOutcome(
            receipt=receipt,
            verified=verified,
            blockers=tuple(blockers),
        )

    def _ports(
        self,
        exchange_name: str,
    ) -> tuple[
        _AccountSessionPortView | _UnavailableAccountSessionPort,
        _SettlementConfirmationPortView | _UnavailableSettlementConfirmationPort,
    ]:
        _require_route_text(exchange_name)
        try:
            route = self._route_resolver(exchange_name)
        except Exception:
            return (
                _UnavailableAccountSessionPort(
                    exchange_name, "account_session_route_resolution_failed"
                ),
                _UnavailableSettlementConfirmationPort(
                    exchange_name, "settlement_confirmation_route_resolution_failed"
                ),
            )
        if type(route) is not SelectedAccountSessionRoute or route.exchange_name != exchange_name:
            return (
                _UnavailableAccountSessionPort(
                    exchange_name, "account_session_route_unavailable"
                ),
                _UnavailableSettlementConfirmationPort(
                    exchange_name, "settlement_confirmation_route_unavailable"
                ),
            )

        provider_error = self._load_selected_provider(exchange_name)
        if provider_error is not None:
            return (
                _UnavailableAccountSessionPort(exchange_name, provider_error),
                _UnavailableSettlementConfirmationPort(
                    exchange_name,
                    provider_error.replace(
                        "account_session_", "settlement_confirmation_", 1
                    )
                ),
            )
        factories = self._registry._get(exchange_name)
        if factories is None:
            return (
                _UnavailableAccountSessionPort(
                    exchange_name, "account_session_route_unregistered"
                ),
                _UnavailableSettlementConfirmationPort(
                    exchange_name,
                    "settlement_confirmation_route_unregistered"
                ),
            )
        with self._lock:
            cached = self._cache.get(exchange_name)
            if cached is not None and (
                not _same_route(cached[0], route) or cached[3] is not factories
            ):
                self._issued_session_snapshots.pop(exchange_name, None)
            if (
                cached is not None
                and _same_route(cached[0], route)
                and cached[3] is factories
            ):
                account_view, settlement_view = cached[1], cached[2]
                if (
                    isinstance(account_view, _UnavailableAccountSessionPort)
                    and factories.account_session is not None
                ):
                    try:
                        provider_port = factories.account_session(route)
                        if not isinstance(provider_port, AccountSessionPort):
                            raise TypeError
                        account_view = _AccountSessionPortView(
                            exchange_name, provider_port
                        )
                    except Exception:
                        pass
                if (
                    isinstance(settlement_view, _UnavailableSettlementConfirmationPort)
                    and factories.settlement_confirmation is not None
                ):
                    try:
                        provider_port = factories.settlement_confirmation(route)
                        if not isinstance(provider_port, SettlementConfirmationPort):
                            raise TypeError
                        settlement_view = _SettlementConfirmationPortView(
                            exchange_name, provider_port
                        )
                    except Exception:
                        pass
                self._cache[exchange_name] = (
                    route,
                    account_view,
                    settlement_view,
                    factories,
                )
                return (account_view, settlement_view)

            account_view: _AccountSessionPortView | _UnavailableAccountSessionPort
            settlement_view: (
                _SettlementConfirmationPortView | _UnavailableSettlementConfirmationPort
            )
            account_factory = factories.account_session
            if account_factory is None:
                account_view = _UnavailableAccountSessionPort(
                    exchange_name, "account_session_capability_unavailable"
                )
            else:
                try:
                    provider_port = account_factory(route)
                    if not isinstance(provider_port, AccountSessionPort):
                        raise TypeError
                    account_view = _AccountSessionPortView(exchange_name, provider_port)
                except Exception:
                    account_view = _UnavailableAccountSessionPort(
                        exchange_name, "account_session_provider_unavailable"
                    )

            settlement_factory = factories.settlement_confirmation
            if settlement_factory is None:
                settlement_view = _UnavailableSettlementConfirmationPort(
                    exchange_name, "settlement_confirmation_capability_unavailable"
                )
            else:
                try:
                    provider_port = settlement_factory(route)
                    if not isinstance(provider_port, SettlementConfirmationPort):
                        raise TypeError
                    settlement_view = _SettlementConfirmationPortView(
                        exchange_name, provider_port
                    )
                except Exception:
                    settlement_view = _UnavailableSettlementConfirmationPort(
                        exchange_name,
                        "settlement_confirmation_provider_unavailable"
                    )

            if not isinstance(account_view, _UnavailableAccountSessionPort) or not isinstance(
                settlement_view, _UnavailableSettlementConfirmationPort
            ):
                self._cache[exchange_name] = (
                    route,
                    account_view,
                    settlement_view,
                    factories,
                )
            return (account_view, settlement_view)

    def _account_session_port_is_current(
        self,
        exchange_name: str,
        expected: _AccountSessionPortView | _UnavailableAccountSessionPort,
    ) -> bool:
        """Re-resolve the route and require the same cached view after a read."""

        if isinstance(expected, _UnavailableAccountSessionPort):
            return True
        if type(expected) is not _AccountSessionPortView:
            return False
        try:
            current = self.account_session(exchange_name)
        except Exception:
            return False
        return current is expected

    def _load_selected_provider(self, exchange_name: str) -> str | None:
        entry_points = _route_entry_points(exchange_name)
        if entry_points is None:
            return "account_session_discovery_failed"
        if len(entry_points) > 1:
            return "account_session_entry_point_conflict"
        if not entry_points:
            return (
                None
                if self._registry.has_route(exchange_name)
                else "account_session_route_unregistered"
            )

        entry_point = entry_points[0]
        try:
            source = _entry_point_identity(entry_point, exchange_name)
        except AccountSessionCompositionError:
            return "account_session_entry_point_invalid"
        with self._lock:
            if self._registry.has_route(exchange_name):
                if self._registry._entry_point_source(exchange_name) == source:
                    return None
                return "account_session_provider_conflict"
            try:
                registrar = entry_point.load()
            except Exception:
                return "account_session_import_failed"
            if not callable(registrar):
                return "account_session_entry_point_invalid"
            isolated = AccountSessionRouteRegistry()
            try:
                registrar(isolated)
            except Exception:
                return "account_session_registration_failed"
            if isolated._registered_routes() != (exchange_name,):
                return "account_session_entry_point_scope_invalid"
            factories = isolated._get(exchange_name)
            if factories is None:
                return "account_session_entry_point_scope_invalid"
            try:
                self._registry._register_entry_point_factory(
                    exchange_name,
                    factories,
                    source,
                )
            except AccountSessionCompositionError:
                return "account_session_provider_conflict"
        return None


class _AccountSessionPortView(AccountSessionPort):
    __slots__ = ("_exchange_name", "__provider_port")

    def __init__(self, exchange_name: str, provider_port: AccountSessionPort) -> None:
        self._exchange_name = exchange_name
        self.__provider_port = provider_port

    def session_snapshot(self) -> AccountSessionSnapshot:
        try:
            result = self.__provider_port.session_snapshot()
        except Exception:
            return _unavailable_session_snapshot(
                self._exchange_name, "account_session_read_outcome_unknown"
            )
        if type(result) is not AccountSessionSnapshot or result.exchange_name != self._exchange_name:
            return _unavailable_session_snapshot(
                self._exchange_name, "account_session_projection_invalid"
            )
        return result

    def preflight(self, request: AccountPreflightRequest) -> AccountPreflightSnapshot:
        if type(request) is not AccountPreflightRequest:
            raise TypeError("request must be an AccountPreflightRequest")
        if request.exchange_name != self._exchange_name:
            return _unavailable_preflight(request, "account_session_route_mismatch")
        try:
            result = self.__provider_port.preflight(request)
        except Exception:
            return _unavailable_preflight(request, "account_preflight_outcome_unknown")
        if type(result) is not AccountPreflightSnapshot or result.request != request:
            return _unavailable_preflight(request, "account_preflight_projection_invalid")
        return result

    def verify_settlement(self, *, deadline: datetime) -> SettlementVerificationReceipt:
        _require_utc(deadline, "deadline")
        if deadline <= datetime.now(timezone.utc):
            return _unavailable_settlement_verification(
                self._exchange_name, "settlement_deadline_elapsed"
            )
        try:
            result = self.__provider_port.verify_settlement(deadline=deadline)
        except Exception:
            return _unavailable_settlement_verification(
                self._exchange_name, "settlement_readback_outcome_unknown"
            )
        if (
            type(result) is not SettlementVerificationReceipt
            or result.exchange_name != self._exchange_name
        ):
            return _unavailable_settlement_verification(
                self._exchange_name, "settlement_readback_projection_invalid"
            )
        return result

    def validate_preflight(
        self,
        snapshot: AccountPreflightSnapshot,
        *,
        now: datetime,
    ) -> bool:
        if (
            type(snapshot) is not AccountPreflightSnapshot
            or snapshot.request.exchange_name != self._exchange_name
            or snapshot.ready is not True
            or not snapshot.available
            or snapshot.query_receipt is None
            or snapshot.query_lease is None
        ):
            return False
        _require_utc(now, "now")
        try:
            result = self.__provider_port.validate_preflight(snapshot, now=now)
        except Exception:
            return False
        return result is True


class _UnavailableAccountSessionPort(AccountSessionPort):
    __slots__ = ("_exchange_name", "_reason")

    def __init__(self, exchange_name: str, reason: str) -> None:
        self._exchange_name = exchange_name
        self._reason = reason

    def session_snapshot(self) -> AccountSessionSnapshot:
        return _unavailable_session_snapshot(self._exchange_name, self._reason)

    def preflight(self, request: AccountPreflightRequest) -> AccountPreflightSnapshot:
        if type(request) is not AccountPreflightRequest:
            raise TypeError("request must be an AccountPreflightRequest")
        blocker = (
            self._reason
            if request.exchange_name == self._exchange_name
            else "account_session_route_mismatch"
        )
        return _unavailable_preflight(request, blocker)

    def verify_settlement(self, *, deadline: datetime) -> SettlementVerificationReceipt:
        return _unavailable_settlement_verification(self._exchange_name, self._reason)

    def validate_preflight(
        self,
        snapshot: AccountPreflightSnapshot,
        *,
        now: datetime,
    ) -> bool:
        return False


_SETTLEMENT_CLAIM_SEAL = object()
_MAX_RETAINED_SETTLEMENT_CLAIMS = 128


class _SettlementClaimHandle:
    """SDK-owned opaque claim handle; provider claims never leave the router."""

    __slots__ = ("__seal",)

    def __init__(self, seal: object) -> None:
        if seal is not _SETTLEMENT_CLAIM_SEAL:
            raise TypeError("settlement claim handle is SDK-owned")
        self.__seal = seal

    def _is_valid(self) -> bool:
        return self.__seal is _SETTLEMENT_CLAIM_SEAL


class _SettlementConfirmationPortView(SettlementConfirmationPort):
    __slots__ = ("_exchange_name", "__provider_port", "__lock", "__claims")

    def __init__(
        self,
        exchange_name: str,
        provider_port: SettlementConfirmationPort,
    ) -> None:
        self._exchange_name = exchange_name
        self.__provider_port = provider_port
        self.__lock = threading.RLock()
        self.__claims: OrderedDict[
            int,
            tuple[
                _SettlementClaimHandle,
                SettlementConfirmationRequest,
                object,
                object,
            ],
        ] = OrderedDict()

    def claim_settlement_confirmation(
        self,
        request: SettlementConfirmationRequest,
        *,
        authorization: object,
    ) -> object:
        if type(request) is not SettlementConfirmationRequest:
            raise TypeError("request must be a SettlementConfirmationRequest")
        if request.exchange_name != self._exchange_name:
            raise AccountSessionCompositionError(
                "SETTLEMENT_CONFIRMATION_ROUTE_MISMATCH"
            )
        handle = _SettlementClaimHandle(_SETTLEMENT_CLAIM_SEAL)
        try:
            with self.__lock:
                self.__prune_claims(datetime.now(timezone.utc))
                if len(self.__claims) >= _MAX_RETAINED_SETTLEMENT_CLAIMS:
                    raise AccountSessionCompositionError(
                        "SETTLEMENT_CONFIRMATION_CLAIM_CAPACITY"
                    )
                provider_claim = self.__provider_port.claim_settlement_confirmation(
                    request, authorization=authorization
                )
                if provider_claim is None:
                    raise AccountSessionCompositionError(
                        "SETTLEMENT_CONFIRMATION_CLAIM_INVALID"
                    )
                self.__claims[id(handle)] = (
                    handle,
                    request,
                    authorization,
                    provider_claim,
                )
        except AccountSessionCompositionError:
            raise
        except Exception:
            raise AccountSessionCompositionError(
                "SETTLEMENT_CONFIRMATION_AUTHORIZATION_REJECTED"
            ) from None
        return handle

    def confirm_settlement(
        self,
        request: SettlementConfirmationRequest,
        *,
        claim: object,
        authorization: object,
    ) -> SettlementConfirmationReceipt:
        if type(request) is not SettlementConfirmationRequest:
            raise TypeError("request must be a SettlementConfirmationRequest")
        if request.exchange_name != self._exchange_name:
            return _unknown_settlement_confirmation(
                request, "settlement_confirmation_route_mismatch"
            )
        if type(claim) is not _SettlementClaimHandle or not claim._is_valid():
            return _unknown_settlement_confirmation(
                request, "settlement_confirmation_claim_invalid"
            )
        with self.__lock:
            retained = self.__claims.pop(id(claim), None)
        if retained is None or retained[0] is not claim:
            return _unknown_settlement_confirmation(
                request, "settlement_confirmation_claim_invalid"
            )
        _, claimed_request, claimed_authorization, provider_claim = retained
        if claimed_request is not request or claimed_authorization is not authorization:
            return _unknown_settlement_confirmation(
                request, "settlement_confirmation_claim_binding_mismatch"
            )
        try:
            result = self.__provider_port.confirm_settlement(
                request,
                claim=provider_claim,
                authorization=authorization,
            )
        except Exception:
            return _unknown_settlement_confirmation(
                request, "settlement_confirmation_outcome_unknown"
            )
        if (
            type(result) is not SettlementConfirmationReceipt
            or result.request is not request
        ):
            return _unknown_settlement_confirmation(
                request, "settlement_confirmation_receipt_invalid"
            )
        return result

    def verify_confirmation(
        self,
        receipt: SettlementConfirmationReceipt,
        *,
        now: datetime,
    ) -> bool:
        if type(receipt) is not SettlementConfirmationReceipt:
            return False
        if (
            receipt.request.exchange_name != self._exchange_name
            or receipt.disposition != "accepted"
            or receipt.receipt_ref is None
        ):
            return False
        _require_utc(now, "now")
        try:
            result = self.__provider_port.verify_confirmation(receipt, now=now)
        except Exception:
            return False
        return result is True

    def __prune_claims(self, now: datetime) -> None:
        expired = [
            identity
            for identity, (_, request, _, _) in self.__claims.items()
            if request.deadline <= now
        ]
        for identity in expired:
            self.__claims.pop(identity, None)


class _UnavailableSettlementConfirmationPort(SettlementConfirmationPort):
    __slots__ = ("_exchange_name", "_reason")

    def __init__(self, exchange_name: str, reason: str) -> None:
        self._exchange_name = exchange_name
        self._reason = reason

    def claim_settlement_confirmation(
        self,
        request: SettlementConfirmationRequest,
        *,
        authorization: object,
    ) -> object:
        if type(request) is not SettlementConfirmationRequest:
            raise TypeError("request must be a SettlementConfirmationRequest")
        if request.exchange_name != self._exchange_name:
            raise AccountSessionCompositionError(
                "SETTLEMENT_CONFIRMATION_ROUTE_MISMATCH"
            )
        raise AccountSessionCompositionError(
            "SETTLEMENT_CONFIRMATION_CAPABILITY_UNAVAILABLE"
        )

    def confirm_settlement(
        self,
        request: SettlementConfirmationRequest,
        *,
        claim: object,
        authorization: object,
    ) -> SettlementConfirmationReceipt:
        if type(request) is not SettlementConfirmationRequest:
            raise TypeError("request must be a SettlementConfirmationRequest")
        if request.exchange_name != self._exchange_name:
            return _unknown_settlement_confirmation(
                request, "settlement_confirmation_route_mismatch"
            )
        return _unknown_settlement_confirmation(request, self._reason)

    def verify_confirmation(
        self,
        receipt: SettlementConfirmationReceipt,
        *,
        now: datetime,
    ) -> bool:
        if (
            type(receipt) is not SettlementConfirmationReceipt
            or receipt.request.exchange_name != self._exchange_name
        ):
            return False
        return False


_DEFAULT_ACCOUNT_SESSION_REGISTRY = AccountSessionRouteRegistry()


def default_account_session_registry() -> AccountSessionRouteRegistry:
    """Return the process-wide account-session provider registry."""

    return _DEFAULT_ACCOUNT_SESSION_REGISTRY


def default_account_session_route_registry() -> AccountSessionRouteRegistry:
    """Return the global exact-route registry used by SDK provider plugins."""

    return _DEFAULT_ACCOUNT_SESSION_REGISTRY


def register_account_session_provider(
    exchange_name: str,
    account_session_factory: AccountSessionFactory | None,
    settlement_factory: SettlementConfirmationFactory | None = None,
    *,
    registry: AccountSessionRouteRegistry | None = None,
) -> None:
    """Register provider-owned optional capabilities for one exact route."""

    _select_registry(registry).register(
        exchange_name,
        account_session_factory,
        settlement_factory,
    )


def default_account_session_router(
    route_resolver: AccountSessionRouteResolver,
) -> AccountSessionRouter:
    """Create a router over the shared registry and an SDK-owned route lookup."""

    return AccountSessionRouter(_DEFAULT_ACCOUNT_SESSION_REGISTRY, route_resolver)


def create_account_session_router(
    route_resolver: AccountSessionRouteResolver,
    *,
    registry: AccountSessionRouteRegistry | None = None,
) -> AccountSessionRouter:
    """Create a router, optionally isolated to an explicit registry."""

    return AccountSessionRouter(_select_registry(registry), route_resolver)


def _unavailable_session_snapshot(
    exchange_name: str,
    blocker: str,
) -> AccountSessionSnapshot:
    return AccountSessionSnapshot(
        exchange_name=exchange_name,
        source_ref=None,
        generation=None,
        session_ref=None,
        connected=None,
        authenticated=None,
        trading_enabled=None,
        business_day=None,
        settlement_status="unavailable",
        observed_at=datetime.now(timezone.utc),
        available=False,
        blockers=(blocker,),
    )


def _unavailable_preflight(
    request: AccountPreflightRequest,
    blocker: str,
) -> AccountPreflightSnapshot:
    return AccountPreflightSnapshot(
        request=request,
        source_ref=None,
        generation=None,
        observed_at=None,
        ready=None,
        available=False,
        blockers=(blocker,),
    )


def _unavailable_settlement_verification(
    exchange_name: str,
    reason_code: str,
) -> SettlementVerificationReceipt:
    return SettlementVerificationReceipt(
        exchange_name=exchange_name,
        source_ref=None,
        generation=None,
        business_day=None,
        status="unavailable",
        observed_at=datetime.now(timezone.utc),
        complete=False,
        read_only_safe=False,
        receipt_ref=None,
        reason_code=reason_code,
    )


def _unknown_settlement_confirmation(
    request: SettlementConfirmationRequest,
    reason_code: str,
) -> SettlementConfirmationReceipt:
    return SettlementConfirmationReceipt(
        request=request,
        disposition="unknown",
        observed_at=datetime.now(timezone.utc),
        receipt_ref=None,
        reason_code=reason_code,
    )


def _same_route(
    left: SelectedAccountSessionRoute,
    right: SelectedAccountSessionRoute,
) -> bool:
    return bool(
        left.exchange_name == right.exchange_name
        and left.client is right.client
        and left.environment == right.environment
        and left.settlement_context is right.settlement_context
    )


def _same_session_snapshot_state(
    issued: AccountSessionSnapshot,
    current: object,
) -> bool:
    if type(current) is not AccountSessionSnapshot:
        return False
    observed_at = issued.observed_at
    current_observed_at = current.observed_at
    now = datetime.now(timezone.utc)
    if (
        observed_at is None
        or current_observed_at is None
        or observed_at > now
        or current_observed_at > now
        or current_observed_at < observed_at
    ):
        return False
    return all(
        getattr(issued, field_name) == getattr(current, field_name)
        for field_name in (
            "exchange_name",
            "source_ref",
            "generation",
            "session_ref",
            "connected",
            "authenticated",
            "trading_enabled",
            "business_day",
            "settlement_status",
            "available",
            "blockers",
        )
    )


def _require_route_text(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or not value.isprintable()
    ):
        raise AccountSessionCompositionError("ACCOUNT_SESSION_ROUTE_INVALID")
    try:
        if len(value.encode("utf-8", errors="strict")) > 256:
            raise AccountSessionCompositionError("ACCOUNT_SESSION_ROUTE_INVALID")
    except UnicodeEncodeError:
        raise AccountSessionCompositionError("ACCOUNT_SESSION_ROUTE_INVALID") from None


def _select_registry(
    registry: AccountSessionRouteRegistry | None,
) -> AccountSessionRouteRegistry:
    if registry is None:
        return _DEFAULT_ACCOUNT_SESSION_REGISTRY
    if type(registry) is not AccountSessionRouteRegistry:
        raise AccountSessionCompositionError("ACCOUNT_SESSION_REGISTRY_INVALID")
    return registry


def _route_entry_points(exchange_name: str) -> tuple[object, ...] | None:
    try:
        discovered = importlib_metadata.entry_points()
        if hasattr(discovered, "select"):
            candidates = tuple(
                discovered.select(group=ACCOUNT_SESSION_PROVIDER_ENTRY_POINT_GROUP)
            )
        elif isinstance(discovered, dict):
            candidates = tuple(
                discovered.get(ACCOUNT_SESSION_PROVIDER_ENTRY_POINT_GROUP, ())
            )
        else:
            candidates = tuple(
                entry_point
                for entry_point in discovered
                if getattr(entry_point, "group", None)
                == ACCOUNT_SESSION_PROVIDER_ENTRY_POINT_GROUP
            )
    except Exception:
        return None
    return tuple(
        entry_point
        for entry_point in candidates
        if getattr(entry_point, "name", None) == exchange_name
    )


def _entry_point_identity(
    entry_point: object,
    exchange_name: str,
) -> tuple[str, str, str]:
    value = getattr(entry_point, "value", None)
    distribution = getattr(entry_point, "dist", None)
    distribution_name = getattr(distribution, "name", "") if distribution is not None else ""
    if (
        type(value) is not str
        or not value
        or type(distribution_name) is not str
        or type(getattr(entry_point, "name", None)) is not str
        or getattr(entry_point, "name", None) != exchange_name
    ):
        raise AccountSessionCompositionError("ACCOUNT_SESSION_ENTRY_POINT_INVALID")
    return (exchange_name, value, distribution_name)


def _require_utc(value: object, field_name: str) -> datetime:
    if type(value) is not datetime:
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return value


__all__ = [
    "ACCOUNT_SESSION_PROVIDER_ENTRY_POINT_GROUP",
    "AccountSessionCompositionError",
    "AccountSessionFactory",
    "AccountSessionRouteRegistry",
    "AccountSessionRouter",
    "AccountSessionRouteResolver",
    "SelectedAccountSessionRoute",
    "SettlementConfirmationFactory",
    "create_account_session_router",
    "default_account_session_registry",
    "default_account_session_route_registry",
    "default_account_session_router",
    "register_account_session_provider",
]
