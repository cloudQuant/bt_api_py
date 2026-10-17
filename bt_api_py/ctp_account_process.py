"""Trusted CTP SimNow read-only account process binding.

This module is the SDK-side composition edge between the generic Gateway
account-process protocol and the CTP package.  Importing it does not load the
CTP native client; CTP modules are imported only by the spawned child after
the fixed profile reference has been accepted.

The selected deployment is deliberately narrow: the official SimNow SET2
7x24 profile, account/position/order queries, and bounded native shutdown.
The launch document can select only this code-registered profile reference.
Credentials remain in the inherited trusted process environment and never
cross the child Pipe or appear in a public status projection.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bt_api_base.account_protocol import AccountKey
from bt_api_gateway.account_process import (
    GatewayAccountSessionProcessBinding,
    GatewayAccountSessionProcessError,
    GatewayAccountSessionProcessProvider,
    GatewayAccountSessionProfileUnavailableError,
    serve_account_session_child,
)

from bt_api_py.gateway_operator_application import GatewayOperatorProfile

CTP_SIMNOW_SET2_7X24_PROFILE_REF = "ctp.simnow.set2-7x24.readonly.v1"
CTP_SIMNOW_SET2_7X24_SESSION_REF = "ctp.simnow.set2-7x24.account.v1"
CTP_SIMNOW_SET2_7X24_OPERATOR_PROFILE_REF = "ctp.simnow.set2-7x24.account-operator.v1"
CTP_SIMNOW_SET2_7X24_MARKET_OPERATOR_PROFILE_REF = "ctp.simnow.set2-7x24.market-operator.v1"
CTP_SIMNOW_SET2_7X24_MARKET_SOURCE_REF = "ctp-simnow-set2-7x24-market-v1"
CTP_SIMNOW_SET2_7X24_MARKET_ENTITLEMENT_REF = "ctp-simnow-set2-7x24-quote-v2-v1"
_CTP_SIMNOW_SET2_PROFILE = "set2_7x24_4000x"
_CONNECT_TIMEOUT_SECONDS = 15.0
_ACCOUNT_RESOURCE_PRODUCT = "future"
_ACCOUNT_RESOURCE_MARGIN = "futures"
_ACCOUNT_REFRESH_INTERVAL_SECONDS = 5.0
_ACCOUNT_MAX_STALENESS_SECONDS = 30.0
_ACCOUNT_REQUEST_TIMEOUT_SECONDS = 45.0


class CtpSimNowAccountProfileError(RuntimeError):
    """Finite local configuration failure; never carries account values."""

    _CODES = frozenset({"PROFILE_REF_UNAVAILABLE", "ACCOUNT_IDENTITY_UNAVAILABLE"})

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in self._CODES:
            code = "ACCOUNT_IDENTITY_UNAVAILABLE"
        self.code = code
        super().__init__(code)


def ctp_simnow_set2_account_key_from_environment() -> AccountKey:
    """Build the local account identity from the same variables CTP uses.

    Values remain inside trusted composition code.  This function never logs,
    formats, or returns them separately from the typed ``AccountKey``.
    """

    try:
        investor_id = os.environ.get("CTP_USER_ID", "").strip()
        broker_id = os.environ.get("CTP_BROKER_ID", "").strip()
    except Exception:
        raise CtpSimNowAccountProfileError("ACCOUNT_IDENTITY_UNAVAILABLE") from None
    if not investor_id or not broker_id:
        raise CtpSimNowAccountProfileError("ACCOUNT_IDENTITY_UNAVAILABLE")
    try:
        return AccountKey(
            provider="ctp",
            venue="simnow",
            environment="simnow",
            account_id=investor_id,
            subaccount_id=broker_id,
        )
    except Exception:
        raise CtpSimNowAccountProfileError("ACCOUNT_IDENTITY_UNAVAILABLE") from None


def create_ctp_simnow_set2_account_provider(
    profile_ref: str,
) -> GatewayAccountSessionProcessBinding:
    """Resolve the exact read-only CTP provider binding inside the child."""

    if type(profile_ref) is not str or profile_ref != CTP_SIMNOW_SET2_7X24_PROFILE_REF:
        raise GatewayAccountSessionProfileUnavailableError()
    try:
        account_key = ctp_simnow_set2_account_key_from_environment()
    except CtpSimNowAccountProfileError:
        raise GatewayAccountSessionProfileUnavailableError() from None

    # Keep all CTP imports and native-module discovery inside the child target.
    from bt_api_ctp.account_provider import CtpReadOnlyAccountSessionProvider
    from bt_api_ctp.ctp_env_selector import official_simnow_fronts
    from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture

    td_front, md_front = official_simnow_fronts(_CTP_SIMNOW_SET2_PROFILE)

    def request_feed_factory() -> object:
        return CtpRequestDataFuture(
            broker_id=account_key.subaccount_id,
            user_id=account_key.account_id,
            td_front=td_front,
            md_front=md_front,
            ctp_env_profile=_CTP_SIMNOW_SET2_PROFILE,
            require_ctp_profile=_CTP_SIMNOW_SET2_PROFILE,
            auto_detect_fronts=False,
            auto_settlement_confirm=False,
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
        )

    provider = CtpReadOnlyAccountSessionProvider(
        request_feed_factory,
        stop_timeout_seconds=3.0,
    )
    return GatewayAccountSessionProcessBinding(account_key=account_key, provider=provider)


def project_ctp_native_stop_receipt(
    provider: object, expected_native_generation: int
) -> dict[str, object]:
    """Project the exact, complete native receipt for its CTP generation."""

    from bt_api_ctp.account_provider import CtpReadOnlyAccountSessionProvider
    from bt_api_ctp.ctp.client import CtpNativeStopReceipt

    if type(provider) is not CtpReadOnlyAccountSessionProvider:
        raise GatewayAccountSessionProcessError("NATIVE_RECEIPT_PROVIDER_INVALID")
    try:
        owner_created = provider.native_owner_created
        native_generation = provider.native_connection_generation
        receipt = provider.last_native_stop_receipt
    except Exception:
        raise GatewayAccountSessionProcessError("NATIVE_RECEIPT_UNAVAILABLE") from None
    if (
        owner_created is not True
        or type(expected_native_generation) is not int
        or expected_native_generation <= 0
        or type(native_generation) is not int
        or native_generation <= 0
        or native_generation != expected_native_generation
        or type(receipt) is not CtpNativeStopReceipt
        or type(receipt.connection_generation) is not int
        or receipt.connection_generation <= 0
        or receipt.connection_generation != native_generation
        or receipt.complete is not True
    ):
        raise GatewayAccountSessionProcessError("NATIVE_RECEIPT_UNCONFIRMED")
    return {
        "connection_generation": receipt.connection_generation,
        "join_required": receipt.join_required,
        "join_completed": receipt.join_completed,
        "native_released": receipt.native_released,
        "thread_alive": receipt.thread_alive,
        "timed_out": receipt.timed_out,
        "complete": receipt.complete,
    }


def ctp_simnow_set2_account_process_child(connection: Any) -> None:
    """Importable spawn target registered by trusted application code."""

    serve_account_session_child(
        connection,
        provider_factory=create_ctp_simnow_set2_account_provider,
        close_receipt_projector=project_ctp_native_stop_receipt,
    )


class CtpSimNowSet2AccountProcessOwner:
    """One-shot trusted owner that creates the bounded CTP child adapter."""

    def __init__(self, account_session_ref: str = CTP_SIMNOW_SET2_7X24_SESSION_REF) -> None:
        if type(account_session_ref) is not str or account_session_ref != CTP_SIMNOW_SET2_7X24_SESSION_REF:
            raise CtpSimNowAccountProfileError("PROFILE_REF_UNAVAILABLE")
        self._account_session_ref = account_session_ref
        self._account_key = ctp_simnow_set2_account_key_from_environment()
        self._owner_thread = threading.get_ident()
        self._claimed = False

    @property
    def account_key(self) -> AccountKey:
        """Return the trusted identity for server-side binding construction."""

        self._check_owner()
        return self._account_key

    def create_provider(self, account_session_ref: str) -> GatewayAccountSessionProcessProvider:
        """Return exactly one process provider for the fixed session reference."""

        self._check_owner()
        if type(account_session_ref) is not str or account_session_ref != self._account_session_ref:
            raise CtpSimNowAccountProfileError("PROFILE_REF_UNAVAILABLE")
        if self._claimed:
            raise GatewayAccountSessionProcessError("SESSION_ALREADY_CLAIMED")
        self._claimed = True
        return GatewayAccountSessionProcessProvider(
            account_key=self._account_key,
            profile_ref=CTP_SIMNOW_SET2_7X24_PROFILE_REF,
            child_target=ctp_simnow_set2_account_process_child,
            close_timeout_seconds=5.0,
        )

    def _check_owner(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise GatewayAccountSessionProcessError("PROCESS_WRONG_THREAD")


def _reject_unexpected_service_component(
    component: str, _instance_id: str, _options: Mapping[str, object], _scope: object
) -> object:
    """Fail closed if a CTP read-only profile grows beyond its Gateway role."""

    del component, _instance_id, _options, _scope
    from bt_api_py.gateway_operator import GatewayOperatorDeploymentError

    raise GatewayOperatorDeploymentError("INVALID_DEPLOYMENT_CONFIG")


def _gateway_profile_config(config: object, *, role: str, ports: frozenset[str]) -> None:
    """Require one parsed Gateway component with the exact read-only role."""

    from bt_api_py.service_config import ServiceConfig

    if type(config) is not ServiceConfig or set(config.components) != {"gateway"}:
        raise GatewayOperatorAccountProfileError("SERVICE_CONFIG_INVALID")
    if config.clients:
        raise GatewayOperatorAccountProfileError("SERVICE_CONFIG_INVALID")
    gateway = config.components.get("gateway")
    if not isinstance(gateway, Mapping):
        raise GatewayOperatorAccountProfileError("SERVICE_CONFIG_INVALID")
    settings = gateway.get("settings")
    if not isinstance(settings, Mapping):
        raise GatewayOperatorAccountProfileError("SERVICE_CONFIG_INVALID")
    options = settings.get("options")
    raw_ports = settings.get("bind_ports")
    if (
        not isinstance(options, Mapping)
        or options.get("role") != role
        or type(raw_ports) not in (tuple, list)
        or frozenset(raw_ports) != ports
        or len(raw_ports) != len(ports)
    ):
        raise GatewayOperatorAccountProfileError("SERVICE_CONFIG_INVALID")
    if role == "account_session_worker" and options.get(
        "account_session_ref"
    ) != CTP_SIMNOW_SET2_7X24_SESSION_REF:
        raise GatewayOperatorAccountProfileError("SERVICE_CONFIG_INVALID")


class GatewayOperatorAccountProfileError(RuntimeError):
    """Finite, value-free error while building one trusted CTP deployment."""

    _CODES = frozenset(
        {"SERVICE_CONFIG_INVALID", "TRUSTED_BINDING_INVALID", "MARKET_UNIVERSE_INVALID"}
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self._CODES else "TRUSTED_BINDING_INVALID"
        super().__init__(self.code)


def build_ctp_simnow_set2_account_operator_profile(
    config: object,
    authority: object,
    registry_path: str | Path,
    *,
    journal: object,
    account_scope: object,
    principals: Mapping[str, object],
    guard_directory: str | Path,
    state_scope: str,
    owner_ref: str,
) -> GatewayOperatorProfile:
    """Build the fixed, read-only CTP account Gateway profile.

    All authority, journal, account alias and scope values are supplied by
    trusted server composition code. The parsed service config may select only
    Gateway account-read and provider-session ports. Credentials are never
    accepted here; the spawned child resolves them from its trusted local
    environment when its fixed profile is selected.
    """

    from bt_api_base.account_protocol import AccountResourceKey
    from bt_api_gateway.event_journal import GatewayEventJournal
    from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
    from bt_api_gateway.service_component import GatewayServiceAccountScope
    from bt_api_gateway.session_guard import LocalAccountSessionGuard

    from bt_api_py.gateway_operator import (
        GatewayAccountSessionBinding,
        GatewayOperatorDeployment,
        GatewayOperatorDeploymentError,
    )
    from bt_api_py.service_host import TrustedServiceAuthority

    _gateway_profile_config(
        config,
        role="account_session_worker",
        ports=frozenset({"service.account_read", "service.provider_session"}),
    )
    if (
        type(authority) is not TrustedServiceAuthority
        or type(journal) is not GatewayEventJournal
        or type(account_scope) is not GatewayServiceAccountScope
        or not isinstance(principals, Mapping)
        or not principals
        or any(
            type(principal_ref) is not str
            or type(value) is not GatewayPrincipal
            or value.principal_id != principal_ref
            or value.account_scopes
            != frozenset({account_scope.journal_account_scope})
            or value.strategy_scopes != frozenset({state_scope})
            or value.allowed_kinds != frozenset({GatewayCommandKind.READ})
            for principal_ref, value in principals.items()
        )
        or not isinstance(guard_directory, (str, Path))
        or type(state_scope) is not str
        or not state_scope
        or state_scope != state_scope.strip()
        or type(owner_ref) is not str
        or not owner_ref
        or owner_ref != owner_ref.strip()
    ):
        raise GatewayOperatorAccountProfileError("TRUSTED_BINDING_INVALID")

    directory = Path(guard_directory)
    coverage = (
        AccountResourceKey(
            product_type=_ACCOUNT_RESOURCE_PRODUCT,
            margin_pool=_ACCOUNT_RESOURCE_MARGIN,
        ),
    )

    def deployment_factory() -> GatewayOperatorDeployment:
        # The profile factory runs on GatewayOperatorApplication's owner
        # thread, so the guard and child adapter share the proper owner.
        session_owner = CtpSimNowSet2AccountProcessOwner()
        session_guard = LocalAccountSessionGuard(
            directory,
            session_owner.account_key.account_key_id,
            owner_ref,
        )
        binding = GatewayAccountSessionBinding(
            account_session_ref=CTP_SIMNOW_SET2_7X24_SESSION_REF,
            journal=journal,
            account_key=session_owner.account_key,
            account_scope=account_scope,
            state_scope=state_scope,
            guard=session_guard,
            required_coverage=coverage,
            principals=principals,
            require_open_orders=True,
            refresh_interval_seconds=_ACCOUNT_REFRESH_INTERVAL_SECONDS,
            max_staleness_seconds=_ACCOUNT_MAX_STALENESS_SECONDS,
            request_timeout_seconds=_ACCOUNT_REQUEST_TIMEOUT_SECONDS,
        )
        try:
            return GatewayOperatorDeployment(
                config,
                authority,
                registry_path,
                resource_provider=_reject_unexpected_service_component,
                session_owner=session_owner,
                account_session=binding,
            )
        except GatewayOperatorDeploymentError:
            raise

    return GatewayOperatorProfile(
        profile_ref=CTP_SIMNOW_SET2_7X24_OPERATOR_PROFILE_REF,
        role="account_session_worker",
        deployment_factory=deployment_factory,
    )


def _read_ctp_market_environment() -> tuple[str, str, str]:
    """Return local read-only MD credentials without logging or forwarding them."""

    try:
        user_id = os.environ.get("CTP_USER_ID", "").strip()
        broker_id = os.environ.get("CTP_BROKER_ID", "").strip()
        password = os.environ.get("CTP_PASSWORD", "").strip()
    except Exception:
        raise CtpSimNowAccountProfileError("ACCOUNT_IDENTITY_UNAVAILABLE") from None
    if not user_id or not broker_id or not password:
        raise CtpSimNowAccountProfileError("ACCOUNT_IDENTITY_UNAVAILABLE")
    return user_id, broker_id, password


def build_ctp_simnow_set2_market_operator_profile(
    config: object,
    authority: object,
    registry_path: str | Path,
    *,
    key_universe: tuple[object, ...],
    principal_allowed_keys: Mapping[str, frozenset[object]],
) -> GatewayOperatorProfile:
    """Build the existing Gateway MarketHub over the typed CTP quote-v2 port.

    ``key_universe`` and its audience are server-owned configuration. The
    provider and CTP stream are created lazily through the existing operator;
    profile resolution and deployment construction perform no native I/O.
    """

    from bt_api_base.market_protocol import MarketSubscriptionKey

    from bt_api_py.gateway_operator import GatewayOperatorDeployment
    from bt_api_py.gateway_operator_market import GatewayMarketBinding
    from bt_api_py.service_host import TrustedServiceAuthority

    _gateway_profile_config(
        config,
        role="market_hub",
        ports=frozenset({"service.market_source"}),
    )
    if (
        type(authority) is not TrustedServiceAuthority
        or type(key_universe) is not tuple
        or not key_universe
        or len(key_universe) > 1_024
        or any(
            type(key) is not MarketSubscriptionKey
            or key.source_ref != CTP_SIMNOW_SET2_7X24_MARKET_SOURCE_REF
            or key.entitlement_ref != CTP_SIMNOW_SET2_7X24_MARKET_ENTITLEMENT_REF
            or key.channel != "ctp.quote.v2"
            or key.depth is not None
            or key.timeframe is not None
            or key.instrument.provider != "ctp"
            or key.instrument.environment != "simnow"
            or key.instrument.instrument_id != key.instrument.native_symbol
            for key in key_universe
        )
        or len(set(key_universe)) != len(key_universe)
        or not isinstance(principal_allowed_keys, Mapping)
        or set(principal_allowed_keys) != set(authority.principals.values())
    ):
        raise GatewayOperatorAccountProfileError("MARKET_UNIVERSE_INVALID")

    def create_provider() -> object:
        from bt_api_ctp.feeds.live_ctp_feed import CtpMarketStream
        from bt_api_ctp.market_provider import CtpQuoteV2MarketProvider

        user_id, broker_id, password = _read_ctp_market_environment()
        from bt_api_ctp.ctp_env_selector import official_simnow_fronts

        td_front, md_front = official_simnow_fronts(_CTP_SIMNOW_SET2_PROFILE)

        def stream_factory(key: object, quote_queue: object) -> object:
            if type(key) is not MarketSubscriptionKey:
                raise GatewayOperatorAccountProfileError("MARKET_UNIVERSE_INVALID")
            return CtpMarketStream(
                data_queue=quote_queue,
                broker_id=broker_id,
                user_id=user_id,
                password=password,
                td_front=td_front,
                md_front=md_front,
                ctp_env_profile=_CTP_SIMNOW_SET2_PROFILE,
                require_ctp_profile=_CTP_SIMNOW_SET2_PROFILE,
                auto_detect_fronts=False,
                asset_type=key.instrument.product_type.upper(),
            )

        return CtpQuoteV2MarketProvider(
            stream_factory=stream_factory,
            source_ref=CTP_SIMNOW_SET2_7X24_MARKET_SOURCE_REF,
            entitlement_ref=CTP_SIMNOW_SET2_7X24_MARKET_ENTITLEMENT_REF,
            queue_capacity=4_096,
            connect_timeout_seconds=30.0,
            stop_timeout_seconds=5.0,
        )

    binding = GatewayMarketBinding(
        key_universe=key_universe,
        principal_allowed_keys=principal_allowed_keys,
        provider_factory=create_provider,
        max_provider_events_per_maintain=32,
    )

    def deployment_factory() -> GatewayOperatorDeployment:
        return GatewayOperatorDeployment(
            config,
            authority,
            registry_path,
            resource_provider=_reject_unexpected_service_component,
            market_binding=binding,
        )

    return GatewayOperatorProfile(
        profile_ref=CTP_SIMNOW_SET2_7X24_MARKET_OPERATOR_PROFILE_REF,
        role="market_hub",
        deployment_factory=deployment_factory,
    )


__all__ = [
    "CTP_SIMNOW_SET2_7X24_PROFILE_REF",
    "CTP_SIMNOW_SET2_7X24_SESSION_REF",
    "CTP_SIMNOW_SET2_7X24_OPERATOR_PROFILE_REF",
    "CTP_SIMNOW_SET2_7X24_MARKET_OPERATOR_PROFILE_REF",
    "CTP_SIMNOW_SET2_7X24_MARKET_SOURCE_REF",
    "CTP_SIMNOW_SET2_7X24_MARKET_ENTITLEMENT_REF",
    "CtpSimNowAccountProfileError",
    "GatewayOperatorAccountProfileError",
    "CtpSimNowSet2AccountProcessOwner",
    "build_ctp_simnow_set2_account_operator_profile",
    "build_ctp_simnow_set2_market_operator_profile",
    "ctp_simnow_set2_account_key_from_environment",
    "ctp_simnow_set2_account_process_child",
    "create_ctp_simnow_set2_account_provider",
    "project_ctp_native_stop_receipt",
]
