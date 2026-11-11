"""Risk service host composition and CLI (64.09). UNIT evidence: temp storage, no Gateway contact."""

from __future__ import annotations

import json
import os
import stat

import pytest

from bt_api_py.risk_service_config import load_risk_service_config
from bt_api_py.risk_service_host import (
    RiskServiceHost,
    RiskServiceStartError,
    bootstrap_policy,
    build_authority,
    main,
    probe_writer_fence,
)
from tests.unit.test_iter64_risk_service_config import document, write_keys

POLICY = (
    "schema: bt_api_risk.policy.v1\n"
    "policy_id: unit-{name}\n"
    "account: {{route: CTP___FUTURE, account_scope: account-a}}\n"
    "per_order:\n  max_quantity: {{strategy-a: {{rb2701: \"{cap}\"}}}}\n"
)


def setup(tmp_path, cap="5"):
    write_keys(tmp_path)
    (tmp_path / "risk_policy.yaml").write_text(POLICY.format(name="a", cap=cap), encoding="utf-8")
    path = tmp_path / "risk_service.json"
    path.write_text(json.dumps(document()), encoding="utf-8")
    return path


def opened(tmp_path):
    host = RiskServiceHost(load_risk_service_config(tmp_path / "risk_service.json"))
    host.open_storage()
    host.build_engine()
    return host


def test_policy_bootstrap_first_unchanged_tightened_and_loosening_refused(tmp_path):
    setup(tmp_path)
    host = opened(tmp_path)
    try:
        assert bootstrap_policy(host.engine, host.ledger, host.config, host.rules) == "bootstrapped"
        assert host.engine.status_summary()["policy_version"] == 1
        assert bootstrap_policy(host.engine, host.ledger, host.config, host.rules) == "unchanged"
        (tmp_path / "risk_policy.yaml").write_text(POLICY.format(name="a", cap="3"))
        assert bootstrap_policy(host.engine, host.ledger, host.config, host.rules) == "tightened"
        assert host.engine.status_summary()["policy_version"] == 2
        (tmp_path / "risk_policy.yaml").write_text(POLICY.format(name="a", cap="9"))
        with pytest.raises(RiskServiceStartError) as raised:
            bootstrap_policy(host.engine, host.ledger, host.config, host.rules)
        assert raised.value.code == "RISK_SERVICE_POLICY_FILE_LOOSENS"
        assert host.engine.status_summary()["policy_version"] == 2
        (tmp_path / "risk_policy.yaml").write_text(
            POLICY.format(name="a", cap="3").replace("account-a", "account-b"))
        with pytest.raises(RiskServiceStartError) as raised:
            bootstrap_policy(host.engine, host.ledger, host.config, host.rules)
        assert raised.value.code == "RISK_SERVICE_POLICY_ACCOUNT_MISMATCH"
    finally:
        host.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file locks and modes")
def test_storage_is_private_and_a_held_fence_refuses_at_once(tmp_path):
    setup(tmp_path)
    host = opened(tmp_path)
    try:
        config = host.config
        assert stat.S_IMODE(config.storage_directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(config.risk_database.stat().st_mode) == 0o600
        # Another opener of the same account's journal must not wait for the fence.
        from bt_api_risk.core.admission import AccountScope
        from bt_api_risk.risk_order_journal import RiskOrderJournal

        other = RiskOrderJournal(config.risk_database, writer_id="second")
        with pytest.raises(RiskServiceStartError) as raised:
            probe_writer_fence(other, AccountScope("ctp", "account-a", "simnow"))
        assert raised.value.code == "RISK_SERVICE_FENCE_HELD"
    finally:
        host.close()
    # After close the fence is free again.
    probe_writer_fence(other, AccountScope("ctp", "account-a", "simnow"))


def test_authority_installs_the_principal_table_only(tmp_path):
    setup(tmp_path)
    config = load_risk_service_config(tmp_path / "risk_service.json")
    authority = build_authority(config)
    assert set(authority.principals.values()) == {"principal-a", "operator-1"}
    (strategy_grant,) = authority.grants["principal-a"]
    assert strategy_grant.identity.role.value == "strategy_client"
    (resource,) = strategy_grant.resources
    assert resource.port == "service.risk_order" and resource.scope.strategy_ref == "strategy-a"
    assert resource.scope.run_id is None and "risk.policy.publish" not in resource.operations
    (operator_grant,) = authority.grants["operator-1"]
    (resource,) = operator_grant.resources
    assert resource.port == "service.risk_admin" and resource.scope.strategy_ref is None
    assert authority.identity.actor_ref == "risk-service"


def test_cli_validate_policy_offline(tmp_path, capsys):
    setup(tmp_path)
    assert main(["validate-policy", str(tmp_path / "risk_policy.yaml")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["valid"] is True and out["policy_id"] == "unit-a" and len(out["digest"]) == 64
    (tmp_path / "bad.yaml").write_text("schema: bt_api_risk.policy.v1\npolicy_id: x\n")
    assert main(["validate-policy", str(tmp_path / "bad.yaml")]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["valid"] is False and out["code"]


def test_cli_status_reads_the_local_ledger_without_the_gateway(tmp_path, capsys):
    path = setup(tmp_path)
    assert main(["status", "--config", str(path)]) == 2
    assert json.loads(capsys.readouterr().out) == {"code": "RISK_SERVICE_STORAGE_MISSING"}
    host = opened(tmp_path)
    bootstrap_policy(host.engine, host.ledger, host.config, host.rules)
    try:
        # While the service holds the fence, status answers "running" at once.
        assert main(["status", "--config", str(path)]) == 3
        assert json.loads(capsys.readouterr().out) == {"running": True,
                                                       "code": "RISK_SERVICE_FENCE_HELD"}
    finally:
        host.close()
    assert main(["status", "--config", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["running"] is False and out["summary"]["policy_version"] == 1
    text = json.dumps(out)
    assert str(tmp_path) not in text and "account-a" not in json.dumps(out["summary"]["counts"])


def test_cli_run_refuses_with_a_finite_code(tmp_path, capsys):
    path = setup(tmp_path)
    raw = json.loads(path.read_text())
    raw["deadlines"]["reservation_ttl_seconds"] = 31
    path.write_text(json.dumps(raw))
    assert main(["run", "--config", str(path)]) == 2
    assert capsys.readouterr().err.strip() == (
        "RISK_SERVICE_START_REFUSED RISK_SERVICE_CONFIG_NUMBER_OUT_OF_RANGE")
