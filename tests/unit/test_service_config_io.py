"""Exercise real YAML files and the public offline config command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bt_api_py.service_config import ServiceConfigError
from bt_api_py.service_config_io import (
    MAX_CONFIG_BYTES,
    load_client_config,
    load_service_config,
    main,
)


@pytest.mark.parametrize(
    ("filename", "role"),
    [
        ("unconfigured-client.yaml", "client"),
        ("strategy-readonly.yaml", "client"),
        ("portfolio-analysis-client.yaml", "client"),
        ("account-service.yaml", "service"),
        ("portfolio-analysis-service.yaml", "service"),
    ],
)
def test_shipped_config_examples_validate_through_public_loader(filename, role):
    path = Path(__file__).resolve().parents[2] / "examples" / "services" / filename
    loader = load_client_config if role == "client" else load_service_config
    config = loader(path)
    assert len(config.fingerprint()) == 64
    assert config.to_public_dict().get("process_role") != "embedded"


def test_empty_clients_file_has_no_activation(tmp_path, capsys):
    path = tmp_path / "client.yaml"
    path.write_text("{}\n", encoding="utf-8-sig")
    assert load_client_config(path).to_public_dict().get("clients", {}) == {}
    assert main(["--role", "client", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "valid"
    assert result["activated"] is False
    assert result["component_count"] == result["client_count"] == 0


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("clients: {}\nclients: {}", "CONFIG_DUPLICATE_KEY"),
        ("clients: &x [*x]", "CONFIG_ALIAS_FORBIDDEN"),
        ("x: &x {}\ny: {<<: *x}", "CONFIG_ALIAS_FORBIDDEN"),
        ("{true: 1}", "CONFIG_KEY_INVALID"),
        ("!python/object/apply:os.system ['secret']", "CONFIG_YAML_INVALID"),
        ("{}\n---\n{}", "CONFIG_YAML_INVALID"),
        ("[]", "CONFIG_ROOT_INVALID"),
        ("[" * 34 + "]" * 34, "CONFIG_TOO_DEEP"),
    ],
)
def test_unsafe_or_ambiguous_yaml_is_rejected(tmp_path, content, code):
    path = tmp_path / "input.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ServiceConfigError) as exc:
        load_client_config(path)
    assert exc.value.code == code


def test_bounded_read_and_encoding_errors(tmp_path):
    path = tmp_path / "input.yaml"
    path.write_bytes(b" " * (MAX_CONFIG_BYTES + 1))
    with pytest.raises(ServiceConfigError) as exc:
        load_client_config(path)
    assert exc.value.code == "CONFIG_TOO_LARGE"
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ServiceConfigError) as exc:
        load_client_config(path)
    assert exc.value.code == "CONFIG_ENCODING_INVALID"


def test_cli_errors_do_not_echo_yaml_or_filename(tmp_path, capsys):
    path = tmp_path / "private-secret-reference.yaml"
    path.write_text("clients: [malformed-secret-input", encoding="utf-8")
    assert main(["--role", "client", str(path)]) == 2
    output = capsys.readouterr().out
    assert "secret" not in output
    assert json.loads(output) == {
        "status": "invalid",
        "code": "CONFIG_YAML_INVALID",
        "path": "$",
    }


def test_module_cli_is_a_real_process_with_no_service_activation(tmp_path):
    path = tmp_path / "client.yaml"
    path.write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env.pop("BT_API_PY_LIGHT_IMPORT", None)
    result = subprocess.run(
        [sys.executable, "-m", "bt_api_py.service_config_io", "--role", "client", str(path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["activated"] is False


def test_normal_sdk_config_import_never_attempts_optional_modules(tmp_path):
    """A cold interpreter validates without the light-import escape hatch."""
    path = tmp_path / "client.yaml"
    path.write_text("{}", encoding="utf-8")
    script = """
import importlib.abc
import json
import sys

blocked = {"zmq", "bt_api_gateway", "bt_api_execution", "bt_api_risk", "bt_api_monitor"}
attempts = []
class NoOptionalImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in blocked:
            attempts.append(fullname)
            raise ImportError("optional package unavailable")
sys.meta_path.insert(0, NoOptionalImports())
from bt_api_py import BtApi
from bt_api_py.service_config_io import load_client_config
config = load_client_config(sys.argv[1])
assert config.to_public_dict().get("clients", {}) == {}
assert BtApi.__module__ == "bt_api_py.bt_api"
assert not attempts, attempts
assert not blocked.intersection(sys.modules)
print(json.dumps({"attempts": attempts}))
"""
    env = os.environ.copy()
    env.pop("BT_API_PY_LIGHT_IMPORT", None)
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"attempts": []}
