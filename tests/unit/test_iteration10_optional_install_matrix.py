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
from pathlib import Path
from typing import Any

import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_config import parse_client_config
from bt_api_py.service_market import ServiceMarketClient, ZmqMarketTransportFactory
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)

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
