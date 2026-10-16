"""Offline V2 analytics over separate Risk-host and SDK-client processes."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import pytest
import zmq

_SERVICE_ID = "afi04-metrics-service"
_INSTANCE_ID = "afi04-risk"
_PRINCIPAL = "afi04-manager"
_DOMAIN = "afi04:metrics"
_PORTFOLIO = "portfolio-v2"
_SCOPE_REF = "afi04-scope-v1"
_SECURITY_REF = "afi04-server-v1"
_MANAGER_GRANT = "afi04-manager-v1"
_SERVICE_REQUIREMENTS = "afi04-requirements-v1"
_REGISTRATION_POLICY = "afi04-registration-v1"
_OPERATIONS = frozenset(
    {"analysis.input_upload", "analysis.submit", "analysis.query", "analysis.cancel"}
)


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _service_config(endpoint: str) -> dict[str, object]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SCOPE_REF,
        "transport_ref": "analysis-listener",
        "registration_policy_ref": _REGISTRATION_POLICY,
        "service_requirements_ref": _SERVICE_REQUIREMENTS,
        "components": {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE_ID,
                    "bind_ports": ["service.risk_analysis"],
                    "options": {
                        "role": "portfolio_analytics",
                        "model_profile_ref": "afi04-metrics-profile-v1",
                        "allowed_source_modes": ["research", "imported", "paper"],
                        "max_concurrent_tasks": 2,
                        "max_input_bytes": 1_048_576,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "analysis-listener": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {"high_water_mark": 32, "max_message_bytes": 65_536},
                }
            },
        },
    }


def _client_config(endpoint: str) -> dict[str, object]:
    return {
        "client_config_schema_version": 1,
        "process_role": "application_client",
        "registration": {"application_ref": _PRINCIPAL},
        "clients": {
            "risk": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "application.risk_analysis",
                            "service_id": _SERVICE_ID,
                            "instance_id": _INSTANCE_ID,
                            "portfolio_ref": _PORTFOLIO,
                            "scope_ref": _MANAGER_GRANT,
                            "transport_ref": "analysis-connect",
                        }
                    ],
                    "options": {"request_timeout_ms": 3_000},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "analysis-connect": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {"high_water_mark": 32, "max_message_bytes": 65_536},
                }
            },
        },
    }


def _wire_keys() -> dict[str, str]:
    names = ("server", "client")
    values = {}
    for name in names:
        public, secret = zmq.curve_keypair()
        values[f"{name}_public"] = public.decode("ascii")
        values[f"{name}_secret"] = secret.decode("ascii")
    return values


def _v2_facts() -> dict[str, object]:
    cut = "2026-01-01T00:00:00+00:00"
    return {
        "facts_snapshot_ref": "offline-snapshot-v2",
        "loss_history": {
            "coverage_complete": True,
            "coverage_end_utc": "2026-01-05T00:00:00+00:00",
            "coverage_start_utc": cut,
            "end_cursor": "loss-end-4",
            "horizon_ref": "one-day-v1",
            "observations": [
                {
                    "as_of_utc": f"2026-01-0{day}T00:00:00+00:00",
                    "currency": "USD",
                    "loss": str(day),
                    "loss_unit": "REPORTING_CURRENCY_AMOUNT",
                }
                for day in range(1, 6)
            ],
            "reason_code": None,
            "source_generation": "loss-generation-v1",
            "source_ref": "synthetic-loss-history",
            "source_version": "loss-history-v1",
            "start_cursor": "loss-start-0",
            "status": "available",
        },
        "nav_history": {
            "coverage_complete": True,
            "coverage_end_utc": "2026-01-04T00:00:00+00:00",
            "coverage_start_utc": cut,
            "end_cursor": "nav-end-3",
            "observations": [
                {
                    "as_of_utc": f"2026-01-0{day}T00:00:00+00:00",
                    "currency": "USD",
                    "nav": value,
                }
                for day, value in enumerate(("100", "120", "90", "110"), start=1)
            ],
            "reason_code": None,
            "source_generation": "nav-generation-v1",
            "source_ref": "synthetic-nav-history",
            "source_version": "nav-history-v1",
            "start_cursor": "nav-start-0",
            "status": "available",
        },
        "portfolio_ref": _PORTFOLIO,
        "scenario": {
            "coverage_complete": True,
            "reason_code": None,
            "scenario_ref": "risk-off",
            "scenario_version": "risk-off-v1",
            "shocks": [
                {
                    "fx_return": "0",
                    "position_ref": "btc-long",
                    "price_return": "0.1",
                    "shock_unit": "FRACTIONAL_RETURN",
                },
                {
                    "fx_return": "0",
                    "position_ref": "btc-short",
                    "price_return": "0.1",
                    "shock_unit": "FRACTIONAL_RETURN",
                },
                {
                    "fx_return": "-0.1",
                    "position_ref": "eth-long",
                    "price_return": "0.2",
                    "shock_unit": "FRACTIONAL_RETURN",
                },
            ],
            "source_cursor": "scenario-cursor-1",
            "source_generation": "scenario-generation-1",
            "source_ref": "synthetic-scenarios",
            "source_version": "scenario-catalog-v1",
            "status": "available",
        },
        "source_generation": "offline-generation-v2",
        "source_mode": "imported",
        "source_ref": "synthetic-offline-source",
        "source_version": "typed-facts-v2",
        "spot": {
            "as_of_utc": cut,
            "coverage_complete": True,
            "positions": [
                {
                    "as_of_utc": cut,
                    "base_currency": "BTC",
                    "fx_unit": "REPORTING_PER_QUOTE",
                    "position_ref": "btc-long",
                    "quantity": "2",
                    "quantity_unit": "BTC",
                    "quote_currency": "USD",
                    "reporting_per_quote": "1",
                    "price_unit": "QUOTE_PER_BASE",
                    "unit_price": "100",
                },
                {
                    "as_of_utc": cut,
                    "base_currency": "BTC",
                    "fx_unit": "REPORTING_PER_QUOTE",
                    "position_ref": "btc-short",
                    "quantity": "-0.5",
                    "quantity_unit": "BTC",
                    "quote_currency": "USD",
                    "reporting_per_quote": "1",
                    "price_unit": "QUOTE_PER_BASE",
                    "unit_price": "100",
                },
                {
                    "as_of_utc": cut,
                    "base_currency": "ETH",
                    "fx_unit": "REPORTING_PER_QUOTE",
                    "position_ref": "eth-long",
                    "quantity": "3",
                    "quantity_unit": "ETH",
                    "quote_currency": "EUR",
                    "reporting_per_quote": "1.1",
                    "price_unit": "QUOTE_PER_BASE",
                    "unit_price": "20",
                },
            ],
            "reason_code": None,
            "source_cursor": "spot-cursor-9",
            "source_generation": "spot-generation-9",
            "source_ref": "synthetic-spot-cut",
            "source_version": "spot-facts-v1",
            "status": "available",
        },
        "version": 2,
    }


def _v2_request() -> dict[str, object]:
    return {
        "confidence": "0.8",
        "loss_horizon_ref": "one-day-v1",
        "portfolio_ref": _PORTFOLIO,
        "reporting_currency": "USD",
        "scenario_ref": "risk-off",
        "scenario_version": "risk-off-v1",
        "version": 2,
    }


def _host_worker(config_path: Path) -> int:
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
    from bt_api_py.service_components import ComponentResources
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
    from bt_api_risk.portfolio_analytics import PortfolioAnalyticsModelProfileV1
    from bt_api_risk.service_analytics import AnalysisTaskStore

    config = json.loads(config_path.read_text(encoding="utf-8"))
    keys = {key: value.encode("ascii") for key, value in config["keys"].items()}
    endpoint = config["endpoint"]
    database = Path(config["database"])
    profile = PortfolioAnalyticsModelProfileV1(
        "afi04-metrics-profile-v1",
        max_mark_age_ns=86_400_000_000_000,
        max_fx_age_ns=86_400_000_000_000,
        max_input_bytes=1_048_576,
    )
    stores: list[AnalysisTaskStore] = []

    def resources(component, instance_id, options, _scope):
        if (
            component != "risk"
            or instance_id != _INSTANCE_ID
            or options.get("role") != "portfolio_analytics"
        ):
            raise RuntimeError("unexpected_service_component")
        store = AnalysisTaskStore(
            str(database),
            model_profile=profile,
            allowed_source_modes=options["allowed_source_modes"],
            max_concurrent_tasks=options.get("max_concurrent_tasks", 2),
            pending_ttl_seconds=60,
        )
        stores.append(store)
        return ComponentResources.borrowed({"analysis": store}, ready=lambda: True)

    service_identity = WireIdentity(WireRole.SERVICE, f"{_SERVICE_ID}-principal", _SERVICE_ID)
    manager_identity = WireIdentity(WireRole.APPLICATION_CLIENT, _PRINCIPAL, _PRINCIPAL)
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=WireScope(_DOMAIN),
        scope_ref=_SCOPE_REF,
        scope_version="afi04-scope-v1",
        security_ref=_SECURITY_REF,
        security_version="afi04-curve-v1",
        registration_policy_ref=_REGISTRATION_POLICY,
        registration_policy_version="afi04-registration-v1",
        service_requirements_ref=_SERVICE_REQUIREMENTS,
        service_requirements_version="afi04-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals={keys["client_public"]: _PRINCIPAL},
        grants={
            _PRINCIPAL: (
                PrincipalGrant(
                    manager_identity,
                    (
                        ResourceGrant(
                            _INSTANCE_ID,
                            "service.risk_analysis",
                            _MANAGER_GRANT,
                            WireScope(_DOMAIN, portfolio_ref=_PORTFOLIO),
                            _OPERATIONS,
                        ),
                    ),
                ),
            )
        },
    )
    host = None
    try:
        host = ServiceHost(
            parse_service_config(_service_config(endpoint)),
            authority,
            str(config["registry"]),
            resources,
        )
        host.start()
        Path(config["ready"]).write_text("ready", encoding="ascii")
        stop_path = Path(config["stop"])
        while not stop_path.exists():
            host.serve_once(25)
        host.close()
        Path(config["closed"]).write_text("closed", encoding="ascii")
        return 0
    except BaseException as exc:
        Path(config["failure"]).write_text(type(exc).__name__, encoding="ascii")
        return 1
    finally:
        if host is not None and host.state != "closed":
            try:
                host.close()
            except BaseException:
                pass
        for store in stores:
            try:
                store.close()
            except BaseException:
                pass


def _client_worker(config_path: Path) -> int:
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory

    config = json.loads(config_path.read_text(encoding="utf-8"))
    keys = {key: value.encode("ascii") for key, value in config["keys"].items()}
    scope = WireScope(_DOMAIN, portfolio_ref=_PORTFOLIO)
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, _PRINCIPAL, _PRINCIPAL)
    runtime = ServiceClientRuntime(
        parse_client_config(_client_config(config["endpoint"])),
        identity=identity,
        scope_resolver=lambda ref: {"afi04-manager-v1": scope}[ref],
        peer_resolver=lambda _service_id: WireIdentity(
            WireRole.SERVICE, f"{_SERVICE_ID}-principal", _SERVICE_ID
        ),
        transport_factory=ZmqTransportFactory(
            {
                _SECURITY_REF: ClientCurveKeys(
                    keys["server_public"], keys["client_public"], keys["client_secret"]
                )
            }
        ),
    )
    results: dict[str, object] = {}
    try:
        runtime.start()
        uploaded = runtime.call(
            "application.risk_analysis",
            "analysis.input_upload",
            {"input_snapshot_id": "cut-v2-1", "facts": _v2_facts()},
            portfolio_ref=_PORTFOLIO,
        )
        results["upload"] = _plain(uploaded)
        submitted = runtime.call(
            "application.risk_analysis",
            "analysis.submit",
            {
                "input_snapshot_id": "cut-v2-1",
                "request": _v2_request(),
                "task_ref": "metrics-task-v2-1",
            },
            portfolio_ref=_PORTFOLIO,
        )
        results["submit"] = _plain(submitted)
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            queried = runtime.call(
                "application.risk_analysis",
                "analysis.query",
                {"task_ref": "metrics-task-v2-1"},
                portfolio_ref=_PORTFOLIO,
            )
            results["query"] = _plain(queried)
            state = queried.get("result", {}).get("state")
            if state in {"succeeded", "unavailable", "failed", "cancelled", "expired"}:
                break
            time.sleep(1.05)
        else:
            raise TimeoutError("analysis task did not reach a terminal state")

        partial_facts = _v2_facts()
        partial_facts["nav_history"] = {
            "coverage_complete": None,
            "coverage_end_utc": None,
            "coverage_start_utc": None,
            "end_cursor": None,
            "observations": [],
            "reason_code": "SOURCE_STALE",
            "source_generation": None,
            "source_ref": None,
            "source_version": None,
            "start_cursor": None,
            "status": "unavailable",
        }
        partial_uploaded = runtime.call(
            "application.risk_analysis",
            "analysis.input_upload",
            {"input_snapshot_id": "cut-v2-partial", "facts": partial_facts},
            portfolio_ref=_PORTFOLIO,
        )
        results["partial_upload"] = _plain(partial_uploaded)
        partial_submitted = runtime.call(
            "application.risk_analysis",
            "analysis.submit",
            {
                "input_snapshot_id": "cut-v2-partial",
                "request": _v2_request(),
                "task_ref": "metrics-task-v2-partial",
            },
            portfolio_ref=_PORTFOLIO,
        )
        results["partial_submit"] = _plain(partial_submitted)
        partial_deadline = time.monotonic() + 30.0
        while time.monotonic() < partial_deadline:
            partial_query = runtime.call(
                "application.risk_analysis",
                "analysis.query",
                {"task_ref": "metrics-task-v2-partial"},
                portfolio_ref=_PORTFOLIO,
            )
            results["partial_query"] = _plain(partial_query)
            partial_state = partial_query.get("result", {}).get("state")
            if partial_state in {
                "succeeded",
                "unavailable",
                "failed",
                "cancelled",
                "expired",
            }:
                break
            time.sleep(1.05)
        else:
            raise TimeoutError("partial analysis task did not reach a terminal state")
        Path(config["result"]).write_text(json.dumps(results), encoding="utf-8")
        return 0
    except BaseException as exc:
        results["error_type"] = type(exc).__name__
        Path(config["result"]).write_text(json.dumps(results), encoding="utf-8")
        return 1
    finally:
        runtime.close()


def _child_entry() -> int:
    if len(sys.argv) != 3:
        return 2
    if sys.argv[1] == "--risk-host":
        return _host_worker(Path(sys.argv[2]))
    if sys.argv[1] == "--sdk-client":
        return _client_worker(Path(sys.argv[2]))
    return 2


@pytest.mark.integration
def test_v2_metrics_cross_independent_risk_host_and_sdk_client_processes(tmp_path) -> None:
    endpoint = _endpoint()
    keys = _wire_keys()
    paths = {
        name: tmp_path / f"{name}.json"
        for name in ("host-config", "client-config", "result")
    }
    markers = {
        name: tmp_path / f"{name}.marker"
        for name in ("ready", "stop", "closed", "failure")
    }
    host_config = {
        "closed": str(markers["closed"]),
        "database": str(tmp_path / "analysis.sqlite"),
        "endpoint": endpoint,
        "failure": str(markers["failure"]),
        "keys": keys,
        "ready": str(markers["ready"]),
        "registry": str(tmp_path / "registry.sqlite"),
        "stop": str(markers["stop"]),
    }
    client_config = {
        "endpoint": endpoint,
        "keys": keys,
        "result": str(paths["result"]),
    }
    paths["host-config"].write_text(json.dumps(host_config), encoding="utf-8")
    paths["client-config"].write_text(json.dumps(client_config), encoding="utf-8")
    environment = os.environ.copy()
    repository_root = Path(__file__).resolve().parents[2]
    inherited_pythonpath = environment.get("PYTHONPATH")
    local_pythonpath = (
        repository_root,
        repository_root / "bt_api" / "bt_api_base" / "src",
        repository_root / "bt_api" / "bt_api_risk" / "src",
    )
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(path) for path in local_pythonpath]
        + ([inherited_pythonpath] if inherited_pythonpath else [])
    )
    host_log = tmp_path / "risk-host.log"
    with host_log.open("w", encoding="utf-8") as host_log_stream:
        host_process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--risk-host",
                str(paths["host-config"]),
            ],
            cwd=repository_root,
            env=environment,
            stdout=host_log_stream,
            stderr=subprocess.STDOUT,
        )
    try:
        ready_deadline = time.monotonic() + 20.0
        while time.monotonic() < ready_deadline and not markers["ready"].exists():
            if host_process.poll() is not None:
                break
            time.sleep(0.05)
        assert markers["ready"].exists(), (
            markers["failure"].read_text(encoding="ascii")
            if markers["failure"].exists()
            else host_log.read_text(encoding="utf-8", errors="replace")
        )

        client_result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--sdk-client",
                str(paths["client-config"]),
            ],
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=40.0,
            check=False,
        )
        assert client_result.returncode == 0, client_result.stderr
        assert paths["result"].exists()
        result = json.loads(paths["result"].read_text(encoding="utf-8"))
        upload = result["upload"]
        submit = result["submit"]
        query = result["query"]
        assert upload["ok"] is True and upload["code"] == "OK"
        assert upload["result"]["source_mode"] == "imported"
        assert len(upload["result"]["input_sha256"]) == 64
        assert submit["ok"] is True and submit["code"] == "OK"
        assert query["ok"] is True and query["code"] == "OK"
        assert query["result"]["state"] == "succeeded"
        report = query["result"]["result"]
        assert report["version"] == 2
        assert report["schema_ref"] == "portfolio_analytics_v2"
        assert report["calculation_ref"] == "portfolio_metrics_v1_bundle_v2"
        assert report["serialization_ref"] == "json_sort_keys_compact_ascii_v1"
        assert report["decimal_serialization_ref"] == "finite_decimal_fixed_point_canonical_v1"
        assert report["input_sha256"] == upload["result"]["input_sha256"]
        assert report["status"] == "AVAILABLE"
        metrics = report["metrics"]
        assert metrics["spot_exposure"]["value"]["net_reporting_notional"] == "216"
        assert metrics["spot_exposure"]["value"]["gross_reporting_notional"] == "316"
        assert metrics["max_drawdown"]["value"]["max_drawdown_ratio"] == "-0.25"
        assert metrics["historical_var_es"]["value"]["var_loss"] == "4"
        assert metrics["historical_var_es"]["value"]["expected_shortfall_loss"] == "5"
        assert metrics["stress_scenario"]["value"]["pnl_reporting_amount"] == "20.28"
        assert report["provenance"]["spot"]["source_cursor"] == "spot-cursor-9"
        assert report["provenance"]["spot"]["as_of_utc"] == "2026-01-01T00:00:00+00:00"
        assert (
            report["provenance"]["nav_history"]["coverage_end_utc"]
            == "2026-01-04T00:00:00+00:00"
        )
        assert (
            report["provenance"]["loss_history"]["coverage_end_utc"]
            == "2026-01-05T00:00:00+00:00"
        )
        report_digest = report.pop("report_sha256")
        canonical_report = json.dumps(
            report,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        assert hashlib.sha256(canonical_report).hexdigest() == report_digest
        partial_upload = result["partial_upload"]
        partial_submit = result["partial_submit"]
        partial_query = result["partial_query"]
        assert partial_upload["ok"] is True and partial_upload["code"] == "OK"
        assert partial_submit["ok"] is True and partial_submit["code"] == "OK"
        assert partial_query["ok"] is True and partial_query["code"] == "OK"
        assert partial_query["result"]["state"] == "succeeded"
        partial_report = partial_query["result"]["result"]
        partial_metrics = partial_report["metrics"]
        assert partial_report["status"] == "AVAILABLE"
        assert partial_metrics["max_drawdown"] == {
            "reason_code": "SOURCE_STALE",
            "status": "unavailable",
            "value": None,
        }
        assert partial_metrics["spot_exposure"]["status"] == "available"
        assert partial_metrics["historical_var_es"]["status"] == "available"
        assert partial_metrics["stress_scenario"]["status"] == "available"
    finally:
        markers["stop"].touch()
        try:
            host_process.wait(timeout=20.0)
        except subprocess.TimeoutExpired:
            host_process.terminate()
            host_process.wait(timeout=5.0)
            raise AssertionError("Risk ServiceHost child failed to close normally")
        assert host_process.returncode == 0
        assert markers["closed"].exists()


if __name__ == "__main__":
    raise SystemExit(_child_entry())
