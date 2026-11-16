"""Explicit recovery relay protected configuration; synthetic keys only."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
import zmq

from bt_api_py.ctp_gateway_local_profile import CtpLocalProfileError, load_simnow_trading_resources
from tests.unit.test_ctp_gateway_recovery_operator_config import recovery
from tests.unit.test_iter63_ctp_gateway_local_profile import _fixture, _returns


def relay_config(tmp_path):
    server, _ = zmq.curve_keypair()
    client, secret = zmq.curve_keypair()
    return {
        "monitor_identity": {
            "role": "service",
            "principal_ref": "monitor-principal",
            "actor_ref": "monitor-service",
            "delegate_ref": None,
        },
        "instance_id": "monitor-ingress",
        "scope_ref": "monitor-scope",
        "rpc_connect": "tcp://127.0.0.1:8765",
        "server_curve_public_key": server.decode(),
        "client_curve_public_key": client.decode(),
        "client_curve_secret_key": secret.decode(),
        "outbox_scope": "monitor-events",
        "binding_ref": "gateway-source-binding",
        "checkpoint_path": str(tmp_path / "relay.sqlite"),
        "poll_interval_seconds": 0.1,
        "start_timeout_seconds": 2,
        "close_timeout_seconds": 2,
        "request_timeout_seconds": 1,
        "max_records": 16,
        "max_bytes": 65536,
    }


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["returns"] = _returns()
    doc["recovery_observation"] = recovery(tmp_path)
    doc["recovery_monitor_relay"] = relay_config(tmp_path)

    def load():
        file.write_text(json.dumps(doc), encoding="utf-8")
        return load_simnow_trading_resources(config)

    return doc, load


def test_explicit_protected_config_derives_source_and_uses_lazy_dedicated_runtime(bundle):
    doc, load = bundle
    resources = load()
    settings = resources.recovery_monitor_relay
    assert settings.source_ref == resources.recovery_observation_settings.source_ref
    assert settings.journal_scope_ref == resources.recovery_observation_settings.scope_ref
    assert settings.journal_path == resources.recovery_observation_settings.journal_path
    assert not Path(settings.checkpoint_path).exists()
    runtime = settings.runtime_factory(require_unregister_ack=True)
    assert runtime.state == "new"
    assert runtime._requested_operations == {"upstream.telemetry": frozenset({"events.append"})}
    assert runtime._require_unregister_ack is True
    assert doc["recovery_monitor_relay"]["client_curve_secret_key"] not in repr(settings)


@pytest.mark.parametrize(
    "change",
    [
        lambda doc: doc.pop("recovery_observation"),
        lambda doc: doc.update(recovery_monitor_relay=None),
        lambda doc: doc["recovery_monitor_relay"].update(extra=True),
        lambda doc: doc["recovery_monitor_relay"].update(source_ref="injected"),
        lambda doc: doc["recovery_monitor_relay"].update(journal_scope_ref="injected"),
        lambda doc: doc["recovery_monitor_relay"].update(journal_path="injected"),
        lambda doc: doc["recovery_monitor_relay"].update(checkpoint_path="relative.sqlite"),
        lambda doc: doc["recovery_monitor_relay"].update(request_timeout_seconds=True),
        lambda doc: doc["recovery_monitor_relay"].update(request_timeout_seconds=0.0001),
        lambda doc: doc["recovery_monitor_relay"].update(request_timeout_seconds=121),
        lambda doc: doc["recovery_monitor_relay"].update(poll_interval_seconds=0),
        lambda doc: doc["recovery_monitor_relay"].update(client_curve_secret_key="bad"),
        lambda doc: doc["recovery_monitor_relay"]["monitor_identity"].update(
            role="strategy_client"
        ),
        lambda doc: doc["recovery_monitor_relay"]["monitor_identity"].update(delegate_ref="other"),
        lambda doc: doc["recovery_observation"].update(
            qualification_mode="disabled", clock_max_interval_ns=None
        ),
    ],
)
def test_invalid_selected_config_fails_without_network(bundle, change):
    doc, load = bundle
    change(doc)
    with pytest.raises(CtpLocalProfileError, match="CTP_LOCAL_RESOURCES_INVALID"):
        load()


def test_each_relay_field_is_required(bundle):
    doc, load = bundle
    original = deepcopy(doc["recovery_monitor_relay"])
    for field in original:
        doc["recovery_monitor_relay"] = deepcopy(original)
        del doc["recovery_monitor_relay"][field]
        with pytest.raises(CtpLocalProfileError):
            load()


def test_relay_omitted_leaves_existing_observation_enabled(bundle):
    doc, load = bundle
    del doc["recovery_monitor_relay"]
    resources = load()
    assert resources.recovery_monitor_relay is None
    assert resources.recovery_observation_settings is not None


def test_checkpoint_storage_rejects_aliases_and_roots(tmp_path):
    from types import SimpleNamespace

    from bt_api_py.ctp_gateway_trading import _relay_storage_distinct

    guard, flow, calendar = (tmp_path / name for name in ("guard", "flow", "calendar"))
    for directory in (guard, flow, calendar):
        directory.mkdir()
    settings = SimpleNamespace(
        guard_root=guard, trusted_flow_root=flow, journal_path=tmp_path / "journal"
    )
    recovery = SimpleNamespace(journal_path=tmp_path / "source")
    account = SimpleNamespace(
        journal_path=tmp_path / "read", observation_identity_path=tmp_path / "identity"
    )
    calendar_settings = SimpleNamespace(root=calendar)
    relay = SimpleNamespace(checkpoint_path=tmp_path / "relay")
    assert _relay_storage_distinct(
        settings, tmp_path / "registry", account, recovery, calendar_settings, relay
    )
    for path in (
        tmp_path / "registry",
        tmp_path / "registry-wal",
        tmp_path / "journal",
        tmp_path / "source",
        tmp_path / "read",
        tmp_path / "identity",
        guard / "relay",
        flow / "relay",
        calendar / "relay",
    ):
        relay.checkpoint_path = path
        assert not _relay_storage_distinct(
            settings, tmp_path / "registry", account, recovery, calendar_settings, relay
        )
    (tmp_path / "source").write_bytes(b"synthetic")
    (tmp_path / "alias").hardlink_to(tmp_path / "source")
    relay.checkpoint_path = tmp_path / "alias"
    assert not _relay_storage_distinct(
        settings, tmp_path / "registry", account, recovery, calendar_settings, relay
    )
    relay.checkpoint_path = tmp_path / "relay"
    (tmp_path / "relay.lock").hardlink_to(tmp_path / "source")
    assert not _relay_storage_distinct(
        settings, tmp_path / "registry", account, recovery, calendar_settings, relay
    )


def test_default_import_does_not_load_optional_relay_components():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import bt_api_py.ctp_gateway_trading; "
            "assert 'bt_api_py.gateway_recovery_monitor_worker' not in sys.modules; "
            "assert 'bt_api_py.ctp_gateway_recovery_relay_config' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
