"""
Integrate all exchange APIs using this BtApi class
通过 ExchangeRegistry 实现交易所的即插即用，新增交易所无需修改此文件
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import queue
import threading
import uuid
import warnings
from collections import defaultdict, deque
from collections.abc import Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from functools import wraps
from pathlib import Path
from typing import Any

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
from .balance_manager import BalanceManagerMixin
from .data_downloader import DataDownloaderMixin

# 导入注册模块，确保交易所在使用前完成注册。
# 自动扫描 exchange_registers/ 下所有模块，无需手动维护 import 列表。
__all__ = ["BtApi"]

DATANAME_SEPARATOR = "___"
_NORMALIZED_WRITE_OPERATIONS = frozenset(
    {"make_order", "cancel_order", "set_position_mode"}
)
_CTP_TRANSITION_LOCK_INIT = threading.Lock()
_CTP_EXECUTION_RECOVERY_PUBLIC_FIELDS = (
    "schema_version",
    "status",
    "recovery_required",
    "can_arm_execution",
    "can_arm_recovery",
    "account_fingerprint",
    "trading_day",
    "instrument",
    "connection_generation",
    "strategy_id",
    "execution_cycle_id",
    "remote_position",
    "owned_position",
    "allowed_closes",
    "allowed_cancels",
    "allowed_actions",
    "unknown_ids",
    "evidence_errors",
    "journal_sha256",
    "fencing_epoch",
    "recovery_token_sha256",
)
_CRYPTO_CREDENTIAL_ALIASES = {
    "OKX": {
        "public": ("public_key", "api_key"),
        "secret": ("private_key", "secret_key", "api_secret"),
        "passphrase": ("passphrase",),
    },
    "BINANCE": {
        "public": ("public_key", "api_key"),
        "secret": ("private_key", "secret_key", "api_secret"),
    },
}


class _CtpPrivateIngressFence:
    """Linearization point shared by CTP private producers and recovery gates."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.epoch = 0
        self.pending = 0
        self.active_writes = 0
        self.revocation_pending = False
        self.revocation_worker_active = False
        self.revocation_teardown_required = False


class _CtpPrivateIngressQueue:
    """Write-only account-stream proxy which fences every private producer put."""

    def __init__(
        self,
        target: queue.Queue[Any],
        fence: _CtpPrivateIngressFence,
        record_ingress: Any,
        revoke_arm: Any,
    ) -> None:
        self.target = target
        self.fence = fence
        self._record_ingress = record_ingress
        self._revoke_arm = revoke_arm

    def _publish(self, item: Any, publisher: Any) -> None:
        # Publish the epoch before waiting on the session mutex.  An arm which
        # currently owns that mutex can still observe this producer attempt at
        # its final fence and fail closed.
        should_revoke = False
        published = False
        try:
            with self.fence.lock:
                self.fence.epoch += 1
                self.fence.pending += 1
                published = True
                ordered_after_write = self.fence.active_writes > 0
                publisher(item)
            should_revoke = self._record_ingress(ordered_after_write) is True
            if should_revoke:
                self._revoke_arm()
        finally:
            with self.fence.lock:
                if published:
                    self.fence.pending -= 1

    def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
        def publish(value: Any) -> None:
            if timeout is None:
                self.target.put(value, block=block)
            else:
                self.target.put(value, block=block, timeout=timeout)

        self._publish(item, publish)

    def append_normalized(self, item: Any, target: Any) -> None:
        self._publish(item, target.append)

    def put_nowait(self, item: Any) -> None:
        self.put(item, block=False)

    def qsize(self) -> int:
        return self.target.qsize()

    def empty(self) -> bool:
        return self.target.empty()

    def full(self) -> bool:
        return self.target.full()


def _serialized_ctp_execution_transition(method):
    """Serialize public CTP gate/session transitions on one SDK instance."""

    @wraps(method)
    def wrapped(self, *args, **kwargs):
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
    fingerprint = str(value or "").strip().lower()
    digest = fingerprint.removeprefix("acct_")
    if len(digest) != 16 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        return ""
    return f"acct_{digest}"


_reg_logger = get_logger("registry")


def _credential_alias_value(parameters, aliases):
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


def _execution_credential_fingerprints(settings, config, transport_mode):
    """Preflight private credentials and return hashes of public identifiers only."""
    if config["market_data_only"] or transport_mode is not TransportMode.DIRECT:
        return {}
    fingerprints = {}
    settings = dict(settings or {})
    for venue in sorted(settings):
        parameters = settings.get(venue, {})
        provider = str(venue).partition(DATANAME_SEPARATOR)[0].upper()
        aliases = _CRYPTO_CREDENTIAL_ALIASES.get(provider)
        if aliases is None:
            continue
        if not isinstance(parameters, Mapping):
            raise NormalizedApiError(
                "configure_execution",
                "invalid_exchange_credentials",
                definite_reject=True,
            )
        values = {
            name: _credential_alias_value(parameters, names)
            for name, names in aliases.items()
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


class _RuntimeRegistrar:
    """Minimal runtime registrar for adapter registration."""

    def __init__(self) -> None:
        self._adapters: dict[str, type] = {}

    def register_adapter(self, exchange_type: str, adapter_cls: type) -> None:
        normalized = str(exchange_type).strip().upper()
        self._adapters[normalized] = adapter_cls

    def get_adapter(self, exchange_type: str) -> type | None:
        return self._adapters.get(str(exchange_type).strip().upper())

    def list_adapters(self) -> list[str]:
        return list(self._adapters.keys())


_runtime_registrar = _RuntimeRegistrar()
_plugins_loaded = False


def _initialize_plugin_and_legacy_registrations() -> None:
    """Load all plugins via entry points and commit registrations to the global registry."""
    from bt_api_base.plugins.loader import PluginLoader

    registry = ExchangeRegistry._get_default()
    loader = PluginLoader(registry, _runtime_registrar)
    loader.load_all()


def _ensure_plugins_loaded() -> None:
    """惰性加载插件（首次 BtApi 实例化时），坏插件不阻断整体导入。"""
    global _plugins_loaded
    if _plugins_loaded:
        return
    try:
        _initialize_plugin_and_legacy_registrations()
    except Exception as exc:
        get_logger("api").warning(
            f"Plugin loading degraded: {type(exc).__name__}: {exc}"
        )
    finally:
        _plugins_loaded = True


# 常用异步方法白名单（A-09：__getattr__ 只代理白名单内的方法，避免 hasattr 恒真）
class BtApi(DataDownloaderMixin, BalanceManagerMixin):
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
        transport_mode: TransportMode = TransportMode.DIRECT,
        forwarding_config: ForwardingConfig | None = None,
        execution_config: dict[str, Any] | None = None,
    ) -> None:
        """初始化 BtApi 实例。

        Args:
            exchange_kwargs: 交易所配置 dict，key 为 exchange_name，value 为对应参数。
            debug: 是否开启 debug 模式，控制日志输出。
            event_bus: 事件总线实例，用于 BarEvent/OrderEvent 等回调；None 则创建默认实例。
            transport_mode: direct（默认，直接持有 Feed）或 zmq（经转发网关）。
            forwarding_config: ZMQ 模式下的转发网关端点与 scope 配置。
            execution_config: Optional durable execution session. None preserves
                legacy behavior; configured writes require typed normalized calls.
        """
        self.exchange_kwargs = {}
        self.debug = debug
        self.data_queues = {}
        self.exchange_feeds = {}
        self.logger = self.init_logger()
        self._value_dict = {}
        self._cash_dict = {}
        self.subscribe_bar_num = 0
        self.event_bus = event_bus or EventBus()
        self._subscription_flags = {}
        self._subscription_streams: list[Any] = []
        self._normalized_event_pending = defaultdict(deque)
        self._position_modes = {}
        self._position_mode_lock = threading.RLock()
        self._ctp_execution_transition_lock = threading.RLock()
        self._ctp_private_ingress_fences: dict[str, _CtpPrivateIngressFence] = {}
        self._ctp_private_ingress_queues: dict[str, _CtpPrivateIngressQueue] = {}
        self._position_mode_reconcile_required: dict[str, str] = {}
        self._position_mode_active_placements: dict[str, int] = {}
        self._execution_session = None
        # Managed CTP feeds receive this opaque, process-local capability before
        # they are exposed through ``exchange_feeds``.  The backend resolves it
        # lazily so configure_execution() also works before or after add_exchange().
        self._ctp_execution_capability = (
            object() if execution_config is not None else None
        )
        self._instrument_cache = {}
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
        self.transport_mode = TransportMode(transport_mode)
        settings = deepcopy(exchange_kwargs or {})
        if isinstance(forwarding_config, dict):
            forwarding_config = ForwardingConfig(**forwarding_config)
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
        from ._execution_session import _ExecutionSession, session_config

        config = session_config(execution_config)
        if self._ctp_execution_capability is None:
            self._ctp_execution_capability = object()
        if isinstance(_exchange_names, Mapping):
            exchange_settings = dict(_exchange_names)
        else:
            names = tuple(_exchange_names or self.list_exchanges())
            exchange_settings = {
                name: self.exchange_kwargs.get(name, {})
                for name in names
                if name in self.exchange_kwargs
            }
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
                raise NormalizedApiError(
                    "configure_execution", "execution_already_configured"
                )
            if self.list_exchanges():
                self._validate_required_environments()
            return
        configured_names = set(exchange_settings) | set(config["required_environments"])
        for venue in configured_names:
            provider = str(venue).partition(DATANAME_SEPARATOR)[0].upper()
            if (
                provider in _CRYPTO_CREDENTIAL_ALIASES
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
            raise NormalizedApiError(
                "configure_execution", "configure_before_subscribing"
            )
        exchange_names = tuple(
            _exchange_names.keys()
            if isinstance(_exchange_names, Mapping)
            else (_exchange_names or self.list_exchanges())
        )
        self._execution_session = _ExecutionSession(
            config,
            exchange_names=exchange_names,
            credential_fingerprints=credential_fingerprints,
        )
        try:
            for exchange_name, feed in self.exchange_feeds.items():
                if str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper() == "CTP":
                    self._configure_ctp_execution_gate(exchange_name, feed)
        except Exception:
            self._execution_session.close()
            self._execution_session = None
            raise
        if config["market_data_only"]:
            for value in self.exchange_kwargs.values():
                value["subscribe_account"] = False
        if self.list_exchanges():
            try:
                self._validate_required_environments()
            except Exception:
                self._execution_session.close()
                self._execution_session = None
                raise

    def new_client_order_id(
        self,
        exchange_name: str,
        account_id: str | None = None,
        strategy_id: str | None = None,
    ) -> str:
        """Allocate a numeric client reference before the caller binds its order.

        Allocation reserves the value locally; only a persisted order intent
        consumes it. Decimal references also fit CTP's native OrderRef contract.
        """
        if self._execution_session is not None:
            return self._execution_session.new_client_order_id(
                exchange_name,
                account_id=account_id,
                strategy_id=strategy_id,
            )
        import time

        return f"{time.time_ns() % 10**12:012d}"

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

    def get_account_risk_snapshot(
        self,
        *,
        initialize_baseline: bool = False,
    ) -> dict[str, Any]:
        """Return SDK-owned, durable account-equity evidence for execution.

        The method performs authenticated normalized account and position reads
        for every configured crypto venue.  Before the first baseline it also
        queries each venue's remote open orders through this public normalized
        API.  It never accepts runner-supplied equity, flatness, or empty-order
        claims.  A first baseline is persisted only when both the initial and
        commit-time remote open-order sweeps are known empty, all positions are
        authoritatively known flat, and the execution journal has no active or
        unknown order.
        """
        return self._collect_account_risk_snapshot(
            initialize_baseline=initialize_baseline,
            reset_loss_latch=False,
        )

    def reset_account_maximum_loss_latch(self) -> dict[str, Any]:
        """Explicitly reset a breached loss latch after authoritative flat proof.

        The reset re-baselines equity only after two normalized open-order
        sweeps, normalized flat positions on every configured venue, and a
        clean durable execution ledger. Any missing or conflicting evidence
        leaves the persisted latch unchanged.
        """
        return self._collect_account_risk_snapshot(
            initialize_baseline=False,
            reset_loss_latch=True,
        )

    def _ctp_bound_query_records(
        self,
        session: Any,
        exchange_name: str,
        query_type: str,
        *,
        operation: str = "get_account_risk_snapshot",
        read_only: bool = False,
    ) -> tuple[list[dict[str, Any]], str]:
        """Return complete CTP rows bound to one account/day/generation."""
        evidence = self._ctp_bound_query_evidence(
            session,
            exchange_name,
            query_type,
            operation=operation,
            read_only=read_only,
        )
        return list(evidence["records"]), evidence["account_fingerprint"]

    def _ctp_bound_query_evidence(
        self,
        session: Any,
        exchange_name: str,
        query_type: str,
        *,
        operation: str,
        read_only: bool,
    ) -> dict[str, Any]:
        """Return records plus terminal-packet evidence for one CTP request."""
        guard = session.require_bound_read if read_only else session.require_write
        guard(operation, venue=exchange_name)
        identity = session.execution_identity(exchange_name)
        expected_generation = identity.get("connection_generation")
        expected_fingerprint = str(identity.get("account_id") or "")
        expected_trading_day = str(identity.get("trading_day") or "")
        try:
            result = self.query_ctp_result(exchange_name, query_type)
            records = result.records
            request_id = result.request_id
            result_fingerprint = _canonical_ctp_account_fingerprint(
                result.account_fingerprint
            )
            valid = bool(
                getattr(result, "request_type", None) == query_type
                and type(request_id) is int
                and request_id > 0
                and getattr(result, "complete", None) is True
                and getattr(result, "evidence_complete", None) is True
                and getattr(result, "is_last_seen", None) is True
                and getattr(result, "timed_out", None) is False
                and getattr(result, "unsupported", None) is False
                and getattr(result, "error_code", None) in (None, 0)
                and getattr(result, "late_callback_count", None) == 0
                and getattr(result, "connection_generation", None)
                == expected_generation
                and result_fingerprint == expected_fingerprint
                and isinstance(records, tuple)
            )
        except Exception:
            valid = False
            records = ()
            result_fingerprint = ""
            request_id = None
        guard(operation, venue=exchange_name)
        if not valid:
            raise NormalizedApiError(
                operation,
                f"ctp_{query_type}_query_incomplete",
                definite_reject=True,
            )
        safe_rows = []
        for record in records:
            if not isinstance(record, Mapping):
                raise NormalizedApiError(
                    operation,
                    f"ctp_{query_type}_records_invalid",
                    definite_reject=True,
                )
            row = dict(record)
            for key in ("AccountID", "InvestorID", "account_id"):
                if key in row:
                    row[key] = result_fingerprint
            # QueryResult owns the terminal-packet/account binding.  Persist
            # that verified envelope explicitly on every row so downstream
            # recovery cannot infer identity from a missing native field.
            row["account_id"] = result_fingerprint
            row["trading_day"] = expected_trading_day
            row["connection_generation"] = expected_generation
            row["evidence_complete"] = True
            safe_rows.append(row)
        return {
            "query_type": query_type,
            "request_id": request_id,
            "connection_generation": expected_generation,
            "account_fingerprint": result_fingerprint,
            "trading_day": expected_trading_day,
            "records": tuple(safe_rows),
        }

    @staticmethod
    def _canonical_ctp_recovery_rows(
        records: tuple[dict[str, Any], ...],
        *,
        operation: str,
    ) -> tuple[dict[str, Any], ...]:
        try:
            keyed = [
                (
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                    row,
                )
                for row in records
            ]
        except (TypeError, ValueError):
            raise NormalizedApiError(
                operation,
                "ctp_recovery_records_not_canonical",
                definite_reject=True,
            ) from None
        return tuple(dict(row) for _key, row in sorted(keyed, key=lambda item: item[0]))

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
                if (
                    raw_budget[0] == before_budget
                    and len(normalized_pending) == before_pending
                ):
                    break
        private_events = 0
        for event in events:
            if str(event.get("kind") or "").lower() in {"order", "trade"}:
                private_events += 1
                event = session.event(exchange_name, event)
            if event is not None:
                session.pending[exchange_name].append(event)
        return private_events

    def _ctp_recovery_query_round(
        self,
        session: Any,
        exchange_name: str,
        *,
        operation: str,
    ) -> dict[str, Any]:
        """Run one complete account/position/order/trade recovery query set."""
        identity = session.execution_identity(exchange_name)
        expected_account = str(identity.get("account_id") or "")
        expected_day = str(identity.get("trading_day") or "")
        expected_generation = identity.get("connection_generation")
        results = {
            query_type: self._ctp_bound_query_evidence(
                session,
                exchange_name,
                query_type,
                operation=operation,
                read_only=True,
            )
            for query_type in ("account", "positions", "orders", "trades")
        }
        request_ids = [result["request_id"] for result in results.values()]
        if len(set(request_ids)) != len(request_ids):
            raise NormalizedApiError(
                operation,
                "ctp_recovery_query_id_reused",
                definite_reject=True,
            )
        if len(results["account"]["records"]) != 1:
            raise NormalizedApiError(
                operation,
                "ctp_recovery_account_query_incomplete",
                definite_reject=True,
            )
        for query_type, result in results.items():
            if (
                result["account_fingerprint"] != expected_account
                or result["trading_day"] != expected_day
                or result["connection_generation"] != expected_generation
            ):
                raise NormalizedApiError(
                    operation,
                    "ctp_recovery_query_identity_mismatch",
                    definite_reject=True,
                )
            for row in result["records"]:
                account_value = next(
                    (
                        row[name]
                        for name in ("AccountID", "InvestorID", "account_id")
                        if row.get(name) not in (None, "")
                    ),
                    None,
                )
                day_value = next(
                    (
                        row[name]
                        for name in ("TradingDay", "trading_day")
                        if row.get(name) not in (None, "")
                    ),
                    None,
                )
                if (
                    str(account_value or "") != expected_account
                    or str(day_value or "") != expected_day
                ):
                    raise NormalizedApiError(
                        operation,
                        f"ctp_recovery_{query_type}_row_identity_incomplete",
                        definite_reject=True,
                    )
        account_snapshot = self._canonical_ctp_recovery_rows(
            results["account"]["records"],
            operation=operation,
        )
        snapshot = {
            query_type: self._canonical_ctp_recovery_rows(
                results[query_type]["records"],
                operation=operation,
            )
            for query_type in ("positions", "orders", "trades")
        }
        full_snapshot = {"account": account_snapshot, **snapshot}
        snapshot_json = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        account_snapshot_json = json.dumps(
            account_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        full_snapshot_json = json.dumps(
            full_snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return {
            "snapshot": snapshot,
            "full_snapshot": full_snapshot,
            "request_ids": {
                query_type: results[query_type]["request_id"]
                for query_type in ("account", "positions", "orders", "trades")
            },
            "account_fingerprint": expected_account,
            "trading_day": expected_day,
            "connection_generation": expected_generation,
            "snapshot_sha256": hashlib.sha256(
                snapshot_json.encode("utf-8")
            ).hexdigest(),
            "account_snapshot_sha256": hashlib.sha256(
                account_snapshot_json.encode("utf-8")
            ).hexdigest(),
            "full_snapshot_sha256": hashlib.sha256(
                full_snapshot_json.encode("utf-8")
            ).hexdigest(),
        }

    def _ctp_recovery_query_barrier(
        self,
        session: Any,
        exchange_name: str,
        *,
        operation: str,
        max_attempts: int = 3,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return two independent stable query rounds with an event fence."""
        if type(max_attempts) is not int or max_attempts <= 0:
            raise ValueError("max_attempts must be a positive integer")
        last_second = {"positions": (), "orders": (), "trades": ()}
        last_rounds: list[dict[str, Any]] = []
        stable = False
        attempt = 0
        start_revision = middle_revision = end_revision = (
            session.recovery_event_revision()
        )
        private_start_revision = private_end_revision = (
            session.recovery_private_event_revision()
        )
        ingress_start_epoch, ingress_start_pending = self._ctp_private_ingress_snapshot(
            exchange_name
        )
        ingress_end_epoch = ingress_start_epoch
        ingress_end_pending = ingress_start_pending
        ingress_start_revision = ingress_end_revision = (
            session.recovery_private_ingress_revision()
        )
        for current_attempt in range(1, max_attempts + 1):
            attempt = current_attempt
            self._ingest_ctp_private_queue(
                session,
                exchange_name,
                operation=operation,
            )
            start_revision = session.recovery_event_revision()
            first = self._ctp_recovery_query_round(
                session,
                exchange_name,
                operation=operation,
            )
            self._ingest_ctp_private_queue(
                session,
                exchange_name,
                operation=operation,
            )
            middle_revision = session.recovery_event_revision()
            second = self._ctp_recovery_query_round(
                session,
                exchange_name,
                operation=operation,
            )
            self._ingest_ctp_private_queue(
                session,
                exchange_name,
                operation=operation,
            )
            end_revision = session.recovery_event_revision()
            private_end_revision = session.recovery_private_event_revision()
            ingress_end_epoch, ingress_end_pending = self._ctp_private_ingress_snapshot(
                exchange_name
            )
            ingress_end_revision = session.recovery_private_ingress_revision()
            all_request_ids = [
                request_id
                for round_result in (first, second)
                for request_id in round_result["request_ids"].values()
            ]
            unique_requests = len(set(all_request_ids)) == len(all_request_ids)
            stable = bool(
                unique_requests
                and start_revision == middle_revision == end_revision
                and private_start_revision == private_end_revision
                and ingress_start_epoch == ingress_end_epoch
                and ingress_start_pending == ingress_end_pending == 0
                and ingress_start_revision == ingress_end_revision
                and first["full_snapshot"] == second["full_snapshot"]
            )
            last_second = second["snapshot"]
            last_rounds = [first, second]
            if stable:
                break
        barrier_material = {
            "schema_version": "bt-api-py.ctp-recovery-query-barrier.v1",
            "stable": stable,
            "attempts": attempt,
            "event_revisions": {
                "start": start_revision,
                "middle": middle_revision,
                "end": end_revision,
            },
            "private_event_revisions": {
                "start": private_start_revision,
                "end": private_end_revision,
            },
            "private_ingress_revisions": {
                "start": ingress_start_revision,
                "end": ingress_end_revision,
            },
            "private_ingress_epochs": {
                "start": ingress_start_epoch,
                "end": ingress_end_epoch,
            },
            "private_ingress_pending": {
                "start": ingress_start_pending,
                "end": ingress_end_pending,
            },
            "rounds": [
                {
                    "request_ids": dict(round_result["request_ids"]),
                    "account_fingerprint": round_result["account_fingerprint"],
                    "trading_day": round_result["trading_day"],
                    "connection_generation": round_result["connection_generation"],
                    "snapshot_sha256": round_result["snapshot_sha256"],
                    "account_snapshot_sha256": round_result["account_snapshot_sha256"],
                    "full_snapshot_sha256": round_result["full_snapshot_sha256"],
                }
                for round_result in last_rounds
            ],
        }
        barrier_json = json.dumps(
            barrier_material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        barrier = {
            **barrier_material,
            "barrier_sha256": hashlib.sha256(barrier_json.encode("utf-8")).hexdigest(),
        }
        return last_second, barrier

    @staticmethod
    def _public_ctp_recovery_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
        """Detach the closed public recovery shape from SDK-owned mutable state."""
        if not isinstance(plan, Mapping) or any(
            field not in plan for field in _CTP_EXECUTION_RECOVERY_PUBLIC_FIELDS
        ):
            raise NormalizedApiError(
                "prepare_execution_recovery",
                "execution_recovery_plan_invalid",
                definite_reject=True,
            )
        return {
            field: deepcopy(plan[field])
            for field in _CTP_EXECUTION_RECOVERY_PUBLIC_FIELDS
        }

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
        account_rows, fingerprint = self._ctp_bound_query_records(
            session, exchange_name, "account"
        )
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
        account = normalize_result(
            "get_account", [account_row], exchange_name, currency
        )
        positions = normalize_result("get_position", position_rows, exchange_name, None)
        if not isinstance(account, Mapping) or not isinstance(positions, list):
            raise NormalizedApiError(
                "get_account_risk_snapshot",
                "ctp_account_risk_schema_invalid",
                definite_reject=True,
            )
        quantities = []
        for position in positions:
            if (
                not isinstance(position, Mapping)
                or position.get("quantity_known") is not True
                or "quantity" not in position
            ):
                raise NormalizedApiError(
                    "get_account_risk_snapshot",
                    "ctp_position_schema_invalid",
                    definite_reject=True,
                )
            quantity = Decimal(str(position["quantity"]))
            if not quantity.is_finite():
                raise NormalizedApiError(
                    "get_account_risk_snapshot",
                    "ctp_position_schema_invalid",
                    definite_reject=True,
                )
            quantities.append(quantity)
        return {
            "currency": currency,
            "equity": account.get("equity", account.get("value")),
            "positions_known": True,
            "positions_flat": all(quantity == 0 for quantity in quantities),
            "open_orders_known": open_orders_known,
            "open_orders_empty": open_orders_empty,
            "authenticated_account_id": fingerprint,
        }

    def _ctp_open_orders(
        self, session: Any, exchange_name: str
    ) -> list[dict[str, Any]]:
        from ._normalization import normalize_result

        rows, fingerprint = self._ctp_bound_query_records(
            session, exchange_name, "orders"
        )
        active_rows = []
        for row in rows:
            status = str(row.get("OrderStatus", row.get("status", ""))).strip().lower()
            if status in {
                "0",
                "2",
                "4",
                "5",
                "filled",
                "completed",
                "canceled",
                "cancelled",
                "expired",
                "rejected",
            }:
                continue
            active_rows.append({**row, "account_id": fingerprint})
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
            str(venue).partition(DATANAME_SEPARATOR)[0].upper() == "CTP"
            for venue in risk_venues
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
                    if (
                        str(sweep_venue).partition(DATANAME_SEPARATOR)[0].upper()
                        == "CTP"
                    ):
                        open_orders = self._ctp_open_orders(session, sweep_venue)
                    else:
                        open_orders = self.get_open_orders(
                            sweep_venue, None, normalized=True
                        )
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
            open_orders_known = (
                not requires_open_order_proof or venue in initially_empty_venues
            )
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
                quantities = []
                for position in positions:
                    if (
                        not isinstance(position, Mapping)
                        or "quantity" not in position
                        or position.get("quantity_known") is not True
                    ):
                        raise ValueError("incomplete_position_evidence")
                    quantity = Decimal(str(position["quantity"]))
                    if not quantity.is_finite():
                        raise ValueError("invalid_position_quantity")
                    quantities.append(quantity)
                observations[venue] = {
                    "currency": account.get("currency") or currency,
                    "equity": account.get("equity", account.get("value")),
                    "positions_known": True,
                    "positions_flat": all(quantity == 0 for quantity in quantities),
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
            baseline_commit_check=lambda: strict_open_order_sweep(
                "baseline_commit_open_orders"
            )[1],
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
            verifier = getattr(
                self.exchange_feeds[exchange_name], "get_environment_info", None
            )
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
            if not isinstance(simulated, bool) or simulated != (
                environment != "production"
            ):
                return result
            provider = str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper()
            if provider == "OKX":
                api_region = proof.get("api_region")
                if not isinstance(api_region, str) or api_region not in {
                    "global",
                    "eea",
                    "us",
                    "tr",
                }:
                    return result
                if api_region == "tr" and environment != "production":
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
            provider = str(exchange_name).partition("___")[0].upper()
            if provider not in {"OKX", "BINANCE"}:
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

    def _normalized_call(self, operation, exchange_name, symbol, call, *, request=None):
        """Opt-in SDK result contract; never expose credentials in normalized errors."""
        from ._normalization import normalize_error, normalize_result

        def invoke():
            failure = None
            try:
                result = call()
                normalized = normalize_result(
                    operation, result, exchange_name, symbol, request
                )
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
                    raise NormalizedApiError(
                        operation, "market_data_only", definite_reject=True
                    )
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
                    raise CapabilityNotSupportedError(
                        operation, detail="market-data-only session"
                    )
            self._validate_required_environment(exchange_name, operation=operation)
            if session is not None and operation in {
                "make_order",
                "cancel_order",
                "query_order",
            }:
                acquire_write = release_write = None
                if (
                    operation in {"make_order", "cancel_order"}
                    and str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper()
                    == "CTP"
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
                    )
                finally:
                    if release_write is not None:
                        release_write()
                    self._sync_ctp_gate_after_session_invoke(
                        session, exchange_name, operation
                    )
            result = invoke()
            if session is not None:
                if operation in {"get_account", "get_balance"} and isinstance(
                    result, dict
                ):
                    session.accounts[exchange_name] = dict(result)
                elif operation == "get_open_orders":
                    result = [
                        session.event(exchange_name, {**row, "kind": "order"})
                        for row in result
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

    async def _async_backend_call(
        self, operation: str, *args: Any, **kwargs: Any
    ) -> Any:
        """Await an async backend method, with a worker fallback for sync adapters."""
        async_method = getattr(self._backend, f"async_{operation}", None)
        if callable(async_method):
            if inspect.iscoroutinefunction(async_method):
                return await async_method(*args, **kwargs)
            result = await asyncio.to_thread(async_method, *args, **kwargs)
            return await result if inspect.isawaitable(result) else result
        sync_method = getattr(self._backend, operation)
        return await asyncio.to_thread(sync_method, *args, **kwargs)

    async def _async_normalized_call(
        self,
        operation: str,
        exchange_name: str,
        symbol: str,
        call: Any,
        *,
        request: Any,
    ) -> Any:
        """Normalize an awaited result through the same execution-session owner."""
        from ._normalization import normalize_error, normalize_result

        async def invoke() -> Any:
            failure = None
            try:
                native = await call()
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

        session = self._execution_session
        if session is not None and session.config["market_data_only"]:
            raise NormalizedApiError(
                operation, "market_data_only", definite_reject=True
            )
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
                )
            finally:
                if release_write is not None:
                    release_write()
                self._sync_ctp_gate_after_session_invoke(
                    session, exchange_name, operation
                )
        return await invoke()

    def _enrich_order_commission(self, exchange_name, order):
        """Attach actual fees only when fills fully reconcile a terminal order.

        Fees remain denominated in commission_currency; consumers must compare
        that currency with the account currency before recording cash costs.
        An unavailable fee query does not erase a confirmed order state.
        """
        if (
            not exchange_name.startswith("BINANCE___")
            or not order.get("terminal_confirmed")
            or not order.get("filled")
            or order.get("cumulative_commission") is not None
            or not order.get("order_id")
        ):
            return
        try:
            fills = self.get_deals(
                exchange_name, order["symbol"], count=1000, normalized=True
            )
            matching = {
                fill["trade_id"]: fill
                for fill in fills
                if fill.get("trade_id") and fill.get("order_id") == order["order_id"]
            }
            from decimal import Decimal

            quantity = sum(
                (Decimal(str(fill.get("size") or 0)) for fill in matching.values()),
                Decimal(0),
            )
            currencies = {fill.get("fee_currency") for fill in matching.values()}
            if (
                quantity != Decimal(str(order["filled"]))
                or len(currencies) != 1
                or None in currencies
                or "" in currencies
                or any(fill.get("fee") is None for fill in matching.values())
            ):
                return
            order["cumulative_commission"] = float(
                sum(
                    (Decimal(str(fill["fee"])) for fill in matching.values()),
                    Decimal(0),
                )
            )
            order["commission_currency"] = order["fee_currency"] = currencies.pop()
            order["commission_source"] = "exchange"
        except Exception:
            order["commission_source"] = "unavailable"

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
            raise ValueError(
                "coalesce_market_snapshots must be an iterable of event kinds"
            )
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
            if (
                raw_budget[0] == before_budget
                and len(normalized_pending) == before_pending
            ):
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
                for _, event, dropped in sorted(
                    latest.values(), key=lambda item: item[0]
                )
            )
            latest.clear()

        for index, event in enumerate(events):
            kind = str(event.get("kind") or "").lower()
            symbol = event.get("symbol")
            complete_snapshot = not (
                kind == "orderbook"
                and (
                    event.get("snapshot_or_delta") == "delta"
                    or event.get("continuity_status")
                    in {"gap", "out_of_order", "checksum_failed"}
                )
            )
            if (
                kind in snapshot_kinds
                and symbol not in (None, "")
                and complete_snapshot
            ):
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
                getter = getattr(item, "get_data", None)
                if callable(getter):
                    values = getter()
                    pending.extend(
                        values if isinstance(values, (tuple, list)) else [values]
                    )
                    continue
                event = normalize_event(item, exchange_name)
                if event is not None:
                    self._event_metrics["normalized_events"] += 1
                    return event
            return None
        except Exception as exc:
            failure = normalize_error(exc, "poll_event", exchange_name=exchange_name)
        raise failure from None

    def _build_backend(
        self, transport_mode: TransportMode, forwarding_config: ForwardingConfig | None
    ) -> Any:
        if transport_mode is TransportMode.ZMQ:
            if forwarding_config is None:
                raise ValueError("transport_mode=ZMQ requires forwarding_config")
            from .forwarding.btapi_backend import ZmqBtApiBackend

            return ZmqBtApiBackend(forwarding_config)
        from ._direct_backend import DirectBackend

        return DirectBackend(
            self._get_feed,
            self.exchange_feeds,
            execution_capability=lambda: getattr(
                self, "_ctp_execution_capability", None
            ),
        )

    def _configure_ctp_execution_gate(
        self,
        exchange_name: str,
        feed: Any,
    ) -> dict[str, Any]:
        """Install the SDK-owned native guard before a managed feed is exposed."""
        operation = "configure_execution"
        capability = getattr(self, "_ctp_execution_capability", None)
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
            raise NormalizedApiError(
                operation, "execution_session_required", definite_reject=True
            )
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
            raise NormalizedApiError(
                operation, "single_ctp_session_required", definite_reject=True
            )
        return session, exchange_names[0]

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
            raise InvalidOrderError(
                exchange_name, symbol, "order_type must be a non-empty string"
            )

        normalized_order_type = order_type.lower()
        if normalized_order_type not in {"limit", "market"}:
            raise InvalidOrderError(
                exchange_name,
                symbol,
                "order_type must be one of: limit, market",
            )
        if normalized_order_type == "limit" and price <= 0:
            raise InvalidOrderError(
                exchange_name, symbol, "price must be > 0 for limit order"
            )
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
                raise SubscribeError(
                    "", detail=f"invalid topic at index {index}: expected dict"
                )
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
            session = self._execution_session
            credential_fingerprints = (
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
                    and str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper()
                    == "CTP"
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
                if (
                    session is not None
                    and str(exchange_name).partition(DATANAME_SEPARATOR)[0].upper()
                    == "CTP"
                ):
                    self._configure_ctp_execution_gate(exchange_name, feed)
                self.exchange_feeds[exchange_name] = feed
                self._validate_required_environment(exchange_name)
                fingerprint = credential_fingerprints.get(exchange_name)
                if fingerprint is not None:
                    session.bind_credential_identity(exchange_name, fingerprint)
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

    def get_async_request_api(self, exchange_name: str) -> Any:
        """Deprecated alias for get_request_api."""
        warnings.warn(
            "get_async_request_api is deprecated; use get_request_api instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.get_request_api(exchange_name)

    def _ctp_execution_runtime_identity(self) -> dict[str, str]:
        """Return reproducible source-manifest and native-runtime identities."""
        operation = "arm_execution_from_preflight"
        try:
            import bt_api_ctp

            diagnostics_getter = getattr(bt_api_ctp, "get_ctp_native_diagnostics", None)
            if not callable(diagnostics_getter):
                raise ValueError("native diagnostics unavailable")
            diagnostics = dict(diagnostics_getter())
            native_path = Path(
                str(diagnostics.get("loaded_module_path") or "")
            ).resolve()
            reported_native_sha256 = str(
                diagnostics.get("loaded_module_sha256") or ""
            ).lower()
            package_path = Path(
                str(getattr(bt_api_ctp, "__file__", "") or "")
            ).resolve()
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
            relative_paths = {
                path.relative_to(package_root).as_posix() for path in package_files
            }
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
            package_sha256 = hashlib.sha256(
                package_manifest_json.encode("utf-8")
            ).hexdigest()
            if (
                diagnostics.get("ctp_package_manifest") != package_manifest
                or str(diagnostics.get("ctp_package_sha256") or "").lower()
                != package_sha256
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

    def _ctp_execution_arm_context(
        self,
        exchange_name: str,
    ) -> dict[str, Any]:
        """Read the non-network state that fences a CTP execution arm."""
        operation = "arm_execution_from_preflight"
        try:
            runtime_identity = self._ctp_execution_runtime_identity()
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
                or str(state.get("login_state") or "").strip().lower()
                not in {"logged_in", "ready"}
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
            if not account_fingerprint or not trading_day or not environment_profile:
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

    def _ctp_execution_stream(self, exchange_name: str) -> Any:
        streams = getattr(self, "_subscription_streams", None)
        if not isinstance(streams, list):
            return None
        candidates = [
            stream
            for stream in streams
            if str(getattr(stream, "stream_name", "")) == "ctp_trade_stream"
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _ctp_execution_stream_ready(self, exchange_name: str) -> bool:
        flags = getattr(self, "_subscription_flags", None)
        if (
            not isinstance(flags, dict)
            or flags.get(f"{exchange_name}_account") is not True
        ):
            return False
        stream = self._ctp_execution_stream(exchange_name)
        if stream is None or getattr(stream, "_running", None) is not True:
            return False
        state = getattr(stream, "state", None)
        state_value = str(getattr(state, "value", state) or "").strip().lower()
        if state_value != "authenticated":
            return False
        feed = self.exchange_feeds.get(exchange_name)
        request_trader = getattr(feed, "trader_client", None)
        stream_trader = getattr(stream, "trader_client", None)
        if (
            feed is None
            or request_trader is None
            or stream_trader is None
            or stream_trader is not request_trader
        ):
            return False
        state_reader = getattr(request_trader, "get_session_state", None)
        feed_state_reader = getattr(feed, "get_session_state", None)
        if not callable(state_reader) or not callable(feed_state_reader):
            return False
        try:
            trader_state = state_reader()
            feed_state = feed_state_reader()
        except Exception:
            return False
        if not isinstance(trader_state, Mapping) or not isinstance(feed_state, Mapping):
            return False
        generation = feed_state.get("connection_generation")
        account = _canonical_ctp_account_fingerprint(
            feed_state.get("account_fingerprint")
        )
        trading_day = str(feed_state.get("trading_day") or "").strip()
        return bool(
            type(generation) is int
            and generation > 0
            and account
            and trading_day
            and feed_state.get("connected") is True
            and feed_state.get("trading_ready") is True
            and feed_state.get("settlement_readback_verified") is True
            and request_trader is stream_trader
            and trader_state.get("connected") is True
            and trader_state.get("trading_ready") is True
            and trader_state.get("settlement_readback_verified") is True
            and str(trader_state.get("auth_state") or "").strip().lower()
            in {"authenticated", "success", "logged_in"}
            and str(trader_state.get("login_state") or "").strip().lower()
            in {"logged_in", "ready"}
            and str(trader_state.get("settlement_state") or "").strip().lower()
            in {"confirmed", "ready"}
            and trader_state.get("connection_generation") == generation
            and _canonical_ctp_account_fingerprint(
                trader_state.get("account_fingerprint")
            )
            == account
            and str(trader_state.get("trading_day") or "").strip() == trading_day
            and not trader_state.get("last_error")
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
        *,
        operation: str,
    ) -> dict[str, Any]:
        from ._execution_session import _canonical_ctp_instrument, _execution_arm_proof

        normalized, proof_sha256 = _execution_arm_proof(proof)
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
            method(capability, normalized)
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
        if (
            state.get("armed") is not True
            or state.get("connection_generation") != normalized["connection_generation"]
            or state.get("trading_day") != normalized["trading_day"]
            or _canonical_ctp_instrument(state.get("instrument"))
            != normalized["instrument"]
            or state.get("proof_sha256") != proof_sha256
            or state.get("environment_profile") != normalized["environment_profile"]
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
                reason = (
                    session._arm_revoked_reason
                    or "execution_recovery_action_requires_refresh"
                )
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
                    pass
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
            getattr(self, "_subscription_flags", {}).pop(
                f"{exchange_name}_account", None
            )
        exchange_params = self.exchange_kwargs.get(exchange_name)
        if isinstance(exchange_params, dict):
            previous = token.get("previous_subscribe_account")
            if previous is None:
                exchange_params.pop("subscribe_account", None)
            else:
                exchange_params["subscribe_account"] = previous

    @_serialized_ctp_execution_transition
    def arm_execution_from_preflight(self, proof: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically arm one direct CTP session from current read-only proof."""
        operation = "arm_execution_from_preflight"
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        private_event_revision = session.recovery_private_event_revision()
        private_ingress_revision = session.recovery_private_ingress_revision()
        private_ingress_epoch, private_ingress_pending = (
            self._ctp_private_ingress_snapshot(exchange_name)
        )
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
            )
            self._ingest_ctp_private_queue(
                session,
                exchange_name,
                operation=operation,
            )
            current_epoch, current_pending = self._ctp_private_ingress_snapshot(
                exchange_name
            )
            private_fence_changed = bool(
                current_epoch != private_ingress_epoch
                or current_pending
                or session.recovery_private_ingress_revision()
                != private_ingress_revision
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
            result = session.prepare_execution_authorization(reason)
        if failure is not None:
            raise failure
        return result

    def _prepare_execution_recovery_plan(
        self, session: Any, exchange_name: str
    ) -> dict[str, Any]:
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
        if failure is not None:
            raise failure
        session.prepare_recovery(proof, state_reader, venue=exchange_name)
        return self._prepare_execution_recovery_plan(session, exchange_name)

    @_serialized_ctp_execution_transition
    def _arm_execution_recovery(
        self,
        *,
        proof: Mapping[str, Any],
        recovery_token_sha256: str,
    ) -> dict[str, Any]:
        operation = "arm_execution_recovery"
        session, exchange_name = self._sole_ctp_execution_venue(operation)
        private_event_revision = session.recovery_private_event_revision()
        private_ingress_revision = session.recovery_private_ingress_revision()
        private_ingress_epoch, private_ingress_pending = (
            self._ctp_private_ingress_snapshot(exchange_name)
        )
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
            self._arm_ctp_execution_gate(exchange_name, proof, operation=operation)

        def rollback_execution() -> None:
            self._disarm_ctp_execution_gate(
                exchange_name,
                "execution_recovery_arm_rollback",
                operation=operation,
            )

        try:
            self._prepare_ctp_execution_stream(exchange_name, operation=operation)
            result = session.arm_recovery_from_preflight(
                proof,
                recovery_token_sha256,
                state_reader,
                venue=exchange_name,
                prepare_execution=prepare_execution,
                rollback_execution=rollback_execution,
            )
            self._ingest_ctp_private_queue(
                session,
                exchange_name,
                operation=operation,
            )
            current_epoch, current_pending = self._ctp_private_ingress_snapshot(
                exchange_name
            )
            private_fence_changed = bool(
                current_epoch != private_ingress_epoch
                or current_pending
                or session.recovery_private_ingress_revision()
                != private_ingress_revision
                or session.recovery_private_event_revision() != private_event_revision
            )
            if private_fence_changed:
                raise NormalizedApiError(
                    operation,
                    "execution_recovery_required",
                    definite_reject=True,
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
            session.pause_recovery()
            raise

    def arm_execution_recovery(
        self,
        *,
        proof: Mapping[str, Any],
        recovery_token_sha256: str,
    ) -> dict[str, Any]:
        """Arm one one-shot SDK plan while ordinary execution stays closed."""
        return self._arm_execution_recovery(
            proof=proof,
            recovery_token_sha256=recovery_token_sha256,
        )

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
            raise
        return result

    @_serialized_ctp_execution_transition
    def disarm_execution(self, reason: str = "execution_arm_revoked") -> dict[str, Any]:
        """Idempotently revoke execution while retaining read-only CTP access."""
        operation = "disarm_execution"
        session, exchange_name = self._sole_ctp_execution_venue(operation)
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

    def get_ctp_session_state(
        self, exchange_name: str = "CTP___FUTURE"
    ) -> dict[str, Any]:
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
        state = dict(method())
        public_fields = {
            "connected",
            "auth_state",
            "login_state",
            "settlement_state",
            "settlement_readback_verified",
            "read_only_ready",
            "trading_ready",
            "ready",
            "auto_settlement_confirm",
            "front_id",
            "session_id",
            "trading_day",
            "connection_generation",
            "account_fingerprint",
            "settlement_request_id",
            "settlement_connection_generation",
            "settlement_trading_day",
            "settlement_late_callback_count",
            "settlement_proof_source",
            "settlement_proof_query_request_id",
            "request_counts",
            "last_error",
            "environment_profile",
            "environment_readiness",
        }
        return {name: value for name, value in state.items() if name in public_fields}

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
        methods = {
            "account": "query_account_result",
            "positions": "query_positions_result",
            "orders": "query_orders_result",
            "trades": "query_trades_result",
            "instruments": "query_instruments_result",
            "margin_rate": "query_instrument_margin_rate_result",
            "commission_rate": "query_instrument_commission_rate_result",
            # Reading a matching account/day confirmation is also the bounded
            # cross-process proof that promotes this live session to trading-ready.
            "settlement_confirmation": "verify_settlement_confirmation",
        }
        try:
            method_name = methods[str(query_type).strip().lower()]
        except KeyError as exc:
            raise ValueError(f"unknown CTP query_type {query_type!r}") from exc
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
        feed = self.exchange_feeds.get(exchange_name)
        method = getattr(feed, "verify_settlement_confirmation", None)
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
        """Explicitly confirm settlement for a logged-in CTP session."""
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
        feed = self.exchange_feeds.get(exchange_name)
        method = getattr(feed, "confirm_settlement", None)
        if not callable(method):
            raise CapabilityNotSupportedError(
                "confirm_ctp_settlement", detail="CTP feed cannot confirm settlement"
            )
        options: dict[str, Any] = {}
        if self._execution_session is not None:
            capability = getattr(self, "_ctp_execution_capability", None)
            if capability is None:
                raise NormalizedApiError(
                    "confirm_ctp_settlement",
                    "ctp_execution_gate_unavailable",
                    definite_reject=True,
                )
            state = self._ctp_execution_gate_state(
                exchange_name,
                operation="confirm_ctp_settlement",
            )
            if state.get("armed") is not False:
                raise NormalizedApiError(
                    "confirm_ctp_settlement",
                    "ctp_settlement_confirmation_requires_read_only",
                    definite_reject=True,
                )
            options["_execution_capability"] = capability
        return bool(method(timeout=timeout, **options))

    def get_data_queue(self, exchange_name: str) -> queue.Queue | None:
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
        return data_queue

    def subscribe(self, dataname: str, topics: list[dict[str, Any]]) -> None:
        """通过 ExchangeRegistry 查找订阅处理函数，无需硬编码交易所类型"""
        exchange, asset_type, symbol = self._parse_dataname(dataname)
        exchange_name = exchange + DATANAME_SEPARATOR + asset_type
        normalized_topics, subscribe_bar_num = self._normalize_subscribe_topics(topics)
        if self.transport_mode is TransportMode.ZMQ:
            self._backend.subscribe(exchange_name, symbol, normalized_topics)
            self.subscribe_bar_num += subscribe_bar_num
            return
        exchange_params = self._copy_exchange_params(
            self.exchange_kwargs.get(exchange_name, {})
        )
        data_queue = self.get_data_queue(exchange_name)
        if data_queue is None:
            raise SubscribeError(exchange_name, detail="exchange not registered")

        subscribe_handler = ExchangeRegistry.get_stream_class(
            exchange_name, "subscribe"
        )
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
        """Release transports and the execution lock, including on close failure."""
        failure = None
        try:
            self._close_transports()
        except Exception as exc:
            if self._execution_session is not None:
                from ._normalization import normalize_error

                failure = normalize_error(exc, "close")
            else:
                raise
        finally:
            self.event_bus.off("ws.connected", self._on_websocket_connected)
            if self._execution_session is not None:
                self._execution_session.close()
        if failure is not None:
            raise failure from None

    def _close_transports(self) -> None:
        """Close all exchange feeds (WebSocket streams + HTTP clients)."""
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
                lambda: self.get_tick(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                ),
            )
        consistency = self._pop_consistency(kwargs)
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("get_tick", extra_data, kwargs)
            return self._backend.get_tick(
                exchange_name, symbol, consistency=consistency
            )
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
            return self._backend.get_depth(
                exchange_name, symbol, count, consistency=consistency
            )
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

    def _direct_read_method(
        self, exchange_name: str, operation: str, *aliases: str
    ) -> Any:
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
            if (
                self._execution_session is not None
                and cache_key in self._instrument_cache
            ):
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
        self._validate_required_environment(
            exchange_name, operation="get_instrument_spec"
        )
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
        from ._normalization import fee_schedule, okx_fee_scope

        self._validate_required_environment(exchange_name, operation="get_fee_schedule")
        try:
            method = self._direct_read_method(exchange_name, "get_fee")
            if exchange_name.startswith("OKX___"):
                forbidden = {"inst_id", "inst_family", "group_id", "uly"} & set(kwargs)
                if forbidden:
                    raise NormalizedApiError(
                        "get_fee_schedule",
                        "fee_scope_override_forbidden",
                        definite_reject=True,
                        category="parameter",
                    )
                metadata = self.get_exchange_info(
                    exchange_name,
                    symbol,
                    extra_data=extra_data,
                    **kwargs,
                )
                scope = okx_fee_scope(metadata, exchange_name, symbol)
                fee_kwargs = {
                    "inst_type": scope["asset_type"],
                    "extra_data": extra_data,
                    **kwargs,
                }
                if scope["asset_type"] in {"SPOT", "MARGIN"}:
                    fee_kwargs["inst_id"] = scope["inst_id"]
                elif scope["group_id"] is not None:
                    # OKX forbids groupId together with instId/instFamily.
                    fee_kwargs["group_id"] = scope["group_id"]
                else:
                    fee_kwargs["inst_family"] = scope["inst_family"]
                native = method(**fee_kwargs)
                return fee_schedule(
                    native,
                    exchange_name,
                    symbol,
                    account_id,
                    expected_group_id=scope["group_id"],
                    metadata_currency=scope["currency"],
                )
            else:
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

    @staticmethod
    def _binance_funding_symbol_key(value: Any) -> str:
        """Match Binance native symbols to the SDK's optional dashed spelling."""
        return "".join(
            character for character in str(value or "").upper() if character.isalnum()
        )

    @classmethod
    def _matching_binance_funding_rows(
        cls,
        source: list[dict[str, Any]],
        symbol: str,
    ) -> list[dict[str, Any]]:
        target = cls._binance_funding_symbol_key(symbol)
        if not target:
            return []
        matched = []
        for row in source:
            identities = [
                cls._binance_funding_symbol_key(row.get(field))
                for field in (
                    "symbol",
                    "symbol_name",
                    "funding_rate_symbol_name",
                )
                if row.get(field) not in (None, "")
            ]
            if identities and all(identity == target for identity in identities):
                matched.append(row)
        return matched

    def _complete_binance_funding_schedule(
        self,
        exchange_name: str,
        symbol: str,
        premium_native: Any,
        incomplete: FundingSnapshot,
        extra_data: Any,
    ) -> FundingSnapshot:
        """Complete Binance's premium-index row from exchange-evidenced schedule data.

        ``fundingInfo`` contains only symbols whose schedule or caps were
        adjusted. For an unlisted symbol, the most recent public funding
        settlement and ``nextFundingTime`` provide an evidenced interval. No
        default Binance interval is assumed.
        """
        from ._normalization import funding_snapshot, rows, seconds

        try:
            premium_rows = rows(
                premium_native,
                "get_funding_snapshot",
                exchange_name=exchange_name,
            )
        except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
            return self._unavailable_funding_snapshot(
                exchange_name,
                symbol,
                "funding_payload_invalid",
            )
        matched_premium = self._matching_binance_funding_rows(premium_rows, symbol)
        if len(matched_premium) != 1:
            return self._unavailable_funding_snapshot(
                exchange_name,
                symbol,
                "funding_payload_invalid",
            )
        premium_row = matched_premium[0]
        incomplete = funding_snapshot(premium_row, exchange_name, symbol)
        if (
            incomplete.available
            or incomplete.unavailable_reason != "funding_interval_missing"
        ):
            return incomplete
        feed = self._get_feed(exchange_name)
        transport_failed = False
        payload_invalid = False

        funding_info = getattr(feed, "get_funding_info", None)
        if callable(funding_info):
            try:
                info_native = funding_info(extra_data=deepcopy(extra_data))
            except (CapabilityNotSupportedError, NotImplementedError):
                pass
            except Exception:
                transport_failed = True
            else:
                try:
                    info_rows = rows(
                        info_native,
                        "get_funding_info",
                        exchange_name=exchange_name,
                    )
                except NormalizedApiError:
                    transport_failed = True
                except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
                    payload_invalid = True
                else:
                    matched = self._matching_binance_funding_rows(info_rows, symbol)
                    if len(matched) > 1:
                        return self._unavailable_funding_snapshot(
                            exchange_name,
                            symbol,
                            "funding_payload_invalid",
                        )
                    if matched:
                        merged = {
                            **premium_row,
                            "fundingIntervalHours": matched[0].get(
                                "fundingIntervalHours"
                            ),
                        }
                        return funding_snapshot(merged, exchange_name, symbol)

        history = getattr(feed, "get_history_funding_rate", None)
        if callable(history):
            try:
                history_native = history(
                    symbol,
                    count=2,
                    extra_data=deepcopy(extra_data),
                )
            except (CapabilityNotSupportedError, NotImplementedError):
                pass
            except Exception:
                transport_failed = True
            else:
                try:
                    history_rows = rows(
                        history_native,
                        "get_history_funding_rate",
                        exchange_name=exchange_name,
                    )
                except NormalizedApiError:
                    transport_failed = True
                except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
                    payload_invalid = True
                else:
                    matched = self._matching_binance_funding_rows(history_rows, symbol)
                    settlements = []
                    next_epoch = incomplete.next_funding_time.timestamp()
                    for row in matched:
                        funding_time = row.get(
                            "fundingTime", row.get("current_funding_time")
                        )
                        try:
                            funding_epoch = seconds(funding_time)
                        except (OverflowError, TypeError, ValueError):
                            payload_invalid = True
                            continue
                        if funding_epoch is not None and 0 < funding_epoch < next_epoch:
                            settlements.append((funding_epoch, funding_time))
                    if settlements:
                        _, latest_funding_time = max(
                            settlements, key=lambda item: item[0]
                        )
                        merged = {
                            **premium_row,
                            "fundingTime": latest_funding_time,
                        }
                        return funding_snapshot(merged, exchange_name, symbol)
                    if matched:
                        payload_invalid = True

        if payload_invalid:
            return self._unavailable_funding_snapshot(
                exchange_name,
                symbol,
                "funding_payload_invalid",
            )
        if transport_failed:
            return self._unavailable_funding_snapshot(
                exchange_name,
                symbol,
                "funding_transport_failed",
            )
        return incomplete

    def get_funding_snapshot(
        self,
        exchange_name: str,
        symbol: str,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> FundingSnapshot:
        """Read the next funding settlement as a Decimal typed snapshot."""
        from ._normalization import funding_snapshot, rows

        self._validate_required_environment(
            exchange_name, operation="get_funding_snapshot"
        )

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
            if exchange_name.startswith("BINANCE___"):
                native_rows = rows(
                    native,
                    "get_funding_snapshot",
                    exchange_name=exchange_name,
                )
                matching_rows = self._matching_binance_funding_rows(native_rows, symbol)
                if len(matching_rows) != 1:
                    return self._unavailable_funding_snapshot(
                        exchange_name,
                        symbol,
                        "funding_payload_invalid",
                    )
                native = matching_rows[0]
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
        if (
            exchange_name.startswith("BINANCE___")
            and result.available is False
            and result.unavailable_reason == "funding_interval_missing"
        ):
            return self._complete_binance_funding_schedule(
                exchange_name,
                symbol,
                native,
                result,
                extra_data,
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
        self._validate_required_environment(
            exchange_name, operation="get_trading_readiness"
        )
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

    def _mark_position_mode_reconcile_required(
        self, exchange_name: str, reason: str
    ) -> None:
        """Invalidate a mode snapshot after an outcome that needs reconciliation."""
        with self._position_mode_lock:
            self._position_modes.pop(exchange_name, None)
            self._position_mode_reconcile_required[exchange_name] = reason

    def _record_verified_position_mode(
        self, exchange_name: str, position_mode: str
    ) -> None:
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
        """Atomically enforce mode state and register a crypto order placement."""
        if exchange_name.partition(DATANAME_SEPARATOR)[0] not in {"OKX", "BINANCE"}:
            return request, False
        with self._position_mode_lock:
            if exchange_name in self._position_mode_reconcile_required:
                raise NormalizedApiError(
                    "make_order",
                    "position_mode_reconcile_required",
                    definite_reject=True,
                )
            if resolve_mode and request is None:
                raise TypeError("normalized placement requires an OrderRequest")
            if resolve_mode and request.position_mode is None:
                self._validate_required_environment(
                    exchange_name, operation="get_position_mode"
                )
                mode = self._position_modes.get(exchange_name)
                if mode is None:
                    mode = self.get_position_mode(exchange_name, normalized=True)[
                        "position_mode"
                    ]
                request = replace(request, position_mode=mode)
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

        OKX exposes this operation as ``get_config``; its native ``data``
        entries include ``posMode`` and API-key permissions. Binance USD-M
        futures exposes ``dualSidePosition`` and ``canTrade`` together through
        its read-only account-configuration endpoint.
        """
        if normalized:
            with self._position_mode_lock:
                result = self._normalized_call(
                    "get_account_config",
                    exchange_name,
                    None,
                    lambda: self.get_account_config(
                        exchange_name, extra_data=extra_data
                    ),
                )
                self._record_verified_position_mode(
                    exchange_name, result["position_mode"]
                )
                return result
        method = self._direct_read_method(
            exchange_name, "get_account_config", "get_config"
        )
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
                    lambda: self.get_position_mode(
                        exchange_name, extra_data=extra_data
                    ),
                )
                self._record_verified_position_mode(
                    exchange_name, result["position_mode"]
                )
                return result
        aliases = ("get_config",) if exchange_name.startswith("OKX___") else ()
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
            raise NormalizedApiError(
                operation, "normalized_result_required", definite_reject=True
            )
        if not isinstance(position_mode, str) or position_mode not in {
            "net",
            "dual_side",
        }:
            raise NormalizedApiError(
                operation, "invalid_position_mode", definite_reject=True
            )
        if exchange_name not in {"OKX___SWAP", "BINANCE___SWAP"}:
            raise CapabilityNotSupportedError(
                operation,
                detail=f"{exchange_name} has no normalized position-mode mutation",
                definite_reject=True,
            )
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
            self._mark_position_mode_reconcile_required(
                exchange_name, "verification_required"
            )
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
                self._mark_position_mode_reconcile_required(
                    exchange_name, "verification_failed"
                )
                raise NormalizedApiError(
                    operation,
                    "position_mode_verification_failed",
                    execution_unknown=True,
                ) from None
            if observed["position_mode"] != position_mode:
                self._mark_position_mode_reconcile_required(
                    exchange_name, "verification_mismatch"
                )
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

        ``quantity_native`` is an OKX contract count or Binance base-asset
        quantity. A result with ``ready=True`` still carries
        ``execution_unproven=True`` because only an exchange order response can
        prove execution access.
        """
        operation = "get_order_readiness"
        if exchange_name not in {"OKX___SWAP", "BINANCE___SWAP"}:
            raise CapabilityNotSupportedError(
                operation,
                detail=f"{exchange_name} has no normalized readiness mapper",
            )
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
            raise NormalizedApiError(
                operation, "invalid_margin_mode", definite_reject=True
            )
        session = self._execution_session
        if session is not None and session.config["market_data_only"]:
            raise CapabilityNotSupportedError(
                operation, detail="market-data-only session"
            )
        self._validate_required_environment(exchange_name, operation=operation)

        from ._normalization import normalize_error

        def options() -> Any:
            return deepcopy(extra_data)

        failure = None
        try:
            account_config = self._backend.get_account_config(
                exchange_name, extra_data=options()
            )
            if exchange_name == "OKX___SWAP":
                from ._venue_mappers.okx import normalize_order_readiness

                account_instruments = self._backend.get_account_instruments(
                    exchange_name, symbol, extra_data=options()
                )
                snapshot = normalize_order_readiness(
                    exchange_name,
                    symbol,
                    quantity_native,
                    margin_mode=margin_mode,
                    expected_position_mode=position_mode,
                    account_config=account_config,
                    account_instruments=account_instruments,
                )
                if snapshot["definite_failure"]:
                    return snapshot
                leverage_info = self._backend.get_leverage_info(
                    exchange_name,
                    symbol,
                    margin_mode=margin_mode,
                    extra_data=options(),
                )
                max_size = self._backend.get_max_size(
                    exchange_name,
                    symbol,
                    margin_mode=margin_mode,
                    extra_data=options(),
                )
                return normalize_order_readiness(
                    exchange_name,
                    symbol,
                    quantity_native,
                    margin_mode=margin_mode,
                    expected_position_mode=position_mode,
                    account_config=account_config,
                    account_instruments=account_instruments,
                    leverage_info=leverage_info,
                    max_size=max_size,
                )

            from ._venue_mappers.binance import normalize_order_readiness

            exchange_info = self._backend.get_exchange_info(
                exchange_name, symbol, extra_data=options()
            )

            def optional_read(name: str, *args: Any, **read_kwargs: Any) -> Any:
                method = getattr(self._backend, name, None)
                if not callable(method):
                    return None
                try:
                    return method(*args, **read_kwargs)
                except Exception:
                    return None

            position_mode_info = optional_read(
                "get_position_mode", exchange_name, extra_data=options()
            )
            initial = normalize_order_readiness(
                exchange_name,
                symbol,
                quantity_native,
                margin_mode=margin_mode,
                expected_position_mode=position_mode,
                account_config=account_config,
                exchange_info=exchange_info,
                position_mode_info=position_mode_info,
            )
            if initial["definite_failure"]:
                return initial
            symbol_config = optional_read(
                "get_symbol_config", exchange_name, symbol, extra_data=options()
            )
            leverage_info = optional_read(
                "get_leverage_info",
                exchange_name,
                symbol,
                margin_mode=margin_mode,
                extra_data=options(),
            )
            max_size = optional_read(
                "get_max_size",
                exchange_name,
                symbol,
                margin_mode=margin_mode,
                extra_data=options(),
            )
            return normalize_order_readiness(
                exchange_name,
                symbol,
                quantity_native,
                margin_mode=margin_mode,
                expected_position_mode=position_mode,
                account_config=account_config,
                exchange_info=exchange_info,
                position_mode_info=position_mode_info,
                symbol_config=symbol_config,
                leverage_info=leverage_info,
                max_size=max_size,
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
        """
        if kwargs.pop("normalized", False):
            if not isinstance(symbol, OrderRequest):
                raise NormalizedApiError(
                    "make_order", "typed_request_required", definite_reject=True
                )
            request = symbol
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
        """
        if kwargs.pop("normalized", False):
            request = (
                symbol
                if isinstance(symbol, CancelOrderRequest)
                else CancelOrderRequest(
                    symbol=symbol, account_id="legacy", order_id=order_id
                )
            )
            return self._normalized_call(
                "cancel_order",
                exchange_name,
                request.symbol,
                lambda: self._cancel_order_raw(
                    exchange_name, request, extra_data=extra_data, **kwargs
                ),
                request=request,
            )
        if self._execution_session is not None:
            raise NormalizedApiError(
                "cancel_order",
                "session_requires_normalized_typed_request",
                definite_reject=True,
            )
        return self._cancel_order_raw(
            exchange_name, symbol, order_id, extra_data, **kwargs
        )

    def _cancel_order_raw(
        self, exchange_name, symbol, order_id=None, extra_data=None, **kwargs
    ):
        request = (
            symbol
            if isinstance(symbol, CancelOrderRequest)
            else CancelOrderRequest(
                symbol=symbol, account_id="legacy", order_id=order_id
            )
        )
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("cancel_order", extra_data, kwargs)
            return self._backend.cancel_order(exchange_name, request)
        return self._backend.cancel_order(
            exchange_name, request, extra_data=extra_data, **kwargs
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
        return self._backend.cancel_all(
            exchange_name, request, extra_data=extra_data, **kwargs
        )

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
                else QueryOrderRequest(
                    symbol=symbol, account_id="legacy", order_id=order_id
                )
            )
            return self._normalized_call(
                "query_order",
                exchange_name,
                request.symbol,
                lambda: self.query_order(
                    exchange_name, request, extra_data=extra_data, **kwargs
                ),
                request=request,
            )
        request = (
            symbol
            if isinstance(symbol, QueryOrderRequest)
            else QueryOrderRequest(
                symbol=symbol, account_id="legacy", order_id=order_id
            )
        )
        if self.transport_mode is TransportMode.ZMQ:
            self._reject_zmq_legacy_options("query_order", extra_data, kwargs)
            return self._backend.query_order(exchange_name, request)
        return self._backend.query_order(
            exchange_name, request, extra_data=extra_data, **kwargs
        )

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
                lambda: self.get_deals(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                ),
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
        return self._get_feed(exchange_name).get_trades(
            symbol, extra_data=extra_data, **kwargs
        )

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
                lambda: self.get_balance(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                ),
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
                lambda: self.get_account(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                ),
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
                lambda: self.get_position(
                    exchange_name, symbol, extra_data=extra_data, **kwargs
                ),
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
    def _reject_zmq_legacy_options(
        operation: str, extra_data: Any, kwargs: dict[str, Any]
    ) -> None:
        """Do not silently drop feed-specific legacy options in ZMQ mode."""
        if extra_data is None and not kwargs:
            return
        names = sorted(kwargs)
        if extra_data is not None:
            names.insert(0, "extra_data")
        raise CapabilityNotSupportedError(
            operation,
            detail=(
                "transport=zmq does not support feed-specific legacy options: "
                + ", ".join(names)
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
    async def _await_legacy_async(operation, call, *args: Any, **kwargs: Any) -> Any:
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

    async def async_make_order(
        self, exchange_name: str, *args: Any, **kwargs: Any
    ) -> Any:
        if kwargs.pop("normalized", False):
            if len(args) != 1 or not isinstance(args[0], OrderRequest) or kwargs:
                raise NormalizedApiError(
                    "async_make_order", "typed_request_required", definite_reject=True
                )
            request = args[0]
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
                    lambda: self._async_backend_call(
                        "make_order", exchange_name, request
                    ),
                    request=request,
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

    async def async_cancel_order(
        self, exchange_name: str, *args: Any, **kwargs: Any
    ) -> Any:
        if kwargs.pop("normalized", False):
            if len(args) != 1 or not isinstance(args[0], CancelOrderRequest) or kwargs:
                raise NormalizedApiError(
                    "async_cancel_order", "typed_request_required", definite_reject=True
                )
            request = args[0]
            return await self._async_normalized_call(
                "cancel_order",
                exchange_name,
                request.symbol,
                lambda: self._async_backend_call(
                    "cancel_order", exchange_name, request
                ),
                request=request,
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

    async def async_cancel_all(
        self, exchange_name: str, *args: Any, **kwargs: Any
    ) -> Any:
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

    async def async_query_order(
        self, exchange_name: str, *args: Any, **kwargs: Any
    ) -> Any:
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
                lambda: self._async_backend_call("query_order", exchange_name, request),
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

    async def async_get_open_orders(
        self, exchange_name: str, *args: Any, **kwargs: Any
    ) -> Any:
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

    async def async_get_balance(
        self, exchange_name: str, *args: Any, **kwargs: Any
    ) -> Any:
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

    async def async_get_account(
        self, exchange_name: str, *args: Any, **kwargs: Any
    ) -> Any:
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

    async def async_get_position(
        self, exchange_name: str, *args: Any, **kwargs: Any
    ) -> Any:
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

    def get_all_ticks(
        self, symbol: str, extra_data: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
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
                self.log(
                    f"get_balance failed for {exchange_name}: {e}", level="warning"
                )
                results[exchange_name] = e
        return results

    def get_portfolio_balance(self, *, venue_balances=None) -> dict[str, Any]:
        """Aggregate normalized accounts only when all values share a currency.

        A supplied snapshot avoids issuing the same account requests twice.
        No currency conversion or partial-success aggregation is implied.
        """
        from ._normalization import number

        balances = (
            self.get_all_balances(normalized=True)
            if venue_balances is None
            else venue_balances
        )
        currencies = {row.get("currency") for row in balances.values()}
        if len(currencies) > 1 or (
            None in currencies
            and any(row.get("cash") or row.get("value") for row in balances.values())
        ):
            raise NormalizedApiError(
                "get_portfolio_balance", "mixed_or_unknown_account_currencies"
            )
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
                self.log(
                    f"get_position failed for {exchange_name}: {e}", level="warning"
                )
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
