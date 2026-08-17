from pathlib import Path

from bt_api_py.certification import (
    CertificationAuditEvent,
    InMemoryCertificationAuditSink,
    JsonlCertificationAuditSink,
    default_certification_scenario_registry,
)
from bt_api_py.gateway.client import GatewayClient


def test_gateway_client_import_path_is_stable() -> None:
    client = GatewayClient(
        gateway_market_endpoint="tcp://127.0.0.1:10001",
        gateway_command_endpoint="tcp://127.0.0.1:10002",
        gateway_event_endpoint="tcp://127.0.0.1:10003",
        account_id="paper",
        exchange_type="CTP",
        asset_type="FUTURE",
    )

    assert client.market_endpoint == "tcp://127.0.0.1:10001"
    assert client.command_endpoint == "tcp://127.0.0.1:10002"
    assert client.private_endpoint == "tcp://127.0.0.1:10003"


def test_certification_registry_contains_33_unique_scenarios() -> None:
    registry = default_certification_scenario_registry()
    scenarios = registry.all()
    scenario_ids = [item.scenario_id for item in scenarios]

    assert len(scenarios) == 33
    assert len(scenario_ids) == len(set(scenario_ids))
    assert registry.get("AUTH-01").category == "接口适应性"


def test_certification_audit_sinks_mask_sensitive_fields(tmp_path: Path) -> None:
    event = CertificationAuditEvent(
        scenario_id="AUTH-01",
        scenario_name="认证登录",
        category="接口适应性",
        status="PASS",
        message="auth ok",
        evidence={"auth_code": "1234567890", "broker_id": "9999"},
        raw_fields={"password": "secret"},
    )
    memory = InMemoryCertificationAuditSink()
    jsonl = JsonlCertificationAuditSink(tmp_path / "audit.jsonl")

    memory_payload = memory.emit(event)
    file_payload = jsonl.emit(event)

    assert memory_payload["evidence"]["auth_code"] == "**********"
    assert memory_payload["raw_fields"]["password"] == "******"
    assert file_payload["evidence"]["auth_code"] == "**********"
    assert memory.query(scenario_id="AUTH-01")[0]["message"] == "auth ok"
