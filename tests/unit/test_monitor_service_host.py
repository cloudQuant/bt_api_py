"""Synthetic startup failure and cleanup checks for standalone Monitor."""

import socket

import pytest
import yaml

from bt_api_py.monitor_service_config import load_monitor_service_config
from bt_api_py.monitor_service_host import MonitorServiceError, MonitorServiceHost
from tests.integration.test_monitor_service_process import deployment


def test_key_mismatch_fails_before_storage_creation(tmp_path):
    path, _, keys = deployment(tmp_path)
    (tmp_path / "events.secret").write_bytes(keys["heartbeat"][1])
    host = MonitorServiceHost(load_monitor_service_config(path))
    with pytest.raises(MonitorServiceError, match="MONITOR_KEYS_INVALID"):
        host.start()
    assert not (tmp_path / "data").exists()


def test_symlink_key_and_world_readable_key_rejected(tmp_path):
    path, _, _ = deployment(tmp_path)
    secret = tmp_path / "events.secret"
    saved = tmp_path / "saved.secret"
    secret.rename(saved)
    secret.symlink_to(saved)
    with pytest.raises(MonitorServiceError, match="MONITOR_KEYS_INVALID"):
        MonitorServiceHost(load_monitor_service_config(path)).start()
    secret.unlink()
    saved.rename(secret)
    secret.chmod(0o644)
    with pytest.raises(MonitorServiceError, match="MONITOR_KEYS_INVALID"):
        MonitorServiceHost(load_monitor_service_config(path)).start()
    assert not (tmp_path / "data").exists()


def test_symlink_ancestor_of_key_is_refused(tmp_path):
    path, doc, _ = deployment(tmp_path)
    (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
    doc["ingress"]["secret_key_file"] = "alias/events.secret"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(MonitorServiceError, match="MONITOR_KEYS_INVALID"):
        MonitorServiceHost(load_monitor_service_config(path)).start()
    assert not (tmp_path / "data").exists()


def test_same_key_with_different_identities_is_refused(tmp_path):
    path, doc, _ = deployment(tmp_path)
    doc["ingress"]["principals"][0]["public_key_file"] = "gateway.public"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(MonitorServiceError, match="MONITOR_KEYS_INVALID"):
        MonitorServiceHost(load_monitor_service_config(path)).start()
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("kind", ["ancestor", "wal"])
def test_storage_symlink_refused_before_listener_start(tmp_path, kind):
    path, doc, _ = deployment(tmp_path)
    if kind == "ancestor":
        (tmp_path / "physical").mkdir(mode=0o700)
        (tmp_path / "alias").symlink_to(tmp_path / "physical", target_is_directory=True)
        doc["storage"]["directory"] = "alias/data"
        path.write_text(yaml.safe_dump(doc))
    else:
        (tmp_path / "data").mkdir(mode=0o700)
        (tmp_path / "data/events.sqlite-wal").symlink_to(tmp_path / "events.public")
    with pytest.raises(MonitorServiceError, match="MONITOR_STORAGE_INVALID"):
        MonitorServiceHost(load_monitor_service_config(path)).start()


def test_second_listener_failure_closes_first(tmp_path):
    path, doc, _ = deployment(tmp_path)
    port = int(doc["heartbeat"]["rpc_bind"].rsplit(":", 1)[1])
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", port))
        occupied.listen()
        host = MonitorServiceHost(load_monitor_service_config(path))
        with pytest.raises(MonitorServiceError, match="MONITOR_START_FAILED"):
            host.start()
        assert host.state == "failed"
        assert all(item.state in {"closed", "failed"} for item in host._hosts)
        first_port = int(doc["ingress"]["rpc_bind"].rsplit(":", 1)[1])
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", first_port))
