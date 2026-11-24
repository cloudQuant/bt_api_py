"""Offline, strict configuration checks; inputs are synthetic."""

from copy import deepcopy

import pytest

from bt_api_py.monitor_service_config import (
    MonitorServiceConfigError,
    load_monitor_service_config,
    parse_monitor_service_config,
    read_bounded,
)
from tests.integration.test_monitor_service_process import deployment


def test_validate_never_reads_keys_or_creates_storage(tmp_path):
    path, document, _ = deployment(tmp_path)
    for key in tmp_path.glob("*.public"):
        key.unlink()
    for key in tmp_path.glob("*.secret"):
        key.unlink()
    config = load_monitor_service_config(path)
    assert not config.storage_directory.exists()
    assert config.ingress.public_key_file == tmp_path / "events.public"
    path.write_text("schema: first\nschema: second\n")
    with pytest.raises(MonitorServiceConfigError):
        load_monitor_service_config(path)


def test_validate_rejects_oversized_config(tmp_path):
    path = tmp_path / "large.yaml"
    path.write_bytes(b"x" * (1_048_576 + 1))
    with pytest.raises(MonitorServiceConfigError):
        load_monitor_service_config(path)


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="POSIX rename-over-open-file TOCTOU test; Windows forbids replacing an open file")
def test_bounded_reader_rejects_entry_replacement(tmp_path, monkeypatch):
    import os

    path = tmp_path / "key"
    path.write_bytes(b"x" * 40)
    path.chmod(0o600)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"y" * 40)
    replacement.chmod(0o600)
    original_read = os.read
    replaced = False

    def replace_during_read(fd, size):
        nonlocal replaced
        result = original_read(fd, size)
        if not replaced:
            os.replace(replacement, path)
            replaced = True
        return result

    monkeypatch.setattr(os, "read", replace_during_read)
    with pytest.raises(ValueError, match="FILE_UNAVAILABLE"):
        read_bounded(path.resolve().parent / path.name, 4096, protected=True)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra="refused"),
        lambda d: d["scope"].update(account_ref=""),
        lambda d: d["ingress"].update(rpc_bind="tcp://0.0.0.0:9999"),
        lambda d: d["heartbeat"].update(rpc_bind=d["ingress"]["rpc_bind"]),
        lambda d: d["heartbeat"].update(service_id=d["ingress"]["service_id"]),
        lambda d: d["storage"].update(
            event_types=["synthetic.observation", "synthetic.observation"]
        ),
        lambda d: d["ingress"]["principals"][0].update(operations=["monitor.heartbeat"]),
    ],
)
def test_invalid_or_ambiguous_config_is_refused(tmp_path, mutate):
    path, document, _ = deployment(tmp_path)
    mutated = deepcopy(document)
    mutate(mutated)
    with pytest.raises(MonitorServiceConfigError):
        parse_monitor_service_config(mutated, path=path)
