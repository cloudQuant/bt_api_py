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
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from typing import TYPE_CHECKING, Any

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
_HEDGE_FLAGS = frozenset({"1", "2", "3"})
_TERMINAL_ORDER_STATUS = frozenset({"0", "2", "4", "5"})

if TYPE_CHECKING:
    from bt_api_ctp import CtpNativeQueryCertificate


class CtpSimNowExecutionError(ValueError):
    """A fail-closed SimNow adapter contract error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CtpSimNowSessionIdentity:
    environment: str
    profile: str
    account_fingerprint: str = field(repr=False)
    trading_day: str
    connection_generation: int


@dataclass(frozen=True)
class CtpSimNowOrderRequest:
    client_order_id: str
    instrument_id: str
    exchange_id: str
    side: str
    quantity: Decimal
    limit_price: Decimal
    offset: str = "open"
    hedge_flag: str = "1"
    runtime_order_id: str | None = None

    def __post_init__(self) -> None:
        if not self.client_order_id or self.client_order_id != self.client_order_id.strip():
            raise ValueError("client_order_id must be non-empty and trimmed")
        try:
            order_ref_bytes = self.client_order_id.encode("ascii")
        except UnicodeEncodeError:
            raise CtpSimNowExecutionError("ctp_native_order_ref_mapping_unavailable") from None
        if b"\x00" in order_ref_bytes or len(order_ref_bytes) > 12:
            raise CtpSimNowExecutionError("ctp_native_order_ref_mapping_unavailable")
        if self.runtime_order_id is not None and (
            not isinstance(self.runtime_order_id, str)
            or not self.runtime_order_id
            or self.runtime_order_id != self.runtime_order_id.strip()
            or len(self.runtime_order_id.encode("utf-8")) > 256
        ):
            raise ValueError("runtime_order_id must be a bounded non-empty string or None")
        if not self.instrument_id or self.instrument_id != self.instrument_id.strip():
            raise ValueError("instrument_id must be non-empty and trimmed")
        if not self.exchange_id or self.exchange_id != self.exchange_id.strip():
            raise ValueError("exchange_id must be non-empty and trimmed")
        if self.side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        if not isinstance(self.quantity, Decimal) or not self.quantity.is_finite():
            raise ValueError("quantity must be a finite Decimal")
        if self.quantity <= 0 or self.quantity != self.quantity.to_integral_value():
            raise ValueError("quantity must be a positive integer number of lots")
        if not isinstance(self.limit_price, Decimal) or not self.limit_price.is_finite():
            raise ValueError("limit_price must be a finite Decimal")
        if self.limit_price <= 0:
            raise ValueError("limit_price must be positive")
        if self.offset not in {"open", "close", "close_today", "close_yesterday"}:
            raise ValueError("unsupported CTP offset")
        if self.hedge_flag not in _HEDGE_FLAGS:
            raise ValueError("unsupported CTP hedge flag")


@dataclass(frozen=True)
class CtpSimNowOrderIdentity:
    instrument_id: str
    exchange_id: str
    client_order_id: str
    order_ref: str
    order_sys_id: str | None
    front_id: int | None
    session_id: int | None
    trading_day: str
    runtime_order_id: str | None = None


@dataclass(frozen=True)
class CtpSimNowOrderResult:
    identity: CtpSimNowOrderIdentity
    status: str
    execution_unknown: bool


@dataclass(frozen=True)
class CtpSimNowCancelResult:
    action_id: str
    identity: CtpSimNowOrderIdentity
    session_identity: CtpSimNowSessionIdentity
    status: str
    request_id: int | None
    order_action_ref: int | None
    execution_unknown: bool


@dataclass(frozen=True)
class CtpSimNowQueryResult:
    complete: bool
    identity: CtpSimNowSessionIdentity
    records: tuple[CtpSimNowOrderIdentity, ...]


@dataclass(frozen=True)
class CtpSimNowReadObservation:
    """Digest-only CTP read evidence with no execution or account-order authority."""

    identity: CtpSimNowSessionIdentity
    native_query_certificate: CtpNativeQueryCertificate = field(repr=False)

    @property
    def authority_status(self) -> str:
        """This observation never authorizes an execution action."""

        return "NON_AUTHORIZING"

    @property
    def execution_authorized(self) -> bool:
        return False

    @property
    def account_open_orders_complete(self) -> bool:
        """A terminal query packet does not prove full account-wide coverage."""

        return False

    @property
    def account_open_orders_status(self) -> str:
        return "UNPROVEN"

    def as_public_dict(self) -> dict[str, Any]:
        """Expose the certificate digests and scope without native records."""

        return {
            "schema": "ctp_simnow_read_observation.v1",
            "authority_status": self.authority_status,
            "execution_authorized": False,
            "session": {
                "environment": self.identity.environment,
                "profile": self.identity.profile,
                "trading_day": self.identity.trading_day,
                "connection_generation": self.identity.connection_generation,
            },
            "account_open_orders": {
                "complete": False,
                "status": "UNPROVEN",
            },
            "native_query_certificate": self.native_query_certificate.as_public_dict(),
        }


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _value(row: Any, *names: str) -> Any:
    if isinstance(row, Mapping):
        for name in names:
            if name in row:
                return row[name]
    for name in names:
        value = getattr(row, name, None)
        if value not in (None, ""):
            return value
    return None


def _is_order_action_evidence_instance(value: Any) -> bool:
    """Recognize the SDK evidence dataclass without authenticating its origin."""
    try:
        from bt_api_ctp.order_action import CtpOrderActionEvidence
    except ImportError:
        return False
    return type(value) is CtpOrderActionEvidence


def _native_order_identity(
    row: Any,
    *,
    fallback: CtpSimNowOrderIdentity | None = None,
    trading_day: str = "",
) -> CtpSimNowOrderIdentity:
    fallback = fallback or CtpSimNowOrderIdentity("", "", "", "", None, None, None, trading_day)
    return CtpSimNowOrderIdentity(
        instrument_id=str(_value(row, "instrument_id", "InstrumentID") or fallback.instrument_id),
        exchange_id=str(_value(row, "exchange_id", "ExchangeID") or fallback.exchange_id),
        client_order_id=str(
            _value(row, "client_order_id", "OrderRef", "order_ref") or fallback.client_order_id
        ),
        order_ref=str(_value(row, "order_ref", "OrderRef") or fallback.order_ref),
        order_sys_id=(
            str(_value(row, "order_id", "order_sys_id", "OrderSysID"))
            if _value(row, "order_id", "order_sys_id", "OrderSysID") not in (None, "")
            else fallback.order_sys_id
        ),
        front_id=(
            _int_or_none(_value(row, "front_id", "FrontID"))
            if _int_or_none(_value(row, "front_id", "FrontID")) is not None
            else fallback.front_id
        ),
        session_id=(
            _int_or_none(_value(row, "session_id", "SessionID"))
            if _int_or_none(_value(row, "session_id", "SessionID")) is not None
            else fallback.session_id
        ),
        trading_day=str(_value(row, "trading_day", "TradingDay") or fallback.trading_day),
        runtime_order_id=fallback.runtime_order_id,
    )


def map_ctp_simnow_order_result(
    row: Any, request: CtpSimNowOrderRequest, identity: CtpSimNowSessionIdentity
) -> CtpSimNowOrderResult:
    """Bind CTP's OrderRef/front/session and optional OrderSysID to the request."""
    return _map_order_row(
        row,
        fallback=CtpSimNowOrderIdentity(
            request.instrument_id,
            request.exchange_id,
            request.client_order_id,
            request.client_order_id,
            None,
            None,
            None,
            identity.trading_day,
            request.runtime_order_id,
        ),
        identity=identity,
        expected_client_order_id=request.client_order_id,
    )


def _map_order_row(
    row: Any,
    *,
    fallback: CtpSimNowOrderIdentity,
    identity: CtpSimNowSessionIdentity,
    expected_client_order_id: str,
) -> CtpSimNowOrderResult:
    order_identity = _native_order_identity(
        row, fallback=fallback, trading_day=identity.trading_day
    )
    if order_identity.client_order_id != expected_client_order_id:
        raise CtpSimNowExecutionError("ctp_order_client_reference_mismatch")
    if (
        order_identity.instrument_id != fallback.instrument_id
        or order_identity.exchange_id != fallback.exchange_id
    ):
        raise CtpSimNowExecutionError("ctp_order_instrument_identity_mismatch")
    if order_identity.trading_day and order_identity.trading_day != identity.trading_day:
        raise CtpSimNowExecutionError("ctp_order_trading_day_mismatch")
    raw_status = str(_value(row, "status") or "").strip().lower()
    if _value(row, "execution_unknown") is True or raw_status in {
        "unknown",
        "submitted",
        "pending",
    }:
        status = "UNKNOWN"
    elif _value(row, "definite_reject") is True or raw_status == "rejected":
        status = "REJECTED"
    elif raw_status in {"canceled", "cancelled"}:
        status = "CANCELED"
    elif raw_status == "completed":
        status = "FILLED"
    elif raw_status == "partial":
        status = "PARTIALLY_FILLED"
    elif raw_status == "accepted":
        status = "ACCEPTED"
    else:
        status = "UNKNOWN"
    return CtpSimNowOrderResult(order_identity, status, status == "UNKNOWN")


def build_ctp_simnow_cancel_request(
    identity: CtpSimNowOrderIdentity,
    *,
    account_id: str,
    action_id: str,
    idempotency_key: str = "",
) -> CancelOrderRequest:
    """Map a client/native order identity into the SDK's typed cancel request."""
    if not action_id or action_id != action_id.strip():
        raise ValueError("action_id must be non-empty and trimmed")
    if not identity.runtime_order_id:
        raise CtpSimNowExecutionError("ctp_runtime_cancel_identity_required")
    if not identity.order_sys_id and not (
        identity.order_ref and identity.front_id is not None and identity.session_id is not None
    ):
        raise CtpSimNowExecutionError("ctp_cancel_native_identity_incomplete")
    return CancelOrderRequest(
        symbol=identity.instrument_id,
        account_id=account_id,
        order_id=identity.order_sys_id,
        client_order_id=identity.client_order_id or None,
        idempotency_key=idempotency_key,
        exchange_id=identity.exchange_id,
        front_id=identity.front_id,
        session_id=identity.session_id,
        order_ref=identity.order_ref or None,
        runtime_order_id=identity.runtime_order_id,
        runtime_action_id=action_id,
    )


def map_ctp_simnow_cancel_result(
    action_id: str,
    identity: CtpSimNowOrderIdentity,
    evidence: Any,
    session_identity: CtpSimNowSessionIdentity,
    *,
    request_id: int | None,
    order_action_ref: int | str | None,
) -> CtpSimNowCancelResult:
    """Project callback evidence; this helper never grants write authority.

    The exact SDK evidence dataclass is recognized for diagnostic projection;
    it is publicly constructible, so its type does not authenticate callback
    origin. This result cannot grant write or terminal-order authority.
    Redacted dictionaries and caller-constructed mappings remain UNKNOWN.
    """
    recognized_evidence = _is_order_action_evidence_instance(evidence)
    status_value = _value(evidence, "status")
    normalized = str(status_value or "unknown").strip().lower()
    status = {"accepted": "ACCEPTED", "rejected": "REJECTED"}.get(normalized, "UNKNOWN")
    expected_request_id = _int_or_none(request_id)
    expected_action_ref = _int_or_none(order_action_ref)
    evidence_request_id = _int_or_none(_value(evidence, "request_id"))
    evidence_action_ref = _int_or_none(_value(evidence, "order_action_ref"))
    # An API return code or an order status is not a matching action callback.
    evidence_account = str(_value(evidence, "account_fingerprint") or "").lower()
    if evidence_account.startswith("acct_"):
        evidence_account = evidence_account[5:]
    expected_account = session_identity.account_fingerprint[5:]
    matching = bool(
        recognized_evidence
        and evidence is not None
        and expected_request_id is not None
        and expected_action_ref is not None
        and evidence_request_id == expected_request_id
        and evidence_action_ref == expected_action_ref
        and _value(evidence, "evidence_received") is True
        and _value(evidence, "callback_received") is True
        and evidence_account == expected_account
        and _int_or_none(_value(evidence, "connection_generation"))
        == session_identity.connection_generation
        and str(_value(evidence, "order_ref") or "") == identity.order_ref
        and str(_value(evidence, "order_sys_id") or "") == str(identity.order_sys_id or "")
        and str(_value(evidence, "trading_day") or "") == session_identity.trading_day
        and str(_value(evidence, "instrument_id") or "") == identity.instrument_id
        and str(_value(evidence, "exchange_id") or "") == identity.exchange_id
        and str(_value(evidence, "action_flag") or "0") == "0"
        and (
            identity.front_id is None
            or _int_or_none(_value(evidence, "front_id")) == identity.front_id
        )
        and (
            identity.session_id is None
            or _int_or_none(_value(evidence, "session_id")) == identity.session_id
        )
    )
    if not matching:
        status = "UNKNOWN"
    return CtpSimNowCancelResult(
        action_id=action_id,
        identity=identity,
        session_identity=session_identity,
        status=status,
        request_id=evidence_request_id if matching else None,
        order_action_ref=evidence_action_ref if matching else None,
        execution_unknown=status in {"UNKNOWN", "ACCEPTED"},
    )


class CtpSimNowExecutionAdapter:
    """Public set1-only facade over ``BtApi``; native writes remain fail-closed.

    ``selected_profile`` must come from the caller's sealed runtime
    configuration.  The adapter verifies that exact profile against current
    SDK session evidence and never reads environment variables or substitutes
    another SimNow family member.
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

    def build_order_request(self, request: CtpSimNowOrderRequest) -> OrderRequest:
        """Build the typed SDK intent without dispatching a native write."""
        identity, _session = self._require_scope()
        ledger = self._api.get_execution_identity(self.exchange_name)
        account_id = str(ledger.get("account_id") or identity.account_fingerprint)
        return OrderRequest(
            symbol=request.instrument_id,
            side=Side(request.side),
            order_type=OrderType.LIMIT,
            quantity=request.quantity,
            account_id=account_id,
            client_order_id=request.client_order_id,
            price=request.limit_price,
            time_in_force="DAY",
            quantity_unit="lots",
            offset=request.offset,
            exchange_id=request.exchange_id,
            hedge_flag=request.hedge_flag,
            runtime_order_id=request.runtime_order_id,
        )

    def build_cancel_request(
        self, identity: CtpSimNowOrderIdentity, *, action_id: str
    ) -> CancelOrderRequest:
        current, _session = self._require_scope()
        if identity.trading_day != current.trading_day:
            raise CtpSimNowExecutionError("ctp_cancel_trading_day_mismatch")
        ledger = self._api.get_execution_identity(self.exchange_name)
        return build_ctp_simnow_cancel_request(
            identity,
            account_id=str(ledger.get("account_id") or current.account_fingerprint),
            action_id=action_id,
        )

    def arm_from_approval(self, *_: Any, **__: Any) -> None:
        """Keep actual arming disabled until private config and key bindings exist."""
        self._require_scope()
        raise CtpSimNowExecutionError("ctp_execution_credential_binding_unavailable")

    def submit_order_insert(self, request: CtpSimNowOrderRequest) -> CtpSimNowOrderResult:
        """Refuse dispatch until private credentials and native reference mapping bind."""
        self._require_scope()
        self.build_order_request(request)
        raise CtpSimNowExecutionError("ctp_native_order_ref_mapping_unavailable")

    def submit_order_action(
        self, identity: CtpSimNowOrderIdentity, action_id: str
    ) -> CtpSimNowCancelResult:
        """Refuse dispatch while approval context omits private credential binding."""
        self._require_scope()
        self.build_cancel_request(identity, action_id=action_id)
        raise CtpSimNowExecutionError("ctp_execution_credential_binding_unavailable")

    def query_order(self, identity: CtpSimNowOrderIdentity) -> CtpSimNowOrderResult:
        """Resolve an order through the SDK's public typed query and journal."""
        current, _session = self._require_scope()
        if identity.trading_day != current.trading_day:
            raise CtpSimNowExecutionError("ctp_order_query_trading_day_mismatch")
        ledger = self._api.get_execution_identity(self.exchange_name)
        request = QueryOrderRequest(
            symbol=identity.instrument_id,
            account_id=str(ledger.get("account_id") or current.account_fingerprint),
            order_id=identity.order_sys_id,
            client_order_id=identity.client_order_id or None,
            exchange_id=identity.exchange_id,
            front_id=identity.front_id,
            session_id=identity.session_id,
            order_ref=identity.order_ref or None,
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
        if getattr(result, "request_type", None) not in {"orders", "order"}:
            raise CtpSimNowExecutionError("ctp_orders_query_type_mismatch")
        result_identity = (
            str(getattr(result, "account_fingerprint", "") or "").lower(),
            getattr(result, "connection_generation", None),
        )
        expected_account = (
            identity.account_fingerprint[5:]
            if identity.account_fingerprint.startswith("acct_")
            else identity.account_fingerprint
        )
        if result_identity != (expected_account, identity.connection_generation):
            raise CtpSimNowExecutionError("ctp_orders_query_identity_mismatch")
        # QueryResult.complete proves a terminal packet, not account-wide
        # or pagination completeness.
        native_rows = tuple(getattr(result, "records", ()))
        if any(
            not _value(row, "trading_day", "TradingDay")
            or str(_value(row, "trading_day", "TradingDay")) != identity.trading_day
            for row in native_rows
        ):
            raise CtpSimNowExecutionError("ctp_orders_query_trading_day_mismatch")
        rows = tuple(
            _native_order_identity(row, trading_day=identity.trading_day) for row in native_rows
        )
        open_rows = tuple(
            row for row, raw in zip(rows, native_rows, strict=True) if _is_open_native_order(raw)
        )
        return CtpSimNowQueryResult(False, identity, open_rows)

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

        try:
            from bt_api_ctp import CtpNativeQueryCertificateBuilder
            from bt_api_ctp.containers.ctp.ctp_native_query_certificate import (
                CtpNativeQueryCertificateError,
            )
        except Exception as exc:
            raise CtpSimNowExecutionError("ctp_native_query_certificate_unavailable") from exc

        try:
            certificate_builder = CtpNativeQueryCertificateBuilder(
                client,
                instrument_id=instrument_id,
                exchange_id=exchange_id,
                hedge_flag=hedge_flag,
            )
            query_calls = (
                ("account", {}),
                ("positions", {}),
                ("orders", {}),
                ("trades", {}),
                ("instruments", {"instrument_id": instrument_id, "exchange_id": exchange_id}),
                (
                    "margin_rate",
                    {
                        "instrument_id": instrument_id,
                        "exchange_id": exchange_id,
                        "hedge_flag": hedge_flag,
                    },
                ),
                (
                    "commission_rate",
                    {"instrument_id": instrument_id, "exchange_id": exchange_id},
                ),
            )
            for query_type, query_kwargs in query_calls:
                result = self._api.query_ctp_result(
                    self.exchange_name,
                    query_type,
                    **query_kwargs,
                )
                certificate_builder.add(result)
            certificate = certificate_builder.finish()
        except CtpNativeQueryCertificateError as exc:
            raise CtpSimNowExecutionError(f"ctp_native_query_certificate_{exc.code}") from exc
        except CtpSimNowExecutionError:
            raise
        except Exception as exc:
            raise CtpSimNowExecutionError("ctp_native_query_observation_unavailable") from exc

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


def _is_open_native_order(row: Any) -> bool:
    status = str(_value(row, "OrderStatus", "order_status") or "a").lower()
    remaining = _value(row, "VolumeTotal", "volume_total")
    if remaining in (None, ""):
        original = _value(row, "VolumeTotalOriginal", "volume_total_original")
        traded = _value(row, "VolumeTraded", "volume_traded") or 0
        try:
            remaining = int(original) - int(traded)
        except (TypeError, ValueError):
            remaining = 1
    try:
        has_remaining = int(remaining) > 0
    except (TypeError, ValueError):
        has_remaining = True
    # Treat unknown native statuses as potentially open. Only documented CTP
    # terminal statuses prove that the order no longer rests in the account.
    return status not in _TERMINAL_ORDER_STATUS and has_remaining


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
