"""Trusted process lifecycle for a service-v2 Gateway operator deployment.

The deployment factory is registered by installed application code, never by
the launch document.  The factory and every ``GatewayOperatorDeployment``
operation run on one dedicated owner thread.  The launch document contains
only a profile reference, so it cannot select credentials, Python imports,
provider classes, authority or arbitrary filesystem paths.
"""

from __future__ import annotations

import math
import queue
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from bt_api_py.gateway_operator import (
    GatewayOperatorDeployment,
    GatewayOperatorDeploymentError,
)
from bt_api_py.service_config import ServiceConfigError
from bt_api_py.service_config_io import _read_yaml
from bt_api_py.service_host import ServiceHostError

GatewayOperatorRole = Literal["account_session_worker", "market_hub"]
PROFILE_ENTRY_POINT_GROUP = "bt_api_py.gateway_operator_profiles"
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROFILE_ROLES = frozenset({"account_session_worker", "market_hub"})
_OPERATION_STATES = frozenset({"idle", "running", "succeeded", "failed", "outcome_uncertain"})
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class GatewayOperatorApplicationError(RuntimeError):
    """Finite, value-free error from local operator application control."""

    def __init__(self, code: str) -> None:
        self.code = (
            code
            if type(code) is str and _ERROR_CODE.fullmatch(code) is not None
            else "APPLICATION_FAILED"
        )
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class GatewayOperatorProfile:
    """One application-code-registered operator deployment factory."""

    profile_ref: str
    role: GatewayOperatorRole
    deployment_factory: Callable[[], GatewayOperatorDeployment] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            type(self.profile_ref) is not str
            or _SAFE_REF.fullmatch(self.profile_ref) is None
            or type(self.role) is not str
            or self.role not in _PROFILE_ROLES
            or not callable(self.deployment_factory)
        ):
            raise GatewayOperatorApplicationError("INVALID_PROFILE")


class GatewayOperatorProfileRegistry:
    """Finite registry populated by trusted in-process application code."""

    def __init__(self, profiles: tuple[GatewayOperatorProfile, ...] = ()) -> None:
        self._profiles: dict[str, GatewayOperatorProfile] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: GatewayOperatorProfile) -> None:
        if type(profile) is not GatewayOperatorProfile:
            raise GatewayOperatorApplicationError("INVALID_PROFILE_REGISTRY")
        if profile.profile_ref in self._profiles:
            raise GatewayOperatorApplicationError("PROFILE_DUPLICATE")
        self._profiles[profile.profile_ref] = profile

    def resolve(self, profile_ref: str) -> GatewayOperatorProfile:
        if type(profile_ref) is not str or _SAFE_REF.fullmatch(profile_ref) is None:
            raise GatewayOperatorApplicationError("INVALID_LAUNCH_CONFIG")
        profile = self._profiles.get(profile_ref)
        if profile is None:
            raise GatewayOperatorApplicationError("PROFILE_NOT_REGISTERED")
        return profile

    @classmethod
    def from_entry_points(cls) -> GatewayOperatorProfileRegistry:
        """Load fixed installed-code profiles from the SDK entry-point group."""

        from importlib.metadata import entry_points

        registry = cls()
        try:
            discovered = entry_points()
            select = getattr(discovered, "select", None)
            if callable(select):
                candidates = tuple(select(group=PROFILE_ENTRY_POINT_GROUP))
            elif isinstance(discovered, Mapping):
                candidates = tuple(discovered.get(PROFILE_ENTRY_POINT_GROUP, ()))
            else:
                candidates = ()
            for candidate in candidates:
                profile = candidate.load()
                if (
                    type(profile) is not GatewayOperatorProfile
                    or profile.profile_ref != candidate.name
                ):
                    raise GatewayOperatorApplicationError("PROFILE_ENTRY_POINT_INVALID")
                registry.register(profile)
        except GatewayOperatorApplicationError:
            raise
        except BaseException as exc:
            if isinstance(exc, Exception):
                raise GatewayOperatorApplicationError("PROFILE_ENTRY_POINT_INVALID") from None
            raise
        return registry


@dataclass(frozen=True, slots=True)
class GatewayOperatorLaunchConfig:
    """Local launch selection; it carries no authority or resource material."""

    profile_ref: str


def load_gateway_operator_launch_config(
    path: str | Path,
) -> GatewayOperatorLaunchConfig:
    """Load the strict, bounded profile-selection document.

    The only accepted keys are ``gateway_operator_config_schema_version`` and
    ``profile_ref``. In particular, paths, module names, credential references,
    endpoints and provider settings cannot be supplied by the launch file.
    """

    try:
        raw = _read_yaml(path)
    except ServiceConfigError:
        raise GatewayOperatorApplicationError("INVALID_LAUNCH_CONFIG") from None
    except BaseException as exc:
        if isinstance(exc, Exception):
            raise GatewayOperatorApplicationError("INVALID_LAUNCH_CONFIG") from None
        raise
    if (
        set(raw) != {"gateway_operator_config_schema_version", "profile_ref"}
        or type(raw.get("gateway_operator_config_schema_version")) is not int
        or raw.get("gateway_operator_config_schema_version") != 1
    ):
        raise GatewayOperatorApplicationError("INVALID_LAUNCH_CONFIG")
    profile_ref = raw.get("profile_ref")
    if type(profile_ref) is not str or _SAFE_REF.fullmatch(profile_ref) is None:
        raise GatewayOperatorApplicationError("INVALID_LAUNCH_CONFIG")
    return GatewayOperatorLaunchConfig(profile_ref)


@dataclass(slots=True)
class _OwnerCommand:
    name: Literal["recover", "cleanup", "stop"]
    done: threading.Event = field(default_factory=threading.Event)
    result: Mapping[str, object] | None = None
    error_code: str | None = None


class GatewayOperatorApplication:
    """Serve, recover and close one Gateway deployment on its owner thread.

    ``start`` is synchronous through successful Host startup. The owner thread
    then calls ``serve_once`` at a bounded interval while accepting local
    recover/stop requests. Timeouts never interrupt Python or provider work;
    the application retains the pending command, deployment and live thread.
    """

    def __init__(
        self,
        profile: GatewayOperatorProfile,
        *,
        poll_interval_ms: int = 50,
    ) -> None:
        if type(profile) is not GatewayOperatorProfile:
            raise GatewayOperatorApplicationError("INVALID_PROFILE")
        if type(poll_interval_ms) is not int or not 1 <= poll_interval_ms <= 1_000:
            raise GatewayOperatorApplicationError("INVALID_POLL_INTERVAL")
        self._profile = profile
        self._poll_interval_ms = poll_interval_ms
        self._lock = threading.RLock()
        self._command_lock = threading.Lock()
        self._commands: queue.Queue[_OwnerCommand] = queue.Queue(maxsize=1)
        self._startup_done = threading.Event()
        self._thread: threading.Thread | None = None
        self._deployment: GatewayOperatorDeployment | None = None
        self._pending_command: _OwnerCommand | None = None
        self._control_only = False
        self._host_close_confirmed = False
        self._terminal_state_after_cleanup: str | None = None
        self._status: dict[str, object] = {
            "state": "new",
            "role": profile.role,
            "ready": False,
            "error_code": None,
            "operation": {"name": None, "state": "idle", "error_code": None},
            "account_session": None,
            "cleanup": {"unclaimed_provider_count": 0, "last_cleanup_code": None},
        }

    @classmethod
    def from_launch_config(
        cls,
        config: GatewayOperatorLaunchConfig,
        profiles: GatewayOperatorProfileRegistry,
        *,
        poll_interval_ms: int = 50,
    ) -> GatewayOperatorApplication:
        if (
            type(config) is not GatewayOperatorLaunchConfig
            or type(profiles) is not GatewayOperatorProfileRegistry
        ):
            raise GatewayOperatorApplicationError("INVALID_LAUNCH_CONFIG")
        return cls(profiles.resolve(config.profile_ref), poll_interval_ms=poll_interval_ms)

    @property
    def status(self) -> Mapping[str, object]:
        """Return a detached finite status snapshot without endpoint or identity."""

        with self._lock:
            return _copy_status(self._status)

    def start(self, *, timeout_seconds: float = 30.0) -> Mapping[str, object]:
        timeout = _validated_timeout(timeout_seconds)
        with self._lock:
            state = self._status["state"]
            if state == "new":
                self._status["state"] = "starting"
                self._set_operation_locked("start", "running", None)
                self._thread = threading.Thread(
                    target=self._owner_main,
                    name="gateway-operator-owner",
                    daemon=False,
                )
                self._thread.start()
            elif state not in {"starting", "running"}:
                raise GatewayOperatorApplicationError("APPLICATION_NOT_RESTARTABLE")
        if not self._startup_done.wait(timeout):
            with self._lock:
                if self._status["state"] == "starting":
                    self._set_operation_locked(
                        "start", "outcome_uncertain", "APPLICATION_START_TIMEOUT"
                    )
            raise GatewayOperatorApplicationError("APPLICATION_START_TIMEOUT")
        with self._lock:
            if self._status["state"] != "running":
                code = self._status.get("error_code")
                raise GatewayOperatorApplicationError(
                    code if type(code) is str else "APPLICATION_START_FAILED"
                )
            return _copy_status(self._status)

    def recover_account_session(self, *, timeout_seconds: float = 30.0) -> Mapping[str, object]:
        if self._profile.role != "account_session_worker":
            raise GatewayOperatorApplicationError("APPLICATION_RECOVERY_FAILED")
        return self._submit("recover", timeout_seconds=timeout_seconds)

    def retry_unclaimed_provider_cleanup(
        self, *, timeout_seconds: float = 30.0
    ) -> Mapping[str, object]:
        """Retry retained provider cleanup on the deployment owner thread."""

        return self._submit("cleanup", timeout_seconds=timeout_seconds)

    def stop(self, *, timeout_seconds: float = 30.0) -> Mapping[str, object]:
        timeout = _validated_timeout(timeout_seconds)
        retry_startup_cleanup = False
        terminal_thread: threading.Thread | None = None
        terminal_owner = False
        with self._lock:
            state = self._status["state"]
            if state == "new":
                self._status["state"] = "closed"
                self._set_operation_locked("stop", "succeeded", None)
                return _copy_status(self._status)
            if state == "closed" or self._confirmed_terminal_owner_locked():
                terminal_owner = True
                terminal_thread = self._thread
            elif state == "failed" and (self._thread is None or not self._thread.is_alive()):
                return _copy_status(self._status)
            else:
                retry_cleanup = state == "failed" or (
                    state == "uncertain" and self._host_close_confirmed
                )
                if retry_cleanup:
                    retry_startup_cleanup = True
        if terminal_owner:
            if terminal_thread is not None and terminal_thread is not threading.current_thread():
                terminal_thread.join(timeout)
            return self.status
        if retry_startup_cleanup:
            status = self._submit("cleanup", timeout_seconds=timeout)
            thread = self._thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout)
                if thread.is_alive():
                    with self._lock:
                        terminal = self._confirmed_terminal_owner_locked()
                        if not terminal:
                            self._set_operation_locked(
                                "stop", "outcome_uncertain", "APPLICATION_STOP_TIMEOUT"
                            )
                    if not terminal:
                        raise GatewayOperatorApplicationError("APPLICATION_STOP_TIMEOUT")
            return self.status if thread is not None else status
        return self._submit("stop", timeout_seconds=timeout)

    def close(self, *, timeout_seconds: float = 30.0) -> Mapping[str, object]:
        """Alias for ``stop`` for application lifespan integrations."""

        return self.stop(timeout_seconds=timeout_seconds)

    def _submit(
        self, name: Literal["recover", "cleanup", "stop"], *, timeout_seconds: float
    ) -> Mapping[str, object]:
        timeout = _validated_timeout(timeout_seconds)
        if not self._command_lock.acquire(timeout=timeout):
            raise GatewayOperatorApplicationError("APPLICATION_OPERATION_IN_PROGRESS")
        try:
            with self._lock:
                state = self._status["state"]
                if name == "recover" and state != "running":
                    raise GatewayOperatorApplicationError("APPLICATION_NOT_RUNNING")
                if name == "cleanup" and state not in {
                    "starting",
                    "running",
                    "failed",
                    "uncertain",
                }:
                    raise GatewayOperatorApplicationError("APPLICATION_NOT_RUNNING")
                if name == "stop" and state not in {
                    "starting",
                    "running",
                    "stopping",
                    "uncertain",
                }:
                    raise GatewayOperatorApplicationError("APPLICATION_NOT_RUNNING")
                pending = self._pending_command
                if pending is not None and not pending.done.is_set():
                    raise GatewayOperatorApplicationError("APPLICATION_OPERATION_IN_PROGRESS")
                if pending is not None:
                    self._pending_command = None
                command = _OwnerCommand(name)
                self._pending_command = command
                if name == "stop":
                    self._status["state"] = "stopping"
                self._set_operation_locked(name, "running", None)
                try:
                    self._commands.put_nowait(command)
                except queue.Full:
                    self._pending_command = None
                    raise GatewayOperatorApplicationError(
                        "APPLICATION_OPERATION_IN_PROGRESS"
                    ) from None
            if not command.done.wait(timeout):
                code = {
                    "stop": "APPLICATION_STOP_TIMEOUT",
                    "recover": "APPLICATION_RECOVERY_TIMEOUT",
                    "cleanup": "APPLICATION_CLEANUP_UNCERTAIN",
                }[name]
                with self._lock:
                    completed = command.done.is_set()
                    if not completed:
                        self._set_operation_locked(name, "outcome_uncertain", code)
                        if name == "stop" and self._status["state"] == "stopping":
                            self._status["state"] = "uncertain"
                if not completed:
                    raise GatewayOperatorApplicationError(code)
            if command.error_code is not None:
                raise GatewayOperatorApplicationError(command.error_code)
            if name == "stop":
                thread = self._thread
                if thread is not None and thread is not threading.current_thread():
                    thread.join(timeout)
                    if thread.is_alive():
                        with self._lock:
                            terminal = self._confirmed_terminal_owner_locked()
                            if not terminal:
                                self._status["state"] = "uncertain"
                                self._status["ready"] = False
                                self._set_operation_locked(
                                    "stop", "outcome_uncertain", "APPLICATION_STOP_TIMEOUT"
                                )
                        if not terminal:
                            raise GatewayOperatorApplicationError("APPLICATION_STOP_TIMEOUT")
            return _copy_status(command.result or self.status)
        finally:
            self._command_lock.release()

    def _owner_main(self) -> None:
        deployment: GatewayOperatorDeployment | None = None
        try:
            candidate = self._profile.deployment_factory()
            if not isinstance(candidate, GatewayOperatorDeployment):
                raise GatewayOperatorApplicationError("APPLICATION_DEPLOYMENT_INVALID")
            deployment = candidate
            self._deployment = deployment
            deployment.start()
            self._refresh_deployment_status(state="running", operation=("start", "succeeded", None))
            self._startup_done.set()
        except BaseException as exc:
            close_confirmed = self._try_close(deployment)
            error_code = (
                exc.code
                if isinstance(exc, GatewayOperatorApplicationError)
                else _typed_error_code(exc, "APPLICATION_START_FAILED")
            )
            if close_confirmed:
                self._host_close_confirmed = deployment is not None
                self._terminal_state_after_cleanup = "failed"
                self._set_state("failed", error_code=error_code)
                self._finish_operation("start", "failed", error_code)
                self._startup_done.set()
                if deployment is None or not self._cleanup_requires_owner(deployment):
                    self._fail_queued_commands("APPLICATION_NOT_RUNNING")
                    return
                self._control_only = True
                self._refresh_deployment_status(operation=("start", "failed", error_code))
            else:
                self._control_only = True
                self._refresh_deployment_status(
                    state="uncertain",
                    error_code="APPLICATION_CLOSE_UNCERTAIN",
                    operation=("start", "outcome_uncertain", "APPLICATION_CLOSE_UNCERTAIN"),
                )
                if deployment is None:
                    self._fail_queued_commands("APPLICATION_NOT_RUNNING")
                    return
            self._startup_done.set()

        assert deployment is not None
        while True:
            try:
                if self._control_only:
                    command = self._commands.get(timeout=self._poll_interval_ms / 1000)
                else:
                    command = self._commands.get_nowait()
            except queue.Empty:
                if self._control_only:
                    continue
                try:
                    deployment.serve_once(self._poll_interval_ms)
                    self._refresh_deployment_status(state="running")
                except BaseException as exc:
                    error_code = _typed_error_code(exc, "APPLICATION_SERVE_FAILED")
                    if self._try_close(deployment):
                        self._host_close_confirmed = True
                        self._terminal_state_after_cleanup = "failed"
                        self._set_state("failed", error_code=error_code)
                        self._finish_operation("serve", "failed", error_code)
                        if not self._cleanup_requires_owner(deployment):
                            self._fail_queued_commands("APPLICATION_NOT_RUNNING")
                            return
                        self._control_only = True
                        self._refresh_deployment_status(operation=("serve", "failed", error_code))
                    else:
                        self._control_only = True
                        self._refresh_deployment_status(
                            state="uncertain",
                            error_code="APPLICATION_CLOSE_UNCERTAIN",
                            operation=(
                                "serve",
                                "outcome_uncertain",
                                "APPLICATION_CLOSE_UNCERTAIN",
                            ),
                        )
                continue
            if command.name == "recover":
                self._execute_recovery(deployment, command)
                continue
            if command.name == "cleanup":
                if self._execute_cleanup(deployment, command):
                    return
                continue
            if command.name == "stop" and self._execute_stop(deployment, command):
                return

    def _execute_cleanup(
        self, deployment: GatewayOperatorDeployment, command: _OwnerCommand
    ) -> bool:
        """Retry retained provider cleanup without crossing the owner thread."""

        try:
            deployment.retry_unclaimed_provider_cleanup()
            self._refresh_deployment_status(operation=("cleanup", "succeeded", None))
            if self._cleanup_pending_in_snapshot(self.status):
                command.error_code = "APPLICATION_CLEANUP_UNCERTAIN"
                self._refresh_deployment_status(
                    error_code=command.error_code,
                    operation=("cleanup", "outcome_uncertain", command.error_code),
                )
            elif self._host_close_confirmed and self._terminal_state_after_cleanup == "closed":
                self._refresh_deployment_status(
                    state="closed", operation=("stop", "succeeded", None)
                )
            command.result = self.status
        except BaseException as exc:
            error_code = _typed_error_code(exc, "APPLICATION_CLEANUP_UNCERTAIN")
            self._refresh_deployment_status(
                error_code=error_code,
                operation=("cleanup", "outcome_uncertain", error_code),
            )
            command.error_code = error_code
        finally:
            command.done.set()
            with self._lock:
                if self._pending_command is command:
                    self._pending_command = None
        with self._lock:
            cleanup_complete = not self._cleanup_pending_in_snapshot(self._status)
        return (
            self._host_close_confirmed
            and self._terminal_state_after_cleanup in {"failed", "closed"}
            and cleanup_complete
            and command.error_code is None
        )

    def _execute_recovery(
        self, deployment: GatewayOperatorDeployment, command: _OwnerCommand
    ) -> None:
        try:
            deployment.recover_account_session()
            self._refresh_deployment_status(
                state="running", operation=("recover", "succeeded", None)
            )
            command.result = self.status
        except BaseException as exc:
            error_code = _typed_error_code(exc, "APPLICATION_RECOVERY_FAILED")
            self._refresh_deployment_status(
                state="running",
                error_code=error_code,
                operation=("recover", "failed", error_code),
            )
            command.error_code = error_code
        finally:
            command.done.set()
            with self._lock:
                if self._pending_command is command:
                    self._pending_command = None

    def _execute_stop(self, deployment: GatewayOperatorDeployment, command: _OwnerCommand) -> bool:
        try:
            deployment.close()
        except BaseException as exc:
            error_code = _typed_error_code(exc, "APPLICATION_CLOSE_UNCERTAIN")
            self._control_only = True
            self._refresh_deployment_status(
                state="uncertain",
                error_code=error_code,
                operation=("stop", "outcome_uncertain", error_code),
            )
            command.error_code = error_code
        else:
            self._host_close_confirmed = True
            self._terminal_state_after_cleanup = "closed"
            self._refresh_deployment_status(state="closed", operation=("stop", "succeeded", None))
            if self._cleanup_pending_in_snapshot(self.status):
                self._control_only = True
                error_code = "APPLICATION_CLEANUP_UNCERTAIN"
                self._refresh_deployment_status(
                    state="uncertain",
                    error_code=error_code,
                    operation=("stop", "outcome_uncertain", error_code),
                )
                command.error_code = error_code
            else:
                command.result = self.status
                command.done.set()
                with self._lock:
                    if self._pending_command is command:
                        self._pending_command = None
                return True
        command.done.set()
        with self._lock:
            if self._pending_command is command:
                self._pending_command = None
        return False

    def _try_close(self, deployment: GatewayOperatorDeployment | None) -> bool:
        if deployment is None:
            return True
        try:
            deployment.close()
            return True
        except BaseException:
            return False

    @staticmethod
    def _cleanup_requires_owner(deployment: GatewayOperatorDeployment) -> bool:
        """Keep the owner alive if cleanup is pending or cannot be read."""

        try:
            cleanup = deployment.cleanup_status
            count = cleanup.get("unclaimed_provider_count")
            code = cleanup.get("last_cleanup_code")
            if type(count) is not int or count < 0:
                return True
            return count > 0 or code is not None
        except BaseException:
            return True

    @staticmethod
    def _cleanup_pending_in_snapshot(status: Mapping[str, object]) -> bool:
        cleanup = status.get("cleanup")
        return not (
            isinstance(cleanup, Mapping)
            and cleanup.get("unclaimed_provider_count") == 0
            and cleanup.get("last_cleanup_code") is None
        )

    def _confirmed_terminal_owner_locked(self) -> bool:
        return (
            self._host_close_confirmed
            and self._status.get("state") in {"closed", "failed"}
            and not self._cleanup_pending_in_snapshot(self._status)
        )

    def _refresh_deployment_status(
        self,
        *,
        state: str | None = None,
        error_code: str | None = None,
        operation: tuple[str, str, str | None] | None = None,
    ) -> None:
        deployment = self._deployment
        account_session: dict[str, object] | None = None
        cleanup: dict[str, object] = {
            "unclaimed_provider_count": 0,
            "last_cleanup_code": None,
        }
        ready = False
        deployment_status_unavailable = False
        if deployment is not None:
            try:
                deployment_state = deployment.state
                if type(deployment_state) is not str:
                    raise ValueError
                ready = deployment_state == "started"
            except BaseException:
                deployment_status_unavailable = True
            if self._profile.role == "account_session_worker":
                try:
                    account_session = _copy_account_status(deployment.account_session_status)
                    ready = ready and account_session["ready"] is True
                except BaseException:
                    account_session = None
                    ready = False
                    deployment_status_unavailable = True
            try:
                cleanup = _copy_cleanup_status(deployment.cleanup_status)
            except BaseException:
                cleanup = {
                    "unclaimed_provider_count": 0,
                    "last_cleanup_code": "CLEANUP_STATUS_UNAVAILABLE",
                }
        with self._lock:
            if state is not None and not (
                state == "running" and self._status["state"] in {"stopping", "uncertain"}
            ):
                self._status["state"] = state
            if error_code is not None:
                self._status["error_code"] = error_code
            elif deployment_status_unavailable:
                self._status["error_code"] = "DEPLOYMENT_STATUS_UNAVAILABLE"
            elif state in {"running", "closed"}:
                self._status["error_code"] = None
            self._status["ready"] = ready
            self._status["account_session"] = account_session
            self._status["cleanup"] = cleanup
            if operation is not None:
                self._set_operation_locked(*operation)

    def _set_state(self, state: str, *, error_code: str | None) -> None:
        with self._lock:
            self._status["state"] = state
            self._status["ready"] = False
            self._status["error_code"] = error_code

    def _set_operation_locked(self, name: str | None, state: str, error_code: str | None) -> None:
        self._status["operation"] = {
            "name": name,
            "state": state if state in _OPERATION_STATES else "failed",
            "error_code": error_code,
        }

    def _finish_operation(self, name: str, state: str, error_code: str | None) -> None:
        with self._lock:
            self._set_operation_locked(name, state, error_code)

    def _fail_queued_commands(self, code: str) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            command.error_code = code
            command.done.set()
            with self._lock:
                if self._pending_command is command:
                    self._pending_command = None


def _typed_error_code(exc: BaseException, fallback: str) -> str:
    if isinstance(exc, GatewayOperatorDeploymentError):
        code = exc.code
        if type(code) is str and _ERROR_CODE.fullmatch(code) is not None:
            return code
    if isinstance(exc, ServiceHostError):
        code = exc.code
        if type(code) is str and _ERROR_CODE.fullmatch(code) is not None:
            return code
    if isinstance(exc, GatewayOperatorApplicationError):
        return exc.code
    return fallback


def _validated_timeout(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
        or float(value) > 300
    ):
        raise GatewayOperatorApplicationError("INVALID_TIMEOUT")
    return float(value)


def _copy_account_status(value: Mapping[str, object]) -> dict[str, object]:
    state = value.get("state")
    generation = value.get("session_generation")
    ready = value.get("ready")
    fresh = value.get("fresh_for_seconds")
    if (
        not isinstance(value, Mapping)
        or type(state) is not str
        or (generation is not None and type(generation) is not str)
        or type(ready) is not bool
        or isinstance(fresh, bool)
        or not isinstance(fresh, (int, float))
        or not math.isfinite(float(fresh))
        or float(fresh) < 0
    ):
        raise ValueError("invalid finite account session status")
    return {
        "state": state,
        "session_generation": generation,
        "ready": ready,
        "fresh_for_seconds": float(fresh),
    }


def _copy_cleanup_status(value: Mapping[str, object]) -> dict[str, object]:
    count = value.get("unclaimed_provider_count")
    code = value.get("last_cleanup_code")
    if (
        not isinstance(value, Mapping)
        or type(count) is not int
        or count < 0
        or (code is not None and (type(code) is not str or _ERROR_CODE.fullmatch(code) is None))
    ):
        raise ValueError("invalid finite cleanup status")
    return {"unclaimed_provider_count": count, "last_cleanup_code": code}


def _copy_status(value: Mapping[str, object]) -> Mapping[str, object]:
    account = value.get("account_session")
    operation = value.get("operation")
    cleanup = value.get("cleanup")
    return MappingProxyType(
        {
            "state": value.get("state"),
            "role": value.get("role"),
            "ready": value.get("ready"),
            "error_code": value.get("error_code"),
            "operation": MappingProxyType(dict(operation))
            if isinstance(operation, Mapping)
            else None,
            "account_session": (
                MappingProxyType(dict(account)) if isinstance(account, Mapping) else None
            ),
            "cleanup": MappingProxyType(dict(cleanup)) if isinstance(cleanup, Mapping) else None,
        }
    )


__all__ = [
    "GatewayOperatorApplication",
    "GatewayOperatorApplicationError",
    "GatewayOperatorLaunchConfig",
    "GatewayOperatorProfile",
    "GatewayOperatorProfileRegistry",
    "GatewayOperatorRole",
    "PROFILE_ENTRY_POINT_GROUP",
    "load_gateway_operator_launch_config",
]
