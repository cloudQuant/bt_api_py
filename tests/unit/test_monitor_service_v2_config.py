"""Offline v2 producer binding validation with synthetic deployment settings."""

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest
from bt_api_base.service_protocol import WireRole

from bt_api_py.monitor_service_config import MonitorServiceConfigError, parse_monitor_service_config
from tests.integration.test_monitor_service_process import deployment


def v2_document(tmp_path):
    path, doc, _ = deployment(tmp_path)
    doc["schema"] = "bt_api_monitor.service.v2"
    doc["producer_bindings"] = [
        {
            "binding_ref": "binding-1",
            "source_ref": "source-1",
            "identity": {"principal_ref": "producer", "actor_ref": "producer-service"},
            "scope": {
                "execution_domain_ref": "synthetic-domain",
                "account_ref": "synthetic-account",
                "account_refs": [],
                "strategy_ref": None,
                "run_id": None,
                "manual_book_ref": None,
                "portfolio_ref": None,
                "group_id": None,
                "leg_id": None,
            },
            "outbox_scope": "events",
            "event_types": ["synthetic.observation"],
        }
    ]
    return path, doc


def test_v2_copies_and_freezes_complete_producer_binding(tmp_path):
    path, doc = v2_document(tmp_path)
    config = parse_monitor_service_config(doc, path=path)
    binding = config.producer_bindings[0]
    assert binding.identity.role is WireRole.SERVICE
    assert binding.identity.delegate_ref is None
    assert binding.scope.strategy_ref is None and binding.scope.run_id is None
    doc["producer_bindings"][0]["event_types"].clear()
    assert binding.event_types == ("synthetic.observation",)
    with pytest.raises(FrozenInstanceError):
        binding.source_ref = "other"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(schema="bt_api_monitor.service.v1"),
        lambda d: d.pop("producer_bindings"),
        lambda d: d.update(producer_bindings=None),
        lambda d: d.update(producer_bindings=[]),
        lambda d: d["producer_bindings"][0].update(extra=1),
        lambda d: d["producer_bindings"][0]["scope"].pop("run_id"),
        lambda d: d["producer_bindings"][0]["scope"].update(
            strategy_ref="strategy", run_id="run-1"
        ),
        lambda d: d["producer_bindings"][0]["scope"].update(account_ref="other"),
        lambda d: d["scope"].update(account_ref="bad\x85account"),
        lambda d: d["producer_bindings"][0]["scope"].update(execution_domain_ref="other"),
        lambda d: d["producer_bindings"][0]["scope"].update(account_refs=["other"]),
        lambda d: d["producer_bindings"][0]["identity"].update(actor_ref="other"),
        lambda d: d["producer_bindings"][0]["identity"].update(delegate_ref=None),
        lambda d: d["producer_bindings"][0]["identity"].update(
            principal_ref="gateway", actor_ref="gateway-service"
        ),
        lambda d: d["producer_bindings"][0].update(outbox_scope="other"),
        lambda d: d["producer_bindings"][0].update(source_ref="bad\x85source"),
        lambda d: d["producer_bindings"][0].update(event_types=[]),
        lambda d: d["producer_bindings"][0].update(event_types=["other"]),
        lambda d: d["producer_bindings"][0].update(event_types=["synthetic.observation"] * 2),
        lambda d: d["producer_bindings"].append(deepcopy(d["producer_bindings"][0])),
        lambda d: d.update(producer_bindings=[deepcopy(d["producer_bindings"][0])] * 1025),
        lambda d: d["producer_bindings"].append(
            {**deepcopy(d["producer_bindings"][0]), "binding_ref": "binding-2"}
        ),
    ],
)
def test_v2_rejects_invalid_bindings_offline(tmp_path, mutation):
    path, doc = v2_document(tmp_path)
    mutation(doc)
    with pytest.raises(MonitorServiceConfigError):
        parse_monitor_service_config(doc, path=path)


def test_v1_keeps_original_shape(tmp_path):
    path, doc, _ = deployment(tmp_path)
    assert parse_monitor_service_config(doc, path=path).producer_bindings == ()
    doc["producer_bindings"] = []
    with pytest.raises(MonitorServiceConfigError):
        parse_monitor_service_config(doc, path=path)
