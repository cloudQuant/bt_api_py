"""Risk service host composition and CLI (64.09). UNIT evidence: temp storage, no Gateway contact."""

from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace

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


def test_risk_gateway_runtimes_require_explicit_unregister_ack(tmp_path):
    from bt_api_base.service_protocol import WireScope
    from bt_api_py.risk_service_host import OBSERVER_OPERATIONS
    from bt_api_py.risk_service_gateway import CONTROL_OPERATIONS

    host = RiskServiceHost(load_risk_service_config(setup(tmp_path)))
    scope = WireScope(host.config.execution_domain_ref, account_ref=host.config.account_scope)
    observer = host._service_runtime("upstream.account", scope, OBSERVER_OPERATIONS)._factory()
    control = host._application_runtime({"application.direct_write": CONTROL_OPERATIONS})._factory()
    try:
        assert observer._require_unregister_ack is True
        assert control._require_unregister_ack is True
    finally:
        observer.close()
        control.close()
        host.close()


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


def test_cli_status_busy_storage_does_not_claim_service_stopped(tmp_path, capsys, monkeypatch):
    path = setup(tmp_path)
    config = load_risk_service_config(path)
    config.storage_directory.mkdir()
    config.risk_database.touch()

    def busy(_self):
        raise RiskServiceStartError("RISK_SERVICE_STORAGE_BUSY")

    monkeypatch.setattr(RiskServiceHost, "open_storage", busy)
    assert main(["status", "--config", str(path)]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "running": None, "code": "RISK_SERVICE_STORAGE_BUSY"}


@pytest.mark.parametrize("cleanup", ["exception", "alive", "owner_uncertain", "unregister_uncertain", "registration_unknown", "factory_unproven"])
def test_observer_reconnect_never_replaces_an_unresolved_owner(tmp_path, monkeypatch, cleanup):
    host = RiskServiceHost(load_risk_service_config(setup(tmp_path)), monotonic=lambda: 1.0)
    gaps = []
    host.pump = SimpleNamespace(_gap=gaps.append)
    host._observer_route = object()
    class Old:
        def status(self):
            return SimpleNamespace(state="failed")
        def close(self, **_kwargs):
            if cleanup == "exception":
                raise RuntimeError("cleanup failed")
            return SimpleNamespace(state="failed", thread_alive=cleanup == "alive",
                                   error_code="FACTORY_FAILED_CLEANUP_UNPROVEN" if cleanup == "factory_unproven" else "MAINTENANCE_FAILED",
                                   owner_cleanup_uncertainties=int(cleanup == "owner_uncertain"),
                                   unregister_uncertainties=int(cleanup == "unregister_uncertain"),
                                   unknown_registration_count=int(cleanup == "registration_unknown"))
    old = Old()
    host._observer_runtime = old
    host._runtimes = [old]
    monkeypatch.setattr(host, "_service_runtime", lambda *_args, **_kwargs: pytest.fail("replacement"))
    host._reconnect_observer()
    assert gaps == ["observation_reconnect"]
    assert host._observer_runtime is old and host._runtimes == [old]


def test_delegate_failed_start_with_unresolved_cleanup_retains_original_runtime(tmp_path, monkeypatch):
    host = RiskServiceHost(load_risk_service_config(setup(tmp_path)))
    class Unresolved:
        def start(self, **_kwargs):
            raise RuntimeError("startup failed")
        def close(self, **_kwargs):
            raise RuntimeError("cleanup unconfirmed")
    runtime = Unresolved()
    monkeypatch.setattr(host, "_service_runtime", lambda *_args, **_kwargs: runtime)
    route = SimpleNamespace(scope=SimpleNamespace(run_id="original-run"))
    with pytest.raises(RuntimeError, match="startup failed"):
        host._connect_delegate(route)
    assert host._delegate_cleanup_unresolved == [runtime]
    host._delegate_cleanup_unresolved[:] = [runtime] * 16
    with pytest.raises(RiskServiceStartError) as raised:
        host._connect_delegate(route)
    assert raised.value.code == "RISK_SERVICE_GATEWAY_DELEGATE_CLEANUP_UNRESOLVED"


@pytest.mark.parametrize("problem", ["unregister", "registration"])
def test_delegate_unknown_remote_unregister_blocks_next_session(tmp_path, monkeypatch, problem):
    host = RiskServiceHost(load_risk_service_config(setup(tmp_path)))
    class UnregisteredUnknown:
        def start(self, **_kwargs):
            raise RuntimeError("startup failed")
        def close(self, **_kwargs):
            return SimpleNamespace(state="failed", thread_alive=False,
                                   error_code="MAINTENANCE_FAILED",
                                   unregister_uncertainties=int(problem == "unregister"),
                                   owner_cleanup_uncertainties=0,
                                   unknown_registration_count=int(problem == "registration"))
    old = UnregisteredUnknown()
    attempts = []
    def create(*_args, **_kwargs):
        attempts.append(1)
        return old
    monkeypatch.setattr(host, "_service_runtime", create)
    route = SimpleNamespace(scope=SimpleNamespace(run_id="original-run"))
    with pytest.raises(RuntimeError):
        host._connect_delegate(route)
    with pytest.raises(RiskServiceStartError) as raised:
        host._connect_delegate(route)
    assert raised.value.code == "RISK_SERVICE_GATEWAY_DELEGATE_CLEANUP_UNRESOLVED"
    assert attempts == [1] and host._delegate_cleanup_unresolved == [old]


@pytest.mark.parametrize("problem", ["unregister", "registration_unknown", "factory", "alive", "owner", "exception"])
def test_incomplete_host_close_retains_fence_and_allows_retry(tmp_path, problem):
    import gc
    import weakref
    from bt_api_py.risk_service_host import _RETAINED_HOSTS

    config = load_risk_service_config(setup(tmp_path))
    class Runtime:
        resolved = False
        def close(self, **_kwargs):
            if not self.resolved and problem == "exception":
                raise RuntimeError("cleanup unavailable")
            return SimpleNamespace(
                state="failed", thread_alive=not self.resolved and problem == "alive",
                error_code=("FACTORY_FAILED_CLEANUP_UNPROVEN"
                            if not self.resolved and problem == "factory" else "MAINTENANCE_FAILED"),
                unregister_uncertainties=int(not self.resolved and problem == "unregister"),
                owner_cleanup_uncertainties=int(not self.resolved and problem == "owner"),
                unknown_registration_count=int(not self.resolved and problem == "registration_unknown"))
    resource = Runtime()
    host = RiskServiceHost(config)
    host.open_storage()
    host._delegate_cleanup_unresolved.append(resource)
    ref = weakref.ref(host)
    contender = RiskServiceHost(config)
    try:
        assert host.close() is False and host in _RETAINED_HOSTS
        with pytest.raises(RiskServiceStartError) as raised:
            contender.open_storage()
        assert raised.value.code == "RISK_SERVICE_FENCE_HELD"
        del host
        gc.collect()
        host = ref()
        assert host is not None and host in _RETAINED_HOSTS
        resource.resolved = True
        assert host.close() is True and host not in _RETAINED_HOSTS
        contender.open_storage()
    finally:
        resource.resolved = True
        if ref() is not None:
            ref().close()
        contender.close()


def test_incomplete_delegate_port_close_retains_fence(tmp_path):
    config = load_risk_service_config(setup(tmp_path))
    class Port:
        resolved = False
        def close(self):
            return self.resolved
    port = Port()
    host = RiskServiceHost(config)
    host.open_storage()
    host.order_port = port
    contender = RiskServiceHost(config)
    try:
        assert host.close() is False
        with pytest.raises(RiskServiceStartError) as raised:
            contender.open_storage()
        assert raised.value.code == "RISK_SERVICE_FENCE_HELD"
        port.resolved = True
        assert host.close() is True
        contender.open_storage()
    finally:
        port.resolved = True
        host.close()
        contender.close()


def test_run_entry_retries_incomplete_cleanup_before_return(monkeypatch):
    from bt_api_py import risk_service_host as module

    events = []
    class Host:
        def __init__(self, _config):
            self.stop_event = SimpleNamespace(set=lambda: None)
        def start(self):
            events.append("start")
        def run(self):
            events.append("run")
        def close(self):
            events.append("close")
            return events.count("close") == 2
    monkeypatch.setattr(module, "load_risk_service_config", lambda _path: object())
    monkeypatch.setattr(module, "RiskServiceHost", Host)
    monkeypatch.setattr(module.time, "sleep", lambda _delay: events.append("wait"))
    assert module.main(["run", "--config", "synthetic"]) == 0
    assert events == ["start", "run", "close", "wait", "close"]


def test_cli_run_refuses_with_a_finite_code(tmp_path, capsys):
    path = setup(tmp_path)
    raw = json.loads(path.read_text())
    raw["deadlines"]["reservation_ttl_seconds"] = 31
    path.write_text(json.dumps(raw))
    assert main(["run", "--config", str(path)]) == 2
    assert capsys.readouterr().err.strip() == (
        "RISK_SERVICE_START_REFUSED RISK_SERVICE_CONFIG_NUMBER_OUT_OF_RANGE")
