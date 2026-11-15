"""Legacy SDK service execution assembly and lifecycle types.

New assembly through the legacy ``ClientConfig`` route pair is retired.
This module is separate from ``managed_runtime_composition``: that API composes
provider-owned ``ProviderConfiguration`` routes and does not construct
``ServiceClientRuntime`` or ``ServiceExecutionClient``.

Configured bindings are requests to the service.  They do not grant
authorization; ``ServiceClientRuntime.start`` still registers them against the
service and validates the returned leases.  This assembly owns the SDK runtime
objects after ``claim_owned_runtimes``.  It has no Backtrader or Cerebro
dependency and never accepts a Backtrader Order.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any

from .service_config import ClientConfig
from .service_execution import ServiceExecutionClient
from .service_recovery import ServiceRecoveryFacadePort, ServiceRecoveryFlowPort
from .service_runtime import ServiceClientRuntime

if TYPE_CHECKING:
    from .service_execution_monitor_composition import (
        ServiceExecutionMonitorComposition,
    )

__all__ = (
    "ServiceExecutionAssembly",
    "ServiceExecutionAssemblyError",
    "ServiceExecutionRegistration",
    "ServiceExecutionRoutePlan",
    "ServiceExecutionRuntimeFact",
    "ServiceExecutionRuntimeSummary",
    "assemble_execution_monitor_relay",
    "assemble_service_execution",
    "safe_nonnegative_int",
    "safe_runtime_metric",
    "safe_runtime_state",
    "safe_service_execution_error_code",
    "summarize_runtime_facts",
)

# Preserve the established runtime fence name so an older BT session cannot
# concurrently claim a runtime assembled here. The value remains this SDK
# assembly object; no Backtrader object is imported or required.
_OWNER_ATTRIBUTE = "_backtrader_service_execution_session_owner"
_RUNTIME_OWNERSHIP_LOCK = threading.RLock()
_WRITER_MOUNT = "broker.orders"
_QUERY_MOUNT = "broker.order_query"
_WRITER_OPERATIONS = frozenset({"orders.submit", "orders.cancel"})
_QUERY_OPERATIONS = frozenset({"orders.query"})
_ACCOUNT_BOOK_OPERATION = "execution.account_books.query"


class ServiceExecutionAssemblyError(RuntimeError):
    """Finite SDK assembly or lifecycle error with a stable ``code``."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not code or len(code) > 96:
            code = "SERVICE_EXECUTION_ASSEMBLY_FAILED"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ServiceExecutionRoutePlan:
    """The two validated configured execution route requests."""

    writer_mount: str
    writer_scope_ref: str
    query_mount: str
    query_scope_ref: str

    def __post_init__(self) -> None:
        if (
            self.writer_mount != _WRITER_MOUNT
            or self.query_mount != _QUERY_MOUNT
            or not _nonempty_text(self.writer_scope_ref)
            or not _nonempty_text(self.query_scope_ref)
        ):
            raise ValueError("execution route plan is invalid")


@dataclass(frozen=True)
class ServiceExecutionRegistration:
    """Validated server registration facts for the writer/query route pair."""

    writer_mount: str
    writer_scope_ref: str
    query_mount: str
    query_scope_ref: str
    strategy_ref: str
    account_ref: str

    def __post_init__(self) -> None:
        if (
            self.writer_mount != _WRITER_MOUNT
            or self.query_mount != _QUERY_MOUNT
            or not _nonempty_text(self.writer_scope_ref)
            or not _nonempty_text(self.query_scope_ref)
            or not _nonempty_text(self.strategy_ref)
            or not _nonempty_text(self.account_ref)
        ):
            raise ValueError("execution registration is invalid")


@dataclass(frozen=True)
class ServiceExecutionRuntimeFact:
    """Finite post-lifecycle facts for one owned runtime."""

    state: str | None
    unregister_uncertainties: int | None
    cleanup_failures: int | None


@dataclass(frozen=True)
class ServiceExecutionRuntimeSummary:
    """Aggregate observations for the assembly's ordered runtime set."""

    states: tuple[str | None, ...]
    primary_state: str | None
    unregister_uncertainties: int | None
    cleanup_failures: int | None


def summarize_runtime_facts(runtime_facts: Any, runtimes: tuple[Any, ...]) -> ServiceExecutionRuntimeSummary:
    """Summarize owned runtime facts, retaining the original read fallback."""
    if type(runtime_facts) is tuple and len(runtime_facts) == len(runtimes):
        states = tuple(fact.state for fact in runtime_facts)
        unregister_values = tuple(
            safe_nonnegative_int(fact.unregister_uncertainties) for fact in runtime_facts
        )
        cleanup_values = tuple(
            safe_nonnegative_int(fact.cleanup_failures) for fact in runtime_facts
        )
    else:
        states = tuple(safe_runtime_state(runtime) for runtime in runtimes)
        unregister_values = tuple(
            safe_runtime_metric(runtime, "unregister_uncertainties") for runtime in runtimes
        )
        cleanup_values = tuple(
            safe_runtime_metric(runtime, "cleanup_failures") for runtime in runtimes
        )
    return ServiceExecutionRuntimeSummary(
        states=states,
        primary_state=states[0] if states else None,
        unregister_uncertainties=(
            sum(unregister_values) if all(value is not None for value in unregister_values) else None
        ),
        cleanup_failures=(
            sum(cleanup_values) if all(value is not None for value in cleanup_values) else None
        ),
    )


class ServiceExecutionAssembly:
    """Legacy execution assembly; new construction is retired.

    The recovery facade is borrowed. The SDK creates the route-bound recovery
    flow but does not close the facade, which remains owned by its creator.
    New claims and starts are retired. Existing claimed objects retain their
    maintenance, release-before-start and close methods.

    An existing owner remains responsible for its established cleanup path;
    rejecting a new start does not release or close its runtimes.
    """

    def __init__(
        self,
        runtime: ServiceClientRuntime,
        *,
        execution_client: ServiceExecutionClient,
        routes: ServiceExecutionRoutePlan,
        recovery_facade: ServiceRecoveryFacadePort | None = None,
        recovery_flow: ServiceRecoveryFlowPort | None = None,
        logical_run_id: str | None = None,
        recovery_event_version: int = 1,
        recovery_callback: Callable[[Any], Any] | None = None,
    ) -> None:
        raise ServiceExecutionAssemblyError("LEGACY_SERVICE_EXECUTION_RETIRED")

    @property
    def runtime(self) -> ServiceClientRuntime:
        """Return the primary runtime for existing framework adapters."""

        return self._runtime

    @property
    def execution_client(self) -> ServiceExecutionClient:
        """Return the SDK-created neutral execution client."""

        return self._execution_client

    @property
    def recovery_facade(self) -> ServiceRecoveryFacadePort | None:
        """Return the borrowed recovery facade, if configured."""

        return self._recovery_facade

    @property
    def recovery_flow(self) -> ServiceRecoveryFlowPort | None:
        """Return the SDK-created recovery flow, if configured."""

        return self._recovery_flow

    @property
    def logical_run_id(self) -> str | None:
        """Return the exact configured recovery namespace run ID."""

        return self._logical_run_id

    @property
    def recovery_event_version(self) -> int:
        """Return the validated recovery-event policy used to create the flow."""

        return self._recovery_event_version

    @property
    def recovery_callback(self) -> Callable[[Any], Any] | None:
        """Return the caller callback for the host adapter to consume.

        The callback belongs to the host layer and is never invoked by this
        SDK assembly. It is retained here so assembly does not silently discard
        a configured recovery policy value.
        """

        return self._recovery_callback

    @property
    def routes(self) -> ServiceExecutionRoutePlan:
        return self._routes

    @property
    def owned_runtimes(self) -> tuple[ServiceClientRuntime, ...]:
        return self._owned_runtimes

    @property
    def registration(self) -> ServiceExecutionRegistration | None:
        return self._registration

    @property
    def runtime_facts(self) -> tuple[ServiceExecutionRuntimeFact, ...]:
        return self._runtime_facts

    @property
    def close_errors(self) -> tuple[str, ...]:
        return self._close_errors

    @property
    def state(self) -> str:
        return self._state

    @property
    def start_attempted(self) -> bool:
        return bool(self._start_attempted_ids)

    @property
    def close_attempted(self) -> bool:
        return self._close_attempted

    def claim_owned_runtimes(
        self,
        additional_owned_runtimes: tuple[ServiceClientRuntime, ...] = (),
    ) -> None:
        """Reject new ownership claims for the retired execution path."""

        raise ServiceExecutionAssemblyError("LEGACY_SERVICE_EXECUTION_RETIRED")

    def release_unstarted(self) -> tuple[str, ...]:
        """Release owner markers if no runtime start was attempted."""

        self._assert_owner_thread()
        if not self._claimed:
            return ()
        if self._start_attempted_ids or self._close_attempted:
            raise ServiceExecutionAssemblyError("RUNTIME_OWNERSHIP_ALREADY_CONSUMED")
        errors: list[str] = []
        with _RUNTIME_OWNERSHIP_LOCK:
            for runtime in reversed(self._owned_runtimes):
                try:
                    if getattr(runtime, _OWNER_ATTRIBUTE, None) is self:
                        delattr(runtime, _OWNER_ATTRIBUTE)
                except Exception:
                    errors.append("RUNTIME_OWNER_RELEASE_FAILED")
        self._claimed = False
        self._released = True
        self._state = "released" if not errors else "close_failed"
        return tuple(dict.fromkeys(errors))

    def start(self) -> ServiceExecutionRegistration:
        """Reject starting the retired execution path."""

        raise ServiceExecutionAssemblyError("LEGACY_SERVICE_EXECUTION_RETIRED")

    def maintain(self) -> None:
        """Maintain every runtime whose start was attempted."""

        self._assert_owner_thread()
        if self._state != "registered" or self._close_attempted:
            raise ServiceExecutionAssemblyError("RUNTIME_NOT_REGISTERED")
        for runtime in self._owned_runtimes:
            if id(runtime) in self._start_attempted_ids:
                runtime.maintain()

    def close(self) -> None:
        """Attempt close on all owned runtimes in reverse acquisition order.

        The first original exception is re-raised after every close has been
        attempted. Its public ``code`` is separately available through
        ``close_errors``; no exception text is stored in this assembly.
        """

        self._assert_owner_thread()
        if not self._claimed:
            raise ServiceExecutionAssemblyError("RUNTIME_OWNERSHIP_NOT_TRANSFERRED")
        if self._close_attempted:
            raise ServiceExecutionAssemblyError("RUNTIME_CLOSE_ALREADY_ATTEMPTED")
        self._close_attempted = True
        self._state = "closing"
        first_error: BaseException | None = None
        first_traceback: TracebackType | None = None
        errors: list[str] = []
        for runtime in reversed(self._owned_runtimes):
            try:
                runtime.close()
            except BaseException as error:
                errors.append(safe_service_execution_error_code(error, "SERVICE_RUNTIME_CLOSE_FAILED"))
                if first_error is None:
                    first_error = error
                    first_traceback = error.__traceback__

        facts = tuple(_runtime_fact(runtime) for runtime in self._owned_runtimes)
        if any(fact.state == "close_failed" for fact in facts):
            errors.append("SERVICE_RUNTIME_CLOSE_FAILED")
        if any(
            fact.unregister_uncertainties is not None
            and fact.unregister_uncertainties > 0
            for fact in facts
        ):
            errors.append("UNREGISTER_UNCERTAIN")
        self._runtime_facts = facts
        self._close_errors = tuple(dict.fromkeys(errors))
        self._state = "close_failed" if any(
            error != "UNREGISTER_UNCERTAIN" for error in self._close_errors
        ) else ("closed_with_uncertainty" if self._close_errors else "closed")

        if first_error is not None:
            raise first_error.with_traceback(first_traceback)

    def _assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ServiceExecutionAssemblyError("WRONG_RUNTIME_THREAD")


def assemble_service_execution(
    client_config: object,
    *,
    runtime_factory: Callable[[ClientConfig], ServiceClientRuntime] | None = None,
    recovery_facade: ServiceRecoveryFacadePort | None = None,
    logical_run_id: str | None = None,
    recovery_callback: Callable[[Any], Any] | None = None,
    recovery_event_version: int = 1,
) -> ServiceExecutionAssembly:
    """Reject new legacy service assemblies before parsing or resource setup.

    Existing assemblies and their lifecycle remain outside this entry-point
    retirement. Callers supporting an unconfigured run choose that path before
    invoking this function.
    """

    raise ServiceExecutionAssemblyError("LEGACY_SERVICE_EXECUTION_RETIRED")


def assemble_execution_monitor_relay(
    *,
    source: str,
    execution_domain_ref: str,
    account_ref: str,
    strategy_ref: str,
    checkpoint_path: str | Path | None = None,
    checkpoint_dir: str | Path | None = None,
    runtime: ServiceClientRuntime | None = None,
    journal_reader: object | None = None,
    projection: object | None = None,
    scope_ref: str | None = None,
    sink: object | None = None,
    consumer_id: str | None = None,
    page_limit: int | None = None,
    max_events_per_poll: int | None = None,
    byte_budget: int | None = None,
) -> ServiceExecutionMonitorComposition:
    """Compose exactly one explicit Execution -> Monitor relay lifetime.

    The selection, trusted references and handles are explicit constructor
    arguments because no relay-selection key is frozen in the service config
    schema yet.  The factory imports only the selected relay module and starts
    no thread, timer or network connection.  The caller owns the returned
    ``start``/``maintain``/``poll``/``stop`` lifetime on its construction
    thread, and the borrowed runtime/reader/projection are never closed here.
    """

    from .service_execution_monitor_composition import (
        compose_execution_monitor_relay,
    )

    return compose_execution_monitor_relay(
        source=source,
        execution_domain_ref=execution_domain_ref,
        account_ref=account_ref,
        strategy_ref=strategy_ref,
        checkpoint_path=checkpoint_path,
        checkpoint_dir=checkpoint_dir,
        runtime=runtime,
        journal_reader=journal_reader,
        projection=projection,
        scope_ref=scope_ref,
        sink=sink,
        consumer_id=consumer_id,
        page_limit=page_limit,
        max_events_per_poll=max_events_per_poll,
        byte_budget=byte_budget,
    )


def _configured_execution_routes(parsed: ClientConfig) -> ServiceExecutionRoutePlan:
    if type(parsed) is not ClientConfig:
        raise ServiceExecutionAssemblyError("INVALID_CLIENT_CONFIG")
    if getattr(parsed, "process_role", None) != "strategy_client":
        raise ServiceExecutionAssemblyError("UNSUPPORTED_CLIENT_ROLE")
    clients = getattr(parsed, "clients", None)
    if not isinstance(clients, Mapping):
        raise ServiceExecutionAssemblyError("CLIENTS_UNAVAILABLE")
    if set(clients.keys()) != {"execution"}:
        raise ServiceExecutionAssemblyError("UNSUPPORTED_CLIENT_SET")

    execution = clients.get("execution")
    settings = execution.get("settings") if isinstance(execution, Mapping) else None
    bindings = settings.get("bindings") if isinstance(settings, Mapping) else None
    if type(bindings) is not tuple or len(bindings) != 2:
        raise ServiceExecutionAssemblyError("UNSUPPORTED_EXECUTION_BINDINGS")

    by_mount: dict[str, Mapping[str, Any]] = {}
    for binding in bindings:
        if not isinstance(binding, Mapping):
            raise ServiceExecutionAssemblyError("INVALID_EXECUTION_BINDING")
        mount = binding.get("mount")
        if mount not in {_WRITER_MOUNT, _QUERY_MOUNT} or mount in by_mount:
            raise ServiceExecutionAssemblyError("UNSUPPORTED_EXECUTION_BINDINGS")
        by_mount[mount] = binding

    if set(by_mount) != {_WRITER_MOUNT, _QUERY_MOUNT}:
        raise ServiceExecutionAssemblyError("UNSUPPORTED_EXECUTION_BINDINGS")
    shared_fields = ("service_id", "instance_id", "account_ref", "transport_ref")
    writer = by_mount[_WRITER_MOUNT]
    query = by_mount[_QUERY_MOUNT]
    if any(
        type(writer.get(field)) is not str
        or not writer.get(field)
        or writer.get(field) != query.get(field)
        for field in shared_fields
    ):
        raise ServiceExecutionAssemblyError("EXECUTION_BINDING_MISMATCH")

    for binding in (writer, query):
        if binding.get("required") is not True:
            raise ServiceExecutionAssemblyError("EXECUTION_BINDING_NOT_REQUIRED")
    writer_scope = writer.get("scope_ref")
    query_scope = query.get("scope_ref")
    if not _nonempty_text(writer_scope) or not _nonempty_text(query_scope):
        raise ServiceExecutionAssemblyError("EXECUTION_BINDING_NOT_REQUIRED")
    return ServiceExecutionRoutePlan(
        writer_mount=_WRITER_MOUNT,
        writer_scope_ref=writer_scope,
        query_mount=_QUERY_MOUNT,
        query_scope_ref=query_scope,
    )


def _configured_command_binding(parsed: ClientConfig) -> Any:
    """The writer route's ``ServiceCommandBinding`` (single SDK derivation)."""

    from .service_command_wire import ServiceCommandWireError, derive_command_binding

    bindings = parsed.clients["execution"]["settings"]["bindings"]
    writer = next(binding for binding in bindings if binding.get("mount") == _WRITER_MOUNT)
    declaration = writer.get("command_binding")
    if declaration is None:
        raise ServiceExecutionAssemblyError("COMMAND_BINDING_REQUIRED")
    try:
        return derive_command_binding(
            declaration,
            service_id=writer["service_id"],
            instance_id=writer["instance_id"],
            account_ref=writer["account_ref"],
        )
    except ServiceCommandWireError as error:
        raise ServiceExecutionAssemblyError(error.code) from None


def _validate_registered_execution_routes(
    runtime: ServiceClientRuntime,
    routes: ServiceExecutionRoutePlan,
) -> ServiceExecutionRegistration:
    from .service_contracts import WireScope

    if safe_runtime_state(runtime) != "registered":
        raise ServiceExecutionAssemblyError("RUNTIME_NOT_REGISTERED")
    try:
        bindings = runtime.bindings
    except Exception:
        raise ServiceExecutionAssemblyError("RUNTIME_BINDINGS_UNAVAILABLE") from None
    if type(bindings) is not tuple:
        raise ServiceExecutionAssemblyError("RUNTIME_BINDINGS_INVALID")

    writer = _unique_active_route(
        bindings,
        routes.writer_mount,
        routes.writer_scope_ref,
        _WRITER_OPERATIONS,
    )
    query = _unique_active_route(
        bindings,
        routes.query_mount,
        routes.query_scope_ref,
        _QUERY_OPERATIONS,
    )
    writer_scope = getattr(writer, "scope", None)
    query_scope = getattr(query, "scope", None)
    if type(writer_scope) is not WireScope or type(query_scope) is not WireScope:
        raise ServiceExecutionAssemblyError("RUNTIME_SCOPE_INVALID")
    if writer_scope != query_scope:
        raise ServiceExecutionAssemblyError("EXECUTION_QUERY_SCOPE_MISMATCH")
    strategy_ref = getattr(writer_scope, "strategy_ref", None)
    account_ref = getattr(writer_scope, "account_ref", None)
    if not _nonempty_text(strategy_ref) or not _nonempty_text(account_ref):
        raise ServiceExecutionAssemblyError("EXECUTION_SCOPE_INCOMPLETE")
    return ServiceExecutionRegistration(
        writer_mount=routes.writer_mount,
        writer_scope_ref=routes.writer_scope_ref,
        query_mount=routes.query_mount,
        query_scope_ref=routes.query_scope_ref,
        strategy_ref=strategy_ref,
        account_ref=account_ref,
    )


def _unique_active_route(
    bindings: tuple[Any, ...],
    mount: str,
    scope_ref: str,
    operations: frozenset[str],
) -> Any:
    matches = tuple(
        binding
        for binding in bindings
        if getattr(binding, "active", False) is True
        and getattr(binding, "mount", None) == mount
        and getattr(binding, "scope_ref", None) == scope_ref
    )
    if len(matches) != 1:
        raise ServiceExecutionAssemblyError("RUNTIME_ROUTE_MISSING_OR_AMBIGUOUS")
    binding = matches[0]
    if getattr(binding, "required", None) is not True:
        raise ServiceExecutionAssemblyError("RUNTIME_ROUTE_NOT_REQUIRED")
    granted = getattr(binding, "operations", None)
    if type(granted) not in (frozenset, set) or not operations <= granted:
        raise ServiceExecutionAssemblyError("RUNTIME_ROUTE_OPERATION_MISSING")
    return binding


def _runtime_fact(runtime: ServiceClientRuntime) -> ServiceExecutionRuntimeFact:
    return ServiceExecutionRuntimeFact(
        state=safe_runtime_state(runtime),
        unregister_uncertainties=safe_runtime_metric(runtime, "unregister_uncertainties"),
        cleanup_failures=safe_runtime_metric(runtime, "cleanup_failures"),
    )


def safe_runtime_state(runtime: ServiceClientRuntime) -> str | None:
    try:
        state = runtime.state
    except BaseException:
        return None
    if type(state) is str and state.isascii() and len(state) <= 64:
        return state
    return None


def safe_nonnegative_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def safe_runtime_metric(runtime: ServiceClientRuntime, name: str) -> int | None:
    try:
        value = getattr(runtime, name, None)
    except BaseException:
        return None
    return safe_nonnegative_int(value)


def safe_service_execution_error_code(error: BaseException, fallback: str) -> str:
    code = getattr(error, "code", None)
    if (
        type(code) is str
        and 0 < len(code) <= 64
        and code.isascii()
        and all(character.isalnum() or character in "_.-" for character in code)
    ):
        return code
    return fallback


def _recovery_read_failure_code(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return "recovery_read_timeout"
    return safe_service_execution_error_code(error, "recovery_read_failed").lower()


def _nonempty_text(value: object) -> bool:
    """Match the legacy route validator's exact nonempty-string rule."""

    return type(value) is str and bool(value)


