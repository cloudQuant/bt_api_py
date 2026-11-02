"""SDK host-resource composition for Monitor threshold workers.

Monitor owns rules, worker lifecycle, and trusted target definitions. This
module wires them to the SDK host's exact resource ownership contract.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from contextlib import suppress
from types import MappingProxyType
from typing import Callable

from bt_api_base.service_protocol import WireScope
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.threshold_service import (
    ThresholdRuleConsumerSupervisor,
    ThresholdServiceError,
    TrustedThresholdTarget,
    parse_threshold_service_config,
    validate_threshold_reference,
)


class MonitorThresholdResourceProvider:
    """Wrap a normal Host provider and own a scoped threshold worker at start.

    Construct this in the trusted local operator using a fixed parsed service
    config, Registry-derived grants/targets, and the existing provider that
    supplies Monitor's writable outbox.  It is not a wire-facing API.
    """

    def __init__(
        self,
        resource_provider: Callable[..., object],
        *,
        monitor_instance_id: str,
        config_document: bytes | str,
        trusted_targets: Mapping[str, TrustedThresholdTarget],
        startup_timeout_seconds: float = 10.0,
        shutdown_timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        if not callable(resource_provider):
            raise ThresholdServiceError("BASE_RESOURCE_PROVIDER_REQUIRED")
        validate_threshold_reference(monitor_instance_id, "MONITOR_INSTANCE_INVALID")
        self._provider = resource_provider
        self._instance_id = monitor_instance_id
        self._targets = MappingProxyType(dict(trusted_targets))
        self._config = parse_threshold_service_config(config_document, self._targets)
        self._startup_timeout = startup_timeout_seconds
        self._shutdown_timeout = shutdown_timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._supervisor_lock = threading.Lock()
        self._supervisor: ThresholdRuleConsumerSupervisor | None = None

    @property
    def status(self) -> Mapping[str, object]:
        """Finite local operator view of this provider's owned child."""

        with self._supervisor_lock:
            supervisor = self._supervisor
        if supervisor is None:
            return MappingProxyType(
                {
                    "state": "not_started",
                    "ready": False,
                    "process_id": None,
                    "start_count": 0,
                    "restart_count": 0,
                    "last_exit_code": None,
                    "last_failure_code": None,
                    "outstanding_pipe_count": 0,
                    "closed": False,
                }
            )
        return supervisor.status

    def close(self) -> None:
        """Retry cleanup when a prior Host/startup close was uncertain."""

        with self._supervisor_lock:
            supervisor = self._supervisor
        if supervisor is not None:
            state = supervisor.status["state"]
            if state == "closed":
                return
            if not isinstance(state, str) or not state.endswith("_uncertain"):
                raise ThresholdServiceError("SUPERVISOR_CLOSE_RETRY_NOT_PENDING")
            supervisor.close()

    def __call__(
        self,
        component: str,
        instance_id: str,
        options: Mapping[str, object],
        scope: WireScope,
    ):
        resources = self._provider(component, instance_id, options, scope)
        if component != "monitor" or instance_id != self._instance_id:
            return resources
        supervisor: ThresholdRuleConsumerSupervisor | None = None
        try:
            if not isinstance(options, Mapping) or options.get("role") != "durable_ingress":
                raise ThresholdServiceError("DURABLE_INGRESS_ROLE_REQUIRED")
            if type(scope) is not WireScope:
                raise ThresholdServiceError("HOST_SCOPE_INVALID")
            _validate_bound_targets(self._targets, resources, scope)
            outbox = resources.values.get("outbox")  # type: ignore[attr-defined]
            if type(outbox) is not DurableOutbox or outbox.is_read_only:
                raise ThresholdServiceError("WRITABLE_OUTBOX_REQUIRED")
            supervisor = ThresholdRuleConsumerSupervisor(
                str(outbox.database_path),
                self._config,
                startup_timeout_seconds=self._startup_timeout,
                shutdown_timeout_seconds=self._shutdown_timeout,
                poll_interval_seconds=self._poll_interval,
            )
            with self._supervisor_lock:
                current = self._supervisor
                if current is not None and current.status["state"] != "closed":
                    raise ThresholdServiceError("THRESHOLD_SUPERVISOR_ALREADY_BOUND")
                self._supervisor = supervisor
            supervisor.start()
            return _owned_resources_with_supervisor(resources, supervisor)
        except BaseException as primary:
            cleanup: list[str] = []
            if supervisor is not None:
                with self._supervisor_lock:
                    if self._supervisor is None:
                        self._supervisor = supervisor
                try:
                    supervisor.close()
                except Exception:
                    cleanup.append("THRESHOLD_WORKER_CLOSE_FAILED")
            try:
                resources.close()  # type: ignore[attr-defined]
            except Exception:
                cleanup.append("BASE_RESOURCE_CLOSE_FAILED")
            if cleanup:
                with suppress(Exception):
                    setattr(  # noqa: B010 - preserve the original startup exception.
                        primary, "threshold_cleanup_codes", tuple(cleanup)
                    )
            raise


def create_monitor_threshold_resource_provider(
    resource_provider: Callable[..., object],
    *,
    monitor_instance_id: str,
    config_document: bytes | str,
    trusted_targets: Mapping[str, TrustedThresholdTarget],
    startup_timeout_seconds: float = 10.0,
    shutdown_timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.05,
) -> MonitorThresholdResourceProvider:
    """Create the callable passed to normal ``ServiceHost`` startup.

    The operator supplies the already-selected Monitor resource provider,
    bounded rule document, and target map built from the deployment's exact
    Registry grants.  Parsing and scope binding happen here, before Host start;
    the spawned consumer is then started/closed with the Monitor component's
    ordinary resource lifecycle.
    """

    return MonitorThresholdResourceProvider(
        resource_provider,
        monitor_instance_id=monitor_instance_id,
        config_document=config_document,
        trusted_targets=trusted_targets,
        startup_timeout_seconds=startup_timeout_seconds,
        shutdown_timeout_seconds=shutdown_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def _owned_resources_with_supervisor(resources: object, supervisor: ThresholdRuleConsumerSupervisor):
    try:
        from bt_api_py.service_components import ComponentResources
    except Exception:
        raise ThresholdServiceError("HOST_RESOURCE_API_UNAVAILABLE") from None
    if type(resources) is not ComponentResources:
        raise ThresholdServiceError("HOST_RESOURCES_INVALID")

    def close_owned() -> None:
        first: BaseException | None = None
        try:
            supervisor.close()
        except BaseException as exc:
            first = exc
        try:
            resources.close()
        except BaseException as exc:
            if first is None:
                first = exc
        if first is not None:
            raise first

    return ComponentResources.owned(
        resources.values,
        ready=lambda: resources.ready() is True and supervisor.ready,
        close=close_owned,
        internal_ports=resources.internal_ports,
        risk_policy_bindings=resources.risk_policy_bindings,
        shared_collateral_domains=resources.shared_collateral_domains,
    )


def _validate_bound_targets(
    trusted_targets: Mapping[str, TrustedThresholdTarget], resources: object, host_scope: WireScope
) -> None:
    values = getattr(resources, "values", None)
    targets = values.get("targets") if isinstance(values, Mapping) else None
    if not isinstance(targets, Mapping) or len(targets) != len(trusted_targets):
        raise ThresholdServiceError("INGRESS_TARGET_BINDING_MISMATCH")
    for binding in trusted_targets.values():
        if binding.scope.execution_domain_ref != host_scope.execution_domain_ref:
            raise ThresholdServiceError("TARGET_DOMAIN_MISMATCH")
        for field in ("account_ref", "strategy_ref", "portfolio_ref", "manual_book_ref"):
            expected = getattr(host_scope, field)
            if expected is not None and getattr(binding.scope, field) != expected:
                raise ThresholdServiceError("TARGET_OUTSIDE_HOST_SCOPE")
        if targets.get(binding.scope) != binding.ingress_target:
            raise ThresholdServiceError("INGRESS_TARGET_BINDING_MISMATCH")


__all__ = ("MonitorThresholdResourceProvider", "create_monitor_threshold_resource_provider")
