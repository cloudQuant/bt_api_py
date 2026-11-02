"""Synthetic-only two-host acceptance runner for Iteration 10 DIST checks.

This is a deliberately fixed composition over ``ServiceHost`` and
``ServiceClientRuntime``.  It has no exchange adapters, dotenv loading, plugin
discovery, or direct broker fallback.  The only provider is an in-process
synthetic dispatch counter which acknowledges bounded test orders.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

SCHEMA_VERSION = 1
SERVICE_ID = "i10-dist-synthetic-service"
SERVICE_PRINCIPAL = "i10-dist-synthetic-service-principal"
STRATEGY_REF = "i10-dist-synthetic-strategy"
STRATEGY_PRINCIPAL = "i10-dist-synthetic-strategy-principal"
ACCOUNT_REF = "i10-dist-synthetic-account"
DOMAIN_REF = "i10-dist-synthetic-domain"
EXECUTION_INSTANCE = "i10-dist-synthetic-execution-owner"
ORDER_SCOPE_REF = "i10-dist-synthetic-order-scope-v1"
SERVICE_SCOPE_REF = "i10-dist-synthetic-service-scope-v1"
TRANSPORT_REF = "i10-dist-synthetic-curve-v1"
SECURITY_REF = "i10-dist-synthetic-security-v1"
REGISTRATION_POLICY_REF = "i10-dist-synthetic-registration-v1"
REQUIREMENTS_REF = "i10-dist-synthetic-requirements-v1"
MAX_MESSAGE_BYTES = 65_536
MAX_ORDERS = 100
_SAFE_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class AcceptanceRunnerError(RuntimeError):
    """A finite, value-free runner error."""

    def __init__(self, code: str) -> None:
        self.code = code if _SAFE_CODE.fullmatch(code) else "RUNNER_FAILED"
        super().__init__(self.code)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if type(code) is str and _SAFE_CODE.fullmatch(code):
        return code
    return type(error).__name__ if _SAFE_CODE.fullmatch(type(error).__name__) else "RUNNER_FAILED"


def _write_json(path: Path, value: dict[str, Any], *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(path, flags, 0o600 if private else 0o644)
    try:
        if private and os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_json(path: Path, *, max_bytes: int = 256_000) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
            raise AcceptanceRunnerError("BUNDLE_FILE_INVALID")
        value = json.loads(path.read_text(encoding="utf-8"))
    except AcceptanceRunnerError:
        raise
    except Exception:
        raise AcceptanceRunnerError("BUNDLE_FILE_UNREADABLE") from None
    if type(value) is not dict:
        raise AcceptanceRunnerError("BUNDLE_SCHEMA_INVALID")
    return value


def _endpoint(host: str, port: int) -> str:
    if type(host) is not str or not host or host != host.strip():
        raise AcceptanceRunnerError("ADVERTISE_HOST_REQUIRED")
    if any(character in host for character in "/?#@"):
        raise AcceptanceRunnerError("ADVERTISE_HOST_INVALID")
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        host = f"[{host}]"
    return f"tcp://{host}:{port}"


def _owner_service_config(bind_host: str, port: int) -> dict[str, Any]:
    bind_endpoint = f"tcp://{bind_host}:{port}"
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": SERVICE_ID,
        "scope_ref": SERVICE_SCOPE_REF,
        "transport_ref": TRANSPORT_REF,
        "registration_policy_ref": REGISTRATION_POLICY_REF,
        "service_requirements_ref": REQUIREMENTS_REF,
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": EXECUTION_INSTANCE,
                    "bind_ports": ["service.order_entry"],
                    "options": {"role": "account_owner"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": bind_endpoint},
                    "security_profile_ref": SECURITY_REF,
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _strategy_client_config(connect_endpoint: str, timeout_ms: int) -> dict[str, Any]:
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": STRATEGY_REF},
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "broker.orders",
                            "service_id": SERVICE_ID,
                            "instance_id": EXECUTION_INSTANCE,
                            "scope_ref": ORDER_SCOPE_REF,
                            "transport_ref": TRANSPORT_REF,
                            "account_ref": ACCOUNT_REF,
                        }
                    ],
                    "options": {"request_timeout_ms": timeout_ms},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": connect_endpoint},
                    "security_profile_ref": SECURITY_REF,
                    "options": {
                        "request_timeout_ms": timeout_ms,
                        "high_water_mark": 32,
                        "max_message_bytes": MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def prepare_bundle(
    output_dir: str | Path,
    *,
    advertise_host: str,
    port: int = 7601,
    request_timeout_ms: int = 1_500,
) -> dict[str, Any]:
    """Create non-overwriting owner/client bundles with separate CURVE secrets."""

    if type(port) is not int or not 1_024 <= port <= 65_535:
        raise AcceptanceRunnerError("PORT_INVALID")
    if type(request_timeout_ms) is not int or not 250 <= request_timeout_ms <= 10_000:
        raise AcceptanceRunnerError("TIMEOUT_INVALID")
    connect_endpoint = _endpoint(advertise_host, port)
    root = Path(output_dir).expanduser()
    if root.exists():
        raise AcceptanceRunnerError("OUTPUT_ALREADY_EXISTS")

    # Import the existing PyZMQ CURVE key utility only for explicit bundle
    # preparation. The public and secret values are written to side-specific
    # files and never included in returned metadata or stdout.
    try:
        import zmq

        server_public, server_secret = zmq.curve_keypair()
        client_public, client_secret = zmq.curve_keypair()
    except Exception:
        raise AcceptanceRunnerError("CURVE_KEY_GENERATION_FAILED") from None

    bundle_id = uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    owner_dir = root / "owner"
    strategy_dir = root / "strategy"
    owner_dir.mkdir()
    strategy_dir.mkdir()
    owner_bundle = {
        "schema_version": SCHEMA_VERSION,
        "source_mode": "synthetic-only",
        "role": "owner",
        "bundle_id": bundle_id,
        "service_id": SERVICE_ID,
        "service_principal": SERVICE_PRINCIPAL,
        "strategy_ref": STRATEGY_REF,
        "strategy_principal": STRATEGY_PRINCIPAL,
        "account_ref": ACCOUNT_REF,
        "execution_domain_ref": DOMAIN_REF,
        "instance_id": EXECUTION_INSTANCE,
        "scope_ref": SERVICE_SCOPE_REF,
        "security_ref": SECURITY_REF,
        "registration_policy_ref": REGISTRATION_POLICY_REF,
        "service_requirements_ref": REQUIREMENTS_REF,
        "order_scope_ref": ORDER_SCOPE_REF,
        "advertise_endpoint": connect_endpoint,
        "service_config": _owner_service_config("0.0.0.0", port),
    }
    strategy_bundle = {
        "schema_version": SCHEMA_VERSION,
        "source_mode": "synthetic-only",
        "role": "strategy",
        "bundle_id": bundle_id,
        "service_id": SERVICE_ID,
        "service_principal": SERVICE_PRINCIPAL,
        "strategy_ref": STRATEGY_REF,
        "strategy_principal": STRATEGY_PRINCIPAL,
        "account_ref": ACCOUNT_REF,
        "execution_domain_ref": DOMAIN_REF,
        "instance_id": EXECUTION_INSTANCE,
        "scope_ref": ORDER_SCOPE_REF,
        "security_ref": SECURITY_REF,
        "registration_policy_ref": REGISTRATION_POLICY_REF,
        "service_requirements_ref": REQUIREMENTS_REF,
        "order_scope_ref": ORDER_SCOPE_REF,
        "advertise_endpoint": connect_endpoint,
        "client_config": _strategy_client_config(connect_endpoint, request_timeout_ms),
    }
    try:
        _write_json(owner_dir / "bundle.json", owner_bundle)
        _write_json(strategy_dir / "bundle.json", strategy_bundle)
        _write_json(
            owner_dir / "curve-keys.json",
            {
                "server_public": server_public.decode("ascii"),
                "server_secret": server_secret.decode("ascii"),
                "strategy_public": client_public.decode("ascii"),
            },
            private=True,
        )
        _write_json(
            strategy_dir / "curve-keys.json",
            {
                "server_public": server_public.decode("ascii"),
                "strategy_public": client_public.decode("ascii"),
                "strategy_secret": client_secret.decode("ascii"),
            },
            private=True,
        )
    except Exception:
        # The unique output directory is left intact for inspection and safe
        # manual removal; it is never recursively deleted by this command.
        raise AcceptanceRunnerError("BUNDLE_WRITE_FAILED") from None

    return {
        "status": "prepared",
        "source_mode": "synthetic-only",
        "bundle_id": bundle_id,
        "owner_bundle": str(owner_dir),
        "strategy_bundle": str(strategy_dir),
        "advertise_endpoint": connect_endpoint,
        "bind_endpoint": f"tcp://0.0.0.0:{port}",
        "secret_material_printed": False,
    }


def _require_bundle(bundle_dir: str | Path, expected_role: str) -> tuple[Path, dict[str, Any], dict[str, bytes]]:
    directory = Path(bundle_dir).expanduser()
    bundle = _read_json(directory / "bundle.json")
    keys_raw = _read_json(directory / "curve-keys.json", max_bytes=4_096)
    if (
        bundle.get("schema_version") != SCHEMA_VERSION
        or bundle.get("source_mode") != "synthetic-only"
        or bundle.get("role") != expected_role
        or type(bundle.get("bundle_id")) is not str
        or len(bundle["bundle_id"]) != 32
        or any(bundle.get(name) != value for name, value in _FIXED_BUNDLE_FIELDS.items())
    ):
        raise AcceptanceRunnerError("BUNDLE_NOT_SYNTHETIC")
    try:
        key_bytes = {name: keys_raw[name].encode("ascii") for name in keys_raw}
    except Exception:
        raise AcceptanceRunnerError("CURVE_KEYS_INVALID") from None
    if expected_role == "owner":
        required = {"server_public", "server_secret", "strategy_public"}
        if set(key_bytes) != required:
            raise AcceptanceRunnerError("CURVE_KEYS_INVALID")
    else:
        required = {"server_public", "strategy_public", "strategy_secret"}
        if set(key_bytes) != required:
            raise AcceptanceRunnerError("CURVE_KEYS_INVALID")
    if any(len(value) != 40 for value in key_bytes.values()):
        raise AcceptanceRunnerError("CURVE_KEYS_INVALID")
    return directory, bundle, key_bytes


_FIXED_BUNDLE_FIELDS = {
    "service_id": SERVICE_ID,
    "service_principal": SERVICE_PRINCIPAL,
    "strategy_ref": STRATEGY_REF,
    "strategy_principal": STRATEGY_PRINCIPAL,
    "account_ref": ACCOUNT_REF,
    "execution_domain_ref": DOMAIN_REF,
    "instance_id": EXECUTION_INSTANCE,
    "security_ref": SECURITY_REF,
    "registration_policy_ref": REGISTRATION_POLICY_REF,
    "service_requirements_ref": REQUIREMENTS_REF,
    "order_scope_ref": ORDER_SCOPE_REF,
}


def _scope_types():
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
    from bt_api_execution import ExecutionScope

    return WireIdentity, WireRole, WireScope, ExecutionScope


class _SyntheticDispatch:
    """A finite, file-audited synthetic provider leaf."""

    def __init__(
        self,
        evidence_path: Path,
        owner_instance: str,
        *,
        stop_after_submits: int | None,
        stop_event: threading.Event,
    ) -> None:
        self._evidence_path = evidence_path
        self._owner_instance = owner_instance
        self._stop_after = stop_after_submits
        self._stop_event = stop_event
        self.submit_calls = 0
        self.blocked_submit_attempts = 0
        self.cancel_calls = 0
        self.intent_ids: list[str] = []
        self.write_halted = False
        self._persist("started")

    def submit(self, intent):
        from bt_api_execution import ProviderObservation

        if self.write_halted:
            self.blocked_submit_attempts += 1
            self._persist("write_halted")
            raise RuntimeError("synthetic owner write halt")
        self.submit_calls += 1
        self.intent_ids.append(intent.intent_id)
        self._persist("dispatch_acknowledged")
        if self._stop_after is not None and self.submit_calls >= self._stop_after:
            self.write_halted = True
            self._stop_event.set()
            self._persist("write_halted")
        return ProviderObservation.accepted(
            intent.intent_id,
            f"synthetic-{self._owner_instance[:8]}-{self.submit_calls:04d}",
        )

    def cancel(self, intent):
        from bt_api_execution import CancelObservation

        self.cancel_calls += 1
        self._persist("cancel_acknowledged")
        return CancelObservation.accepted(
            intent.cancel_id,
            intent.target_intent_id,
            intent.provider_order_id,
        )

    def _persist(self, phase: str) -> None:
        _write_json(
            self._evidence_path,
            {
                "evidence_schema_version": 1,
                "evidence_class": "SYNTHETIC_LOCAL_OR_DUAL_HOST_RUNNER",
                "source_mode": "synthetic-only",
                "owner_instance": self._owner_instance,
                "phase": phase,
                "provider_submit_calls": self.submit_calls,
                "provider_cancel_calls": self.cancel_calls,
                "blocked_submit_attempts": self.blocked_submit_attempts,
                "accepted_intent_ids": list(self.intent_ids),
                "direct_adapter_calls": 0,
                "direct_fallback_configured": False,
                "updated_at_utc": _utc_now(),
            },
        )


def _build_owner(bundle: dict[str, Any], key_bytes: dict[str, bytes], state_dir: Path, dispatch):
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
    from bt_api_execution import ExecutionScope, SqliteExecutionStore
    from bt_api_py.service_components import ComponentResources
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

    parsed_config = parse_service_config(bundle["service_config"])
    wire_scope = WireScope(
        execution_domain_ref=DOMAIN_REF,
        account_ref=ACCOUNT_REF,
        strategy_ref=STRATEGY_REF,
    )
    execution_scope = ExecutionScope(
        "SYNTHETIC",
        "simulation",
        ACCOUNT_REF,
        STRATEGY_REF,
        "20261021",
    )
    client_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        STRATEGY_PRINCIPAL,
        STRATEGY_REF,
    )
    service_identity = WireIdentity(
        WireRole.SERVICE,
        SERVICE_PRINCIPAL,
        SERVICE_ID,
    )
    grants = {
        STRATEGY_PRINCIPAL: (
            PrincipalGrant(
                client_identity,
                (
                    ResourceGrant(
                        EXECUTION_INSTANCE,
                        "service.order_entry",
                        ORDER_SCOPE_REF,
                        wire_scope,
                        frozenset({"orders.submit"}),
                    ),
                ),
            ),
        )
    }
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=wire_scope,
        scope_ref=SERVICE_SCOPE_REF,
        scope_version="i10-dist-service-scope-version-v1",
        security_ref=SECURITY_REF,
        security_version="i10-dist-curve-keys-v1",
        registration_policy_ref=REGISTRATION_POLICY_REF,
        registration_policy_version="i10-dist-registration-policy-version-v1",
        service_requirements_ref=REQUIREMENTS_REF,
        service_requirements_version="i10-dist-requirements-version-v1",
        curve_public_key=key_bytes["server_public"],
        curve_secret_key=key_bytes["server_secret"],
        principals={key_bytes["strategy_public"]: STRATEGY_PRINCIPAL},
        grants=grants,
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    store_path = state_dir / "synthetic-execution.sqlite3"

    def resource_provider(component, instance_id, options, _scope):
        if (
            component != "execution"
            or instance_id != EXECUTION_INSTANCE
            or options.get("role") != "account_owner"
        ):
            raise AcceptanceRunnerError("UNEXPECTED_COMPONENT_REQUEST")
        store = SqliteExecutionStore(store_path)
        return ComponentResources.owned(
            {
                "store": store,
                "targets": {wire_scope: execution_scope},
                "dispatch": dispatch,
                "admission": {"allow_unprotected": True},
            },
            ready=lambda: True,
            close=store.close,
        )

    host = ServiceHost(
        parsed_config,
        authority,
        state_dir / "synthetic-registry.sqlite3",
        resource_provider,
    )
    return host


def serve_owner(
    bundle_dir: str | Path,
    *,
    state_dir: str | Path | None = None,
    evidence_dir: str | Path | None = None,
    stop_after_submits: int | None = None,
    max_runtime_seconds: float | None = None,
    stop_event: threading.Event | None = None,
    on_ready: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Start the fixed synthetic owner and serve until bounded normal close."""

    if stop_after_submits is not None and (
        type(stop_after_submits) is not int or not 1 <= stop_after_submits <= MAX_ORDERS
    ):
        raise AcceptanceRunnerError("STOP_AFTER_INVALID")
    if max_runtime_seconds is not None and (
        type(max_runtime_seconds) not in (int, float)
        or not 0.1 <= max_runtime_seconds <= 86_400
    ):
        raise AcceptanceRunnerError("RUNTIME_LIMIT_INVALID")
    directory, bundle, key_bytes = _require_bundle(bundle_dir, "owner")
    state_root = Path(state_dir).expanduser() if state_dir else directory / "state"
    evidence_root = Path(evidence_dir).expanduser() if evidence_dir else directory / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    owner_instance = uuid.uuid4().hex
    stop = stop_event or threading.Event()
    provider_evidence = evidence_root / "provider-calls.json"
    dispatch = _SyntheticDispatch(
        provider_evidence,
        owner_instance,
        stop_after_submits=stop_after_submits,
        stop_event=stop,
    )
    host = _build_owner(bundle, key_bytes, state_root, dispatch)
    started_at = time.monotonic()
    runtime_evidence = evidence_root / "owner-runtime.json"
    status: dict[str, Any]
    try:
        host.start()
        status = {
            "evidence_schema_version": 1,
            "evidence_class": "SYNTHETIC_LOCAL_OR_DUAL_HOST_RUNNER",
            "source_mode": "synthetic-only",
            "bundle_id": bundle["bundle_id"],
            "owner_instance": owner_instance,
            "phase": "started",
            "configured_endpoint": bundle["advertise_endpoint"],
            "bound_endpoint": host.endpoint,
            "host_state": host.state,
            "direct_adapter_calls": 0,
            "direct_fallback_configured": False,
            "started_at_utc": _utc_now(),
        }
        _write_json(runtime_evidence, status)
        if on_ready is not None:
            on_ready(dict(status))
        halt_deadline: float | None = None
        deadline = (
            None if max_runtime_seconds is None else started_at + max_runtime_seconds
        )
        while True:
            now = time.monotonic()
            if stop.is_set() and halt_deadline is None:
                # Keep the service available briefly for the strategy's normal
                # unregister after the final accepted call. New writes are
                # already blocked by the dispatch leaf.
                halt_deadline = now + 1.5
            if halt_deadline is not None and now >= halt_deadline:
                break
            if deadline is not None and now >= deadline:
                break
            host.serve_once(100)
        host.close()
        status.update(
            {
                "phase": "closed",
                "host_state": host.state,
                "normal_shutdown": host.state == "closed",
                "stop_reason": (
                    "synthetic_write_halt"
                    if stop.is_set()
                    else "max_runtime"
                    if deadline is not None and time.monotonic() >= deadline
                    else "operator_stop"
                ),
                "provider_submit_calls": dispatch.submit_calls,
                "provider_cancel_calls": dispatch.cancel_calls,
                "blocked_submit_attempts": dispatch.blocked_submit_attempts,
                "closed_at_utc": _utc_now(),
            }
        )
        _write_json(runtime_evidence, status)
        dispatch._persist("closed")
        return status
    except BaseException as error:
        code = _safe_code(error)
        if host.state == "started":
            try:
                host.close()
            except BaseException:
                code = "OWNER_CLOSE_FAILED"
        status = {
            "evidence_schema_version": 1,
            "evidence_class": "SYNTHETIC_LOCAL_OR_DUAL_HOST_RUNNER",
            "source_mode": "synthetic-only",
            "bundle_id": bundle["bundle_id"],
            "owner_instance": owner_instance,
            "phase": "start_or_serve_failed",
            "error_code": code,
            "host_state": host.state,
            "host_start_failure": host.summary.get("start_failure"),
            "direct_adapter_calls": 0,
            "direct_fallback_configured": False,
            "observed_at_utc": _utc_now(),
        }
        _write_json(runtime_evidence, status)
        dispatch._persist("failed")
        if not isinstance(error, Exception):
            raise
        return status


def check_owner_exclusion(
    bundle_dir: str | Path,
    *,
    state_dir: str | Path | None = None,
    evidence_file: str | Path | None = None,
) -> dict[str, Any]:
    """Attempt a second local owner against the same durable owner store."""

    directory, bundle, key_bytes = _require_bundle(bundle_dir, "owner")
    state_root = Path(state_dir).expanduser() if state_dir else directory / "state"
    evidence_path = (
        Path(evidence_file).expanduser()
        if evidence_file
        else directory / "evidence" / "second-owner-check.json"
    )
    stop = threading.Event()
    dispatch = _SyntheticDispatch(
        evidence_path.with_name("second-owner-provider-calls.json"),
        uuid.uuid4().hex,
        stop_after_submits=None,
        stop_event=stop,
    )
    host = _build_owner(bundle, key_bytes, state_root, dispatch)
    try:
        host.start()
    except Exception as error:
        code = _safe_code(error)
        result = {
            "evidence_schema_version": 1,
            "evidence_class": "SYNTHETIC_OWNER_EXCLUSION_CHECK",
            "source_mode": "synthetic-only",
            "bundle_id": bundle["bundle_id"],
            "second_owner_started": False,
            "second_owner_rejected": code == "ORDER_OWNER_ALREADY_ACTIVE",
            "error_code": code,
            "second_owner_provider_calls": 0,
            "observed_at_utc": _utc_now(),
        }
        _write_json(evidence_path, result)
        return result
    try:
        host.close()
    except Exception:
        result = {
            "evidence_schema_version": 1,
            "evidence_class": "SYNTHETIC_OWNER_EXCLUSION_CHECK",
            "source_mode": "synthetic-only",
            "bundle_id": bundle["bundle_id"],
            "second_owner_started": True,
            "second_owner_rejected": False,
            "error_code": "SECOND_OWNER_CLOSE_FAILED",
            "second_owner_provider_calls": 0,
            "observed_at_utc": _utc_now(),
        }
        _write_json(evidence_path, result)
        return result
    result = {
        "evidence_schema_version": 1,
        "evidence_class": "SYNTHETIC_OWNER_EXCLUSION_CHECK",
        "source_mode": "synthetic-only",
        "bundle_id": bundle["bundle_id"],
        "second_owner_started": True,
        "second_owner_rejected": False,
        "error_code": "SECOND_OWNER_STARTED",
        "second_owner_provider_calls": 0,
        "observed_at_utc": _utc_now(),
    }
    _write_json(evidence_path, result)
    return result


def _client_runtime(bundle: dict[str, Any], key_bytes: dict[str, bytes], run_id: str):
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory

    parsed = parse_client_config(bundle["client_config"])
    scope = WireScope(
        execution_domain_ref=DOMAIN_REF,
        account_ref=ACCOUNT_REF,
        strategy_ref=STRATEGY_REF,
    )
    peer = WireIdentity(WireRole.SERVICE, SERVICE_PRINCIPAL, SERVICE_ID)
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, STRATEGY_PRINCIPAL, STRATEGY_REF)
    keys = ClientCurveKeys(
        key_bytes["server_public"],
        key_bytes["strategy_public"],
        key_bytes["strategy_secret"],
    )
    return ServiceClientRuntime(
        parsed,
        identity=identity,
        scope_resolver=lambda scope_ref: scope if scope_ref == ORDER_SCOPE_REF else None,
        peer_resolver=lambda service_id: peer if service_id == SERVICE_ID else None,
        transport_factory=ZmqTransportFactory({TRANSPORT_REF: keys}),
        run_id=run_id,
        requested_operations={"broker.orders": frozenset({"orders.submit"})},
    )


def run_strategy_probe(
    bundle_dir: str | Path,
    *,
    evidence_dir: str | Path | None = None,
    orders: int = 2,
    expect_unavailable: bool = False,
) -> dict[str, Any]:
    """Submit synthetic orders over CURVE, reconnect between calls, fail closed."""

    if type(orders) is not int or not 1 <= orders <= MAX_ORDERS:
        raise AcceptanceRunnerError("ORDER_COUNT_INVALID")
    directory, bundle, key_bytes = _require_bundle(bundle_dir, "strategy")
    output_dir = Path(evidence_dir).expanduser() if evidence_dir else directory / "evidence"
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = "i10-dist-run-" + uuid.uuid4().hex[:16]
    intent_prefix = "i10-dist-intent-" + uuid.uuid4().hex[:12]
    entries: list[dict[str, Any]] = []
    owner_epochs: list[str] = []
    submitted_attempts = 0
    reconnects = 0
    normal_shutdown = True
    failure_code: str | None = None
    failure_phase: str | None = None
    epoch_changed = False
    provider = None

    try:
        from bt_api_py.service_execution import ServiceExecutionClient
        from bt_api_py.service_runtime import ServiceClientRuntime
    except Exception:
        raise AcceptanceRunnerError("CLIENT_RUNTIME_UNAVAILABLE") from None

    for index in range(orders):
        runtime = _client_runtime(bundle, key_bytes, run_id)
        try:
            try:
                registration = runtime.start()
            except Exception as error:
                failure_code = _safe_code(error)
                failure_phase = "registration"
                break
            active = [binding for binding in registration.bindings if binding.mount == "broker.orders"]
            if len(active) != 1 or not active[0].active:
                failure_code = "ORDER_ROUTE_UNAVAILABLE"
                failure_phase = "registration"
                break
            epoch = active[0].service_epoch
            owner_epochs.append(epoch)
            if len(set(owner_epochs)) > 1:
                # A new server epoch is not silently treated as the same owner.
                epoch_changed = True
                failure_code = "OWNER_EPOCH_CHANGED"
                failure_phase = "owner_identity"
                break
            client = ServiceExecutionClient(runtime, "broker.orders", scope_ref=ORDER_SCOPE_REF)
            intent_id = f"{intent_prefix}-{index + 1:03d}"
            submitted_attempts += 1
            try:
                receipt = client.submit(
                    intent_id=intent_id,
                    signal_id=f"{intent_id}-signal",
                    instrument="SYNTH-USD",
                    side="BUY",
                    position_effect="OPEN",
                    order_type="LIMIT",
                    quantity="1",
                    price="10",
                    metadata_version="i10-dist-synthetic-metadata-v1",
                )
                entries.append(
                    {
                        "intent_id": intent_id,
                        "state": getattr(receipt.state, "value", str(receipt.state)),
                        "dispatch_attempts": receipt.dispatch_attempts,
                        "service_epoch": epoch,
                    }
                )
                if getattr(receipt.state, "value", str(receipt.state)) == "UNKNOWN":
                    # UNKNOWN is never resubmitted; the run stops here.
                    failure_code = "BUSINESS_OUTCOME_UNKNOWN"
                    failure_phase = "business_request"
                    break
            except Exception as error:
                failure_code = _safe_code(error)
                failure_phase = "business_request"
                entries.append(
                    {
                        "intent_id": intent_id,
                        "state": "unknown_or_rejected",
                        "service_epoch": epoch,
                        "error_code": failure_code,
                    }
                )
                # A failed business call is never automatically retried.
                break
        finally:
            try:
                runtime.close()
                if runtime.state != "closed" or runtime.cleanup_failures:
                    normal_shutdown = False
            except Exception as error:
                normal_shutdown = False
                if failure_code is None:
                    failure_code = _safe_code(error)
                    failure_phase = "client_shutdown"
        if index + 1 < orders and failure_code is None:
            reconnects += 1

    if expect_unavailable:
        accepted_unavailable = (
            failure_phase == "registration"
            and submitted_attempts == 0
            and not entries
            and normal_shutdown
        )
    else:
        accepted_unavailable = False
    if expect_unavailable and not accepted_unavailable and failure_code is None:
        failure_code = "OWNER_UNAVAILABLE_NOT_OBSERVED"

    result = {
        "evidence_schema_version": 1,
        "evidence_class": "SYNTHETIC_LOCAL_OR_DUAL_HOST_RUNNER",
        "source_mode": "synthetic-only",
        "bundle_id": bundle["bundle_id"],
        "endpoint": bundle["advertise_endpoint"],
        "run_id": run_id,
        "requested_orders": orders,
        "registration_count": len(owner_epochs),
        "reconnects": reconnects,
        "service_epochs": owner_epochs,
        "owner_epoch_changed": epoch_changed,
        "business_submit_attempts": submitted_attempts,
        "business_results": entries,
        "direct_adapter_calls": 0,
        "direct_fallback_configured": False,
        "normal_client_shutdown": normal_shutdown,
        "expected_unavailable": expect_unavailable,
        "expected_unavailable_observed": accepted_unavailable,
        "failure_phase": failure_phase,
        "failure_code": failure_code,
        "completed_at_utc": _utc_now(),
    }
    _write_json(output_dir / "strategy-probe.json", result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare", help="make separate synthetic owner and client bundles")
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--advertise-host", required=True)
    prepare.add_argument("--port", type=int, default=7601)
    prepare.add_argument("--request-timeout-ms", type=int, default=1_500)

    owner = commands.add_parser("owner", help="serve the synthetic account owner")
    owner.add_argument("--bundle-dir", required=True)
    owner.add_argument("--state-dir")
    owner.add_argument("--evidence-dir")
    owner.add_argument("--stop-after-submits", type=int)
    owner.add_argument("--max-runtime-seconds", type=float)

    owner_check = commands.add_parser(
        "second-owner-check", help="verify the active execution store rejects another owner"
    )
    owner_check.add_argument("--bundle-dir", required=True)
    owner_check.add_argument("--state-dir")
    owner_check.add_argument("--evidence-file")

    strategy = commands.add_parser("probe", help="send synthetic business requests over CURVE")
    strategy.add_argument("--bundle-dir", required=True)
    strategy.add_argument("--evidence-dir")
    strategy.add_argument("--orders", type=int, default=2)
    strategy.add_argument("--expect-unavailable", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_bundle(
                args.output_dir,
                advertise_host=args.advertise_host,
                port=args.port,
                request_timeout_ms=args.request_timeout_ms,
            )
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "owner":
            stop = threading.Event()

            def request_stop(_signum=None, _frame=None) -> None:
                stop.set()

            old_handlers = {}
            for signum in (signal.SIGINT, signal.SIGTERM):
                try:
                    old_handlers[signum] = signal.signal(signum, request_stop)
                except (ValueError, OSError):
                    continue

            def print_ready(record: dict[str, Any]) -> None:
                print(
                    json.dumps(
                        {"event": "owner_ready", **record},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    flush=True,
                )

            result = serve_owner(
                args.bundle_dir,
                state_dir=args.state_dir,
                evidence_dir=args.evidence_dir,
                stop_after_submits=args.stop_after_submits,
                max_runtime_seconds=args.max_runtime_seconds,
                stop_event=stop,
                on_ready=print_ready,
            )
            for signum, handler in old_handlers.items():
                try:
                    signal.signal(signum, handler)
                except (ValueError, OSError):
                    pass
            print(json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
            return 0 if result.get("phase") == "closed" and result.get("normal_shutdown") else 2
        if args.command == "second-owner-check":
            result = check_owner_exclusion(
                args.bundle_dir,
                state_dir=args.state_dir,
                evidence_file=args.evidence_file,
            )
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0 if result.get("second_owner_rejected") is True else 2
        if args.command == "probe":
            result = run_strategy_probe(
                args.bundle_dir,
                evidence_dir=args.evidence_dir,
                orders=args.orders,
                expect_unavailable=args.expect_unavailable,
            )
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            successful = (
                result.get("expected_unavailable_observed") is True
                if args.expect_unavailable
                else result.get("failure_code") is None
                and len(result.get("business_results", ())) == args.orders
                and result.get("normal_client_shutdown") is True
            )
            return 0 if successful else 2
    except Exception as error:
        code = _safe_code(error)
        print(json.dumps({"status": "blocked", "error_code": code}, separators=(",", ":")))
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
