"""Offline event allowlist limits match the real Monitor target."""

import json
import subprocess
import sys

import pytest
import yaml
from bt_api_monitor.service_component import MonitorReadTarget
from bt_api_monitor.service_ingress import MonitorIngressTarget

from bt_api_py import monitor_service_host
from bt_api_py.monitor_service_config import MonitorServiceConfigError, parse_monitor_service_config


def document(event_types):
    def listener(name, port):
        return {
            "service_id": f"{name}-service",
            "principal_ref": f"{name}-principal",
            "instance_id": f"{name}-instance",
            "scope_ref": f"{name}-scope",
            "rpc_bind": f"tcp://127.0.0.1:{port}",
            "public_key_file": f"{name}.public",
            "secret_key_file": f"{name}.secret",
            "principals": [{
                "principal_ref": f"{name}-client",
                "actor_ref": f"{name}-actor",
                "public_key_file": f"{name}-client.public",
            }],
        }

    return {
        "schema": "bt_api_monitor.service.v1",
        "scope": {"execution_domain_ref": "domain", "account_ref": "account"},
        "storage": {"directory": "data", "outbox_scope": "scope", "event_types": event_types},
        "ingress": listener("ingress", 11001),
        "heartbeat": listener("heartbeat", 11002),
    }


def test_sixteen_events_match_real_monitor_targets(tmp_path):
    names = [f"event.{i}" for i in range(15)] + ["界" * 42 + "ab"]
    config = parse_monitor_service_config(document(names), path=tmp_path / "config.yaml")
    assert config.event_types == tuple(names)
    assert MonitorIngressTarget is MonitorReadTarget
    assert MonitorIngressTarget(config.outbox_scope, config.event_types).event_types == tuple(
        sorted(names)
    )
    assert not config.storage_directory.exists()


@pytest.mark.parametrize(
    "events",
    [
        [f"event.{i}" for i in range(17)],
        [],
        True,
        ("event",),
        [True],
        ["private-event", "private-event"],
        ["界" * 43],
        ["invalid\ud800"],
        [" private-event"],
        ["private\x00event"],
    ],
)
def test_invalid_events_fail_with_finite_error_before_side_effects(tmp_path, events):
    with pytest.raises(MonitorServiceConfigError) as error:
        parse_monitor_service_config(document(events), path=tmp_path / "secret-path.yaml")
    assert error.value.code == "MONITOR_CONFIG_INVALID"
    assert str(error.value) == "MONITOR_CONFIG_INVALID"
    assert not (tmp_path / "data").exists()


def test_seventeen_events_refused_before_host_or_key_access(tmp_path, monkeypatch, capsys):
    config = tmp_path / "sensitive-config.yaml"
    config.write_text(yaml.safe_dump(document([f"private-event.{i}" for i in range(17)])))

    def must_not_start(*args, **kwargs):
        raise AssertionError("Host construction must not be reached")

    monkeypatch.setattr(monitor_service_host, "MonitorServiceHost", must_not_start)
    assert monitor_service_host.main(["run", "--config", str(config)]) == 1
    output = capsys.readouterr()
    assert output.out == "MONITOR_FAILED\n"
    assert output.err == ""
    assert not (tmp_path / "data").exists()


def test_config_import_and_validation_do_not_require_monitor_package(tmp_path):
    script = r'''
import importlib.abc
import json
import sys
from pathlib import Path
class DenyMonitor(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "bt_api_monitor" or fullname.startswith("bt_api_monitor."):
            raise ImportError("optional Monitor is disabled")
sys.meta_path.insert(0, DenyMonitor())
from bt_api_py.monitor_service_config import parse_monitor_service_config
config = parse_monitor_service_config(json.loads(sys.argv[1]), path=Path("config.yaml"))
assert config.event_types == ("synthetic.event",)
'''
    result = subprocess.run(
        [sys.executable, "-c", script, json.dumps(document(["synthetic.event"]))],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
