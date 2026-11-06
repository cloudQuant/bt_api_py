"""
Integrate all exchange APIs using this BtApi class
通过 ExchangeRegistry 实现交易所的即插即用，新增交易所无需修改此文件
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import math
import queue
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import wraps
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from bt_api_base.event_bus import EventBus
from bt_api_base.exceptions import (
    ExchangeNotFoundError,
    InvalidOrderError,
    SubscribeError,
)
from bt_api_base.logging_factory import _LoggerProxy, get_logger
from bt_api_base.registry import ExchangeRegistry

from ._contracts.errors import (
    CapabilityNotSupportedError,
    LegacyForwardingRetiredError,
    LegacyOrderApiError,
    NormalizedApiError,
)
from ._contracts.models import (
    CancelAllRequest,
    CancelOrderRequest,
    CommandStatus,
    Consistency,
    FeeSchedule,
    ForwardingConfig,
    Freshness,
    FundingSnapshot,
    InstrumentSpec,
    OrderRequest,
    OrderType,
    PositionModeUpdate,
    QueryOrderRequest,
    Side,
    TradingReadiness,
    TransportMode,
)
from ._ctp_recovery_query import (
    CtpRecoveryQueryMixin,
    _CTP_EXECUTION_RECOVERY_BUNDLE_SCOPE_VERSION,
)
from ._ctp_ingress import (
    _CtpMarketConsumerQueue,
    _CtpMarketIngressEnvelope,
    _CtpMarketIngressQueue,
    _CtpPrivateIngressFence,
    _CtpPrivateIngressQueue,
)
from ._ctp_execution_authorization import (
    _CONTEXT_FIELDS,
    APPROVAL_PURPOSE,
    ENTRY_APPROVAL_SCHEMA_VERSION,
    RECOVERY_APPROVAL_PURPOSE,
    RECOVERY_APPROVAL_SCHEMA_VERSION,
    RECOVERY_APPROVAL_SCOPE_VERSION,
    SIMNOW_APPROVAL_SCHEMA_VERSION,
    SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION,
    SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION,
    CtpExecutionApproval,
    CtpExecutionApprovalCapability,
    CtpExecutionApprovalContext,
    _is_ctp_entry_write_guard,
    _is_ctp_execution_approval_capability,
    _jsonable,
    _new_capability,
    _new_ctp_entry_write_guard,
    _new_runtime_context,
    _normalize_context,
    _refresh_runtime_context,
    _thaw,
    current_ctp_execution_revocation_snapshot,
    recovery_action_digest,
    recovery_plan_digest,
    revalidate_ctp_execution_approval,
    verify_ctp_execution_approval,
)

from .balance_manager import BalanceManagerMixin
from .data_downloader import DataDownloaderMixin

if TYPE_CHECKING:
    from bt_api_ctp.managed_request_identity import CtpCancelIdentityBinding
    from bt_api_base.account_session_protocol import (
        AccountPreflightRequest,
        AccountPreflightSnapshot,
        AccountSessionPort,
        AccountSessionSnapshot,
        SettlementConfirmationOutcome,
        SettlementConfirmationPort,
        SettlementConfirmationRequest,
        SettlementVerificationReceipt,
    )
    from bt_api_base.account_balance_snapshot_protocol import AccountBalanceSnapshotPort
    from bt_api_base.account_position_snapshot_protocol import AccountPositionSnapshotPort
    from bt_api_base.execution_correlation_protocol import (
        OrderCorrelationIssuer,
    )
    from bt_api_base.execution_economics_protocol import ExecutionEconomicsPort
    from bt_api_base.execution_identity_protocol import ExecutionIdentityAssurancePort
    from bt_api_base.execution_identity_protocol import ExecutionIdentityRouteExpectation
    from bt_api_base.account_risk_protocol import ExecutionIdentityVectorBinding
    from bt_api_base.futures_expected_calendar_protocol import FuturesExpectedCalendarPort
    from bt_api_base.execution_rules_protocol import (
        ExecutionRulesPort,
        ExecutionRulesSnapshotPort,
    )
    from bt_api_base.order_position_evidence_protocol import (
        OrderPositionEvidenceFacadePort,
    )
    from bt_api_base.market_protocol import MarketInstrumentKey
    from bt_api_base.market_history_protocol import MarketBar
    from bt_api_base.market_stream_protocol import MarketStreamPort
    from bt_api_base.market_session_protocol import MarketSessionResolver

    from .execution_capacity import ExecutionCapacityFacade
    from .execution_economics_composition import ExecutionEconomicsOwnerResolver
    from .facade_protocol import (
        CorrelatedOrderExecutionFacade,
        ReconciliationFacadePort,
    )
    from .reconciliation_routes import ReconciliationRouteComposer
    from .service_market_tick_projection import ServiceMarketTickProjector
    from .market_tick_composition import MarketTickRuntimeFactory
    from .query_facade import QueryFacade

# 导入注册模块，确保交易所在使用前完成注册。
# 自动扫描 exchange_registers/ 下所有模块，无需手动维护 import 列表。
__all__ = ["BtApi"]


def _execution_identity_snapshot_contract_types():
    """Load additive snapshot contracts without raising the SDK's base floor."""
    module_name = "bt_api_base.execution_identity_protocol"
    try:
        protocol = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise
    names = (
        "ExecutionIdentitySnapshot",
        "ExecutionIdentityAssurance",
        "ExecutionIdentitySnapshotUnavailable",
        "ExecutionIdentityAssurancePort",
    )
    values = tuple(getattr(protocol, name, None) for name in names)
    if any(not isinstance(value, type) for value in values):
        return None
    return values


def _execution_identity_scope_ref_contract_type():
    """Load the optional opaque identity-scope marker without widening imports."""
    module_name = "bt_api_base.execution_identity_protocol"
    try:
        protocol = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise
    ref_type = getattr(protocol, "ExecutionIdentityScopeRef", None)
    return ref_type if isinstance(ref_type, type) else None


def _execution_identity_evidence_contract_types():
    """Load the optional exact declarations for unified identity evidence."""
    module_name = "bt_api_base.execution_identity_protocol"
    try:
        protocol = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise
    values = tuple(
        getattr(protocol, name, None)
        for name in (
            "ExecutionIdentityRouteExpectation",
            "ExecutionIdentityEvidenceUnavailable",
            "SelectedExecutionIdentityRoute",
        )
    )
    if any(not isinstance(value, type) for value in values):
        return None
    return values


def _execution_identity_evidence_error(code):
    """Return the exact finite evidence error when the optional base has it."""
    contract_types = _execution_identity_evidence_contract_types()
    if contract_types is not None:
        return contract_types[1](code)
    return NormalizedApiError("execution_identity_evidence", code, definite_reject=True)


def _execution_identity_snapshot_error(code):
    """Use the exact base finite error where available, else SDK normalization."""
    contract_types = _execution_identity_snapshot_contract_types()
    if contract_types is not None:
        return contract_types[2](code)
    return NormalizedApiError("execution_identity_snapshot", code, definite_reject=True)


def _account_risk_snapshot_contract_types():
    """Load optional SDK-owned risk DTOs without raising the base-version floor."""
    module_name = "bt_api_base.account_risk_protocol"
    try:
        protocol = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise
    names = (
        "AccountRiskSnapshot",
        "AccountRiskSnapshotUnavailable",
        "ExecutionIdentityVectorBinding",
        "ExecutionIdentityVectorBindingRef",
    )
    values = tuple(getattr(protocol, name, None) for name in names)
    if any(not isinstance(value, type) for value in values):
        return None
    fact_fields = getattr(protocol, "ACCOUNT_RISK_FACT_FIELDS", None)
    if (
        type(fact_fields) is not tuple
        or not fact_fields
        or any(type(name) is not str or not name for name in fact_fields)
        or len(set(fact_fields)) != len(fact_fields)
    ):
        return None
    return values + (fact_fields,)


def _account_risk_snapshot_error(code):
    """Use the exact optional base finite error where available."""
    contract_types = _account_risk_snapshot_contract_types()
    if contract_types is not None:
        return contract_types[1](code)
    return NormalizedApiError("account_risk_snapshot", code, definite_reject=True)

DATANAME_SEPARATOR = "___"
_NORMALIZED_WRITE_OPERATIONS = frozenset({"make_order", "cancel_order", "set_position_mode"})
_SIMNOW_BOUND_PROFILES = frozenset({"config_front_pair", "set1_group1", "set1_group2"})
_SIMNOW_RESTRICTED_PROFILES = _SIMNOW_BOUND_PROFILES | frozenset(
    {
        "set2_7x24",
        "set1_group1_vpn",
        "set2_7x24_4000x",
        "set2_7x24_vpn",
        "set1",
        "set2",
    }
)
_CTP_CREDENTIAL_BINDING_FIELDS = frozenset(
    {"credential_binding_key_id", "credential_binding_hmac_sha256"}
)
_CTP_TRANSITION_LOCK_INIT = threading.Lock()
_CTP_INTERNAL_AUTHORIZATION_SEAL = object()
_CTP_CONTROLLED_TEST_AUTHORITY_SEAL = object()
_U1A_RUNTIME_DISTRIBUTIONS = (
    "bt_api_base",
    "numpy",
    "python-dotenv",
    "requests",
    "websocket-client",
    "pyyaml",
    "pandas",
    "aiohttp",
    "pytz",
    "python-rapidjson",
    "httpx",
    "pydantic",
    "websockets",
    "typing-extensions",
    "cryptography",
    "bt_api_ctp",
)


class _CtpExecutionArmAuthorization:
    """Private one-shot facade token for the CTP native arm bridge.

    Public proof dictionaries remain useful audit evidence, but they are not
    authority.  This token is created only by the deliberately internal
    controlled issuer below and carries the corresponding native one-shot
    authorization as well as the local session binding.
    """

    __slots__ = (
        "_seal",
        "_api",
        "_venue",
        "_proof",
        "_context",
        "_native_authorization",
        "_strategy_identity_sha256",
        "_execution_cycle_id",
        "_preflight_epoch",
        "_used",
        "_approval_capability",
        "_entry_write_guard",
        "_recovery_plan_sha256",
        "_recovery_action_sha256",
        "_recovery_token_sha256",
    )

    def __init__(
        self,
        *,
        api: object,
        venue: str,
        proof: Mapping[str, Any],
        context: Mapping[str, Any],
        native_authorization: object,
        strategy_identity_sha256: str,
        execution_cycle_id: str,
        preflight_epoch: int,
        approval_capability: CtpExecutionApprovalCapability | None = None,
        entry_write_guard: object | None = None,
        recovery_plan_sha256: str | None = None,
        recovery_action_sha256: str | None = None,
        recovery_token_sha256: str | None = None,
    ) -> None:
        self._seal = _CTP_INTERNAL_AUTHORIZATION_SEAL
        self._api = api
        self._venue = venue
        self._proof = dict(proof)
        self._context = dict(context)
        self._native_authorization = native_authorization
        self._strategy_identity_sha256 = strategy_identity_sha256
        self._execution_cycle_id = execution_cycle_id
        self._preflight_epoch = preflight_epoch
        self._used = False
        self._approval_capability = approval_capability
        self._entry_write_guard = entry_write_guard
        self._recovery_plan_sha256 = recovery_plan_sha256
        self._recovery_action_sha256 = recovery_action_sha256
        self._recovery_token_sha256 = recovery_token_sha256


class _CtpControlledTestAuthority:
    """Opaque marker for the deliberately internal offline issuer seam."""

    __slots__ = ("_seal",)

    def __init__(self, seal: object) -> None:
        if seal is not _CTP_CONTROLLED_TEST_AUTHORITY_SEAL:
            raise TypeError("controlled CTP test authority required")
        self._seal = seal


def _issue_ctp_controlled_test_authority_for_core() -> object:
    """Return an internal test authority, never a public arm credential."""

    return _CtpControlledTestAuthority(_CTP_CONTROLLED_TEST_AUTHORITY_SEAL)


def _is_ctp_controlled_test_authority(value: object) -> bool:
    return bool(
        type(value) is _CtpControlledTestAuthority
        and getattr(value, "_seal", None) is _CTP_CONTROLLED_TEST_AUTHORITY_SEAL
    )


class _CtpSettlementAuthorization:
    """Private facade wrapper for an independent native settlement token."""

    __slots__ = (
        "_seal",
        "_api",
        "_venue",
        "_context",
        "_native_authorization",
        "_used",
    )

    def __init__(
        self,
        *,
        api: object,
        venue: str,
        context: Mapping[str, Any],
        native_authorization: object,
    ) -> None:
        self._seal = _CTP_INTERNAL_AUTHORIZATION_SEAL
        self._api = api
        self._venue = venue
        self._context = dict(context)
        self._native_authorization = native_authorization
        self._used = False


def _is_ctp_exchange(exchange_name: object) -> bool:
    """Return whether an exchange name belongs to the CTP plugin."""

    return str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() == "CTP"


def _require_ctp_read_only_settings(
    exchange_name: object,
    exchange_params: dict[str, Any],
    *,
    operation: str,
) -> None:
    """Reject CTP settings which could write before the SDK's preflight gate."""

    if not _is_ctp_exchange(exchange_name):
        return
    from bt_api_ctp.preflight_settings import require_read_only_settings

    require_read_only_settings(
        exchange_params, operation=operation, error_type=NormalizedApiError
    )


def _is_native_ctp_quote_v2_ticker(item: Any) -> bool:
    """Ask the selected provider to identify its exact native ticker."""
    try:
        from bt_api_ctp.quote_v2_provenance import _is_native_ctp_quote_v2_ticker as check
    except Exception:
        return False
    return check(item)




def _serialized_ctp_execution_transition(
    method: Callable[..., Any],
) -> Callable[..., Any]:
    """Serialize public CTP gate/session transitions on one SDK instance."""

    @wraps(method)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        lock = getattr(self, "_ctp_execution_transition_lock", None)
        if lock is None:
            with _CTP_TRANSITION_LOCK_INIT:
                lock = getattr(self, "_ctp_execution_transition_lock", None)
                if lock is None:
                    lock = threading.RLock()
                    self._ctp_execution_transition_lock = lock
        with lock:
            return method(self, *args, **kwargs)

    return wrapped


def _canonical_ctp_account_fingerprint(value: Any) -> str:
    """Map the native short hash to the public receipt identity."""
    from bt_api_ctp.query_result_evidence import canonical_ctp_account_fingerprint

    return canonical_ctp_account_fingerprint(value)


def _approval_material_digest(value: Any) -> str:
    """Hash one locally supplied approval material without widening types."""

    if isinstance(value, Path):
        try:
            material = value.read_bytes()
        except (OSError, ValueError):
            raise ValueError("approval_material_unavailable") from None
    elif isinstance(value, bytes):
        material = value
    elif isinstance(value, str):
        material = value.encode("utf-8", "strict")
    elif isinstance(value, (Mapping, list, tuple)):
        try:
            material = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8", "strict")
        except (TypeError, UnicodeEncodeError, ValueError):
            raise ValueError("approval_material_unavailable") from None
    else:
        raise ValueError("approval_material_unavailable")
    return hashlib.sha256(material).hexdigest()


def _package_source_manifest_digest(module_name: str) -> str:
    """Hash the source/native files of one package as loaded by this process."""

    try:
        from importlib import import_module

        module = import_module(module_name)
        module_path = Path(str(getattr(module, "__file__", "") or "")).resolve()
        if not module_path.is_file():
            raise ValueError("package_path_unavailable")
        package_root = module_path.parent
        paths = sorted(
            (
                path
                for path in package_root.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix.lower() in {".py", ".so", ".dylib", ".pyd"}
            ),
            key=lambda path: path.relative_to(package_root).as_posix(),
        )
        if not paths:
            raise ValueError("package_manifest_empty")
        manifest = [
            {
                "path": path.relative_to(package_root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in paths
        ]
        return _approval_material_digest(manifest)
    except Exception:
        raise ValueError("package_identity_unavailable") from None


def _dependency_source_manifest_digest() -> str:
    """Hash dependencies used by protected direct CTP execution.

    The direct CTP path does not use the optional ZeroMQ transport or the
    optional spdlog backend, so their presence must not affect this identity.
    The manifest records each required distribution and every installed file
    digest; it is collected at approval time instead of accepting a
    caller-provided dependency hash. Changing this set changes the approval
    identity, so approvals bound to an earlier set must be issued again.
    """

    try:
        from importlib import metadata

        manifest = []
        for distribution_name in _U1A_RUNTIME_DISTRIBUTIONS:
            distribution = metadata.distribution(distribution_name)
            name = str(distribution.metadata.get("Name") or "").strip()
            version = str(distribution.version or "").strip()
            files = distribution.files
            if not name or not version or files is None:
                raise ValueError("dependency_metadata_unavailable")
            file_manifest = []
            for relative in sorted(files, key=lambda item: str(item)):
                path = Path(str(distribution.locate_file(relative)))
                if not path.is_file():
                    raise ValueError("dependency_file_unavailable")
                file_manifest.append(
                    {
                        "path": str(relative),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
            if not file_manifest:
                raise ValueError("dependency_manifest_empty")
            manifest.append({"name": name, "version": version, "files": file_manifest})
        if not manifest:
            raise ValueError("dependency_manifest_empty")
        return _approval_material_digest(manifest)
    except Exception:
        raise ValueError("dependency_identity_unavailable") from None


_reg_logger = get_logger("registry")


def _provider_execution_configuration_policy(exchange_name: str, *, operation: str):
    """Resolve the exact provider declaration without exposing configuration values."""
    try:
        from .execution_configuration_policy import execution_configuration_policy

        return execution_configuration_policy(exchange_name)
    except Exception:
        raise NormalizedApiError(
            operation, "provider_execution_policy_unavailable", definite_reject=True
        ) from None


def _credential_alias_value(parameters: Mapping[str, Any], aliases: tuple[str, ...]) -> str | None:
    supplied = []
    for alias in aliases:
        if alias not in parameters or parameters[alias] is None:
            continue
        value = parameters[alias]
        if value == "":
            continue
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise NormalizedApiError(
                "configure_execution",
                "private_credentials_malformed",
                definite_reject=True,
            )
        supplied.append(value)
    if len(set(supplied)) > 1:
        raise NormalizedApiError(
            "configure_execution", "credential_alias_conflict", definite_reject=True
        )
    return supplied[0] if supplied else None


def _execution_credential_fingerprints(
    settings: Mapping[str, Any] | None,
    config: Mapping[str, Any],
    transport_mode: TransportMode,
) -> dict[str, str]:
    """Preflight private credentials and return hashes of public identifiers only."""
    if config["market_data_only"] or transport_mode is not TransportMode.DIRECT:
        return {}
    fingerprints = {}
    settings = dict(settings or {})
    for venue in sorted(settings):
        parameters = settings.get(venue, {})
        provider = str(venue).partition(DATANAME_SEPARATOR)[0].upper()
        policy = _provider_execution_configuration_policy(venue, operation="configure_execution")
        aliases = dict(policy.credential_aliases)
        if not aliases:
            continue
        if not isinstance(parameters, Mapping):
            raise NormalizedApiError(
                "configure_execution",
                "invalid_exchange_credentials",
                definite_reject=True,
            )
        values = {
            name: _credential_alias_value(parameters, names) for name, names in aliases.items()
        }
        if any(value is None for value in values.values()):
            raise NormalizedApiError(
                "configure_execution",
                "private_credentials_missing",
                definite_reject=True,
            )
        public_identifier = values["public"]
        material = f"bt-api-py\0{provider}\0{public_identifier}".encode()
        fingerprints[str(venue).strip()] = hashlib.sha256(material).hexdigest()
    return fingerprints


def _execution_credential_lineage_ref(selected_client: object) -> object | None:
    """Read only the provider-issued opaque marker paired with a feed binding."""
    module_name = "bt_api_base.execution_request_receipt_protocol"
    try:
        protocol = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise
    lineage_source_port = getattr(protocol, "ExecutionCredentialLineageSourcePort", None)
    opaque_ref_type = getattr(protocol, "ExecutionCredentialRequestOpaqueRef", None)
    if not isinstance(lineage_source_port, type) or not isinstance(opaque_ref_type, type):
        return None
    if not isinstance(selected_client, lineage_source_port):
        return None
    marker = selected_client.execution_credential_lineage_ref()
    if type(marker) is not opaque_ref_type:
        return None
    return marker


_plugins_loaded = False

_MAX_EVENT_WAKERS = 32
_MAX_EVENT_WAKER_CALLBACK_ERRORS = 2**31 - 1


class _EventWakerSubscription:
    __slots__ = ("callback", "active", "in_flight")

    def __init__(self, callback: Callable[[], Any]) -> None:
        self.callback: Callable[[], Any] | None = callback
        self.active = True
        self.in_flight = 0


def _initialize_plugins() -> None:
    """Load provider plugins into the direct ExchangeRegistry only."""
    from bt_api_base.plugins.loader import PluginLoader

    registry = ExchangeRegistry._get_default()
    loader = PluginLoader(registry)
    loader.load_all()


def _ensure_plugins_loaded() -> None:
    """惰性加载插件（首次 BtApi 实例化时），坏插件不阻断整体导入。"""
    global _plugins_loaded
    if _plugins_loaded:
        return
    try:
        _initialize_plugins()
    except Exception as exc:
        get_logger("api").warning(f"Plugin loading degraded: {type(exc).__name__}: {exc}")
    finally:
        _plugins_loaded = True


# 常用异步方法白名单（A-09：__getattr__ 只代理白名单内的方法，避免 hasattr 恒真）
class BtApi(CtpRecoveryQueryMixin, DataDownloaderMixin, BalanceManagerMixin):
    """统一多交易所 API 入口，通过 ExchangeRegistry 实现交易所即插即用。"""

    exchange_kwargs: dict[str, Any]
    debug: bool
    data_queues: dict[str, queue.Queue[Any]]
    exchange_feeds: dict[str, Any]
    logger: _LoggerProxy
    _value_dict: dict[str, Any]
    _cash_dict: dict[str, Any]
    subscribe_bar_num: int
    event_bus: EventBus
    _subscription_flags: dict[str, bool]
    transport_mode: TransportMode
    _backend: Any

    def __init__(
        self,
        exchange_kwargs: dict[str, Any] | None = None,
        debug: bool = True,
        event_bus: EventBus | None = None,
        *,
        transport_mode: TransportMode | str = TransportMode.DIRECT,
        forwarding_config: ForwardingConfig | None = None,
        gateway_wire_config: object | None = None,
        execution_config: dict[str, Any] | None = None,
        reconciliation_composer: ReconciliationRouteComposer | None = None,
        order_correlation_issuer: OrderCorrelationIssuer | None = None,
        provider_execution_contexts: Mapping[str, object] | None = None,
        execution_economics_owner_resolver: ExecutionEconomicsOwnerResolver | None = None,
        market_runtime_factory: MarketTickRuntimeFactory | None = None,
        write_admission: Mapping[str, bool] | None = None,
        market_clock_verification: bool | Mapping[str, Any] = False,
    ) -> None:
        """初始化 BtApi 实例。

        Args:
            exchange_kwargs: 交易所配置 dict，key 为 exchange_name，value 为对应参数。
            debug: 是否开启 debug 模式，控制日志输出。
            event_bus: 事件总线实例，用于 BarEvent/OrderEvent 等回调；None 则创建默认实例。
            transport_mode: direct（默认，直接持有 Feed）。zmq 模式已退役：
                传入 ZMQ 会在建立任何 socket/线程前抛出
                LegacyForwardingRetiredError。
            forwarding_config: ZMQ 模式的历史配置输入；该模式退役后仅保留
                作显式拒绝输入（ForwardingConfig 仍可构造）。
            gateway_wire_config: 历史 gateway writer 配置；非 None 时在任何
                plugin/feed/backend 初始化前抛出 LegacyForwardingRetiredError。
            execution_config: Optional durable execution session. None preserves
                legacy behavior; configured writes require typed normalized calls.
            reconciliation_composer: Trusted SDK-owned exact-route composer for
                optional neutral reconciliation reads.
            order_correlation_issuer: Optional durable correlation issuer injected
                by trusted SDK runtime composition. It is not inferred from paths
                or exchange configuration.
            provider_execution_contexts: Opaque provider runtime bindings keyed by
                exact SDK exchange name. Values are passed unchanged only to the
                registered provider factory for that selected route.
            execution_economics_owner_resolver: Trusted SDK composition callback
                that resolves the exact selected route to its SDK-owned evidence
                owner. Missing owners remain unavailable; this callback does not
                itself grant order authorization.
            market_runtime_factory: Trusted SDK host factory for service-market
                clients. It must create and validate clients on its owner thread;
                BtApi does not infer a service runtime from a selected feed.
        """
        requested_transport_mode = TransportMode(transport_mode)
        if requested_transport_mode is TransportMode.ZMQ:
            # Keep the established ZMQ retirement diagnostic while rejecting
            # before logger, event-bus, plugin, feed, socket, or thread setup.
            raise LegacyForwardingRetiredError(
                "transport_mode=ZMQ",
                detail=(
                    "the legacy ZMQ forwarding backend was retired; use TransportMode.DIRECT "
                    "for ordinary direct/backtest calls or Backtrader "
                    "ServiceExecutionSession for managed service writes"
                ),
            )
        if gateway_wire_config is not None:
            raise LegacyForwardingRetiredError(
                "gateway_wire_config",
                detail=(
                    "the legacy gateway wire writer was retired; managed service writes "
                    "must use Backtrader ServiceExecutionSession with ServiceHost and "
                    "service.order_entry"
                ),
            )
        if forwarding_config is not None:
            raise LegacyForwardingRetiredError(
                "forwarding_config",
                detail=(
                    "legacy forwarding configuration is not accepted with any transport; "
                    "remove it for ordinary direct/backtest use. Managed service operations "
                    "must use SDK ServiceClientRuntime bindings or "
                    "Backtrader ServiceExecutionSession."
                ),
            )
        if reconciliation_composer is not None:
            from .reconciliation_routes import ReconciliationRouteComposer

            if type(reconciliation_composer) is not ReconciliationRouteComposer:
                raise TypeError("reconciliation_composer must be an exact SDK route composer")
        if (
            execution_economics_owner_resolver is not None
            and not callable(execution_economics_owner_resolver)
        ):
            raise TypeError("execution_economics_owner_resolver must be callable")
        if market_runtime_factory is not None:
            from .market_tick_composition import MarketTickRuntimeFactory

            if not isinstance(market_runtime_factory, MarketTickRuntimeFactory):
                raise TypeError("market_runtime_factory must implement MarketTickRuntimeFactory")
        if provider_execution_contexts is None:
            execution_contexts = {}
        else:
            if not isinstance(provider_execution_contexts, Mapping):
                raise TypeError("provider_execution_contexts must be a mapping")
            execution_contexts = dict(provider_execution_contexts)
            if any(
                type(name) is not str or not name or context is None
                for name, context in execution_contexts.items()
            ):
                raise TypeError("provider_execution_contexts requires exact route keys and values")
        self.exchange_kwargs = {}
        self.debug = debug
        self.data_queues = {}
        self.exchange_feeds = {}
        # Keep a private identity snapshot for SDK-selected feeds. Public
        # callers can inspect ``exchange_feeds`` for compatibility, so route
        # assurance must not trust a feed object that was later substituted.
        self._selected_route_clients = MappingProxyType({})
        self.logger = self.init_logger()
        self._value_dict = {}
        self._cash_dict = {}
        self.subscribe_bar_num = 0
        self.event_bus = event_bus or EventBus()
        self._subscription_flags = {}
        self._subscription_streams: list[Any] = []
        self._initialize_event_waker_state()
        self._normalized_event_pending: defaultdict[str, deque[Any]] = defaultdict(deque)
        self._position_modes: dict[str, str] = {}
        self._position_mode_lock = threading.RLock()
        self._ctp_execution_transition_lock = threading.RLock()
        self._ctp_private_ingress_fences: dict[str, _CtpPrivateIngressFence] = {}
        self._ctp_private_ingress_queues: dict[str, _CtpPrivateIngressQueue] = {}
        self._ctp_market_ingress_seal = object()
        self._ctp_market_ingress_queues: dict[str, _CtpMarketIngressQueue] = {}
        self._ctp_market_consumer_queues: dict[str, _CtpMarketConsumerQueue] = {}
        self._position_mode_reconcile_required: dict[str, str] = {}
        self._position_mode_active_placements: dict[str, int] = {}
        self._execution_session: Any | None = None
        self._reconciliation_composer = reconciliation_composer
        self._order_correlation_issuer = order_correlation_issuer
        self._execution_economics_owner_resolver = execution_economics_owner_resolver
        self._market_runtime_factory = market_runtime_factory
        self._execution_rules_router: Any | None = None
        self._execution_rules_snapshot_router: Any | None = None
        self._order_position_evidence_router: Any | None = None
        self._execution_identity_assurance_router: Any | None = None
        # Values remain opaque and retain their exact object identity. The
        # copied mapping prevents later caller mutation from rebinding a route.
        self._provider_execution_contexts = MappingProxyType(execution_contexts)
        self._sdk_capability_lock = threading.RLock()
        # Iteration 62: environment-neutral write admission (default: all
        # routes disabled) and the lazily created direct write coordinator.
        from .write_admission import WriteAdmission

        self._write_admission = WriteAdmission(write_admission)
        self._direct_write_coordinator: Any | None = None
        self._trading_session_schedules: dict[str, Any] = {}
        # Optional SDK clock qualification of direct market ticks (D62-21/27).
        self._market_clock_verifier: Any | None = None
        self._market_clock_max_latency_seconds = 2.0
        if market_clock_verification:
            from .clock_verification import ClockVerifier

            options = (
                dict(market_clock_verification)
                if isinstance(market_clock_verification, Mapping)
                else {}
            )
            self._market_clock_max_latency_seconds = float(
                options.pop("max_latency_seconds", 2.0)
            )
            refresh_interval = float(options.pop("refresh_interval_seconds", 300.0))
            self._market_clock_verifier = ClockVerifier(**options)
            self._market_clock_verifier.start_background_refresh(refresh_interval)
        # These exact-object registries retain only currentness bindings. They
        # never expose session owner tokens, ledger rows, or credential data.
        self._execution_identity_vector_bindings: dict[object, tuple[Any, ...]] = {}
        self._account_risk_bound_snapshots: dict[object, tuple[Any, ...]] = {}
        self._account_session_router: Any | None = None
        self._typed_order_provider_router: Any | None = None
        self._direct_order_provider_router: Any | None = None
        self._direct_order_selection_started = False
        self._managed_order_selection_started = False
        self._managed_configuration_requested = False
        self._execution_configuration_in_progress = False
        self._execution_economics_provider_router: Any | None = None
        self._execution_economics_router: Any | None = None
        self._typed_order_routes_resolved: set[str] = set()
        self._market_session_router: Any | None = None
        self._futures_expected_calendar_router: Any | None = None
        self._market_stream_owner: Any | None = None
        # Managed CTP feeds receive this opaque, process-local capability before
        # they are exposed through ``exchange_feeds``.  The backend resolves it
        # lazily so configure_execution() also works before or after add_exchange().
        self._ctp_execution_capability = object() if execution_config is not None else None
        # Facade-owned epoch fences internal one-shot grants across terminal
        # settlement and explicit preflight resets, including when a contract
        # test uses a lightweight native-feed double.
        self._ctp_execution_authorization_epoch = 0
        self._instrument_cache: dict[tuple[str, str | None], Any] = {}
        self._instrument_spec_cache: dict[tuple[str, str], InstrumentSpec] = {}
        self._event_metrics = defaultdict(
            int,
            {
                "raw_ingress_items": 0,
                "normalized_events": 0,
                "delivered_events": 0,
                "coalesced_events": 0,
            },
        )
        self._private_reconcile_generations: set[tuple[str, int]] = set()
        self.event_bus.on("ws.connected", self._on_websocket_connected)
        self.transport_mode = requested_transport_mode
        settings = deepcopy(exchange_kwargs or {})
        self._backend = self._build_backend(self.transport_mode, forwarding_config)
        try:
            self.configure_execution(execution_config, _exchange_names=settings)
            _ensure_plugins_loaded()
            if execution_config and execution_config.get("market_data_only"):
                for value in settings.values():
                    value["subscribe_account"] = False
            self.init_exchange(settings)
            self._validate_required_environments()
        except Exception:
            with suppress(Exception):
                self.close()
            raise

    def configure_execution(
        self,
        execution_config: dict[str, Any] | None,
        *,
        _exchange_names: Any = (),
    ) -> None:
        """Enable an optional execution session; the same configuration is idempotent.

        Configure before subscribing or submitting orders. Replacing a live
        session's journal is forbidden. The journal lock is held until close().
        """
        if execution_config is None:
            return
        with self._sdk_capability_lock:
            if self._direct_order_selection_started:
                raise NormalizedApiError(
                    "configure_execution", "direct_order_route_already_selected", definite_reject=True
                )
            if self._execution_configuration_in_progress:
                raise NormalizedApiError(
                    "configure_execution", "execution_configuration_in_progress", definite_reject=True
                )
            self._managed_configuration_requested = True
            self._execution_configuration_in_progress = True
        try:
            from ._execution_session import _ExecutionSession, session_config

            if isinstance(_exchange_names, Mapping):
                exchange_settings = dict(_exchange_names)
            else:
                names = tuple(_exchange_names or self.list_exchanges())
                exchange_settings = {
                    name: self.exchange_kwargs.get(name, {})
                    for name in names
                    if name in self.exchange_kwargs
                }
            if self.transport_mode is TransportMode.DIRECT:
                for exchange_name, exchange_params in tuple(exchange_settings.items()):
                    if isinstance(exchange_params, Mapping):
                        if not isinstance(exchange_params, dict):
                            exchange_params = dict(exchange_params)
                            exchange_settings[exchange_name] = exchange_params
                        _require_ctp_read_only_settings(
                            exchange_name,
                            exchange_params,
                            operation="configure_execution",
                        )
            config = session_config(execution_config)
            if self._ctp_execution_capability is None:
                self._ctp_execution_capability = object()
            current = self._execution_session
            if current is not None:
                comparable = dict(config)
                for path_key in ("order_journal", "account_risk_state"):
                    if comparable[path_key] is None:
                        comparable[path_key] = current.config[path_key]
                credential_fingerprints = (
                    _execution_credential_fingerprints(
                        exchange_settings, comparable, self.transport_mode
                    )
                    if exchange_settings
                    else current.credential_fingerprints
                )
                if (
                    current.config != comparable
                    or current.closed
                    or current.credential_fingerprints != credential_fingerprints
                ):
                    raise NormalizedApiError("configure_execution", "execution_already_configured")
                if self.list_exchanges():
                    self._validate_required_environments()
                return
            configured_names = set(exchange_settings) | set(config["required_environments"])
            for venue in configured_names:
                policy = _provider_execution_configuration_policy(
                    venue, operation="configure_execution"
                )
                if (
                    policy.require_environment
                    and venue not in config["required_environments"]
                ):
                    raise NormalizedApiError(
                        "configure_execution",
                        "required_environment_missing",
                        definite_reject=True,
                    )
            if (
                not config["market_data_only"]
                and self.transport_mode is TransportMode.DIRECT
                and set(config["required_environments"]) - set(exchange_settings)
            ):
                raise NormalizedApiError(
                    "configure_execution",
                    "private_credentials_missing",
                    definite_reject=True,
                )
            credential_fingerprints = _execution_credential_fingerprints(
                exchange_settings, config, self.transport_mode
            )
            if self._subscription_flags:
                raise NormalizedApiError("configure_execution", "configure_before_subscribing")
            exchange_names = tuple(
                _exchange_names.keys()
                if isinstance(_exchange_names, Mapping)
                else (_exchange_names or self.list_exchanges())
            )
            credential_lineage_bindings = {}
            for exchange_name, feed in tuple(self.exchange_feeds.items()):
                if (
                    exchange_name not in credential_fingerprints
                    or self._selected_route_clients.get(exchange_name) is not feed
                ):
                    continue
                lineage_ref = _execution_credential_lineage_ref(feed)
                if lineage_ref is not None:
                    credential_lineage_bindings[exchange_name] = (feed, lineage_ref)
            self._execution_session = _ExecutionSession(
                config,
                exchange_names=exchange_names,
                credential_fingerprints=credential_fingerprints,
                credential_lineage_bindings=credential_lineage_bindings,
            )
            try:
                for exchange_name, feed in self.exchange_feeds.items():
                    if str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() == "CTP":
                        self._configure_ctp_execution_gate(exchange_name, feed)
            except Exception:
                session = self._execution_session
                if session is not None:
                    session.close()
                self._execution_session = None
                raise
            if config["market_data_only"]:
                for value in self.exchange_kwargs.values():
                    value["subscribe_account"] = False
            if self.list_exchanges():
                try:
                    self._validate_required_environments()
                except Exception:
                    session = self._execution_session
                    if session is not None:
                        session.close()
                    self._execution_session = None
                    raise
        finally:
            with self._sdk_capability_lock:
                self._execution_configuration_in_progress = False

    def new_client_order_id(
        self,
        exchange_name: str,
        account_id: str | None = None,
        strategy_id: str | None = None,
    ) -> str:
        """Allocate a numeric client reference before the caller binds its order.

        Allocation reserves the value locally; only a persisted order intent
        consumes it. Bound managed CTP sessions must consume an existing
        code-owned OrderRef reservation instead of using this local allocator.
        """
        if self._execution_session is not None:
            return self._execution_session.new_client_order_id(
                exchange_name,
                account_id=account_id,
                strategy_id=strategy_id,
            )
        if _is_ctp_exchange(exchange_name):
            raise NormalizedApiError(
                "new_client_order_id",
                "ctp_order_identity_reservation_required",
                definite_reject=True,
            )
        import time

        return f"{time.time_ns() % 10**12:012d}"

    def consume_ctp_order_identity_reservation(
        self,
        exchange_name: str,
        *,
        scope: Any,
        identity_store: Any,
        managed_intent_id: str,
        runtime_order_id: str,
    ):
        """Candidate-only mirror of an existing I9 CTP OrderRef.

        The I9 store must already own the committed reservation. This
        candidate API only validates and fsyncs that exact identity into the
        SDK execution journal; it never calls an OrderRef allocator, queue, or
        native sender. The mirror is not authenticated authority provenance
        and is not a binding for a later OrderRequest or cancel. Do not use it
        to enable managed dispatch; a same-row prepared request, queue receipt,
        and single-worker contract are still required.
        """
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "consume_ctp_order_identity_reservation",
                "execution_session_unavailable",
                definite_reject=True,
            )
        return session.consume_ctp_order_identity_reservation(
            exchange_name,
            scope=scope,
            identity_store=identity_store,
            managed_intent_id=managed_intent_id,
            runtime_order_id=runtime_order_id,
        )

    def consume_ctp_cancel_dispatch_command(
        self,
        exchange_name: str,
        *,
        scope: Any,
        identity_store: Any,
        command_id: str,
        request: CancelOrderRequest,
    ) -> CtpCancelIdentityBinding:
        """Read back one exact I9 CTP cancel command as a non-authorizing echo.

        The result is identity only. It does not claim the I9 worker row,
        reverify fresh approval, consume a queue receipt, or enable the public
        cancel path.
        """
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "consume_ctp_cancel_dispatch_command",
                "execution_session_unavailable",
                definite_reject=True,
            )
        return session.consume_ctp_cancel_dispatch_command(
            exchange_name,
            scope=scope,
            identity_store=identity_store,
            command_id=command_id,
            request=request,
        )

    def new_runtime_order_binding(
        self,
        exchange_name: str,
        *,
        symbol: str,
        account_id: str | None = None,
        managed_intent_id: str | None = None,
        runtime_order_id: str | None = None,
        budget_capability: Any = None,
        recovery_action: bool = False,
    ) -> dict[str, Any]:
        """Atomically reserve a runtime order identity and its 12-digit CTP OrderRef."""
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "new_runtime_order_binding",
                "execution_session_required",
                definite_reject=True,
            )
        binding_options: dict[str, Any] = {}
        if managed_intent_id is not None:
            binding_options["managed_intent_id"] = managed_intent_id
        return session.new_runtime_order_binding(
            exchange_name,
            symbol=symbol,
            account_id=account_id,
            runtime_order_id=runtime_order_id,
            budget_capability=budget_capability,
            recovery_action=recovery_action,
            **binding_options,
        )

    @staticmethod
    def _require_ctp_managed_order_identity(
        operation: str, exchange_name: str, request: OrderRequest, session: Any
    ) -> None:
        if session is None or str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() != "CTP":
            return
        identity = request.ctp_order_identity
        if identity is not None:
            if request.runtime_order_id not in (
                None,
                identity.runtime_order_id,
            ) or request.managed_intent_id not in (None, identity.managed_intent_id):
                raise NormalizedApiError(
                    operation, "ctp_managed_order_identity_mismatch", definite_reject=True
                )
            return
        if request.managed_intent_id is not None:
            raise NormalizedApiError(
                operation, "ctp_order_identity_binding_missing_or_mismatch", definite_reject=True
            )
        if not request.runtime_order_id:
            # Preserve the older runtime-binding error from the session itself.
            return
        bindings = session.get_runtime_order_bindings(
            exchange_name,
            unresolved_only=False,
            runtime_order_id=request.runtime_order_id,
        )
        bound_intent_id = bindings[0].get("managed_intent_id") if len(bindings) == 1 else None
        if (
            (bound_intent_id is not None and request.managed_intent_id != bound_intent_id)
            or (request.managed_intent_id is not None and bound_intent_id is None)
            or (request.managed_intent_id is not None and request.hedge_flag not in {"1", "2", "3"})
        ):
            raise NormalizedApiError(
                operation,
                "ctp_managed_order_identity_mismatch",
                definite_reject=True,
            )

    @staticmethod
    def _bind_ctp_managed_cancel_identity(
        exchange_name: str, request: CancelOrderRequest, session: Any
    ) -> CancelOrderRequest:
        if session is None or str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() != "CTP":
            return request
        identity = request.ctp_cancel_identity
        if identity is not None:
            if (
                request.runtime_order_id not in (None, identity.runtime_order_id)
                or request.runtime_action_id not in (None, identity.managed_action_id)
                or request.managed_cancel_intent_id not in (None, identity.managed_action_id)
            ):
                raise NormalizedApiError(
                    "cancel_order", "ctp_managed_cancel_identity_mismatch", definite_reject=True
                )
            return replace(
                request,
                runtime_order_id=identity.runtime_order_id,
                runtime_action_id=identity.managed_action_id,
                managed_cancel_intent_id=identity.managed_action_id,
                client_order_id=identity.order_ref,
                order_ref=identity.order_ref,
            )
        if request.managed_cancel_intent_id is not None:
            raise NormalizedApiError(
                "cancel_order",
                "ctp_cancel_identity_binding_missing_or_mismatch",
                definite_reject=True,
            )
        if not request.runtime_order_id:
            raise NormalizedApiError(
                "cancel_order",
                "ctp_runtime_cancel_identity_required",
                definite_reject=True,
            )
        bindings = session.get_runtime_order_bindings(
            exchange_name,
            unresolved_only=False,
            runtime_order_id=request.runtime_order_id,
        )
        if len(bindings) != 1:
            raise NormalizedApiError(
                "cancel_order",
                "ctp_runtime_order_binding_unavailable",
                definite_reject=True,
            )
        binding = bindings[0]
        if binding.get("managed_intent_id") is not None:
            raise NormalizedApiError(
                "cancel_order",
                "ctp_cancel_identity_binding_missing_or_mismatch",
                definite_reject=True,
            )
        if (
            binding.get("status") != "unresolved"
            or not binding.get("client_order_id")
            or (
                binding.get("managed_intent_id") is not None
                and not request.managed_cancel_intent_id
            )
            or (
                binding.get("managed_intent_id") is None
                and request.managed_cancel_intent_id is not None
            )
        ):
            raise NormalizedApiError(
                "cancel_order",
                "ctp_managed_cancel_identity_required",
                definite_reject=True,
            )
        durable_order_ref = binding["ctp_order_ref"]
        supplied_refs = (
            request.order_id,
            request.client_order_id,
            request.order_ref,
        )
        if any(value is not None and value != durable_order_ref for value in supplied_refs):
            raise NormalizedApiError(
                "cancel_order",
                "ctp_runtime_cancel_reference_mismatch",
                definite_reject=True,
            )
        if binding.get("managed_intent_id") is not None and (
            request.front_id is not None or request.session_id is not None
        ):
            raise NormalizedApiError(
                "cancel_order",
                "ctp_runtime_cancel_native_reference_forbidden",
                definite_reject=True,
            )
        runtime_action_id = session.next_runtime_action_id(
            exchange_name,
            account_id=request.account_id,
            runtime_order_id=request.runtime_order_id,
        )
        if request.runtime_action_id not in (None, runtime_action_id):
            raise NormalizedApiError(
                "cancel_order",
                "ctp_runtime_cancel_action_id_mismatch",
                definite_reject=True,
            )
        return replace(
            request,
            order_id=None,
            client_order_id=durable_order_ref,
            order_ref=durable_order_ref,
            runtime_action_id=runtime_action_id,
        )

    def get_runtime_order_bindings(
        self,
        exchange_name: str,
        *,
        unresolved_only: bool = True,
        runtime_order_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read persisted CTP runtime/OrderRef joins for the active account scope."""
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "get_runtime_order_bindings",
                "execution_session_required",
                definite_reject=True,
            )
        return session.get_runtime_order_bindings(
            exchange_name,
            unresolved_only=unresolved_only,
            runtime_order_id=runtime_order_id,
        )

    def get_runtime_action_bindings(
        self,
        exchange_name: str,
        *,
        runtime_order_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read persisted CTP cancel-attempt joins for the active account scope."""
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "get_runtime_action_bindings",
                "execution_session_required",
                definite_reject=True,
            )
        return session.get_runtime_action_bindings(
            exchange_name,
            runtime_order_id=runtime_order_id,
        )

    def next_runtime_action_id(
        self,
        exchange_name: str,
        *,
        account_id: str | None,
        runtime_order_id: str,
    ) -> str:
        """Preview the next scoped CTP cancel action id without persisting it.

        The normalized ``cancel_order`` path derives and validates this same id
        while holding the execution-session lock, then fsyncs the cancel intent
        before dispatch. A concurrent cancel can consume the preview first; in
        that case the typed request is rejected rather than rebound.
        """
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "next_runtime_action_id",
                "execution_session_required",
                definite_reject=True,
            )
        return session.next_runtime_action_id(
            exchange_name,
            account_id=account_id,
            runtime_order_id=runtime_order_id,
        )

    def capture_execution_identity_evidence(
        self,
        expectations: tuple["ExecutionIdentityRouteExpectation", ...],
    ) -> "ExecutionIdentityVectorBinding":
        """Capture the configured route vector and its provider assurances in SDK.

        Backtrader supplies only exact route/config declarations. Snapshot and
        assurance ports remain inside this SDK method; the returned vector
        binding retains their exact objects and the established canonical
        digest under this execution session's owner/fence registry.
        """
        evidence_types = _execution_identity_evidence_contract_types()
        identity_types = _execution_identity_snapshot_contract_types()
        vector_types = _account_risk_snapshot_contract_types()
        if evidence_types is None or identity_types is None:
            raise _execution_identity_evidence_error(
                "execution_identity_evidence_contract_unavailable"
            )
        expectation_type, unavailable_type, route_type = evidence_types
        snapshot_type, assurance_type, snapshot_error_type, assurance_port_type = identity_types
        if vector_types is None:
            raise _execution_identity_evidence_error(
                "execution_identity_evidence_vector_unavailable"
            )
        binding_type = vector_types[2]
        scope_ref_type = _execution_identity_scope_ref_contract_type()
        binding_fields = getattr(binding_type, "__dataclass_fields__", {})
        if scope_ref_type is None or "identity_scope_refs" not in binding_fields:
            raise _execution_identity_evidence_error(
                "execution_identity_evidence_contract_unavailable"
            )
        if (
            type(expectations) is not tuple
            or not expectations
            or any(type(item) is not expectation_type for item in expectations)
        ):
            raise _execution_identity_evidence_error(
                "execution_identity_evidence_vector_invalid"
            )
        try:
            for expectation in expectations:
                expectation_type.__post_init__(expectation)
        except Exception:
            raise _execution_identity_evidence_error(
                "execution_identity_evidence_vector_invalid"
            ) from None
        ordered_expectations = tuple(sorted(expectations, key=lambda item: item.exchange_name))
        route_names = tuple(item.exchange_name for item in ordered_expectations)
        if len(set(route_names)) != len(route_names):
            raise _execution_identity_evidence_error(
                "execution_identity_evidence_vector_invalid"
            )

        session = self._execution_session
        if session is None:
            raise _execution_identity_evidence_error(
                "execution_identity_evidence_route_unavailable"
            )
        snapshot_capture = getattr(session, "execution_identity_snapshot", None)
        snapshot_validator = getattr(session, "validate_execution_identity_snapshot", None)
        scope_resolver = getattr(session, "execution_identity_scope_ref", None)
        if (
            not callable(snapshot_capture)
            or not callable(snapshot_validator)
            or not callable(scope_resolver)
        ):
            raise _execution_identity_evidence_error(
                "execution_identity_evidence_contract_unavailable"
            )

        snapshots = []
        assurances = []
        try:
            assurance_router = self._execution_identity_assurance_capability_router()
            for expectation in ordered_expectations:
                route = self._resolve_execution_identity_assurance_route(
                    expectation.exchange_name
                )
                if type(route) is not route_type:
                    raise _execution_identity_evidence_error(
                        "execution_identity_evidence_route_unavailable"
                    )
                if (
                    route.provider != expectation.expected_provider
                    or (
                        expectation.expected_environment is not None
                        and route.environment != expectation.expected_environment
                    )
                ):
                    raise _execution_identity_evidence_error(
                        "execution_identity_evidence_config_mismatch"
                    )

                snapshot = snapshot_capture(
                    route,
                    expected_strategy_id=expectation.expected_strategy_id,
                    expected_account_alias=expectation.expected_account_alias,
                )
                if (
                    type(snapshot) is not snapshot_type
                    or snapshot.exchange_name != route.exchange_name
                    or snapshot.provider != route.provider
                    or snapshot.environment != route.environment
                    or snapshot.session_generation != route.session_generation
                    or snapshot.fencing_epoch != route.fencing_epoch
                ):
                    raise _execution_identity_evidence_error(
                        "execution_identity_evidence_vector_invalid"
                    )
                valid_snapshot = snapshot_validator(snapshot, route)
                if type(valid_snapshot) is not bool or valid_snapshot is not True:
                    raise _execution_identity_evidence_error(
                        "execution_identity_evidence_not_current"
                    )

                # Select against the same route object used for the ledger
                # snapshot. The route-bound provider port rechecks this exact
                # client, ledger binding, session generation, and fence before
                # and after its provider-owned identity observation.
                assurance_port = assurance_router._execution_identity_assurance_for_route(
                    route
                )
                if not isinstance(assurance_port, assurance_port_type):
                    raise _execution_identity_evidence_error(
                        "execution_identity_evidence_provider_unavailable"
                    )
                assurance = assurance_port.assure_execution_identity()
                if (
                    type(assurance) is not assurance_type
                    or assurance.exchange_name != route.exchange_name
                    or assurance.provider != route.provider
                    or assurance.environment != route.environment
                    or assurance.account_ref != route.account_ref
                    or assurance.identity_binding != route.identity_binding
                    or assurance.session_generation != route.session_generation
                ):
                    raise _execution_identity_evidence_error(
                        "execution_identity_evidence_provider_unavailable"
                    )
                valid_assurance = assurance_port.validate_execution_identity(
                    assurance,
                    now_utc=datetime.now(UTC),
                )
                if type(valid_assurance) is not bool or valid_assurance is not True:
                    raise _execution_identity_evidence_error(
                        "execution_identity_evidence_not_current"
                    )
                if self.validate_execution_identity_snapshot(snapshot) is not True:
                    raise _execution_identity_evidence_error(
                        "execution_identity_evidence_route_changed"
                    )
                snapshots.append(snapshot)
                assurances.append(assurance)

            binding = self.execution_identity_vector_binding(
                tuple(snapshots),
                tuple(assurances),
            )
            if (
                type(binding) is not binding_type
                or binding.exchange_names != route_names
                or self.validate_execution_identity_evidence(binding, expectations) is not True
            ):
                raise _execution_identity_evidence_error(
                    "execution_identity_evidence_not_current"
                )
            return binding
        except unavailable_type:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code in {
                "execution_identity_snapshot_config_mismatch",
            }:
                safe_code = "execution_identity_evidence_config_mismatch"
            elif code in {
                "execution_identity_snapshot_route_unavailable",
                "execution_identity_assurance_route_unavailable",
            }:
                safe_code = "execution_identity_evidence_route_unavailable"
            elif code in {
                "execution_identity_snapshot_route_changed",
                "execution_identity_vector_not_current",
                "execution_identity_vector_binding_not_current",
                "execution_identity_assurance_route_changed",
            }:
                safe_code = "execution_identity_evidence_route_changed"
            elif code in {
                "execution_identity_assurance_provider_unavailable",
                "execution_identity_assurance_discovery_failed",
                "execution_identity_assurance_unavailable",
                "execution_identity_assurance_identity_mismatch",
                "execution_identity_vector_assurance_unavailable",
            }:
                safe_code = "execution_identity_evidence_provider_unavailable"
            elif code in {
                "execution_identity_vector_invalid",
                "execution_identity_vector_binding_invalid",
            }:
                safe_code = "execution_identity_evidence_vector_invalid"
            elif code in {
                "execution_identity_snapshot_not_current",
                "execution_identity_assurance_not_current",
            }:
                safe_code = "execution_identity_evidence_not_current"
            else:
                safe_code = "execution_identity_evidence_unavailable"
            raise _execution_identity_evidence_error(safe_code) from None

    def validate_execution_identity_evidence(
        self,
        evidence: "ExecutionIdentityVectorBinding",
        expectations: tuple["ExecutionIdentityRouteExpectation", ...],
    ) -> bool:
        """Revalidate an exact vector binding against current route declarations."""
        evidence_types = _execution_identity_evidence_contract_types()
        identity_types = _execution_identity_snapshot_contract_types()
        vector_types = _account_risk_snapshot_contract_types()
        if evidence_types is None or identity_types is None or vector_types is None:
            return False
        expectation_type, _unavailable_type, route_type = evidence_types
        snapshot_type = identity_types[0]
        binding_type = vector_types[2]
        if (
            type(evidence) is not binding_type
            or type(expectations) is not tuple
            or not expectations
            or any(type(item) is not expectation_type for item in expectations)
        ):
            return False
        try:
            for expectation in expectations:
                expectation_type.__post_init__(expectation)
        except Exception:
            return False
        ordered_expectations = tuple(sorted(expectations, key=lambda item: item.exchange_name))
        route_names = tuple(item.exchange_name for item in ordered_expectations)
        if len(set(route_names)) != len(route_names) or evidence.exchange_names != route_names:
            return False
        record = self._execution_identity_vector_binding_record(evidence)
        session = self._execution_session
        if record is None or session is None or record[3] is not session:
            return False
        snapshots = record[1]
        if (
            type(snapshots) is not tuple
            or len(snapshots) != len(ordered_expectations)
            or any(type(snapshot) is not snapshot_type for snapshot in snapshots)
        ):
            return False
        capture = getattr(session, "execution_identity_snapshot", None)
        if not callable(capture):
            return False
        try:
            for expectation, snapshot in zip(ordered_expectations, snapshots):
                route = self._resolve_execution_identity_assurance_route(
                    expectation.exchange_name
                )
                if (
                    type(route) is not route_type
                    or route.provider != expectation.expected_provider
                    or (
                        expectation.expected_environment is not None
                        and route.environment != expectation.expected_environment
                    )
                ):
                    return False
                current_snapshot = capture(
                    route,
                    expected_strategy_id=expectation.expected_strategy_id,
                    expected_account_alias=expectation.expected_account_alias,
                )
                if current_snapshot is not snapshot:
                    return False
                if self.validate_execution_identity_snapshot(snapshot) is not True:
                    return False
        except Exception:
            return False
        try:
            valid = self.validate_execution_identity_vector_binding(evidence)
        except Exception:
            return False
        return type(valid) is bool and valid is True

    def execution_identity_snapshot(
        self,
        exchange_name: str,
        *,
        expected_strategy_id: str,
        expected_account_alias: str | None = None,
    ) -> Any:
        """Issue the exact current SDK ledger/config declaration for one route.

        The local strategy and optional account-alias declarations are checked
        inside the SDK and are not copied into the returned DTO. This snapshot
        is not provider identity assurance and grants no write authority.
        """
        contract_types = _execution_identity_snapshot_contract_types()
        if contract_types is None:
            raise _execution_identity_snapshot_error(
                "execution_identity_snapshot_unavailable"
            )
        snapshot_type = contract_types[0]
        session = self._execution_session
        if session is None:
            raise _execution_identity_snapshot_error(
                "execution_identity_snapshot_route_unavailable"
            )
        route = self._resolve_execution_identity_assurance_route(exchange_name)
        if route is None:
            raise _execution_identity_snapshot_error(
                "execution_identity_snapshot_route_unavailable"
            )
        capture = getattr(session, "execution_identity_snapshot", None)
        if not callable(capture):
            raise _execution_identity_snapshot_error(
                "execution_identity_snapshot_unavailable"
            )
        try:
            snapshot = capture(
                route,
                expected_strategy_id=expected_strategy_id,
                expected_account_alias=expected_account_alias,
            )
        except contract_types[2]:
            raise
        except Exception:
            raise _execution_identity_snapshot_error(
                "execution_identity_snapshot_projection_invalid"
            ) from None
        if type(snapshot) is not snapshot_type:
            raise _execution_identity_snapshot_error(
                "execution_identity_snapshot_projection_invalid"
            )
        if self.validate_execution_identity_snapshot(snapshot) is not True:
            raise _execution_identity_snapshot_error(
                "execution_identity_snapshot_not_current"
            )
        return snapshot

    def validate_execution_identity_snapshot(self, snapshot: Any) -> bool:
        """Revalidate one exact SDK-issued snapshot against its live route."""
        contract_types = _execution_identity_snapshot_contract_types()
        if contract_types is None or type(snapshot) is not contract_types[0]:
            return False
        session = self._execution_session
        if session is None:
            return False
        route = self._resolve_execution_identity_assurance_route(snapshot.exchange_name)
        if route is None:
            return False
        if (
            route.exchange_name != snapshot.exchange_name
            or route.provider != snapshot.provider
            or route.environment != snapshot.environment
            or route.account_ref != snapshot.account_ref
            or route.session_generation != snapshot.session_generation
            or route.fencing_epoch != snapshot.fencing_epoch
        ):
            return False
        validator = getattr(session, "validate_execution_identity_snapshot", None)
        if not callable(validator):
            return False
        try:
            valid = validator(snapshot, route)
        except Exception:
            return False
        if type(valid) is not bool or valid is not True:
            return False

        # Resolve once more after validation so route replacement during the
        # check cannot leave an old session token appearing current.
        current = self._resolve_execution_identity_assurance_route(snapshot.exchange_name)
        if current is None or (
            current.client is not route.client
            or current.ledger_binding is not route.ledger_binding
            or current.session_generation != route.session_generation
            or current.fencing_epoch != route.fencing_epoch
            or current.request_receipt_context is not route.request_receipt_context
        ):
            return False
        if self.exchange_feeds.get(snapshot.exchange_name) is not route.client:
            return False
        with self._sdk_capability_lock:
            if self._selected_route_clients.get(snapshot.exchange_name) is not route.client:
                return False
        try:
            valid = validator(snapshot, current)
        except Exception:
            return False
        return type(valid) is bool and valid is True

    def execution_identity_vector_digest(
        self,
        snapshots: tuple[Any, ...],
        assurances: tuple[Any, ...],
    ) -> str:
        """Return the established canonical digest for exact current route pairs.

        The raw canonical row remains inside the SDK. Each supplied assurance
        must be the exact provider-issued object retained by this API's
        route-bound assurance facade and must still validate with the provider.
        No caller clock is accepted. The output preserves the existing sorted,
        compact JSON row encoding used by the Store identity vector.
        """
        contract_types = _execution_identity_snapshot_contract_types()
        if contract_types is None:
            raise _execution_identity_snapshot_error(
                "execution_identity_snapshot_unavailable"
            )
        snapshot_type, assurance_type, _error_type, assurance_port_type = contract_types
        if type(snapshots) is not tuple or type(assurances) is not tuple:
            raise _execution_identity_snapshot_error("execution_identity_vector_invalid")
        if len(snapshots) != len(assurances):
            raise _execution_identity_snapshot_error("execution_identity_vector_invalid")
        if not snapshots:
            return ""

        snapshot_by_route = {}
        assurance_by_route = {}
        for snapshot in snapshots:
            if type(snapshot) is not snapshot_type or snapshot.exchange_name in snapshot_by_route:
                raise _execution_identity_snapshot_error(
                    "execution_identity_vector_invalid"
                )
            snapshot_by_route[snapshot.exchange_name] = snapshot
        for assurance in assurances:
            if (
                type(assurance) is not assurance_type
                or assurance.exchange_name in assurance_by_route
            ):
                raise _execution_identity_snapshot_error(
                    "execution_identity_vector_invalid"
                )
            assurance_by_route[assurance.exchange_name] = assurance
        if set(snapshot_by_route) != set(assurance_by_route):
            raise _execution_identity_snapshot_error(
                "execution_identity_vector_invalid"
            )

        session = self._execution_session
        row_builder = getattr(session, "_execution_identity_vector_row", None)
        if session is None or not callable(row_builder):
            raise _execution_identity_snapshot_error(
                "execution_identity_snapshot_unavailable"
            )
        rows = []
        for exchange_name in sorted(snapshot_by_route):
            snapshot = snapshot_by_route[exchange_name]
            assurance = assurance_by_route[exchange_name]
            if self.validate_execution_identity_snapshot(snapshot) is not True:
                raise _execution_identity_snapshot_error(
                    "execution_identity_vector_not_current"
                )
            try:
                port = self.execution_identity_assurance(exchange_name)
            except Exception:
                raise _execution_identity_snapshot_error(
                    "execution_identity_vector_assurance_unavailable"
                ) from None
            if not isinstance(port, assurance_port_type):
                raise _execution_identity_snapshot_error(
                    "execution_identity_vector_assurance_unavailable"
                )
            now_utc = datetime.now(UTC)
            try:
                valid = port.validate_execution_identity(assurance, now_utc=now_utc)
            except Exception:
                valid = False
            if type(valid) is not bool or valid is not True:
                raise _execution_identity_snapshot_error(
                    "execution_identity_vector_assurance_unavailable"
                )
            route = self._resolve_execution_identity_assurance_route(exchange_name)
            if route is None:
                raise _execution_identity_snapshot_error(
                    "execution_identity_vector_not_current"
                )
            try:
                row = row_builder(snapshot, assurance, route)
            except Exception as exc:
                if type(exc) is contract_types[2]:
                    raise
                raise _execution_identity_snapshot_error(
                    "execution_identity_vector_invalid"
                ) from None
            if type(row) is not dict or row.get("exchange_name") != exchange_name:
                raise _execution_identity_snapshot_error(
                    "execution_identity_vector_invalid"
                )
            # Provider and ledger currentness are both rechecked after the
            # private canonical row is assembled and before hashing it.
            if (
                self.validate_execution_identity_snapshot(snapshot) is not True
                or port.validate_execution_identity(assurance, now_utc=datetime.now(UTC))
                is not True
            ):
                raise _execution_identity_snapshot_error(
                    "execution_identity_vector_not_current"
                )
            rows.append(row)
        payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def execution_identity_vector_binding(
        self,
        snapshots: tuple[Any, ...],
        assurances: tuple[Any, ...],
    ) -> Any:
        """Issue an exact currentness handle for the established identity digest.

        The digest bytes are exactly those returned by
        :meth:`execution_identity_vector_digest`; no random identity replaces
        the persisted cross-route value. The opaque binding ref is an SDK-local
        lookup key, not proof or authorization.
        """
        contract_types = _account_risk_snapshot_contract_types()
        if contract_types is None:
            raise _account_risk_snapshot_error(
                "execution_identity_vector_binding_unavailable"
            )
        binding_type, ref_type = contract_types[2], contract_types[3]
        digest = self.execution_identity_vector_digest(snapshots, assurances)
        if not digest:
            raise _account_risk_snapshot_error("execution_identity_vector_binding_invalid")
        identity_types = _execution_identity_snapshot_contract_types()
        session = self._execution_session
        if identity_types is None or session is None:
            raise _account_risk_snapshot_error(
                "execution_identity_vector_binding_unavailable"
            )
        snapshot_type, assurance_type = identity_types[0], identity_types[1]
        snapshot_by_route = {snapshot.exchange_name: snapshot for snapshot in snapshots}
        assurance_by_route = {assurance.exchange_name: assurance for assurance in assurances}
        if (
            type(snapshots) is not tuple
            or type(assurances) is not tuple
            or not snapshot_by_route
            or len(snapshot_by_route) != len(snapshots)
            or len(assurance_by_route) != len(assurances)
            or set(snapshot_by_route) != set(assurance_by_route)
            or any(type(item) is not snapshot_type for item in snapshots)
            or any(type(item) is not assurance_type for item in assurances)
        ):
            raise _account_risk_snapshot_error("execution_identity_vector_binding_invalid")
        exchange_names = tuple(sorted(snapshot_by_route))
        ordered_snapshots = tuple(snapshot_by_route[name] for name in exchange_names)
        ordered_assurances = tuple(assurance_by_route[name] for name in exchange_names)
        scope_ref_type = _execution_identity_scope_ref_contract_type()
        scope_resolver = getattr(session, "execution_identity_scope_ref", None)
        if scope_ref_type is None or not callable(scope_resolver):
            raise _account_risk_snapshot_error(
                "execution_identity_vector_binding_unavailable"
            )
        identity_scope_refs = []
        for exchange_name, snapshot in zip(exchange_names, ordered_snapshots):
            route = self._resolve_execution_identity_assurance_route(exchange_name)
            if route is None:
                raise _account_risk_snapshot_error(
                    "execution_identity_vector_binding_not_current"
                )
            try:
                scope_ref = scope_resolver(snapshot, route)
            except Exception:
                raise _account_risk_snapshot_error(
                    "execution_identity_vector_binding_unavailable"
                ) from None
            if type(scope_ref) is not scope_ref_type:
                raise _account_risk_snapshot_error(
                    "execution_identity_vector_binding_unavailable"
                )
            identity_scope_refs.append(scope_ref)
        epochs = {snapshot.fencing_epoch for snapshot in ordered_snapshots}
        if len(epochs) != 1:
            raise _account_risk_snapshot_error("execution_identity_vector_binding_invalid")
        fencing_epoch = next(iter(epochs))
        owner_token = getattr(session, "owner_token", None)
        if (
            getattr(session, "closed", True)
            or type(owner_token) is not str
            or not owner_token
            or getattr(session, "fencing_epoch", None) != fencing_epoch
        ):
            raise _account_risk_snapshot_error(
                "execution_identity_vector_binding_not_current"
            )
        try:
            binding = binding_type(
                identity_binding_sha256=digest,
                exchange_names=exchange_names,
                identity_scope_refs=tuple(identity_scope_refs),
                session_generations=tuple(
                    snapshot_by_route[name].session_generation for name in exchange_names
                ),
                fencing_epoch=fencing_epoch,
                binding_ref=ref_type(),
            )
        except Exception:
            raise _account_risk_snapshot_error(
                "execution_identity_vector_binding_invalid"
            ) from None
        record = (
            binding,
            ordered_snapshots,
            ordered_assurances,
            session,
            owner_token,
        )
        with self._sdk_capability_lock:
            self._execution_identity_vector_bindings[binding.binding_ref] = record
            while len(self._execution_identity_vector_bindings) > 32:
                evicted_ref = next(iter(self._execution_identity_vector_bindings))
                self._execution_identity_vector_bindings.pop(evicted_ref, None)
                self._account_risk_bound_snapshots.pop(evicted_ref, None)
        if self.validate_execution_identity_vector_binding(binding) is not True:
            with self._sdk_capability_lock:
                if self._execution_identity_vector_bindings.get(binding.binding_ref) is record:
                    self._execution_identity_vector_bindings.pop(binding.binding_ref, None)
                    self._account_risk_bound_snapshots.pop(binding.binding_ref, None)
            raise _account_risk_snapshot_error(
                "execution_identity_vector_binding_not_current"
            )
        return binding

    def _execution_identity_vector_binding_record(self, binding: Any) -> tuple[Any, ...] | None:
        contract_types = _account_risk_snapshot_contract_types()
        if (
            contract_types is None
            or type(binding) is not contract_types[2]
            or type(binding.binding_ref) is not contract_types[3]
        ):
            return None
        with self._sdk_capability_lock:
            record = self._execution_identity_vector_bindings.get(binding.binding_ref)
            if record is None or record[0] is not binding:
                return None
            return record

    def validate_execution_identity_vector_binding(self, binding: Any) -> bool:
        """Revalidate one exact SDK-issued vector and provider assurance set."""
        record = self._execution_identity_vector_binding_record(binding)
        if record is None:
            return False
        _issued, snapshots, assurances, session, owner_token = record
        scope_ref_type = _execution_identity_scope_ref_contract_type()
        scope_resolver = getattr(session, "execution_identity_scope_ref", None)
        identity_scope_refs = getattr(binding, "identity_scope_refs", None)
        if (
            scope_ref_type is None
            or not callable(scope_resolver)
            or session is not self._execution_session
            or getattr(session, "closed", True)
            or getattr(session, "owner_token", None) != owner_token
            or getattr(session, "fencing_epoch", None) != binding.fencing_epoch
            or tuple(snapshot.exchange_name for snapshot in snapshots)
            != binding.exchange_names
            or tuple(snapshot.session_generation for snapshot in snapshots)
            != binding.session_generations
            or type(identity_scope_refs) is not tuple
            or len(identity_scope_refs) != len(snapshots)
            or any(type(ref) is not scope_ref_type for ref in identity_scope_refs)
            or any(
                snapshot.fencing_epoch != binding.fencing_epoch
                or self.validate_execution_identity_snapshot(snapshot) is not True
                for snapshot in snapshots
            )
        ):
            return False
        for snapshot, scope_ref in zip(snapshots, identity_scope_refs):
            route = self._resolve_execution_identity_assurance_route(snapshot.exchange_name)
            if route is None:
                return False
            try:
                current_scope_ref = scope_resolver(snapshot, route)
            except Exception:
                return False
            if type(current_scope_ref) is not scope_ref_type or current_scope_ref is not scope_ref:
                return False
        try:
            digest = self.execution_identity_vector_digest(snapshots, assurances)
        except Exception:
            return False
        if digest != binding.identity_binding_sha256:
            return False
        with self._sdk_capability_lock:
            current = self._execution_identity_vector_bindings.get(binding.binding_ref)
            return (
                current is record
                and current[0] is binding
                and session is self._execution_session
                and getattr(session, "owner_token", None) == owner_token
            )

    @staticmethod
    def _canonical_account_risk_identity(identity: Any) -> dict[str, str] | None:
        """Normalize one internal risk-ledger row without exposing it."""
        if type(identity) is not dict:
            return None
        allowed = {"provider", "environment", "account_id", "credential_fingerprint"}
        if set(identity) not in (allowed - {"credential_fingerprint"}, allowed):
            return None
        provider = identity.get("provider")
        environment = identity.get("environment")
        account_id = identity.get("account_id")
        if any(type(value) is not str or not value.strip() for value in (provider, environment, account_id)):
            return None
        result = {
            "provider": provider.strip().upper(),
            "environment": environment.strip().lower(),
            "account_id": account_id.strip().casefold(),
        }
        fingerprint = identity.get("credential_fingerprint")
        if "credential_fingerprint" in identity:
            if type(fingerprint) is not str or not fingerprint.strip():
                return None
            result["credential_fingerprint"] = fingerprint.strip().lower()
        return result

    def account_risk_snapshot_for_identity_binding(
        self,
        binding: Any,
        *,
        initialize_baseline: bool = False,
        reset_loss_latch: bool = False,
    ) -> Any:
        """Collect route-bound neutral risk facts under one current identity vector.

        Each exact route uses one retained QueryFacade for its account,
        valuation, and position batch. A first-baseline or explicit reset adds
        a complete open-orders snapshot and performs a second open-orders
        batch from that same facade immediately before the durable reducer
        commits. The query source/generation must exactly match the independent
        provider-issued identity assurance for that route. Legacy client
        getters and caller-created account evidence are never consulted.
        """
        contract_types = _account_risk_snapshot_contract_types()
        if contract_types is None:
            raise _account_risk_snapshot_error("account_risk_snapshot_unavailable")
        snapshot_type, unavailable_type, binding_type = contract_types[:3]
        if type(initialize_baseline) is not bool or type(reset_loss_latch) is not bool:
            raise _account_risk_snapshot_error(
                "account_risk_snapshot_projection_invalid"
            )
        if type(binding) is not binding_type:
            raise _account_risk_snapshot_error(
                "execution_identity_vector_binding_invalid"
            )

        identity_types = _execution_identity_snapshot_contract_types()
        session = self._execution_session
        binding_record = self._execution_identity_vector_binding_record(binding)
        if (
            identity_types is None
            or session is None
            or binding_record is None
            or binding_record[3] is not session
            or getattr(session, "closed", True)
            or session.config.get("market_data_only") is True
            or self.validate_execution_identity_vector_binding(binding) is not True
        ):
            raise _account_risk_snapshot_error(
                "execution_identity_vector_binding_not_current"
            )

        risk_venues = tuple(session.risk_venues())
        if not risk_venues or tuple(sorted(risk_venues)) != binding.exchange_names:
            raise _account_risk_snapshot_error(
                "execution_identity_vector_binding_invalid"
            )
        snapshots_by_route = {
            item.exchange_name: item for item in binding_record[1]
        }
        assurances_by_route = {
            item.exchange_name: item for item in binding_record[2]
        }
        if (
            set(snapshots_by_route) != set(risk_venues)
            or set(assurances_by_route) != set(risk_venues)
        ):
            raise _account_risk_snapshot_error(
                "execution_identity_vector_binding_invalid"
            )

        # The selected provider may require native account binding that the
        # current neutral query/assurance contracts cannot yet express.
        from .account_risk_source_policy import requires_native_account_binding

        try:
            native_binding_required = any(
                requires_native_account_binding(snapshots_by_route[venue].provider)
                or requires_native_account_binding(session._provider(venue))
                for venue in risk_venues
            )
        except Exception:
            raise _account_risk_snapshot_error(
                "account_risk_source_binding_unavailable"
            ) from None
        if native_binding_required:
            raise _account_risk_snapshot_error(
                "account_risk_source_binding_unavailable"
            )

        try:
            from bt_api_base.execution_identity_protocol import (
                SelectedExecutionIdentityRoute,
            )
            from bt_api_base.query_provider_protocol import SelectedQueryRoute

            from .query_contracts import (
                AccountBalance,
                AccountOpenOrder,
                AccountPosition,
                AccountValuation,
                QueryBatchReceipt,
                QueryCompleteness,
                QueryRequest,
            )
            from .query_facade import QueryFacade
            from .query_composition import create_query_facade
        except Exception:
            raise _account_risk_snapshot_error(
                "account_risk_snapshot_unavailable"
            ) from None

        try:
            max_age_seconds = float(session.config["account_risk_max_age_seconds"])
            if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
                raise ValueError
            max_age_ns = session._risk_max_age_ns()
            if type(max_age_ns) is not int or max_age_ns <= 0:
                raise ValueError
        except Exception:
            raise _account_risk_snapshot_error(
                "account_risk_snapshot_unavailable"
            ) from None

        route_states: dict[str, dict[str, Any]] = {}
        lease_records: list[tuple[Any, Any, Any]] = []

        def timestamp_is_current(value: str, now_utc: datetime) -> bool:
            if type(value) is not str or not value.endswith("Z"):
                return False
            try:
                observed = datetime.fromisoformat(value[:-1] + "+00:00")
            except ValueError:
                return False
            age = now_utc - observed
            return observed <= now_utc and age.total_seconds() <= max_age_seconds

        def validate_leases() -> bool:
            now_utc = datetime.now(UTC)
            for venue, route_state in tuple(route_states.items()):
                if not query_route_matches_identity(
                    venue,
                    route_state["identity_route"],
                    route_state["query_route"],
                ):
                    return False
            for facade, receipt, lease in tuple(lease_records):
                if facade.validate_lease(lease, receipt, now=now_utc) is not True:
                    return False
            return True

        def query_route_matches_identity(
            venue: str,
            identity_route: Any,
            query_route: Any,
        ) -> bool:
            if (
                type(identity_route) is not SelectedExecutionIdentityRoute
                or type(query_route) is not SelectedQueryRoute
                or identity_route.exchange_name != venue
                or query_route.exchange_name != venue
                or query_route.client is not identity_route.client
                or query_route.environment != identity_route.environment
            ):
                return False
            try:
                current_route = self._resolve_execution_identity_assurance_route(venue)
                route_current = (
                    session.validate_execution_identity_assurance_route(identity_route)
                    is True
                )
            except Exception:
                return False
            return bool(
                route_current
                and type(current_route) is SelectedExecutionIdentityRoute
                and current_route.client is identity_route.client
                and current_route.environment == identity_route.environment
                and current_route.provider == identity_route.provider
                and current_route.account_ref == identity_route.account_ref
                and current_route.identity_binding == identity_route.identity_binding
                and current_route.session_generation == identity_route.session_generation
                and current_route.fencing_epoch == identity_route.fencing_epoch
                and current_route.ledger_binding is identity_route.ledger_binding
                and getattr(current_route, "request_receipt_context", None)
                is getattr(identity_route, "request_receipt_context", None)
            )

        def capture_batch(
            facade: Any,
            requests: tuple[Any, ...],
            assurance: Any,
            venue: str,
            identity_route: Any,
            query_route: Any,
        ) -> tuple[Any, dict[str, Any]]:
            now_utc = datetime.now(UTC)
            deadline = now_utc + timedelta(seconds=max_age_seconds)
            if not query_route_matches_identity(venue, identity_route, query_route):
                raise _account_risk_snapshot_error(
                    "account_risk_source_binding_unavailable"
                )
            receipt = facade.refresh(requests, deadline=deadline)
            if type(receipt) is not QueryBatchReceipt:
                raise _account_risk_snapshot_error(
                    "account_risk_snapshot_unavailable"
                )
            if (
                type(receipt.source_ref) is not str
                or type(receipt.generation) is not str
                or receipt.source_ref != assurance.source_ref
                or receipt.generation != assurance.session_generation
            ):
                raise _account_risk_snapshot_error(
                    "account_risk_source_binding_unavailable"
                )
            if not receipt.complete or receipt.completed_at > deadline:
                raise _account_risk_snapshot_error(
                    "account_risk_snapshot_unavailable"
                )

            snapshots_by_family: dict[str, Any] = {}
            expiries = [deadline]
            now_after_query = datetime.now(UTC)
            for request in requests:
                result = facade.snapshot(receipt, request.request_ref)
                if (
                    result is None
                    or result.request != request
                    or result.completeness is not QueryCompleteness.COMPLETE
                    or result.is_complete is not True
                    or result.scope_complete is not True
                    or result.source_ref != receipt.source_ref
                    or result.generation != receipt.generation
                    or result.observed_at is None
                    or result.completed_at is None
                    or result.observed_at > now_after_query
                    or (now_after_query - result.observed_at).total_seconds()
                    > max_age_seconds
                ):
                    raise _account_risk_snapshot_error(
                        "account_risk_snapshot_unavailable"
                    )
                expiries.append(result.observed_at + timedelta(seconds=max_age_seconds))
                snapshots_by_family[request.family] = result

            expires_at = min(expiries)
            if expires_at <= now_after_query:
                raise _account_risk_snapshot_error(
                    "account_risk_snapshot_not_current"
                )
            lease = facade.acquire_snapshot_lease(
                receipt,
                purpose="bt_api.account_risk.snapshot",
                expires_at=expires_at,
            )
            if facade.validate_lease(lease, receipt, now=datetime.now(UTC)) is not True:
                raise _account_risk_snapshot_error(
                    "account_risk_snapshot_not_current"
                )
            if (
                not query_route_matches_identity(venue, identity_route, query_route)
                or self.validate_execution_identity_vector_binding(binding) is not True
            ):
                raise _account_risk_snapshot_error(
                    "account_risk_source_binding_unavailable"
                )
            lease_records.append((facade, receipt, lease))
            return receipt, snapshots_by_family

        def request_for(family: str) -> Any:
            return QueryRequest(
                request_ref=f"risk-{uuid.uuid4().hex}",
                family=family,
                filters={},
                required=True,
                require_complete_scope=True,
            )

        try:
            with session.risk_collection():
                revision = session.current_risk_snapshot_revision()
                if type(revision) is not int or revision <= 0:
                    raise _account_risk_snapshot_error(
                        "account_risk_snapshot_unavailable"
                    )
                observations: dict[str, dict[str, Any]] = {}
                needs_open_orders = bool(
                    reset_loss_latch
                    or (initialize_baseline and session.requires_risk_baseline())
                )
                for venue in risk_venues:
                    identity = snapshots_by_route[venue]
                    assurance = assurances_by_route[venue]
                    if (
                        assurance.exchange_name != venue
                        or assurance.provider != identity.provider
                        or assurance.environment != identity.environment
                        or assurance.account_ref != identity.account_ref
                    ):
                        raise _account_risk_snapshot_error(
                            "account_risk_source_binding_unavailable"
                        )

                    try:
                        identity_route = self._resolve_execution_identity_assurance_route(
                            venue
                        )
                        if (
                            type(identity_route) is not SelectedExecutionIdentityRoute
                            or assurance.exchange_name != identity_route.exchange_name
                            or assurance.provider != identity_route.provider
                            or assurance.environment != identity_route.environment
                            or assurance.account_ref != identity_route.account_ref
                            or assurance.session_generation
                            != identity_route.session_generation
                        ):
                            raise _account_risk_snapshot_error(
                                "account_risk_source_binding_unavailable"
                            )
                        query_route = SelectedQueryRoute(
                            exchange_name=venue,
                            client=identity_route.client,
                            environment=identity_route.environment,
                        )
                        facade = create_query_facade(query_route)
                    except Exception:
                        raise _account_risk_snapshot_error(
                            "account_risk_source_binding_unavailable"
                        ) from None
                    if type(facade) is not QueryFacade:
                        raise _account_risk_snapshot_error(
                            "account_risk_snapshot_unavailable"
                        )
                    if not query_route_matches_identity(
                        venue,
                        identity_route,
                        query_route,
                    ):
                        raise _account_risk_snapshot_error(
                            "account_risk_source_binding_unavailable"
                        )

                    route_states[venue] = {
                        "assurance": assurance,
                        "identity_route": identity_route,
                        "query_route": query_route,
                        "facade": facade,
                    }

                    families = ["account", "account_valuation", "positions"]
                    if needs_open_orders:
                        families.append("open_orders")
                    requests = tuple(request_for(family) for family in families)
                    receipt, family_snapshots = capture_batch(
                        facade,
                        requests,
                        assurance,
                        venue,
                        identity_route,
                        query_route,
                    )

                    try:
                        balances = tuple(
                            AccountBalance.from_dict(row)
                            for row in family_snapshots["account"].records
                        )
                        valuations = tuple(
                            AccountValuation.from_dict(row)
                            for row in family_snapshots["account_valuation"].records
                        )
                        positions = tuple(
                            AccountPosition.from_dict(row)
                            for row in family_snapshots["positions"].records
                        )
                        open_orders = (
                            tuple(
                                AccountOpenOrder.from_dict(row)
                                for row in family_snapshots["open_orders"].records
                            )
                            if "open_orders" in family_snapshots
                            else None
                        )
                    except Exception:
                        raise _account_risk_snapshot_error(
                            "account_risk_snapshot_projection_invalid"
                        ) from None
                    if len(valuations) != 1 or valuations[0].equity is None:
                        raise _account_risk_snapshot_error(
                            "account_risk_snapshot_unavailable"
                        )
                    valuation = valuations[0]
                    if valuation.currency != valuation.currency.strip().upper():
                        raise _account_risk_snapshot_error(
                            "account_risk_snapshot_projection_invalid"
                        )
                    configured_currency = session._configured_risk_currency(venue)
                    if configured_currency is not None and valuation.currency != configured_currency:
                        raise _account_risk_snapshot_error(
                            "account_risk_snapshot_projection_invalid"
                        )

                    for balance in balances:
                        if not timestamp_is_current(
                            balance.observed_at_utc,
                            datetime.now(UTC),
                        ):
                            raise _account_risk_snapshot_error(
                                "account_risk_snapshot_not_current"
                            )
                    for position in positions:
                        if (
                            position.instrument.provider != identity.provider
                            or position.instrument.environment != identity.environment
                            or not timestamp_is_current(
                                position.observed_at_utc,
                                datetime.now(UTC),
                            )
                        ):
                            raise _account_risk_snapshot_error(
                                "account_risk_snapshot_projection_invalid"
                            )
                    if not timestamp_is_current(
                        valuation.observed_at_utc,
                        datetime.now(UTC),
                    ):
                        raise _account_risk_snapshot_error(
                            "account_risk_snapshot_not_current"
                        )
                    if open_orders is not None:
                        for order in open_orders:
                            if (
                                order.instrument.provider != identity.provider
                                or order.instrument.environment != identity.environment
                                or not timestamp_is_current(
                                    order.observed_at_utc,
                                    datetime.now(UTC),
                                )
                            ):
                                raise _account_risk_snapshot_error(
                                    "account_risk_snapshot_projection_invalid"
                                )

                    route_states[venue] = {
                        "assurance": assurance,
                        "identity_route": identity_route,
                        "query_route": query_route,
                        "facade": facade,
                        "first_receipt": receipt,
                        "open_orders": open_orders,
                    }
                    observations[venue] = {
                        "currency": valuation.currency,
                        "equity": valuation.equity,
                        "positions_known": True,
                        "positions_flat": all(
                            Decimal(position.quantity) == 0 for position in positions
                        ),
                        "open_orders_known": open_orders is not None,
                        "open_orders_empty": open_orders is not None and not open_orders,
                        "authenticated_account_id": None,
                    }

                def baseline_commit_check() -> dict[str, str]:
                    errors: dict[str, str] = {}
                    for venue in risk_venues:
                        route_state = route_states.get(venue)
                        if route_state is None or route_state["open_orders"] is None:
                            errors[f"{venue}:baseline_commit_open_orders"] = (
                                "open_orders_not_proven"
                            )
                            continue
                        if not validate_leases():
                            errors[f"{venue}:baseline_commit_open_orders"] = (
                                "open_orders_commit_sweep_not_proven"
                            )
                            continue
                        request = request_for("open_orders")
                        try:
                            second_receipt, second_snapshots = capture_batch(
                                route_state["facade"],
                                (request,),
                                route_state["assurance"],
                                venue,
                                route_state["identity_route"],
                                route_state["query_route"],
                            )
                            first_receipt = route_state["first_receipt"]
                            if (
                                second_receipt.source_ref != first_receipt.source_ref
                                or second_receipt.generation != first_receipt.generation
                            ):
                                errors[f"{venue}:baseline_commit_open_orders"] = (
                                    "account_risk_source_binding_unavailable"
                                )
                                continue
                            rows = tuple(
                                AccountOpenOrder.from_dict(row)
                                for row in second_snapshots["open_orders"].records
                            )
                            if rows:
                                errors[f"{venue}:baseline_commit_open_orders"] = (
                                    "open_orders_present"
                                )
                                continue
                            route_state["second_receipt"] = second_receipt
                        except Exception:
                            errors[f"{venue}:baseline_commit_open_orders"] = (
                                "open_orders_commit_sweep_not_proven"
                            )
                    if not validate_leases():
                        for venue in risk_venues:
                            errors.setdefault(
                                f"{venue}:baseline_commit_open_orders",
                                "open_orders_commit_sweep_not_proven",
                            )
                    if (
                        self.validate_execution_identity_vector_binding(binding) is not True
                        or session.current_risk_snapshot_revision() != revision
                    ):
                        for venue in risk_venues:
                            errors.setdefault(
                                f"{venue}:baseline_commit_open_orders",
                                "account_risk_source_binding_unavailable",
                            )
                    return errors

                def evidence_commit_check() -> bool:
                    return (
                        self.validate_execution_identity_vector_binding(binding) is True
                        and session.current_risk_snapshot_revision() == revision
                        and not session.closed
                        and session.risk_check_in_progress is True
                        and validate_leases()
                    )

                result = session.account_risk_snapshot(
                    observations,
                    initialize_baseline=initialize_baseline,
                    reset_loss_latch=reset_loss_latch,
                    baseline_commit_check=baseline_commit_check,
                    evidence_commit_check=evidence_commit_check,
                )
        except unavailable_type:
            raise
        except Exception:
            raise _account_risk_snapshot_error(
                "account_risk_snapshot_unavailable"
            ) from None

        if (
            session.current_risk_snapshot_revision() != revision
            or session.risk_check_in_progress
            or not validate_leases()
            or self.validate_execution_identity_vector_binding(binding) is not True
        ):
            raise _account_risk_snapshot_error(
                "account_risk_snapshot_not_current"
            )
        facts = {name: result.get(name) for name in contract_types[4]}
        try:
            snapshot = snapshot_type(facts=facts, identity_binding=binding)
        except Exception:
            raise _account_risk_snapshot_error(
                "account_risk_snapshot_projection_invalid"
            ) from None
        owner_token = getattr(session, "owner_token", None)
        record = (
            snapshot,
            binding,
            session,
            owner_token,
            revision,
            tuple(lease_records),
        )
        with self._sdk_capability_lock:
            if self._execution_identity_vector_bindings.get(binding.binding_ref) is not binding_record:
                raise _account_risk_snapshot_error(
                    "execution_identity_vector_binding_not_current"
                )
            self._account_risk_bound_snapshots[binding.binding_ref] = record
        if self.validate_account_risk_snapshot(snapshot) is not True:
            with self._sdk_capability_lock:
                if self._account_risk_bound_snapshots.get(binding.binding_ref) is record:
                    self._account_risk_bound_snapshots.pop(binding.binding_ref, None)
            raise _account_risk_snapshot_error("account_risk_snapshot_not_current")
        return snapshot

    def validate_account_risk_snapshot(self, snapshot: Any) -> bool:
        """Validate this exact DTO, query leases, and current risk revision."""
        contract_types = _account_risk_snapshot_contract_types()
        if contract_types is None or type(snapshot) is not contract_types[0]:
            return False
        binding = snapshot.identity_binding
        record = self._execution_identity_vector_binding_record(binding)
        if record is None:
            return False
        with self._sdk_capability_lock:
            issued = self._account_risk_bound_snapshots.get(binding.binding_ref)
        if (
            issued is None
            or issued[0] is not snapshot
            or issued[1] is not binding
            or issued[2] is not self._execution_session
        ):
            return False
        _snapshot, _binding, session, owner_token, revision, lease_records = issued
        if (
            getattr(session, "closed", True)
            or getattr(session, "owner_token", None) != owner_token
            or session.current_risk_snapshot_revision() != revision
            or session.risk_check_in_progress
            or self.validate_execution_identity_vector_binding(binding) is not True
        ):
            return False
        try:
            as_of = snapshot.facts["as_of_monotonic_ns"]
            now_monotonic = time.monotonic_ns()
            max_age_ns = session._risk_max_age_ns()
        except Exception:
            return False
        if (
            type(as_of) is not int
            or type(now_monotonic) is not int
            or type(max_age_ns) is not int
            or max_age_ns <= 0
            or now_monotonic < as_of
            or now_monotonic - as_of > max_age_ns
            or snapshot.facts["owner_pid"] != session.owner_pid
        ):
            return False
        now_utc = datetime.now(UTC)
        for facade, receipt, lease in lease_records:
            try:
                valid = facade.validate_lease(lease, receipt, now=now_utc)
            except Exception:
                return False
            if type(valid) is not bool or valid is not True:
                return False
        current_revision = session.current_risk_snapshot_revision()
        current_in_progress = session.risk_check_in_progress
        with self._sdk_capability_lock:
            current = self._account_risk_bound_snapshots.get(binding.binding_ref)
            return (
                current is issued
                and current[0] is snapshot
                and session is self._execution_session
                and current_revision == revision
                and current_in_progress is False
            )

    def get_execution_identity(self, exchange_name: str) -> dict[str, Any]:
        """Return the SDK-owned ledger identity used for typed order requests."""
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "get_execution_identity",
                "execution_session_required",
                definite_reject=True,
            )
        return {
            **session.execution_identity(exchange_name),
            "mode": self.transport_mode.value,
        }

    def get_execution_summary(self) -> dict[str, Any]:
        """Read session execution evidence without touching a transport adapter."""
        if self._execution_session is None:
            return {
                "session_enabled": False,
                "armed": False,
                "market_data_only": True,
                "arm_managed": False,
                "arm_revoked": False,
                "revocation_reason": None,
                "arm_proof_sha256": None,
                "proof_sha256": None,
                "submit_calls": None,
                "unknown_ids": [],
                "active_orders": None,
                "fee_unresolved_orders": [],
                "estimated_fee_orders": [],
                "funding_unresolved_orders": [],
                "funding_evidence_status": "unavailable",
                "signed_funding_cashflow": None,
                "evidence_errors": [],
                "trading_blocked": False,
                "evidence_complete": False,
            }
        return {"session_enabled": True, **self._execution_session.summary()}

    def recover_reservation_only_cancel_unknowns(self) -> dict[str, Any]:
        """Resolve locally proven reservation-only cancel unknowns before Store startup.

        The SDK execution session checks the durable journal sequence and writes
        only a local terminal journal event. No venue adapter is consulted.
        """
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "recover_cancel_unknown",
                "execution_session_required",
                definite_reject=True,
            )
        return session.recover_reservation_only_cancel_unknowns()

    def recover_historical_documented_definite_rejections(self) -> dict[str, Any]:
        """Resolve only historical unknowns proven rejected by a documented venue code."""
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "recover_documented_definite_rejection",
                "execution_session_required",
                definite_reject=True,
            )
        return session.recover_historical_documented_definite_rejections()

    @staticmethod
    def evaluate_ctp_execution_budget(
        evidence: Mapping[str, Any], *, mode: str = "ordinary", now: Any = None
    ) -> Any:
        """Evaluate provider-neutral CTP path evidence without reserving funds."""
        from ._ctp_budget import evaluate_ctp_budget

        return evaluate_ctp_budget(evidence, mode=mode, now=now)

    def reserve_ctp_execution_budget(
        self,
        evidence: Mapping[str, Any],
        *,
        mode: str = "ordinary",
        now: Any = None,
        reservation_id: str | None = None,
    ) -> Any:
        """Durably reserve one CTP path budget in the existing execution journal."""
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "reserve_ctp_execution_budget",
                "execution_session_required",
                definite_reject=True,
            )
        return session.reserve_ctp_execution_budget(
            evidence,
            mode=mode,
            now=now,
            reservation_id=reservation_id,
        )

    reserve_ctp_budget = reserve_ctp_execution_budget

    def record_ctp_budget_pnl(
        self,
        cumulative_pnl_cny: Any,
        *,
        version: str | None = None,
        context: Mapping[str, Any] | None = None,
        source: str = "synthetic_test",
    ) -> dict[str, Any]:
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "record_ctp_budget_pnl",
                "execution_session_required",
                definite_reject=True,
            )
        return session.record_ctp_budget_pnl(
            cumulative_pnl_cny,
            version=version,
            context=context,
            source=source,
        )

    def get_ctp_execution_budget_snapshot(self) -> dict[str, Any]:
        session = self._execution_session
        if session is None:
            return {
                "schema_version": "ctp-execution-budget-v1",
                "production_status": "PRODUCTION_BLOCKED_EXECUTION_SESSION_REQUIRED",
            }
        return session.ctp_budget_snapshot()

    inspect_ctp_execution_budget = get_ctp_execution_budget_snapshot

    def transition_ctp_execution_budget(
        self,
        capability: Any,
        transition: str,
        *,
        amount_cny: Any = 0,
        transition_id: str | None = None,
        evidence: Mapping[str, Any] | None = None,
        terminated_unused: bool = False,
    ) -> dict[str, Any]:
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "transition_ctp_execution_budget",
                "execution_session_required",
                definite_reject=True,
            )
        return session.transition_ctp_budget(
            capability,
            transition,
            amount_cny=amount_cny,
            transition_id=transition_id,
            evidence=evidence,
            terminated_unused=terminated_unused,
        )

    update_ctp_budget_obligation = transition_ctp_execution_budget

    def get_account_risk_snapshot(
        self,
        *,
        initialize_baseline: bool = False,
    ) -> dict[str, Any]:
        """Return a plain projection of the SDK-issued neutral risk facts.

        Account observations are collected only through the exact selected
        route's QueryFacade and typed receipt/lease lifecycle. The account
        identity assurance remains a separate required proof; current providers
        without a matching source/session binding report unavailable.
        """
        return self._collect_typed_account_risk_snapshot(
            initialize_baseline=initialize_baseline,
            reset_loss_latch=False,
        )

    def reset_account_maximum_loss_latch(self) -> dict[str, Any]:
        """Explicitly reset a breached loss latch after authoritative flat proof.

        The SDK requires full-scope neutral account/valuation/position reads,
        two QueryFacade open-order sweeps, current provider identity assurance,
        and a clean durable execution ledger before the reset can commit. Missing
        query-source binding leaves the persisted latch unchanged.
        """
        return self._collect_typed_account_risk_snapshot(
            initialize_baseline=False,
            reset_loss_latch=True,
        )

    def _collect_typed_account_risk_snapshot(
        self,
        *,
        initialize_baseline: bool,
        reset_loss_latch: bool,
    ) -> dict[str, Any]:
        session = self._execution_session
        if session is None or session.config.get("market_data_only") is True:
            raise _account_risk_snapshot_error("account_risk_snapshot_unavailable")
        if type(initialize_baseline) is not bool or type(reset_loss_latch) is not bool:
            raise _account_risk_snapshot_error(
                "account_risk_snapshot_projection_invalid"
            )

        snapshots = []
        assurances = []
        for venue in session.risk_venues():
            try:
                local_identity = session.execution_identity(venue)
                snapshot = self.execution_identity_snapshot(
                    venue,
                    expected_strategy_id=session.config["strategy_id"],
                    expected_account_alias=local_identity.get("account_alias"),
                )
                assurance = self.execution_identity_assurance(
                    venue
                ).assure_execution_identity()
            except Exception:
                raise _account_risk_snapshot_error(
                    "account_risk_source_binding_unavailable"
                ) from None
            snapshots.append(snapshot)
            assurances.append(assurance)
        try:
            binding = self.execution_identity_vector_binding(
                tuple(snapshots),
                tuple(assurances),
            )
            snapshot = self.account_risk_snapshot_for_identity_binding(
                binding,
                initialize_baseline=initialize_baseline,
                reset_loss_latch=reset_loss_latch,
            )
        except Exception as error:
            contract_types = _account_risk_snapshot_contract_types()
            if contract_types is not None and type(error) is contract_types[1]:
                raise
            raise _account_risk_snapshot_error(
                "account_risk_snapshot_unavailable"
            ) from None

        def thaw(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {key: thaw(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [thaw(item) for item in value]
            return value

        return thaw(snapshot.facts)




    def _ctp_private_ingress_fence(self, exchange_name: str) -> _CtpPrivateIngressFence:
        fences = getattr(self, "_ctp_private_ingress_fences", None)
        if not isinstance(fences, dict):
            with _CTP_TRANSITION_LOCK_INIT:
                fences = getattr(self, "_ctp_private_ingress_fences", None)
                if not isinstance(fences, dict):
                    fences = {}
                    self._ctp_private_ingress_fences = fences
        fence = fences.get(exchange_name)
        if fence is None:
            with _CTP_TRANSITION_LOCK_INIT:
                fence = fences.get(exchange_name)
                if fence is None:
                    fence = _CtpPrivateIngressFence()
                    fences[exchange_name] = fence
        return fence

    def _ctp_private_ingress_snapshot(self, exchange_name: str) -> tuple[int, int]:
        fence = self._ctp_private_ingress_fence(exchange_name)
        with fence.lock:
            pending = fence.pending + int(fence.revocation_pending)
            return fence.epoch, pending

    def _ctp_write_ingress_lease(
        self,
        exchange_name: str,
        operation: str,
    ) -> tuple[Any, Any]:
        """Order one CTP write before or after every started private ingress."""
        fence = self._ctp_private_ingress_fence(exchange_name)
        acquired = False

        def acquire() -> None:
            nonlocal acquired
            with fence.lock:
                if fence.pending or fence.revocation_pending:
                    raise NormalizedApiError(
                        operation,
                        "execution_private_event_pending",
                        definite_reject=True,
                    )
                fence.active_writes += 1
                acquired = True

        def release() -> None:
            nonlocal acquired
            with fence.lock:
                if acquired:
                    fence.active_writes -= 1
                    acquired = False

        return acquire, release

    def _record_ctp_private_ingress(
        self,
        exchange_name: str,
        ordered_after_write: bool,
    ) -> bool:
        session = self._execution_session
        if session is None:
            return False
        return (
            session.note_private_ingress(
                exchange_name,
                ordered_after_write=ordered_after_write,
            )
            is True
        )

    def _revoke_ctp_arm_for_private_ingress(self, exchange_name: str) -> None:
        """Close a just-armed native gate after pre-write private ingress."""
        # A private ingress event is a preflight boundary even when the native
        # gate has already been disarmed by another transition.  Do not leave a
        # sibling, same-generation facade grant usable after this event.
        self._invalidate_ctp_execution_authorizations()
        lock = getattr(self, "_ctp_execution_transition_lock", None)
        if lock is None:
            with _CTP_TRANSITION_LOCK_INIT:
                lock = getattr(self, "_ctp_execution_transition_lock", None)
                if lock is None:
                    lock = threading.RLock()
                    self._ctp_execution_transition_lock = lock

        def disarm() -> None:
            state = self._ctp_execution_gate_state(
                exchange_name,
                operation="ctp_private_ingress",
            )
            if state.get("armed") is True:
                self._disarm_ctp_execution_gate(
                    exchange_name,
                    "execution_private_event_pending",
                    operation="ctp_private_ingress",
                )

        if not lock.acquire(blocking=False):
            self._defer_ctp_private_arm_revocation(exchange_name)
            return
        failed = False
        try:
            disarm()
        except Exception:
            failed = True
        finally:
            lock.release()
        if failed:
            self._defer_ctp_private_arm_revocation(
                exchange_name,
                teardown=True,
            )

    def _defer_ctp_private_arm_revocation(
        self,
        exchange_name: str,
        *,
        teardown: bool = False,
    ) -> None:
        """Finish native revocation off a callback thread after a transition."""
        fence = self._ctp_private_ingress_fence(exchange_name)
        with fence.lock:
            fence.revocation_pending = True
            fence.revocation_teardown_required = bool(
                fence.revocation_teardown_required or teardown
            )
            if fence.revocation_worker_active:
                return
            fence.revocation_worker_active = True

        def finish() -> None:
            lock = self._ctp_execution_transition_lock
            try:
                with lock:
                    with fence.lock:
                        reset_required = fence.revocation_teardown_required
                    if reset_required:
                        self._reset_ctp_execution_stream(exchange_name)
                        return
                    try:
                        state = self._ctp_execution_gate_state(
                            exchange_name,
                            operation="ctp_private_ingress",
                        )
                        if state.get("armed") is True:
                            self._disarm_ctp_execution_gate(
                                exchange_name,
                                "execution_private_event_pending",
                                operation="ctp_private_ingress",
                            )
                    except Exception:
                        # Native disarm could not be proven. The separate worker
                        # may safely stop/join the callback-producing stream.
                        self._reset_ctp_execution_stream(exchange_name)
            finally:
                with fence.lock:
                    fence.revocation_pending = False
                    fence.revocation_worker_active = False
                    fence.revocation_teardown_required = False

        threading.Thread(
            target=finish,
            daemon=True,
            name=f"btapi-ctp-private-revoke-{exchange_name}",
        ).start()

    def _ctp_private_stream_queue(
        self,
        exchange_name: str,
        target: queue.Queue[Any],
    ) -> _CtpPrivateIngressQueue:
        queues = getattr(self, "_ctp_private_ingress_queues", None)
        if not isinstance(queues, dict):
            queues = {}
            self._ctp_private_ingress_queues = queues
        current = queues.get(exchange_name)
        if isinstance(current, _CtpPrivateIngressQueue) and current.target is target:
            return current
        fence = self._ctp_private_ingress_fence(exchange_name)
        current = _CtpPrivateIngressQueue(
            target,
            fence,
            lambda ordered_after_write: self._record_ctp_private_ingress(
                exchange_name,
                ordered_after_write,
            ),
            lambda: self._revoke_ctp_arm_for_private_ingress(exchange_name),
        )
        queues[exchange_name] = current
        return current

    def _publish_private_event(
        self,
        exchange_name: str,
        item: Any,
        *,
        normalized: bool = False,
    ) -> None:
        """Publish one private-lane item through the managed CTP ingress fence."""
        source = self.data_queues.get(exchange_name)
        if source is None:
            return
        session = self._execution_session
        managed_ctp = bool(
            session is not None
            and str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() == "CTP"
        )
        if not managed_ctp:
            if normalized:
                self._normalized_event_pending[exchange_name].append(item)
            else:
                source.put(item)
            return
        producer = self._ctp_private_stream_queue(exchange_name, source)
        if normalized:
            producer.append_normalized(
                item,
                self._normalized_event_pending[exchange_name],
            )
        else:
            producer.put(item)

    def _ingest_ctp_private_queue(
        self,
        session: Any,
        exchange_name: str,
        *,
        operation: str,
    ) -> int:
        """Fence queued CTP order/trade events without dropping consumer delivery."""
        if self.transport_mode is not TransportMode.DIRECT:
            raise NormalizedApiError(
                operation,
                "ctp_recovery_private_queue_unavailable",
                definite_reject=True,
            )
        source = self.data_queues.get(exchange_name)
        if source is None or not callable(getattr(source, "qsize", None)):
            raise NormalizedApiError(
                operation,
                "ctp_recovery_private_queue_unavailable",
                definite_reject=True,
            )
        if not hasattr(self, "_normalized_event_pending"):
            self._normalized_event_pending = defaultdict(deque)
        if not hasattr(self, "_event_metrics"):
            self._event_metrics = defaultdict(int)
        fence = self._ctp_private_ingress_fence(exchange_name)
        events: list[dict[str, Any]] = []
        with fence.lock:
            normalized_pending = self._normalized_event_pending[exchange_name]
            raw_budget = [source.qsize()]
            while raw_budget[0] > 0 or normalized_pending:
                before_budget = raw_budget[0]
                before_pending = len(normalized_pending)
                event = self._poll_event_raw(exchange_name, source_budget=raw_budget)
                if event is not None:
                    events.append(event)
                    continue
                if raw_budget[0] == before_budget and len(normalized_pending) == before_pending:
                    break
        private_events = 0
        for event in events:
            if str(event.get("kind") or "").lower() in {"order", "trade"}:
                private_events += 1
                event = session.event(exchange_name, event)
            if event is not None:
                session.pending[exchange_name].append(event)
        return private_events




    def _ctp_account_risk_observation(
        self,
        session: Any,
        exchange_name: str,
        *,
        open_orders_known: bool,
        open_orders_empty: bool,
    ) -> dict[str, Any]:
        from ._normalization import normalize_result

        currency = session.config["account_currencies"].get(
            exchange_name, session.config["account_currency"]
        )
        currency = str(currency or "").strip().upper()
        if not currency:
            raise NormalizedApiError(
                "get_account_risk_snapshot",
                "ctp_account_currency_missing",
                definite_reject=True,
            )
        account_rows, fingerprint = self._ctp_bound_query_records(session, exchange_name, "account")
        position_rows, position_fingerprint = self._ctp_bound_query_records(
            session, exchange_name, "positions"
        )
        if len(account_rows) != 1 or position_fingerprint != fingerprint:
            raise NormalizedApiError(
                "get_account_risk_snapshot",
                "ctp_account_query_incomplete",
                definite_reject=True,
            )
        account_row = {
            **account_rows[0],
            "currency": currency,
            "account_id": fingerprint,
        }
        account = normalize_result("get_account", [account_row], exchange_name, currency)
        positions = normalize_result("get_position", position_rows, exchange_name, None)
        if not isinstance(account, Mapping) or not isinstance(positions, list):
            raise NormalizedApiError(
                "get_account_risk_snapshot",
                "ctp_account_risk_schema_invalid",
                definite_reject=True,
            )
        from .account_risk_projection import normalized_positions_are_flat

        def invalid_position():
            return NormalizedApiError(
                "get_account_risk_snapshot", "ctp_position_schema_invalid", definite_reject=True,
            )

        positions_flat = normalized_positions_are_flat(
            positions, invalid_shape=invalid_position, invalid_quantity=invalid_position,
            known_before_quantity=True,
        )
        return {
            "currency": currency,
            "equity": account.get("equity", account.get("value")),
            "positions_known": True,
            "positions_flat": positions_flat,
            "open_orders_known": open_orders_known,
            "open_orders_empty": open_orders_empty,
            "authenticated_account_id": fingerprint,
        }

    def _ctp_open_orders(self, session: Any, exchange_name: str) -> list[dict[str, Any]]:
        from bt_api_ctp.query_result_evidence import project_ctp_active_order_rows

        from ._normalization import normalize_result

        rows, fingerprint = self._ctp_bound_query_records(session, exchange_name, "orders")
        active_rows = project_ctp_active_order_rows(rows, fingerprint=fingerprint)
        result = normalize_result("get_open_orders", active_rows, exchange_name, None)
        if not isinstance(result, list):
            raise NormalizedApiError(
                "get_account_risk_snapshot",
                "ctp_orders_records_invalid",
                definite_reject=True,
            )
        return result

    def _collect_account_risk_snapshot(
        self,
        *,
        initialize_baseline: bool,
        reset_loss_latch: bool,
        _risk_collection_held: bool = False,
    ) -> dict[str, Any]:
        session = self._execution_session
        if session is None or session.config["market_data_only"]:
            raise NormalizedApiError(
                "get_account_risk_snapshot",
                "execution_session_required",
                definite_reject=True,
            )
        if not _risk_collection_held:
            # The session marks the entire remote-read interval in progress.
            # Order placement checks that state both before mapping and under
            # the journal mutex, while a dedicated lock serializes refreshes.
            with session.risk_collection():
                return self._collect_account_risk_snapshot(
                    initialize_baseline=initialize_baseline,
                    reset_loss_latch=reset_loss_latch,
                    _risk_collection_held=True,
                )
        observations: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        risk_venues = session.risk_venues()
        has_ctp = any(
            str(venue).partition(DATANAME_SEPARATOR)[0].upper() == "CTP" for venue in risk_venues
        )
        requires_open_order_proof = (
            reset_loss_latch
            or (initialize_baseline and session.requires_risk_baseline())
            or has_ctp
        )

        def strict_open_order_sweep(
            error_scope: str,
        ) -> tuple[set[str], dict[str, str]]:
            empty_venues: set[str] = set()
            sweep_errors: dict[str, str] = {}
            for sweep_venue in risk_venues:
                error_key = f"{sweep_venue}:{error_scope}"
                try:
                    if str(sweep_venue).partition(DATANAME_SEPARATOR)[0].upper() == "CTP":
                        open_orders = self._ctp_open_orders(session, sweep_venue)
                    else:
                        open_orders = self.get_open_orders(sweep_venue, None, normalized=True)
                    if not isinstance(open_orders, list):
                        raise ValueError("incomplete_open_order_evidence")
                    if open_orders:
                        sweep_errors[error_key] = "open_orders_present"
                    else:
                        empty_venues.add(sweep_venue)
                except Exception as error:
                    code = getattr(error, "code", None)
                    sweep_errors[error_key] = str(code or type(error).__name__)
            return empty_venues, sweep_errors

        initially_empty_venues: set[str] = set()
        if requires_open_order_proof:
            initially_empty_venues, initial_open_order_errors = strict_open_order_sweep(
                "open_orders"
            )
            errors.update(initial_open_order_errors)

        for venue in risk_venues:
            currency = session.config["account_currencies"].get(
                venue, session.config["account_currency"]
            )
            open_orders_known = not requires_open_order_proof or venue in initially_empty_venues
            open_orders_empty = open_orders_known
            try:
                if str(venue).partition(DATANAME_SEPARATOR)[0].upper() == "CTP":
                    observations[venue] = self._ctp_account_risk_observation(
                        session,
                        venue,
                        open_orders_known=open_orders_known,
                        open_orders_empty=open_orders_empty,
                    )
                    continue
                account = self.get_account(venue, currency or "ALL", normalized=True)
                positions = self.get_position(venue, None, normalized=True)
                if not isinstance(account, Mapping) or not isinstance(positions, list):
                    raise ValueError("incomplete_account_risk_evidence")
                from .account_risk_projection import normalized_positions_are_flat

                positions_flat = normalized_positions_are_flat(
                    positions,
                    invalid_shape=lambda: ValueError("incomplete_position_evidence"),
                    invalid_quantity=lambda: ValueError("invalid_position_quantity"),
                    known_before_quantity=False,
                )
                observations[venue] = {
                    "currency": account.get("currency") or currency,
                    "equity": account.get("equity", account.get("value")),
                    "positions_known": True,
                    "positions_flat": positions_flat,
                    "open_orders_known": open_orders_known,
                    "open_orders_empty": open_orders_empty,
                    "authenticated_account_id": account.get("account_id"),
                }
            except Exception as error:
                code = getattr(error, "code", None)
                errors[venue] = str(code or type(error).__name__)
        return session.account_risk_snapshot(
            observations,
            evidence_errors=errors,
            initialize_baseline=initialize_baseline,
            reset_loss_latch=reset_loss_latch,
            baseline_commit_check=lambda: strict_open_order_sweep("baseline_commit_open_orders")[1],
        )

    async def async_get_account_risk_snapshot(
        self,
        *,
        initialize_baseline: bool = False,
    ) -> dict[str, Any]:
        """Run the authenticated risk snapshot without blocking an event loop."""
        return await asyncio.to_thread(
            self.get_account_risk_snapshot,
            initialize_baseline=initialize_baseline,
        )

    async def async_reset_account_maximum_loss_latch(self) -> dict[str, Any]:
        """Run the explicit loss-latch reset without blocking an event loop."""
        return await asyncio.to_thread(self.reset_account_maximum_loss_latch)

    def get_event_metrics(self) -> dict[str, int]:
        """Return process-local ingress/coalescing counters without resetting them."""
        return dict(self._event_metrics)

    def _on_websocket_connected(self, payload: Any) -> None:
        """Schedule one private-state backfill for each reconnect generation."""
        if not isinstance(payload, dict) or payload.get("stream_role") != "account":
            return
        try:
            generation = int(payload.get("connection_generation", 0))
        except (TypeError, ValueError):
            return
        if generation <= 1:
            return
        exchange_name = str(payload.get("exchange_name") or "")
        asset_type = str(payload.get("asset_type") or "")
        if "___" not in exchange_name and exchange_name and asset_type:
            exchange_name = f"{exchange_name}___{asset_type}"
        if exchange_name not in self.list_exchanges():
            return
        key = (exchange_name, generation)
        if key in self._private_reconcile_generations:
            return
        self._private_reconcile_generations.add(key)
        self._event_metrics["private_reconcile_triggers"] += 1
        self._publish_private_event(
            exchange_name,
            {
                "kind": "reconcile",
                "exchange_name": exchange_name,
                "status": "required",
                "connection_generation": generation,
                "scopes": ("orders", "account", "positions", "trades"),
                "event_id": f"reconcile:{exchange_name}:{generation}:required",
            },
        )
        threading.Thread(
            target=self.reconcile_private_state,
            args=(exchange_name,),
            kwargs={"connection_generation": generation},
            daemon=True,
            name=f"btapi-reconcile-{exchange_name}-{generation}",
        ).start()

    @staticmethod
    def _reconcile_rows(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            return [value]
        if isinstance(value, (list, tuple)):
            return [row for row in value if isinstance(row, dict)]
        return []

    def reconcile_private_state(
        self,
        exchange_name: str,
        *,
        connection_generation: int | None = None,
    ) -> dict[str, Any]:
        """Backfill private state after reconnect and publish one audit status."""
        operations = (
            ("orders", lambda: self.get_open_orders(exchange_name, normalized=True)),
            ("account", lambda: self.get_account(exchange_name, normalized=True)),
            ("positions", lambda: self.get_position(exchange_name, normalized=True)),
            ("trades", lambda: self.get_deals(exchange_name, normalized=True)),
        )
        scopes: dict[str, dict[str, Any]] = {}
        for scope, call in operations:
            try:
                rows = self._reconcile_rows(call())
                scopes[scope] = {"status": "complete", "records": len(rows)}
                kind = {
                    "orders": "order",
                    "account": "account",
                    "positions": "position",
                    "trades": "trade",
                }[scope]
                for row in rows:
                    event = {"kind": kind, **row}
                    if scope == "orders" and self._execution_session is not None:
                        # get_open_orders(normalized=True) already merged these
                        # rows into the execution session.
                        continue
                    self._publish_private_event(
                        exchange_name,
                        event,
                        normalized=True,
                    )
            except Exception as exc:
                scopes[scope] = {
                    "status": "failed",
                    "error_code": str(getattr(exc, "code", type(exc).__name__)),
                }
        failed = [name for name, value in scopes.items() if value["status"] == "failed"]
        generation = connection_generation if connection_generation is not None else 0
        result = {
            "kind": "reconcile",
            "exchange_name": exchange_name,
            "status": "complete" if not failed else "partial_failed",
            "connection_generation": generation,
            "scopes": scopes,
            "failed_scopes": tuple(failed),
            "event_id": f"reconcile:{exchange_name}:{generation}:result",
        }
        self._publish_private_event(exchange_name, result)
        return result

    async def async_reconcile_private_state(
        self,
        exchange_name: str,
        *,
        connection_generation: int | None = None,
    ) -> dict[str, Any]:
        """Run private reconnect backfill without blocking the caller's loop."""
        return await asyncio.to_thread(
            self.reconcile_private_state,
            exchange_name,
            connection_generation=connection_generation,
        )

    def get_environment_info(self, exchange_name: str) -> dict[str, Any]:
        """Return the selected transport environment without configuration secrets.

        Direct feeds expose their resolved environment through the venue's
        exchange-data object. A ZMQ client cannot prove the gateway's server-side
        environment, so it deliberately reports an unverified unknown value.
        """
        if exchange_name not in self.list_exchanges():
            raise ExchangeNotFoundError(exchange_name, self.list_exchanges())
        result = {
            "exchange_name": exchange_name,
            "environment": "unknown",
            "api_region": None,
            "simulated": None,
            "transport_mode": self.transport_mode.value,
            "verified": False,
        }
        if self.transport_mode is not TransportMode.DIRECT:
            return result

        try:
            verifier = getattr(self.exchange_feeds[exchange_name], "get_environment_info", None)
            if not callable(verifier):
                return result
            proof = verifier()
            if not isinstance(proof, dict):
                return result
            environment = proof.get("environment")
            simulated = proof.get("simulated")
            verified = proof.get("verified") is True
            if environment not in {"production", "demo", "testnet"}:
                return result
            if not isinstance(simulated, bool) or simulated != (environment != "production"):
                return result
            policy = _provider_execution_configuration_policy(
                exchange_name, operation="get_environment_info"
            )
            if policy.api_regions:
                api_region = proof.get("api_region")
                if not isinstance(api_region, str) or api_region not in policy.api_regions:
                    return result
                if api_region in policy.production_only_api_regions and environment != "production":
                    return result
            else:
                api_region = None
        except Exception:
            return result
        return {
            "exchange_name": exchange_name,
            "environment": environment,
            "api_region": api_region,
            "simulated": simulated,
            "transport_mode": self.transport_mode.value,
            "verified": verified,
        }

    def fetch_market_history(
        self,
        exchange_name: str,
        instrument: MarketInstrumentKey,
        timeframe: str | None,
        *,
        since: datetime | None = None,
        limit: int | None = None,
        preserve_raw_bars: bool = False,
    ) -> tuple[MarketBar, ...]:
        """Fetch complete provider-neutral history for one exact selected route.

        Provider registration supplies source/entitlement identity and bounded
        collection policy. Missing capability is a finite error; raw provider
        fields are never silently discarded by this typed contract.
        """
        try:
            from .market_history_composition import fetch_market_history
        except ModuleNotFoundError as exc:
            if exc.name != "bt_api_base.market_history_provider_protocol":
                raise
            raise NormalizedApiError(
                "market_history",
                "MARKET_HISTORY_PROVIDER_CAPABILITY_UNAVAILABLE",
                definite_reject=True,
            ) from None

        return fetch_market_history(
            self,
            exchange_name,
            instrument,
            timeframe,
            since=since,
            limit=limit,
            preserve_raw_bars=preserve_raw_bars,
        )

    def query_facade(self, exchange_name: str) -> QueryFacade:
        """Compose the selected route's provider-neutral query facade.

        The provider factory receives the already-selected SDK feed as an
        opaque route client. Provider-specific unwrapping stays inside that
        provider package. A fresh facade is composed for each call so a new
        provider session generation can be captured after reconnect; callers
        should retain one returned facade only for the batch/receipt/lease
        lifecycle it owns. Legacy venue query methods are not routed here.
        """
        from .query_composition import (
            QueryCompositionError,
            SelectedQueryRoute,
            create_query_facade as compose_query_facade,
        )

        if type(exchange_name) is not str:
            raise QueryCompositionError("QUERY_PROVIDER_ROUTE_INVALID")
        selected = self._selected_provider_route(exchange_name)
        if selected is None:
            raise QueryCompositionError("QUERY_PROVIDER_ROUTE_CLIENT_UNAVAILABLE")
        feed, environment = selected
        with self._sdk_capability_lock:
            pinned_feed = self._selected_route_clients.get(exchange_name)
        if pinned_feed is not feed:
            raise QueryCompositionError("QUERY_PROVIDER_ROUTE_CLIENT_UNAVAILABLE")

        route = SelectedQueryRoute(
            exchange_name=exchange_name,
            client=feed,
            environment=environment,
            execution_context=self._provider_execution_contexts.get(exchange_name),
            order_observation_issuer=self._order_correlation_issuer,
        )
        return compose_query_facade(
            route,
            account_session_snapshot_validator=(
                self._validate_query_account_session_snapshot
            ),
        )

    def _validate_query_account_session_snapshot(
        self,
        route: Any,
        snapshot: Any,
    ) -> bool:
        """Bind query evidence to the exact SDK-selected account-session route."""
        from bt_api_base.account_session_protocol import AccountSessionSnapshot
        from .query_composition import SelectedQueryRoute

        if type(route) is not SelectedQueryRoute:
            return False
        if snapshot is not None and (
            type(snapshot) is not AccountSessionSnapshot
            or route.exchange_name != snapshot.exchange_name
        ):
            return False

        selected = self._selected_provider_route(route.exchange_name)
        if selected is None:
            return False
        feed, environment = selected
        with self._sdk_capability_lock:
            pinned_feed = self._selected_route_clients.get(route.exchange_name)
        if (
            feed is not route.client
            or environment != route.environment
            or route.execution_context
            is not self._provider_execution_contexts.get(route.exchange_name)
            or route.order_observation_issuer is not self._order_correlation_issuer
            or self.exchange_feeds.get(route.exchange_name) is not route.client
            or pinned_feed is not route.client
        ):
            return False

        if snapshot is None:
            current = self._selected_provider_route(route.exchange_name)
            with self._sdk_capability_lock:
                current_pinned_feed = self._selected_route_clients.get(route.exchange_name)
            return bool(
                current is not None
                and current[0] is route.client
                and current[1] == route.environment
                and self.exchange_feeds.get(route.exchange_name) is route.client
                and current_pinned_feed is route.client
            )

        router = self._account_capability_router()
        try:
            valid = router.validate_account_session_snapshot_binding(
                snapshot,
                expected_client=route.client,
                expected_environment=route.environment,
            )
        except Exception:
            return False
        if type(valid) is not bool or valid is not True:
            return False

        current = self._selected_provider_route(route.exchange_name)
        with self._sdk_capability_lock:
            current_pinned_feed = self._selected_route_clients.get(route.exchange_name)
        return bool(
            current is not None
            and current[0] is route.client
            and current[1] == route.environment
            and route.execution_context
            is self._provider_execution_contexts.get(route.exchange_name)
            and route.order_observation_issuer is self._order_correlation_issuer
            and self.exchange_feeds.get(route.exchange_name) is route.client
            and current_pinned_feed is route.client
        )

    def account_balance_snapshot(self, exchange_name: str) -> AccountBalanceSnapshotPort:
        """Select the route's neutral, full-scope account balance query port.

        The returned adapter uses the same registered QueryFacade provider
        families and their source/generation/lease evidence. Routes without a
        query provider return an explicit unavailable port; legacy
        ``get_balance`` methods are not a fallback.
        """
        from bt_api_py.account_balance_facade import AccountBalanceSnapshotFacade
        from bt_api_py.query_composition import QueryCompositionError

        try:
            query_facade = self.query_facade(exchange_name)
        except QueryCompositionError:
            return AccountBalanceSnapshotFacade.unavailable(exchange_name)
        try:
            session_port = self.account_session(exchange_name)
        except Exception:
            session_port = None
        return AccountBalanceSnapshotFacade(exchange_name, query_facade, session_port)

    def account_position_snapshot(self, exchange_name: str) -> AccountPositionSnapshotPort:
        """Select the route's neutral, full-scope account positions port.

        The adapter composes the exact selected route's standard query and
        account-session capabilities. Routes without a positions query
        provider return an explicit unavailable port; legacy position getters
        are not a fallback.
        """
        from bt_api_py.account_position_snapshot_facade import AccountPositionSnapshotFacade
        from bt_api_py.query_composition import QueryCompositionError

        try:
            query_facade = self.query_facade(exchange_name)
        except QueryCompositionError:
            return AccountPositionSnapshotFacade.unavailable(exchange_name)
        try:
            session_port = self.account_session(exchange_name)
        except Exception:
            session_port = None
        return AccountPositionSnapshotFacade(exchange_name, query_facade, session_port)

    def account_session(self, exchange_name: str) -> AccountSessionPort:
        """Select the registered neutral account-session port for one route."""
        return self._account_capability_router().account_session(exchange_name)

    def settlement_confirmation(self, exchange_name: str) -> SettlementConfirmationPort:
        """Select the separately authorized settlement port for one route."""
        return self._account_capability_router().settlement_confirmation(exchange_name)

    def account_session_snapshot(self, exchange_name: str) -> AccountSessionSnapshot:
        """Read the exact route's SDK-validated account-session snapshot."""
        return self._account_capability_router().account_session_snapshot(exchange_name)

    def account_preflight(
        self,
        request: AccountPreflightRequest,
    ) -> AccountPreflightSnapshot:
        """Collect and validate neutral account readiness evidence in the SDK."""
        return self._account_capability_router().account_preflight(request)

    def verify_account_settlement(
        self,
        exchange_name: str,
        *,
        deadline: datetime,
    ) -> SettlementVerificationReceipt:
        """Perform one SDK-owned, route-bound read-only settlement readback."""
        return self._account_capability_router().verify_account_settlement(
            exchange_name,
            deadline=deadline,
        )

    def confirm_settlement_once(
        self,
        exchange_name: str,
        request: SettlementConfirmationRequest,
        *,
        authorization: object,
    ) -> SettlementConfirmationOutcome:
        """Run one SDK-owned settlement claim, command, and verification cycle."""
        return self._account_capability_router().confirm_settlement_once(
            exchange_name,
            request,
            authorization=authorization,
        )

    def order_capacity(self, exchange_name: str) -> ExecutionCapacityFacade:
        """Select the provider-neutral bounded order-capacity facade."""
        return self._typed_order_capability_router().order_capacity(exchange_name)

    def correlated_order_execution(
        self,
        exchange_name: str,
    ) -> CorrelatedOrderExecutionFacade:
        """Select one direct or explicitly managed owner without mode fallback."""
        with self._sdk_capability_lock:
            if self._execution_configuration_in_progress:
                raise NormalizedApiError(
                    "correlated_order_execution", "execution_configuration_in_progress",
                    definite_reject=True,
                )
            direct = (
                not self._managed_configuration_requested
                and self._execution_session is None
                and self._order_correlation_issuer is None
            )
            if direct:
                if self._managed_order_selection_started:
                    raise NormalizedApiError(
                        "correlated_order_execution", "order_owner_mode_changed", definite_reject=True
                    )
                self._direct_order_selection_started = True
                selected = self._direct_order_capability_router()
            else:
                if self._direct_order_selection_started:
                    raise NormalizedApiError(
                        "correlated_order_execution", "order_owner_mode_changed", definite_reject=True
                    )
                self._managed_order_selection_started = True
                selected = self._typed_order_capability_router()
        return selected.correlated_order_execution(exchange_name)

    def execution_economics(self, exchange_name: str) -> ExecutionEconomicsPort:
        """Select the trusted route-bound economics evidence port.

        The returned SDK wrapper hides the injected owner. Without an explicit
        trusted owner resolver, route, or current same-route evidence source,
        its evaluation returns a typed unavailable decision and revalidation
        returns exact ``False``.
        """
        return self._execution_economics_capability_router().execution_economics(
            exchange_name
        )

    def execution_identity_assurance(
        self,
        exchange_name: str,
    ) -> ExecutionIdentityAssurancePort:
        """Select provider-issued identity assurance for one exact route.

        Account aliases and the SDK's execution identity mapping are not
        provider evidence. If no exact provider registrar can issue and
        revalidate assurance for the selected route/session, the returned port
        fails closed with a typed unavailable result. This proof does not grant
        order authorization or dispatch permission.
        """
        return self._execution_identity_assurance_capability_router().execution_identity_assurance(
            exchange_name
        )

    def execution_rules(self, exchange_name: str) -> ExecutionRulesPort:
        """Select the exact route's order-rules evaluator for Store preflight."""
        return self._execution_rules_capability_router().execution_rules(exchange_name)

    def execution_rules_snapshot(
        self,
        exchange_name: str,
    ) -> ExecutionRulesSnapshotPort:
        """Select an independent route-bound instrument and fee snapshot port.

        The port exposes neutral facts and validates its exact issued snapshot
        against the selected provider's current session/source. It is not an
        order decision or a write capability.
        """
        return self._execution_rules_snapshot_capability_router().execution_rules_snapshot(
            exchange_name
        )

    def order_position_evidence(
        self,
        exchange_name: str,
    ) -> OrderPositionEvidenceFacadePort:
        """Select the exact route's source-issued current position facade."""
        return self._order_position_evidence_capability_router().order_position_evidence(
            exchange_name
        )

    def bind_provider_execution_context(
        self,
        exchange_name: str,
        selected_feed: Any,
        context: object,
        *,
        context_verifier: Any,
        timeout_seconds: float = 5.0,
    ) -> None:
        """Bind one runtime-issued opaque context to its exact selected feed.

        This post-init hook supports runtimes whose context can only be built
        after the selected feed has connected. The verifier must be supplied
        by trusted SDK runtime composition and must return exact ``True`` for
        the current route/feed/session/context identity. That check establishes
        binding provenance only; it does not authorize an order or native call.
        Each route can be bound once, and no typed-order route may have been
        resolved before this method is called.
        """
        if type(exchange_name) is not str or not exchange_name:
            raise TypeError("provider_execution_context_route_invalid")
        if context is None:
            raise TypeError("provider_execution_context_required")
        if (
            type(timeout_seconds) not in (int, float)
            or isinstance(timeout_seconds, bool)
            or not 0.01 <= float(timeout_seconds) <= 30.0
        ):
            raise ValueError("provider_execution_context_timeout_invalid")
        validate = getattr(
            context_verifier,
            "validate_selected_feed_order_provider_context",
            None,
        )
        if not callable(validate):
            raise TypeError("provider_execution_context_verifier_unavailable")

        with self._sdk_capability_lock:
            if self._typed_order_provider_router is not None:
                raise RuntimeError("provider_execution_context_router_already_created")
            if exchange_name in self._typed_order_routes_resolved:
                raise RuntimeError("provider_execution_context_route_already_resolved")
            if exchange_name in self._provider_execution_contexts:
                raise RuntimeError("provider_execution_context_already_bound")
            if self.exchange_feeds.get(exchange_name) is not selected_feed:
                raise RuntimeError("provider_execution_context_feed_mismatch")
            selected = self._selected_provider_route(exchange_name)
            if selected is None or selected[0] is not selected_feed:
                raise RuntimeError("provider_execution_context_route_unavailable")
            try:
                valid = validate(
                    selected_feed,
                    context,
                    exchange_name=exchange_name,
                    timeout_seconds=float(timeout_seconds),
                )
            except Exception:
                valid = False
            if type(valid) is not bool or valid is not True:
                raise RuntimeError("provider_execution_context_binding_unverified")
            execution_contexts = dict(self._provider_execution_contexts)
            execution_contexts[exchange_name] = context
            self._provider_execution_contexts = MappingProxyType(execution_contexts)

    def market_tick_projector(
        self,
        exchange_name: str,
        instrument: MarketInstrumentKey,
    ) -> ServiceMarketTickProjector:
        """Compose one provider-owned tick projector for an exact instrument.

        Each call creates a dedicated projector and subscription. The selected
        feed remains opaque to the SDK-neutral composer, and the provider must
        bind its authenticated subscription to the exact canonical key before
        returning a projector. No direct-feed polling fallback is used.
        """
        from .market_tick_composition import (
            MarketTickCompositionError,
            SelectedMarketTickRoute,
            create_market_tick_projector,
        )
        from .service_contracts import MarketInstrumentKey

        if type(exchange_name) is not str:
            raise MarketTickCompositionError("MARKET_TICK_ROUTE_INVALID")
        if type(instrument) is not MarketInstrumentKey:
            raise MarketTickCompositionError("MARKET_TICK_ROUTE_INVALID")
        selected = self._selected_provider_route(exchange_name)
        if selected is None:
            raise MarketTickCompositionError("MARKET_TICK_ROUTE_CLIENT_UNAVAILABLE")
        feed, environment = selected
        route = SelectedMarketTickRoute(
            exchange_name=exchange_name,
            client=feed,
            instrument=instrument,
            environment=environment,
        )
        return create_market_tick_projector(
            route,
            runtime_factory=self._market_runtime_factory,
        )

    # ------------------------------------------------------------------
    # Iteration 62: direct writes, settlement, sessions, clock qualification
    # ------------------------------------------------------------------
    @property
    def write_admission(self) -> Any:
        """The environment-neutral write admission of this facade."""
        return self._write_admission

    def direct_writes(self) -> Any:
        """Return the SDK direct write coordinator (no execution session)."""
        with self._sdk_capability_lock:
            coordinator = self._direct_write_coordinator
            if coordinator is None:
                from .direct_write import DIRECT_WRITE_PROVIDER_GROUP, DirectWriteCoordinator
                from .provider_entrypoints import load_route_entrypoint

                def _factory(exchange_name: str) -> Any:
                    factory = load_route_entrypoint(
                        DIRECT_WRITE_PROVIDER_GROUP, exchange_name, optional=True
                    )
                    if factory is None:
                        return None
                    return factory(self._get_feed(exchange_name))

                coordinator = DirectWriteCoordinator(self._write_admission, _factory)
                self._direct_write_coordinator = coordinator
            return coordinator

    def poll_direct_execution_events(
        self, exchange_name: str, max_events: int = 256
    ) -> tuple[Any, ...]:
        """Drain correlated, de-duplicated direct order/trade returns."""
        return self.direct_writes().poll_events(exchange_name, max_events)

    def direct_account_facts(self, exchange_name: str, **sections: Any) -> Any:
        """Read-only account facts (positions, working orders, trades, funds).

        Neutral :class:`DirectAccountFacts` from the route's direct provider for
        host reconciliation (D62-05/13/16/22).  Needs no write admission.
        """
        provider = self.direct_writes().provider(exchange_name)
        reader = getattr(provider, "account_facts", None)
        if not callable(reader):
            raise NormalizedApiError(
                "direct_account_facts", "capability_not_supported", definite_reject=True
            )
        return reader(**sections)

    def direct_instrument_positions(self, exchange_name: str, symbol: str, **kwargs: Any) -> Any:
        """Read-only positions of one instrument (natively filtered query, D62-11a).

        Returns the provider's instrument-scoped result (``scope ==
        "instrument"``, ``proves_account_flat is False``).  It never proves the
        whole account flat, even when empty; whole-account reconciliation and
        audits must use :meth:`direct_account_facts`.  A provider result that
        does not declare instrument scope for the requested symbol is refused.
        Needs no write admission.
        """
        operation = "direct_instrument_positions"
        if type(symbol) is not str or not symbol:
            raise NormalizedApiError(operation, "symbol_invalid", definite_reject=True)
        provider = self.direct_writes().provider(exchange_name)
        reader = getattr(provider, "instrument_positions", None)
        if not callable(reader):
            raise NormalizedApiError(operation, "capability_not_supported", definite_reject=True)
        result = reader(symbol, **kwargs)
        if (
            getattr(result, "scope", None) != "instrument"
            or getattr(result, "proves_account_flat", None) is not False
            or getattr(result, "symbol", None) != symbol
        ):
            raise NormalizedApiError(operation, "result_scope_invalid", definite_reject=True)
        return result

    def direct_trading_permissions(
        self, exchange_name: str, symbols: Any, **kwargs: Any
    ) -> Any:
        """Read-only trading-permission facts of ``symbols`` (D62-25, 62.03).

        Returns the provider's instrument-permission result: per instrument a
        set of facts (``known_positive`` / ``known_negative`` / ``unknown``
        with finite reasons) bound to one session generation and trading day.
        It never proves that the account may trade (``proves_permission`` is
        False); hosts may refuse locally only on a complete, current,
        definitive negative and must treat everything else as unknown, the
        venue remaining authoritative.  A result of another scope or for other
        instruments is refused.  Routes whose provider has no such read
        (``capability_not_supported``) are unchanged.  Needs no write admission.
        """
        operation = "direct_trading_permissions"
        if symbols is None or isinstance(symbols, (str, bytes, Mapping)):
            raise NormalizedApiError(operation, "symbols_invalid", definite_reject=True)
        try:
            names = tuple(symbols)
        except TypeError:
            raise NormalizedApiError(operation, "symbols_invalid", definite_reject=True) from None
        if not names or any(type(name) is not str or not name for name in names):
            raise NormalizedApiError(operation, "symbols_invalid", definite_reject=True)
        provider = self.direct_writes().provider(exchange_name)
        reader = getattr(provider, "trading_permission_facts", None)
        if not callable(reader):
            raise NormalizedApiError(operation, "capability_not_supported", definite_reject=True)
        result = reader(names, **kwargs)
        try:
            returned = tuple(entry.symbol for entry in result.instruments)
        except Exception:
            returned = None
        if (
            getattr(result, "scope", None) != "instrument_permission"
            or getattr(result, "proves_permission", None) is not False
            or type(getattr(result, "complete", None)) is not bool
            or returned is None
            or len(set(returned)) != len(returned)
            or set(returned) != set(names)
        ):
            raise NormalizedApiError(operation, "result_scope_invalid", definite_reject=True)
        return result

    def ensure_settlement_confirmed(
        self,
        exchange_name: str,
        *,
        pre_dispatch_commit: Any,
        host_scope: str,
    ) -> Any:
        """Read the settlement state and confirm it under write admission (D62-20)."""
        return self.direct_writes().ensure_settlement(
            exchange_name, commit_port=pre_dispatch_commit, host_scope=host_scope
        )

    def trading_session_schedule(self, exchange_name: str) -> Any:
        """Provider-owned trading-session schedule for one route (D62-36)."""
        with self._sdk_capability_lock:
            schedule = self._trading_session_schedules.get(exchange_name)
            if schedule is None:
                from .provider_entrypoints import load_route_entrypoint

                factory = load_route_entrypoint(
                    "bt_api.trading_session_providers", exchange_name
                )
                schedule = factory(self.exchange_feeds.get(exchange_name))
                self._trading_session_schedules[exchange_name] = schedule
            return schedule

    @property
    def market_clock_verifier(self) -> Any:
        return self._market_clock_verifier

    def _venue_clock_check(self, exchange_name: str, venue: str) -> Any:
        verifier = self._market_clock_verifier
        if verifier is None:
            return None
        try:
            provider = self.direct_writes().provider(exchange_name)
        except Exception:
            return None
        reader = getattr(provider, "login_clock_observation", None)
        observation = reader() if callable(reader) else None
        if not isinstance(observation, Mapping) or not observation.get("exchange_times"):
            # Venues without login exchange times (OKX, Binance) expose a
            # REST server-time observation instead.
            return self._venue_epoch_clock_check(exchange_name, venue, provider, verifier)
        generation = observation.get("connection_generation")
        cache = self.__dict__.setdefault("_venue_clock_checks", {})
        key = (exchange_name, venue, generation)
        cached = cache.get(key)
        if cached is not None:
            return cached
        times = observation.get("exchange_times") or {}
        local_ns = observation.get("local_received_ns")
        hms = times.get(venue) or times.get("SHFE") or next(
            (value for value in times.values() if value), ""
        )
        if not hms or type(local_ns) is not int:
            return None
        check = verifier.check_venue_time(
            hms,
            local_ns,
            latency_allowance_ms=self._market_clock_max_latency_seconds * 1000.0,
            source=f"{exchange_name}:{venue}:login",
            # Per-tick path: never an inline SNTP measurement (it would block
            # the stream owner for timeout x servers on every tick while SNTP
            # is unreachable); the background refresher measures.
            refresh_if_expired=False,
        )
        if check is not None:
            cache[key] = check
        return check

    def _venue_epoch_clock_check(
        self, exchange_name: str, venue: str, provider: Any, verifier: Any
    ) -> Any:
        reader = getattr(provider, "server_time_observation", None)
        observation = reader() if callable(reader) else None
        if not isinstance(observation, Mapping):
            return None
        sent, received = observation.get("local_sent_ns"), observation.get("local_received_ns")
        if type(sent) is not int or type(received) is not int:
            return None
        cache = self.__dict__.setdefault("_venue_clock_checks", {})
        key = (exchange_name, venue, "epoch", received)
        cached = cache.get(key)
        if cached is not None:
            return cached
        check = verifier.check_venue_epoch(
            observation.get("venue_epoch_ms"),
            sent,
            received,
            granularity_ms=float(observation.get("granularity_ms") or 1.0),
            latency_allowance_ms=self._market_clock_max_latency_seconds * 1000.0,
            source=f"{exchange_name}:{venue}:server_time",
            refresh_if_expired=False,  # per-tick path, see _venue_clock_check
        )
        if check is not None:
            for stale in [k for k in cache if k[:3] == (exchange_name, venue, "epoch")]:
                cache.pop(stale, None)
            cache[key] = check
        return check

    def _qualify_market_tick(self, exchange_name: str, event: Any) -> Any:
        verifier = self._market_clock_verifier
        if verifier is None:
            return event
        from bt_api_base.market_tick_protocol import MarketTickEvent

        from .clock_verification import qualify_market_tick_event

        if type(event) is not MarketTickEvent:
            return event
        evidence = verifier.evidence(refresh_if_expired=False)
        check = self._venue_clock_check(exchange_name, event.data.instrument.venue)
        return qualify_market_tick_event(
            event,
            evidence,
            check,
            max_latency_seconds=self._market_clock_max_latency_seconds,
        )

    def market_stream(self) -> MarketStreamPort:
        """Return this facade's single SDK-owned, multi-channel stream owner.

        The owner binds exact selected routes, checks provider channel
        capability before opening a source, and multiplexes supported
        channels on one provider session per route/instrument. Tick requests
        use the multi-channel provider when it advertises tick; otherwise the
        existing exact-route typed tick projector remains available through
        the same facade. Unsupported channels never use ``poll_events`` as an
        implicit fallback.
        """
        with self._sdk_capability_lock:
            owner = self._market_stream_owner
            if owner is None:
                from .service_market_stream_owner import ProviderMarketStreamOwner

                owner = ProviderMarketStreamOwner(
                    self,
                    wake_callback=self._notify_event_wakers,
                )
                self._market_stream_owner = owner
            return owner

    def market_session_resolver(
        self,
        exchange_name: str,
        instrument: MarketInstrumentKey,
    ) -> MarketSessionResolver:
        """Select the registered neutral session resolver for one exact route.

        The selected feed remains opaque here. Provider-owned factories bind
        the canonical instrument to their own schedule source; an unresolved
        or unverified route is returned as a typed unavailable resolver.
        """
        return self._market_session_capability_router().market_session_resolver(
            exchange_name,
            instrument,
        )

    def futures_expected_calendar(
        self,
        exchange_name: str,
    ) -> FuturesExpectedCalendarPort:
        """Select the provider-owned expected calendar for one exact route.

        The selected feed and verified environment stay inside SDK composition.
        A missing or conflicting route provider raises a finite typed
        composition error; no venue-name fallback is used.
        """
        return self._futures_expected_calendar_capability_router().expected_calendar(
            exchange_name
        )

    def reconciliation(self, exchange_name: str) -> ReconciliationFacadePort:
        """Select an explicitly configured neutral reconciliation facade."""
        from .reconciliation_routes import ReconciliationRouteError

        composer = self._reconciliation_composer
        if composer is None:
            raise ReconciliationRouteError("reconciliation_route_unavailable")
        return composer.reconciliation(exchange_name)

    def _selected_provider_route(self, exchange_name: object) -> tuple[Any, str | None] | None:
        """Resolve only SDK-selected route facts shared by neutral capabilities."""
        if type(exchange_name) is not str:
            return None
        feeds = getattr(self, "exchange_feeds", None)
        if not isinstance(feeds, Mapping):
            return None
        feed = feeds.get(exchange_name)
        if feed is None:
            return None

        environment = None
        try:
            info = self.get_environment_info(exchange_name)
        except Exception:
            info = None
        if (
            isinstance(info, Mapping)
            and info.get("verified") is True
            and type(info.get("environment")) is str
            and info["environment"] in {"production", "demo", "testnet"}
        ):
            environment = info["environment"]
        return feed, environment

    def _resolve_account_session_route(self, exchange_name: str) -> Any | None:
        """Bind account capabilities to the exact selected SDK feed."""
        from .account_session_composition import SelectedAccountSessionRoute

        selected = self._selected_provider_route(exchange_name)
        if selected is None:
            return None
        feed, environment = selected
        return SelectedAccountSessionRoute(
            exchange_name=exchange_name,
            client=feed,
            environment=environment,
            settlement_context=None,
        )

    def _resolve_typed_order_route(self, exchange_name: str) -> Any | None:
        """Build an exact typed-order route without inspecting provider fields."""
        from .correlated_execution import SelectedExecutionRoute

        if type(exchange_name) is str:
            with self._sdk_capability_lock:
                self._typed_order_routes_resolved.add(exchange_name)
        selected = self._selected_provider_route(exchange_name)
        if selected is None:
            return None
        feed, environment = selected
        return SelectedExecutionRoute(
            exchange_name=exchange_name,
            client=feed,
            environment=environment,
            execution_context=self._provider_execution_contexts.get(exchange_name),
        )

    def _resolve_execution_identity_assurance_route(
        self,
        exchange_name: str,
    ) -> Any | None:
        """Issue provider-composition input from the active execution ledger.

        The mutable compatibility feed mapping is cross-checked against the
        exact feed originally installed by this BtApi instance. The execution
        session then issues the opaque ledger binding; neither a configured
        account alias nor ``get_execution_identity`` can create this route.
        """
        if type(exchange_name) is not str:
            return None
        session = self._execution_session
        if session is None:
            return None
        selected = self._selected_provider_route(exchange_name)
        if selected is None:
            return None
        feed, environment = selected
        with self._sdk_capability_lock:
            if self._selected_route_clients.get(exchange_name) is not feed:
                return None
        try:
            route = session.execution_identity_assurance_route(
                exchange_name,
                feed,
                environment,
            )
        except Exception:
            return None
        if route is None:
            return None
        # Recheck the currently selected feed after issuance. The provider
        # facade repeats this resolver before each assurance operation.
        with self._sdk_capability_lock:
            if self._selected_route_clients.get(exchange_name) is not feed:
                return None
        if self.exchange_feeds.get(exchange_name) is not feed:
            return None
        try:
            if session.validate_execution_identity_assurance_route(route) is not True:
                return None
        except Exception:
            return None
        return route

    def _resolve_market_session_route(
        self,
        exchange_name: str,
        instrument: Any,
    ) -> Any | None:
        """Bind a neutral market-session route to the already-selected feed."""
        from bt_api_base.market_protocol import MarketInstrumentKey
        from .market_session_composition import SelectedMarketSessionRoute

        if type(exchange_name) is not str or type(instrument) is not MarketInstrumentKey:
            return None
        selected = self._selected_provider_route(exchange_name)
        if selected is None:
            return None
        feed, environment = selected
        # A typed instrument key does not prove that the selected SDK session
        # is in the same verified environment. Keep resolver selection closed
        # until those independently selected facts agree.
        if environment is None or instrument.environment != environment:
            return None
        try:
            return SelectedMarketSessionRoute(
                exchange_name=exchange_name,
                client=feed,
                instrument=instrument,
                environment=environment,
            )
        except Exception:
            return None

    def _resolve_futures_expected_calendar_route(self, exchange_name: str) -> Any | None:
        """Bind expected-calendar providers to the exact selected SDK feed."""
        from bt_api_base.futures_expected_calendar_protocol import (
            SelectedFuturesExpectedCalendarRoute,
        )

        selected = self._selected_provider_route(exchange_name)
        if selected is None:
            return None
        feed, environment = selected
        try:
            return SelectedFuturesExpectedCalendarRoute(
                exchange_name=exchange_name,
                client=feed,
                environment=environment,
            )
        except Exception:
            return None

    def _account_capability_router(self) -> Any:
        """Return this API's router over the SDK-global exact-route registry."""
        with self._sdk_capability_lock:
            router = self._account_session_router
            if router is None:
                from .account_session_composition import create_account_session_router

                router = create_account_session_router(self._resolve_account_session_route)
                self._account_session_router = router
            return router

    def _direct_order_mode_current(self) -> bool:
        """Fence a direct port against later managed configuration or issuer replacement."""
        with self._sdk_capability_lock:
            return bool(
                self._direct_order_selection_started
                and not self._execution_configuration_in_progress
                and not self._managed_configuration_requested
                and self._execution_session is None
                and self._order_correlation_issuer is None
            )

    def _direct_order_capability_router(self) -> Any:
        """Return the per-API selector for trusted provider-owned direct routes."""
        with self._sdk_capability_lock:
            router = self._direct_order_provider_router
            if router is None:
                from .direct_correlated_execution import DirectOrderProviderRouter

                router = DirectOrderProviderRouter(
                    self._resolve_typed_order_route,
                    direct_mode_current=self._direct_order_mode_current,
                )
                self._direct_order_provider_router = router
            return router

    def _typed_order_capability_router(self) -> Any:
        """Return the per-API router shared by writes and same-provider capacity."""
        with self._sdk_capability_lock:
            router = self._typed_order_provider_router
            if router is None:
                from .correlated_execution import (
                    TypedOrderProviderRouter,
                    default_typed_order_provider_registry,
                )

                router = TypedOrderProviderRouter(
                    self._resolve_typed_order_route,
                    issuer=self._order_correlation_issuer,
                    registry=default_typed_order_provider_registry(),
                )
                self._typed_order_provider_router = router
            return router

    def _execution_economics_capability_router(self) -> Any:
        """Return this API's route-bound economics evidence selector."""
        with self._sdk_capability_lock:
            router = self._execution_economics_router
            if router is None:
                from .execution_economics_composition import (
                    ExecutionEconomicsRouteRouter,
                )

                owner_resolver = self._execution_economics_owner_resolver
                if owner_resolver is None:
                    from .execution_economics_composition import (
                        ExecutionEconomicsProviderRouter,
                        default_execution_economics_provider_registry,
                    )

                    provider_router = self._execution_economics_provider_router
                    if provider_router is None:
                        provider_router = ExecutionEconomicsProviderRouter(
                            default_execution_economics_provider_registry()
                        )
                        self._execution_economics_provider_router = provider_router
                    owner_resolver = provider_router.resolve_owner

                router = ExecutionEconomicsRouteRouter(
                    self._resolve_typed_order_route,
                    owner_resolver=owner_resolver,
                )
                self._execution_economics_router = router
            return router

    def _execution_identity_assurance_capability_router(self) -> Any:
        """Return this API's route-bound provider identity assurance router."""
        with self._sdk_capability_lock:
            router = self._execution_identity_assurance_router
            if router is None:
                from .execution_identity_assurance_composition import (
                    ExecutionIdentityAssuranceProviderRouter,
                    default_execution_identity_assurance_provider_registry,
                )

                # Identity assurance uses a stricter ledger-issued route than
                # generic execution capabilities; a feed or alias alone is not
                # sufficient to construct provider input.
                router = ExecutionIdentityAssuranceProviderRouter(
                    self._resolve_execution_identity_assurance_route,
                    registry=default_execution_identity_assurance_provider_registry(),
                )
                self._execution_identity_assurance_router = router
            return router

    def _market_session_capability_router(self) -> Any:
        """Return this API's exact-route market-session provider router."""
        with self._sdk_capability_lock:
            router = self._market_session_router
            if router is None:
                from .market_session_composition import MarketSessionRouter

                router = MarketSessionRouter(self._resolve_market_session_route)
                self._market_session_router = router
            return router

    def _futures_expected_calendar_capability_router(self) -> Any:
        """Return this API's router over exact expected-calendar providers."""
        with self._sdk_capability_lock:
            router = self._futures_expected_calendar_router
            if router is None:
                from .futures_expected_calendar_composition import (
                    FuturesExpectedCalendarProviderRouter,
                )

                router = FuturesExpectedCalendarProviderRouter(
                    self._resolve_futures_expected_calendar_route
                )
                self._futures_expected_calendar_router = router
            return router

    def _execution_rules_snapshot_capability_router(self) -> Any:
        """Return this API instance's exact-route rules snapshot selector."""
        with self._sdk_capability_lock:
            router = self._execution_rules_snapshot_router
            if router is None:
                from .execution_rules_composition import (
                    ExecutionRulesSnapshotProviderRouter,
                    default_execution_rules_snapshot_provider_registry,
                )

                router = ExecutionRulesSnapshotProviderRouter(
                    self._resolve_typed_order_route,
                    registry=default_execution_rules_snapshot_provider_registry(),
                )
                self._execution_rules_snapshot_router = router
            return router

    def _execution_rules_capability_router(self) -> Any:
        """Return this API instance's exact-route order-rules selector."""
        with self._sdk_capability_lock:
            router = self._execution_rules_router
            if router is None:
                from .execution_rule_evaluation_composition import (
                    OrderRulesProviderRouter,
                    default_order_rules_provider_registry,
                )

                router = OrderRulesProviderRouter(
                    self._resolve_typed_order_route,
                    registry=default_order_rules_provider_registry(),
                )
                self._execution_rules_router = router
            return router

    def _order_position_evidence_capability_router(self) -> Any:
        """Return this API instance's exact-route position-evidence selector."""
        with self._sdk_capability_lock:
            router = self._order_position_evidence_router
            if router is None:
                from .order_position_evidence_composition import (
                    OrderPositionEvidenceProviderRouter,
                    default_order_position_evidence_provider_registry,
                )

                # Reuse the exact selected execution route, including the
                # runtime-sealed opaque context. Resolving it also closes the
                # one-time late context-binding window on this route.
                router = OrderPositionEvidenceProviderRouter(
                    self._resolve_typed_order_route,
                    registry=default_order_position_evidence_provider_registry(),
                )
                self._order_position_evidence_router = router
            return router

    def _validate_required_environment(
        self, exchange_name: str, *, operation: str = "make_order"
    ) -> None:
        session = self._execution_session
        if session is None:
            return
        if session.config["market_data_only"]:
            return
        requirements = session.config["required_environments"]
        expected = requirements.get(exchange_name)
        if expected is None:
            policy = _provider_execution_configuration_policy(exchange_name, operation=operation)
            if not policy.require_environment:
                return
            raise NormalizedApiError(
                operation, "required_environment_missing", definite_reject=True
            )
        try:
            info = self.get_environment_info(exchange_name)
        except ExchangeNotFoundError:
            info = {"verified": False}
        if not info["verified"]:
            raise NormalizedApiError(
                operation, "required_environment_unverified", definite_reject=True
            )
        if info["environment"] != expected:
            raise NormalizedApiError(
                operation, "required_environment_mismatch", definite_reject=True
            )

    def _validate_required_environments(self) -> None:
        session = self._execution_session
        if session is None:
            return
        requirements = session.config["required_environments"]
        if not requirements:
            return
        for exchange_name in self.list_exchanges():
            self._validate_required_environment(exchange_name)

    def _normalized_call(
        self,
        operation: str,
        exchange_name: str,
        symbol: str | None,
        call: Callable[[], Any],
        *,
        request: Any = None,
        budget_capability: Any = None,
    ) -> Any:
        """Opt-in SDK result contract; never expose credentials in normalized errors."""
        from ._normalization import normalize_error, normalize_result

        def finalize_dispatch(context: Any) -> None:
            self._finalize_ctp_execution_dispatch(session, exchange_name, context)

        def invoke() -> Any:
            failure = None
            try:
                result = call()
                normalized = normalize_result(operation, result, exchange_name, symbol, request)
                if operation in {"query_order", "make_order", "cancel_order"}:
                    self._enrich_order_commission(exchange_name, normalized)
                return normalized
            except Exception as exc:
                failure = normalize_error(
                    exc,
                    operation,
                    exchange_name=exchange_name,
                    write=operation in _NORMALIZED_WRITE_OPERATIONS,
                )
            # Raise after leaving the except block so Python cannot retain the
            # credential-bearing transport exception in __context__.
            raise failure from None

        session = self._execution_session
        failure = None
        try:
            if session is not None and session.config["market_data_only"]:
                if operation in {
                    "make_order",
                    "cancel_order",
                    "query_order",
                    "set_position_mode",
                }:
                    raise NormalizedApiError(operation, "market_data_only", definite_reject=True)
                if operation in {"get_position", "get_open_orders", "get_deals"}:
                    return []
                if operation in {"get_account", "get_balance"}:
                    return {
                        "exchange_name": exchange_name,
                        "cash": 0.0,
                        "value": 0.0,
                        "currency": session.currency(exchange_name),
                    }
                if operation in {"get_position_mode", "get_account_config"}:
                    raise CapabilityNotSupportedError(operation, detail="market-data-only session")
            if operation in _NORMALIZED_WRITE_OPERATIONS:
                self._reject_simnow_profile_write(exchange_name, operation)
            self._validate_required_environment(exchange_name, operation=operation)
            if session is not None and operation in {
                "make_order",
                "cancel_order",
                "query_order",
            }:
                acquire_write = release_write = None
                if (
                    operation in {"make_order", "cancel_order"}
                    and str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() == "CTP"
                ):
                    acquire_write, release_write = self._ctp_write_ingress_lease(
                        exchange_name,
                        operation,
                    )
                try:
                    return session.invoke(
                        operation,
                        exchange_name,
                        request,
                        invoke,
                        preauthorize=acquire_write,
                        pre_dispatch=finalize_dispatch,
                        budget_capability=budget_capability,
                    )
                finally:
                    if release_write is not None:
                        release_write()
                    self._sync_ctp_gate_after_session_invoke(session, exchange_name, operation)
            result = invoke()
            if session is not None:
                if operation in {"get_account", "get_balance"} and isinstance(result, dict):
                    session.accounts[exchange_name] = dict(result)
                elif operation == "get_open_orders":
                    result = [
                        session.event(exchange_name, {**row, "kind": "order"}) for row in result
                    ]
            return result
        except Exception as exc:
            failure = normalize_error(
                exc,
                operation,
                exchange_name=exchange_name,
                write=operation in _NORMALIZED_WRITE_OPERATIONS,
            )
        # Do not wrap an already normalized failure a second time, and detach
        # exception chaining before it crosses the public API boundary.
        raise failure from None

    def _finalize_ctp_execution_dispatch(
        self,
        session: Any,
        exchange_name: str,
        context: Any,
    ) -> None:
        """Run the last SDK gate and require a sealed guard for every managed CTP write.

        Feed implementation details cannot weaken the SDK boundary: legacy and
        custom CTP feeds are subject to the same entry authorization check as
        the bundled native feed.  Recovery dispatch keeps its separate guard.
        """

        if (
            isinstance(context, dict)
            and str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() == "CTP"
        ):
            context["_native_ctp_entry_guard_required"] = True
        session.finalize_dispatch(context)

    async def _async_backend_call(
        self,
        operation: str,
        *args: Any,
        pre_dispatch: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Await a backend method and gate recovery at its actual handoff."""
        async_method = getattr(self._backend, f"async_{operation}", None)
        if callable(async_method):
            # DirectBackend's coroutine methods delegate to a feed coroutine
            # when one exists, but otherwise queue the synchronous backend
            # twin through ``asyncio.to_thread``.  Use our worker wrapper for
            # that fallback so the final recovery gate runs after the last
            # SDK-controlled queueing decision and immediately before the
            # lower call.  A genuine feed coroutine keeps its native async
            # path and receives the same gate at coroutine handoff.
            sync_fallback = self._async_sync_worker_method(operation, args)
            if sync_fallback is not None and pre_dispatch is not None:
                return await asyncio.to_thread(
                    self._run_async_backend_worker,
                    sync_fallback,
                    args,
                    kwargs,
                    pre_dispatch,
                )
            if inspect.iscoroutinefunction(async_method):
                if pre_dispatch is not None:
                    pre_dispatch()
                return await async_method(*args, **kwargs)
            result = await asyncio.to_thread(
                self._run_async_backend_worker,
                async_method,
                args,
                kwargs,
                pre_dispatch,
            )
            return await result if inspect.isawaitable(result) else result
        sync_method = getattr(self._backend, operation)
        return await asyncio.to_thread(
            self._run_async_backend_worker,
            sync_method,
            args,
            kwargs,
            pre_dispatch,
        )

    def _async_sync_worker_method(
        self, operation: str, args: tuple[Any, ...]
    ) -> Callable[..., Any] | None:
        """Find a DirectBackend sync twin when its async method will queue it."""

        backend_type = type(self._backend)
        if (
            backend_type.__name__ != "DirectBackend"
            or backend_type.__module__ != "bt_api_py._direct_backend"
            or operation not in {"make_order", "cancel_order"}
            or not args
        ):
            return None
        async_method = getattr(self._backend, f"async_{operation}", None)
        declared_async_method = getattr(backend_type, f"async_{operation}", None)
        if getattr(async_method, "__func__", async_method) is not declared_async_method:
            return None
        feed_getter = getattr(self._backend, "_feed", None)
        if not callable(feed_getter):
            return None
        try:
            feed = feed_getter(args[0])
        except Exception:
            return None
        feed_async_method = getattr(feed, f"async_{operation}", None)
        if callable(feed_async_method) and inspect.iscoroutinefunction(feed_async_method):
            return None
        sync_method = getattr(self._backend, operation, None)
        return sync_method if callable(sync_method) else None

    @staticmethod
    def _run_async_backend_worker(
        method: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: Mapping[str, Any],
        pre_dispatch: Callable[[], None] | None,
    ) -> Any:
        """Run the final recovery gate immediately before a worker call."""

        if pre_dispatch is not None:
            pre_dispatch()
        return method(*args, **kwargs)

    async def _async_normalized_call(
        self,
        operation: str,
        exchange_name: str,
        symbol: str,
        call: Any,
        *,
        request: Any,
        budget_capability: Any = None,
    ) -> Any:
        """Normalize an awaited result through the same execution-session owner."""
        from ._normalization import normalize_error, normalize_result

        session = self._execution_session
        handoff_context: dict[str, Any] = {}
        needs_managed_handoff = bool(
            session is not None
            and operation in {"make_order", "cancel_order"}
            and str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() == "CTP"
        )

        def final_handoff() -> None:
            context = handoff_context.get("context")
            if context is None or session is None:
                return
            # Keep the exact context object so the session can reject a worker
            # that outlives a cancelled async invocation instead of silently
            # allowing a stale thread to reach the lower transport.
            context["_async_handoff"] = True
            self._finalize_ctp_execution_dispatch(
                session,
                exchange_name,
                context,
            )

        def bind_handoff_context(context: Any) -> None:
            handoff_context["context"] = context

        async def invoke() -> Any:
            failure = None
            try:
                native = await call(final_handoff if needs_managed_handoff else None)
                normalized = normalize_result(
                    operation,
                    native,
                    exchange_name,
                    symbol,
                    request,
                )
                if operation in {"query_order", "make_order", "cancel_order"}:
                    await asyncio.to_thread(
                        self._enrich_order_commission,
                        exchange_name,
                        normalized,
                    )
                return normalized
            except Exception as exc:
                failure = normalize_error(
                    exc,
                    operation,
                    exchange_name=exchange_name,
                    write=operation in {"make_order", "cancel_order"},
                )
            raise failure from None

        if session is not None and session.config["market_data_only"]:
            raise NormalizedApiError(operation, "market_data_only", definite_reject=True)
        if operation in _NORMALIZED_WRITE_OPERATIONS:
            self._reject_simnow_profile_write(exchange_name, operation)
        self._validate_required_environment(exchange_name, operation=operation)
        if session is not None:
            acquire_write = release_write = None
            if (
                operation in {"make_order", "cancel_order"}
                and str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() == "CTP"
            ):
                acquire_write, release_write = self._ctp_write_ingress_lease(
                    exchange_name,
                    operation,
                )
            try:
                return await session.async_invoke(
                    operation,
                    exchange_name,
                    request,
                    invoke,
                    preauthorize=acquire_write,
                    pre_dispatch=lambda context: self._finalize_ctp_execution_dispatch(
                        session,
                        exchange_name,
                        context,
                    ),
                    budget_capability=budget_capability,
                    on_context=(bind_handoff_context if needs_managed_handoff else None),
                )
            finally:
                if release_write is not None:
                    release_write()
                self._sync_ctp_gate_after_session_invoke(session, exchange_name, operation)
        return await invoke()

    def _enrich_order_commission(self, exchange_name: str, order: dict[str, Any]) -> None:
        """Attach actual fees only when fills fully reconcile a terminal order.

        Fees remain denominated in commission_currency; consumers must compare
        that currency with the account currency before recording cash costs.
        An unavailable fee query does not erase a confirmed order state.
        """
        if (
            not order.get("terminal_confirmed")
            or not order.get("filled")
            or order.get("cumulative_commission") is not None
            or not order.get("order_id")
        ):
            return
        from .venue_normalization_policy import normalization_policy

        try:
            policy = normalization_policy(exchange_name)
        except Exception:
            return
        if getattr(policy, "reconcile_terminal_order_commission", False) is not True:
            return
        from .order_fee_projection import enrich_order_commission

        enrich_order_commission(exchange_name, order, get_deals=self.get_deals)

    def poll_execution_maintenance(self, exchange_name: str) -> list[dict[str, Any]]:
        """Drive due order queries and return normalized order updates.

        This is the execution-only maintenance path for callers whose market
        feed blocks the ordinary event loop.  It does not consume market data
        and never places, retries or cancels an order.
        """
        session = self._execution_session
        if session is None:
            return []
        if session.closed:
            raise NormalizedApiError("poll_execution_maintenance", "execution_session_closed")
        session.poll_due(self, exchange_name)
        updates: list[dict[str, Any]] = []
        pending = session.pending[exchange_name]
        while pending:
            updates.append(pending.popleft())
        return updates

    def poll_event(self, exchange_name: str) -> dict[str, Any] | None:
        """Poll normalized events and advance configured pending-order reconciliation.

        Reconciliation is time based and runs even while market events keep
        arriving. No placement is retried. Call this without requiring a bar.
        """
        session = self._execution_session
        if session is None:
            event = self._poll_event_raw(exchange_name)
            if event is not None:
                self._event_metrics["delivered_events"] += 1
            return event
        if session.closed:
            raise NormalizedApiError("poll_event", "execution_session_closed")
        session.poll_due(self, exchange_name)
        if session.pending[exchange_name]:
            self._event_metrics["delivered_events"] += 1
            return session.pending[exchange_name].popleft()
        event = self._poll_event_raw(exchange_name)
        event = session.event(exchange_name, event) if event is not None else None
        if event is not None:
            self._event_metrics["delivered_events"] += 1
        return event

    def poll_events(
        self,
        exchange_name: str,
        *,
        max_raw_items: int | None = 100,
        coalesce_market_snapshots: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        """Poll a finite batch of normalized events from one exchange.

        ``poll_event(exchange_name)`` keeps its historical one-event FIFO
        contract. This batch API is an opt-in path for latency-sensitive
        consumers which may explicitly coalesce complete market snapshots.

        ``max_raw_items`` counts items removed from the exchange transport, not
        normalized events produced by a container. ``None`` means the finite
        ``Queue.qsize()`` snapshot observed at the start of a direct-transport
        call; items appended by producers during the call remain for the next
        poll. Transports without an inspectable queue use the conservative
        default batch bound of 100 when ``None`` is requested.

        Only explicitly named complete snapshot kinds are coalesced. Currently
        ``orderbook`` and ``tick`` are supported. Coalescing is per kind and
        symbol, and only within a contiguous market-data segment. Every other
        event is a barrier, so order, trade, account and position events are
        neither discarded nor reordered. Callers must not opt incremental
        order-book deltas into snapshot coalescing.
        """
        if max_raw_items is not None and (
            isinstance(max_raw_items, bool) or not isinstance(max_raw_items, int)
        ):
            raise ValueError("max_raw_items must be a non-negative integer or None")
        if max_raw_items is not None and max_raw_items < 0:
            raise ValueError("max_raw_items must be a non-negative integer or None")
        if isinstance(coalesce_market_snapshots, str):
            raise ValueError("coalesce_market_snapshots must be an iterable of event kinds")
        try:
            snapshot_kinds = frozenset(
                str(kind).strip().lower() for kind in coalesce_market_snapshots
            )
        except TypeError as exc:
            raise ValueError(
                "coalesce_market_snapshots must be an iterable of event kinds"
            ) from exc
        unsupported = snapshot_kinds - {"orderbook", "tick"}
        if unsupported or "" in snapshot_kinds:
            raise ValueError(
                "coalesce_market_snapshots supports only complete orderbook and tick snapshots"
            )

        session = self._execution_session
        if session is not None:
            if session.closed:
                raise NormalizedApiError("poll_events", "execution_session_closed")
            session.poll_due(self, exchange_name)

        if max_raw_items is None:
            if self.transport_mode is TransportMode.DIRECT:
                source = self.data_queues.get(exchange_name)
                if source is None:
                    raise CapabilityNotSupportedError(
                        "poll_events", detail="exchange has no event queue"
                    )
                raw_budget = [source.qsize()]
            else:
                raw_budget = [100]
        else:
            raw_budget = [max_raw_items]

        events: list[dict[str, Any]] = []

        def drain_session_pending() -> None:
            if session is None:
                return
            pending = session.pending[exchange_name]
            while pending:
                events.append(pending.popleft())

        drain_session_pending()
        normalized_pending = self._normalized_event_pending[exchange_name]
        while raw_budget[0] > 0 or normalized_pending:
            before_budget = raw_budget[0]
            before_pending = len(normalized_pending)
            event = self._poll_event_raw(exchange_name, source_budget=raw_budget)
            if event is not None:
                if session is not None:
                    event = session.event(exchange_name, event)
                if event is not None:
                    events.append(event)
                # Persistence failure deliberately publishes an uncertainty
                # update before the real fill queued by the execution session.
                drain_session_pending()
                continue
            if raw_budget[0] == before_budget and len(normalized_pending) == before_pending:
                break

        if snapshot_kinds:
            result = self._coalesce_market_snapshot_events(events, snapshot_kinds)
            self._event_metrics["coalesced_events"] += len(events) - len(result)
        else:
            result = events
        self._event_metrics["delivered_events"] += len(result)
        return result

    @staticmethod
    def _coalesce_market_snapshot_events(
        events: list[dict[str, Any]], snapshot_kinds: frozenset[str]
    ) -> list[dict[str, Any]]:
        """Keep the newest snapshot per stream without crossing event barriers."""
        result: list[dict[str, Any]] = []
        latest: dict[tuple[Any, ...], tuple[int, dict[str, Any], int]] = {}

        def flush() -> None:
            result.extend(
                {
                    **event,
                    "coalesced_count": int(event.get("coalesced_count") or 0) + dropped,
                }
                for _, event, dropped in sorted(latest.values(), key=lambda item: item[0])
            )
            latest.clear()

        for index, event in enumerate(events):
            kind = str(event.get("kind") or "").lower()
            symbol = event.get("symbol")
            complete_snapshot = not (
                kind == "orderbook"
                and (
                    event.get("snapshot_or_delta") == "delta"
                    or event.get("continuity_status") in {"gap", "out_of_order", "checksum_failed"}
                )
            )
            if kind in snapshot_kinds and symbol not in (None, "") and complete_snapshot:
                key = (
                    kind,
                    event.get("exchange_name") or event.get("exchange"),
                    event.get("asset_type"),
                    symbol,
                )
                previous = latest.get(key)
                dropped = (
                    0
                    if previous is None
                    else previous[2] + 1 + int(previous[1].get("coalesced_count") or 0)
                )
                latest[key] = (index, event, dropped)
                continue
            flush()
            result.append(event)
        flush()
        return result

    def _ctp_market_stream_ingress_queue(
        self,
        exchange_name: str,
        target: queue.Queue[Any],
    ) -> _CtpMarketIngressQueue:
        """Return the private stream producer queue for one CTP market lane."""

        queues = getattr(self, "_ctp_market_ingress_queues", None)
        if not isinstance(queues, dict):
            queues = {}
            self._ctp_market_ingress_queues = queues
        seal = getattr(self, "_ctp_market_ingress_seal", None)
        if seal is None:
            seal = object()
            self._ctp_market_ingress_seal = seal
        current = queues.get(exchange_name)
        if isinstance(current, _CtpMarketIngressQueue) and current.target is target:
            return current
        current = _CtpMarketIngressQueue(
            target,
            lambda item: self._seal_ctp_market_ingress(item, seal),
        )
        queues[exchange_name] = current
        return current

    def _ctp_market_consumer_queue(
        self,
        exchange_name: str,
        target: queue.Queue[Any],
    ) -> _CtpMarketConsumerQueue:
        """Return the public consumer-only view for a managed CTP queue."""

        queues = getattr(self, "_ctp_market_consumer_queues", None)
        if not isinstance(queues, dict):
            queues = {}
            self._ctp_market_consumer_queues = queues
        seal = getattr(self, "_ctp_market_ingress_seal", None)
        if seal is None:
            seal = object()
            self._ctp_market_ingress_seal = seal
        current = queues.get(exchange_name)
        if isinstance(current, _CtpMarketConsumerQueue) and current.target is target:
            return current
        current = _CtpMarketConsumerQueue(target, seal)
        queues[exchange_name] = current
        return current

    @staticmethod
    def _seal_ctp_market_ingress(
        item: Any,
        seal: object,
    ) -> _CtpMarketIngressEnvelope:
        """Freeze a native quote at the managed producer boundary when possible."""

        row = None
        if _is_native_ctp_quote_v2_ticker(item):
            try:
                from ._normalization import _dict

                row = deepcopy(_dict(item))
            except (AttributeError, TypeError, ValueError):
                row = None
        return _CtpMarketIngressEnvelope(seal, item, row)

    def _consume_ctp_market_ingress(
        self,
        exchange_name: str,
        item: Any,
    ) -> tuple[Any, dict[str, Any] | None] | None:
        """Unwrap one managed stream item; invalid/replayed envelopes are dropped."""

        if not _is_ctp_exchange(exchange_name):
            return item, None
        if type(item) is not _CtpMarketIngressEnvelope:
            return item, None
        seal = getattr(self, "_ctp_market_ingress_seal", None)
        if seal is None:
            return None
        return item.consume(seal)

    def _ctp_quote_v2_parent_attestation(
        self,
        exchange_name: str,
        item: Any,
        *,
        cohort_now_monotonic_ns: int,
        cohort_now_epoch: float,
        sealed_row: Mapping[str, Any] | None,
    ) -> Any | None:
        """Bind a native CTP quote to parent-owned direct-ingress evidence.

        Raw mappings and adapter transport objects are deliberately excluded:
        their public fields are still normalized, but they cannot manufacture
        the opaque proof required for an execution-eligible V2 quote.
        """

        if (
            self.transport_mode is not TransportMode.DIRECT
            or not _is_ctp_exchange(exchange_name)
            or not isinstance(sealed_row, Mapping)
        ):
            return None
        data_queues = getattr(self, "data_queues", None)
        if not isinstance(data_queues, Mapping):
            return None
        data_queue = data_queues.get(exchange_name)
        if data_queue is None:
            return None
        market_queues = getattr(self, "_ctp_market_ingress_queues", None)
        if not isinstance(market_queues, Mapping):
            return None
        market_queue = market_queues.get(exchange_name)
        if (
            not isinstance(market_queue, _CtpMarketIngressQueue)
            or market_queue.target is not data_queue
        ):
            return None
        from ._ctp_market_attestation import _issue_ctp_quote_v2_parent_attestation

        try:
            from bt_api_ctp.quote_v2_provenance import quote_v2_attestation_fields
        except ImportError:
            return None
        fields = quote_v2_attestation_fields(
            exchange_name,
            item,
            sealed_row=sealed_row,
            feed=getattr(self, "exchange_feeds", {}).get(exchange_name),
            streams=getattr(self, "_subscription_streams", None),
            market_queue=market_queue,
            cohort_now_monotonic_ns=cohort_now_monotonic_ns,
            cohort_now_epoch=cohort_now_epoch,
        )
        if fields is None:
            return None
        return _issue_ctp_quote_v2_parent_attestation(**fields)

    def _poll_event_raw(
        self, exchange_name: str, *, source_budget: list[int] | None = None
    ) -> dict[str, Any] | None:
        """Return one normalized SDK event, preserving mixed queue arrival order.

        Event timestamps are Unix seconds. Quantity remains in native venue
        units. CTP executions arrive as distinct trade events, never as an
        invented average price in an order report.
        """
        from ._normalization import normalize_error, normalize_event

        pending = self._normalized_event_pending[exchange_name]
        failure = None
        try:
            for _ in range(100):
                from_ingress = False
                if pending:
                    item = pending.popleft()
                elif self.transport_mode is TransportMode.ZMQ:
                    if source_budget is not None and source_budget[0] <= 0:
                        return None
                    item = self._backend.poll_event(exchange_name)
                    if item is None:
                        return None
                    from_ingress = True
                    if source_budget is not None:
                        source_budget[0] -= 1
                else:
                    source = self.data_queues.get(exchange_name)
                    if source is None:
                        raise CapabilityNotSupportedError(
                            "poll_event", detail="exchange has no event queue"
                        )
                    if source_budget is not None and source_budget[0] <= 0:
                        return None
                    try:
                        item = source.get_nowait()
                    except queue.Empty:
                        return None
                    from_ingress = True
                    if source_budget is not None:
                        source_budget[0] -= 1
                if from_ingress:
                    self._event_metrics["raw_ingress_items"] += 1
                ingress_item = self._consume_ctp_market_ingress(exchange_name, item)
                if ingress_item is None:
                    continue
                item, sealed_row = ingress_item
                getter = getattr(item, "get_data", None)
                if callable(getter):
                    values = getter()
                    pending.extend(values if isinstance(values, (tuple, list)) else [values])
                    continue
                cohort_now_monotonic_ns = time.monotonic_ns()
                cohort_now_epoch = time.time()
                event = normalize_event(
                    sealed_row if sealed_row is not None else item,
                    exchange_name,
                    _ctp_quote_v2_parent_attestation=self._ctp_quote_v2_parent_attestation(
                        exchange_name,
                        item,
                        cohort_now_monotonic_ns=cohort_now_monotonic_ns,
                        cohort_now_epoch=cohort_now_epoch,
                        sealed_row=sealed_row,
                    ),
                )
                if event is not None:
                    self._event_metrics["normalized_events"] += 1
                    return event
            return None
        except Exception as exc:
            failure = normalize_error(exc, "poll_event", exchange_name=exchange_name)
        raise failure from None

    def _build_backend(
        self,
        transport_mode: TransportMode,
        forwarding_config: ForwardingConfig | None,
    ) -> Any:
        if transport_mode is not TransportMode.DIRECT:
            # Retired entry point: fail closed before any socket/thread is
            # created.  Keep the typed error so callers can distinguish the
            # retirement from ordinary validation errors.
            raise LegacyForwardingRetiredError(
                "transport_mode=ZMQ",
                detail=(
                    "the legacy ZMQ forwarding backend was retired; use TransportMode.DIRECT "
                    "for ordinary direct/backtest calls or Backtrader "
                    "ServiceExecutionSession for managed service writes"
                ),
            )
        if forwarding_config is not None:
            raise LegacyForwardingRetiredError(
                "forwarding_config",
                detail=(
                    "legacy forwarding configuration is not accepted with any transport; "
                    "remove it for ordinary direct/backtest use. Managed service operations "
                    "must use SDK ServiceClientRuntime bindings or "
                    "Backtrader ServiceExecutionSession."
                ),
            )
        from ._direct_backend import DirectBackend

        return DirectBackend(
            self._get_feed,
            self.exchange_feeds,
        )

    def _configure_ctp_execution_gate(
        self,
        exchange_name: str,
        feed: Any,
    ) -> dict[str, Any]:
        """Install the SDK-owned native guard before a managed feed is exposed."""
        operation = "configure_execution"
        capability = getattr(self, "_ctp_execution_capability", None)
        # A real CTP feed owns the native authority type.  Do not install the
        # historic bare ``object()`` into it: direct callers could reproduce
        # that value pattern through the public feed surface.  Test doubles
        # intentionally omit this private issuer and remain confined to the
        # parent contract suite.
        issuer = getattr(feed, "_issue_execution_capability_for_core", None)
        if callable(issuer):
            try:
                capability = issuer()
            except Exception:
                raise NormalizedApiError(
                    operation,
                    "ctp_execution_gate_authority_unavailable",
                    definite_reject=True,
                ) from None
            self._ctp_execution_capability = capability
        method = getattr(feed, "configure_execution_gate", None)
        state_reader = getattr(feed, "get_execution_gate_state", None)
        if capability is None or not callable(method) or not callable(state_reader):
            raise NormalizedApiError(
                operation,
                "ctp_execution_gate_unavailable",
                definite_reject=True,
            )
        try:
            configured = method(capability)
            state = state_reader()
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_execution_gate_configuration_failed",
                definite_reject=True,
            ) from None
        if (
            not isinstance(configured, Mapping)
            or not isinstance(state, Mapping)
            or configured.get("managed") is not True
            or state.get("managed") is not True
            or configured.get("armed") is not False
            or state.get("armed") is not False
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_gate_not_closed",
                definite_reject=True,
            )
        return dict(state)

    def _sole_ctp_execution_venue(self, operation: str) -> tuple[Any, str]:
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(operation, "execution_session_required", definite_reject=True)
        if self.transport_mode is not TransportMode.DIRECT:
            raise CapabilityNotSupportedError(
                operation,
                detail="CTP execution authorization requires direct transport",
                definite_reject=True,
            )
        exchange_names = tuple(self.list_exchanges())
        if (
            len(exchange_names) != 1
            or str(exchange_names[0]).partition(DATANAME_SEPARATOR)[0].upper() != "CTP"
            or set(session.exchange_names) != {exchange_names[0]}
        ):
            raise NormalizedApiError(operation, "single_ctp_session_required", definite_reject=True)
        return session, exchange_names[0]

    def _reject_simnow_native_execution(
        self,
        environment_profile: Any,
        operation: str,
        *,
        schema_version: str | None = None,
    ) -> None:
        """Keep all known SimNow profiles outside generic native-write routes."""
        if str(
            environment_profile or ""
        ).strip() in _SIMNOW_RESTRICTED_PROFILES or schema_version in {
            SIMNOW_APPROVAL_SCHEMA_VERSION,
            SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION,
            SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION,
        }:
            raise NormalizedApiError(
                operation,
                "ctp_simnow_execution_not_admitted",
                definite_reject=True,
            )

    def _reject_simnow_profile_write(self, exchange_name: str, operation: str) -> None:
        """Check cached CTP profile state before a generic write can dispatch."""
        if str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() != "CTP":
            return
        exchange_feeds = getattr(self, "exchange_feeds", None)
        feed = exchange_feeds.get(exchange_name) if isinstance(exchange_feeds, Mapping) else None
        # Iteration 62 (D62-01): once bt_api_py write admission granted writes
        # for this route, the decision is environment-neutral; the SimNow
        # profile restriction below only guards writes that bypass admission.
        admitted = getattr(feed, "sdk_write_admission_active", None)
        if callable(admitted) and admitted() is True:
            return
        state_reader = getattr(feed, "get_session_state", None)
        if not callable(state_reader):
            return
        state = state_reader()
        profile = state.get("environment_profile") if isinstance(state, Mapping) else None
        self._reject_simnow_native_execution(profile, operation)

    def init_exchange(self, exchange_kwargs: dict[str, Any]) -> None:
        """根据 exchange_kwargs 初始化并添加交易所。

        Args:
            exchange_kwargs: {exchange_name: params} 格式的配置。
        """
        for exchange_name in exchange_kwargs:
            exchange_params = exchange_kwargs[exchange_name]
            self.add_exchange(exchange_name, exchange_params)

    def init_logger(self) -> _LoggerProxy:
        """Initialize and return the API logger instance."""
        return get_logger("api", print_info=bool(self.debug))

    def log(self, txt: str, level: str = "info") -> None:
        if level in ("info", "warning", "error", "debug"):
            getattr(self.logger, level)(txt)
        else:
            self.logger.warning(f"Unknown log level '{level}', message: {txt}")

    def _parse_dataname(self, dataname: str) -> tuple[str, str, str]:
        if not isinstance(dataname, str) or not dataname:
            raise SubscribeError("", detail="dataname must be a non-empty string")
        parts = dataname.split(DATANAME_SEPARATOR)
        if len(parts) != 3 or not all(parts):
            raise SubscribeError("", detail=f"invalid dataname format: {dataname}")
        return parts[0], parts[1], parts[2]

    def _validate_order_args(
        self,
        exchange_name: str,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
    ) -> str:
        if volume <= 0:
            raise InvalidOrderError(exchange_name, symbol, "volume must be > 0")
        if price < 0:
            raise InvalidOrderError(exchange_name, symbol, "price must be >= 0")
        if not isinstance(order_type, str) or not order_type:
            raise InvalidOrderError(exchange_name, symbol, "order_type must be a non-empty string")

        normalized_order_type = order_type.lower()
        if normalized_order_type not in {"limit", "market"}:
            raise InvalidOrderError(
                exchange_name,
                symbol,
                "order_type must be one of: limit, market",
            )
        if normalized_order_type == "limit" and price <= 0:
            raise InvalidOrderError(exchange_name, symbol, "price must be > 0 for limit order")
        return normalized_order_type

    @staticmethod
    def _copy_exchange_params(exchange_params: dict[str, Any] | None) -> dict[str, Any]:
        if exchange_params is None:
            return {}
        try:
            return deepcopy(dict(exchange_params))
        except (TypeError, ValueError) as exc:
            raise TypeError("exchange_params must be a mapping") from exc

    @staticmethod
    def _normalize_subscribe_topics(
        topics: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        if not isinstance(topics, list):
            raise SubscribeError("", detail="topics must be a list of dict items")

        normalized_topics: list[dict[str, Any]] = []
        subscribe_bar_num = 0
        for index, topic in enumerate(topics):
            if not isinstance(topic, dict):
                raise SubscribeError("", detail=f"invalid topic at index {index}: expected dict")
            topic_name = topic.get("topic")
            if not isinstance(topic_name, str) or not topic_name:
                raise SubscribeError(
                    "",
                    detail=f"invalid topic at index {index}: missing non-empty 'topic'",
                )
            normalized_topics.append(deepcopy(dict(topic)))
            if topic_name == "kline":
                subscribe_bar_num += 1
        return normalized_topics, subscribe_bar_num

    def add_exchange(self, exchange_name: str, exchange_params: dict[str, Any]) -> None:
        """Add a new exchange to the API instance.

        Args:
            exchange_name: Exchange identifier (e.g., "BINANCE___SPOT", "OKX___SWAP")
            exchange_params: Exchange-specific parameters (api_key, secret, etc.)

        Example:
            >>> api = BtApi()
            >>> api.add_exchange("BINANCE___SPOT", {
            ...     "api_key": "your_key",
            ...     "secret": "your_secret",
            ...     "testnet": True
            ... })
        """
        if self.transport_mode is TransportMode.ZMQ:
            added = exchange_name not in self.exchange_kwargs
            self.exchange_kwargs.setdefault(
                exchange_name, self._copy_exchange_params(exchange_params)
            )
            try:
                self._validate_required_environment(exchange_name)
            except Exception:
                if added:
                    self.exchange_kwargs.pop(exchange_name, None)
                raise
            return
        if exchange_name not in self.exchange_feeds:
            if exchange_name in self.data_queues:
                raise ExchangeNotFoundError(
                    exchange_name,
                    "data_queue exists but feed does not — inconsistent state",
                )
            stored_exchange_params = self._copy_exchange_params(exchange_params)
            _require_ctp_read_only_settings(
                exchange_name,
                stored_exchange_params,
                operation="add_exchange",
            )
            session = self._execution_session
            credential_fingerprints: dict[str, str] = (
                _execution_credential_fingerprints(
                    {exchange_name: stored_exchange_params},
                    session.config,
                    self.transport_mode,
                )
                if session is not None
                else {}
            )
            data_queue: queue.Queue[Any] = queue.Queue()
            self.data_queues[exchange_name] = data_queue
            self.exchange_kwargs[exchange_name] = stored_exchange_params
            self.log(f"adding exchange: {exchange_name}")
            feed = None
            try:
                producer_queue: Any = data_queue
                if (
                    session is not None
                    and str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() == "CTP"
                ):
                    # Install the producer fence before the request feed can cache
                    # the queue handle. Consumers retain the raw target above.
                    producer_queue = self._ctp_private_stream_queue(
                        exchange_name,
                        data_queue,
                    )
                feed = ExchangeRegistry.create_feed(
                    exchange_name, producer_queue, **stored_exchange_params
                )
                selected_stream_configuration = getattr(
                    feed, "get_verified_stream_configuration", None
                )
                if callable(selected_stream_configuration):
                    selected = selected_stream_configuration(
                        MappingProxyType(dict(stored_exchange_params))
                    )
                    if not isinstance(selected, Mapping):
                        raise TypeError("verified stream configuration must be a mapping")
                    stored_exchange_params.update(selected)
                if session is not None and _is_ctp_exchange(exchange_name):
                    self._configure_ctp_execution_gate(exchange_name, feed)
                self.exchange_feeds[exchange_name] = feed
                self._validate_required_environment(exchange_name)
                fingerprint = credential_fingerprints.get(exchange_name)
                if fingerprint is not None and session is not None:
                    session.bind_credential_identity(exchange_name, fingerprint)
                    lineage_ref = _execution_credential_lineage_ref(feed)
                    if lineage_ref is not None:
                        session.bind_execution_credential_lineage(
                            exchange_name,
                            feed,
                            lineage_ref,
                        )
                with self._sdk_capability_lock:
                    selected_clients = dict(self._selected_route_clients)
                    selected_clients[exchange_name] = feed
                    self._selected_route_clients = MappingProxyType(selected_clients)
            except Exception:
                registered_feed = self.exchange_feeds.pop(exchange_name, None)
                feed = registered_feed if registered_feed is not None else feed
                if feed is not None:
                    with suppress(Exception):
                        feed.disconnect()
                self.data_queues.pop(exchange_name, None)
                self.exchange_kwargs.pop(exchange_name, None)
                raise
        else:
            self.log(f"exchange_name: {exchange_name} already exists")

    def get_request_api(self, exchange_name: str) -> Any:
        """Get the REST Feed instance for the specified exchange (synchronous API).

        ZMQ transport has no direct feed escape hatch and raises
        ``CapabilityNotSupportedError`` instead of returning ``None``.
        """
        if self._execution_session is not None:
            raise CapabilityNotSupportedError(
                "get_request_api",
                detail="execution session does not expose unmanaged writes",
            )
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                "get_request_api",
                detail="ZMQ transport has no direct feed escape hatch",
            )
        api = self.exchange_feeds.get(exchange_name)
        if api is None:
            self.log(f"exchange_name: {exchange_name} does not exist", level="error")
        return api

    def _ctp_execution_runtime_identity(self) -> dict[str, str]:
        """Return reproducible source-manifest and native-runtime identities."""
        operation = "arm_execution_from_preflight"
        try:
            import bt_api_ctp

            diagnostics_getter = getattr(bt_api_ctp, "get_ctp_native_diagnostics", None)
            if not callable(diagnostics_getter):
                raise ValueError("native diagnostics unavailable")
            diagnostics = dict(diagnostics_getter())
            native_path = Path(str(diagnostics.get("loaded_module_path") or "")).resolve()
            reported_native_sha256 = str(diagnostics.get("loaded_module_sha256") or "").lower()
            package_path = Path(str(getattr(bt_api_ctp, "__file__", "") or "")).resolve()
            if (
                diagnostics.get("native_loaded") is not True
                or not native_path.is_file()
                or not package_path.is_file()
            ):
                raise ValueError("loaded CTP runtime identity unavailable")
            native_sha256 = hashlib.sha256(native_path.read_bytes()).hexdigest()
            if not reported_native_sha256 or reported_native_sha256 != native_sha256:
                raise ValueError("loaded CTP native hash is inconsistent")
            package_root = package_path.parent
            package_files = sorted(
                (
                    path
                    for path in package_root.rglob("*.py")
                    if "__pycache__" not in path.parts and path.is_file()
                ),
                key=lambda path: path.relative_to(package_root).as_posix(),
            )
            required = {
                "__init__.py",
                "ctp/client.py",
                "feeds/live_ctp_feed.py",
                "gateway/adapter.py",
            }
            relative_paths = {path.relative_to(package_root).as_posix() for path in package_files}
            if not package_files or not required <= relative_paths:
                raise ValueError("CTP package manifest is incomplete")
            package_manifest = [
                {
                    "path": path.relative_to(package_root).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in package_files
            ]
            package_manifest_json = json.dumps(
                package_manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            package_sha256 = hashlib.sha256(package_manifest_json.encode("utf-8")).hexdigest()
            if (
                diagnostics.get("ctp_package_manifest") != package_manifest
                or str(diagnostics.get("ctp_package_sha256") or "").lower() != package_sha256
            ):
                raise ValueError("reported CTP package manifest is inconsistent")
            return {
                "native_sha256": native_sha256,
                "ctp_package_sha256": package_sha256,
            }
        except Exception:
            raise NormalizedApiError(
                operation, "ctp_runtime_identity_unavailable", definite_reject=True
            ) from None

    def _ctp_execution_runtime_python_identity(self) -> dict[str, str]:
        """Collect the Python package and dependency identities locally."""

        try:
            return {
                "backtrader_sha256": _package_source_manifest_digest("backtrader"),
                "bt_api_py_sha256": _package_source_manifest_digest("bt_api_py"),
                "bt_api_base_sha256": _package_source_manifest_digest("bt_api_base"),
                "dependency_hashes_sha256": _dependency_source_manifest_digest(),
            }
        except Exception:
            raise NormalizedApiError(
                "build_ctp_execution_approval_context",
                "ctp_runtime_identity_unavailable",
                definite_reject=True,
            ) from None

    def _ctp_verified_execution_environment(
        self,
        exchange_name: str,
        *,
        operation: str,
    ) -> dict[str, Any]:
        """Require a provider-verified CTP environment even while disarmed.

        ``_validate_required_environment`` intentionally skips a
        ``market_data_only`` session for ordinary read queries.  An arm or
        settlement authorization is different: it must not use that shortcut
        to promote a custom/unknown front profile after a read-only preflight.
        """

        session = self._execution_session
        feed = self.exchange_feeds.get(exchange_name)
        reader = getattr(feed, "get_environment_info", None)
        if session is None or not callable(reader):
            raise NormalizedApiError(operation, "ctp_environment_unverified", definite_reject=True)
        try:
            environment = reader()
        except Exception:
            environment = None
        expected = session.config["required_environments"].get(exchange_name)
        profile = (
            str(environment.get("profile") or "").strip()
            if isinstance(environment, Mapping)
            else ""
        )
        if (
            not isinstance(environment, Mapping)
            or environment.get("verified") is not True
            or environment.get("environment") != "demo"
            or expected != "demo"
            or not profile
        ):
            raise NormalizedApiError(operation, "ctp_environment_unverified", definite_reject=True)
        return {
            "environment": "demo",
            "profile": profile,
            "verified": True,
        }

    def _ctp_execution_authorization_epoch_value(self) -> int:
        """Return the facade-local preflight epoch for opaque arm grants."""

        value = getattr(self, "_ctp_execution_authorization_epoch", 0)
        return value if type(value) is int and value >= 0 else 0

    def _invalidate_ctp_execution_authorizations(self) -> int:
        """Make every unconsumed facade arm grant unusable immediately."""

        value = self._ctp_execution_authorization_epoch_value() + 1
        self._ctp_execution_authorization_epoch = value
        return value

    def _ctp_execution_arm_context(
        self,
        exchange_name: str,
    ) -> dict[str, Any]:
        """Read the non-network state that fences a CTP execution arm."""
        operation = "arm_execution_from_preflight"
        try:
            runtime_identity = self._ctp_execution_runtime_identity()
            environment = self._ctp_verified_execution_environment(
                exchange_name, operation=operation
            )
            state = self.get_ctp_session_state(exchange_name)
            generation = state.get("connection_generation")
            if type(generation) is not int or generation <= 0:
                raise ValueError
            if (
                state.get("connected") is not True
                or state.get("read_only_ready") is not True
                or state.get("trading_ready") is not True
                or state.get("auto_settlement_confirm") is not False
                or str(state.get("auth_state") or "").strip().lower()
                not in {"authenticated", "success", "logged_in"}
                or str(state.get("login_state") or "").strip().lower() not in {"logged_in", "ready"}
                or str(state.get("settlement_state") or "").strip().lower()
                not in {"confirmed", "ready"}
                or state.get("settlement_readback_verified") is not True
                or bool(state.get("last_error"))
            ):
                raise ValueError
            account_fingerprint = _canonical_ctp_account_fingerprint(
                state.get("account_fingerprint")
            )
            trading_day = str(state.get("trading_day") or "").strip()
            environment_profile = str(state.get("environment_profile") or "").strip()
            if (
                not account_fingerprint
                or not trading_day
                or not environment_profile
                or environment_profile != environment["profile"]
            ):
                raise ValueError
            return {
                "account_fingerprint": account_fingerprint,
                "trading_day": trading_day,
                "connection_generation": generation,
                "environment_profile": environment_profile,
                "native_sha256": runtime_identity["native_sha256"],
                "ctp_package_sha256": runtime_identity["ctp_package_sha256"],
                "account_stream_ready": self._ctp_execution_stream_ready(exchange_name),
            }
        except NormalizedApiError:
            raise
        except Exception:
            raise NormalizedApiError(
                operation, "ctp_session_not_trading_ready", definite_reject=True
            ) from None

    def _ctp_settlement_context(
        self,
        exchange_name: str,
        *,
        operation: str,
    ) -> dict[str, Any]:
        """Read the disarmed, provider-verified context for settlement only.

        Unlike an order arm, a settlement confirmation is allowed only before
        settlement readback promotes ``trading_ready``.  It still binds the
        one-shot grant to the live account/day/generation/runtime/profile and
        rejects the read-only shortcut used by ordinary market-data calls.
        """

        try:
            runtime_identity = self._ctp_execution_runtime_identity()
            environment = self._ctp_verified_execution_environment(
                exchange_name, operation=operation
            )
            state = self.get_ctp_session_state(exchange_name)
            generation = state.get("connection_generation")
            if type(generation) is not int or generation <= 0:
                raise ValueError
            if (
                state.get("connected") is not True
                or state.get("read_only_ready") is not True
                or state.get("auto_settlement_confirm") is not False
                or str(state.get("auth_state") or "").strip().lower()
                not in {"authenticated", "success", "logged_in"}
                or str(state.get("login_state") or "").strip().lower() not in {"logged_in", "ready"}
                or bool(state.get("last_error"))
            ):
                raise ValueError
            account_fingerprint = _canonical_ctp_account_fingerprint(
                state.get("account_fingerprint")
            )
            trading_day = str(state.get("trading_day") or "").strip()
            environment_profile = str(state.get("environment_profile") or "").strip()
            if (
                not account_fingerprint
                or not trading_day
                or not environment_profile
                or environment_profile != environment["profile"]
            ):
                raise ValueError
            return {
                "account_fingerprint": account_fingerprint,
                "trading_day": trading_day,
                "connection_generation": generation,
                "environment_profile": environment_profile,
                "native_sha256": runtime_identity["native_sha256"],
                "ctp_package_sha256": runtime_identity["ctp_package_sha256"],
            }
        except NormalizedApiError:
            raise
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_session_not_read_only_ready",
                definite_reject=True,
            ) from None

    def _ctp_execution_stream(self, exchange_name: str) -> Any:
        from bt_api_ctp.trade_stream_readiness import select_ctp_trade_stream

        return select_ctp_trade_stream(getattr(self, "_subscription_streams", None))

    def _ctp_execution_stream_ready(self, exchange_name: str) -> bool:
        flags = getattr(self, "_subscription_flags", None)
        if not isinstance(flags, dict) or flags.get(f"{exchange_name}_account") is not True:
            return False
        stream = self._ctp_execution_stream(exchange_name)
        from bt_api_ctp.trade_stream_readiness import is_ctp_trade_stream_ready

        return is_ctp_trade_stream_ready(
            stream, resolve_feed=lambda: self.exchange_feeds.get(exchange_name)
        )

    def _ctp_execution_gate_state(
        self,
        exchange_name: str,
        *,
        operation: str,
    ) -> dict[str, Any]:
        feed = self.exchange_feeds.get(exchange_name)
        reader = getattr(feed, "get_execution_gate_state", None)
        if not callable(reader):
            raise NormalizedApiError(
                operation,
                "ctp_execution_gate_unavailable",
                definite_reject=True,
            )
        try:
            state = reader()
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_execution_gate_state_unavailable",
                definite_reject=True,
            ) from None
        if not isinstance(state, Mapping) or state.get("managed") is not True:
            raise NormalizedApiError(
                operation,
                "ctp_execution_gate_not_managed",
                definite_reject=True,
            )
        return dict(state)

    def _arm_ctp_execution_gate(
        self,
        exchange_name: str,
        proof: Mapping[str, Any],
        authorization: _CtpExecutionArmAuthorization,
        *,
        operation: str,
    ) -> dict[str, Any]:
        from ._execution_session import (
            _canonical_ctp_execution_instrument,
            _execution_arm_instruments,
            _execution_arm_proof,
            _is_execution_arm_bundle,
        )

        normalized, proof_sha256 = _execution_arm_proof(proof)
        self._reject_simnow_native_execution(normalized.get("environment_profile"), operation)
        feed = self.exchange_feeds.get(exchange_name)
        capability = getattr(self, "_ctp_execution_capability", None)
        method = getattr(feed, "arm_execution_gate", None)
        if capability is None or not callable(method):
            raise NormalizedApiError(
                operation,
                "ctp_execution_gate_unavailable",
                definite_reject=True,
            )
        try:
            method(capability, authorization._native_authorization)
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_execution_gate_arm_failed",
                definite_reject=True,
            ) from None
        state = self._ctp_execution_gate_state(
            exchange_name,
            operation=operation,
        )
        bundle_scope_matches = True
        if _is_execution_arm_bundle(normalized):
            bundle_scope_matches = (
                state.get("scope_version") == normalized["scope_version"]
                and isinstance(state.get("authorized_instruments"), (list, tuple))
                and tuple(state["authorized_instruments"]) == _execution_arm_instruments(normalized)
            )
        if (
            state.get("armed") is not True
            or state.get("connection_generation") != normalized["connection_generation"]
            or state.get("trading_day") != normalized["trading_day"]
            or _canonical_ctp_execution_instrument(normalized, state.get("instrument"))
            != normalized["instrument"]
            or state.get("proof_sha256") != proof_sha256
            or state.get("environment_profile") != normalized["environment_profile"]
            or not bundle_scope_matches
        ):
            with suppress(Exception):
                self._disarm_ctp_execution_gate(
                    exchange_name,
                    "ctp_execution_gate_arm_state_mismatch",
                    operation=operation,
                )
            raise NormalizedApiError(
                operation,
                "ctp_execution_gate_arm_state_mismatch",
                definite_reject=True,
            )
        return state

    def _disarm_ctp_execution_gate(
        self,
        exchange_name: str,
        reason: str,
        *,
        operation: str,
    ) -> dict[str, Any]:
        # Every attempted revocation invalidates unconsumed facade grants.  A
        # failed native disarm is an even stronger reason to require a fresh
        # preflight, because the gate's final state cannot be trusted.
        self._invalidate_ctp_execution_authorizations()
        feed = self.exchange_feeds.get(exchange_name)
        capability = getattr(self, "_ctp_execution_capability", None)
        method = getattr(feed, "disarm_execution_gate", None)
        if capability is None or not callable(method):
            raise NormalizedApiError(
                operation,
                "ctp_execution_gate_unavailable",
                definite_reject=True,
            )
        try:
            method(capability, reason)
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_execution_gate_disarm_failed",
                definite_reject=True,
            ) from None
        state = self._ctp_execution_gate_state(
            exchange_name,
            operation=operation,
        )
        if state.get("armed") is not False:
            raise NormalizedApiError(
                operation,
                "ctp_execution_gate_disarm_failed",
                definite_reject=True,
            )
        return state

    def _reset_ctp_execution_stream(self, exchange_name: str) -> None:
        """Stop every CTP account stream and leave the request feed read-only."""
        # Stream teardown is a terminal transition for the associated
        # preflight. This also covers callers that must reset after an
        # unproven native disarm instead of reaching the normal helper above.
        self._invalidate_ctp_execution_authorizations()
        streams = getattr(self, "_subscription_streams", None)
        flags = getattr(self, "_subscription_flags", None)
        if isinstance(streams, list):
            for stream in tuple(streams):
                if str(getattr(stream, "stream_name", "")) != "ctp_trade_stream":
                    continue
                streams.remove(stream)
                with suppress(Exception):
                    stream.stop()
        if isinstance(flags, dict):
            flags.pop(f"{exchange_name}_account", None)
        exchange_params = self.exchange_kwargs.get(exchange_name)
        if isinstance(exchange_params, dict):
            exchange_params["subscribe_account"] = False

    def _sync_ctp_gate_after_session_invoke(
        self, session: Any, exchange_name: str, operation: str
    ) -> None:
        """Close the native gate before returning a session-level revocation."""
        if str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() != "CTP":
            return
        lock = getattr(self, "_ctp_execution_transition_lock", None)
        if lock is None:
            with _CTP_TRANSITION_LOCK_INIT:
                lock = getattr(self, "_ctp_execution_transition_lock", None)
                if lock is None:
                    lock = threading.RLock()
                    self._ctp_execution_transition_lock = lock
        with lock:
            with session.mutex:
                should_disarm = bool(
                    session._arm_managed
                    and session.config["market_data_only"]
                    and session._arm_venue == exchange_name
                )
                reason = session._arm_revoked_reason or "execution_recovery_action_requires_refresh"
            if not should_disarm:
                return
            try:
                self._disarm_ctp_execution_gate(
                    exchange_name,
                    reason,
                    operation=operation,
                )
                if not self._ctp_execution_stream_ready(exchange_name):
                    self._prepare_ctp_execution_stream(
                        exchange_name,
                        operation=operation,
                    )
            except Exception:
                # An unproven native disarm cannot safely share its trader with
                # a continuing account stream. Tear it down only on this path.
                self._reset_ctp_execution_stream(exchange_name)
                raise

    def _prepare_ctp_execution_stream(
        self,
        exchange_name: str,
        *,
        operation: str = "arm_execution_from_preflight",
    ) -> dict[str, Any]:
        """Attach the account stream to the already connected request feed."""
        exchange_params = self.exchange_kwargs.get(exchange_name)
        if not isinstance(exchange_params, dict):
            raise NormalizedApiError(
                operation, "ctp_account_stream_unavailable", definite_reject=True
            )
        previous_subscribe_account = exchange_params.get("subscribe_account")
        exchange_params["subscribe_account"] = True
        token = {
            "stream": None,
            "created": False,
            "previous_subscribe_account": previous_subscribe_account,
        }
        streams = getattr(self, "_subscription_streams", None)
        flag = f"{exchange_name}_account"
        flags = getattr(self, "_subscription_flags", None)
        if not isinstance(streams, list) or not isinstance(flags, dict):
            self._rollback_ctp_execution_stream(exchange_name, token)
            raise NormalizedApiError(
                operation, "ctp_account_stream_unavailable", definite_reject=True
            )
        data_queue = self.data_queues.get(exchange_name)
        if data_queue is None:
            self._rollback_ctp_execution_stream(exchange_name, token)
            raise NormalizedApiError(
                operation, "ctp_account_stream_unavailable", definite_reject=True
            )
        producer_queue = self._ctp_private_stream_queue(exchange_name, data_queue)
        if flags.get(flag, False):
            account_stream = self._ctp_execution_stream(exchange_name)
            waiter = getattr(account_stream, "wait_connected", None)
            if (
                account_stream is not None
                and getattr(account_stream, "data_queue", None) is producer_queue
                and callable(waiter)
            ):
                try:
                    if waiter(timeout=5.0) is True and self._ctp_execution_stream_ready(
                        exchange_name
                    ):
                        return token
                except Exception:
                    self.log(
                        "CTP account stream readiness probe failed; recreating stream",
                        level="warning",
                    )
            if account_stream is not None and account_stream in streams:
                streams.remove(account_stream)
                with suppress(Exception):
                    account_stream.stop()
            flags.pop(flag, None)
        else:
            # A failed earlier start may have appended a stream without its flag.
            stale = [
                stream
                for stream in streams
                if str(getattr(stream, "stream_name", "")) == "ctp_trade_stream"
            ]
            for stream in stale:
                streams.remove(stream)
                with suppress(Exception):
                    stream.stop()
        stream_class = ExchangeRegistry.get_stream_class(exchange_name, "account")
        feed = self.exchange_feeds.get(exchange_name)
        if stream_class is None or feed is None:
            self._rollback_ctp_execution_stream(exchange_name, token)
            raise NormalizedApiError(
                operation, "ctp_account_stream_unavailable", definite_reject=True
            )
        stream = None
        try:
            stream_kwargs = self._copy_exchange_params(exchange_params)
            stream_kwargs.update(
                stream_name="ctp_trade_stream",
                request_feed=feed,
            )
            stream = stream_class(producer_queue, **stream_kwargs)
            token["stream"] = stream
            token["created"] = True
            stream.start()
            waiter = getattr(stream, "wait_connected", None)
            if not callable(waiter) or waiter(timeout=5.0) is not True:
                raise RuntimeError("CTP account stream did not become ready")
            streams.append(stream)
            flags[flag] = True
            if not self._ctp_execution_stream_ready(exchange_name):
                raise RuntimeError("CTP account stream is not authenticated")
            return token
        except Exception:
            if stream is not None:
                with suppress(Exception):
                    stream.stop()
            self._rollback_ctp_execution_stream(exchange_name, token)
            raise NormalizedApiError(
                operation, "ctp_account_stream_unavailable", definite_reject=True
            ) from None

    def _rollback_ctp_execution_stream(
        self,
        exchange_name: str,
        token: Mapping[str, Any],
    ) -> None:
        stream = token.get("stream")
        streams = getattr(self, "_subscription_streams", None)
        if stream is not None and token.get("created") is True:
            if isinstance(streams, list) and stream in streams:
                streams.remove(stream)
            with suppress(Exception):
                stream.stop()
            getattr(self, "_subscription_flags", {}).pop(f"{exchange_name}_account", None)
        exchange_params = self.exchange_kwargs.get(exchange_name)
        if isinstance(exchange_params, dict):
            previous = token.get("previous_subscribe_account")
            if previous is None:
                exchange_params.pop("subscribe_account", None)
            else:
                exchange_params["subscribe_account"] = previous

    def build_ctp_execution_approval_context(
        self,
        context: Mapping[str, Any],
        *,
        exchange_name: str = "CTP___FUTURE",
        configuration: Any = None,
        strategy_source: Any = None,
        preflight: Any = None,
        evidence: Any = None,
        source: str = "sdk_runtime",
        deployment_manifest: Mapping[str, Any] | None = None,
        credential_binding_verifier: Any = None,
        credential_binding_provider: Any = None,
    ) -> CtpExecutionApprovalContext:
        """Collect a sealed approval context from this running deployment.

        Runtime package/native hashes, the CTP account/day/generation and the
        resolved environment are read here; callers cannot establish those
        identities by copying strings into a mapping.  Application material
        is supplied as raw bytes, paths, or JSON values and hashed by the SDK.
        ``deployment_manifest`` can pin the locally observed runtime hashes,
        but it cannot replace them. Official SimNow set1 contexts also require
        an SDK-sealed ``credential_binding_verifier`` created from the reviewed
        deployment adapter. Bare callbacks and mappings are rejected. The
        verifier is refreshed with the current native session, front and
        artifact scope before approval transitions. This remains a nominal
        package-provenance boundary, not an in-process sandbox.
        """

        operation = "build_ctp_execution_approval_context"
        from ._ctp_credential_binding import _is_verifier, _new_scope

        if credential_binding_provider is not None:
            if credential_binding_verifier is not None or not _is_verifier(
                credential_binding_provider, owner=self
            ):
                raise NormalizedApiError(
                    operation, "ctp_credential_binding_trust_required", definite_reject=True
                )
            credential_binding_verifier = credential_binding_provider
        if credential_binding_verifier is not None and not _is_verifier(
            credential_binding_verifier, owner=self
        ):
            raise NormalizedApiError(
                operation, "ctp_credential_binding_trust_required", definite_reject=True
            )
        if source not in {"sdk_runtime", "deployment_manifest"}:
            raise NormalizedApiError(
                operation, "ctp_approval_context_untrusted", definite_reject=True
            )
        if not isinstance(context, Mapping) or type(context) is not dict:
            raise NormalizedApiError(
                operation, "ctp_approval_context_incomplete", definite_reject=True
            )
        context_seed = deepcopy(dict(context))
        unknown = set(context) - _CONTEXT_FIELDS
        required = _CONTEXT_FIELDS - {
            "source",
            "context_source",
            "account_fingerprint",
            "trading_day",
            "connection_generation",
            "environment_profile",
            "backtrader_sha256",
            "bt_api_py_sha256",
            "bt_api_ctp_sha256",
            "bt_api_base_sha256",
            "native_sha256",
            "dependency_hashes_sha256",
            "strategy_identity_sha256",
            "configuration_sha256",
            "preflight_sha256",
            "evidence_sha256",
        }
        if unknown or not required <= set(context):
            raise NormalizedApiError(
                operation, "ctp_approval_context_incomplete", definite_reject=True
            )
        provider = str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper()
        if provider != "CTP" or getattr(self, "transport_mode", None) is not TransportMode.DIRECT:
            raise NormalizedApiError(
                operation, "ctp_runtime_identity_unavailable", definite_reject=True
            )
        try:
            runtime_identity = self._ctp_execution_runtime_identity()
            python_identity = self._ctp_execution_runtime_python_identity()
            state = self.get_ctp_session_state(exchange_name)
            environment = self.get_environment_info(exchange_name)
            if not isinstance(state, Mapping) or not isinstance(environment, Mapping):
                raise ValueError("runtime_state_unavailable")
            if environment.get("verified") is not True:
                raise ValueError("environment_unverified")
            account_fingerprint = _canonical_ctp_account_fingerprint(
                state.get("account_fingerprint")
            )
            trading_day = str(state.get("trading_day") or "").strip()
            generation = state.get("connection_generation")
            environment_profile = str(state.get("environment_profile") or "").strip()
            feed = self.exchange_feeds.get(exchange_name)
            environment_reader = getattr(feed, "get_environment_info", None)
            feed_environment = environment_reader() if callable(environment_reader) else None
            if (
                not account_fingerprint
                or not re.fullmatch(r"[0-9]{8}", trading_day)
                or type(generation) is not int
                or generation <= 0
                or not environment_profile
                or not isinstance(feed_environment, Mapping)
                or feed_environment.get("verified") is not True
                or str(feed_environment.get("profile") or "").strip() != environment_profile
            ):
                raise ValueError("runtime_state_incomplete")
        except NormalizedApiError:
            raise
        except Exception:
            raise NormalizedApiError(
                operation, "ctp_runtime_identity_unavailable", definite_reject=True
            ) from None
        actual_runtime = {
            "native_sha256": runtime_identity.get("native_sha256"),
            "bt_api_ctp_sha256": runtime_identity.get("ctp_package_sha256"),
            **python_identity,
        }
        if any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in actual_runtime.values()
        ):
            raise NormalizedApiError(
                operation, "ctp_runtime_identity_unavailable", definite_reject=True
            )
        if deployment_manifest is not None and (
            not isinstance(deployment_manifest, Mapping)
            or type(deployment_manifest) is not dict
            or set(deployment_manifest) != {"runtime_hashes"}
            or not isinstance(deployment_manifest["runtime_hashes"], dict)
            or set(deployment_manifest["runtime_hashes"]) != set(actual_runtime)
            or deployment_manifest["runtime_hashes"] != actual_runtime
        ):
            raise NormalizedApiError(
                operation,
                "ctp_approval_runtime_manifest_mismatch",
                definite_reject=True,
            )
        values = dict(context)
        values.update(actual_runtime)
        values.update(
            {
                "source": source,
                "context_source": source,
                "account_fingerprint": account_fingerprint,
                "trading_day": trading_day,
                "connection_generation": generation,
                "environment_profile": environment_profile,
            }
        )
        try:
            values.update(
                {
                    "configuration_sha256": _approval_material_digest(configuration),
                    "strategy_identity_sha256": _approval_material_digest(strategy_source),
                    "preflight_sha256": _approval_material_digest(preflight),
                    "evidence_sha256": _approval_material_digest(evidence),
                }
            )
            if environment_profile in _SIMNOW_BOUND_PROFILES:
                if not _is_verifier(credential_binding_verifier, owner=self):
                    raise NormalizedApiError(
                        operation, "ctp_credential_binding_required", definite_reject=True
                    )
                front_scope = self._ctp_credential_binding_fronts(
                    exchange_name,
                    feed,
                    state,
                    credential_binding_verifier,
                    operation=operation,
                )
                binding_scope = _new_scope(
                    {
                        "account_fingerprint": account_fingerprint,
                        "account_fingerprint_sha256": front_scope["account_fingerprint_sha256"],
                        "trading_day": trading_day,
                        "connection_generation": generation,
                        "environment_profile": environment_profile,
                        "td_front": front_scope["td_front"],
                        "md_front": front_scope["md_front"],
                        "td_front_sha256": hashlib.sha256(
                            front_scope["td_front"].encode("utf-8", "strict")
                        ).hexdigest(),
                        "md_front_sha256": hashlib.sha256(
                            front_scope["md_front"].encode("utf-8", "strict")
                        ).hexdigest(),
                        "backtrader_sha256": values["backtrader_sha256"],
                        "backtrader_runtime_sha256": credential_binding_verifier.package_sha256,
                        "bt_api_py_sha256": values["bt_api_py_sha256"],
                        "bt_api_ctp_sha256": values["bt_api_ctp_sha256"],
                        "bt_api_base_sha256": values["bt_api_base_sha256"],
                        "native_sha256": values["native_sha256"],
                        "dependency_hashes_sha256": values["dependency_hashes_sha256"],
                        "configuration_sha256": values["configuration_sha256"],
                        "strategy_identity_sha256": values["strategy_identity_sha256"],
                        "preflight_sha256": values["preflight_sha256"],
                        "evidence_sha256": values["evidence_sha256"],
                        "md_connection_generation": front_scope["md_connection_generation"],
                        "md_stream_generation": front_scope["md_stream_generation"],
                    }
                )
                values.update(
                    credential_binding_verifier.refresh(
                        binding_scope, owner=self, operation=operation
                    )
                )
            elif (
                credential_binding_verifier is not None
                or environment_profile in _SIMNOW_RESTRICTED_PROFILES
            ):
                raise NormalizedApiError(
                    operation,
                    "ctp_credential_binding_scope_unsupported",
                    definite_reject=True,
                )
            normalized = _normalize_context(_new_runtime_context(values))
        except NormalizedApiError as exc:
            raise NormalizedApiError(operation, exc.code, definite_reject=True) from None
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_approval_context_material_unavailable",
                definite_reject=True,
            ) from None

        def refresh_context() -> CtpExecutionApprovalContext:
            return self.build_ctp_execution_approval_context(
                context_seed,
                exchange_name=exchange_name,
                configuration=configuration,
                strategy_source=strategy_source,
                preflight=preflight,
                evidence=evidence,
                source=source,
                deployment_manifest=deployment_manifest,
                credential_binding_verifier=credential_binding_verifier,
            )

        return _new_runtime_context(
            normalized,
            owner=self,
            refresh=refresh_context,
        )

    def verify_ctp_execution_approval(
        self,
        artifact: bytes | str,
        *,
        trust_root: Mapping[str, Any] | None,
        context: Mapping[str, Any] | CtpExecutionApprovalContext,
    ) -> CtpExecutionApproval:
        """Verify an independent CTP approval without arming a native gate.

        ``trust_root`` is deployment-owned key and revocation configuration;
        ``context`` must be the sealed SDK-collected runtime/deployment-manifest
        identity for non-synthetic sources, including the exact instrument
        scope and all artifact hashes.  A plain mapping is reserved for
        explicit ``synthetic_test`` offline evidence.  The method performs no
        network I/O and never invokes the controlled test issuer or a native
        CTP write path.
        """

        return verify_ctp_execution_approval(
            artifact,
            trust_root=trust_root,
            context=context,
        )

    def verify_ctp_execution_recovery_approval(
        self,
        artifact: bytes | str,
        *,
        trust_root: Mapping[str, Any] | None,
        context: Mapping[str, Any] | CtpExecutionApprovalContext,
    ) -> CtpExecutionApproval:
        """Verify only the versioned recovery-purpose approval contract."""

        approval = self.verify_ctp_execution_approval(
            artifact,
            trust_root=trust_root,
            context=context,
        )
        if approval.purpose != RECOVERY_APPROVAL_PURPOSE:
            raise NormalizedApiError(
                "verify_ctp_execution_recovery_approval",
                "ctp_recovery_purpose_required",
                definite_reject=True,
            )
        return approval

    def _revalidate_ctp_execution_approval(
        self,
        approval: CtpExecutionApproval,
        *,
        trust_root: Mapping[str, Any] | None,
        context: Mapping[str, Any] | CtpExecutionApprovalContext,
    ) -> CtpExecutionApproval:
        return revalidate_ctp_execution_approval(
            approval,
            trust_root=trust_root,
            context=context,
        )

    def _refresh_ctp_execution_approval_context(
        self,
        context: Mapping[str, Any] | CtpExecutionApprovalContext,
    ) -> Mapping[str, Any] | CtpExecutionApprovalContext:
        if type(context) is CtpExecutionApprovalContext:
            return _refresh_runtime_context(context, self)
        return context

    def create_ctp_credential_binding_verifier(self, adapter: object) -> Any:
        """Seal one reviewed runtime adapter for this BtApi owner."""

        operation = "create_ctp_credential_binding_verifier"
        if self.transport_mode is not TransportMode.DIRECT:
            raise NormalizedApiError(
                operation, "ctp_credential_binding_trust_required", definite_reject=True
            )
        from ._ctp_credential_binding import _new_reviewed_verifier

        return _new_reviewed_verifier(self, adapter)

    def _create_ctp_credential_binding_verifier_for_test(
        self, provider: object, *, authority: object
    ) -> Any:
        """Create an owner-bound credential binding verifier for fake tests only."""

        operation = "create_ctp_credential_binding_verifier"
        if not _is_ctp_controlled_test_authority(authority):
            raise NormalizedApiError(
                operation, "ctp_credential_binding_trust_required", definite_reject=True
            )
        from ._ctp_credential_binding import _new_test_verifier

        return _new_test_verifier(self, provider)

    def _ctp_credential_binding_fronts(
        self,
        exchange_name: str,
        feed: Any,
        state: Mapping[str, Any],
        verifier: Any,
        *,
        operation: str,
    ) -> dict[str, Any]:
        """Bind the provider's native validation to the SDK-owned live state."""
        from bt_api_ctp.credential_binding_fronts import resolve_credential_binding_fronts

        return resolve_credential_binding_fronts(
            exchange_name,
            feed,
            state,
            verifier,
            operation=operation,
            exchange_feeds=lambda: getattr(self, "exchange_feeds", {}),
            market_ingress_for=lambda route: getattr(
                self, "_ctp_market_ingress_queues", {}
            ).get(route),
            subscription_streams=lambda: getattr(self, "_subscription_streams", None),
            error_type=NormalizedApiError,
        )

    @staticmethod
    def _canonical_ctp_approval_context_value(value: Any) -> str:
        """Canonicalize one sealed context field for an exact comparison."""

        return json.dumps(
            _jsonable(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def _validate_ctp_recovery_material(
        self,
        approval_capability: CtpExecutionApprovalCapability,
        *,
        operation: str,
    ) -> None:
        """Recollect SDK-owned application material bound by a recovery approval.

        Synthetic mapping contexts remain available to offline compatibility
        tests.  A recovery capability produced from a sealed runtime context
        retains that collector and must observe exactly the same signed
        configuration, strategy, preflight, evidence, and runtime bindings at
        every native/write boundary.
        """

        context = getattr(approval_capability, "_context", None)
        if type(context) is not CtpExecutionApprovalContext:
            return
        try:
            refreshed = _refresh_runtime_context(context, self)
            values = refreshed.as_dict()
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_material_unavailable",
                definite_reject=True,
            ) from None
        for field_name, expected in approval_capability.bindings.items():
            if field_name == "source" or field_name not in values:
                continue
            try:
                matches = self._canonical_ctp_approval_context_value(
                    expected
                ) == self._canonical_ctp_approval_context_value(values[field_name])
            except (TypeError, ValueError, UnicodeEncodeError):
                matches = False
            if not matches:
                raise NormalizedApiError(
                    operation,
                    "ctp_execution_authorization_material_mismatch",
                    definite_reject=True,
                )

    def _ctp_entry_approval_proof(
        self,
        approval_capability: CtpExecutionApprovalCapability,
    ) -> dict[str, Any]:
        """Build the exact native arm proof from one redeemed entry approval."""

        payload = approval_capability._approval.payload
        primary = payload["primary_instrument"]
        authorized = [
            f"{item['exchange_id']}.{item['instrument_id']}"
            for item in payload["authorized_instruments"]
        ]
        return {
            "account_fingerprint": payload["account_fingerprint"],
            "trading_day": payload["trading_day"],
            "instrument": f"{primary['exchange_id']}.{primary['instrument_id']}",
            "connection_generation": payload["connection_generation"],
            "environment_profile": payload["environment_profile"],
            "receipt_sha256": payload["receipt_sha256"],
            "native_sha256": payload["native_sha256"],
            "ctp_package_sha256": payload["ctp_package_sha256"],
            "source_hashes_sha256": payload["source_hashes_sha256"],
            "dependency_hashes_sha256": payload["dependency_hashes_sha256"],
            "preflight_sha256": payload["preflight_sha256"],
            "scope_version": _CTP_EXECUTION_RECOVERY_BUNDLE_SCOPE_VERSION,
            "authorized_instruments": authorized,
        }

    def _validate_active_ctp_entry_authorization(
        self,
        guard: object,
        *,
        operation: str,
        **_write_context: Any,
    ) -> None:
        """Revalidate one ordinary entry approval at every managed write edge."""

        if not _is_ctp_entry_write_guard(guard):
            raise NormalizedApiError(
                operation,
                "ctp_entry_authorization_guard_invalid",
                definite_reject=True,
            )
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        if (
            guard._owner is not self
            or guard._session is not session
            or session._entry_write_guard is not guard
        ):
            raise NormalizedApiError(
                operation,
                "ctp_entry_authorization_guard_invalid",
                definite_reject=True,
            )
        capability = guard._capability
        if (
            not _is_ctp_execution_approval_capability(capability)
            or capability._owner is not self
            or capability._entry_used is not True
            or capability.purpose != APPROVAL_PURPOSE
            or capability.schema_version != ENTRY_APPROVAL_SCHEMA_VERSION
            or capability._context is not guard._context
            or capability._context is None
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_required",
                definite_reject=True,
            )
        self._reject_simnow_native_execution(
            capability.bindings.get("environment_profile"),
            operation,
            schema_version=capability.schema_version,
        )

        approval = capability._approval
        try:
            payload_sha256 = hashlib.sha256(
                json.dumps(
                    _jsonable(approval.payload),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            signature_sha256 = hashlib.sha256(approval.signature).hexdigest()
            frozen_trust_root = _thaw(capability._trust_root)
            trust_root_sha256 = hashlib.sha256(
                json.dumps(
                    _jsonable(frozen_trust_root),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_material_unavailable",
                definite_reject=True,
            ) from None
        if (
            payload_sha256 != approval.payload_sha256
            or payload_sha256 != guard._approval_payload_sha256
            or signature_sha256 != guard._approval_signature_sha256
            or approval.trust_root_sha256 != guard._trust_root_sha256
            or trust_root_sha256 != guard._trust_root_sha256
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_material_mismatch",
                definite_reject=True,
            )

        context = capability._context
        if type(context) is not CtpExecutionApprovalContext:
            raise NormalizedApiError(
                operation,
                "ctp_approval_context_untrusted",
                definite_reject=True,
            )
        try:
            refreshed_context = _refresh_runtime_context(context, self)
            current_approval = self._revalidate_ctp_execution_approval(
                approval,
                trust_root=frozen_trust_root,
                context=refreshed_context,
            )
        except NormalizedApiError:
            raise
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_material_unavailable",
                definite_reject=True,
            ) from None
        if (
            current_approval.payload_sha256 != guard._approval_payload_sha256
            or hashlib.sha256(current_approval.signature).hexdigest()
            != guard._approval_signature_sha256
            or current_approval.trust_root_sha256 != guard._trust_root_sha256
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_material_mismatch",
                definite_reject=True,
            )

        now = datetime.now(UTC)
        snapshot = approval.revocation_snapshot
        snapshot_expires = snapshot.get("expires_at") if isinstance(snapshot, Mapping) else None
        if not isinstance(snapshot_expires, str):
            raise NormalizedApiError(
                operation,
                "ctp_approval_revocation_snapshot_stale",
                definite_reject=True,
            )
        try:
            if now >= datetime.fromisoformat(snapshot_expires[:-1] + "+00:00"):
                raise NormalizedApiError(
                    operation,
                    "ctp_approval_revocation_snapshot_stale",
                    definite_reject=True,
                )
        except ValueError:
            raise NormalizedApiError(
                operation,
                "ctp_approval_revocation_snapshot_stale",
                definite_reject=True,
            ) from None
        try:
            snapshot_sha256 = hashlib.sha256(
                json.dumps(
                    _jsonable(snapshot),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_approval_revocation_snapshot_stale",
                definite_reject=True,
            ) from None
        if (
            approval.approval_id in session._ctp_approval_revoked_ids
            or approval.nonce in session._ctp_approval_revoked_nonces
            or approval.revocation_snapshot_version
            < session._ctp_approval_revocation_snapshot_version
            or session._ctp_approval_revocation_snapshot_version
            != approval.revocation_snapshot_version
            or session._ctp_approval_revocation_snapshot_sha256 != snapshot_sha256
        ):
            raise NormalizedApiError(
                operation,
                "ctp_approval_revoked",
                definite_reject=True,
            )

        from ._execution_session import _execution_arm_proof

        proof, proof_sha256 = _execution_arm_proof(self._ctp_entry_approval_proof(capability))
        if (
            proof_sha256 != guard._proof_sha256
            or not isinstance(session._arm_proof, Mapping)
            or dict(session._arm_proof) != proof
            or session._arm_proof_sha256 != proof_sha256
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_context_mismatch",
                definite_reject=True,
            )
        current_context = self._ctp_execution_arm_context(exchange_name)
        error = session._arm_context_error(proof, current_context, require_account_stream=True)
        if error is not None:
            raise NormalizedApiError(operation, error, definite_reject=True)
        gate = self._ctp_execution_gate_state(exchange_name, operation=operation)
        if (
            gate.get("managed") is not True
            or gate.get("armed") is not True
            or gate.get("connection_generation") != proof["connection_generation"]
            or gate.get("trading_day") != proof["trading_day"]
            or gate.get("instrument") != proof["instrument"]
            or gate.get("scope_version") != proof["scope_version"]
            or tuple(gate.get("authorized_instruments") or ())
            != tuple(proof["authorized_instruments"])
            or gate.get("environment_profile") != proof["environment_profile"]
            or gate.get("proof_sha256") != proof_sha256
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_gate_state_mismatch",
                definite_reject=True,
            )

    def _latch_ctp_recovery_failure(self, session: Any, error: BaseException) -> None:
        """Fence the current generation after a recovery authority failure."""

        reason = str(getattr(error, "code", "") or type(error).__name__)
        generation = None
        proof = getattr(session, "_arm_proof", None)
        if isinstance(proof, Mapping):
            generation = proof.get("connection_generation")
        with suppress(Exception):
            session._revoke_arm(reason, generation=generation)
        with suppress(Exception):
            session.pause_recovery()
        self._invalidate_ctp_execution_authorizations()

    def _bind_ctp_approval_identity(
        self,
        context: Mapping[str, Any] | CtpExecutionApprovalContext,
        *,
        exchange_name: str = "CTP___FUTURE",
    ) -> None:
        if type(context) is not CtpExecutionApprovalContext:
            return
        values = context.as_dict()
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "ctp_execution_approval",
                "execution_session_required",
                definite_reject=True,
            )
        session.bind_ctp_approval_identity(
            exchange_name,
            account_fingerprint=values.get("account_fingerprint"),
            environment_profile=values.get("environment_profile"),
        )

    def _run_ctp_approval_transition(
        self,
        approval: CtpExecutionApproval,
        *,
        trust_root: Mapping[str, Any] | None,
        context: Mapping[str, Any] | CtpExecutionApprovalContext,
        transition: Callable[..., Any],
    ) -> tuple[CtpExecutionApproval, Any]:
        """Run one approval transition under the session's commit fence.

        The initial verification is only a cheap admission check.  The
        session mutex is acquired before the runtime context is recollected;
        the existing account registry and journal leases are then acquired by
        ``_bind_ctp_approval_identity`` in their established order.  The
        transition guard recollects and revalidates immediately after that
        lease boundary and again after the durable journal event, so a
        session/material change observed during either wait cannot produce an
        opaque success result.
        """

        session = self._execution_session
        if session is None:
            raise NormalizedApiError(
                "ctp_execution_approval",
                "execution_session_required",
                definite_reject=True,
            )
        with session.mutex:
            current_context = self._refresh_ctp_execution_approval_context(context)
            current_approval = self._revalidate_ctp_execution_approval(
                approval,
                trust_root=trust_root,
                context=current_context,
            )
            self._bind_ctp_approval_identity(current_context)

            def transition_guard(stage: str, record: Any) -> Any:
                nonlocal current_context, current_approval
                current_context = self._refresh_ctp_execution_approval_context(context)
                current_approval = self._revalidate_ctp_execution_approval(
                    current_approval,
                    trust_root=trust_root,
                    context=current_context,
                )
                if stage == "post_lease":
                    return current_approval.journal_record()
                if stage != "post_commit":
                    raise NormalizedApiError(
                        "ctp_execution_approval",
                        "ctp_approval_transition_stage_invalid",
                        definite_reject=True,
                    )
                return record

            result = transition(
                current_approval.journal_record(),
                transition_guard=transition_guard,
            )
            return current_approval, result

    def redeem_ctp_execution_approval(
        self,
        approval: CtpExecutionApproval | bytes | str,
        *,
        trust_root: Mapping[str, Any] | None,
        context: Mapping[str, Any] | CtpExecutionApprovalContext,
    ) -> CtpExecutionApprovalCapability:
        """Durably consume a verified approval and return process-local opaque evidence.

        Revalidation immediately before consumption observes expiry, current
        deployment context, and the current trusted revocation snapshot.  The
        existing execution journal must fsync the nonce before this method
        returns.  An ordinary-purpose capability remains ineligible for native
        arming; a recovery-purpose capability is accepted only by the separate
        bounded recovery entry point.
        """

        operation = "redeem_ctp_execution_approval"
        if isinstance(approval, (bytes, str)):
            approval = self.verify_ctp_execution_approval(
                approval,
                trust_root=trust_root,
                context=context,
            )
        elif type(approval) is not CtpExecutionApproval:
            raise NormalizedApiError(
                operation, "ctp_approval_opaque_required", definite_reject=True
            )
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(operation, "execution_session_required", definite_reject=True)
        approval, _result = self._run_ctp_approval_transition(
            approval,
            trust_root=trust_root,
            context=context,
            transition=session.consume_ctp_execution_approval,
        )
        return _new_capability(
            approval,
            self,
            context=context,
            trust_root=trust_root,
        )

    def preauthorize_ctp_execution_approval(
        self,
        approval: CtpExecutionApproval | bytes | str,
        *,
        trust_root: Mapping[str, Any] | None,
        context: Mapping[str, Any] | CtpExecutionApprovalContext,
    ) -> dict[str, Any]:
        """Persist approval evidence while retaining a read-only session."""

        operation = "preauthorize_ctp_execution_approval"
        if isinstance(approval, (bytes, str)):
            approval = self.verify_ctp_execution_approval(
                approval,
                trust_root=trust_root,
                context=context,
            )
        elif type(approval) is not CtpExecutionApproval:
            raise NormalizedApiError(
                operation, "ctp_approval_opaque_required", definite_reject=True
            )
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(operation, "execution_session_required", definite_reject=True)
        _approval, result = self._run_ctp_approval_transition(
            approval,
            trust_root=trust_root,
            context=context,
            transition=session.record_ctp_execution_approval_preauthorization,
        )
        return result

    def record_ctp_execution_approval_revocation_snapshot(
        self,
        *,
        trust_root: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Durably record a newer deployment revocation snapshot.

        The SDK does not fetch or infer revocations.  A deployment supplies
        its independently managed trust-root snapshot; this method records it
        in the same fenced execution journal so a restart cannot roll back to
        an older version.
        """

        operation = "record_ctp_execution_approval_revocation_snapshot"
        session = self._execution_session
        if session is None:
            raise NormalizedApiError(operation, "execution_session_required", definite_reject=True)
        snapshot, snapshot_hash = current_ctp_execution_revocation_snapshot(trust_root)
        version = snapshot["version"]
        record = {
            "approval_id": f"revocation-{version}",
            "nonce": f"revocation-{version}",
            "revocation_snapshot": dict(snapshot),
            "revocation_snapshot_sha256": snapshot_hash,
            "revoked_approval_ids": list(snapshot["revoked_approval_ids"]),
            "revoked_nonces": list(snapshot["revoked_nonces"]),
            "revocation_snapshot_version": version,
            "exchange_name": "CTP___FUTURE",
        }
        try:
            return session.record_ctp_execution_approval_revocation_snapshot(record)
        except NormalizedApiError:
            raise
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_approval_revocation_persistence_failed",
                definite_reject=True,
            ) from None

    def _issue_ctp_execution_authorization_for_test(
        self,
        proof: Mapping[str, Any],
        *,
        _test_authority: object,
        execution_cycle_id: str = "controlled-test-cycle",
    ) -> object:
        """Internal controlled issuer for offline core-contract tests only.

        There is no production signer in this iteration.  Consequently this
        helper is deliberately private and the public arm endpoint rejects
        mappings.  It creates a token only after checking the live native
        context and asks the native feed to mint its paired one-shot token.
        Calling public state/hash methods or ``prepare_execution_authorization``
        cannot create this object.
        """

        from ._execution_session import _execution_arm_proof

        operation = "issue_ctp_execution_authorization"
        if not _is_ctp_controlled_test_authority(_test_authority):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_required",
                definite_reject=True,
            )
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        normalized, _proof_sha256 = _execution_arm_proof(proof)
        context = self._ctp_execution_arm_context(exchange_name)
        if session._arm_context_error(normalized, context) is not None:
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_context_mismatch",
                definite_reject=True,
            )
        strategy_identity = session.config.get("strategy_identity_sha256")
        if (
            not isinstance(strategy_identity, str)
            or len(strategy_identity) != 64
            or not isinstance(execution_cycle_id, str)
            or not execution_cycle_id
            or execution_cycle_id != execution_cycle_id.strip()
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_identity_invalid",
                definite_reject=True,
            )
        feed = self.exchange_feeds.get(exchange_name)
        capability = getattr(self, "_ctp_execution_capability", None)
        issuer = getattr(feed, "_issue_execution_authorization_for_core", None)
        if capability is None or not callable(issuer):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_unavailable",
                definite_reject=True,
            )
        try:
            native_authorization = issuer(
                capability,
                normalized,
                strategy_identity_sha256=strategy_identity,
                execution_cycle_id=execution_cycle_id,
            )
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_issue_failed",
                definite_reject=True,
            ) from None
        return _CtpExecutionArmAuthorization(
            api=self,
            venue=exchange_name,
            proof=normalized,
            context=context,
            native_authorization=native_authorization,
            strategy_identity_sha256=strategy_identity,
            execution_cycle_id=execution_cycle_id,
            preflight_epoch=self._ctp_execution_authorization_epoch_value(),
        )

    @staticmethod
    def _ctp_recovery_action_plan_matches(
        approval: CtpExecutionApproval, plan: Mapping[str, Any]
    ) -> bool:
        from bt_api_ctp.recovery_plan_validation import ctp_recovery_action_plan_matches

        return ctp_recovery_action_plan_matches(approval, plan)

    @staticmethod
    def _ctp_recovery_remaining_plan_matches(
        current: Mapping[str, Any], expected: Mapping[str, Any]
    ) -> bool:
        from bt_api_ctp.recovery_plan_validation import ctp_recovery_remaining_plan_matches

        return ctp_recovery_remaining_plan_matches(current, expected)

    def _ctp_recovery_approval_proof(
        self,
        approval: CtpExecutionApproval,
        *,
        exchange_name: str,
        session: Any,
    ) -> dict[str, Any]:
        """Turn signed recovery material into the existing core proof shape."""

        from ._execution_session import _execution_arm_proof

        payload = approval.payload
        authorized = [
            f"{item['exchange_id']}.{item['instrument_id']}"
            for item in payload["authorized_instruments"]
        ]
        primary = payload["primary_instrument"]
        proof = {
            "account_fingerprint": payload["account_fingerprint"],
            "trading_day": payload["trading_day"],
            "instrument": f"{primary['exchange_id']}.{primary['instrument_id']}",
            "connection_generation": payload["connection_generation"],
            "environment_profile": payload["environment_profile"],
            "receipt_sha256": payload["receipt_sha256"],
            "native_sha256": payload["native_sha256"],
            "ctp_package_sha256": payload["ctp_package_sha256"],
            "source_hashes_sha256": payload["source_hashes_sha256"],
            "dependency_hashes_sha256": payload["dependency_hashes_sha256"],
            "preflight_sha256": payload["preflight_sha256"],
        }
        current_arm_proof = session._arm_proof
        if (
            not isinstance(current_arm_proof, Mapping)
            or current_arm_proof.get("scope_version") != "ctp-contract-bundle-v1"
            or not isinstance(current_arm_proof.get("authorized_instruments"), (list, tuple))
            or tuple(current_arm_proof["authorized_instruments"]) != tuple(authorized)
            or current_arm_proof.get("instrument")
            != f"{primary['exchange_id']}.{primary['instrument_id']}"
        ):
            raise NormalizedApiError(
                "arm_execution_recovery",
                "ctp_recovery_scope_mismatch",
                definite_reject=True,
            )
        proof.update(
            {
                "scope_version": current_arm_proof["scope_version"],
                "authorized_instruments": authorized,
            }
        )
        runtime_identity = self._ctp_execution_runtime_identity()
        if payload.get("bt_api_ctp_sha256") != runtime_identity.get("ctp_package_sha256"):
            raise NormalizedApiError(
                "arm_execution_recovery",
                "ctp_execution_authorization_material_mismatch",
                definite_reject=True,
            )
        normalized, _proof_sha256 = _execution_arm_proof(proof)
        if exchange_name.partition(DATANAME_SEPARATOR)[0].upper() != "CTP":
            raise NormalizedApiError(
                "arm_execution_recovery",
                "single_ctp_session_required",
                definite_reject=True,
            )
        return normalized

    def _prepare_ctp_recovery_arm_authorization(
        self,
        approval_capability: CtpExecutionApprovalCapability,
        recovery_token_sha256: str,
    ) -> _CtpExecutionArmAuthorization:
        """Validate a redeemed recovery approval and mint its native pair.

        The only mapping-to-native bridge here is the CTP core's existing
        ``_issue_execution_authorization_for_core`` method.  The controlled
        test issuer is deliberately unreachable from this public transition.
        """

        operation = "arm_execution_recovery"
        if type(approval_capability) is not CtpExecutionApprovalCapability:
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_required",
                definite_reject=True,
            )
        self._reject_simnow_native_execution(
            approval_capability.bindings.get("environment_profile"),
            operation,
            schema_version=approval_capability.schema_version,
        )
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        if (
            approval_capability._owner is not self
            or approval_capability.purpose != RECOVERY_APPROVAL_PURPOSE
            or approval_capability.schema_version
            not in {
                RECOVERY_APPROVAL_SCHEMA_VERSION,
                SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION,
            }
            or approval_capability.recovery_scope_version != RECOVERY_APPROVAL_SCOPE_VERSION
            or approval_capability._recovery_used
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_required",
                definite_reject=True,
            )
        # A recovery approval is an opaque one-shot arm attempt.  Mark it
        # before any live-context or native call so all failures remain
        # conservative and cannot be retried with the same nonce in-process.
        approval_capability._recovery_used = True
        if (
            not isinstance(recovery_token_sha256, str)
            or recovery_token_sha256 != approval_capability.recovery_token_sha256
        ):
            raise NormalizedApiError(
                operation, "invalid_or_consumed_recovery_token", definite_reject=True
            )
        plan = session._recovery_plan
        if (
            not isinstance(plan, Mapping)
            or plan.get("status") != "RECOVERABLE"
            or plan.get("recovery_token_sha256") != recovery_token_sha256
            or approval_capability.recovery_plan_sha256 != recovery_plan_digest(plan)
            or approval_capability.recovery_action_sha256
            != recovery_action_digest(
                [dict(action) for action in approval_capability.recovery_actions]
            )
            or not self._ctp_recovery_action_plan_matches(approval_capability._approval, plan)
        ):
            raise NormalizedApiError(operation, "ctp_recovery_plan_mismatch", definite_reject=True)
        now = datetime.now(UTC)
        expires_at = datetime.fromisoformat(approval_capability.expires_at[:-1] + "+00:00")
        if now >= expires_at:
            raise NormalizedApiError(operation, "ctp_approval_expired", definite_reject=True)
        if (
            approval_capability.approval_id in session._ctp_approval_revoked_ids
            or approval_capability.nonce in session._ctp_approval_revoked_nonces
            or approval_capability.revocation_snapshot_version
            < session._ctp_approval_revocation_snapshot_version
        ):
            raise NormalizedApiError(operation, "ctp_approval_revoked", definite_reject=True)
        if (
            session.config.get("strategy_id") != approval_capability.bindings.get("strategy_id")
            or session.config.get("strategy_identity_sha256")
            != approval_capability.bindings.get("strategy_identity_sha256")
            or plan.get("strategy_id") != approval_capability.bindings.get("strategy_id")
            or plan.get("execution_cycle_id")
            != approval_capability.bindings.get("execution_cycle_id")
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_identity_mismatch",
                definite_reject=True,
            )
        if plan.get("execution_cycle_id") is None or not isinstance(
            plan.get("execution_cycle_id"), str
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_identity_mismatch",
                definite_reject=True,
            )
        proof = self._ctp_recovery_approval_proof(
            approval_capability._approval,
            exchange_name=exchange_name,
            session=session,
        )
        current_context = self._ctp_execution_arm_context(exchange_name)
        if session._arm_context_error(proof, current_context) is not None:
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_context_mismatch",
                definite_reject=True,
            )
        runtime_python = self._ctp_execution_runtime_python_identity()
        for field_name in (
            "backtrader_sha256",
            "bt_api_py_sha256",
            "bt_api_base_sha256",
            "dependency_hashes_sha256",
        ):
            if approval_capability.bindings.get(field_name) != runtime_python.get(field_name):
                raise NormalizedApiError(
                    operation,
                    "ctp_execution_authorization_material_mismatch",
                    definite_reject=True,
                )
        try:
            self._validate_ctp_recovery_material(
                approval_capability,
                operation=operation,
            )
        except Exception as exc:
            self._latch_ctp_recovery_failure(session, exc)
            raise
        environment = self._ctp_verified_execution_environment(exchange_name, operation=operation)
        feed = self.exchange_feeds.get(exchange_name)
        core_capability = getattr(self, "_ctp_execution_capability", None)
        issuer = getattr(feed, "_issue_execution_authorization_for_core", None)
        if core_capability is None or not callable(issuer):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_unavailable",
                definite_reject=True,
            )
        self._reject_simnow_native_execution(
            approval_capability.bindings.get("environment_profile"),
            operation,
            schema_version=approval_capability.schema_version,
        )
        try:
            native_authorization = issuer(
                core_capability,
                proof,
                environment_profile=environment["profile"],
                environment_verified=environment["verified"],
                strategy_identity_sha256=approval_capability.bindings["strategy_identity_sha256"],
                execution_cycle_id=approval_capability.bindings["execution_cycle_id"],
            )
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_issue_failed",
                definite_reject=True,
            ) from None
        return _CtpExecutionArmAuthorization(
            api=self,
            venue=exchange_name,
            proof=proof,
            context=current_context,
            native_authorization=native_authorization,
            strategy_identity_sha256=approval_capability.bindings["strategy_identity_sha256"],
            execution_cycle_id=approval_capability.bindings["execution_cycle_id"],
            preflight_epoch=self._ctp_execution_authorization_epoch_value(),
            approval_capability=approval_capability,
            recovery_plan_sha256=approval_capability.recovery_plan_sha256,
            recovery_action_sha256=approval_capability.recovery_action_sha256,
            recovery_token_sha256=recovery_token_sha256,
        )

    def _validate_active_ctp_recovery_authorization(
        self,
        approval_capability: CtpExecutionApprovalCapability,
        recovery_token_sha256: str,
        *,
        operation: str,
    ) -> None:
        """Recheck the signed lease immediately before each managed write."""

        session, exchange_name = self._sole_ctp_execution_venue(operation)
        if (
            type(approval_capability) is not CtpExecutionApprovalCapability
            or approval_capability._owner is not self
            or approval_capability.purpose != RECOVERY_APPROVAL_PURPOSE
            or approval_capability.schema_version
            not in {
                RECOVERY_APPROVAL_SCHEMA_VERSION,
                SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION,
            }
            or approval_capability.recovery_scope_version != RECOVERY_APPROVAL_SCOPE_VERSION
            or not approval_capability._recovery_used
            or approval_capability.recovery_token_sha256 != recovery_token_sha256
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_required",
                definite_reject=True,
            )
        try:
            expires_at = datetime.fromisoformat(approval_capability.expires_at[:-1] + "+00:00")
        except (TypeError, ValueError):
            raise NormalizedApiError(
                operation,
                "ctp_recovery_authorization_invalid",
                definite_reject=True,
            ) from None
        if datetime.now(UTC) >= expires_at:
            raise NormalizedApiError(operation, "ctp_approval_expired", definite_reject=True)
        if (
            approval_capability.approval_id in session._ctp_approval_revoked_ids
            or approval_capability.nonce in session._ctp_approval_revoked_nonces
            or approval_capability.revocation_snapshot_version
            < session._ctp_approval_revocation_snapshot_version
        ):
            raise NormalizedApiError(operation, "ctp_approval_revoked", definite_reject=True)
        snapshot = approval_capability._approval.revocation_snapshot
        snapshot_expires = snapshot.get("expires_at") if isinstance(snapshot, Mapping) else None
        if not isinstance(snapshot_expires, str):
            raise NormalizedApiError(
                operation,
                "ctp_approval_revocation_snapshot_stale",
                definite_reject=True,
            )
        try:
            if datetime.now(UTC) >= datetime.fromisoformat(snapshot_expires[:-1] + "+00:00"):
                raise NormalizedApiError(
                    operation,
                    "ctp_approval_revocation_snapshot_stale",
                    definite_reject=True,
                )
        except ValueError:
            raise NormalizedApiError(
                operation,
                "ctp_approval_revocation_snapshot_stale",
                definite_reject=True,
            ) from None
        try:
            snapshot_sha256 = hashlib.sha256(
                json.dumps(
                    dict(snapshot),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
        except (TypeError, ValueError, UnicodeEncodeError):
            raise NormalizedApiError(
                operation,
                "ctp_approval_revocation_snapshot_stale",
                definite_reject=True,
            ) from None
        if (
            session._ctp_approval_revocation_snapshot_version
            != approval_capability.revocation_snapshot_version
            or session._ctp_approval_revocation_snapshot_sha256 != snapshot_sha256
        ):
            raise NormalizedApiError(
                operation,
                "ctp_approval_revocation_snapshot_stale",
                definite_reject=True,
            )
        plan = session._recovery_plan
        authorized_plan, remaining_plan = session._recovery_authorized_plan_state()
        if authorized_plan is None:
            # Private compatibility recovery arms have no signed plan baseline.
            # They retain the historical exact-plan check; the public opaque
            # route always installs the two detached plans before native arm.
            authorized_plan = plan
            remaining_plan = plan
        if (
            not isinstance(plan, Mapping)
            or plan.get("status") != "RECOVERABLE"
            or plan.get("recovery_token_sha256") != recovery_token_sha256
            or session.config.get("strategy_id") != approval_capability.bindings.get("strategy_id")
            or session.config.get("strategy_identity_sha256")
            != approval_capability.bindings.get("strategy_identity_sha256")
            or approval_capability.recovery_plan_sha256 != recovery_plan_digest(authorized_plan)
            or approval_capability.recovery_action_sha256
            != recovery_action_digest(
                [dict(action) for action in approval_capability.recovery_actions]
            )
            or not self._ctp_recovery_action_plan_matches(
                approval_capability._approval, authorized_plan
            )
            or not self._ctp_recovery_remaining_plan_matches(plan, remaining_plan)
        ):
            raise NormalizedApiError(
                operation,
                "ctp_recovery_plan_unavailable",
                definite_reject=True,
            )
        proof = self._ctp_recovery_approval_proof(
            approval_capability._approval,
            exchange_name=exchange_name,
            session=session,
        )
        current = self._ctp_execution_arm_context(exchange_name)
        if (
            session._arm_context_error(proof, current, require_account_stream=True) is not None
            or not isinstance(session._arm_proof, Mapping)
            or dict(session._arm_proof) != proof
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_context_mismatch",
                definite_reject=True,
            )
        self._validate_ctp_recovery_material(
            approval_capability,
            operation=operation,
        )
        # Keep runtime package hashing adjacent to the sealed material
        # collector.  Both are the last potentially blocking identity reads;
        # the final bounded gate below then performs only state/time checks.
        runtime_python = self._ctp_execution_runtime_python_identity()
        if any(
            approval_capability.bindings.get(field_name) != runtime_python.get(field_name)
            for field_name in (
                "backtrader_sha256",
                "bt_api_py_sha256",
                "bt_api_base_sha256",
                "dependency_hashes_sha256",
            )
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_material_mismatch",
                definite_reject=True,
            )

        try:
            current_epoch, current_pending = self._ctp_private_ingress_snapshot(exchange_name)
        except Exception:
            raise NormalizedApiError(
                operation, "execution_recovery_required", definite_reject=True
            ) from None
        if (
            current_pending
            or session._recovery_private_ingress_epoch_fence is None
            or current_epoch != session._recovery_private_ingress_epoch_fence
            or session.recovery_private_ingress_revision()
            != session._recovery_private_ingress_revision_fence
            or session.recovery_private_event_revision()
            != session._recovery_private_event_revision_fence
        ):
            raise NormalizedApiError(operation, "execution_recovery_required", definite_reject=True)

        gate = self._ctp_execution_gate_state(exchange_name, operation=operation)
        from ._execution_session import _execution_arm_proof, _is_execution_arm_bundle

        _normalized_proof, proof_sha256 = _execution_arm_proof(proof)
        scope_matches = True
        if _is_execution_arm_bundle(_normalized_proof):
            scope_matches = bool(
                gate.get("scope_version") == _normalized_proof["scope_version"]
                and isinstance(gate.get("authorized_instruments"), (list, tuple))
                and tuple(gate["authorized_instruments"])
                == tuple(_normalized_proof["authorized_instruments"])
            )
        if (
            gate.get("managed") is not True
            or gate.get("armed") is not True
            or gate.get("connection_generation") != _normalized_proof["connection_generation"]
            or gate.get("trading_day") != _normalized_proof["trading_day"]
            or gate.get("environment_profile") != _normalized_proof["environment_profile"]
            or gate.get("proof_sha256") != proof_sha256
            or not scope_matches
        ):
            raise NormalizedApiError(operation, "execution_recovery_required", definite_reject=True)
        # Finish every potentially blocking read and lock acquisition before
        # entering the bounded handoff gate.  In particular, these calls may
        # inspect native/package files through their transitive runtime
        # identity collectors.  The final helper below must remain a pure
        # comparison of the observed values plus current short-lived state.
        (
            final_authorized_plan,
            final_remaining_plan,
        ) = session._recovery_authorized_plan_state()
        final_plan = session._recovery_plan
        if final_authorized_plan is None:
            final_authorized_plan = final_plan
            final_remaining_plan = final_plan
        final_proof = self._ctp_recovery_approval_proof(
            approval_capability._approval,
            exchange_name=exchange_name,
            session=session,
        )
        final_context = self._ctp_execution_arm_context(exchange_name)

        # Re-read every short-lived binding only after the durable
        # intent/cancel-intent append and all material collection.  A lease
        # that expires or is revoked during any preceding wait must never
        # reach the lower transport.
        self._validate_ctp_recovery_dispatch_freshness(
            approval_capability,
            recovery_token_sha256,
            session=session,
            exchange_name=exchange_name,
            operation=operation,
            runtime_python=runtime_python,
            current_proof=final_proof,
            current_context=final_context,
            recovery_plan=final_plan,
            authorized_plan=final_authorized_plan,
            remaining_plan=final_remaining_plan,
        )

    def _validate_ctp_recovery_dispatch_freshness(
        self,
        approval_capability: CtpExecutionApprovalCapability,
        recovery_token_sha256: str,
        *,
        session: Any,
        exchange_name: str,
        operation: str,
        runtime_python: Mapping[str, Any],
        current_proof: Mapping[str, Any],
        current_context: Mapping[str, Any],
        recovery_plan: Mapping[str, Any] | None,
        authorized_plan: Mapping[str, Any] | None,
        remaining_plan: Mapping[str, Any] | None,
    ) -> None:
        """Run the bounded final gate immediately before a recovery write.

        The caller performs every material/native read, plan snapshot, and
        journal/lease transition first, then invokes this helper while the
        session's transition mutex is held.  This helper deliberately performs
        no collector, filesystem I/O, feed query, or lock acquisition: after
        its final time/revocation comparisons it hands off directly to the
        permitted lower call.
        """

        if (
            type(approval_capability) is not CtpExecutionApprovalCapability
            or approval_capability._owner is not self
            or approval_capability.purpose != RECOVERY_APPROVAL_PURPOSE
            or approval_capability.schema_version
            not in {
                RECOVERY_APPROVAL_SCHEMA_VERSION,
                SIMNOW_RECOVERY_APPROVAL_SCHEMA_VERSION,
            }
            or approval_capability.recovery_scope_version != RECOVERY_APPROVAL_SCOPE_VERSION
            or not approval_capability._recovery_used
            or approval_capability.recovery_token_sha256 != recovery_token_sha256
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_required",
                definite_reject=True,
            )
        self._reject_simnow_native_execution(
            approval_capability.bindings.get("environment_profile"),
            operation,
            schema_version=approval_capability.schema_version,
        )

        now = datetime.now(UTC)
        try:
            expires_at = datetime.fromisoformat(approval_capability.expires_at[:-1] + "+00:00")
        except (TypeError, ValueError):
            raise NormalizedApiError(
                operation,
                "ctp_recovery_authorization_invalid",
                definite_reject=True,
            ) from None
        if now >= expires_at:
            raise NormalizedApiError(operation, "ctp_approval_expired", definite_reject=True)

        snapshot = approval_capability._approval.revocation_snapshot
        if not isinstance(snapshot, Mapping):
            raise NormalizedApiError(
                operation,
                "ctp_approval_revocation_snapshot_stale",
                definite_reject=True,
            )
        snapshot_expires = snapshot.get("expires_at")
        try:
            if not isinstance(snapshot_expires, str) or now >= datetime.fromisoformat(
                snapshot_expires[:-1] + "+00:00"
            ):
                raise NormalizedApiError(
                    operation,
                    "ctp_approval_revocation_snapshot_stale",
                    definite_reject=True,
                )
        except (TypeError, ValueError):
            raise NormalizedApiError(
                operation,
                "ctp_approval_revocation_snapshot_stale",
                definite_reject=True,
            ) from None
        if (
            approval_capability.approval_id in session._ctp_approval_revoked_ids
            or approval_capability.nonce in session._ctp_approval_revoked_nonces
            or approval_capability.revocation_snapshot_version
            < session._ctp_approval_revocation_snapshot_version
        ):
            raise NormalizedApiError(operation, "ctp_approval_revoked", definite_reject=True)
        try:
            snapshot_sha256 = hashlib.sha256(
                json.dumps(
                    dict(snapshot),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
        except (TypeError, ValueError, UnicodeEncodeError):
            raise NormalizedApiError(
                operation,
                "ctp_approval_revocation_snapshot_stale",
                definite_reject=True,
            ) from None
        if (
            session._ctp_approval_revocation_snapshot_version
            != approval_capability.revocation_snapshot_version
            or session._ctp_approval_revocation_snapshot_sha256 != snapshot_sha256
        ):
            raise NormalizedApiError(
                operation,
                "ctp_approval_revocation_snapshot_stale",
                definite_reject=True,
            )

        if (
            not isinstance(recovery_plan, Mapping)
            or not isinstance(authorized_plan, Mapping)
            or not isinstance(remaining_plan, Mapping)
        ):
            raise NormalizedApiError(
                operation,
                "ctp_recovery_plan_unavailable",
                definite_reject=True,
            )
        if (
            recovery_plan.get("status") != "RECOVERABLE"
            or recovery_plan.get("recovery_token_sha256") != recovery_token_sha256
            or session.config.get("strategy_id") != approval_capability.bindings.get("strategy_id")
            or session.config.get("strategy_identity_sha256")
            != approval_capability.bindings.get("strategy_identity_sha256")
            or approval_capability.recovery_plan_sha256 != recovery_plan_digest(authorized_plan)
            or approval_capability.recovery_action_sha256
            != recovery_action_digest(
                [dict(action) for action in approval_capability.recovery_actions]
            )
            or not self._ctp_recovery_action_plan_matches(
                approval_capability._approval, authorized_plan
            )
            or not self._ctp_recovery_remaining_plan_matches(recovery_plan, remaining_plan)
        ):
            raise NormalizedApiError(
                operation,
                "ctp_recovery_plan_unavailable",
                definite_reject=True,
            )

        # Runtime package hashes and the sealed material collector have already
        # completed.  Comparing the same observed values here keeps the final
        # gate bounded; a changed sealed source/path is rejected before this
        # helper is reached.
        if any(
            approval_capability.bindings.get(field_name) != runtime_python.get(field_name)
            for field_name in (
                "backtrader_sha256",
                "bt_api_py_sha256",
                "bt_api_base_sha256",
                "dependency_hashes_sha256",
            )
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_material_mismatch",
                definite_reject=True,
            )

        if (
            session._arm_context_error(current_proof, current_context, require_account_stream=True)
            is not None
            or not isinstance(session._arm_proof, Mapping)
            or dict(session._arm_proof) != dict(current_proof)
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_context_mismatch",
                definite_reject=True,
            )

    @_serialized_ctp_execution_transition
    def arm_execution_from_preflight(self, authorization: object) -> dict[str, Any]:
        """Atomically arm CTP only from a core-issued opaque authorization."""
        operation = "arm_execution_from_preflight"
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        if (
            type(authorization) is not _CtpExecutionArmAuthorization
            or authorization._seal is not _CTP_INTERNAL_AUTHORIZATION_SEAL
            or authorization._api is not self
            or authorization._venue != exchange_name
            or authorization._used
            or (
                authorization._approval_capability is not None
                and authorization._approval_capability.purpose == APPROVAL_PURPOSE
                and not _is_ctp_entry_write_guard(
                    authorization._entry_write_guard,
                    session=session,
                )
            )
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_required",
                definite_reject=True,
            )
        proof = dict(authorization._proof)
        self._reject_simnow_native_execution(
            authorization._context.get("environment_profile"), operation
        )
        # A valid token is one-shot even when a later recovery or stream gate
        # rejects it.  Re-arming requires a newly issued authority after a
        # fresh state/preflight check.
        authorization._used = True
        if authorization._preflight_epoch != self._ctp_execution_authorization_epoch_value():
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_preflight_invalidated",
                definite_reject=True,
            )
        current_context = self._ctp_execution_arm_context(exchange_name)
        if session._arm_context_error(proof, current_context) is not None or any(
            current_context.get(field) != authorization._context.get(field)
            for field in (
                "account_fingerprint",
                "trading_day",
                "connection_generation",
                "environment_profile",
                "native_sha256",
                "ctp_package_sha256",
            )
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_context_mismatch",
                definite_reject=True,
            )
        private_event_revision = session.recovery_private_event_revision()
        private_ingress_revision = session.recovery_private_ingress_revision()
        (
            private_ingress_epoch,
            private_ingress_pending,
        ) = self._ctp_private_ingress_snapshot(exchange_name)
        if private_ingress_pending:
            raise NormalizedApiError(
                operation,
                "execution_recovery_required",
                definite_reject=True,
            )

        def state_reader() -> dict[str, Any]:
            return self._ctp_execution_arm_context(exchange_name)

        def prepare_execution() -> None:
            self._prepare_ctp_execution_stream(exchange_name)
            self._ingest_ctp_private_queue(
                session,
                exchange_name,
                operation=operation,
            )
            self._arm_ctp_execution_gate(
                exchange_name,
                proof,
                authorization,
                operation=operation,
            )

        def rollback_execution() -> None:
            with suppress(Exception):
                self._disarm_ctp_execution_gate(
                    exchange_name,
                    "execution_arm_rollback",
                    operation=operation,
                )

        try:
            result = session.arm_from_preflight(
                proof,
                state_reader,
                venue=exchange_name,
                prepare_execution=prepare_execution,
                rollback_execution=rollback_execution,
                prepare_execution_outside_mutex=True,
                execution_write_guard=authorization._entry_write_guard,
                authorization_context={
                    "strategy_identity_sha256": authorization._strategy_identity_sha256,
                    "execution_cycle_id": authorization._execution_cycle_id,
                },
            )
            self._ingest_ctp_private_queue(
                session,
                exchange_name,
                operation=operation,
            )
            current_epoch, current_pending = self._ctp_private_ingress_snapshot(exchange_name)
            private_fence_changed = bool(
                current_epoch != private_ingress_epoch
                or current_pending
                or session.recovery_private_ingress_revision() != private_ingress_revision
                or session.recovery_private_event_revision() != private_event_revision
            )
            if private_fence_changed:
                raise NormalizedApiError(
                    operation,
                    "execution_recovery_required",
                    definite_reject=True,
                )
            return result
        except Exception as exc:
            code = str(getattr(exc, "code", "") or "execution_arm_rollback")
            disarm_failed = False
            try:
                self._disarm_ctp_execution_gate(
                    exchange_name,
                    code,
                    operation=operation,
                )
            except Exception:
                disarm_failed = True
            if disarm_failed or code != "execution_recovery_required":
                self._reset_ctp_execution_stream(exchange_name)
            # Both a rejected arm and its rollback invalidate the preflight
            # that minted any unconsumed facade authorization.
            self._invalidate_ctp_execution_authorizations()
            with suppress(Exception):
                reusable_read_only_rejections = {
                    "execution_recovery_required",
                    "execution_recovery_arm_required",
                    "execution_recovery_completion_required",
                    "execution_recovery_manual_intervention",
                    "fresh_execution_preflight_required",
                }
                if code in reusable_read_only_rejections:
                    session.pause_recovery()
                else:
                    generation = proof.get("connection_generation")
                    session.disarm_execution(code, generation=generation)
            raise

    def _issue_ctp_settlement_authorization_for_test(
        self,
        *,
        _test_authority: object,
    ) -> object:
        """Issue one internal controlled settlement grant for offline tests.

        There is intentionally no public settlement issuer in this iteration.
        The caller must hold the separate internal test marker, the managed
        session must be explicitly execution-enabled (not market-data-only),
        and the native feed still mints its own account/day/generation-bound
        token after provider environment verification.
        """

        operation = "issue_ctp_settlement_authorization"
        if not _is_ctp_controlled_test_authority(_test_authority):
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_required",
                definite_reject=True,
            )
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        self._reject_simnow_profile_write(exchange_name, operation)
        if session.config.get("market_data_only") is True:
            raise NormalizedApiError(
                operation,
                "ctp_settlement_market_data_only",
                definite_reject=True,
            )
        context = self._ctp_settlement_context(exchange_name, operation=operation)
        feed = self.exchange_feeds.get(exchange_name)
        capability = getattr(self, "_ctp_execution_capability", None)
        issuer = getattr(feed, "_issue_settlement_authorization_for_core", None)
        if capability is None or not callable(issuer):
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_unavailable",
                definite_reject=True,
            )
        try:
            native_authorization = issuer(capability)
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_issue_failed",
                definite_reject=True,
            ) from None
        return _CtpSettlementAuthorization(
            api=self,
            venue=exchange_name,
            context=context,
            native_authorization=native_authorization,
        )

    @_serialized_ctp_execution_transition
    def _confirm_ctp_settlement_for_core(
        self,
        authorization: object,
        *,
        timeout: float = 5.0,
    ) -> bool:
        """Consume one internal settlement grant and reset all arm state."""

        operation = "confirm_ctp_settlement"
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        if (
            type(authorization) is not _CtpSettlementAuthorization
            or authorization._seal is not _CTP_INTERNAL_AUTHORIZATION_SEAL
            or authorization._api is not self
            or authorization._venue != exchange_name
            or authorization._used
        ):
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_required",
                definite_reject=True,
            )
        self._reject_simnow_native_execution(
            authorization._context.get("environment_profile"), operation
        )
        # A terminal-write grant is one-shot even if a later environment or
        # transport check rejects it.
        authorization._used = True
        if session.config.get("market_data_only") is True:
            raise NormalizedApiError(
                operation,
                "ctp_settlement_market_data_only",
                definite_reject=True,
            )
        current_context = self._ctp_settlement_context(exchange_name, operation=operation)
        self._reject_simnow_native_execution(current_context.get("environment_profile"), operation)
        if any(
            current_context.get(field) != authorization._context.get(field)
            for field in (
                "account_fingerprint",
                "trading_day",
                "connection_generation",
                "environment_profile",
                "native_sha256",
                "ctp_package_sha256",
            )
        ):
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_context_mismatch",
                definite_reject=True,
            )
        feed = self.exchange_feeds.get(exchange_name)
        capability = getattr(self, "_ctp_execution_capability", None)
        method = getattr(feed, "confirm_settlement", None)
        if capability is None or not callable(method):
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_unavailable",
                definite_reject=True,
            )
        # Invalidate facade grants before invoking the terminal native path.
        # A test double cannot be allowed to preserve an old arm token merely
        # because it lacks the native client's preflight epoch implementation.
        self._invalidate_ctp_execution_authorizations()
        try:
            result = bool(
                method(
                    timeout=timeout,
                    _execution_capability=capability,
                    _settlement_authorization=authorization._native_authorization,
                )
            )
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_settlement_confirmation_failed",
                definite_reject=True,
            ) from None
        finally:
            # The native layer advances its preflight epoch before the native
            # ReqSettlementInfoConfirm call.  Mirror that invalidation in the
            # facade session regardless of the terminal transport outcome.
            with suppress(Exception):
                self._disarm_ctp_execution_gate(
                    exchange_name,
                    "ctp_settlement_confirmation_invalidated_preflight",
                    operation=operation,
                )
            self._reset_ctp_execution_stream(exchange_name)
            self._invalidate_ctp_execution_authorizations()
            session.prepare_execution_authorization(
                "ctp_settlement_confirmation_invalidated_preflight"
            )
        return result

    @_serialized_ctp_execution_transition
    def prepare_execution_authorization(
        self,
        reason: str = "execution_authorization_prepared",
    ) -> dict[str, Any]:
        """Reset managed CTP to reusable read-only state before Stage A."""
        operation = "prepare_execution_authorization"
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        failure = None
        try:
            self._ctp_execution_gate_state(
                exchange_name,
                operation=operation,
            )
            self._disarm_ctp_execution_gate(
                exchange_name,
                reason,
                operation=operation,
            )
        except Exception as exc:
            failure = exc
        finally:
            self._reset_ctp_execution_stream(exchange_name)
            self._invalidate_ctp_execution_authorizations()
            result = session.prepare_execution_authorization(reason)
        if failure is not None:
            raise failure
        return result

    def _prepare_execution_recovery_plan(self, session: Any, exchange_name: str) -> dict[str, Any]:
        operation = "prepare_execution_recovery"
        try:
            self._prepare_ctp_execution_stream(exchange_name, operation=operation)
            snapshot, barrier = self._ctp_recovery_query_barrier(
                session, exchange_name, operation=operation
            )
            plan = session.build_recovery_plan(snapshot, barrier=barrier)
        except Exception as exc:
            code = str(getattr(exc, "code", "") or "recovery_query_failed")
            plan = session.build_recovery_plan(
                {"positions": (), "orders": (), "trades": ()},
                barrier=None,
                failure_reason=code,
            )
        return self._public_ctp_recovery_plan(plan)

    @_serialized_ctp_execution_transition
    def prepare_execution_recovery(self, *, proof: Mapping[str, Any]) -> dict[str, Any]:
        """Issue an SDK-owned recovery plan from two fenced query rounds."""
        operation = "prepare_execution_recovery"
        session, exchange_name = self._sole_ctp_execution_venue(operation)

        def state_reader() -> dict[str, Any]:
            return self._ctp_execution_arm_context(exchange_name)

        failure = None
        try:
            self._disarm_ctp_execution_gate(
                exchange_name,
                "execution_recovery_prepared",
                operation=operation,
            )
        except Exception as exc:
            failure = exc
        finally:
            # Revoke the SDK lease before any refreshed evidence is accepted.
            # Stopping the account stream also tears down an uncertain native
            # session if its gate could not return a trustworthy disarm state.
            session.pause_recovery()
            self._reset_ctp_execution_stream(exchange_name)
            self._invalidate_ctp_execution_authorizations()
        if failure is not None:
            raise failure
        session.prepare_recovery(proof, state_reader, venue=exchange_name)
        return self._prepare_execution_recovery_plan(session, exchange_name)

    @_serialized_ctp_execution_transition
    def _arm_execution_recovery(
        self,
        *,
        authorization: _CtpExecutionArmAuthorization,
        recovery_token_sha256: str,
        budget_capability: Any = None,
    ) -> dict[str, Any]:
        operation = "arm_execution_recovery"
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        if (
            type(authorization) is not _CtpExecutionArmAuthorization
            or authorization._seal is not _CTP_INTERNAL_AUTHORIZATION_SEAL
            or authorization._api is not self
            or authorization._venue != exchange_name
            or authorization._used
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_required",
                definite_reject=True,
            )
        self._reject_simnow_native_execution(
            authorization._context.get("environment_profile"), operation
        )
        authorization._used = True
        if authorization._preflight_epoch != self._ctp_execution_authorization_epoch_value():
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_preflight_invalidated",
                definite_reject=True,
            )
        proof = dict(authorization._proof)
        current_context = self._ctp_execution_arm_context(exchange_name)
        self._reject_simnow_native_execution(current_context.get("environment_profile"), operation)
        if session._arm_context_error(proof, current_context) is not None or any(
            current_context.get(field) != authorization._context.get(field)
            for field in (
                "account_fingerprint",
                "trading_day",
                "connection_generation",
                "environment_profile",
                "native_sha256",
                "ctp_package_sha256",
            )
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_context_mismatch",
                definite_reject=True,
            )
        private_event_revision = session.recovery_private_event_revision()
        private_ingress_revision = session.recovery_private_ingress_revision()
        (
            private_ingress_epoch,
            private_ingress_pending,
        ) = self._ctp_private_ingress_snapshot(exchange_name)
        if private_ingress_pending:
            raise NormalizedApiError(
                operation,
                "execution_recovery_required",
                definite_reject=True,
            )

        def state_reader() -> dict[str, Any]:
            return self._ctp_execution_arm_context(exchange_name)

        def prepare_execution() -> None:
            self._ingest_ctp_private_queue(
                session,
                exchange_name,
                operation=operation,
            )
            self._arm_ctp_execution_gate(exchange_name, proof, authorization, operation=operation)

        def rollback_execution() -> None:
            self._disarm_ctp_execution_gate(
                exchange_name,
                "execution_recovery_arm_rollback",
                operation=operation,
            )

        recovery_record = None
        approval_capability = authorization._approval_capability
        if approval_capability is not None:
            recovery_record = approval_capability._approval.journal_record()
            recovery_record.update(
                {
                    "recovery_token_sha256": recovery_token_sha256,
                    "recovery_plan_sha256": authorization._recovery_plan_sha256,
                    "recovery_action_sha256": authorization._recovery_action_sha256,
                }
            )
            session._set_recovery_authorized_plan(session._recovery_plan)
            session.set_recovery_write_guard(
                lambda write_operation, **_kwargs: self._validate_active_ctp_recovery_authorization(
                    approval_capability,
                    recovery_token_sha256,
                    operation=write_operation,
                )
            )

        # Install the pre-arm private-event fence before the durable consumed
        # row.  The same fence is then checked by the post-consumed validator
        # while the session mutex is still held.
        session.set_recovery_ingress_fence(
            private_ingress_epoch,
            private_ingress_revision,
            private_event_revision,
        )

        def validate_consumed_commit() -> None:
            if approval_capability is None:
                return
            try:
                self._validate_active_ctp_recovery_authorization(
                    approval_capability,
                    recovery_token_sha256,
                    operation=operation,
                )
            except Exception as exc:
                self._latch_ctp_recovery_failure(session, exc)
                raise

        try:
            self._prepare_ctp_execution_stream(exchange_name, operation=operation)
            result = session.arm_recovery_from_preflight(
                proof,
                recovery_token_sha256,
                state_reader,
                venue=exchange_name,
                prepare_execution=prepare_execution,
                rollback_execution=rollback_execution,
                authorization_context={
                    "strategy_identity_sha256": authorization._strategy_identity_sha256,
                    "execution_cycle_id": authorization._execution_cycle_id,
                },
                recovery_authorization_record=recovery_record,
                commit_validator=validate_consumed_commit,
                budget_capability=budget_capability,
            )
            self._ingest_ctp_private_queue(
                session,
                exchange_name,
                operation=operation,
            )
            current_epoch, current_pending = self._ctp_private_ingress_snapshot(exchange_name)
            private_fence_changed = bool(
                current_epoch != private_ingress_epoch
                or current_pending
                or session.recovery_private_ingress_revision() != private_ingress_revision
                or session.recovery_private_event_revision() != private_event_revision
            )
            if private_fence_changed:
                raise NormalizedApiError(
                    operation,
                    "execution_recovery_required",
                    definite_reject=True,
                )
            session.set_recovery_ingress_fence(
                current_epoch,
                session.recovery_private_ingress_revision(),
                session.recovery_private_event_revision(),
            )
            return result
        except Exception:
            disarm_failed = False
            try:
                self._disarm_ctp_execution_gate(
                    exchange_name,
                    "execution_recovery_arm_failed",
                    operation=operation,
                )
            except Exception:
                disarm_failed = True
            if disarm_failed:
                self._reset_ctp_execution_stream(exchange_name)
            self._invalidate_ctp_execution_authorizations()
            session.pause_recovery()
            raise

    @_serialized_ctp_execution_transition
    def arm_execution_recovery(
        self,
        *,
        authorization: object | None = None,
        recovery_token_sha256: str,
        proof: Mapping[str, Any] | None = None,
        budget_capability: Any = None,
    ) -> dict[str, Any]:
        """Arm bounded recovery from a redeemed opaque recovery approval.

        ``proof`` is retained as an audit-only compatibility keyword.  It is
        never interpreted as authority and cannot reach the native bridge.
        """

        operation = "arm_execution_recovery"
        if proof is not None or type(authorization) is not CtpExecutionApprovalCapability:
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_required",
                definite_reject=True,
            )
        self._reject_simnow_native_execution(
            authorization.bindings.get("environment_profile"),
            operation,
            schema_version=authorization.schema_version,
        )
        if budget_capability is not None:
            session, _exchange_name = self._sole_ctp_execution_venue(operation)
            session.attach_ctp_budget_reservation(
                budget_capability,
                mode="recovery",
                operation=operation,
            )
        approval_capability = authorization
        internal = self._prepare_ctp_recovery_arm_authorization(
            approval_capability,
            recovery_token_sha256,
        )
        return self._arm_execution_recovery(
            authorization=internal,
            recovery_token_sha256=recovery_token_sha256,
            budget_capability=budget_capability,
        )

    def _prepare_ctp_entry_arm_authorization(
        self,
        approval_capability: CtpExecutionApprovalCapability,
    ) -> _CtpExecutionArmAuthorization:
        """Validate a redeemed entry approval and mint its native pair.

        This is the production signer for normal (non-recovery) V2 bundle
        arming: the caller must have redeemed an independently signed
        ``ctp-execution-entry-approval-v1`` artifact through the public
        approval path.  The         controlled test issuer remains unreachable here.
        """

        from ._execution_session import _execution_arm_proof

        operation = "arm_execution_from_approval"
        if type(approval_capability) is not CtpExecutionApprovalCapability:
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_required",
                definite_reject=True,
            )
        self._reject_simnow_native_execution(
            approval_capability.bindings.get("environment_profile"),
            operation,
            schema_version=approval_capability.schema_version,
        )
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        if (
            approval_capability._owner is not self
            or approval_capability.purpose != APPROVAL_PURPOSE
            or approval_capability.schema_version
            not in {ENTRY_APPROVAL_SCHEMA_VERSION, SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION}
            or approval_capability._entry_used
        ):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_required",
                definite_reject=True,
            )
        # An entry approval is an opaque one-shot arm attempt.  Mark it
        # before any live-context or native call so all failures remain
        # conservative and cannot be retried with the same nonce in-process.
        approval_capability._entry_used = True
        payload = approval_capability._approval.payload
        now = datetime.now(UTC)
        expires_at = datetime.fromisoformat(payload["expires_at"][:-1] + "+00:00")
        if now >= expires_at:
            raise NormalizedApiError(operation, "ctp_approval_expired", definite_reject=True)
        if (
            approval_capability.approval_id in session._ctp_approval_revoked_ids
            or approval_capability.nonce in session._ctp_approval_revoked_nonces
            or approval_capability.revocation_snapshot_version
            < session._ctp_approval_revocation_snapshot_version
        ):
            raise NormalizedApiError(operation, "ctp_approval_revoked", definite_reject=True)
        if session.config.get("strategy_id") != payload.get("strategy_id") or session.config.get(
            "strategy_identity_sha256"
        ) != payload.get("strategy_identity_sha256"):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_identity_mismatch",
                definite_reject=True,
            )
        runtime_identity = self._ctp_execution_runtime_identity()
        if payload.get("native_sha256") != runtime_identity.get("native_sha256") or payload.get(
            "bt_api_ctp_sha256"
        ) != runtime_identity.get("ctp_package_sha256"):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_material_mismatch",
                definite_reject=True,
            )
        runtime_python = self._ctp_execution_runtime_python_identity()
        for field_name in (
            "backtrader_sha256",
            "bt_api_py_sha256",
            "bt_api_base_sha256",
            "dependency_hashes_sha256",
        ):
            if payload.get(field_name) != runtime_python.get(field_name):
                raise NormalizedApiError(
                    operation,
                    "ctp_execution_authorization_material_mismatch",
                    definite_reject=True,
                )
        proof = self._ctp_entry_approval_proof(approval_capability)
        normalized, proof_sha256 = _execution_arm_proof(proof)
        entry_write_guard = _new_ctp_entry_write_guard(
            self,
            session,
            approval_capability,
            proof_sha256,
        )
        if exchange_name.partition(DATANAME_SEPARATOR)[0].upper() != "CTP":
            raise NormalizedApiError(
                operation,
                "single_ctp_session_required",
                definite_reject=True,
            )
        current_context = self._ctp_execution_arm_context(exchange_name)
        if session._arm_context_error(normalized, current_context) is not None:
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_context_mismatch",
                definite_reject=True,
            )
        environment = self._ctp_verified_execution_environment(exchange_name, operation=operation)
        feed = self.exchange_feeds.get(exchange_name)
        core_capability = getattr(self, "_ctp_execution_capability", None)
        issuer = getattr(feed, "_issue_execution_authorization_for_core", None)
        if core_capability is None or not callable(issuer):
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_unavailable",
                definite_reject=True,
            )
        try:
            native_authorization = issuer(
                core_capability,
                normalized,
                environment_profile=environment["profile"],
                environment_verified=environment["verified"],
                strategy_identity_sha256=payload["strategy_identity_sha256"],
                execution_cycle_id=payload["execution_cycle_id"],
            )
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_execution_authorization_issue_failed",
                definite_reject=True,
            ) from None
        return _CtpExecutionArmAuthorization(
            api=self,
            venue=exchange_name,
            proof=normalized,
            context=current_context,
            native_authorization=native_authorization,
            strategy_identity_sha256=payload["strategy_identity_sha256"],
            execution_cycle_id=payload["execution_cycle_id"],
            preflight_epoch=self._ctp_execution_authorization_epoch_value(),
            approval_capability=approval_capability,
            entry_write_guard=entry_write_guard,
        )

    @_serialized_ctp_execution_transition
    def arm_execution_from_approval(
        self,
        approval_capability: CtpExecutionApprovalCapability,
    ) -> dict[str, Any]:
        """Arm normal CTP execution from one redeemed entry approval.

        The capability must come from ``redeem_ctp_execution_approval`` with a
        signed ``ctp-execution-entry-approval-v1`` artifact whose scope, run
        material, and strategy identity match this running deployment.  The
        approval is one-shot: every later failure stays conservative and the
        nonce cannot arm twice in-process.
        """

        authorization = self._prepare_ctp_entry_arm_authorization(approval_capability)
        return self.arm_execution_from_preflight(authorization)

    def confirm_ctp_settlement_from_approval(
        self,
        approval_capability: CtpExecutionApprovalCapability,
        *,
        exchange_name: str = "CTP___FUTURE",
        timeout: float = 5.0,
    ) -> bool:
        """Confirm settlement once from a separately redeemed entry approval.

        The settlement confirmation is the one terminal write a still
        market-data-only session may perform: without it the account can never
        reach the confirmed state ordinary arming requires.  The caller must
        redeem an independently signed ``ctp-execution-entry-approval-v1``
        artifact bound to this session's account/day/generation; the
        capability is one-shot for settlement and independent of its entry
        arming use.
        """

        operation = "confirm_ctp_settlement_from_approval"
        if type(approval_capability) is not CtpExecutionApprovalCapability:
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_required",
                definite_reject=True,
            )
        self._reject_simnow_native_execution(
            approval_capability.bindings.get("environment_profile"),
            operation,
            schema_version=approval_capability.schema_version,
        )
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        if (
            approval_capability._owner is not self
            or approval_capability.purpose != APPROVAL_PURPOSE
            or approval_capability.schema_version
            not in {ENTRY_APPROVAL_SCHEMA_VERSION, SIMNOW_ENTRY_APPROVAL_SCHEMA_VERSION}
            or approval_capability._settlement_used
        ):
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_required",
                definite_reject=True,
            )
        approval_capability._settlement_used = True
        payload = approval_capability._approval.payload
        now = datetime.now(UTC)
        expires_at = datetime.fromisoformat(payload["expires_at"][:-1] + "+00:00")
        if now >= expires_at:
            raise NormalizedApiError(operation, "ctp_approval_expired", definite_reject=True)
        current_context = self._ctp_settlement_context(exchange_name, operation=operation)
        self._reject_simnow_native_execution(current_context.get("environment_profile"), operation)
        expected = {
            "account_fingerprint": payload["account_fingerprint"],
            "trading_day": payload["trading_day"],
            "connection_generation": payload["connection_generation"],
            "environment_profile": payload["environment_profile"],
        }
        if any(current_context.get(field) != value for field, value in expected.items()):
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_context_mismatch",
                definite_reject=True,
            )
        feed = self.exchange_feeds.get(exchange_name)
        capability = getattr(self, "_ctp_execution_capability", None)
        issuer = getattr(feed, "_issue_settlement_authorization_for_core", None)
        if capability is None or not callable(issuer):
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_unavailable",
                definite_reject=True,
            )
        try:
            native_authorization = issuer(capability)
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_issue_failed",
                definite_reject=True,
            ) from None
        authorization = _CtpSettlementAuthorization(
            api=self,
            venue=exchange_name,
            context=current_context,
            native_authorization=native_authorization,
        )
        return self._confirm_ctp_settlement_from_public_approval(authorization, timeout=timeout)

    def _confirm_ctp_settlement_from_public_approval(
        self,
        authorization: _CtpSettlementAuthorization,
        *,
        timeout: float,
    ) -> bool:
        """Run the confirmed native settlement write under approval control.

        Mirrors the controlled core path, except the terminal-write gate is
        the redeemed approval capability rather than the internal test
        marker: the settlement confirmation is precisely the write that
        promotes a market-data-only session to the confirmed state arming
        requires.
        """

        operation = "confirm_ctp_settlement_from_approval"
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        if (
            type(authorization) is not _CtpSettlementAuthorization
            or authorization._seal is not _CTP_INTERNAL_AUTHORIZATION_SEAL
            or authorization._api is not self
            or authorization._venue != exchange_name
            or authorization._used
        ):
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_required",
                definite_reject=True,
            )
        self._reject_simnow_native_execution(
            authorization._context.get("environment_profile"), operation
        )
        authorization._used = True
        current_context = self._ctp_settlement_context(exchange_name, operation=operation)
        self._reject_simnow_native_execution(current_context.get("environment_profile"), operation)
        if any(
            current_context.get(field) != authorization._context.get(field)
            for field in (
                "account_fingerprint",
                "trading_day",
                "connection_generation",
                "environment_profile",
                "native_sha256",
                "ctp_package_sha256",
            )
        ):
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_context_mismatch",
                definite_reject=True,
            )
        feed = self.exchange_feeds.get(exchange_name)
        capability = getattr(self, "_ctp_execution_capability", None)
        method = getattr(feed, "confirm_settlement", None)
        if capability is None or not callable(method):
            raise NormalizedApiError(
                operation,
                "ctp_settlement_authorization_unavailable",
                definite_reject=True,
            )
        self._invalidate_ctp_execution_authorizations()
        try:
            result = bool(
                method(
                    timeout=timeout,
                    _execution_capability=capability,
                    _settlement_authorization=authorization._native_authorization,
                )
            )
        except Exception:
            raise NormalizedApiError(
                operation,
                "ctp_settlement_confirmation_failed",
                definite_reject=True,
            ) from None
        finally:
            with suppress(Exception):
                self._disarm_ctp_execution_gate(
                    exchange_name,
                    "ctp_settlement_confirmation_invalidated_preflight",
                    operation=operation,
                )
            self._reset_ctp_execution_stream(exchange_name)
            self._invalidate_ctp_execution_authorizations()
            session.prepare_execution_authorization(
                "ctp_settlement_confirmation_invalidated_preflight"
            )
        return result

    @_serialized_ctp_execution_transition
    def complete_execution_recovery(
        self,
        *,
        recovery_token_sha256: str,
    ) -> dict[str, Any]:
        """Prove flatness with a fresh query barrier and revoke recovery writes."""
        operation = "complete_execution_recovery"
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        try:
            snapshot, barrier = self._ctp_recovery_query_barrier(
                session, exchange_name, operation=operation
            )
            result = session.complete_recovery(recovery_token_sha256, snapshot, barrier)
            self._disarm_ctp_execution_gate(
                exchange_name, "execution_recovery_completed", operation=operation
            )
            self._invalidate_ctp_execution_authorizations()
        except Exception:
            disarm_failed = False
            try:
                self._disarm_ctp_execution_gate(
                    exchange_name,
                    "execution_recovery_completion_failed",
                    operation=operation,
                )
            except Exception:
                disarm_failed = True
            session.pause_recovery()
            if disarm_failed:
                self._reset_ctp_execution_stream(exchange_name)
            self._invalidate_ctp_execution_authorizations()
            raise
        return result

    @_serialized_ctp_execution_transition
    def disarm_execution(self, reason: str = "execution_arm_revoked") -> dict[str, Any]:
        """Idempotently revoke execution while retaining read-only CTP access."""
        operation = "disarm_execution"
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        # A public disarm is a revocation boundary even when the native gate
        # has already been disconnected or cannot report its current state.
        self._invalidate_ctp_execution_authorizations()
        generation = None
        failure = None
        try:
            state = self._ctp_execution_gate_state(
                exchange_name,
                operation=operation,
            )
            generation = state.get("connection_generation")
            self._disarm_ctp_execution_gate(
                exchange_name,
                reason,
                operation=operation,
            )
        except Exception as exc:
            failure = exc
        if failure is not None:
            self._reset_ctp_execution_stream(exchange_name)
            raise failure
        result = session.disarm_execution(reason, generation=generation)
        try:
            if not self._ctp_execution_stream_ready(exchange_name):
                self._prepare_ctp_execution_stream(
                    exchange_name,
                    operation=operation,
                )
        except Exception:
            self._reset_ctp_execution_stream(exchange_name)
            raise
        return result

    def get_ctp_session_state(self, exchange_name: str = "CTP___FUTURE") -> dict[str, Any]:
        """Return CTP auth/login/settlement evidence without exposing the native client."""
        if self.transport_mode is not TransportMode.DIRECT:
            raise CapabilityNotSupportedError(
                "get_ctp_session_state",
                detail="CTP session state requires direct transport",
            )
        if str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() != "CTP":
            raise CapabilityNotSupportedError(
                "get_ctp_session_state", detail=f"{exchange_name} is not a CTP provider"
            )
        feed = self.exchange_feeds.get(exchange_name)
        method = getattr(feed, "get_session_state", None)
        if not callable(method):
            raise CapabilityNotSupportedError(
                "get_ctp_session_state", detail="CTP feed has no session-state contract"
            )
        from bt_api_ctp.query_result_evidence import project_ctp_session_state

        return project_ctp_session_state(method())

    def query_ctp_result(
        self,
        exchange_name: str,
        query_type: str,
        **kwargs: Any,
    ) -> Any:
        """Run one typed, completion-aware CTP read query."""
        if self.transport_mode is not TransportMode.DIRECT:
            raise CapabilityNotSupportedError(
                "query_ctp_result", detail="typed CTP queries require direct transport"
            )
        if str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() != "CTP":
            raise CapabilityNotSupportedError(
                "query_ctp_result", detail=f"{exchange_name} is not a CTP provider"
            )
        from bt_api_ctp.query_result_evidence import ctp_query_method_name

        method_name = ctp_query_method_name(query_type)
        feed = self.exchange_feeds.get(exchange_name)
        method = getattr(feed, method_name, None)
        if not callable(method):
            raise CapabilityNotSupportedError(
                "query_ctp_result", detail=f"CTP feed has no {method_name} contract"
            )
        return method(**kwargs)

    def verify_ctp_settlement(
        self,
        exchange_name: str = "CTP___FUTURE",
        *,
        timeout: float = 5.0,
    ) -> Any:
        """Verify an account/day server record and promote only that live session."""
        if self.transport_mode is not TransportMode.DIRECT:
            raise CapabilityNotSupportedError(
                "verify_ctp_settlement",
                detail="CTP settlement verification requires direct transport",
            )
        if str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() != "CTP":
            raise CapabilityNotSupportedError(
                "verify_ctp_settlement", detail=f"{exchange_name} is not a CTP provider"
            )
        from bt_api_ctp.query_result_evidence import ctp_query_method_name

        feed = self.exchange_feeds.get(exchange_name)
        method = getattr(feed, ctp_query_method_name("settlement_confirmation"), None)
        if not callable(method):
            raise CapabilityNotSupportedError(
                "verify_ctp_settlement", detail="CTP feed cannot verify settlement"
            )
        return method(timeout=timeout)

    def confirm_ctp_settlement(
        self,
        exchange_name: str = "CTP___FUTURE",
        *,
        timeout: float = 5.0,
    ) -> bool:
        """Fail closed: public callers cannot authorize a terminal CTP write.

        A settlement confirmation is not a read preflight action.  This
        iteration has no production core signer, so state hashes, a managed
        capability, and this public method itself are deliberately insufficient
        to issue the independent one-shot native settlement authorization.
        """
        if self.transport_mode is not TransportMode.DIRECT:
            raise CapabilityNotSupportedError(
                "confirm_ctp_settlement",
                detail="CTP settlement requires direct transport",
            )
        if str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() != "CTP":
            raise CapabilityNotSupportedError(
                "confirm_ctp_settlement",
                detail=f"{exchange_name} is not a CTP provider",
            )
        self._reject_simnow_profile_write(exchange_name, "confirm_ctp_settlement")
        raise NormalizedApiError(
            "confirm_ctp_settlement",
            "ctp_settlement_authorization_required",
            definite_reject=True,
        )

    def get_data_queue(self, exchange_name: str) -> Any | None:
        """Get the data queue for the specified exchange.

        The data queue receives market data and order updates from WebSocket streams.

        Args:
            exchange_name: Exchange identifier (e.g., "BINANCE___SPOT")

        Returns:
            Queue instance if exchange exists, None otherwise

        Example:
            >>> api = BtApi({"BINANCE___SPOT": {...}})
            >>> queue = api.get_data_queue("BINANCE___SPOT")
            >>> data = queue.get()  # Blocks until data arrives
        """
        data_queue = self.data_queues.get(exchange_name)
        if data_queue is None:
            self.log(f"exchange_name: {exchange_name} does not exist", level="error")
        elif (
            _is_ctp_exchange(exchange_name)
            and getattr(self, "transport_mode", None) is TransportMode.DIRECT
        ):
            return self._ctp_market_consumer_queue(exchange_name, data_queue)
        return data_queue

    def _initialize_event_waker_state(self) -> None:
        self._event_waker_condition = threading.Condition()
        self._event_wakers: dict[object, _EventWakerSubscription] = {}
        self._event_waker_closed = False
        self._event_waker_dispatch_context = threading.local()
        self._event_waker_callback_errors = 0
        self._event_waker_error_reported = False

    def register_event_waker(self, callback: Callable[[], Any]) -> object:
        """Register a bounded, no-argument wake callback for CTP stream events.

        Callbacks run after the SDK producer has successfully enqueued an event,
        after a CTP connection-state transition, and once after a successful
        client close. A state-change wake is only a hint to recheck the source;
        it does not prove that the client is terminally closed. Callbacks should
        return quickly; exceptions are isolated from producer and shutdown paths.
        """
        if not callable(callback):
            raise TypeError("event waker callback must be callable")
        with self._event_waker_condition:
            if self._event_waker_closed:
                raise RuntimeError("bt_api_event_waker_client_closed")
            if len(self._event_wakers) >= _MAX_EVENT_WAKERS:
                raise RuntimeError("bt_api_event_waker_capacity_exceeded")
            token = object()
            self._event_wakers[token] = _EventWakerSubscription(callback)
            return token

    def unregister_event_waker(self, token: object) -> bool:
        """Unregister an exact token; repeated or unknown tokens are harmless."""
        if type(token) is not object:
            return False
        with self._event_waker_condition:
            subscription = self._event_wakers.pop(token, None)
            if subscription is None:
                return False
            subscription.active = False
            subscription.callback = None
            if getattr(self._event_waker_dispatch_context, "depth", 0) == 0:
                while subscription.in_flight:
                    self._event_waker_condition.wait()
            return True

    def _notify_event_wakers(self, *, closing: bool = False) -> None:
        """Wake registered readers after enqueue or after terminal close."""
        with self._event_waker_condition:
            if closing:
                if self._event_waker_closed:
                    return
                self._event_waker_closed = True
            elif self._event_waker_closed:
                return
            subscriptions = tuple(self._event_wakers.items())
            for _token, subscription in subscriptions:
                if subscription.active:
                    subscription.in_flight += 1

        for _token, subscription in subscriptions:
            with self._event_waker_condition:
                callback = subscription.callback if subscription.active else None
                if callback is None:
                    subscription.in_flight -= 1
                    if not subscription.in_flight:
                        self._event_waker_condition.notify_all()
                    continue
            depth = getattr(self._event_waker_dispatch_context, "depth", 0)
            self._event_waker_dispatch_context.depth = depth + 1
            try:
                callback()
            except Exception:
                with self._event_waker_condition:
                    self._event_waker_callback_errors = min(
                        self._event_waker_callback_errors + 1,
                        _MAX_EVENT_WAKER_CALLBACK_ERRORS,
                    )
                    report_failure = not self._event_waker_error_reported
                    self._event_waker_error_reported = True
                if report_failure:
                    with suppress(Exception):
                        self.logger.warning(
                            "event waker callback failed; subsequent failures suppressed"
                        )
            finally:
                self._event_waker_dispatch_context.depth = depth
                with self._event_waker_condition:
                    subscription.in_flight -= 1
                    if not subscription.in_flight and not subscription.active:
                        subscription.callback = None
                    self._event_waker_condition.notify_all()

        if closing:
            with self._event_waker_condition:
                for token, subscription in subscriptions:
                    if self._event_wakers.get(token) is subscription:
                        self._event_wakers.pop(token, None)
                    subscription.active = False
                    if not subscription.in_flight:
                        subscription.callback = None
                if getattr(self._event_waker_dispatch_context, "depth", 0) == 0:
                    while any(subscription.in_flight for _, subscription in subscriptions):
                        self._event_waker_condition.wait()
                for _, subscription in subscriptions:
                    if not subscription.in_flight:
                        subscription.callback = None

    def subscribe(self, dataname: str, topics: list[dict[str, Any]]) -> None:
        """通过 ExchangeRegistry 查找订阅处理函数，无需硬编码交易所类型"""
        exchange, asset_type, symbol = self._parse_dataname(dataname)
        exchange_name = exchange + DATANAME_SEPARATOR + asset_type
        normalized_topics, subscribe_bar_num = self._normalize_subscribe_topics(topics)
        if self.transport_mode is TransportMode.ZMQ:
            self._backend.subscribe(exchange_name, symbol, normalized_topics)
            self.subscribe_bar_num += subscribe_bar_num
            return
        exchange_params = self._copy_exchange_params(self.exchange_kwargs.get(exchange_name, {}))
        data_queue = self.data_queues.get(exchange_name)
        if data_queue is None:
            self.log(f"exchange_name: {exchange_name} does not exist", level="error")
            raise SubscribeError(exchange_name, detail="exchange not registered")
        if _is_ctp_exchange(exchange_name):
            data_queue = self._ctp_market_stream_ingress_queue(exchange_name, data_queue)

        subscribe_handler = ExchangeRegistry.get_stream_class(exchange_name, "subscribe")
        if subscribe_handler is None:
            raise CapabilityNotSupportedError(
                "subscribe", detail=f"no stream handler registered for {exchange_name}"
            )
        subscribe_handler(data_queue, exchange_params, normalized_topics, self)
        self.subscribe_bar_num += subscribe_bar_num

    def push_bar_data_to_queue(self, exchange_name: str, data: Any) -> None:
        data_queue = self.get_data_queue(exchange_name)
        if data_queue is None:
            raise ExchangeNotFoundError(exchange_name, list(self.data_queues.keys()))
        bar_list = data.get_data()
        for bar in bar_list:
            data_queue.put(bar)

    def get_event_bus(self) -> EventBus:
        """获取事件总线实例"""
        return self.event_bus

    def put_ticker(self, ticker_data: Any, exchange_name: str | None = None) -> Any:
        """Push a simulated ticker update into the event bus and optional exchange queue."""

        if exchange_name is not None and _is_ctp_exchange(exchange_name):
            raise CapabilityNotSupportedError(
                "put_ticker",
                detail="CTP market-data queues accept only managed native stream ingress",
                definite_reject=True,
            )
        self.event_bus.emit("ticker", ticker_data)
        if exchange_name is not None and exchange_name in self.data_queues:
            self.data_queues[exchange_name].put(ticker_data)
        return ticker_data

    def list_exchanges(self) -> list[str]:
        """列出所有已添加的交易所"""
        return list(
            self.exchange_kwargs
            if self.transport_mode is TransportMode.ZMQ
            else self.exchange_feeds
        )

    def close(self) -> None:
        """Release transports and the execution lock, including on close failure.

        The market clock verifier's background SNTP refresh stops here too; its
        current evidence is kept (it still expires on its own validity).
        """
        failure = None
        try:
            self._close_transports()
        except Exception as exc:
            if self._execution_session is not None:
                from ._normalization import normalize_error

                failure = normalize_error(exc, "close")
            else:
                raise
        else:
            self._notify_event_wakers(closing=True)
        finally:
            self.event_bus.off("ws.connected", self._on_websocket_connected)
            verifier = getattr(self, "_market_clock_verifier", None)
            if verifier is not None:
                verifier.stop_background_refresh()
            coordinator = getattr(self, "_direct_write_coordinator", None)
            if coordinator is not None:
                # Providers may own threads (CTP settlement readback monitor).
                coordinator.close()
            if self._execution_session is not None:
                self._execution_session.close()
        if failure is not None:
            raise failure from None

    def _close_transports(self) -> None:
        """Close all exchange feeds (WebSocket streams + HTTP clients)."""
        market_stream_error = None
        market_stream_owner = getattr(self, "_market_stream_owner", None)
        if market_stream_owner is not None:
            try:
                market_stream_owner.close()
            except Exception as exc:
                code = getattr(exc, "code", None)
                market_stream_error = (
                    "market stream: "
                    + code
                    if type(code) is str and code.startswith("MARKET_STREAM_")
                    else "market stream: close unverified"
                )
        if market_stream_error is not None:
            # Do not disconnect the selected provider feed while a live or
            # uncertain stream may still own a projector on its owner thread.
            raise RuntimeError(market_stream_error)
        if self.transport_mode is TransportMode.ZMQ:
            self._backend.close()
            self._normalized_event_pending.clear()
            return
        errors: list[str] = []
        if getattr(self, "_execution_session", None) is not None:
            for exchange_name in tuple(self.exchange_feeds):
                if str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() != "CTP":
                    continue
                try:
                    self._disarm_ctp_execution_gate(
                        exchange_name,
                        "execution_session_closed",
                        operation="close",
                    )
                except Exception as exc:
                    errors.append(f"{exchange_name} gate: {type(exc).__name__}: {exc}")
        remaining_streams: list[Any] = []
        for index, stream in enumerate(self._subscription_streams):
            stream_failed = False
            stop = getattr(stream, "stop", None)
            disconnect = getattr(stream, "disconnect", None)
            methods = [method for method in (stop, disconnect) if callable(method)]
            if not methods:
                close = getattr(stream, "close", None)
                methods = [close] if callable(close) else []
            for method in methods:
                try:
                    method()
                except Exception as exc:
                    stream_failed = True
                    errors.append(f"subscription {index}: {type(exc).__name__}: {exc}")
            if stream_failed:
                remaining_streams.append(stream)
        self._subscription_streams = remaining_streams
        if not remaining_streams:
            self._subscription_flags.clear()
        for exchange_name, feed in self.exchange_feeds.items():
            try:
                if hasattr(feed, "disconnect"):
                    feed.disconnect()
            except Exception as exc:
                errors.append(f"{exchange_name}: {type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError("failed to close feeds: " + "; ".join(errors))

    def __enter__(self) -> BtApi:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.close()

    async def __aenter__(self) -> BtApi:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        await self.async_close()

    async def async_close(self) -> None:
        """Close all exchange feeds (WebSocket streams + HTTP clients)."""
        # Feed.disconnect() is sync-only; no async variant exists in the base class.
        self.close()

    @staticmethod
    def list_available_exchanges() -> list[str]:
        """列出所有已注册可用的交易所"""
        return ExchangeRegistry.list_exchanges()

    # ══════════════════════════════════════════════════════════════
    # 统一接口 — 直接在 BtApi 上调用，自动路由到对应交易所的 Feed
    # 用法:
    #   bt_api.get_tick("BINANCE___SWAP", "BTC-USDT")
    #   bt_api.make_order("OKX___SWAP", "BTC-USDT", 0.001, 50000, "limit")
    # 原有接口 (get_request_api -> feed.method) 保持不变
    # ══════════════════════════════════════════════════════════════

    def _get_feed(self, exchange_name: str) -> Any:
        """获取指定交易所的 Feed 实例"""
        feed = self.exchange_feeds.get(exchange_name)
        if feed is None:
            raise ExchangeNotFoundError(exchange_name, list(self.exchange_feeds.keys()))
        return feed

    # ── 行情查询（同步）────────────────────────────────────────────

    def get_tick(
        self, exchange_name: str, symbol: str, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """获取最新行情
        :param exchange_name: 交易所标识, 如 "BINANCE___SWAP"
        :param symbol: 交易对, 如 "BTC-USDT"
        """
        if kwargs.pop("normalized", False):
            return self._normalized_call(
                "get_tick",
                exchange_name,
                symbol,
                lambda: self.get_tick(exchange_name, symbol, extra_data=extra_data, **kwargs),
            )
        consistency = self._pop_consistency(kwargs)
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("get_tick", extra_data, kwargs)
            return self._backend.get_tick(exchange_name, symbol, consistency=consistency)
        return self._backend.get_tick(
            exchange_name,
            symbol,
            consistency=consistency,
            extra_data=extra_data,
            **kwargs,
        )

    def get_depth(
        self,
        exchange_name: str,
        symbol: str,
        count: int = 20,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """获取深度数据
        :param exchange_name: 交易所标识
        :param symbol: 交易对
        :param count: 深度档数
        """
        if kwargs.pop("normalized", False):
            return self._normalized_call(
                "get_depth",
                exchange_name,
                symbol,
                lambda: self.get_depth(
                    exchange_name, symbol, count, extra_data=extra_data, **kwargs
                ),
            )
        consistency = self._pop_consistency(kwargs)
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("get_depth", extra_data, kwargs)
            return self._backend.get_depth(exchange_name, symbol, count, consistency=consistency)
        return self._backend.get_depth(
            exchange_name,
            symbol,
            count,
            consistency=consistency,
            extra_data=extra_data,
            **kwargs,
        )

    def get_kline(
        self,
        exchange_name: str,
        symbol: str,
        period: str,
        count: int = 20,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """获取K线数据
        :param exchange_name: 交易所标识
        :param symbol: 交易对
        :param period: K线周期, 如 "1m", "5m", "1H", "1D"
        :param count: K线数量
        """
        if kwargs.pop("normalized", False):
            return self._normalized_call(
                "get_kline",
                exchange_name,
                symbol,
                lambda: self.get_kline(
                    exchange_name,
                    symbol,
                    period,
                    count,
                    extra_data=extra_data,
                    **kwargs,
                ),
            )
        consistency = self._pop_consistency(kwargs)
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("get_kline", extra_data, kwargs)
            return self._backend.get_kline(
                exchange_name, symbol, period, count, consistency=consistency
            )
        return self._backend.get_kline(
            exchange_name,
            symbol,
            period,
            count,
            consistency=consistency,
            extra_data=extra_data,
            **kwargs,
        )

    def _direct_read_method(self, exchange_name: str, operation: str, *aliases: str) -> Any:
        """Resolve an optional read operation without exposing the feed to callers."""
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                operation,
                detail="transport=zmq has no forwarding protocol for this read operation",
            )
        feed = self._get_feed(exchange_name)
        for name in (operation, *aliases):
            method = getattr(feed, name, None)
            if callable(method):
                return method
        raise CapabilityNotSupportedError(
            operation,
            detail=f"transport=direct; {exchange_name} does not implement this operation",
        )

    def get_exchange_info(
        self,
        exchange_name: str,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Read native instrument metadata through the direct transport.

        Returns the feed's native response unchanged. Binance exchange info
        includes ``symbols[].filters``; OKX instruments include ``ctVal``,
        ``ctValCcy``, ``lotSz``, ``minSz`` and ``tickSz``. Callers must use these
        fields to distinguish base-asset quantities from contract quantities.
        The forwarding protocol does not currently support this operation.
        """
        if kwargs.pop("normalized", False):
            cache_key = (exchange_name, symbol)
            if self._execution_session is not None and cache_key in self._instrument_cache:
                return deepcopy(self._instrument_cache[cache_key])
            result = self._normalized_call(
                "get_exchange_info",
                exchange_name,
                symbol,
                lambda: self.get_exchange_info(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                ),
            )
            if self._execution_session is not None:
                self._instrument_cache[cache_key] = deepcopy(result)
            return result
        method = self._direct_read_method(exchange_name, "get_exchange_info")
        return method(symbol, extra_data=extra_data, **kwargs)

    def get_funding_rate(
        self, exchange_name: str, symbol: str, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """Read perpetual funding data, preserving the feed-native response and units."""
        if kwargs.pop("normalized", False):
            return self._normalized_call(
                "get_funding_rate",
                exchange_name,
                symbol,
                lambda: self.get_funding_rate(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                ),
            )
        method = self._direct_read_method(exchange_name, "get_funding_rate")
        return method(symbol, extra_data=extra_data, **kwargs)

    def get_instrument_spec(
        self,
        exchange_name: str,
        symbol: str,
        extra_data: Any = None,
        *,
        refresh: bool = False,
        **kwargs: Any,
    ) -> InstrumentSpec:
        """Return strict Decimal quantity/price rules while preserving legacy reads."""
        from ._normalization import instrument_spec

        cache_key = (exchange_name, symbol)
        if not refresh and cache_key in self._instrument_spec_cache:
            return self._instrument_spec_cache[cache_key]
        self._validate_required_environment(exchange_name, operation="get_instrument_spec")
        try:
            raw = self.get_exchange_info(
                exchange_name,
                symbol,
                extra_data=extra_data,
                **kwargs,
            )
            result = instrument_spec(raw, exchange_name, symbol)
        except Exception as exc:
            result = InstrumentSpec(
                exchange_name=exchange_name,
                symbol=symbol,
                asset_type=exchange_name.partition("___")[2].lower(),
                base_currency="",
                quote_currency="",
                contract_type="unknown",
                linear=True,
                contract_value=None,
                contract_multiplier=None,
                price_tick=None,
                quantity_step=None,
                min_quantity=None,
                max_quantity=None,
                min_notional=None,
                quantity_unit="native",
                status="unknown",
                freshness=Freshness(
                    source="unavailable",
                    observed_at=datetime.now(UTC),
                    stale=True,
                    stale_reason="instrument_rules_unavailable",
                ),
                raw_rule_fingerprint="",
                source="unavailable",
                available=False,
                unavailable_reason=getattr(exc, "code", "instrument_rules_unavailable"),
            )
        if result.available:
            self._instrument_spec_cache[cache_key] = result
        return result

    def get_fee_schedule(
        self,
        exchange_name: str,
        symbol: str,
        account_id: str,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> FeeSchedule:
        """Read account fee rates as an explicit available/unavailable snapshot."""
        from ._normalization import _fee_projection, fee_schedule

        self._validate_required_environment(exchange_name, operation="get_fee_schedule")
        try:
            method = self._direct_read_method(exchange_name, "get_fee")
            projection = _fee_projection(exchange_name)
            if projection is not None:
                return projection.read(
                    method, self.get_exchange_info, exchange_name, symbol,
                    account_id, extra_data, kwargs,
                )
            native = method(symbol, extra_data=extra_data, **kwargs)
            return fee_schedule(native, exchange_name, symbol, account_id)
        except Exception as exc:
            reason = self._fee_schedule_failure_reason(exc, exchange_name)
            return FeeSchedule(
                exchange_name=exchange_name,
                symbol=symbol,
                account_id=account_id,
                maker_rate=None,
                taker_rate=None,
                currency=None,
                source="unavailable",
                freshness=Freshness(
                    source="unavailable",
                    observed_at=datetime.now(UTC),
                    stale=True,
                    stale_reason=reason,
                ),
                available=False,
                unavailable_reason=reason,
            )

    @staticmethod
    def _fee_schedule_failure_reason(exc: Exception, exchange_name: str) -> str:
        """Classify a fee read without retaining transport messages or signed URLs."""
        from ._normalization import normalize_error

        if isinstance(exc, CapabilityNotSupportedError):
            return "fee_capability_not_supported"
        normalized = normalize_error(
            exc,
            "get_fee_schedule",
            exchange_name=exchange_name,
        )
        code = str(getattr(normalized, "code", ""))
        category = getattr(normalized, "category", None)
        if code.startswith("fee_"):
            return code
        if category == "parameter":
            return f"fee_parameter_error_{code}"
        if category == "auth":
            return f"fee_auth_error_{code}"
        if code in {"TimeoutError", "ConnectionError", "OSError"}:
            return "fee_transport_failed"
        if code.lstrip("-").isdigit():
            return f"fee_api_error_{code}"
        if isinstance(exc, ValueError):
            return "fee_parameter_error_local_validation"
        return "fee_read_failed"

    @staticmethod
    def _unavailable_funding_snapshot(
        exchange_name: str,
        symbol: str,
        reason: str,
    ) -> FundingSnapshot:
        """Return a credential-safe fail-closed funding result."""
        return FundingSnapshot(
            exchange_name=exchange_name,
            symbol=symbol,
            rate=None,
            next_funding_time=None,
            settlement_interval_seconds=None,
            source="unavailable",
            freshness=Freshness(
                source="unavailable",
                observed_at=datetime.now(UTC),
                stale=True,
                stale_reason=reason,
            ),
            available=False,
            unavailable_reason=reason,
        )

    def get_funding_snapshot(
        self,
        exchange_name: str,
        symbol: str,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> FundingSnapshot:
        """Read the next funding settlement as a Decimal typed snapshot."""
        from ._normalization import funding_snapshot, rows, seconds
        from .venue_normalization_policy import normalization_policy

        self._validate_required_environment(exchange_name, operation="get_funding_snapshot")

        try:
            native = self.get_funding_rate(
                exchange_name,
                symbol,
                extra_data=extra_data,
                **kwargs,
            )
        except Exception:
            # A failed transport call says nothing about the validity of the
            # last successful snapshot. Consumers may retain that snapshot
            # only until its original freshness/schedule deadline.
            return self._unavailable_funding_snapshot(
                exchange_name,
                symbol,
                "funding_transport_failed",
            )

        try:
            policy = normalization_policy(exchange_name)
            select_payload = getattr(policy, "select_funding_payload", None)
            if callable(select_payload):
                native = select_payload(native, exchange_name, symbol, rows=rows)
            result = funding_snapshot(native, exchange_name, symbol)
        except (NormalizedApiError, CapabilityNotSupportedError):
            # Vendor error payloads are raised by the normalizer. They are a
            # failed read, rather than evidence that a prior schedule changed.
            return self._unavailable_funding_snapshot(
                exchange_name,
                symbol,
                "funding_transport_failed",
            )
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
            # A response that arrived but cannot satisfy the typed contract
            # invalidates the current read and must fail closed.
            return self._unavailable_funding_snapshot(
                exchange_name,
                symbol,
                "funding_payload_invalid",
            )
        complete_schedule = getattr(policy, "complete_funding_schedule", None)
        if (
            callable(complete_schedule)
            and result.available is False
            and result.unavailable_reason == "funding_interval_missing"
        ):
            return complete_schedule(
                exchange_name,
                symbol,
                native,
                result,
                extra_data,
                get_feed=self._get_feed,
                rows=rows,
                funding_snapshot=funding_snapshot,
                seconds=seconds,
                unavailable_snapshot=self._unavailable_funding_snapshot,
                capability_error_type=CapabilityNotSupportedError,
                normalized_error_type=NormalizedApiError,
            )
        return result

    def get_trading_readiness(
        self,
        exchange_name: str,
        symbol: str,
        account_id: str,
        quantity_native: Decimal | None = None,
        *,
        margin_mode: str = "cross",
        position_mode: str | None = None,
        extra_data: Any = None,
    ) -> TradingReadiness:
        """Return a conservative typed preflight for OKX or Binance perpetuals."""
        self._validate_required_environment(exchange_name, operation="get_trading_readiness")
        environment = self.get_environment_info(exchange_name)
        observed = Freshness(source="exchange", observed_at=datetime.now(UTC))
        try:
            if quantity_native is None:
                raise ValueError("quantity_required")
            legacy = self.get_order_readiness(
                exchange_name,
                symbol,
                quantity_native,
                margin_mode=margin_mode,
                position_mode=position_mode,
                extra_data=extra_data,
            )
            leverage_values = [
                Decimal(str(value))
                for value in legacy.get("leverage_by_position_side", {}).values()
                if value is not None
            ]
            if legacy.get("leverage") is not None:
                leverage_values.append(Decimal(str(legacy["leverage"])))
            maxima = [
                Decimal(str(value))
                for value in (
                    legacy.get("max_size"),
                    legacy.get("max_buy"),
                    legacy.get("max_sell"),
                )
                if value is not None
            ]
            reasons = list(legacy.get("reasons", ()))
            if environment.get("verified") is not True:
                reasons.append("environment_unverified")
            return TradingReadiness(
                exchange_name=exchange_name,
                symbol=symbol,
                account_id=account_id,
                environment=str(environment.get("environment", "unknown")),
                can_trade=legacy.get("checks", {}).get("trading_permission"),
                position_mode=legacy.get("position_mode"),
                instrument_status=legacy.get("instrument_state"),
                max_size=min(maxima) if maxima else None,
                leverage=min(leverage_values) if leverage_values else None,
                margin_mode=margin_mode,
                definite_failure=bool(legacy.get("definite_failure")),
                blocked_reasons=tuple(dict.fromkeys(reasons)),
                source=f"{exchange_name.partition('___')[0].lower()}_readiness",
                freshness=observed,
                raw={"checks": dict(legacy.get("checks", {}))},
            )
        except Exception:
            return TradingReadiness(
                exchange_name=exchange_name,
                symbol=symbol,
                account_id=account_id,
                environment=str(environment.get("environment", "unknown")),
                can_trade=None,
                position_mode=None,
                instrument_status=None,
                max_size=None,
                leverage=None,
                margin_mode=margin_mode,
                definite_failure=False,
                blocked_reasons=("readiness_unavailable",),
                source="unavailable",
                freshness=Freshness(
                    source="unavailable",
                    observed_at=datetime.now(UTC),
                    stale=True,
                    stale_reason="readiness_read_failed",
                ),
                available=False,
                unavailable_reason="readiness_read_failed",
            )

    async def async_get_trading_readiness(
        self,
        exchange_name: str,
        symbol: str,
        account_id: str,
        quantity_native: Decimal | None = None,
        *,
        margin_mode: str = "cross",
        position_mode: str | None = None,
        extra_data: Any = None,
    ) -> TradingReadiness:
        """Run the same typed readiness contract without blocking the event loop."""
        return await asyncio.to_thread(
            self.get_trading_readiness,
            exchange_name,
            symbol,
            account_id,
            quantity_native,
            margin_mode=margin_mode,
            position_mode=position_mode,
            extra_data=extra_data,
        )

    def _mark_position_mode_reconcile_required(self, exchange_name: str, reason: str) -> None:
        """Invalidate a mode snapshot after an outcome that needs reconciliation."""
        with self._position_mode_lock:
            self._position_modes.pop(exchange_name, None)
            self._position_mode_reconcile_required[exchange_name] = reason

    def _record_verified_position_mode(self, exchange_name: str, position_mode: str) -> None:
        """Publish one authoritative mode snapshot and release its safety latch."""
        with self._position_mode_lock:
            self._position_modes[exchange_name] = position_mode
            self._position_mode_reconcile_required.pop(exchange_name, None)

    def _begin_position_mode_placement(
        self,
        exchange_name: str,
        request: OrderRequest | None = None,
        *,
        resolve_mode: bool = False,
    ) -> tuple[OrderRequest | None, bool]:
        """Atomically enforce the selected provider's placement mode fence."""
        from bt_api_py.position_mode_composition import position_mode_guard_required

        if not position_mode_guard_required(exchange_name):
            return request, False
        with self._position_mode_lock:
            if exchange_name in self._position_mode_reconcile_required:
                raise NormalizedApiError(
                    "make_order",
                    "position_mode_reconcile_required",
                    definite_reject=True,
                )
            if resolve_mode:
                if request is None:
                    raise TypeError("normalized placement requires an OrderRequest")
                resolved_request: OrderRequest = request
            else:
                resolved_request = request
            if resolve_mode and resolved_request.position_mode is None:
                self._validate_required_environment(exchange_name, operation="get_position_mode")
                mode = self._position_modes.get(exchange_name)
                if mode is None:
                    mode = self.get_position_mode(exchange_name, normalized=True)["position_mode"]
                request = replace(resolved_request, position_mode=mode)
            self._position_mode_active_placements[exchange_name] = (
                self._position_mode_active_placements.get(exchange_name, 0) + 1
            )
            return request, True

    def _end_position_mode_placement(self, exchange_name: str) -> None:
        """Release a crypto placement registration after the provider call ends."""
        with self._position_mode_lock:
            active = self._position_mode_active_placements.get(exchange_name, 0)
            if active <= 1:
                self._position_mode_active_placements.pop(exchange_name, None)
            else:
                self._position_mode_active_placements[exchange_name] = active - 1

    def get_account_config(
        self, exchange_name: str, extra_data: Any = None, *, normalized: bool = False
    ) -> Any:
        """Read account mode and explicit trading permission without changing either.

        Native method aliases are declared by the selected provider.
        """
        if normalized:
            with self._position_mode_lock:
                result = self._normalized_call(
                    "get_account_config",
                    exchange_name,
                    None,
                    lambda: self.get_account_config(exchange_name, extra_data=extra_data),
                )
                self._record_verified_position_mode(exchange_name, result["position_mode"])
                return result
        from bt_api_py.account_config_composition import account_config_read_aliases

        aliases = account_config_read_aliases(exchange_name)
        method = self._direct_read_method(exchange_name, "get_account_config", *aliases)
        return method(extra_data=extra_data)

    def get_position_mode(
        self, exchange_name: str, extra_data: Any = None, *, normalized: bool = False
    ) -> Any:
        """Read the native position mode without modifying it.

        Binance returns ``dualSidePosition``. OKX reports ``posMode`` inside
        its account configuration response. Neither format is normalized to
        a boolean, and unsupported feeds fail explicitly.
        """
        if normalized:
            with self._position_mode_lock:
                result = self._normalized_call(
                    "get_position_mode",
                    exchange_name,
                    None,
                    lambda: self.get_position_mode(exchange_name, extra_data=extra_data),
                )
                self._record_verified_position_mode(exchange_name, result["position_mode"])
                return result
        from bt_api_py.position_mode_composition import position_mode_read_aliases

        aliases = position_mode_read_aliases(exchange_name)
        method = self._direct_read_method(exchange_name, "get_position_mode", *aliases)
        return method(extra_data=extra_data)

    def set_position_mode(
        self,
        exchange_name: str,
        position_mode: str,
        extra_data: Any = None,
        *,
        normalized: bool = True,
        **kwargs: Any,
    ) -> PositionModeUpdate:
        """Set and read back an account-wide perpetual position mode.

        The public contract accepts only ``net`` and ``dual_side``. A provider
        acknowledgement is insufficient: the method reads the position mode
        back from the account and returns only after the requested value is
        observed. An uncertain outcome invalidates the local mode cache and
        blocks normalized placements until a fresh normalized read verifies it.
        """
        operation = "set_position_mode"
        if normalized is not True:
            raise NormalizedApiError(operation, "normalized_result_required", definite_reject=True)
        if not isinstance(position_mode, str) or position_mode not in {
            "net",
            "dual_side",
        }:
            raise NormalizedApiError(operation, "invalid_position_mode", definite_reject=True)
        from bt_api_py.position_mode_composition import resolve_position_mode_writer

        resolve_position_mode_writer(exchange_name)
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                operation,
                detail="transport=zmq has no forwarding protocol for this operation",
                definite_reject=True,
            )
        if self._execution_session is not None:
            self._execution_session.require_write(operation, venue=exchange_name)

        with self._position_mode_lock:
            if self._position_mode_active_placements.get(exchange_name, 0):
                raise NormalizedApiError(
                    operation,
                    "position_mode_placement_inflight",
                    definite_reject=True,
                )
            try:
                acknowledgement = self._normalized_call(
                    operation,
                    exchange_name,
                    None,
                    lambda: self._backend.set_position_mode(
                        exchange_name,
                        position_mode,
                        extra_data=extra_data,
                        **kwargs,
                    ),
                )
            except Exception as exc:
                if getattr(exc, "execution_unknown", False):
                    self._mark_position_mode_reconcile_required(
                        exchange_name, "mutation_outcome_unknown"
                    )
                raise
            self._mark_position_mode_reconcile_required(exchange_name, "verification_required")
            verification_failed = False
            try:
                observed = self._normalized_call(
                    "get_position_mode",
                    exchange_name,
                    None,
                    lambda: self.get_position_mode(
                        exchange_name,
                        extra_data=extra_data,
                    ),
                )
            except Exception:
                verification_failed = True
                observed = None
            if verification_failed:
                self._mark_position_mode_reconcile_required(exchange_name, "verification_failed")
                raise NormalizedApiError(
                    operation,
                    "position_mode_verification_failed",
                    execution_unknown=True,
                ) from None
            if observed["position_mode"] != position_mode:
                self._mark_position_mode_reconcile_required(exchange_name, "verification_mismatch")
                raise NormalizedApiError(
                    operation,
                    "position_mode_verification_mismatch",
                    execution_unknown=True,
                ) from None
            result = PositionModeUpdate(
                exchange_name=exchange_name,
                requested_mode=position_mode,
                position_mode=observed["position_mode"],
                acknowledged=acknowledgement["acknowledged"],
                verified=True,
                cache_updated=True,
                source=f"{exchange_name.partition('___')[0].lower()}_account_readback",
                observed_at=datetime.now(UTC),
            )
            self._record_verified_position_mode(exchange_name, position_mode)
            return result


    def get_order_readiness(
        self,
        exchange_name: str,
        symbol: str,
        quantity_native: Decimal | float | int | str,
        *,
        margin_mode: str = "cross",
        position_mode: str | None = None,
        normalized: bool = True,
        extra_data: Any = None,
    ) -> dict[str, Any]:
        """Inspect perpetual order prerequisites without submitting an order.

        Quantity semantics belong to the selected provider. A ready result
        is an observation and does not prove a subsequent order will execute.
        """
        operation = "get_order_readiness"
        from .order_readiness_composition import readiness_normalizers, resolve_readiness_provider

        readiness_provider = resolve_readiness_provider(exchange_name)
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                operation,
                detail="transport=zmq has no forwarding protocol for readiness reads",
            )
        if not normalized:
            raise CapabilityNotSupportedError(
                operation,
                detail="readiness is available only as a normalized snapshot",
            )
        if margin_mode not in {"cross", "isolated"}:
            raise NormalizedApiError(operation, "invalid_margin_mode", definite_reject=True)
        session = self._execution_session
        if session is not None and session.config["market_data_only"]:
            raise CapabilityNotSupportedError(operation, detail="market-data-only session")
        self._validate_required_environment(exchange_name, operation=operation)

        from ._normalization import normalize_error

        def options() -> Any:
            return deepcopy(extra_data)

        failure = None
        try:
            account_config = self._backend.get_account_config(exchange_name, extra_data=options())
            return readiness_provider(
                self._backend, exchange_name, symbol, quantity_native,
                margin_mode=margin_mode, position_mode=position_mode,
                account_config=account_config, extra_data=extra_data,
                normalizers=readiness_normalizers(exchange_name),
            )
        except Exception as exc:
            failure = normalize_error(exc, operation, exchange_name=exchange_name)
        raise failure from None

    # ── 交易操作（同步）────────────────────────────────────────────

    def make_order(
        self,
        exchange_name: str,
        symbol: str | OrderRequest,
        volume: float | None = None,
        price: float | None = None,
        order_type: str | None = None,
        offset: str = "open",
        post_only: bool = False,
        client_order_id: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """下单。

        标准形式（v1）：``make_order(exchange_name, OrderRequest(...))``。
        兼容形式：``make_order(exchange_name, symbol, volume, price, "buy-limit")``；
        仅当 order_type 能推导 side（``side-type``）时兼容，裸 ``limit``/``market``
        无法推导 side 时抛 ``LegacyOrderApiError``。

        Iteration 62 direct form: ``make_order(exchange_name, OrderRequest(...),
        pre_dispatch_commit=port, host_scope=...)`` runs the SDK direct write
        workflow (admission -> native reservation -> durable host commit ->
        one-shot grant -> send) and returns a ``DirectWriteOutcome``.
        """
        pre_dispatch_commit = kwargs.pop("pre_dispatch_commit", None)
        if pre_dispatch_commit is not None:
            host_scope = kwargs.pop("host_scope", None) or "bt_api"
            request_summary = kwargs.pop("request_summary", None)
            if not isinstance(symbol, OrderRequest) or kwargs:
                raise NormalizedApiError(
                    "make_order", "typed_request_required", definite_reject=True
                )
            return self.direct_writes().place(
                exchange_name,
                symbol,
                commit_port=pre_dispatch_commit,
                host_scope=host_scope,
                request_summary=request_summary,
            )
        self._reject_simnow_profile_write(exchange_name, "make_order")
        if kwargs.pop("normalized", False):
            budget_capability = kwargs.pop("budget_capability", None)
            if not isinstance(symbol, OrderRequest):
                raise NormalizedApiError(
                    "make_order", "typed_request_required", definite_reject=True
                )
            request = symbol
            self._require_ctp_managed_order_identity(
                "make_order", exchange_name, request, self._execution_session
            )
            resolved_request, mode_guarded = self._begin_position_mode_placement(
                exchange_name,
                request,
                resolve_mode=True,
            )
            assert resolved_request is not None
            request = resolved_request
            try:
                return self._normalized_call(
                    "make_order",
                    exchange_name,
                    request.symbol,
                    lambda: self._make_order_typed(exchange_name, request),
                    request=request,
                    budget_capability=budget_capability,
                )
            finally:
                if mode_guarded:
                    self._end_position_mode_placement(exchange_name)
        if self._execution_session is not None:
            raise NormalizedApiError(
                "make_order",
                "session_requires_normalized_typed_request",
                definite_reject=True,
            )
        _, mode_guarded = self._begin_position_mode_placement(exchange_name)
        try:
            if isinstance(symbol, OrderRequest):
                return self._make_order_typed(exchange_name, symbol)
            return self._make_order_legacy(
                exchange_name,
                symbol,
                volume,
                price,
                order_type,
                offset,
                post_only,
                client_order_id,
                extra_data,
                **kwargs,
            )
        finally:
            if mode_guarded:
                self._end_position_mode_placement(exchange_name)

    def _make_order_typed(self, exchange_name: str, request: OrderRequest) -> Any:
        self._reject_simnow_profile_write(exchange_name, "make_order")
        if _is_ctp_exchange(exchange_name) and (
            request.ctp_order_identity is not None
            or request.managed_intent_id is not None
            or str(request.runtime_order_id or "").startswith("bt-managed-v1:")
        ):
            raise NormalizedApiError(
                "make_order", "ctp_order_dispatch_handoff_unavailable", definite_reject=True
            )
        return self._backend.make_order(exchange_name, request)

    def _make_order_legacy(
        self,
        exchange_name: str,
        symbol: str,
        volume: float | None,
        price: float | None,
        order_type: str | None,
        offset: str,
        post_only: bool,
        client_order_id: str | None,
        extra_data: Any,
        **kwargs: Any,
    ) -> Any:
        if self.transport_mode is TransportMode.DIRECT:
            self._get_feed(exchange_name)  # preserve the legacy direct error ordering
        elif extra_data is not None or kwargs:
            self._reject_zmq_legacy_options("make_order", extra_data, kwargs)
        if volume is None or volume <= 0:
            raise InvalidOrderError(exchange_name, symbol, "volume must be > 0")
        if price is None or price < 0:
            raise InvalidOrderError(exchange_name, symbol, "price must be >= 0")

        if isinstance(order_type, str) and "-" in order_type:
            side_str, type_str = order_type.split("-", 1)
            try:
                side = Side(side_str.lower())
                ord_type = OrderType(type_str.lower())
            except ValueError as exc:
                raise InvalidOrderError(
                    exchange_name, symbol, f"invalid order_type: {order_type}"
                ) from exc
        elif order_type in ("limit", "market"):
            raise LegacyOrderApiError(
                f"cannot derive side from order_type={order_type!r}; "
                "use BtApi.make_order(exchange_name, OrderRequest(...))"
            )
        else:
            raise InvalidOrderError(
                exchange_name,
                symbol,
                "order_type must be one of: limit, market, or a side-type pair like buy-limit",
            )

        request = OrderRequest(
            symbol=symbol,
            side=side,
            order_type=ord_type,
            quantity=Decimal(str(volume)),
            price=Decimal(str(price)) if price and price > 0 else None,
            account_id="legacy",
            client_order_id=client_order_id or f"legacy-{uuid.uuid4().hex[:16]}",
            reduce_only=offset in ("close", "close_today", "close_yesterday"),
        )
        return self._make_order_typed(exchange_name, request)

    def cancel_order(
        self,
        exchange_name: str,
        symbol: str | CancelOrderRequest,
        order_id: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """撤单
        :param exchange_name: 交易所标识
        :param symbol: 交易对
        :param order_id: 订单ID

        Iteration 62 direct form: ``cancel_order(exchange_name, symbol,
        pre_dispatch_commit=port, target_identity=NativeOrderIdentity, host_scope=...)``
        cancels by the original venue identity through the SDK direct workflow.
        """
        pre_dispatch_commit = kwargs.pop("pre_dispatch_commit", None)
        if pre_dispatch_commit is not None:
            from bt_api_base.direct_write_protocol import NativeOrderIdentity

            target = kwargs.pop("target_identity", None)
            host_scope = kwargs.pop("host_scope", None) or "bt_api"
            if type(target) is not NativeOrderIdentity or kwargs:
                raise NormalizedApiError(
                    "cancel_order", "target_identity_required", definite_reject=True
                )
            cancel_symbol = symbol.symbol if isinstance(symbol, CancelOrderRequest) else symbol
            return self.direct_writes().cancel(
                exchange_name,
                target,
                str(cancel_symbol),
                commit_port=pre_dispatch_commit,
                host_scope=host_scope,
            )
        self._reject_simnow_profile_write(exchange_name, "cancel_order")
        if isinstance(symbol, CancelOrderRequest):
            self._reject_unhanded_ctp_cancel_identity(exchange_name, symbol)
        for value in kwargs.values():
            if isinstance(value, CancelOrderRequest):
                self._reject_unhanded_ctp_cancel_identity(exchange_name, value)
        if kwargs.pop("normalized", False):
            budget_capability = kwargs.pop("budget_capability", None)
            request = (
                symbol
                if isinstance(symbol, CancelOrderRequest)
                else CancelOrderRequest(symbol=symbol, account_id="legacy", order_id=order_id)
            )
            session = self._execution_session
            request = self._bind_ctp_managed_cancel_identity(exchange_name, request, session)
            return self._normalized_call(
                "cancel_order",
                exchange_name,
                request.symbol,
                lambda: self._cancel_order_raw(
                    exchange_name, request, extra_data=extra_data, **kwargs
                ),
                request=request,
                budget_capability=budget_capability,
            )
        if self._execution_session is not None:
            raise NormalizedApiError(
                "cancel_order",
                "session_requires_normalized_typed_request",
                definite_reject=True,
            )
        return self._cancel_order_raw(exchange_name, symbol, order_id, extra_data, **kwargs)

    def _cancel_order_raw(
        self,
        exchange_name: str,
        symbol: str | CancelOrderRequest,
        order_id: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        self._reject_simnow_profile_write(exchange_name, "cancel_order")
        request = (
            symbol
            if isinstance(symbol, CancelOrderRequest)
            else CancelOrderRequest(symbol=symbol, account_id="legacy", order_id=order_id)
        )
        if isinstance(request, CancelOrderRequest):
            self._reject_unhanded_ctp_cancel_identity(exchange_name, request)
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("cancel_order", extra_data, kwargs)
            return self._backend.cancel_order(exchange_name, request)
        return self._backend.cancel_order(exchange_name, request, extra_data=extra_data, **kwargs)

    def _reject_unhanded_ctp_cancel_identity(
        self,
        exchange_name: str,
        request: CancelOrderRequest,
    ) -> None:
        """Reject every public route carrying an I9 identity echo.

        The parent candidate has no trusted claim/approval/queue handoff from
        the shared I9 worker into this SDK path. The echo is therefore never
        permission to reach a backend or native client.
        """
        if not isinstance(request, CancelOrderRequest):
            return
        if str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() != "CTP":
            if request.ctp_cancel_identity is None:
                return
            raise NormalizedApiError(
                "cancel_order",
                "ctp_cancel_identity_venue_mismatch",
                definite_reject=True,
            )
        if (
            request.ctp_cancel_identity is None
            and request.managed_cancel_intent_id is None
            and not str(request.runtime_order_id or "").startswith("bt-managed-v1:")
        ):
            return
        session = self._execution_session
        if session is not None and request.ctp_cancel_identity is not None:
            session._require_ctp_cancel_identity_binding("cancel_order", request)
        raise NormalizedApiError(
            "cancel_order",
            "ctp_cancel_dispatch_handoff_unavailable",
            definite_reject=True,
        )

    def cancel_all(
        self,
        exchange_name: str,
        symbol: str | CancelAllRequest | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """撤销所有订单
        :param exchange_name: 交易所标识
        :param symbol: 交易对 (None 表示所有品种)
        """
        self._reject_simnow_profile_write(exchange_name, "cancel_all")
        if self._execution_session is not None:
            raise CapabilityNotSupportedError(
                "cancel_all",
                detail="execution session requires individually journaled cancel_order requests",
            )
        request = (
            symbol
            if isinstance(symbol, CancelAllRequest)
            else CancelAllRequest(account_id="legacy", symbol=symbol)
        )
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("cancel_all", extra_data, kwargs)
            return self._backend.cancel_all(exchange_name, request)
        return self._backend.cancel_all(exchange_name, request, extra_data=extra_data, **kwargs)

    def query_order(
        self,
        exchange_name: str,
        symbol: str | QueryOrderRequest,
        order_id: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """查询订单
        :param exchange_name: 交易所标识
        :param symbol: 交易对
        :param order_id: 订单ID
        """
        if kwargs.pop("normalized", False):
            request = (
                symbol
                if isinstance(symbol, QueryOrderRequest)
                else QueryOrderRequest(symbol=symbol, account_id="legacy", order_id=order_id)
            )
            return self._normalized_call(
                "query_order",
                exchange_name,
                request.symbol,
                lambda: self.query_order(exchange_name, request, extra_data=extra_data, **kwargs),
                request=request,
            )
        request = (
            symbol
            if isinstance(symbol, QueryOrderRequest)
            else QueryOrderRequest(symbol=symbol, account_id="legacy", order_id=order_id)
        )
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("query_order", extra_data, kwargs)
            return self._backend.query_order(exchange_name, request)
        return self._backend.query_order(exchange_name, request, extra_data=extra_data, **kwargs)

    def get_open_orders(
        self,
        exchange_name: str,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """查询挂单
        :param exchange_name: 交易所标识
        :param symbol: 交易对 (None 表示所有品种)
        """
        if kwargs.pop("normalized", False):
            return self._normalized_call(
                "get_open_orders",
                exchange_name,
                symbol,
                lambda: self.get_open_orders(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                ),
            )
        consistency = self._pop_consistency(kwargs)
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("get_open_orders", extra_data, kwargs)
            return self._backend.get_open_orders(exchange_name, consistency=consistency)
        return self._backend.get_open_orders(
            exchange_name,
            symbol=symbol,
            consistency=consistency,
            extra_data=extra_data,
            **kwargs,
        )

    def get_deals(
        self,
        exchange_name: str,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """查询账户成交/成交明细，用于获取真实手续费。

        Different exchange feeds expose private fills as ``get_deals``. Keep
        the unified facade thin so callers can pass through exchange-specific
        arguments such as ``limit``, ``count``, ``start_time`` or ``end_time``.
        """
        if kwargs.pop("normalized", False):
            return self._normalized_call(
                "get_deals",
                exchange_name,
                symbol,
                lambda: self.get_deals(exchange_name, symbol, extra_data=extra_data, **kwargs),
            )
        consistency = self._pop_consistency(kwargs)
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("get_deals", extra_data, kwargs)
            return self._backend.get_deals(exchange_name, consistency=consistency)
        return self._backend.get_deals(
            exchange_name,
            symbol=symbol,
            consistency=consistency,
            extra_data=extra_data,
            **kwargs,
        )

    def get_trades(
        self,
        exchange_name: str,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """查询成交记录。

        Some venues use this for public recent trades, while gateway adapters
        may map it to account fills. Callers that require real account fees
        should prefer :meth:`get_deals` when the feed supports it.
        """
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                "get_trades",
                detail="transport=zmq; no forwarding public-trades protocol is enabled",
            )
        return self._get_feed(exchange_name).get_trades(symbol, extra_data=extra_data, **kwargs)

    # ── 账户查询（同步）────────────────────────────────────────────

    def get_balance(
        self,
        exchange_name: str,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """查询余额
        :param exchange_name: 交易所标识
        :param symbol: 币种 (None 表示全部)
        """
        if kwargs.pop("normalized", False):
            return self._normalized_call(
                "get_balance",
                exchange_name,
                symbol,
                lambda: self.get_balance(exchange_name, symbol, extra_data=extra_data, **kwargs),
            )
        consistency = self._pop_consistency(kwargs)
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("get_balance", extra_data, kwargs)
            return self._backend.get_balance(exchange_name, consistency=consistency)
        return self._backend.get_balance(
            exchange_name,
            symbol=symbol,
            consistency=consistency,
            extra_data=extra_data,
            **kwargs,
        )

    def get_account(
        self,
        exchange_name: str,
        symbol: str = "ALL",
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """查询账户信息
        :param exchange_name: 交易所标识
        :param symbol: 币种
        """
        if kwargs.pop("normalized", False):
            return self._normalized_call(
                "get_account",
                exchange_name,
                symbol,
                lambda: self.get_account(exchange_name, symbol, extra_data=extra_data, **kwargs),
            )
        consistency = self._pop_consistency(kwargs)
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("get_account", extra_data, kwargs)
            return self._backend.get_account(exchange_name, consistency=consistency)
        return self._backend.get_account(
            exchange_name,
            symbol=symbol,
            consistency=consistency,
            extra_data=extra_data,
            **kwargs,
        )

    def get_position(
        self,
        exchange_name: str,
        symbol: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """查询持仓
        :param exchange_name: 交易所标识
        :param symbol: 交易对 (None 表示所有品种)
        """
        if kwargs.pop("normalized", False):
            return self._normalized_call(
                "get_position",
                exchange_name,
                symbol,
                lambda: self.get_position(exchange_name, symbol, extra_data=extra_data, **kwargs),
            )
        consistency = self._pop_consistency(kwargs)
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("get_position", extra_data, kwargs)
            return self._backend.get_position(exchange_name, consistency=consistency)
        return self._backend.get_position(
            exchange_name,
            symbol=symbol,
            consistency=consistency,
            extra_data=extra_data,
            **kwargs,
        )

    @staticmethod
    def _pop_consistency(kwargs: dict[str, Any]) -> Consistency:
        value = kwargs.pop("consistency", Consistency.LIVE)
        if isinstance(value, Consistency):
            return value
        return Consistency(str(value))

    @staticmethod
    def _reject_zmq_legacy_options(operation: str, extra_data: Any, kwargs: dict[str, Any]) -> None:
        """Do not silently drop feed-specific legacy options in ZMQ mode."""
        if extra_data is None and not kwargs:
            return
        names = sorted(kwargs)
        if extra_data is not None:
            names.insert(0, "extra_data")
        raise CapabilityNotSupportedError(
            operation,
            detail=(
                "transport=zmq does not support feed-specific legacy options: " + ", ".join(names)
            ),
        )

    @staticmethod
    def _normalize_capability_operations(capabilities: Any) -> dict[str, bool]:
        """Return deterministic, plain-string capability flags from feed metadata."""
        if capabilities is None:
            return {}

        try:
            as_dict = getattr(capabilities, "as_dict", None)
        except Exception:
            return {}
        if callable(as_dict):
            try:
                capabilities = as_dict()
            except Exception:
                return {}

        if isinstance(capabilities, Mapping):
            entries = capabilities.items()
        elif isinstance(capabilities, (set, frozenset)):
            entries = ((capability, True) for capability in capabilities)
        else:
            return {}

        operations: dict[str, bool] = {}
        for capability, enabled in entries:
            if isinstance(capability, str):
                name = capability.value if hasattr(capability, "value") else capability
                if not isinstance(name, str):
                    continue
            else:
                continue
            operations[name] = bool(enabled)
        return dict(sorted(operations.items()))

    def get_capabilities(self, exchange_name: str) -> Any:
        """Return a read-only capability report for the given exchange."""
        from ._contracts.capabilities import CapabilityReport

        if self.transport_mode is TransportMode.ZMQ:
            return CapabilityReport(
                exchange_name=exchange_name,
                status="experimental",
                operations=self._backend.get_capabilities(exchange_name),
            )
        feed = self.exchange_feeds.get(exchange_name)
        if feed is None:
            return CapabilityReport(exchange_name=exchange_name, status="retired")
        try:
            capabilities = getattr(feed, "capabilities", None)
        except Exception:
            capabilities = None
        operations = self._normalize_capability_operations(capabilities)
        return CapabilityReport(
            exchange_name=exchange_name,
            status="loadable",
            operations=operations,
        )

    def get_command_status(self, exchange_name: str, command_id: str) -> CommandStatus:
        """Reconcile a ZMQ command after :class:`CommandResultUnknownError`.

        Direct feeds have no shared forwarding receipt store, so callers must
        use their native venue query semantics in that transport mode.
        """
        if self.transport_mode is not TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                "get_command_status",
                detail="transport=direct has no forwarding command receipt store",
            )
        return self._backend.get_command_status(exchange_name, command_id)

    # ── 异步接口（显式方法，替代动态 __getattr__ 代理）────────────────

    @staticmethod
    async def _await_legacy_async(
        operation: str, call: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        """Await legacy adapters, rejecting fire-and-forget ``None`` results."""
        result = call(*args, **kwargs)
        result = await result if inspect.isawaitable(result) else result
        if result is None:
            raise CapabilityNotSupportedError(
                operation,
                detail="legacy async adapter returned no result",
            )
        return result

    async def async_get_tick(
        self, exchange_name: str, symbol: str, *args: Any, **kwargs: Any
    ) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_tick(exchange_name, symbol)
        return await self._await_legacy_async(
            "async_get_tick",
            self._get_feed(exchange_name).async_get_tick,
            symbol,
            *args,
            **kwargs,
        )

    async def async_get_depth(
        self, exchange_name: str, symbol: str, count: int = 20, **kwargs: Any
    ) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_depth(exchange_name, symbol, count)
        return await self._await_legacy_async(
            "async_get_depth",
            self._get_feed(exchange_name).async_get_depth,
            symbol,
            count=count,
            **kwargs,
        )

    async def async_get_kline(
        self,
        exchange_name: str,
        symbol: str,
        period: str,
        count: int = 20,
        **kwargs: Any,
    ) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_kline(exchange_name, symbol, period, count)
        return await self._await_legacy_async(
            "async_get_kline",
            self._get_feed(exchange_name).async_get_kline,
            symbol,
            period,
            count=count,
            **kwargs,
        )

    async def async_make_order(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        self._reject_simnow_profile_write(exchange_name, "async_make_order")
        if args and isinstance(args[0], OrderRequest) and _is_ctp_exchange(exchange_name):
            request = args[0]
            if (
                request.ctp_order_identity is not None
                or request.managed_intent_id is not None
                or str(request.runtime_order_id or "").startswith("bt-managed-v1:")
            ):
                raise NormalizedApiError(
                    "async_make_order",
                    "ctp_order_dispatch_handoff_unavailable",
                    definite_reject=True,
                )
        if kwargs.pop("normalized", False):
            budget_capability = kwargs.pop("budget_capability", None)
            if len(args) != 1 or not isinstance(args[0], OrderRequest) or kwargs:
                raise NormalizedApiError(
                    "async_make_order", "typed_request_required", definite_reject=True
                )
            request = args[0]
            self._require_ctp_managed_order_identity(
                "async_make_order", exchange_name, request, self._execution_session
            )
            resolved_request, mode_guarded = await asyncio.to_thread(
                self._begin_position_mode_placement,
                exchange_name,
                request,
                resolve_mode=True,
            )
            assert resolved_request is not None
            request = resolved_request
            try:
                return await self._async_normalized_call(
                    "make_order",
                    exchange_name,
                    request.symbol,
                    lambda pre_dispatch: self._async_backend_call(
                        "make_order",
                        exchange_name,
                        request,
                        pre_dispatch=pre_dispatch,
                    ),
                    request=request,
                    budget_capability=budget_capability,
                )
            finally:
                if mode_guarded:
                    await asyncio.shield(
                        asyncio.to_thread(
                            self._end_position_mode_placement,
                            exchange_name,
                        )
                    )
        if self._execution_session is not None:
            raise NormalizedApiError(
                "async_make_order",
                "session_requires_normalized_typed_request",
                definite_reject=True,
            )
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                "async_make_order",
                detail="ZMQ order submission uses typed OrderRequest",
            )
        _, mode_guarded = await asyncio.to_thread(
            self._begin_position_mode_placement, exchange_name
        )
        try:
            call = self._get_feed(exchange_name).async_make_order
            if asyncio.iscoroutinefunction(call):
                result = await call(*args, **kwargs)
                if result is None:
                    raise CapabilityNotSupportedError(
                        "async_make_order",
                        detail="legacy async adapter returned no result",
                    )
                return result
            result = await asyncio.to_thread(call, *args, **kwargs)
            result = await result if inspect.isawaitable(result) else result
            if result is None:
                raise CapabilityNotSupportedError(
                    "async_make_order",
                    detail="legacy async adapter returned no result",
                )
            return result
        finally:
            if mode_guarded:
                await asyncio.shield(
                    asyncio.to_thread(
                        self._end_position_mode_placement,
                        exchange_name,
                    )
                )

    async def async_cancel_order(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        self._reject_simnow_profile_write(exchange_name, "async_cancel_order")
        for value in (*args, *kwargs.values()):
            if isinstance(value, CancelOrderRequest):
                self._reject_unhanded_ctp_cancel_identity(exchange_name, value)
        if kwargs.pop("normalized", False):
            budget_capability = kwargs.pop("budget_capability", None)
            if len(args) != 1 or not isinstance(args[0], CancelOrderRequest) or kwargs:
                raise NormalizedApiError(
                    "async_cancel_order", "typed_request_required", definite_reject=True
                )
            request = args[0]
            session = self._execution_session
            request = self._bind_ctp_managed_cancel_identity(exchange_name, request, session)
            return await self._async_normalized_call(
                "cancel_order",
                exchange_name,
                request.symbol,
                lambda pre_dispatch: self._async_backend_call(
                    "cancel_order",
                    exchange_name,
                    request,
                    pre_dispatch=pre_dispatch,
                ),
                request=request,
                budget_capability=budget_capability,
            )
        if self._execution_session is not None:
            raise NormalizedApiError(
                "async_cancel_order",
                "session_requires_normalized_typed_request",
                definite_reject=True,
            )
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                "async_cancel_order", detail="ZMQ cancel uses typed CancelOrderRequest"
            )
        call = self._get_feed(exchange_name).async_cancel_order
        if asyncio.iscoroutinefunction(call):
            result = await call(*args, **kwargs)
            if result is None:
                raise CapabilityNotSupportedError(
                    "async_cancel_order",
                    detail="legacy async adapter returned no result",
                )
            return result
        result = await asyncio.to_thread(call, *args, **kwargs)
        result = await result if inspect.isawaitable(result) else result
        if result is None:
            raise CapabilityNotSupportedError(
                "async_cancel_order", detail="legacy async adapter returned no result"
            )
        return result

    async def async_cancel_all(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        self._reject_simnow_profile_write(exchange_name, "async_cancel_all")
        if self._execution_session is not None:
            raise CapabilityNotSupportedError(
                "async_cancel_all",
                detail="execution session requires individually journaled cancel_order requests",
            )
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                "async_cancel_all", detail="ZMQ cancel-all uses typed CancelAllRequest"
            )
        return await self._await_legacy_async(
            "async_cancel_all",
            self._get_feed(exchange_name).async_cancel_all,
            *args,
            **kwargs,
        )

    async def async_query_order(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        if kwargs.pop("normalized", False):
            if len(args) != 1 or not isinstance(args[0], QueryOrderRequest) or kwargs:
                raise NormalizedApiError(
                    "async_query_order", "typed_request_required", definite_reject=True
                )
            request = args[0]
            return await self._async_normalized_call(
                "query_order",
                exchange_name,
                request.symbol,
                lambda _pre_dispatch: self._async_backend_call(
                    "query_order", exchange_name, request
                ),
                request=request,
            )
        if self.transport_mode is TransportMode.ZMQ:
            raise CapabilityNotSupportedError(
                "async_query_order", detail="ZMQ query uses typed QueryOrderRequest"
            )
        call = self._get_feed(exchange_name).async_query_order
        if asyncio.iscoroutinefunction(call):
            result = await call(*args, **kwargs)
            if result is None:
                raise CapabilityNotSupportedError(
                    "async_query_order",
                    detail="legacy async adapter returned no result",
                )
            return result
        result = await asyncio.to_thread(call, *args, **kwargs)
        result = await result if inspect.isawaitable(result) else result
        if result is None:
            raise CapabilityNotSupportedError(
                "async_query_order", detail="legacy async adapter returned no result"
            )
        return result

    async def async_get_open_orders(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_open_orders(
                exchange_name, consistency=self._pop_consistency(kwargs)
            )
        return await self._await_legacy_async(
            "async_get_open_orders",
            self._get_feed(exchange_name).async_get_open_orders,
            *args,
            **kwargs,
        )

    async def async_get_balance(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_balance(
                exchange_name, consistency=self._pop_consistency(kwargs)
            )
        return await self._await_legacy_async(
            "async_get_balance",
            self._get_feed(exchange_name).async_get_balance,
            *args,
            **kwargs,
        )

    async def async_get_account(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_account(
                exchange_name, consistency=self._pop_consistency(kwargs)
            )
        return await self._await_legacy_async(
            "async_get_account",
            self._get_feed(exchange_name).async_get_account,
            *args,
            **kwargs,
        )

    async def async_get_position(self, exchange_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.transport_mode is TransportMode.ZMQ:
            return self._backend.get_position(
                exchange_name, consistency=self._pop_consistency(kwargs)
            )
        return await self._await_legacy_async(
            "async_get_position",
            self._get_feed(exchange_name).async_get_position,
            *args,
            **kwargs,
        )

    # ── 批量操作 ───────────────────────────────────────────────────

    def get_all_ticks(self, symbol: str, extra_data: Any = None, **kwargs: Any) -> dict[str, Any]:
        """从所有已连接的交易所获取行情
        :param symbol: 交易对
        :return: dict {exchange_name: ticker_data 或 Exception}
        """
        results = {}
        for exchange_name in self.exchange_feeds:
            try:
                results[exchange_name] = self.get_tick(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                )
            except Exception as e:
                self.log(f"get_tick failed for {exchange_name}: {e}", level="warning")
                results[exchange_name] = e
        return results

    def get_all_balances(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        """从所有已连接的交易所查询余额
        :return: dict {exchange_name: balance_data 或 Exception}
        """
        if kwargs.pop("normalized", False):
            results = {}
            for venue in self.list_exchanges():
                currency = symbol or (
                    self._execution_session.currency(venue)
                    if self._execution_session is not None
                    else None
                )
                results[venue] = self.get_account(
                    venue,
                    currency or "ALL",
                    extra_data=extra_data,
                    normalized=True,
                    **kwargs,
                )
            return results
        results = {}
        for exchange_name in self.exchange_feeds:
            try:
                results[exchange_name] = self.get_balance(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                )
            except Exception as e:
                self.log(f"get_balance failed for {exchange_name}: {e}", level="warning")
                results[exchange_name] = e
        return results

    def get_portfolio_balance(
        self, *, venue_balances: Mapping[str, Mapping[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Aggregate normalized accounts only when all values share a currency.

        A supplied snapshot avoids issuing the same account requests twice.
        No currency conversion or partial-success aggregation is implied.
        """
        from ._normalization import number

        balances = (
            self.get_all_balances(normalized=True) if venue_balances is None else venue_balances
        )
        currencies = {row.get("currency") for row in balances.values()}
        if len(currencies) > 1 or (
            None in currencies
            and any(row.get("cash") or row.get("value") for row in balances.values())
        ):
            raise NormalizedApiError("get_portfolio_balance", "mixed_or_unknown_account_currencies")
        return {
            "cash": sum(number(row["cash"]) for row in balances.values()),
            "value": sum(number(row["value"]) for row in balances.values()),
            "currency": next(iter(currencies), None),
        }

    def get_all_positions(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        """从所有已连接的交易所查询持仓
        :return: dict {exchange_name: position_data 或 Exception}
        """
        if kwargs.pop("normalized", False):
            return {
                venue: self.get_position(
                    venue, symbol, extra_data=extra_data, normalized=True, **kwargs
                )
                for venue in self.list_exchanges()
            }
        results = {}
        for exchange_name in self.exchange_feeds:
            try:
                results[exchange_name] = self.get_position(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                )
            except Exception as e:
                self.log(f"get_position failed for {exchange_name}: {e}", level="warning")
                results[exchange_name] = e
        return results

    def cancel_all_orders(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        """撤销所有已连接交易所的所有订单
        :return: dict {exchange_name: result 或 Exception}
        """
        results = {}
        for exchange_name in self.exchange_feeds:
            try:
                results[exchange_name] = self.cancel_all(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                )
            except Exception as e:
                self.log(f"cancel_all failed for {exchange_name}: {e}", level="warning")
                results[exchange_name] = e
        return results
