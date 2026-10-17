from __future__ import annotations

import importlib
import io
import json
import multiprocessing.process
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from bt_api_py import config_migration
from bt_api_py.service_config import parse_client_config, parse_service_config


def _request() -> dict:
    return config_migration._example_request()


def test_forwarding_conversion_fills_explicit_identity_and_validates_full_target_graph() -> None:
    request = _request()
    template = request["target_client_template"]
    binding = template["clients"]["gateway"]["settings"]["bindings"][0]
    assert "strategy_ref" not in template.get("registration", {})
    assert "account_ref" not in binding

    converted = config_migration.convert_forwarding_config(request)
    client = parse_client_config(converted["client_config"])
    service = parse_service_config(converted["service_configs"][0])

    assert client.registration["strategy_ref"] == "strategy-s01"
    assert client.clients["gateway"]["settings"]["bindings"][0]["account_ref"] == "account-a01"
    assert service.service_id == "account-a01-service"
    assert converted["deployment_plan"]["service_order"] == ["account-a01-service"]
    assert converted["status"] == "DRAFT_REQUIRES_OPERATOR_REVIEW"
    assert converted["deployment_plan"]["services_activated"] is False
    assert converted["migration_review"]["authorization_granted"] is False
    assert converted["migration_review"]["ready_to_start"] is False
    assert converted["migration_review"]["legacy_endpoints_copied"] is False
    assert converted["migration_review"]["legacy_recovery_state_transferred"] is False


def test_explicit_target_endpoints_are_used_and_legacy_endpoints_are_not_copied() -> None:
    request = _request()
    request["legacy_forwarding_config"]["command_endpoint"] = "ipc://old-command"
    request["legacy_forwarding_config"]["market_endpoint"] = "ipc://old-market"
    converted = config_migration.convert_forwarding_config(request)

    client_profile = converted["client_config"]["communication"]["profiles"]["account-a01-client"]
    service_profile = converted["service_configs"][0]["communication"]["profiles"][
        "account-a01-listener"
    ]
    assert client_profile["endpoints"] == {"rpc_connect": "tcp://127.0.0.1:7601"}
    assert service_profile["endpoints"] == {"rpc_bind": "tcp://127.0.0.1:7601"}
    assert "ipc://old-command" not in str(converted)
    assert "ipc://old-market" not in str(converted)


def test_private_channel_rejection_is_finite_and_does_not_echo_legacy_value() -> None:
    request = _request()
    request["legacy_forwarding_config"]["private_endpoint"] = "tcp://private-secret-host:7403"

    with pytest.raises(config_migration.OfflineMigrationError) as caught:
        config_migration.convert_forwarding_config(request)

    assert caught.value.code == "UNSUPPORTED_PRIVATE_CHANNEL"
    assert (
        str(caught.value)
        == "UNSUPPORTED_PRIVATE_CHANNEL at $.legacy_forwarding_config.private_endpoint"
    )
    assert "private-secret-host" not in str(caught.value)


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda request: request["identity_mapping"].__setitem__(
                "legacy_account_id", "different-account"
            ),
            "IDENTITY_MAPPING_MISMATCH",
        ),
        (
            lambda request: request["target_client_template"].__setitem__(
                "registration", {"strategy_ref": "other-strategy"}
            ),
            "IDENTITY_MAPPING_MISMATCH",
        ),
        (
            lambda request: request.__setitem__("target_scope_refs", ["other-scope"]),
            "UNMAPPED_TARGET_SCOPE",
        ),
        (
            lambda request: request["target_scope_refs"].append("unused-scope"),
            "UNMAPPED_TARGET_SCOPE",
        ),
        (
            lambda request: request.__setitem__("target_service_configs", []),
            "MISSING_TARGET_SERVICES",
        ),
        (lambda request: request.pop("recovery_plan_ref"), "INVALID_REQUEST"),
        (
            lambda request: request.__setitem__("recovery_plan_ref", "latest"),
            "INVALID_RECOVERY_PLAN_REF",
        ),
        (
            lambda request: request["legacy_forwarding_config"].__setitem__(
                "recovery_policy", "must-not-be-ignored"
            ),
            "UNSUPPORTED_LEGACY_FIELD",
        ),
    ],
)
def test_unsafe_or_incomplete_conversion_fails_closed(mutate, expected_code: str) -> None:
    request = _request()
    mutate(request)

    with pytest.raises(config_migration.OfflineMigrationError) as caught:
        config_migration.convert_forwarding_config(request)

    assert caught.value.code == expected_code
    assert "must-not-be-ignored" not in str(caught.value)


def test_nondefault_legacy_cache_semantics_are_not_silently_dropped() -> None:
    request = _request()
    request["legacy_forwarding_config"]["max_cache_age_ms"] = 10_000

    with pytest.raises(config_migration.OfflineMigrationError) as caught:
        config_migration.convert_forwarding_config(request)

    assert caught.value.code == "UNMAPPED_LEGACY_SETTING"
    assert caught.value.path == "$.legacy_forwarding_config.max_cache_age_ms"


def test_recovery_plan_reference_is_required_and_never_executed() -> None:
    converted = config_migration.convert_forwarding_config(_request())

    assert converted["migration_review"]["operator_recovery_plan_ref"] == (
        "account-a01-manual-reconcile-v1"
    )
    assert converted["migration_review"]["recovery_plan_resolved_or_executed"] is False
    assert converted["migration_review"]["legacy_recovery_state_transferred"] is False


def test_target_secret_field_is_rejected_without_echoing_value() -> None:
    request = _request()
    request["target_client_template"]["api_secret"] = "synthetic-secret-value"

    with pytest.raises(config_migration.OfflineMigrationError) as caught:
        config_migration.convert_forwarding_config(request)

    assert caught.value.code == "UNKNOWN_FIELD"
    assert "synthetic-secret-value" not in str(caught.value)


def test_module_import_and_conversion_do_not_open_network_or_start_runtime(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("offline converter attempted a side effect")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "bind", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(threading.Thread, "start", forbidden)
    monkeypatch.setattr(multiprocessing.process.BaseProcess, "start", forbidden)

    modules_before_import = set(sys.modules)
    reloaded = importlib.reload(config_migration)
    new_modules = set(sys.modules).difference(modules_before_import)
    assert not any(
        name.startswith("bt_api_py.")
        and any(
            marker in name
            for marker in (
                "bt_api",
                "gateway_operator",
                "service_runtime",
                "market_provider",
                "account_provider",
            )
        )
        for name in new_modules
    )
    result = reloaded.convert_forwarding_config(reloaded._example_request())

    assert result["deployment_plan"]["services_activated"] is False
    assert result["migration_review"]["ready_to_start"] is False


def test_fresh_light_mode_import_has_no_network_or_runtime_startup() -> None:
    script = """
import asyncio
import json
import multiprocessing.process
import socket
import subprocess
import threading

def forbidden(*_args, **_kwargs):
    raise AssertionError("offline converter attempted a side effect")

socket.socket.connect = forbidden
socket.socket.bind = forbidden
subprocess.Popen = forbidden
threading.Thread.start = forbidden
multiprocessing.process.BaseProcess.start = forbidden

import bt_api_py.config_migration as migration

result = migration.convert_forwarding_config(migration._example_request())
markers = ("bt_api_py.bt_api", "bt_api_py.service_runtime", "bt_api_py.gateway_operator", "market_provider", "account_provider")
loaded = sorted(name for name in __import__("sys").modules if any(marker in name for marker in markers))
print(json.dumps({"ready_to_start": result["migration_review"]["ready_to_start"], "loaded_runtime_modules": loaded}))
"""
    repository_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["BT_API_PY_LIGHT_IMPORT"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    assert observed == {"ready_to_start": False, "loaded_runtime_modules": []}


def test_public_example_cli_shows_conversion_and_private_channel_rejection(
    capsys,
) -> None:
    assert config_migration.main(["--example"]) == 0
    success = capsys.readouterr()
    assert success.err == ""
    rendered = json.loads(success.out)
    template = rendered["request"]["target_client_template"]
    binding = template["clients"]["gateway"]["settings"]["bindings"][0]
    assert "strategy_ref" not in template.get("registration", {})
    assert "account_ref" not in binding
    assert rendered["result"]["client_config"]["registration"]["strategy_ref"] == "strategy-s01"
    assert (
        rendered["result"]["client_config"]["clients"]["gateway"]["settings"]["bindings"][0][
            "account_ref"
        ]
        == "account-a01"
    )

    assert config_migration.main(["--example-reject-private"]) == 2
    rejected = capsys.readouterr()
    assert rejected.out == ""
    assert (
        "UNSUPPORTED_PRIVATE_CHANNEL at $.legacy_forwarding_config.private_endpoint" in rejected.err
    )


def test_stdin_cli_converts_operator_request_without_starting_anything(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_request())))

    assert config_migration.main([]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "DRAFT_REQUIRES_OPERATOR_REVIEW"
    assert result["client_config"]["registration"]["strategy_ref"] == "strategy-s01"
    assert result["migration_review"]["ready_to_start"] is False
