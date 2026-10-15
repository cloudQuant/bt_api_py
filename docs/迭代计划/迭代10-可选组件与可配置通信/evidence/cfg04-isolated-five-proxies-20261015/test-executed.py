"""Installed-wheel service composition for Iteration 10 CFG02 (and CFG04 evidence)."""

from __future__ import annotations

import base64
import importlib
import importlib.metadata
import importlib.util
import json
import os
import queue
import socket
import subprocess
import sys
import sysconfig
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import zmq
from bt_api_base.account_protocol import (
    AccountBalance,
    AccountKey,
    AccountObservation,
    AccountResourceKey,
    AccountValuation,
)
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_account import ServiceAccountClient
from bt_api_py.service_config import ServiceConfigError, parse_client_config
from bt_api_py.service_execution import ServiceExecutionClient
from bt_api_py.service_market import ServiceMarketClient, ZmqMarketTransportFactory
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from bt_api_py.service_telemetry import ServiceTelemetryEmitter

_SERVICE_ID = "iteration10-cfg02-market-service"
_SERVICE_PRINCIPAL = "iteration10-cfg02-market-principal"
_SERVICE_INSTANCE = "iteration10-cfg02-market-hub"
_SERVICE_SCOPE_REF = "iteration10-cfg02-market-scope-v1"
_STRATEGY_REF = "iteration10-cfg02-strategy"
_STRATEGY_PRINCIPAL = "iteration10-cfg02-strategy-principal"
_ACCOUNT_REF = "iteration10-cfg02-account"
_DOMAIN_REF = "paper:iteration10-cfg02"
_SECURITY_REF = "iteration10-cfg02-curve-v1"
_TRANSPORT_REF = "iteration10-cfg02-listener-v1"
_MARKET_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_DOMAIN_PACKAGES = (
    "bt_api_gateway",
    "bt_api_execution",
    "bt_api_risk",
    "bt_api_monitor",
)
_EXPECTED_CLIENT_DISTRIBUTIONS = {
    "bt_api_py": "0.15.5",
    "bt_api_base": "0.15.8",
    "bt_api_transport_zmq": "0.1.0",
}
_EXPECTED_SERVER_DISTRIBUTIONS = {
    **_EXPECTED_CLIENT_DISTRIBUTIONS,
    "bt_api_gateway": "0.1.0",
}
_MARKET_KEY = MarketSubscriptionKey(
    source_ref="iteration10-cfg02-synthetic-source",
    entitlement_ref="iteration10-cfg02-synthetic-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="loopback",
        environment="paper",
        product_type="spot",
        instrument_id="iteration10-cfg02-instrument",
        native_symbol="SYNTH-USD",
    ),
    channel="trades",
)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise TypeError(f"unsupported public startup-summary value: {type(value).__name__}")


def _emit(value: Mapping[str, object]) -> None:
    sys.stdout.write(json.dumps(dict(value), sort_keys=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _distribution_versions() -> dict[str, str | None]:
    names = ("bt_api_py", "bt_api_base", "bt_api_transport_zmq", *_DOMAIN_PACKAGES)
    result: dict[str, str | None] = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def _module_origin(module_name: str) -> str:
    module = importlib.import_module(module_name)
    origin = getattr(module, "__file__", None)
    if type(origin) is not str or not origin:
        raise AssertionError(f"installed module has no file origin: {module_name}")
    return str(Path(origin).resolve())


def _assert_installed_origin(module_name: str) -> str:
    origin = Path(_module_origin(module_name))
    purelib = Path(sysconfig.get_paths()["purelib"]).resolve()
    if not origin.is_relative_to(purelib):
        raise AssertionError(f"{module_name} did not load from this venv site-packages")
    return str(origin)


def _domain_presence() -> dict[str, dict[str, bool]]:
    result: dict[str, dict[str, bool]] = {}
    for package in _DOMAIN_PACKAGES:
        try:
            importable = importlib.util.find_spec(package) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            importable = False
        result[package] = {
            "distribution_installed": _distribution_versions()[package] is not None,
            "import_spec_available": importable,
            "module_loaded": any(
                name == package or name.startswith(f"{package}.") for name in sys.modules
            ),
        }
    return result


def _free_endpoints() -> tuple[str, str]:
    sockets: list[socket.socket] = []
    try:
        endpoints: list[str] = []
        for _ in range(2):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            sockets.append(probe)
            endpoints.append(f"tcp://127.0.0.1:{probe.getsockname()[1]}")
        return endpoints[0], endpoints[1]
    finally:
        for probe in sockets:
            probe.close()


def _encode_key(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_key(value: object) -> bytes:
    if type(value) is not str:
        raise ValueError("invalid synthetic CURVE key field")
    return base64.b64decode(value.encode("ascii"), validate=True)


class _SyntheticProvider:
    def __init__(self) -> None:
        self.hub: Any = None
        self.generation: str | None = None
        self.handle: object | None = None
        self.starts = 0
        self.stops = 0

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        if key != _MARKET_KEY or not generation or self.handle is not None:
            raise AssertionError("unexpected or duplicate synthetic market subscription")
        self.starts += 1
        self.generation = generation
        self.handle = ("synthetic-market-handle", self.starts, key.stream_id, generation)
        return self.handle

    def unsubscribe(self, handle: object) -> bool:
        if self.handle is None or handle != self.handle:
            raise AssertionError("gateway attempted to release an unknown synthetic handle")
        self.stops += 1
        self.handle = None
        self.generation = None
        return True

    def publish(self, marker: str) -> int:
        if self.hub is None or self.generation is None or self.handle is None:
            raise AssertionError("synthetic market subscription is not active")
        sent_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        event = self.hub.publish(
            _MARKET_KEY,
            {"marker": marker, "source": "iteration10-cfg02-synthetic"},
            sent_at,
            generation=self.generation,
        )
        return event.header.sequence


def _read_server_commands(commands: queue.Queue[object]) -> None:
    for line in sys.stdin:
        try:
            command = json.loads(line)
        except (TypeError, ValueError):
            commands.put({"action": "invalid-control-message"})
            return
        commands.put(command)
        if isinstance(command, Mapping) and command.get("action") in {"stop", "close"}:
            return
    commands.put(None)


def _run_market_service() -> int:
    """Run the fixed Gateway component in the separately installed server venv."""

    from bt_api_gateway.market_hub import GatewayMarketHub

    first_line = sys.stdin.readline()
    if not first_line:
        return 2
    payload = json.loads(first_line)
    from bt_api_py.service_components import ComponentResources
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

    server_public = _decode_key(payload["server_public"])
    server_secret = _decode_key(payload["server_secret"])
    strategy_public = _decode_key(payload["strategy_public"])
    config = parse_service_config(payload["service_config"])
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    service_scope = WireScope(_DOMAIN_REF)
    strategy_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        _STRATEGY_PRINCIPAL,
        _STRATEGY_REF,
    )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=service_scope,
        scope_ref=_SERVICE_SCOPE_REF,
        scope_version="iteration10-cfg02-service-scope-v1",
        security_ref=_SECURITY_REF,
        security_version="curve-v1",
        registration_policy_ref="iteration10-cfg02-registration-v1",
        registration_policy_version="policy-v1",
        service_requirements_ref="iteration10-cfg02-requirements-v1",
        service_requirements_version="requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={strategy_public: _STRATEGY_PRINCIPAL},
        grants={
            _STRATEGY_PRINCIPAL: (
                PrincipalGrant(
                    strategy_identity,
                    (
                        ResourceGrant(
                            _SERVICE_INSTANCE,
                            "service.market_source",
                            _SERVICE_SCOPE_REF,
                            WireScope(
                                _DOMAIN_REF,
                                account_ref=_ACCOUNT_REF,
                                strategy_ref=_STRATEGY_REF,
                            ),
                            _MARKET_OPERATIONS,
                        ),
                    ),
                ),
            )
        },
    )
    provider = _SyntheticProvider()
    hub = GatewayMarketHub(
        provider,
        key_universe=frozenset({_MARKET_KEY}),
        principal_allowed_keys={
            _STRATEGY_PRINCIPAL: frozenset({_MARKET_KEY}),
        },
        service_identity=service_identity,
        service_scope=service_scope,
    )
    provider.hub = hub

    def resources(component: str, instance_id: str, options, _scope) -> ComponentResources:
        if (
            component != "gateway"
            or instance_id != _SERVICE_INSTANCE
            or options.get("role") != "market_hub"
        ):
            raise AssertionError("the service activated an undeclared component")
        return ComponentResources.owned(
            {"hub": hub},
            ready=lambda: hub.ready,
            close=hub.close,
        )

    host = ServiceHost(config, authority, payload["registry_path"], resources)
    command_queue: queue.Queue[object] = queue.Queue()
    reader = threading.Thread(
        target=_read_server_commands,
        args=(command_queue,),
        name="cfg02-control-reader",
    )
    try:
        reader.start()
        host.start()
        origins = {
            module_name: _assert_installed_origin(module_name)
            for module_name in (
                "bt_api_py.service_host",
                "bt_api_base.service_protocol",
                "bt_api_gateway.market_hub",
                "bt_api_transport_zmq.server",
            )
        }
        _emit(
            {
                "kind": "ready",
                "endpoint": host.endpoint,
                "market_endpoint": host.market_endpoint,
                "summary": _plain(host.summary),
                "python": sys.executable,
                "prefix": sys.prefix,
                "direct_url": _read_direct_url(),
                "origins": origins,
                "distributions": _distribution_versions(),
                "domain_presence": _domain_presence(),
            }
        )
        while True:
            host.serve_once(10)
            try:
                command = command_queue.get_nowait()
            except queue.Empty:
                continue
            if command is None:
                raise RuntimeError("control pipe closed before normal service shutdown")
            if not isinstance(command, Mapping):
                raise RuntimeError("invalid service control command")
            action = command.get("action")
            if action == "status":
                _emit(
                    {
                        "kind": "status",
                        "summary": _plain(host.summary),
                        "provider_starts": provider.starts,
                        "provider_stops": provider.stops,
                    }
                )
            elif action == "publish" and type(command.get("marker")) is str:
                sequence = provider.publish(command["marker"])
                _emit(
                    {
                        "kind": "published",
                        "marker": command["marker"],
                        "sequence": sequence,
                    }
                )
            elif action in {"stop", "close"}:
                host.close()
                reader.join(timeout=2)
                reader_stopped = not reader.is_alive()
                _emit(
                    {
                        "kind": "closed",
                        "summary": _plain(host.summary),
                        "provider_starts": provider.starts,
                        "provider_stops": provider.stops,
                        "control_reader_stopped": reader_stopped,
                    }
                )
                return 0 if reader_stopped else 3
            else:
                raise RuntimeError("unknown service control action")
    except BaseException as error:
        with suppress(Exception):
            _emit(
                {
                    "kind": "server_error",
                    "error_type": type(error).__name__,
                    "error_code": getattr(error, "code", None),
                }
            )
        raise
    finally:
        if host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()


def _stdout_json_reader(stream, output: queue.Queue[object]) -> None:
    for line in stream:
        try:
            output.put(json.loads(line))
        except (TypeError, ValueError):
            output.put({"kind": "invalid_server_output"})


def _receive_server(process, output: queue.Queue[object], timeout: float = 15.0) -> dict[str, Any]:
    try:
        value = output.get(timeout=timeout)
    except queue.Empty as error:
        raise AssertionError("separate Gateway service did not answer within its bound") from error
    if not isinstance(value, dict):
        raise AssertionError("separate Gateway service sent an invalid control record")
    if value.get("kind") == "server_error":
        raise AssertionError(
            f"separate Gateway service failed: {value.get('error_code') or value.get('error_type')}"
        )
    if process.poll() is not None and value.get("kind") != "closed":
        raise AssertionError(f"service exited before completing its response: {process.returncode}")
    return value


def _send_server(process: subprocess.Popen[str], value: Mapping[str, object]) -> None:
    if process.stdin is None or process.stdin.closed or process.poll() is not None:
        raise AssertionError("separate Gateway service is not running")
    process.stdin.write(json.dumps(dict(value), sort_keys=True, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _client_config(rpc_endpoint: str, market_endpoint: str) -> dict[str, object]:
    return {
        "config_schema_version": 6,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _STRATEGY_REF},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "store.market",
                            "service_id": _SERVICE_ID,
                            "instance_id": _SERVICE_INSTANCE,
                            "scope_ref": _SERVICE_SCOPE_REF,
                            "transport_ref": "iteration10-cfg02-client-v1",
                        }
                    ],
                    "options": {
                        "request_timeout_ms": 2_000,
                        "heartbeat_interval_seconds": 5,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "iteration10-cfg02-client-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {
                        "rpc_connect": rpc_endpoint,
                        "market_connect": market_endpoint,
                    },
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "request_timeout_ms": 2_000,
                        "high_water_mark": 128,
                        "max_message_bytes": 262_144,
                    },
                }
            },
        },
    }


def _service_config(rpc_endpoint: str, market_endpoint: str) -> dict[str, object]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": _TRANSPORT_REF,
        "registration_policy_ref": "iteration10-cfg02-registration-v1",
        "service_requirements_ref": "iteration10-cfg02-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": ["service.market_source"],
                    "options": {"role": "market_hub"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {
                        "rpc_bind": rpc_endpoint,
                        "market_bind": market_endpoint,
                    },
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "high_water_mark": 128,
                        "max_message_bytes": 262_144,
                    },
                }
            },
        },
    }


def _assert_domain_absence(presence: Mapping[str, Mapping[str, bool]]) -> None:
    assert set(presence) == set(_DOMAIN_PACKAGES)
    assert all(
        not row["distribution_installed"]
        and not row["import_spec_available"]
        and not row["module_loaded"]
        for row in presence.values()
    ), presence


def _assert_server_optional_domains_absent(
    presence: Mapping[str, Mapping[str, bool]],
) -> None:
    gateway = presence["bt_api_gateway"]
    assert gateway["distribution_installed"]
    assert gateway["import_spec_available"]
    assert gateway["module_loaded"]
    for package in ("bt_api_execution", "bt_api_risk", "bt_api_monitor"):
        row = presence[package]
        assert not row["distribution_installed"]
        assert not row["import_spec_available"]
        assert not row["module_loaded"]


def _read_direct_url() -> dict[str, Any] | None:
    value = importlib.metadata.distribution("bt_api_py").read_text("direct_url.json")
    return None if value is None else json.loads(value)


def _assert_exact_wheel_versions(
    installed: Mapping[str, str | None], expected: Mapping[str, str]
) -> None:
    assert {name: installed.get(name) for name in expected} == dict(expected), installed


def test_installed_gateway_component_serves_a_registered_strategy_without_other_domains(
    tmp_path: Path,
) -> None:
    """A committed-wheel client and one declared Gateway service exchange a real frame."""

    assert sys.prefix != sys.base_prefix, "formal node must run in an isolated client venv"
    client_prefix = str(Path(sys.prefix).resolve())
    direct_url = _read_direct_url()
    assert not (direct_url or {}).get("dir_info", {}).get("editable", False)
    client_distributions = _distribution_versions()
    _assert_exact_wheel_versions(client_distributions, _EXPECTED_CLIENT_DISTRIBUTIONS)
    client_domain_presence = _domain_presence()
    _assert_domain_absence(client_domain_presence)
    client_origins = {
        module_name: _assert_installed_origin(module_name)
        for module_name in (
            "bt_api_py.service_market",
            "bt_api_base.market_protocol",
            "bt_api_base.service_protocol",
            "bt_api_transport_zmq.market_v2",
        )
    }

    server_python_raw = os.environ.get("BT_API_ITERATION10_CFG02_SERVER_PYTHON")
    assert server_python_raw, "Root must provide the separate installed Gateway server interpreter"
    server_python = Path(server_python_raw).resolve()
    assert server_python.is_file()

    rpc_endpoint, market_endpoint = _free_endpoints()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    payload = {
        "service_config": _service_config(rpc_endpoint, market_endpoint),
        "registry_path": str(tmp_path / "cfg02-service-registry.sqlite3"),
        "server_public": _encode_key(server_public),
        "server_secret": _encode_key(server_secret),
        "strategy_public": _encode_key(strategy_public),
    }
    source_file = Path(__file__).resolve()
    stderr_path = tmp_path / "cfg02-service-stderr.txt"
    control_output: queue.Queue[object] = queue.Queue()
    runtime: ServiceClientRuntime | None = None
    market: ServiceMarketClient | None = None
    forced_cleanup: list[str] = []
    server_closed_normally = False
    with stderr_path.open("w", encoding="utf-8", errors="backslashreplace") as stderr_file:
        process = subprocess.Popen(
            [str(server_python), str(source_file), "--cfg02-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
            bufsize=1,
            cwd=tmp_path,
        )
        assert process.stdin is not None and process.stdout is not None
        output_reader = threading.Thread(
            target=_stdout_json_reader,
            args=(process.stdout, control_output),
            name="cfg02-service-output-reader",
            daemon=True,
        )
        output_reader.start()
        try:
            process.stdin.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            process.stdin.flush()
            ready = _receive_server(process, control_output)
            assert ready.get("kind") == "ready", ready
            assert ready["prefix"] != client_prefix
            _assert_server_optional_domains_absent(ready["domain_presence"])
            server_distributions = ready["distributions"]
            _assert_exact_wheel_versions(
                server_distributions,
                _EXPECTED_SERVER_DISTRIBUTIONS,
            )
            assert not (ready["direct_url"] or {}).get("dir_info", {}).get("editable", False)
            for name in ("bt_api_execution", "bt_api_risk", "bt_api_monitor"):
                assert server_distributions[name] is None, server_distributions
            assert ready["summary"]["state"] == "started"
            assert ready["summary"]["service_id"] == _SERVICE_ID
            assert ready["summary"]["components"] == [
                {
                    "component": "gateway",
                    "instance_id": _SERVICE_INSTANCE,
                    "declared_ports": ["service.market_source"],
                    "capabilities": [
                        {
                            "port": "service.market_source",
                            "operations": sorted(_MARKET_OPERATIONS),
                        }
                    ],
                    "internal_ports": [],
                }
            ], ready["summary"]

            service_identity = WireIdentity(
                WireRole.SERVICE,
                _SERVICE_PRINCIPAL,
                _SERVICE_ID,
            )
            strategy_identity = WireIdentity(
                WireRole.STRATEGY_CLIENT,
                _STRATEGY_PRINCIPAL,
                _STRATEGY_REF,
            )
            credentials = ClientCurveKeys(server_public, strategy_public, strategy_secret)
            factories = {_SECURITY_REF: credentials}
            runtime = ServiceClientRuntime(
                parse_client_config(_client_config(ready["endpoint"], ready["market_endpoint"])),
                identity=strategy_identity,
                scope_resolver=lambda _scope_ref: WireScope(
                    _DOMAIN_REF,
                    account_ref=_ACCOUNT_REF,
                    strategy_ref=_STRATEGY_REF,
                ),
                peer_resolver=lambda _service_id: service_identity,
                transport_factory=ZmqTransportFactory(factories),
                run_id="iteration10-cfg02-run-v1",
                requested_operations={"store.market": _MARKET_OPERATIONS},
            )
            registration = runtime.start()
            assert registration.degraded_count == 0
            assert len(registration.bindings) == 1
            binding = registration.bindings[0]
            assert binding.mount == "store.market"
            assert binding.operations == _MARKET_OPERATIONS

            market = ServiceMarketClient(
                runtime,
                "store.market",
                _MARKET_KEY,
                subscriber_factory=ZmqMarketTransportFactory(factories),
                stream_scope=WireScope(_DOMAIN_REF),
                head_check_interval_seconds=2.0,
            )
            bootstrap = market.start()
            assert bootstrap.snapshot_complete is False
            assert market.healthy
            assert market.unsubscription_uncertain is False

            # Give the real CURVE SUB socket a bounded join window; warm-up
            # frames are intentionally consumed before the assertion frame.
            time.sleep(0.2)
            for index in range(3):
                marker = f"warmup-{index}"
                _send_server(process, {"action": "publish", "marker": marker})
                published = _receive_server(process, control_output)
                assert published.get("kind") == "published" and published.get("marker") == marker
                market.poll(timeout_ms=100)

            target_marker = "cfg02-registered-strategy-market-frame"
            _send_server(process, {"action": "publish", "marker": target_marker})
            published = _receive_server(process, control_output)
            assert published.get("kind") == "published"
            deadline = time.monotonic() + 4.0
            observed = None
            while time.monotonic() < deadline:
                event = market.poll(timeout_ms=250)
                if event is not None and event.payload["data"].get("marker") == target_marker:
                    observed = event
                    break
            assert observed is not None, "strategy market proxy did not deliver the provider frame"
            assert observed.payload["key"] == _MARKET_KEY.to_dict()
            assert observed.payload["data"] == {
                "marker": target_marker,
                "source": "iteration10-cfg02-synthetic",
            }
            assert observed.header.sequence == published["sequence"]

            _send_server(process, {"action": "status"})
            active = _receive_server(process, control_output)
            assert active.get("kind") == "status"
            readiness = active["summary"]["market"]
            assert readiness["active_registrations"] == 1
            assert active["provider_starts"] == 1
            assert active["provider_stops"] == 0

            market.close()
            assert market.healthy is False
            assert market.unsubscription_uncertain is False
            market = None
            runtime.close()
            assert runtime.state == "closed"
            assert runtime.cleanup_failures == 0
            assert runtime.unregister_uncertainties == 0

            _send_server(process, {"action": "status"})
            released = _receive_server(process, control_output)
            assert released.get("kind") == "status"
            assert released["summary"]["market"]["active_registrations"] == 0
            assert released["provider_starts"] == 1
            assert released["provider_stops"] == 1

            _send_server(process, {"action": "close"})
            process.stdin.close()
            closed = _receive_server(process, control_output)
            assert closed.get("kind") == "closed"
            assert closed.get("control_reader_stopped") is True
            assert closed["summary"]["state"] == "closed"
            assert closed["provider_starts"] == closed["provider_stops"] == 1
            process.wait(timeout=15)
            assert process.returncode == 0
            server_closed_normally = True
        finally:
            if market is not None:
                with suppress(Exception):
                    market.close()
            if runtime is not None and runtime.state != "closed":
                with suppress(Exception):
                    runtime.close()
            if process.poll() is None:
                try:
                    _send_server(process, {"action": "close"})
                    cleanup_reply = _receive_server(process, control_output, timeout=8.0)
                    if cleanup_reply.get("kind") == "closed":
                        process.wait(timeout=8)
                        server_closed_normally = process.returncode == 0
                except BaseException as error:
                    forced_cleanup.append(f"cleanup_exception:{type(error).__name__}")
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    forced_cleanup.append("server_terminate")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        forced_cleanup.append("server_kill")
                        process.kill()
                        process.wait(timeout=5)
            output_reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()

    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    assert server_closed_normally
    assert forced_cleanup == [], forced_cleanup
    assert process.returncode == 0
    assert not output_reader.is_alive(), "server output reader remained after child exit"
    assert client_prefix != str(Path(sys.base_prefix).resolve())
    report = {
        "status": "PASS_CFG02_SINGLE_DECLARED_GATEWAY_COMPONENT_INSTALLED_WHEEL",
        "client_python": sys.executable,
        "client_prefix": client_prefix,
        "client_distributions": client_distributions,
        "client_domain_presence": client_domain_presence,
        "client_origins": client_origins,
        "server_python": ready["python"],
        "server_prefix": ready["prefix"],
        "server_distributions": ready["distributions"],
        "server_domain_presence": ready["domain_presence"],
        "server_origins": ready["origins"],
        "startup_components": ready["summary"]["components"],
        "market_frame_sequence": published["sequence"],
        "normal_host_close": server_closed_normally,
        "server_control_reader_stopped": closed["control_reader_stopped"],
        "server_output_reader_stopped": not output_reader.is_alive(),
        "forced_cleanup": forced_cleanup,
        "server_stderr_bytes": len(stderr_text.encode("utf-8")),
        "server_stderr_classification": (
            "not captured after close" if not stderr_text else "retained separately in test tempdir"
        ),
    }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__" and sys.argv[1:] == ["--cfg02-server"]:
    raise SystemExit(_run_market_service())


_CFG04_SERVICE_ID = "iteration10-cfg04-service"
_CFG04_SERVICE_PRINCIPAL = "iteration10-cfg04-service-principal"
_CFG04_SERVICE_SCOPE_REF = "iteration10-cfg04-service-scope-v1"
_CFG04_STRATEGY_REF = "iteration10-cfg04-strategy"
_CFG04_STRATEGY_PRINCIPAL = "iteration10-cfg04-strategy-principal"
_CFG04_DOMAIN_REF = "paper:iteration10-cfg04"
_CFG04_ACCOUNT_REF = "iteration10-cfg04-account"
_CFG04_ACCOUNT_KEY = AccountKey("synthetic", "loopback", "paper", "cfg04-owner")
_CFG04_ACCOUNT_RESOURCE = AccountResourceKey("spot", "cash")
_CFG04_WIRE_SCOPE = WireScope(
    _CFG04_DOMAIN_REF,
    account_ref=_CFG04_ACCOUNT_REF,
    strategy_ref=_CFG04_STRATEGY_REF,
)
_CFG04_TRANSPORT_REF = "iteration10-cfg04-curve-v1"
_CFG04_ACCOUNT_INSTANCE = "iteration10-cfg04-account-owner"
_CFG04_EXECUTION_INSTANCE = "iteration10-cfg04-execution-owner"
_CFG04_RISK_INSTANCE = "iteration10-cfg04-risk-reader"
_CFG04_MONITOR_INSTANCE = "iteration10-cfg04-telemetry-ingress"
_CFG04_ACCOUNT_SCOPE_REF = "iteration10-cfg04-account-scope-v1"
_CFG04_ORDER_SCOPE_REF = "iteration10-cfg04-order-scope-v1"
_CFG04_QUERY_SCOPE_REF = "iteration10-cfg04-query-scope-v1"
_CFG04_RISK_SCOPE_REF = "iteration10-cfg04-risk-scope-v1"
_CFG04_TELEMETRY_SCOPE_REF = "iteration10-cfg04-telemetry-scope-v1"
_CFG04_EXECUTION_DAY = "20261015"
_CFG04_MAX_MESSAGE_BYTES = 262_144
_CFG04_CLIENT_MOUNTS = frozenset(
    {
        "store.account",
        "broker.orders",
        "broker.order_query",
        "risk.read_model",
        "runtime.telemetry",
    }
)


class _Cfg04AccountProvider:
    """Local synthetic account facts for the independently installed server."""

    def __init__(self) -> None:
        self.generation: str | None = None
        self.connect_calls = 0
        self.read_calls = 0
        self.close_calls = 0

    def connect(self, *, account_key, generation: str, timeout: float) -> None:
        if account_key != _CFG04_ACCOUNT_KEY or not generation or timeout <= 0:
            raise AssertionError("synthetic account session received invalid metadata")
        if self.generation is not None:
            raise AssertionError("synthetic account provider was connected twice")
        self.generation = generation
        self.connect_calls += 1

    def read_account(self, *, generation: str, timeout: float) -> AccountObservation:
        if generation != self.generation or timeout <= 0:
            raise AssertionError("synthetic account read used a stale session")
        self.read_calls += 1
        observed_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        balance = AccountBalance(
            resource=_CFG04_ACCOUNT_RESOURCE,
            currency="USD",
            total="123.45",
            available="120.00",
            locked="3.45",
            observed_at_utc=observed_at,
        )
        valuation = AccountValuation(
            currency="USD",
            available_cash="120.00",
            equity="123.45",
            observed_at_utc=observed_at,
        )
        return AccountObservation(
            account_key=_CFG04_ACCOUNT_KEY,
            observation_id=f"cfg04-account-observation-{self.read_calls}",
            coverage=(_CFG04_ACCOUNT_RESOURCE,),
            balances=(balance,),
            positions=(),
            valuation=valuation,
            started_at_utc=observed_at,
            completed_at_utc=observed_at,
            consistency="provider_atomic",
            balances_complete=True,
            positions_complete=True,
        )

    def close(self) -> bool:
        self.close_calls += 1
        self.generation = None
        return True


class _Cfg04Permit:
    def __init__(self, permit_id: str) -> None:
        self.permit_id = permit_id


class _Cfg04Admission:
    """Synthetic admission port used only by this local provider fixture."""

    def __init__(self) -> None:
        self.reserved: list[str] = []
        self.claimed: list[str] = []
        self.cancel_claimed: list[str] = []

    def reserve(self, intent):
        identifier = getattr(intent, "intent_id", None) or intent.cancel_id
        self.reserved.append(identifier)
        return _Cfg04Permit(f"cfg04-permit-{identifier}")

    def validate(self, permit_id: str, _intent):
        return _Cfg04Permit(permit_id)

    def claim_for_dispatch(self, permit_id: str, intent):
        self.claimed.append(intent.intent_id)
        return _Cfg04Permit(permit_id)

    def claim_before_dispatch(self, permit_id: str, intent):
        from bt_api_execution import CancelDispatchClaimReceiptV1

        self.cancel_claimed.append(intent.cancel_id)
        return CancelDispatchClaimReceiptV1(
            permit_reference=permit_id,
            scope_key=intent.scope.key,
            cancel_id=intent.cancel_id,
            intent_fingerprint=intent.fingerprint,
        )

    def release(self, _permit_id: str, _reason: str) -> None:
        return None

    def settle(self, _permit_id: str) -> None:
        return None


class _Cfg04ProviderDispatch:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.cancelled: list[str] = []

    def submit(self, intent):
        from bt_api_execution import ProviderObservation

        self.submitted.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, "cfg04-provider-order-1")

    def cancel(self, intent):
        from bt_api_execution import CancelObservation

        self.cancelled.append(intent.cancel_id)
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


def _cfg04_component(
    instance_id: str, ports: tuple[str, ...], options: Mapping[str, object]
) -> dict[str, object]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": list(ports),
            "options": dict(options),
        },
    }


def _cfg04_client_config(rpc_endpoint: str, *, include_group: bool = False) -> dict[str, object]:
    def binding(mount: str, instance_id: str, scope_ref: str) -> dict[str, object]:
        return {
            "mount": mount,
            "service_id": _CFG04_SERVICE_ID,
            "instance_id": instance_id,
            "scope_ref": scope_ref,
            "transport_ref": _CFG04_TRANSPORT_REF,
            "account_ref": _CFG04_ACCOUNT_REF,
        }

    execution_bindings = [
        binding("broker.orders", _CFG04_EXECUTION_INSTANCE, _CFG04_ORDER_SCOPE_REF),
        binding("broker.order_query", _CFG04_EXECUTION_INSTANCE, _CFG04_QUERY_SCOPE_REF),
    ]
    if include_group:
        execution_bindings.append(
            {
                "mount": "broker.order_groups",
                "service_id": _CFG04_SERVICE_ID,
                "instance_id": "iteration10-cfg04-group-coordinator",
                "scope_ref": "iteration10-cfg04-group-scope-v1",
                "transport_ref": _CFG04_TRANSPORT_REF,
                "portfolio_ref": "iteration10-cfg04-portfolio",
            }
        )

    return {
        "config_schema_version": 6,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _CFG04_STRATEGY_REF},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        binding(
                            "store.account",
                            _CFG04_ACCOUNT_INSTANCE,
                            _CFG04_ACCOUNT_SCOPE_REF,
                        )
                    ],
                    "options": {"request_timeout_ms": 3_000},
                },
            },
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": execution_bindings,
                    "options": {"request_timeout_ms": 3_000},
                },
            },
            "risk": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        binding(
                            "risk.read_model",
                            _CFG04_RISK_INSTANCE,
                            _CFG04_RISK_SCOPE_REF,
                        )
                    ],
                    "options": {"request_timeout_ms": 3_000},
                },
            },
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "runtime.telemetry",
                            "service_id": _CFG04_SERVICE_ID,
                            "instance_id": _CFG04_MONITOR_INSTANCE,
                            "scope_ref": _CFG04_TELEMETRY_SCOPE_REF,
                            "transport_ref": _CFG04_TRANSPORT_REF,
                            "required": False,
                        }
                    ],
                    "options": {"request_timeout_ms": 3_000},
                },
            },
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _CFG04_TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": rpc_endpoint},
                    "security_profile_ref": _CFG04_TRANSPORT_REF,
                    "options": {
                        "request_timeout_ms": 3_000,
                        "high_water_mark": 64,
                        "max_message_bytes": _CFG04_MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _cfg04_service_config(rpc_endpoint: str) -> dict[str, object]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _CFG04_SERVICE_ID,
        "scope_ref": _CFG04_SERVICE_SCOPE_REF,
        "transport_ref": _CFG04_TRANSPORT_REF,
        "registration_policy_ref": "iteration10-cfg04-registration-v1",
        "service_requirements_ref": "iteration10-cfg04-requirements-v1",
        "components": {
            "gateway": _cfg04_component(
                _CFG04_ACCOUNT_INSTANCE,
                ("service.account_read", "service.provider_session"),
                {
                    "role": "account_session_worker",
                    "account_session_ref": "iteration10-cfg04-session-v1",
                },
            ),
            "execution": _cfg04_component(
                _CFG04_EXECUTION_INSTANCE,
                ("service.order_entry", "service.order_query"),
                {"role": "account_owner"},
            ),
            "risk": _cfg04_component(
                _CFG04_RISK_INSTANCE,
                ("service.risk_query",),
                {
                    "role": "account_admission",
                    "account_policy_ref": "iteration10-cfg04-risk-policy-v1",
                },
            ),
            "monitor": _cfg04_component(
                _CFG04_MONITOR_INSTANCE,
                ("service.telemetry_ingress",),
                {
                    "role": "telemetry_ingress",
                    "max_streams": 16,
                    "max_bytes": 65_536,
                    "heartbeat_timeout_seconds": 60,
                    "retention_seconds": 600,
                },
            ),
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _CFG04_TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": rpc_endpoint},
                    "security_profile_ref": _CFG04_TRANSPORT_REF,
                    "options": {
                        "high_water_mark": 64,
                        "max_message_bytes": _CFG04_MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _cfg04_reject_client_config(raw: dict[str, object], expected_code: str) -> None:
    try:
        parse_client_config(raw)
    except ServiceConfigError as error:
        assert error.code == expected_code, (error.code, expected_code)
    else:
        raise AssertionError(f"client config was accepted; expected {expected_code}")


def _cfg04_cold_import_probe(cwd: Path) -> dict[str, Any]:
    """Import the installed SDK client surfaces with a domain-import tripwire."""

    script = r'''
import importlib.abc
import importlib.metadata
import json
import sys

domains = ("bt_api_execution", "bt_api_gateway", "bt_api_monitor", "bt_api_risk")
attempted = []

class DomainImportBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        del path, target
        if any(fullname == name or fullname.startswith(name + ".") for name in domains):
            attempted.append(fullname)
            raise AssertionError("domain import blocked")
        return None

sys.meta_path.insert(0, DomainImportBlocker())
from bt_api_py.service_account import ServiceAccountClient
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_execution import ServiceExecutionClient
from bt_api_py.service_runtime import ServiceClientRuntime
from bt_api_py.service_telemetry import ServiceTelemetryEmitter

versions = {}
for name in domains:
    try:
        versions[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        versions[name] = None
loaded = sorted(name for name in sys.modules if any(
    name == domain or name.startswith(domain + ".") for domain in domains
))
assert not attempted, attempted
assert not loaded, loaded
assert all(version is None for version in versions.values()), versions
print(json.dumps({
    "attempted_domain_imports": attempted,
    "loaded_domain_modules": loaded,
    "domain_distributions": versions,
    "client_symbols": [
        ServiceAccountClient.__name__,
        ServiceExecutionClient.__name__,
        ServiceClientRuntime.__name__,
        ServiceTelemetryEmitter.__name__,
        parse_client_config.__name__,
    ],
}, sort_keys=True, separators=(",", ":")))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    record = json.loads(completed.stdout.strip())
    assert record["attempted_domain_imports"] == []
    assert record["loaded_domain_modules"] == []
    assert set(record["domain_distributions"]) == set(_DOMAIN_PACKAGES)
    assert all(value is None for value in record["domain_distributions"].values())
    assert set(record["client_symbols"]) == {
        "ServiceAccountClient",
        "ServiceExecutionClient",
        "ServiceClientRuntime",
        "ServiceTelemetryEmitter",
        "parse_client_config",
    }
    return record


def _run_cfg04_server() -> int:
    """Serve five implemented strategy mounts from a separate installed venv."""

    from bt_api_execution import (
        AllocationOwnerKind,
        ExecutionAccountSnapshot,
        ExecutionAllocationBaseline,
        ExecutionOwnerScope,
        ExecutionScope,
        ExecutionScopeDirectory,
        SqliteExecutionStore,
    )
    from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
    from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker
    from bt_api_gateway.service_component import GatewayServiceAccountScope
    from bt_api_gateway.session_guard import LocalAccountSessionGuard
    from bt_api_risk import AccountScope, DurableRiskGate, RiskPolicy

    from bt_api_py.service_components import ComponentResources
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_execution_account_books import ExecutionAccountBookReadInputsV1
    from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

    first_line = sys.stdin.readline()
    if not first_line:
        return 2
    payload = json.loads(first_line)
    service_config = parse_service_config(payload["service_config"])
    server_public = _decode_key(payload["server_public"])
    server_secret = _decode_key(payload["server_secret"])
    strategy_public = _decode_key(payload["strategy_public"])
    service_identity = WireIdentity(
        WireRole.SERVICE, _CFG04_SERVICE_PRINCIPAL, _CFG04_SERVICE_ID
    )
    strategy_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT, _CFG04_STRATEGY_PRINCIPAL, _CFG04_STRATEGY_REF
    )
    order_scope = ExecutionScope(
        "SYNTHETIC", "simulation", _CFG04_ACCOUNT_REF, _CFG04_STRATEGY_REF, _CFG04_EXECUTION_DAY
    )
    risk_scope = AccountScope("SYNTHETIC", "cfg04-risk-account", "paper")

    class Cfg04AccountBooksSource:
        """Installed account-book resource with a complete, zero-position cut."""

        def __init__(self) -> None:
            self.baseline_as_of_ns = time.time_ns()

        def authorize_account_view(self, *, principal_ref, identity, scope, account_key) -> bool:
            # This strategy-only grant does not authorize the aggregate account view.
            return False

        def authorize_manual_book_view(
            self, *, principal_ref, identity, scope, account_key, manual_book_ref
        ) -> bool:
            return False

        def read_inputs(self, *, account_key, journal_generation, execution_cursor):
            if account_key != order_scope.account_key or execution_cursor < 0:
                raise AssertionError("account-book read crossed its trusted account cut")
            as_of_ns = max(time.time_ns(), self.baseline_as_of_ns + 1)
            directory_revision = "cfg04-owner-directory-r1"
            provider_generation = "cfg04-provider-generation-r1"
            directory = ExecutionScopeDirectory(
                account_key=order_scope.account_key,
                source="trusted-cfg04-owner-registry",
                source_ref="cfg04-complete-directory-r1",
                revision=directory_revision,
                covers_from_ns=self.baseline_as_of_ns,
                as_of_ns=as_of_ns,
                scopes=(
                    ExecutionOwnerScope(
                        order_scope,
                        AllocationOwnerKind.STRATEGY,
                        _CFG04_STRATEGY_REF,
                        _CFG04_WIRE_SCOPE,
                    ),
                ),
            )
            baseline = ExecutionAccountSnapshot(
                account_key=order_scope.account_key,
                source="cfg04-synthetic-net-position-snapshot",
                source_ref="cfg04-provider-baseline-r1",
                generation=provider_generation,
                journal_generation=journal_generation,
                as_of_ns=self.baseline_as_of_ns,
                execution_cursor=0,
                position_mode="NET",
                positions=(),
                cumulative_fees=(),
                fee_coverage_start_ns=self.baseline_as_of_ns,
            )
            current = ExecutionAccountSnapshot(
                account_key=order_scope.account_key,
                source="cfg04-synthetic-net-position-snapshot",
                source_ref="cfg04-provider-current-r1",
                generation=provider_generation,
                journal_generation=journal_generation,
                as_of_ns=as_of_ns,
                execution_cursor=execution_cursor,
                position_mode="NET",
                positions=(),
                cumulative_fees=(),
                fee_coverage_start_ns=self.baseline_as_of_ns,
            )
            allocation_baseline = ExecutionAllocationBaseline(
                account_key=order_scope.account_key,
                source="cfg04-trusted-virtual-position-ledger",
                source_ref="cfg04-virtual-baseline-r1",
                revision="cfg04-virtual-baseline-revision-1",
                scope_directory_revision=directory_revision,
                provider_generation=provider_generation,
                journal_generation=journal_generation,
                as_of_ns=self.baseline_as_of_ns,
                execution_cursor=0,
                positions=(),
            )
            return ExecutionAccountBookReadInputsV1(
                directory,
                baseline,
                current,
                allocation_baseline,
                None,
            )

    account_books_source = Cfg04AccountBooksSource()
    account_scope = GatewayServiceAccountScope(
        account_ref=_CFG04_ACCOUNT_REF,
        execution_domain_ref=_CFG04_DOMAIN_REF,
        journal_account_scope="journal:iteration10-cfg04-account",
        account_read_strategy_scope="state:iteration10-cfg04-account",
        shared_account_facts_scope="state:iteration10-cfg04-account",
        shared_fact_strategy_refs=frozenset({_CFG04_STRATEGY_REF}),
    )
    gateway_principal = GatewayPrincipal(
        principal_id=_CFG04_STRATEGY_PRINCIPAL,
        account_scopes=frozenset({"journal:iteration10-cfg04-account"}),
        strategy_scopes=frozenset({"state:iteration10-cfg04-account"}),
        allowed_kinds=frozenset({GatewayCommandKind.READ}),
    )
    operations_by_port = (
        (
            _CFG04_ACCOUNT_INSTANCE,
            "service.account_read",
            _CFG04_ACCOUNT_SCOPE_REF,
            frozenset({"account.snapshot"}),
        ),
        (
            _CFG04_EXECUTION_INSTANCE,
            "service.order_entry",
            _CFG04_ORDER_SCOPE_REF,
            frozenset({"orders.submit", "orders.cancel"}),
        ),
        (
            _CFG04_EXECUTION_INSTANCE,
            "service.order_query",
            _CFG04_QUERY_SCOPE_REF,
            frozenset({"orders.query", "execution.account_books.query"}),
        ),
        (
            _CFG04_RISK_INSTANCE,
            "service.risk_query",
            _CFG04_RISK_SCOPE_REF,
            frozenset({"risk.snapshot"}),
        ),
        (
            _CFG04_MONITOR_INSTANCE,
            "service.telemetry_ingress",
            _CFG04_TELEMETRY_SCOPE_REF,
            frozenset({"telemetry.append"}),
        ),
    )
    resource_grants = tuple(
        ResourceGrant(instance_id, port, scope_ref, _CFG04_WIRE_SCOPE, operations)
        for instance_id, port, scope_ref, operations in operations_by_port
    )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=WireScope(_CFG04_DOMAIN_REF),
        scope_ref=_CFG04_SERVICE_SCOPE_REF,
        scope_version="iteration10-cfg04-service-scope-v1",
        security_ref=_CFG04_TRANSPORT_REF,
        security_version="curve-v1",
        registration_policy_ref=service_config.registration_policy_ref,
        registration_policy_version="iteration10-cfg04-registration-v1",
        service_requirements_ref=service_config.service_requirements_ref,
        service_requirements_version="iteration10-cfg04-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={strategy_public: _CFG04_STRATEGY_PRINCIPAL},
        grants={
            _CFG04_STRATEGY_PRINCIPAL: (
                PrincipalGrant(strategy_identity, resource_grants),
            )
        },
    )

    account_provider = _Cfg04AccountProvider()
    execution_provider = _Cfg04ProviderDispatch()
    admission = _Cfg04Admission()
    account_worker_holder: dict[str, object] = {}
    component_closes: list[str] = []

    def resource_provider(component, instance_id, options, _scope):
        if component == "gateway":
            journal = GatewayEventJournal(payload["account_journal_path"])
            guard_directory = Path(payload["account_guard_directory"])
            guard_directory.mkdir(parents=True, exist_ok=True)
            worker = GatewayAccountSessionWorker(
                account_provider,
                journal,
                account_key=_CFG04_ACCOUNT_KEY,
                journal_account_scope="journal:iteration10-cfg04-account",
                state_scope="state:iteration10-cfg04-account",
                guard=LocalAccountSessionGuard(
                    guard_directory,
                    _CFG04_ACCOUNT_KEY.account_key_id,
                    owner_ref="iteration10-cfg04-service-owner",
                ),
                required_coverage=(_CFG04_ACCOUNT_RESOURCE,),
                refresh_interval_seconds=60,
                max_staleness_seconds=3_600,
                request_timeout_seconds=2,
            )
            account_worker_holder["worker"] = worker

            def close_gateway() -> None:
                worker.close()
                component_closes.append("gateway")

            return ComponentResources.owned(
                {
                    "journal": journal,
                    "accounts": {_CFG04_ACCOUNT_REF: account_scope},
                    "principals": {_CFG04_STRATEGY_PRINCIPAL: gateway_principal},
                },
                ready=lambda: worker.ready,
                close=close_gateway,
                internal_ports={"service.provider_session": worker},
            )

        if component == "execution":
            store = SqliteExecutionStore(payload["execution_store_path"])

            def close_execution() -> None:
                store.close()
                component_closes.append("execution")

            return ComponentResources.owned(
                {
                    "store": store,
                    "targets": {_CFG04_WIRE_SCOPE: order_scope},
                    "dispatch": execution_provider,
                    "admission": {"order_gate": admission, "cancel_gate": admission},
                    "account_books": account_books_source,
                },
                ready=lambda: True,
                close=close_execution,
            )

        if component == "risk":
            risk_gate = DurableRiskGate(
                payload["risk_store_path"],
                RiskPolicy(
                    policy_id="iteration10-cfg04-policy",
                    max_increase_notional=Decimal("100000"),
                    max_increase_count=20,
                    permit_ttl_seconds=600,
                ),
            )

            def close_risk() -> None:
                risk_gate.close()
                component_closes.append("risk")

            return ComponentResources.owned(
                {"gate": risk_gate, "targets": {_CFG04_WIRE_SCOPE: risk_scope}},
                ready=lambda: True,
                close=close_risk,
            )

        if component == "monitor":
            return ComponentResources.borrowed(
                {"targets": frozenset({_CFG04_WIRE_SCOPE})}, ready=lambda: True
            )

        raise AssertionError(f"unexpected component: {component}:{instance_id}:{options}")

    host = None
    commands: queue.Queue[object] = queue.Queue()
    reader = threading.Thread(
        target=_read_server_commands,
        args=(commands,),
        name="cfg04-service-control-reader",
    )
    try:
        host = ServiceHost(
            service_config,
            authority,
            payload["registry_path"],
            resource_provider,
        )
        reader.start()
        host.start()
        # Force all selected package modules to be loaded before reporting the
        # server installation profile; the client remains in its own process.
        origins = {
            module_name: _assert_installed_origin(module_name)
            for module_name in (
                "bt_api_py.service_host",
                "bt_api_py.service_execution_account_books",
                "bt_api_base.account_protocol",
                "bt_api_base.service_protocol",
                "bt_api_gateway.service_component",
                "bt_api_execution.service_component",
                "bt_api_risk.service_component",
                "bt_api_monitor.service_telemetry",
                "bt_api_transport_zmq.server",
            )
        }
        worker = account_worker_holder.get("worker")
        if worker is None or worker.ready is not True:
            raise AssertionError("gateway account worker did not become ready")
        _emit(
            {
                "kind": "ready",
                "pid": os.getpid(),
                "endpoint": host.endpoint,
                "summary": _plain(host.summary),
                "python": sys.executable,
                "prefix": sys.prefix,
                "direct_url": _read_direct_url(),
                "origins": origins,
                "distributions": _distribution_versions(),
                "domain_presence": _domain_presence(),
            }
        )
        while True:
            host.serve_once(timeout_ms=50)
            try:
                command = commands.get_nowait()
            except queue.Empty:
                continue
            if command is None:
                raise RuntimeError("control pipe closed before host close command")
            if not isinstance(command, Mapping):
                raise RuntimeError("invalid control command")
            action = command.get("action")
            if action == "status":
                _emit(
                    {
                        "kind": "status",
                        "host_state": host.state,
                        "account_connect_calls": account_provider.connect_calls,
                        "account_read_calls": account_provider.read_calls,
                        "account_close_calls": account_provider.close_calls,
                        "submitted": tuple(execution_provider.submitted),
                        "cancelled": tuple(execution_provider.cancelled),
                        "admission_reserved": tuple(admission.reserved),
                    }
                )
            elif action in {"close", "stop"}:
                host.close()
                reader.join(timeout=3)
                reader_stopped = not reader.is_alive()
                _emit(
                    {
                        "kind": "closed",
                        "summary": _plain(host.summary),
                        "account_close_calls": account_provider.close_calls,
                        "resource_closes": tuple(sorted(component_closes)),
                        "control_reader_stopped": reader_stopped,
                    }
                )
                return 0 if reader_stopped else 3
            else:
                raise RuntimeError("unknown service control action")
    except BaseException as error:
        with suppress(Exception):
            _emit(
                {
                    "kind": "server_error",
                    "error_type": type(error).__name__,
                    "error_code": getattr(error, "code", None),
                }
            )
        raise
    finally:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        if reader.is_alive():
            reader.join(timeout=1)


def test_isolated_strategy_runs_implemented_proxies_without_domain_distributions(
    tmp_path: Path,
) -> None:
    """Exercise implemented CFG04 mounts over a separate, installed ServiceHost."""

    assert sys.prefix != sys.base_prefix, "CFG04 must run from its isolated client venv"
    client_prefix = str(Path(sys.prefix).resolve())
    client_distributions = _distribution_versions()
    _assert_exact_wheel_versions(client_distributions, _EXPECTED_CLIENT_DISTRIBUTIONS)
    client_presence_before = _domain_presence()
    _assert_domain_absence(client_presence_before)
    client_origins = {
        module_name: _assert_installed_origin(module_name)
        for module_name in (
            "bt_api_py.service_account",
            "bt_api_py.service_execution",
            "bt_api_py.service_runtime",
            "bt_api_py.service_telemetry",
            "bt_api_base.account_protocol",
            "bt_api_base.service_protocol",
            "bt_api_transport_zmq.client",
        )
    }
    client_direct_url = _read_direct_url()
    assert not (client_direct_url or {}).get("dir_info", {}).get("editable", False)
    cold_import = _cfg04_cold_import_probe(tmp_path)

    rpc_endpoint = _free_endpoints()[0]
    full_schema_config = _cfg04_client_config(rpc_endpoint, include_group=True)
    parse_client_config(full_schema_config)
    configured_group_mounts = [
        binding["mount"]
        for block in full_schema_config["clients"].values()
        for binding in block["settings"]["bindings"]
        if binding["mount"] == "broker.order_groups"
    ]
    assert configured_group_mounts == ["broker.order_groups"]

    domain_components = _cfg04_client_config(rpc_endpoint)
    domain_components["components"] = {"gateway": {}}
    _cfg04_reject_client_config(domain_components, "UNKNOWN_FIELD")
    embedded = _cfg04_client_config(rpc_endpoint)
    embedded["placement"] = "embedded"
    _cfg04_reject_client_config(embedded, "UNKNOWN_FIELD")
    server_port = _cfg04_client_config(rpc_endpoint)
    server_port["clients"]["execution"]["settings"]["bindings"][0][
        "mount"
    ] = "service.order_entry"
    _cfg04_reject_client_config(server_port, "UNKNOWN_MOUNT")
    actual_config = parse_client_config(_cfg04_client_config(rpc_endpoint))

    server_python_raw = os.environ.get("BT_API_ITERATION10_CFG04_SERVER_PYTHON")
    assert server_python_raw, "Root must provide the separate installed-domain server venv"
    server_python = Path(server_python_raw).resolve()
    assert server_python.is_file()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    payload = {
        "service_config": _cfg04_service_config(rpc_endpoint),
        "registry_path": str(tmp_path / "cfg04-service-registry.sqlite3"),
        "account_journal_path": str(tmp_path / "cfg04-account-journal.sqlite3"),
        "account_guard_directory": str(tmp_path / "cfg04-account-guard"),
        "execution_store_path": str(tmp_path / "cfg04-execution-store.sqlite3"),
        "risk_store_path": str(tmp_path / "cfg04-risk-store.sqlite3"),
        "server_public": _encode_key(server_public),
        "server_secret": _encode_key(server_secret),
        "strategy_public": _encode_key(strategy_public),
    }
    source_file = Path(__file__).resolve()
    stderr_path = tmp_path / "cfg04-service-stderr.txt"
    control_output: queue.Queue[object] = queue.Queue()
    runtime: ServiceClientRuntime | None = None
    account_client: ServiceAccountClient | None = None
    telemetry: ServiceTelemetryEmitter | None = None
    forced_cleanup: list[str] = []
    server_closed_normally = False
    with stderr_path.open("w", encoding="utf-8", errors="backslashreplace") as stderr_file:
        process = subprocess.Popen(
            [str(server_python), str(source_file), "--cfg04-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
            bufsize=1,
            cwd=tmp_path,
        )
        assert process.stdin is not None and process.stdout is not None
        output_reader = threading.Thread(
            target=_stdout_json_reader,
            args=(process.stdout, control_output),
            name="cfg04-service-output-reader",
            daemon=True,
        )
        output_reader.start()
        try:
            process.stdin.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            process.stdin.flush()
            ready = _receive_server(process, control_output)
            assert ready.get("kind") == "ready", ready
            assert ready["pid"] != os.getpid()
            assert str(Path(ready["prefix"]).resolve()) != client_prefix
            assert str(Path(ready["prefix"]).resolve()) != str(Path(sys.base_prefix).resolve())
            assert not (ready["direct_url"] or {}).get("dir_info", {}).get("editable", False)
            _assert_exact_wheel_versions(
                ready["distributions"],
                {
                    **_EXPECTED_CLIENT_DISTRIBUTIONS,
                    "bt_api_gateway": "0.1.0",
                    "bt_api_execution": "0.2.0",
                    "bt_api_risk": "0.1.0",
                    "bt_api_monitor": "0.1.0",
                },
            )
            assert all(
                row["distribution_installed"]
                and row["import_spec_available"]
                and row["module_loaded"]
                for row in ready["domain_presence"].values()
            ), ready["domain_presence"]
            expected_server_origins = {
                "bt_api_py.service_host",
                "bt_api_py.service_execution_account_books",
                "bt_api_base.account_protocol",
                "bt_api_base.service_protocol",
                "bt_api_gateway.service_component",
                "bt_api_execution.service_component",
                "bt_api_risk.service_component",
                "bt_api_monitor.service_telemetry",
                "bt_api_transport_zmq.server",
            }
            assert set(ready["origins"]) == expected_server_origins
            assert all(
                Path(origin).is_relative_to(Path(ready["prefix"]).resolve() / "Lib" / "site-packages")
                for origin in ready["origins"].values()
            ), ready["origins"]

            summary_components = {
                row["component"]: row for row in ready["summary"]["components"]
            }
            assert ready["summary"]["state"] == "started"
            assert set(summary_components) == {"gateway", "execution", "risk", "monitor"}
            expected_component_ports = {
                "gateway": {"service.account_read", "service.provider_session"},
                "execution": {"service.order_entry", "service.order_query"},
                "risk": {"service.risk_query"},
                "monitor": {"service.telemetry_ingress"},
            }
            assert {
                name: set(row["declared_ports"])
                for name, row in summary_components.items()
            } == expected_component_ports
            capability_rows = {
                name: {
                    capability["port"]: set(capability["operations"])
                    for capability in row["capabilities"]
                }
                for name, row in summary_components.items()
            }
            assert capability_rows == {
                "gateway": {
                    "service.account_read": {
                        "account.ack",
                        "account.head",
                        "account.progress",
                        "account.read_after",
                        "account.snapshot",
                    }
                },
                "execution": {
                    "service.order_entry": {"orders.submit", "orders.cancel"},
                    "service.order_query": {
                        "orders.query",
                        "execution.account_books.query",
                    },
                },
                "risk": {"service.risk_query": {"risk.snapshot"}},
                "monitor": {"service.telemetry_ingress": {"telemetry.append"}},
            }

            strategy_identity = WireIdentity(
                WireRole.STRATEGY_CLIENT,
                _CFG04_STRATEGY_PRINCIPAL,
                _CFG04_STRATEGY_REF,
            )
            service_identity = WireIdentity(
                WireRole.SERVICE, _CFG04_SERVICE_PRINCIPAL, _CFG04_SERVICE_ID
            )
            client_keys = ClientCurveKeys(server_public, strategy_public, strategy_secret)
            runtime = ServiceClientRuntime(
                actual_config,
                identity=strategy_identity,
                scope_resolver=lambda _scope_ref: _CFG04_WIRE_SCOPE,
                peer_resolver=lambda _service_id: service_identity,
                transport_factory=ZmqTransportFactory({_CFG04_TRANSPORT_REF: client_keys}),
                run_id="iteration10-cfg04-run-v1",
                requested_operations={
                    "store.account": frozenset({"account.snapshot"}),
                    "broker.orders": frozenset({"orders.submit", "orders.cancel"}),
                    "broker.order_query": frozenset({"orders.query"}),
                    "risk.read_model": frozenset({"risk.snapshot"}),
                    "runtime.telemetry": frozenset({"telemetry.append"}),
                },
            )
            registration = runtime.start()
            assert registration.degraded_count == 0
            bindings_by_mount = {binding.mount: binding for binding in registration.bindings}
            assert set(bindings_by_mount) == _CFG04_CLIENT_MOUNTS
            assert bindings_by_mount["runtime.telemetry"].required is False
            assert bindings_by_mount["runtime.telemetry"].operations == frozenset(
                {"telemetry.append"}
            )

            account_client = ServiceAccountClient(
                runtime,
                "store.account",
                account_ref=_CFG04_ACCOUNT_REF,
                expected_account=_CFG04_ACCOUNT_KEY,
                required_coverage=(_CFG04_ACCOUNT_RESOURCE,),
                expected_scope_ref=_CFG04_ACCOUNT_SCOPE_REF,
            )
            account_view = account_client.refresh()
            assert account_view.observation.account_key == _CFG04_ACCOUNT_KEY
            assert account_view.observation.coverage == (_CFG04_ACCOUNT_RESOURCE,)
            assert account_view.observation.balances[0].total == "123.45"
            assert account_client.healthy

            orders = ServiceExecutionClient(
                runtime,
                "broker.orders",
                scope_ref=_CFG04_ORDER_SCOPE_REF,
                query_mount="broker.order_query",
                query_scope_ref=_CFG04_QUERY_SCOPE_REF,
            )
            submitted = orders.submit(
                intent_id="iteration10-cfg04-intent-1",
                signal_id="iteration10-cfg04-signal-1",
                instrument="SYNTH-USD",
                side="BUY",
                position_effect="OPEN",
                order_type="LIMIT",
                quantity=Decimal("1"),
                price=Decimal("10"),
                metadata_version="iteration10-cfg04-metadata-v1",
            )
            assert submitted.intent_id == "iteration10-cfg04-intent-1"
            assert submitted.state == "ACKED"
            assert submitted.provider_order_id == "cfg04-provider-order-1"
            queried = orders.query(submitted.intent_id)
            assert queried is not None and queried.state == "ACKED"
            cancelled = orders.cancel(
                cancel_id="iteration10-cfg04-cancel-1",
                target_intent_id=submitted.intent_id,
                provider_order_id=submitted.provider_order_id,
                metadata_version="iteration10-cfg04-metadata-v1",
            )
            assert cancelled.cancel_id == "iteration10-cfg04-cancel-1"
            assert cancelled.target_intent_id == submitted.intent_id

            risk_snapshot = runtime.call(
                "risk.read_model",
                "risk.snapshot",
                {},
                account_ref=_CFG04_ACCOUNT_REF,
                scope_ref=_CFG04_RISK_SCOPE_REF,
            )
            assert risk_snapshot["source"] == "local_risk_reservation_ledger"
            assert risk_snapshot["increase_count"] == 0
            assert risk_snapshot["active_freeze_reasons"] == ()

            telemetry = ServiceTelemetryEmitter.from_config(
                runtime,
                actual_config,
                scope_ref=_CFG04_TELEMETRY_SCOPE_REF,
            )
            assert telemetry.stats.state == "ready"
            assert telemetry.enqueue_heartbeat()
            telemetry_result = telemetry.flush(max_events=1)
            assert (telemetry_result.attempted, telemetry_result.delivered, telemetry_result.dropped) == (
                1,
                1,
                0,
            )
            assert telemetry.stats.delivered_total == 1
            assert telemetry.stats.state == "ready"

            client_presence_after = _domain_presence()
            _assert_domain_absence(client_presence_after)
            _send_server(process, {"action": "status"})
            live = _receive_server(process, control_output)
            assert live["kind"] == "status"
            assert live["account_connect_calls"] == 1
            assert live["account_read_calls"] >= 1
            assert live["submitted"] == [submitted.intent_id]
            assert live["cancelled"] == [cancelled.cancel_id]
            assert set(live["admission_reserved"]) == {
                submitted.intent_id,
                cancelled.cancel_id,
            }

            account_client.close()
            account_client = None
            telemetry.close()
            telemetry = None
            runtime.close()
            assert runtime.state == "closed"
            assert runtime.cleanup_failures == 0
            assert runtime.unregister_uncertainties == 0

            _send_server(process, {"action": "close"})
            process.stdin.close()
            closed = _receive_server(process, control_output)
            assert closed["kind"] == "closed"
            assert closed["control_reader_stopped"] is True
            assert closed["summary"]["state"] == "closed"
            assert closed["account_close_calls"] == 1
            assert set(closed["resource_closes"]) == {"gateway", "execution", "risk"}
            process.wait(timeout=15)
            assert process.returncode == 0
            server_closed_normally = True
        finally:
            if account_client is not None:
                with suppress(Exception):
                    account_client.close()
            if telemetry is not None:
                with suppress(Exception):
                    telemetry.close()
            if runtime is not None and runtime.state != "closed":
                with suppress(Exception):
                    runtime.close()
            if process.poll() is None:
                try:
                    _send_server(process, {"action": "close"})
                    if process.stdin is not None and not process.stdin.closed:
                        process.stdin.close()
                    cleanup_reply = _receive_server(process, control_output, timeout=8.0)
                    if cleanup_reply.get("kind") == "closed":
                        process.wait(timeout=8)
                        server_closed_normally = process.returncode == 0
                except BaseException as error:
                    forced_cleanup.append(f"cleanup_exception:{type(error).__name__}")
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    forced_cleanup.append("server_terminate")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        forced_cleanup.append("server_kill")
                        process.kill()
                        process.wait(timeout=5)
            output_reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()

    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    assert server_closed_normally
    assert forced_cleanup == [], forced_cleanup
    assert process.returncode == 0
    assert not output_reader.is_alive(), "CFG04 child output reader remained after exit"
    assert client_presence_after == client_presence_before
    report = {
        "status": "PASS_CFG04_FIVE_IMPLEMENTED_STRATEGY_MOUNTS_PARTIAL",
        "client_mounted_operations": sorted(_CFG04_CLIENT_MOUNTS),
        "pending_strategy_mount": "broker.order_groups",
        "group_mount_configuration_only": configured_group_mounts,
        "config_rejections": {
            "strategy_components": "UNKNOWN_FIELD",
            "embedded_placement": "UNKNOWN_FIELD",
            "server_service_port": "UNKNOWN_MOUNT",
        },
        "client_python": sys.executable,
        "client_prefix": client_prefix,
        "client_distributions": client_distributions,
        "client_domain_presence_before": client_presence_before,
        "client_domain_presence_after": client_presence_after,
        "cold_import_probe": cold_import,
        "client_origins": client_origins,
        "server_python": ready["python"],
        "server_prefix": ready["prefix"],
        "server_pid": ready["pid"],
        "server_distributions": ready["distributions"],
        "server_domain_presence": ready["domain_presence"],
        "server_origins": ready["origins"],
        "startup_components": ready["summary"]["components"],
        "account_snapshot_id": account_view.snapshot_id,
        "account_cursor": account_view.cursor,
        "execution_submit_state": submitted.state,
        "execution_cancel_state": cancelled.state,
        "risk_snapshot": dict(risk_snapshot),
        "telemetry_delivered": telemetry_result.delivered,
        "normal_runtime_close": runtime.state == "closed",
        "normal_host_close": server_closed_normally,
        "server_control_reader_stopped": closed["control_reader_stopped"],
        "server_output_reader_stopped": not output_reader.is_alive(),
        "forced_cleanup": forced_cleanup,
        "server_stderr_bytes": len(stderr_text.encode("utf-8")),
        "server_stderr_classification": (
            "not captured after close" if not stderr_text else "retained separately in test tempdir"
        ),
    }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__" and sys.argv[1:] == ["--cfg04-server"]:
    raise SystemExit(_run_cfg04_server())
