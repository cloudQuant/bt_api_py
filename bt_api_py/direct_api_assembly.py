"""SDK-owned assembly for ordinary direct unified API clients.

This module owns the direct-client configuration snapshot, factory invocation,
façade validation, execution-configuration handoff, and one-shot close
ownership.  It does not infer provider or account identity, grant write
authority, or log configuration values.
"""

from __future__ import annotations

from collections.abc import Mapping
from threading import Condition, RLock
from typing import Any, Callable

from bt_api_base.feeds.transport_safety import is_sensitive_key, sanitize_value

from .facade_protocol import (
    is_execution_configurable_facade,
    is_unified_bt_api_facade,
)

__all__ = [
    "DIRECT_EXECUTION_CONFIG_KEYS",
    "DirectApiAssembly",
    "DirectApiAssemblyError",
    "direct_execution_config_snapshot",
    "prepare_direct_execution_authorization",
]


DIRECT_EXECUTION_CONFIG_KEYS = (
    "order_journal",
    "require_order_journal",
    "market_data_only",
    "order_poll_interval",
    "account_currency",
    "account_currencies",
    "account_ids",
    "required_environments",
    "strategy_id",
    "strategy_identity_sha256",
    "account_maximum_loss_bps",
    "account_risk_max_age_seconds",
)


def direct_execution_config_snapshot(options: Mapping[str, Any]) -> dict[str, Any]:
    """Copy explicitly supplied SDK execution settings without granting authority."""
    configured = options.get("execution_config")
    if isinstance(configured, Mapping):
        return dict(configured)
    return {key: options[key] for key in DIRECT_EXECUTION_CONFIG_KEYS if key in options}


class DirectApiAssemblyError(RuntimeError):
    """Finite, value-free failure from direct API assembly or lifecycle."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not code.isascii() or not code:
            raise ValueError("code must be a non-empty ASCII string")
        self.code = code
        super().__init__(code)


def prepare_direct_execution_authorization(api, reason):
    """Request and validate the selected direct facade's reusable read-only state.

    A reusable preparation is not an execution authorization. The caller owns
    its local command fence and decides how to translate finite error codes.
    """
    prepare = getattr(api, "prepare_execution_authorization", None)
    if not callable(prepare):
        raise DirectApiAssemblyError("DIRECT_AUTH_PREPARATION_UNAVAILABLE")
    try:
        result = prepare(reason=str(reason or "execution_authorization_reconfigured"))
    except Exception as exc:
        raise DirectApiAssemblyError("DIRECT_AUTH_PREPARATION_FAILED") from exc
    if not isinstance(result, Mapping) or not (
        result.get("armed") is False
        and result.get("market_data_only") is True
        and result.get("reusable") is True
    ):
        raise DirectApiAssemblyError("DIRECT_AUTH_PREPARATION_NOT_REUSABLE")
    return dict(result)


class DirectApiAssembly:
    """Lazily create and own one configured ``UnifiedBtApiFacade``.

    ``api`` is an explicitly borrowed façade.  Otherwise this object owns the
    façade produced by the supplied factory (or the SDK's ``BtApi`` default)
    and its one-shot close.  The factory receives the same keyword contract as
    the former Store direct path: ``exchange_kwargs``, ``execution_config``,
    ``debug``, and only the explicitly supplied transport/event-bus options.

    A failed construction/configuration is terminal for this assembly object.
    It is never retried implicitly, because a custom factory may have created
    external state before failing.  A successfully closed owned façade may be
    replaced on the next explicit ``ensure_api`` call, but the exact closed
    instance is rejected if the factory returns it again.
    """

    _OPTIONAL_FACTORY_OPTIONS = frozenset(
        (
            "transport_mode",
            "forwarding_config",
            "event_bus",
            # Iteration 62: environment-neutral write admission and SDK
            # clock qualification of direct market ticks.
            "write_admission",
            "market_clock_verification",
            # Iteration 62 (62.08): trusted Gateway MarketHub tick runtime.
            "market_runtime_factory",
        )
    )

    @classmethod
    def from_options(
        cls, *, options, exchange_kwargs, execution_config, api_factory, api,
        expected_exchange_names, execution_option_keys, market_data_only,
    ):
        """Compose one direct client from the caller's explicit options.

        The market-data-only constraint is supplied by the lifecycle owner;
        inspecting configuration here does not grant execution authority.
        """
        factory_options = {
            key: options[key]
            for key in cls._OPTIONAL_FACTORY_OPTIONS
            if key in options
        }
        configure_execution = not market_data_only and (
            "execution_config" in options
            or any(key in options for key in execution_option_keys)
        )
        return cls(
            exchange_kwargs=exchange_kwargs,
            execution_config=execution_config,
            debug=options.get("debug", False),
            factory_options=factory_options,
            api_factory=api_factory,
            api=api,
            configure_execution=configure_execution,
            expected_exchange_names=expected_exchange_names,
        )

    def __init__(
        self,
        *,
        exchange_kwargs: Mapping[str, Any] | None = None,
        execution_config: Mapping[str, Any] | None = None,
        debug: Any = False,
        factory_options: Mapping[str, Any] | None = None,
        api_factory: Callable[..., Any] | None = None,
        api: Any = None,
        configure_execution: bool = False,
        expected_exchange_names: tuple[str, ...] | None = None,
    ) -> None:
        if exchange_kwargs is not None and not isinstance(exchange_kwargs, Mapping):
            raise DirectApiAssemblyError("DIRECT_API_EXCHANGE_CONFIG_INVALID")
        if execution_config is not None and not isinstance(execution_config, Mapping):
            raise DirectApiAssemblyError("DIRECT_API_EXECUTION_CONFIG_INVALID")
        if factory_options is not None and not isinstance(factory_options, Mapping):
            raise DirectApiAssemblyError("DIRECT_API_FACTORY_OPTIONS_INVALID")
        if api_factory is not None and not callable(api_factory):
            raise DirectApiAssemblyError("DIRECT_API_FACTORY_INVALID")
        if type(configure_execution) is not bool:
            raise DirectApiAssemblyError("DIRECT_API_EXECUTION_CONFIG_INVALID")
        if expected_exchange_names is not None and (
            type(expected_exchange_names) is not tuple
            or any(type(name) is not str or not name or name != name.strip()
                   for name in expected_exchange_names)
            or len(set(expected_exchange_names)) != len(expected_exchange_names)
        ):
            raise DirectApiAssemblyError("DIRECT_API_ROUTE_CONFIG_INVALID")

        self.__exchange_kwargs = (
            {} if exchange_kwargs is None else dict(exchange_kwargs)
        )
        self.__execution_config = (
            {} if execution_config is None else dict(execution_config)
        )
        self.__debug = debug
        self.__factory_options = self.__validate_factory_options(factory_options)
        self.__api_factory = api_factory
        self.__expected_exchange_names = expected_exchange_names
        self.__configure_execution = configure_execution
        self.__condition = Condition(RLock())
        self.__api = api
        self.__owns_api = api is None
        self.__busy = False
        self.__failed = False
        self.__failure_code = ""
        self.__closed_successfully = False
        self.__close_attempted = False
        self.__last_closed_api = None
        self.__initialized = False

    @property
    def api(self) -> Any:
        """Return the currently retained façade without creating one."""

        with self.__condition:
            return self.__api

    @property
    def owns_api(self) -> bool:
        """Whether this assembly created and owns the retained façade."""

        return self.__owns_api

    @property
    def exchange_names(self) -> tuple[str, ...]:
        """Return configured route names only; provider values stay private."""

        names = self.__expected_exchange_names
        if names is not None:
            return names
        return tuple(self.__exchange_kwargs)

    def execution_config_snapshot(self) -> dict[str, Any]:
        """Return a shallow snapshot for BT policy projection.

        The assembly remains the configuration owner.  A caller receives a
        new outer mapping and cannot replace the retained configuration.
        Nested objects keep identity, matching the former SDK handoff contract.
        """

        with self.__condition:
            return dict(self.__execution_config)

    def execution_config_value(self, name: str, default: Any = None) -> Any:
        """Read one explicitly configured BT-facing policy value."""

        if type(name) is not str:
            return default
        with self.__condition:
            return self.__execution_config.get(name, default)

    def update_execution_config(self, updates: Mapping[str, Any]) -> None:
        """Update retained configuration without applying an SDK transition.

        Store uses this for local fencing state such as ``market_data_only``;
        explicit SDK transitions still go through :meth:`configure_execution`.
        """

        if not isinstance(updates, Mapping):
            raise DirectApiAssemblyError("DIRECT_API_EXECUTION_CONFIG_INVALID")
        with self.__condition:
            if self.__busy or (self.__close_attempted and not self.__closed_successfully):
                raise DirectApiAssemblyError("DIRECT_API_LIFECYCLE_BUSY")
            self.__execution_config.update(updates)

    def configured_environment(self, exchange_name: str) -> Any:
        """Return an explicit configured environment for a Store consistency check.

        This is configuration projection only.  It is not evidence of the live
        provider, account, or session identity.
        """

        if type(exchange_name) is not str or not exchange_name:
            return None
        route = self.__exchange_kwargs.get(exchange_name)
        if not isinstance(route, Mapping):
            return None
        return route.get("environment")

    def ensure_api(self) -> Any:
        """Construct or return the validated façade exactly once per generation."""

        with self.__condition:
            while self.__busy:
                self.__condition.wait()
            if self.__failed:
                raise DirectApiAssemblyError(
                    self.__failure_code or "DIRECT_API_ASSEMBLY_UNAVAILABLE"
                )
            if self.__closed_successfully:
                self.__last_closed_api = self.__api
                self.__api = None
                self.__closed_successfully = False
                self.__close_attempted = False
                self.__initialized = False
                self.__owns_api = True
            if self.__initialized:
                return self.__api
            self.__busy = True
            api = self.__api
            created = api is None

        try:
            if created:
                factory = self.__api_factory
                if factory is None:
                    factory = self.__default_api_factory()
                api = factory(**self.__factory_kwargs())
                if api is self.__last_closed_api:
                    raise DirectApiAssemblyError(
                        "DIRECT_API_FACTORY_REUSED_CLOSED_INSTANCE"
                    )
            self.__validate_facade(api)
            self.__validate_routes(api)
            with self.__condition:
                self.__api = api
                self.__owns_api = created
            if self.__configure_execution:
                self._apply_execution_config(api, self.__execution_config)
        except BaseException as exc:
            self.sanitize_exception(exc)
            if created and api is not None and api is not self.__last_closed_api:
                self.__retain_failed_api(api)
            with self.__condition:
                self.__failed = True
                self.__failure_code = self._safe_failure_code(exc)
                self.__busy = False
                self.__condition.notify_all()
            raise

        with self.__condition:
            self.__initialized = True
            self.__busy = False
            self.__condition.notify_all()
            return self.__api

    def configure_execution(self, execution_config: Mapping[str, Any]) -> None:
        """Apply an explicit execution configuration to the retained façade."""

        if not isinstance(execution_config, Mapping):
            raise DirectApiAssemblyError("DIRECT_API_EXECUTION_CONFIG_INVALID")
        api = self.ensure_api()
        with self.__condition:
            if self.__busy or self.__close_attempted or self.__closed_successfully:
                raise DirectApiAssemblyError("DIRECT_API_LIFECYCLE_BUSY")
            self.__busy = True
            copied = dict(execution_config)
        try:
            self._apply_execution_config(api, copied)
        except BaseException as exc:
            self.sanitize_exception(exc)
            with self.__condition:
                self.__failed = True
                self.__failure_code = self._safe_failure_code(exc)
            raise
        finally:
            with self.__condition:
                self.__execution_config = copied
                self.__configure_execution = True
                self.__busy = False
                self.__condition.notify_all()

    def reapply_execution_config(self) -> None:
        """Reapply the SDK-owned full configuration without exporting it."""

        self.configure_execution(self.execution_config_snapshot())

    def close_api(self) -> None:
        """Close an owned façade once; borrowed façades remain caller-owned."""

        with self.__condition:
            while self.__busy:
                self.__condition.wait()
            if not self.__owns_api or self.__api is None:
                return
            if self.__closed_successfully:
                return
            if self.__close_attempted:
                raise DirectApiAssemblyError("DIRECT_API_CLOSE_OUTCOME_UNCERTAIN")
            self.__close_attempted = True
            api = self.__api
        close = getattr(api, "close", None)
        if not callable(close):
            with self.__condition:
                self.__failed = True
                self.__failure_code = "DIRECT_API_CLOSE_UNAVAILABLE"
            raise DirectApiAssemblyError("DIRECT_API_CLOSE_UNAVAILABLE")
        try:
            close()
        except BaseException as exc:
            self.sanitize_exception(exc)
            with self.__condition:
                self.__failed = True
                self.__failure_code = "DIRECT_API_CLOSE_FAILED"
            raise
        with self.__condition:
            self.__closed_successfully = True
            self.__last_closed_api = api

    def redact_runtime_value(self, value: Any) -> Any:
        """Redact diagnostics using the private direct configuration snapshot."""

        projected = sanitize_value(value)
        secrets = self.__sensitive_values(
            {
                "exchange_kwargs": self.__exchange_kwargs,
                "execution_config": self.__execution_config,
                "factory_options": self.__factory_options,
            }
        )
        return self.__replace_secrets(projected, secrets)

    def sanitize_exception(self, exc: BaseException) -> BaseException:
        """Redact exception arguments and attributes without logging values."""

        secrets = self.__sensitive_values(
            {
                "exchange_kwargs": self.__exchange_kwargs,
                "execution_config": self.__execution_config,
                "factory_options": self.__factory_options,
            }
        )
        try:
            exc.args = tuple(
                self.__replace_secrets(sanitize_value(item), secrets) for item in exc.args
            )
        except Exception:
            exc.args = ("DIRECT_API_EXCEPTION_REDACTION_FAILED",)
        try:
            for key, value in vars(exc).items():
                setattr(exc, key, self.__replace_secrets(sanitize_value(value), secrets))
        except Exception:
            pass
        return exc

    def __factory_kwargs(self) -> dict[str, Any]:
        # A retained policy snapshot is not itself a request for the optional
        # execution session.  Preserve an explicit read-only fence when a
        # metadata probe prepares the client, even though that probe applies
        # its full configuration separately after construction.
        configure_at_creation = (
            self.__configure_execution
            or self.__execution_config.get("market_data_only") is True
        )
        values = {
            "exchange_kwargs": dict(self.__exchange_kwargs),
            "execution_config": (
                dict(self.__execution_config) if configure_at_creation else None
            ),
            "debug": self.__debug,
        }
        values.update(self.__factory_options)
        return values

    @classmethod
    def __validate_factory_options(
        cls, factory_options: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        if factory_options is None:
            return {}
        if any(type(key) is not str or key not in cls._OPTIONAL_FACTORY_OPTIONS
               for key in factory_options):
            raise DirectApiAssemblyError("DIRECT_API_FACTORY_OPTIONS_INVALID")
        return dict(factory_options)

    @staticmethod
    def __default_api_factory() -> Any:
        try:
            from .bt_api import BtApi
        except Exception:
            raise DirectApiAssemblyError("DIRECT_API_DEFAULT_FACTORY_UNAVAILABLE") from None
        return BtApi

    @staticmethod
    def __validate_facade(api: Any) -> None:
        try:
            valid = is_unified_bt_api_facade(api)
        except Exception:
            valid = False
        if not valid:
            raise DirectApiAssemblyError("DIRECT_API_FACADE_INVALID")

    def __validate_routes(self, api: Any) -> None:
        try:
            names = api.list_exchanges()
        except Exception:
            raise DirectApiAssemblyError("DIRECT_API_ROUTE_PROJECTION_UNAVAILABLE") from None
        if type(names) is not list or any(type(name) is not str or not name for name in names):
            raise DirectApiAssemblyError("DIRECT_API_ROUTE_PROJECTION_INVALID")
        if len(set(names)) != len(names):
            raise DirectApiAssemblyError("DIRECT_API_ROUTE_PROJECTION_INVALID")
        expected = self.__expected_exchange_names
        if expected is None and self.__exchange_kwargs:
            expected = tuple(self.__exchange_kwargs)
        if expected is not None and set(names) != set(expected):
            raise DirectApiAssemblyError("DIRECT_API_ROUTE_MISMATCH")

    @staticmethod
    def _apply_execution_config(api: Any, execution_config: Mapping[str, Any]) -> None:
        try:
            configurable = is_execution_configurable_facade(api)
        except Exception:
            configurable = False
        if not configurable:
            raise DirectApiAssemblyError("DIRECT_API_EXECUTION_CONFIG_UNAVAILABLE")
        api.configure_execution(dict(execution_config))

    def __retain_failed_api(self, api: Any) -> None:
        with self.__condition:
            self.__api = api
            self.__owns_api = True

    @staticmethod
    def _safe_failure_code(exc: BaseException) -> str:
        code = getattr(exc, "code", None)
        if type(code) is str and code.isascii() and code and len(code) <= 128:
            if all(char.isalnum() or char in "._:-" for char in code):
                return code
        return "DIRECT_API_ASSEMBLY_FAILED"

    @classmethod
    def __sensitive_values(cls, value: Any, active: set[int] | None = None) -> tuple[str, ...]:
        found: set[str] = set()
        seen = set() if active is None else active

        def walk(item: Any) -> None:
            identity = id(item)
            if identity in seen:
                return
            if isinstance(item, Mapping):
                seen.add(identity)
                try:
                    for key, nested in item.items():
                        if is_sensitive_key(key):
                            if isinstance(nested, str) and len(nested) >= 4:
                                found.add(nested)
                            elif isinstance(nested, (bytes, bytearray)) and len(nested) >= 4:
                                for encoding in ("utf-8", "latin-1"):
                                    try:
                                        decoded = bytes(nested).decode(encoding)
                                    except UnicodeDecodeError:
                                        continue
                                    if len(decoded) >= 4:
                                        found.add(decoded)
                            elif isinstance(nested, int) and not isinstance(nested, bool):
                                text = str(abs(nested))
                                if len(text) >= 8:
                                    found.add(text)
                        walk(nested)
                finally:
                    seen.discard(identity)
            elif isinstance(item, (list, tuple, set, frozenset)):
                seen.add(identity)
                try:
                    for nested in item:
                        walk(nested)
                finally:
                    seen.discard(identity)

        walk(value)
        return tuple(found)

    @classmethod
    def __replace_secrets(cls, value: Any, secrets: tuple[str, ...]) -> Any:
        if isinstance(value, Mapping):
            return {key: cls.__replace_secrets(item, secrets) for key, item in value.items()}
        if isinstance(value, list):
            return [cls.__replace_secrets(item, secrets) for item in value]
        if isinstance(value, tuple):
            return tuple(cls.__replace_secrets(item, secrets) for item in value)
        if isinstance(value, set):
            return {cls.__replace_secrets(item, secrets) for item in value}
        if isinstance(value, frozenset):
            return frozenset(cls.__replace_secrets(item, secrets) for item in value)
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "***")
        elif isinstance(value, bytes):
            for secret in secrets:
                for encoding in ("utf-8", "latin-1"):
                    try:
                        encoded = secret.encode(encoding)
                    except UnicodeEncodeError:
                        continue
                    if encoded:
                        value = value.replace(encoded, b"***")
        elif isinstance(value, bytearray):
            for secret in secrets:
                for encoding in ("utf-8", "latin-1"):
                    try:
                        encoded = secret.encode(encoding)
                    except UnicodeEncodeError:
                        continue
                    if encoded:
                        value = value.replace(encoded, b"***")
        return value

    def __repr__(self) -> str:
        with self.__condition:
            state = "failed" if self.__failed else "closed" if self.__closed_successfully else (
                "ready" if self.__initialized else "pending"
            )
            return "<DirectApiAssembly routes={} state={}>".format(
                len(self.exchange_names), state
            )
