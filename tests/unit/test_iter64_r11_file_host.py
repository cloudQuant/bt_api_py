"""R-11 v2 file loading and admission through the SDK host, offline only."""

import json

import pytest

from bt_api_py.risk_service_component import RiskOrderService
from bt_api_py.risk_service_config import load_risk_service_config
from bt_api_py.risk_service_host import (
    RiskServiceHost,
    RiskServiceStartError,
    bootstrap_policy,
    main,
    read_policy_files,
)
from tests.unit.test_iter64_risk_service_component import ctx, payload
from tests.unit.test_iter64_risk_service_component import V2_POLICY_BLOCKED
from tests.unit.test_iter64_risk_service_config import document, write_keys


NOW = 1_900_000_000_000_000_000
DAY = "20261112"


def setup_files(root):
    write_keys(root)
    config = document()
    config["policy"]["file"] = "policies/risk.json"
    config_path = root / "service.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    policy_dir = root / "policies"
    (policy_dir / "rules").mkdir(parents=True)
    policy_path = policy_dir / "risk.json"
    policy_path.write_text(json.dumps({
        "schema": "bt_api_risk.policy.v1", "policy_id": "file-test",
        "account": {"route": "CTP___FUTURE", "account_scope": "account-a"},
        "per_order": {"max_quantity": {"strategy-a": {"rb2701": "2"}}},
        "self_trade": {"enabled": False},
        "exchange_rules": {"file": "rules/exchange.json"},
    }), encoding="utf-8")
    parsed = load_risk_service_config(config_path)
    key = parsed.instrument_for("SHFE", "rb2701").key
    rules = {
        "schema": "bt_api_risk.exchange_rules.v2", "rule_set_id": "file-test",
        "account": {"route": "CTP___FUTURE", "account_scope": "account-a"},
        "source": {"source_id": "synthetic", "source_version": "1",
                   "obtained_at_ns": NOW - 1000, "expires_at_ns": NOW + 1000},
        "validity": {"from_trading_day": DAY, "through_trading_day": DAY},
        "instruments": [{"instrument": key.to_dict(),
                         "position": {"applicability": "not_applicable"},
                         "daily_open": {"applicability": "not_applicable"},
                         "delivery": {"applicability": "not_applicable"}}],
    }
    rules_path = policy_dir / "rules" / "exchange.json"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    return config_path, policy_path, rules_path, rules, key


def open_host(config_path):
    host = RiskServiceHost(load_risk_service_config(config_path), now_ns=lambda: NOW)
    host.open_storage()
    host.build_engine()
    return host


# ``setup_files`` writes a v1 policy; the product accepts only v2 documents and
# v2 activation remains blocked by RULE_R04_V2_UNAVAILABLE pending the
# iteration-64 R-04/R-11 decision, so the bootstrap these cases assert cannot
# happen yet.  Strict xfail keeps them collected and flags an XPASS.
@V2_POLICY_BLOCKED
def test_relative_files_offline_validation_and_restart_keep_policy_digest(tmp_path, capsys, monkeypatch):
    config_path, policy_path, rules_path, rules, _ = setup_files(tmp_path)
    # Neither config nor policy paths are interpreted relative to process cwd.
    monkeypatch.chdir(tmp_path.parent)
    assert load_risk_service_config(config_path).policy_file == policy_path
    assert json.loads(read_policy_files(policy_path)[1]) == rules
    assert main(["validate-policy", str(policy_path), "--config", str(config_path)]) == 0
    offline = json.loads(capsys.readouterr().out)
    assert offline["valid"] is True
    host = open_host(config_path)
    try:
        assert bootstrap_policy(host.engine, host.ledger, host.config, host.rules) == "bootstrapped"
        first = host.engine.status_summary()
        assert first["policy_digest"] == offline["digest"]
    finally:
        assert host.close()
    # Whitespace and mapping order do not change the canonical rule digest.
    rules_path.write_text(json.dumps(rules, indent=2, sort_keys=True), encoding="utf-8")
    restarted = open_host(config_path)
    try:
        assert bootstrap_policy(restarted.engine, restarted.ledger, restarted.config, restarted.rules) == "unchanged"
        assert restarted.engine.status_summary()["policy_digest"] == first["policy_digest"]
        assert restarted.engine.status_summary()["policy_version"] == first["policy_version"] == 1
    finally:
        assert restarted.close()


@pytest.mark.parametrize("case", [
    "missing",
    pytest.param("wrong_account", marks=V2_POLICY_BLOCKED),
    pytest.param("invalid_range", marks=V2_POLICY_BLOCKED),
])
def test_unusable_rule_files_refuse_bootstrap(tmp_path, capsys, case):
    config_path, policy_path, rules_path, rules, _ = setup_files(tmp_path)
    if case == "missing":
        rules_path.unlink()
        offline_code, bootstrap_code = "RISK_SERVICE_EXCHANGE_RULES_UNAVAILABLE", "RISK_SERVICE_EXCHANGE_RULES_UNAVAILABLE"
    else:
        if case == "wrong_account":
            rules["account"]["account_scope"] = "another-account"
            offline_code = "policy_account_mismatch"
        else:
            rules["validity"]["from_trading_day"] = "20261113"
            offline_code = "policy_invalid_value"
        rules_path.write_text(json.dumps(rules), encoding="utf-8")
        bootstrap_code = "RISK_SERVICE_POLICY_INVALID"
    assert main(["validate-policy", str(policy_path), "--config", str(config_path)]) == 1
    assert json.loads(capsys.readouterr().out) == {"valid": False, "code": offline_code}
    host = open_host(config_path)
    try:
        with pytest.raises(RiskServiceStartError) as error:
            bootstrap_policy(host.engine, host.ledger, host.config, host.rules)
        assert error.value.code == bootstrap_code
        assert host.engine.status_summary()["policy_version"] is None
    finally:
        assert host.close()


@V2_POLICY_BLOCKED
@pytest.mark.parametrize("window", ["valid", "expired_time", "expired_day"])
def test_selected_r11_stays_unavailable_without_authoritative_issuer(tmp_path, monkeypatch, window):
    from bt_api_risk.pretrade.policy import exchange_rule_context_match, parse_exchange_rules

    config_path, _, rules_path, rules, key = setup_files(tmp_path)
    if window == "expired_time":
        rules["source"]["obtained_at_ns"] = NOW - 2000
        rules["source"]["expires_at_ns"] = NOW - 1000
    elif window == "expired_day":
        rules["validity"] = {"from_trading_day": "20261110", "through_trading_day": "20261111"}
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    parsed = parse_exchange_rules(rules_path.read_text(encoding="utf-8"))
    matched = exchange_rule_context_match(parsed, route="CTP___FUTURE", account_scope="account-a",
                                           instrument=key, trading_day=DAY, now_ns=NOW)
    assert (matched is not None) == (window == "valid")
    host = open_host(config_path)
    try:
        # A fake trading day isolates the rule check; it does not issue source authority.
        host.facts.note_event_day(DAY)
        assert bootstrap_policy(host.engine, host.ledger, host.config, host.rules) == "bootstrapped"
        gateway_calls = []
        monkeypatch.setattr(host.order_port, "reserve", lambda **kw: gateway_calls.append(kw))
        component = RiskOrderService(host.engine, host.ledger, account_scope=host.config.account_scope,
            route=host.config.route, execution_domain_ref=host.config.execution_domain_ref,
            principals=host.config.principals, instrument_for=host.instrument_for, ready=lambda: True)
        reply = component.handle("risk.order.reserve", context=ctx("principal-a"), arguments={
            "request_key": "order-1", "payload": payload("order-1", symbol="rb2701"),
            "parent_ref": None, "child_id": None})
        assert (reply["outcome"], reply["code"]) == ("DENIED", "risk_exchange_rule_unavailable")
        assert gateway_calls == []
    finally:
        assert host.close()
