"""Host end-to-end R-04 v2 selection (decision B-2). UNIT evidence, temp storage.

The service host, the pre-trade engine and a real SQLite ledger are composed in
one process against a synthesised plugin module; no Gateway, provider, account
or key exchange is used. What is proved here: an allowlisted selection becomes
the exact artifact mapping the registry composes, a counters-bearing policy
then publishes and activates through it, and every mismatched or missing
selection leaves the service without a policy.
"""

from __future__ import annotations

import json
import sys
from types import ModuleType

import pytest

from bt_api_py.risk_service_config import load_risk_service_config
from bt_api_py.risk_service_host import (
    RiskServiceHost,
    RiskServiceStartError,
    bootstrap_policy,
)
from tests.unit.test_iter64_risk_service_config import document, write_keys

NAME = "trusted.rules:CombinedR04"


def policy_document(**changes):
    value = {
        "schema": "bt_api_risk.policy.v2",
        "policy_id": "unit-v2",
        "account": {"route": "CTP___FUTURE", "account_scope": "account-a"},
        "per_order": {"max_quantity": {"strategy-a": {"rb2701": "5"}}},
        "counters": {"orders": {"warn": 2, "block": 3}},
        "r04_binding": {
            "implementation": NAME,
            "implementation_version": 1,
            "claim_contract_version": 2,
            "observation_contract_version": 2,
        },
    }
    value.update(changes)
    return value


def setup(tmp_path, *, selection=True, trusted=True, policy=None):
    write_keys(tmp_path)
    (tmp_path / "risk_policy.yaml").write_text(
        json.dumps(policy if policy is not None else policy_document()), encoding="utf-8"
    )
    body = document()
    rules = {}
    if selection:
        rules["r04"] = {
            "mode": "v2", "implementation": NAME, "implementation_version": 1,
            "claim_contract_version": 2, "observation_contract_version": 2,
        }
    if trusted:
        rules["trusted_implementations"] = {NAME: 1}
    if rules:
        body["rules"] = rules
    path = tmp_path / "risk_service.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def plugin(*, combined=True, half_declared=False):
    from bt_api_risk.pretrade.r04_v2_contract import R04RuleV2
    from bt_api_risk.pretrade.r04_v2_observation_contract import (
        BuiltinR04CombinedV2,
        R04ObservationDecision,
    )

    if half_declared:
        # Both runners and both methods exist, but the overriding class does not
        # declare the observation contract version in its own dictionary, so the
        # frozen composition refuses it before any call.
        class HalfDeclared(BuiltinR04CombinedV2):
            implementation_version = 1
            result_contract_version = 2

            def observe(self, context):
                return R04ObservationDecision("NO_BREACH")

        rule_class = HalfDeclared
    elif combined:
        rule_class = BuiltinR04CombinedV2
    else:
        class ClaimOnly(R04RuleV2):  # one runner only: cannot serve observations
            implementation_version = 1
            result_contract_version = 2

            def evaluate(self, context):
                raise NotImplementedError

        rule_class = ClaimOnly
    module = ModuleType("trusted.rules")
    module.__dict__["__path__"] = []
    module.CombinedR04 = rule_class
    package = ModuleType("trusted")
    package.__path__ = []
    return {"trusted": package, "trusted.rules": module}


@pytest.fixture(autouse=True)
def _isolate_process_cleanup_state():
    import bt_api_risk.risk_order_journal as risk_journal

    def reset():
        from bt_api_py import risk_service_host as host_module

        host_module._RETAINED_HOSTS.clear()
        host_module._PROBE_STATE["active"] = False
        host_module._PROBE_STATE["uncertain"].clear()
        host_module._STORAGE_INIT_ROOTS.clear()
        host_module._STORAGE_ACTIVE_ROOTS.clear()
        risk_journal._FENCE_ENTRY_QUARANTINED_KEYS.clear()
        risk_journal._FENCE_ENTRY_QUARANTINED_ALL = False
        risk_journal._FENCE_PROVISIONAL_OWNERS.clear()

    reset()
    yield
    reset()


@pytest.fixture
def installed(monkeypatch):
    def install(**kwargs):
        for name, module in plugin(**kwargs).items():
            monkeypatch.setitem(sys.modules, name, module)

    return install


def opened(tmp_path):
    host = RiskServiceHost(load_risk_service_config(tmp_path / "risk_service.json"))
    host.open_storage()
    host.build_engine()
    return host


def test_selected_artifact_publishes_a_counters_policy_and_binds_one_rule(tmp_path, installed):
    from bt_api_risk.pretrade.r04_v2_registry_candidate import BoundR04V2

    installed()
    setup(tmp_path)
    host = opened(tmp_path)
    try:
        assert host.config.r04_selection is not None
        assert list(host.rules.r04_artifacts) == [NAME]
        assert bootstrap_policy(host.engine, host.ledger, host.config, host.rules) == "bootstrapped"
        summary = host.engine.status_summary()
        assert summary["policy_version"] == 1
        assert summary["policy_load_error"] is None
        active = host.engine._active
        assert type(active.r04_v2) is BoundR04V2 and active.r04 is None
        assert active.r04_v2.implementation == NAME
        # Re-bootstrap is a no-op on the same digest.
        assert bootstrap_policy(host.engine, host.ledger, host.config, host.rules) == "unchanged"
    finally:
        host.close()


def test_policy_binding_another_implementation_is_refused_whole(tmp_path, installed):
    installed()
    policy = policy_document()
    policy["r04_binding"]["implementation"] = "trusted.rules:Other"
    setup(tmp_path, policy=policy)
    host = opened(tmp_path)
    try:
        with pytest.raises(RiskServiceStartError) as raised:
            bootstrap_policy(host.engine, host.ledger, host.config, host.rules)
        assert raised.value.code == "RISK_SERVICE_R04_UNAVAILABLE"
        assert host.engine.status_summary()["policy_version"] is None
        with host.ledger.transaction() as txn:
            assert txn.latest_policy() is None
    finally:
        host.close()


def test_restart_without_the_selection_keeps_the_service_unavailable(tmp_path, installed, monkeypatch):
    installed()
    setup(tmp_path)
    host = opened(tmp_path)
    try:
        assert bootstrap_policy(host.engine, host.ledger, host.config, host.rules) == "bootstrapped"
    finally:
        host.close()
    # A later start without ``rules.r04`` cannot compose the stored binding.
    path = setup(tmp_path, selection=False)
    del path
    restarted = RiskServiceHost(load_risk_service_config(tmp_path / "risk_service.json"))
    restarted.open_storage()
    restarted.build_engine()
    try:
        assert restarted.engine.status_summary()["policy_load_error"] == (
            "RULE_R04_BINDING_UNAVAILABLE")
        with pytest.raises(RiskServiceStartError) as raised:
            bootstrap_policy(restarted.engine, restarted.ledger, restarted.config, restarted.rules)
        assert raised.value.code == "RISK_SERVICE_R04_UNAVAILABLE"
        assert restarted.engine.status_summary()["policy_version"] is None
    finally:
        restarted.close()


def test_an_unlisted_selection_never_opens_storage(tmp_path, installed):
    installed()
    setup(tmp_path, trusted=False)
    with pytest.raises(RiskServiceStartError) as raised:
        RiskServiceHost(load_risk_service_config(tmp_path / "risk_service.json"))
    assert raised.value.code == "RISK_SERVICE_R04_NOT_ALLOWED"


def test_a_class_with_one_runner_cannot_be_selected(tmp_path, installed):
    installed(combined=False)
    setup(tmp_path)
    host = RiskServiceHost(load_risk_service_config(tmp_path / "risk_service.json"))
    with pytest.raises(RiskServiceStartError) as raised:
        host.open_storage()
    assert raised.value.code == "RISK_SERVICE_R04_UNAVAILABLE"


def test_a_missing_plugin_module_refuses_startup_with_a_finite_code(tmp_path):
    setup(tmp_path)
    host = RiskServiceHost(load_risk_service_config(tmp_path / "risk_service.json"))
    with pytest.raises(RiskServiceStartError) as raised:
        host.open_storage()
    assert raised.value.code == "RISK_SERVICE_R04_UNAVAILABLE"


def test_an_inconsistently_overridden_rule_is_refused_at_activation(tmp_path, installed):
    # The host accepts the class shape; compose_r04_v2 then refuses the missing
    # explicit contract declaration, so the policy never activates.
    installed(half_declared=True)
    setup(tmp_path)
    host = opened(tmp_path)
    try:
        with pytest.raises(RiskServiceStartError) as raised:
            bootstrap_policy(host.engine, host.ledger, host.config, host.rules)
        assert raised.value.code == "RISK_SERVICE_R04_UNAVAILABLE"
        assert host.engine.status_summary()["policy_version"] is None
    finally:
        host.close()
