"""Offline construction only; all credentials and identities are synthetic."""
import json

from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources, simnow_trading_profile_factory
from bt_api_py.gateway_operator_application import load_gateway_operator_launch_config
from bt_api_py.monitor_service_config import load_monitor_service_config
from bt_api_py.risk_service_config import load_risk_service_config
from bt_api_risk.pretrade.policy import validate_policy_v2_document
from bt_api_py.simnow_acceptance_bootstrap import bootstrap_simnow_acceptance


def sample(root):
    return bootstrap_simnow_acceptance(root=root, credentials={"broker_id": "broker-synthetic", "user_id": "user-synthetic", "password": "password-synthetic", "auth_code": "auth", "app_id": "app", "td_front": "tcp://td.invalid:1", "md_front": "tcp://md.invalid:2"}, instrument={"venue": "SHFE", "native_symbol": "rb2701", "multiplier": "10", "tick_size": "1", "margin_pool": "main"}, strategy_uuid="c0988d05-a46a-4248-a3b9-729e15c78483")


def test_parse_and_compose_without_connecting(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "private"
    root.mkdir(mode=0o700)
    result = sample(root)
    monkeypatch.setenv("BT_API_CTP_GATEWAY_RESOURCES_FILE", str(result.gateway_resources))
    launch = load_gateway_operator_launch_config(result.gateway_config)
    resources = load_simnow_trading_resources(launch.service_config)
    assert resources.settings.write_enabled is False
    assert resources.required_monitor is not None
    assert result.required_monitor_configured and not result.ready
    assert resources.required_monitor.gateway_identity == resources.authority.identity
    assert resources.required_monitor.automatic_resume is False
    assert simnow_trading_profile_factory.create_deployment(launch.service_config).state == "new"
    assert load_risk_service_config(result.risk_config)
    monitor = load_monitor_service_config(result.monitor_config)
    heartbeat, = monitor.heartbeat.principals
    assert (heartbeat.principal_ref, heartbeat.actor_ref) == (resources.authority.identity.principal_ref, resources.authority.identity.actor_ref)
    assert resources.required_monitor.monitor_identity.principal_ref == monitor.heartbeat.principal_ref
    assert resources.required_monitor.monitor_identity.actor_ref == monitor.heartbeat.service_id
    policy = validate_policy_v2_document(json.loads((root / "risk-policy.json").read_text()))
    assert policy.self_trade_enabled and policy.closable_check_enabled
