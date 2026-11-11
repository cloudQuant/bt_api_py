"""Cancel-only start (D63-58): command line, application, installed factory and CTP profile wiring."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py import gateway_operator_application as application
from bt_api_py import gateway_operator_cli
from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError, build_ctp_gateway_trading_profile
from bt_api_py.gateway_cancel_only import GatewayCancelOnlyRun
from bt_api_py.gateway_direct_service import GatewayControlGrant
from bt_api_py.gateway_operator import GatewayOperatorDeployment
from bt_api_py.gateway_operator_application import (
    GatewayOperatorApplicationError,
    GatewayOperatorLaunchConfig,
    GatewayOperatorProfileFactory,
    GatewayOperatorProfileRegistry,
)
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.test_iter63_ctp_gateway_trading import make


def test_factory_cancel_only_hook_must_be_callable():
    with pytest.raises(GatewayOperatorApplicationError, match="INVALID_PROFILE"):
        GatewayOperatorProfileFactory("p", "trading_owner", lambda config: None,
                                      create_cancel_only_deployment="not callable")


def test_registry_composes_the_cancel_only_deployment_only_through_the_factory_hook(monkeypatch):
    config = GatewayOperatorLaunchConfig("p", object())
    monkeypatch.setattr(application, "ServiceConfig", object)
    calls = []
    without = GatewayOperatorProfileFactory("p", "trading_owner", lambda cfg: calls.append(("normal", cfg)))
    monkeypatch.setattr(application, "_configured_factory", lambda config: without)
    with pytest.raises(GatewayOperatorApplicationError, match="CANCEL_ONLY_UNSUPPORTED"):
        GatewayOperatorProfileRegistry.from_configured_entry_points(config, cancel_only=("ops", "why"))
    hooked = GatewayOperatorProfileFactory(
        "p", "trading_owner", lambda cfg: calls.append(("normal", cfg)),
        create_cancel_only_deployment=lambda cfg, operator, reason: calls.append(("cancel", cfg, operator, reason)))
    monkeypatch.setattr(application, "_configured_factory", lambda config: hooked)
    for bad in (("ops",), ("ops", ""), ("ops", " padded"), ["ops", "why"], ("ops", 1), ("o" * 257, "why")):
        with pytest.raises(GatewayOperatorApplicationError, match="INVALID_CANCEL_ONLY_REQUEST"):
            GatewayOperatorProfileRegistry.from_configured_entry_points(config, cancel_only=bad)
    GatewayOperatorProfileRegistry.from_configured_entry_points(config).resolve("p").deployment_factory()
    GatewayOperatorProfileRegistry.from_configured_entry_points(
        config, cancel_only=("ops", "why")).resolve("p").deployment_factory()
    assert calls == [("normal", config.service_config), ("cancel", config.service_config, "ops", "why")]


def test_status_carries_the_cancel_only_run_only_when_there_is_one():
    base = {"state": "running", "role": "trading_owner", "ready": True, "error_code": None,
            "operation": {"name": None}, "account_session": None, "cleanup": {"last_cleanup_code": None}}
    assert "cancel_only" not in application._copy_status(base)
    copied = application._copy_status(dict(base, cancel_only={"state": "COMPLETE", "targets": 0}))
    assert dict(copied["cancel_only"]) == {"state": "COMPLETE", "targets": 0}
    run = SimpleNamespace(status=lambda: {"state": "CANCELLING", "targets": 2})
    prop = GatewayOperatorDeployment.cancel_only_status.fget
    assert prop(SimpleNamespace(_host=SimpleNamespace(_gateway_cancel_only_runs=[]))) is None
    assert dict(prop(SimpleNamespace(_host=SimpleNamespace(_gateway_cancel_only_runs=[run])))) == {
        "state": "CANCELLING", "targets": 2}


class _Application:
    """Reports the given run states in turn; a stop takes two more status reads to close."""

    def __init__(self, runs):
        self._runs, self.stops, self._closing = list(runs), 0, None

    def start(self, *, timeout_seconds):
        pass

    @property
    def status(self):
        if self._closing is not None:
            self._closing -= 1
            return {"state": "closed" if self._closing < 0 else "stopping", "cancel_only": self._last}
        self._last = self._runs.pop(0) if len(self._runs) > 1 else self._runs[0]
        return {"state": "running", "cancel_only": self._last}

    def stop(self, *, timeout_seconds):
        self.stops += 1
        if self._closing is None:
            self._closing = 2


def _cli(monkeypatch, capsys, runs, *, service_config=True):
    seen = {}
    config = SimpleNamespace(profile_ref="p", service_config=object() if service_config else None)
    monkeypatch.setattr(gateway_operator_cli, "load_gateway_operator_launch_config", lambda path: config)

    def configured(cfg, *, cancel_only=None):
        seen["cancel_only"] = cancel_only
        return "profiles"

    monkeypatch.setattr(gateway_operator_cli.GatewayOperatorProfileRegistry, "from_configured_entry_points",
                        staticmethod(configured))
    fake = _Application(runs)
    monkeypatch.setattr(gateway_operator_cli.GatewayOperatorApplication, "from_launch_config",
                        staticmethod(lambda cfg, profiles: fake))
    code = gateway_operator_cli.main(["--config", "operator.yaml", "--cancel-only", "--operator", "ops",
                                      "--reason", "gateway down"])
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    return code, lines, seen, fake


@pytest.mark.parametrize("final, expected", [("COMPLETE", 0), ("INCOMPLETE", 3), ("UNAVAILABLE", 3)])
def test_cli_cancel_only_reports_the_run_once_stops_and_sets_the_exit_code(monkeypatch, capsys, final, expected):
    code, lines, seen, fake = _cli(monkeypatch, capsys, [
        {"state": "WAITING_FOR_RETURNS", "targets": 0}, {"state": "CANCELLING", "targets": 2},
        {"state": final, "targets": 2, "observed_terminal": 2 if final == "COMPLETE" else 1}])
    assert code == expected and seen == {"cancel_only": ("ops", "gateway down")} and fake.stops == 1
    reports = [line["cancel_only"] for line in lines if set(line) == {"cancel_only"}]
    assert len(reports) == 1 and reports[0]["state"] == final


def test_cli_cancel_only_needs_a_configured_factory(monkeypatch, capsys):
    code, lines, seen, fake = _cli(monkeypatch, capsys, [{"state": "COMPLETE"}], service_config=False)
    assert code == 2 and seen == {} and fake.stops == 0
    assert lines[-1] == {"state": "failed", "error_code": "CANCEL_ONLY_UNSUPPORTED"}


@pytest.mark.parametrize("argv", [
    ["--config", "c", "--cancel-only"],
    ["--config", "c", "--cancel-only", "--operator", "o"],
    ["--config", "c", "--cancel-only", "--reason", "r"],
    ["--config", "c", "--cancel-only", "--operator", "o", "--reason", "r", "--dry-run"],
    ["--config", "c", "--cancel-only", "--operator", "o", "--reason", "r", "--reconcile-session"],
    ["--config", "c", "--operator", "o", "--reason", "r"],
])
def test_cli_rejects_incomplete_or_mixed_cancel_only_arguments(argv):
    with pytest.raises(SystemExit) as exited:
        gateway_operator_cli.main(argv)
    assert exited.value.code == 2


def _profile(tmp_path, monkeypatch, *, cancel_only, control=True):
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    if control:
        raw['components']['gateway']['settings']['options'].update(control_enabled=True, cancel_enabled=True)
    config = parse_service_config(raw)
    operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'operator-actor')
    operator_scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    operations = frozenset({'direct.control.status'})
    resource = ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, operator_scope, operations)
    if control:
        public, _ = zmq.curve_keypair()
        authority = replace(authority, principals={**authority.principals, public: 'operator'},
                            grants={**authority.grants, 'operator': (PrincipalGrant(operator, (resource,)),)})
    kwargs = {'control_grants': {'operator': GatewayControlGrant('operator-actor', True, frozenset(), operations)},
              'upgrade_controls_from_v9': True} if control else {}
    return settings, lambda: build_ctp_gateway_trading_profile(
        config, authority, tmp_path / 'registry.sqlite', profile_ref='cancel-only', settings=settings,
        grants=grants, request_feed_factory=lambda: None, market_stream_factory=lambda *_: None,
        assert_admitted=lambda _: True, cancel_only=cancel_only, **kwargs)


def test_ctp_profile_stores_the_cause_before_the_host_serves_and_the_host_keeps_the_run(tmp_path, monkeypatch):
    settings, build = _profile(tmp_path, monkeypatch, cancel_only=('ops-1', 'gateway cannot serve'))
    deployment = build().deployment_factory()
    deployment.start()
    try:
        host = deployment._host
        run, = host._gateway_cancel_only_runs
        assert type(run) is GatewayCancelOnlyRun and (run.operator, run.reason) == ('ops-1', 'gateway cannot serve')
        cause = run.owner.submit_service_call(lambda: run.control.get(run.cause_id)).result(timeout_seconds=2)
        assert (cause['mode'], cause['scope_kind'], cause['active']) == ('DRAIN', 'account', 1)
        # The host drives the run on every maintenance pass.
        passes, maintain = [], run.maintain
        run.maintain = lambda: passes.append(1) or maintain()
        # The synthetic journal is below schema 17: reported, nothing cancelled.
        host.serve_once(0)
        assert passes
        status = deployment.cancel_only_status
        assert (status['state'], status['code'], status['cause_id']) == (
            'UNAVAILABLE', 'CANCEL_ONLY_REQUIRES_JOURNAL_17', run.cause_id)
    finally:
        deployment.close()


@pytest.mark.parametrize('bad', [('ops',), ('ops', ''), ('ops', ' padded'), ['ops', 'why'], ('ops', 7), 'ops'])
def test_ctp_profile_refuses_an_invalid_cancel_only_request(tmp_path, monkeypatch, bad):
    _, build = _profile(tmp_path, monkeypatch, cancel_only=bad)
    with pytest.raises(CtpGatewayTradingError, match='CANCEL_ONLY_UNAVAILABLE'):
        build()


def test_ctp_profile_refuses_cancel_only_without_the_control_and_cancel_paths(tmp_path, monkeypatch):
    _, build = _profile(tmp_path, monkeypatch, cancel_only=('ops', 'why'), control=False)
    with pytest.raises(CtpGatewayTradingError, match='CANCEL_ONLY_UNAVAILABLE'):
        build()



def test_the_trading_owner_declares_the_cancel_only_run_as_an_optional_resource():
    """Without it a Gateway that also serves market data refused to start (found on SimNow 7x24)."""
    from bt_api_py.service_components import _ROLE_BUILTINS

    assert "cancel_only" in _ROLE_BUILTINS[("gateway", "trading_owner")].optional_resource_keys
