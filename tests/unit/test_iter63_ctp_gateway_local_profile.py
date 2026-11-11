"""Protected local operator bundle; synthetic values only, no network."""
from __future__ import annotations

import json
import tomllib
from dataclasses import fields
from pathlib import Path

import pytest

from bt_api_py.ctp_gateway_local_profile import (
    CtpLocalProfileError, load_local_ctp_resources, profile_factory,
)
from bt_api_py.gateway_operator import GatewayOperatorDeployment
from tests.test_iter63_ctp_gateway_trading import make


def _scope(scope):
    return {name: list(value) if name == "account_refs" else value
            for name in ("execution_domain_ref", "account_ref", "account_refs", "strategy_ref", "run_id",
                         "manual_book_ref", "portfolio_ref", "group_id", "leg_id")
            for value in (getattr(scope, name),)}


def _identity(value):
    return {"role": value.role.value, "principal_ref": value.principal_ref,
            "actor_ref": value.actor_ref, "delegate_ref": value.delegate_ref}


def _fixture(tmp_path, monkeypatch):
    _, settings, config, authority, direct, _ = make(tmp_path, monkeypatch)
    settings_raw = {item.name: getattr(settings, item.name) for item in fields(settings)}
    settings_raw["account_key"] = settings.account_key.to_dict()
    settings_raw = {key: str(value) if isinstance(value, Path) else value for key, value in settings_raw.items()}
    auth = {name: getattr(authority, name) for name in (
        "scope_ref", "scope_version", "security_ref", "security_version", "registration_policy_ref",
        "registration_policy_version", "service_requirements_ref", "service_requirements_version")}
    auth.update(identity=_identity(authority.identity), scope=_scope(authority.scope),
                curve_public_key=authority.curve_public_key.decode("ascii"),
                curve_secret_key=authority.curve_secret_key.decode("ascii"))
    clients = []
    for public, principal in authority.principals.items():
        entry, = authority.grants[principal]
        clients.append({"identity": _identity(entry.identity), "curve_public_key": public.decode("ascii"),
            "resources": [{"instance_id": r.instance_id, "port": r.port, "scope_ref": r.scope_ref,
                           "scope": _scope(r.scope), "operations": sorted(r.operations)} for r in entry.resources],
            "symbols": sorted(direct[principal].symbols)})
    doc = {"schema_version": 1, "registry_path": str(tmp_path / "registry-local.sqlite"),
        "settings": settings_raw, "authority": auth, "clients": clients, "market": None,
        "credentials": {"broker_id": settings.account_key.subaccount_id, "user_id": settings.account_key.account_id, "password": "synthetic-only",
                        "app_id": "test", "auth_code": "test", "td_front": "tcp://td.invalid:1",
                        "md_front": "tcp://md.invalid:2"}}
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    file = parent / "resource.json"
    file.write_text(json.dumps(doc), encoding="utf-8")
    file.chmod(0o600)
    monkeypatch.setenv("BT_API_CTP_GATEWAY_RESOURCES_FILE", str(file))
    return doc, file, config


def test_protected_bundle_composes_real_inert_disabled_write_profile(tmp_path, monkeypatch):
    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["clients"][0]["resources"][0]["operations"] = ["direct.command_status"]
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_local_ctp_resources(config)
    assert resources.settings.write_enabled is False
    assert resources.assert_admitted(object()) is False
    assert "synthetic-only" not in repr(resources)
    deployment = profile_factory.create_deployment(config)
    assert type(deployment) is GatewayOperatorDeployment
    assert deployment.state == "new" and deployment.gateway_role == "trading_owner"


def test_permissions_symlinks_and_enabled_writes_are_rejected_without_values(tmp_path, monkeypatch):
    doc, file, config = _fixture(tmp_path, monkeypatch)
    file.chmod(0o644)
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_local_ctp_resources(config)
    monkeypatch.delenv("BT_API_CTP_GATEWAY_RESOURCES_FILE")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_local_ctp_resources(config)
    file.chmod(0o600)
    link = file.parent / "resource-link.json"
    link.symlink_to(file)
    monkeypatch.setenv("BT_API_CTP_GATEWAY_RESOURCES_FILE", str(link))
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_local_ctp_resources(config)
    monkeypatch.setenv("BT_API_CTP_GATEWAY_RESOURCES_FILE", str(file))
    doc["settings"]["write_enabled"] = True
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_local_ctp_resources(config)


def test_duplicate_key_and_absent_resources_fail_closed(tmp_path, monkeypatch):
    _, file, config = _fixture(tmp_path, monkeypatch)
    file.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_local_ctp_resources(config)


def test_installed_profile_entrypoint_metadata_is_explicit():
    project = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    entry = project["project"]["entry-points"]["bt_api_py.gateway_operator_profile_factories"]["ctp-local-readonly"]
    assert entry == "bt_api_py.ctp_gateway_local_profile:profile_factory"
    assert profile_factory.profile_ref == "ctp-local-readonly"


def _returns():
    from bt_api_base.market_protocol import MarketInstrumentKey

    key = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612")
    return {"instruments": [key.to_dict()], "query_interval_seconds": 1.0}


def test_simnow_trading_profile_enables_writes_only_for_simnow(tmp_path, monkeypatch):
    from dataclasses import replace

    from bt_api_py.ctp_gateway_local_profile import (
        CtpGatewayWriteRequiresBrokerConfirmation, load_simnow_trading_resources,
        simnow_trading_profile_factory,
    )

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    assert resources.settings.write_enabled is True
    assert resources.assert_admitted(object()) is True
    assert resources.return_settings is not None
    assert "synthetic-only" not in repr(resources)
    deployment = simnow_trading_profile_factory.create_deployment(config)
    assert type(deployment) is GatewayOperatorDeployment and deployment.state == "new"
    # The read-only profile still refuses an enabled-write bundle.
    del doc["returns"]
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_local_ctp_resources(config)
    # INV63-08: another environment with writes refuses before any session.
    doc["returns"] = _returns()
    doc["settings"]["account_key"] = dict(doc["settings"]["account_key"], environment="prod")
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpGatewayWriteRequiresBrokerConfirmation,
                       match="^GATEWAY_WRITE_REQUIRES_BROKER_CONFIRMATION$"):
        load_simnow_trading_resources(config)
    # Missing return instruments: dispatch readiness would be impossible.
    doc["settings"]["account_key"] = dict(doc["settings"]["account_key"], environment="simnow")
    del doc["returns"]
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_simnow_trading_resources(config)


def test_simnow_return_query_timeout_is_optional_and_bounded(tmp_path, monkeypatch):
    """I63-E097: SimNow order queries outlast the 0.5 s default once orders exist."""
    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources
    from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert load_simnow_trading_resources(config).return_settings.query_timeout_seconds == 0.5
    doc["returns"] = dict(_returns(), query_timeout_seconds=3)
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert load_simnow_trading_resources(config).return_settings.query_timeout_seconds == 3.0
    # I63-E130: up to 60 s since the owner no longer waits for the query.
    for invalid in (61, "3", True, None):
        doc["returns"] = dict(_returns(), query_timeout_seconds=invalid)
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises((CtpLocalProfileError, CtpGatewayTradingError)):
            load_simnow_trading_resources(config)
    doc["returns"] = dict(_returns(), query_timeout=3)
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_simnow_trading_resources(config)


def test_trading_profile_entrypoint_metadata_is_explicit():
    from bt_api_py.ctp_gateway_local_profile import simnow_trading_profile_factory

    project = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    group = project["project"]["entry-points"]["bt_api_py.gateway_operator_profile_factories"]
    assert group["ctp-simnow-trading"] == (
        "bt_api_py.ctp_gateway_local_profile:simnow_trading_profile_factory")
    assert simnow_trading_profile_factory.profile_ref == "ctp-simnow-trading"


@pytest.mark.parametrize("profile", ["ctp-local-readonly", "ctp-simnow-trading"])
def test_operator_dry_run_resolves_profile_without_resources(tmp_path, monkeypatch, capsys, profile):
    import importlib.metadata as metadata

    from bt_api_py import gateway_operator_cli

    example = (Path(__file__).resolve().parents[2] / "examples" / "services"
               / "ctp-gateway-local-readonly.example.yaml").read_text(encoding="utf-8")
    config = tmp_path / "operator.yaml"
    config.write_text(example.replace("profile_ref: ctp-local-readonly", f"profile_ref: {profile}"),
                      encoding="utf-8")
    target = {"ctp-local-readonly": "profile_factory",
              "ctp-simnow-trading": "simnow_trading_profile_factory"}[profile]
    point = metadata.EntryPoint(profile, f"bt_api_py.ctp_gateway_local_profile:{target}",
                                "bt_api_py.gateway_operator_profile_factories")
    monkeypatch.setattr(metadata, "entry_points", lambda: metadata.EntryPoints((point,)))
    monkeypatch.delenv("BT_API_CTP_GATEWAY_RESOURCES_FILE", raising=False)
    assert gateway_operator_cli.main(["--config", str(config), "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out == {"mode": "offline_config_validation", "profile": profile, "status": "PASS"}


def _clock():
    return {"servers": ["ntp.example.invalid", "time.example.invalid"], "max_error_bound_ms": 200,
            "max_sample_validity_seconds": 300, "refresh_interval_seconds": 60}


def _market(doc):
    from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey

    key = MarketSubscriptionKey("ctp-native-market-r1", "ctp-quote-v2-entitlement-r1",
                                MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612"),
                                "tick")
    principal = doc["clients"][0]["identity"]["principal_ref"]
    return {"subscriptions": [key.to_dict()], "principal_allowed_indices": {principal: [0]}}


def test_simnow_trading_markets_require_a_verified_gateway_clock(tmp_path, monkeypatch):
    # SimNow 63.09: the installed trading profile never passed a Gateway clock,
    # so with markets every dispatch was refused (MARKET_GATE_UNAVAILABLE).
    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources
    from bt_api_py.gateway_server_clock_refresh import GatewayServerClockRefreshSettings

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    doc["market"] = _market(doc)
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_simnow_trading_resources(config)  # markets without a clock: refused
    doc["clock"] = _clock()
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    assert resources.clock_allowed_servers == frozenset(_clock()["servers"])
    assert type(resources.clock_refresh_settings) is GatewayServerClockRefreshSettings
    assert resources.clock_refresh_settings.max_error_bound_ns == 200_000_000
    assert callable(resources.clock_verifier_factory)
    for bad in ({**_clock(), "servers": []}, {**_clock(), "max_error_bound_ms": 0},
                {**_clock(), "refresh_interval_seconds": 0}, {**_clock(), "extra": 1}):
        doc["clock"] = bad
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
            load_simnow_trading_resources(config)
    # A trading bundle without markets (and without a clock) stays valid.
    doc["market"] = None
    del doc["clock"]
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert load_simnow_trading_resources(config).clock_verifier_factory is None


def test_market_gate_latency_above_two_seconds_only_for_simnow(tmp_path, monkeypatch):
    """User decision 2026-11-10: SimNow ticks lag 1-3.5 s; other venues keep 2 s."""
    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources
    from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    doc["clock"] = _clock()
    doc["market"] = dict(_market(doc), dispatch_max_latency_seconds=5)
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    assert resources.market_settings.dispatch_max_latency_seconds == 5.0
    for invalid in (5.5, 0, "5", True):
        doc["market"] = dict(_market(doc), dispatch_max_latency_seconds=invalid)
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises((CtpLocalProfileError, CtpGatewayTradingError, ValueError)):
            load_simnow_trading_resources(config)
    # The profile admits more than 2 s only for a SimNow account.
    from types import SimpleNamespace

    from bt_api_py.ctp_gateway_trading import _market_gate_latency

    def latency(venue, environment, value):
        account = SimpleNamespace(account_key=SimpleNamespace(venue=venue, environment=environment))
        return _market_gate_latency(account, SimpleNamespace(dispatch_max_latency_seconds=value))

    assert latency("simnow", "simnow", 5.0) == 5.0
    assert latency("SHFE", "prod", 2.0) == 2.0
    for venue, environment in (("SHFE", "prod"), ("simnow", "prod"), ("other", "simnow")):
        with pytest.raises(CtpGatewayTradingError, match="^MARKET_GATE_LATENCY_REQUIRES_SIMNOW$"):
            latency(venue, environment, 2.5)


def test_simnow_strategy_ledger_upgrade_is_optional_and_explicit(tmp_path, monkeypatch):
    """63.09 round trip: closing through the Gateway needs the strategy ledger."""
    from bt_api_py.ctp_gateway_local_profile import (
        load_local_ctp_resources,
        load_simnow_trading_resources,
    )

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert load_simnow_trading_resources(config).upgrade_strategy_ledger_from_v10 is False
    doc["strategy_ledger"] = {"upgrade_from_v10": True}
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert load_simnow_trading_resources(config).upgrade_strategy_ledger_from_v10 is True
    for bad in ({"upgrade_from_v10": 1}, {}, {"upgrade_from_v10": True, "extra": 1}, True):
        doc["strategy_ledger"] = bad
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
            load_simnow_trading_resources(config)
    # The read-only profile never accepts it.
    doc["settings"]["write_enabled"] = False
    del doc["returns"]
    doc["strategy_ledger"] = {"upgrade_from_v10": True}
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_local_ctp_resources(config)


def test_simnow_external_baseline_declaration_is_parsed_not_trusted(tmp_path, monkeypatch):
    """D63-06: the bundle only declares holdings; the Gateway proves them."""
    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources
    from bt_api_py.ctp_gateway_strategy_ledger import ExternalBaselineDeclaration

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    instrument = doc["returns"]["instruments"][0]
    lot = {"instrument": instrument, "direction": "long", "today_quantity": "0", "yesterday_quantity": "1"}
    declared = {"operator": "operator-ops", "operation_id": "baseline-1", "positions": [lot]}
    doc["strategy_ledger"] = {"upgrade_from_v10": False}
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert load_simnow_trading_resources(config).strategy_ledger_external_baseline is None
    doc["strategy_ledger"] = {"upgrade_from_v10": True, "external_baseline": declared}
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    baseline = resources.strategy_ledger_external_baseline
    assert type(baseline) is ExternalBaselineDeclaration and resources.upgrade_strategy_ledger_from_v10 is True
    assert (baseline.operator_ref, baseline.operation_id) == ("operator-ops", "baseline-1")
    assert [(item.instrument.native_symbol, item.direction, item.today_quantity, item.yesterday_quantity)
            for item in baseline.positions] == [("rb2612", "long", "0", "1")]
    for bad in (dict(declared, positions=[]), dict(declared, positions=[dict(lot, direction="net")]),
                dict(declared, positions=[dict(lot, today_quantity=0)]), dict(declared, operator=""),
                dict(declared, extra=1), {"operator": "operator-ops"}, [lot],
                dict(declared, positions=[dict(lot, yesterday_quantity="0")]),
                dict(declared, positions=[lot, lot])):
        doc["strategy_ledger"] = {"upgrade_from_v10": True, "external_baseline": bad}
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
            load_simnow_trading_resources(config)


def test_simnow_account_comparison_is_optional_explicit_and_bounded(tmp_path, monkeypatch):
    """D63-48: the bundle switches the comparison on; nothing is implied by the ledger alone."""
    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    doc["strategy_ledger"] = {"upgrade_from_v10": True}
    file.write_text(json.dumps(doc), encoding="utf-8")
    plain = load_simnow_trading_resources(config)
    assert plain.upgrade_account_extensions_from_v11 is False
    assert plain.strategy_ledger_comparison_refresh_seconds is None
    doc["strategy_ledger"] = {"upgrade_from_v10": False,
                              "account_comparison": {"upgrade_from_v11": True, "refresh_seconds": 30}}
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    assert resources.upgrade_strategy_ledger_from_v10 is False
    assert resources.upgrade_account_extensions_from_v11 is True
    assert resources.strategy_ledger_comparison_refresh_seconds == 30.0
    doc["strategy_ledger"]["account_comparison"] = {"upgrade_from_v11": False, "refresh_seconds": 1}
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    assert (resources.upgrade_account_extensions_from_v11,
            resources.strategy_ledger_comparison_refresh_seconds) == (False, 1.0)
    for bad in ({"upgrade_from_v11": 1, "refresh_seconds": 30}, {"upgrade_from_v11": True},
                {"upgrade_from_v11": True, "refresh_seconds": 0.5},
                {"upgrade_from_v11": True, "refresh_seconds": 121},
                {"upgrade_from_v11": True, "refresh_seconds": "30"},
                {"upgrade_from_v11": True, "refresh_seconds": 30, "extra": 1}, True, []):
        doc["strategy_ledger"]["account_comparison"] = bad
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
            load_simnow_trading_resources(config)


def test_strategy_origin_source_names_only_the_operators_declared_instruments():
    from types import SimpleNamespace

    from bt_api_base.market_protocol import MarketInstrumentKey
    from bt_api_py.ctp_gateway_local_profile import _strategy_origin_source_factory
    from bt_api_py.gateway_strategy_origin_source import GatewayStrategyOriginSource

    rb = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2612", "rb2612")
    source = _strategy_origin_source_factory((rb,))(object(), object(), object())
    assert type(source) is GatewayStrategyOriginSource
    assert source._resolve(None, SimpleNamespace(exchange_id="SHFE", symbol="rb2612")) is rb
    for exchange, symbol in (("CZCE", "rb2612"), ("SHFE", "rb2701"), (None, "rb2612")):
        with pytest.raises(KeyError):
            source._resolve(None, SimpleNamespace(exchange_id=exchange, symbol=symbol))
    assert source.recheck(rb) is True
    assert source.recheck(MarketInstrumentKey("ctp", "SHFE", "simnow", "option", "rb2612", "rb2612")) is False
    assert source.recheck(MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "hc2612", "hc2612")) is False


def _operator(doc):
    import zmq

    scope = dict(doc["clients"][0]["resources"][0]["scope"], strategy_ref=None)
    public, _ = zmq.curve_keypair()
    return {"identity": {"role": "application_client", "principal_ref": "principal-ops",
                         "actor_ref": "operator-ops", "delegate_ref": None},
            "curve_public_key": public.decode("ascii"),
            "resources": [{"instance_id": doc["clients"][0]["resources"][0]["instance_id"],
                           "port": "service.direct_write",
                           "scope_ref": doc["clients"][0]["resources"][0]["scope_ref"], "scope": scope,
                           "operations": ["direct.control.apply", "direct.control.resume",
                                          "direct.control.status"]}],
            "account_scope_allowed": True, "strategies": []}


def test_simnow_trading_bundle_installs_operator_control_grants(tmp_path, monkeypatch):
    # 63.12 through the installed entry point: an operator application client
    # may FREEZE/STOP_NEW/DRAIN and resume; it never receives trading operations.
    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources
    from bt_api_py.gateway_direct_service import GatewayControlGrant

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    operator = _operator(doc)
    doc["controls"] = {"operators": [operator], "upgrade_controls_from_v9": True}
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    grant = resources.control_grants["principal-ops"]
    assert type(grant) is GatewayControlGrant and grant.account_scope_allowed is True
    assert grant.operations == frozenset(operator["resources"][0]["operations"])
    assert resources.upgrade_controls_from_v9 is True
    assert "principal-ops" in set(resources.authority.principals.values())
    assert "principal-ops" not in resources.grants  # no trading grant
    bad_role = dict(operator, identity=dict(operator["identity"], role="strategy_client"))
    trading_op = dict(operator, resources=[dict(operator["resources"][0],
                                                operations=["direct.reserve"])])
    duplicate = dict(operator, curve_public_key=doc["clients"][0]["curve_public_key"])
    for operators, flag in (([bad_role], True), ([trading_op], True), ([duplicate], True),
                            ([], True), ([operator], "yes")):
        doc["controls"] = {"operators": operators, "upgrade_controls_from_v9": flag}
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
            load_simnow_trading_resources(config)


def test_simnow_drain_section_walks_the_journal_to_v17_with_a_declared_origin_source(tmp_path, monkeypatch):
    """63.12: operator-driven cancels of a DRAIN cause need schema 17 and an origin source."""
    from types import SimpleNamespace

    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources
    from bt_api_py.gateway_strategy_origin_source import GatewayStrategyOriginSource

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    operator = _operator(doc)
    doc["controls"] = {"operators": [operator], "upgrade_controls_from_v9": False}
    file.write_text(json.dumps(doc), encoding="utf-8")
    plain = load_simnow_trading_resources(config)
    chain = ("upgrade_account_extensions_from_v11", "upgrade_parent_authority_from_v12",
             "upgrade_account_facts_financial_from_v13", "upgrade_actor_stream_from_v14",
             "upgrade_strategy_origin_from_v15", "upgrade_control_cancel_from_v16")
    assert not any(getattr(plain, name) for name in chain)
    assert plain.strategy_origin_source_factory is None

    for upgrade in (True, False):
        doc["controls"]["drain"] = {"upgrade_journal": upgrade}
        file.write_text(json.dumps(doc), encoding="utf-8")
        resources = load_simnow_trading_resources(config)
        assert [getattr(resources, name) for name in chain] == [upgrade] * 6
        assert resources.upgrade_controls_from_v9 is False
        # The origin source names exactly the bundle's return instruments.
        source = resources.strategy_origin_source_factory(object(), object(), object())
        assert type(source) is GatewayStrategyOriginSource
        declared = resources.return_settings.instruments[0]
        order = SimpleNamespace(exchange_id=declared.venue, symbol=declared.native_symbol)
        assert source._resolve(None, order) is declared and source.recheck(declared) is True

    # The comparison's own upgrade request is kept when the drain section does not ask.
    doc["strategy_ledger"] = {"upgrade_from_v10": False,
                              "account_comparison": {"upgrade_from_v11": True, "refresh_seconds": 30}}
    doc["controls"]["drain"] = {"upgrade_journal": False}
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    assert resources.upgrade_account_extensions_from_v11 is True
    assert resources.upgrade_control_cancel_from_v16 is False
    del doc["strategy_ledger"]

    for bad in ({"upgrade_journal": 1}, {}, {"upgrade_journal": True, "extra": 1}, True, None):
        doc["controls"]["drain"] = bad
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
            load_simnow_trading_resources(config)


def test_simnow_lease_expiry_drain_is_off_unless_listed_and_needs_an_expiry_duration(tmp_path, monkeypatch):
    """D63-28: opt-in per strategy, with a mandatory duration; D63-33: automatic release by default."""
    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources
    from bt_api_py.gateway_lease_drain import GatewayLeaseDrainSettings

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    doc["controls"] = {"operators": [_operator(doc)], "upgrade_controls_from_v9": False}
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert load_simnow_trading_resources(config).lease_drain_settings is None
    strategy = doc["clients"][0]["identity"]["actor_ref"]
    doc["controls"]["lease_drain"] = {"strategies": [strategy], "expiry_seconds": 45}
    file.write_text(json.dumps(doc), encoding="utf-8")
    settings = load_simnow_trading_resources(config).lease_drain_settings
    assert type(settings) is GatewayLeaseDrainSettings
    assert (settings.strategies, settings.expiry_seconds, settings.automatic_resume) == (
        frozenset({strategy}), 45, True)
    doc["controls"]["lease_drain"] = {"strategies": [strategy], "expiry_seconds": 0.5, "automatic_resume": False}
    file.write_text(json.dumps(doc), encoding="utf-8")
    settings = load_simnow_trading_resources(config).lease_drain_settings
    assert (settings.expiry_seconds, settings.automatic_resume) == (0.5, False)
    for bad in ({"strategies": [strategy]},                                  # no duration: no default
                {"strategies": [strategy], "automatic_resume": True},
                {"strategies": [strategy], "expiry_seconds": None},
                {"strategies": [strategy], "expiry_seconds": "45"},
                {"strategies": [strategy], "expiry_seconds": True},
                {"strategies": [strategy], "expiry_seconds": -1},
                {"strategies": [strategy], "expiry_seconds": 86401},
                {"strategies": [], "expiry_seconds": 45},
                {"strategies": [strategy, strategy], "expiry_seconds": 45},
                {"strategies": strategy, "expiry_seconds": 45},
                {"strategies": [""], "expiry_seconds": 45},
                {"strategies": [strategy], "expiry_seconds": 45, "automatic_resume": 1},
                {"strategies": [strategy], "expiry_seconds": 45, "extra": 1},
                [strategy]):
        doc["controls"]["lease_drain"] = bad
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
            load_simnow_trading_resources(config)


_RATES_FILE = """schema_version: 1
currency: CNY
products:
  rb:
    multiplier: 10
    margin_ratio: {long: "0.13", short: "0.13"}
    commission:
      open: {per_lot: "0", ratio: "0.0001"}
      close: {per_lot: "0", ratio: "0.0001"}
      close_today: {per_lot: "0", ratio: "0.0001"}
"""


def test_simnow_strategy_financial_section_is_optional_and_strict(tmp_path, monkeypatch, caplog):
    """D63-53/54/55: fixed budgets, restart-time adjustments and one shared local rates file."""
    import hashlib
    import logging
    from decimal import Decimal

    from bt_api_py.ctp_gateway_local_profile import load_local_ctp_resources, load_simnow_trading_resources
    from bt_api_py.ctp_gateway_strategy_financial import CtpStrategyFinancialSettings

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert load_simnow_trading_resources(config).strategy_financial_settings is None   # off unless present
    strategy = doc["clients"][0]["identity"]["actor_ref"]
    rates = tmp_path / "rates.yaml"
    rates.write_text(_RATES_FILE, encoding="utf-8")
    good = {"rates_file": str(rates), "currency": "CNY", "budgets": {strategy: "600000.00"},
            "adjustments": [{"id": "2026-11-12-a", "strategy": strategy, "amount": "-100000.00"}],
            "refresh_seconds": 20, "price_max_age_seconds": 40}
    doc["strategy_financial"] = good
    file.write_text(json.dumps(doc), encoding="utf-8")
    settings = load_simnow_trading_resources(config).strategy_financial_settings
    assert type(settings) is CtpStrategyFinancialSettings and settings.currency == "CNY"
    assert dict(settings.budgets) == {strategy: Decimal("600000.00")}
    assert settings.adjustments == (("2026-11-12-a", strategy, Decimal("-100000.00")),)
    assert (settings.refresh_seconds, settings.price_max_age_seconds) == (20, 40)
    assert settings.rates.digest == hashlib.sha256(_RATES_FILE.encode()).hexdigest()
    assert settings.rates.for_instrument("rb2701").multiplier == Decimal(10)
    broken = tmp_path / "broken.yaml"
    broken.write_text(_RATES_FILE.replace("multiplier: 10", "multiplier: 0"), encoding="utf-8")
    caplog.set_level(logging.ERROR, logger="bt_api_py.ctp_gateway_local_profile")
    for bad, code in (
            ({**good, "rates_file": "rates.yaml"}, "RATES_FILE_PATH_NOT_ABSOLUTE"),
            ({**good, "rates_file": str(tmp_path / "absent.yaml")}, "RATES_FILE_UNREADABLE"),
            ({**good, "rates_file": str(broken)}, "RATES_MULTIPLIER_INVALID"),
            ({**good, "budgets": {strategy: "600000.00", "nobody": "1.00"}},
             "STRATEGY_FINANCIAL_BUDGET_FOR_UNKNOWN_STRATEGY"),
            ({**good, "budgets": {}}, "INVALID_STRATEGY_FINANCIAL_SETTINGS"),            # no default budget
            ({**good, "currency": "USD"}, "INVALID_STRATEGY_FINANCIAL_SETTINGS"),         # not the rates' currency
            ({name: item for name, item in good.items() if name != "rates_file"},
             "INVALID_STRATEGY_FINANCIAL_SETTINGS"),
            ({**good, "default_budget": "1.00"}, "INVALID_STRATEGY_FINANCIAL_SETTINGS"),
            ([strategy], "INVALID_STRATEGY_FINANCIAL_SETTINGS")):
        caplog.clear()
        doc["strategy_financial"] = bad
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
            load_simnow_trading_resources(config)
        # The log names the faulty part and repeats nothing from the bundle.
        assert [record.getMessage().rsplit(": ", 1)[1] for record in caplog.records] == [code]
        assert not any(text in caplog.text for text in ("600000", str(tmp_path), strategy))
    # The section belongs to the trading profile only.
    doc["strategy_financial"] = good
    doc["settings"]["write_enabled"] = False
    del doc["returns"]
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_local_ctp_resources(config)


def _risk_delegate(doc, strategy, instrument):
    import zmq

    resource = doc["clients"][0]["resources"][0]
    public, _ = zmq.curve_keypair()
    return {"identity": {"role": "service", "principal_ref": "principal-risk", "actor_ref": "risk-service",
                         "delegate_ref": None},
            "curve_public_key": public.decode("ascii"), "kind": "risk", "revision": "risk-grant-1",
            "strategies": {strategy: {"instruments": [instrument],
                                      "operations": ["direct.dispatch", "direct.reserve"]}},
            "resources": [{"instance_id": resource["instance_id"], "port": "service.direct_write",
                           "scope_ref": resource["scope_ref"], "scope": dict(resource["scope"]),
                           "operations": ["direct.delegated_dispatch", "direct.delegated_reserve"]}]}


def test_simnow_delegation_section_is_optional_strict_and_static(tmp_path, monkeypatch, caplog):
    """D63-13/D63-56: Risk delegates are declared in the bundle; D63-37 is off unless listed."""
    import logging

    from bt_api_base.market_protocol import MarketInstrumentKey

    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources
    from bt_api_py.gateway_delegation_service import GatewayDelegationSettings

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert load_simnow_trading_resources(config).delegation_settings is None   # off unless present
    strategy = doc["clients"][0]["identity"]["actor_ref"]
    instrument = doc["returns"]["instruments"][0]
    delegate = _risk_delegate(doc, strategy, instrument)
    doc["delegation"] = {"delegates": [delegate]}
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    settings = resources.delegation_settings
    assert type(settings) is GatewayDelegationSettings
    assert settings.risk_only_submit_strategies == frozenset()
    grant, = settings.grants
    declared = MarketInstrumentKey.from_dict(instrument)
    assert (grant.identity.principal_ref, grant.identity.actor_ref, grant.actor_kind, grant.revision) == (
        "principal-risk", "risk-service", "risk", "risk-grant-1")
    assert (grant.account_scope, grant.strategy_scope) == (resources.settings.account_scope, strategy)
    assert grant.execution_domain_ref == resources.authority.scope.execution_domain_ref
    assert grant.instruments == {declared} and grant.operations == {"direct.reserve", "direct.dispatch"}
    assert settings.instrument_recheck(declared) is True
    assert settings.instrument_recheck(MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "hc2612",
                                                           "hc2612")) is False
    assert "principal-risk" in set(resources.authority.principals.values())
    entry, = resources.authority.grants["principal-risk"]
    assert entry.resources[0].operations == {"direct.delegated_reserve", "direct.delegated_dispatch"}
    assert "principal-risk" not in resources.grants        # a delegate never gets a strategy grant
    doc["delegation"]["risk_only_submit"] = [strategy]
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert load_simnow_trading_resources(config).delegation_settings.risk_only_submit_strategies == {strategy}

    other = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "hc2612", "hc2612").to_dict()
    client = doc["clients"][0]

    def changed(**updates):
        return dict(delegate, **updates)
    caplog.set_level(logging.ERROR, logger="bt_api_py.ctp_gateway_local_profile")
    for bad in (
            {"delegates": [changed(kind="execution")]},                      # needs the parent section
            {"delegates": [changed(identity=dict(delegate["identity"], role="strategy_client"))]},
            {"delegates": [changed(identity=dict(delegate["identity"], principal_ref=client["identity"]["principal_ref"]))]},
            {"delegates": [changed(curve_public_key=client["curve_public_key"])]},
            {"delegates": [changed(strategies={"nobody": delegate["strategies"][strategy]})]},
            {"delegates": [changed(strategies={strategy: {"instruments": [other], "operations": ["direct.reserve"]}})]},
            {"delegates": [changed(strategies={strategy: {"instruments": [instrument], "operations": ["direct.control.apply"]}})]},
            {"delegates": [changed(strategies={strategy: {"instruments": [], "operations": ["direct.reserve"]}})]},
            {"delegates": [changed(strategies={})]},
            {"delegates": [changed(revision="")]},
            {"delegates": [changed(resources=[])]},
            {"delegates": [dict(delegate, extra=1)]},
            {"delegates": [delegate, delegate]},
            {"delegates": []},
            {"delegates": delegate},
            {"delegates": [delegate], "risk_only_submit": []},
            {"delegates": [delegate], "risk_only_submit": strategy},
            {"delegates": [delegate], "risk_only_submit": ["nobody"]},
            {"delegates": [changed(strategies={strategy: {"instruments": [instrument],
                                                         "operations": ["direct.command_status"]}})],
             "risk_only_submit": [strategy]},                                # Risk has no SUBMIT to keep
            {"delegates": [delegate], "extra": 1},
            [delegate]):
        caplog.clear()
        doc["delegation"] = bad
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
            load_simnow_trading_resources(config)
        assert "principal-risk" not in caplog.text and strategy not in caplog.text
    # The section belongs to the trading profile only.
    doc["delegation"] = {"delegates": [delegate]}
    doc["settings"]["write_enabled"] = False
    del doc["returns"]
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_local_ctp_resources(config)


def test_simnow_delegation_bundle_composes_and_enforces_risk_only_submit(tmp_path, monkeypatch):
    """The declared delegate and D63-37 pass the trading builder's own grant checks."""
    from bt_api_py.ctp_gateway_local_profile import simnow_trading_profile_factory
    from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError
    from bt_api_py.service_config import parse_service_config

    doc, file, config = _fixture(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["options"].update(
        control_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True,
        delegation_enabled=True)
    config = parse_service_config(raw)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    doc["controls"] = {"operators": [_operator(doc)], "upgrade_controls_from_v9": True}
    doc["strategy_ledger"] = {"upgrade_from_v10": True,
                              "account_comparison": {"upgrade_from_v11": True, "refresh_seconds": 30}}
    strategy = doc["clients"][0]["identity"]["actor_ref"]
    doc["clients"][0]["symbols"] = [doc["returns"]["instruments"][0]["native_symbol"]]
    doc["delegation"] = {"delegates": [_risk_delegate(doc, strategy, doc["returns"]["instruments"][0])]}
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert simnow_trading_profile_factory.create_deployment(config).state == "new"
    # D63-37 on: the strategy's own grant still holds SUBMIT, so the bundle is refused.
    doc["delegation"]["risk_only_submit"] = [strategy]
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpGatewayTradingError, match="INVALID_TRADING_CONFIGURATION"):
        simnow_trading_profile_factory.create_deployment(config)
    # Without SUBMIT in the strategy's own grant (it keeps release and status) it composes.
    doc["clients"][0]["resources"][0]["operations"] = ["direct.command_status", "direct.release"]
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert simnow_trading_profile_factory.create_deployment(config).state == "new"
    # The operator config must switch delegation on explicitly; the bundle alone does not.
    raw["components"]["gateway"]["settings"]["options"].pop("delegation_enabled")
    with pytest.raises(CtpGatewayTradingError, match="INVALID_TRADING_CONFIGURATION"):
        simnow_trading_profile_factory.create_deployment(parse_service_config(raw))


_PARENT_OPERATIONS = ["direct.parent.cancel", "direct.parent.operation", "direct.parent.query",
                      "direct.parent.register"]


def _execution_delegate(doc, strategy, instrument):
    import zmq

    resource = doc["clients"][0]["resources"][0]
    public, _ = zmq.curve_keypair()
    return {"identity": {"role": "service", "principal_ref": "principal-exec", "actor_ref": "execution-service",
                         "delegate_ref": None},
            "curve_public_key": public.decode("ascii"), "kind": "execution", "revision": "exec-grant-1",
            "strategies": {strategy: {"instruments": [instrument],
                                      "operations": ["direct.dispatch", "direct.reserve"],
                                      "parent_operations": list(_PARENT_OPERATIONS)}},
            "resources": [{"instance_id": resource["instance_id"], "port": "service.direct_write",
                           "scope_ref": resource["scope_ref"], "scope": dict(resource["scope"]),
                           "operations": ["direct.delegated_dispatch", "direct.delegated_reserve",
                                          *_PARENT_OPERATIONS]}]}


def test_simnow_execution_delegate_declares_its_parent_grant_and_is_strict(tmp_path, monkeypatch, caplog):
    """63.13 base (D63-56): an Execution delegate brings a parent grant of the same revision."""
    import logging

    from bt_api_base.market_protocol import MarketInstrumentKey

    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources
    from bt_api_py.gateway_parent_service import GatewayParentSettings

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    strategy = doc["clients"][0]["identity"]["actor_ref"]
    instrument = doc["returns"]["instruments"][0]
    delegate = _execution_delegate(doc, strategy, instrument)
    doc["delegation"] = {"delegates": [delegate]}
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    grant, = resources.delegation_settings.grants
    assert (grant.identity.principal_ref, grant.actor_kind, grant.revision, grant.strategy_scope) == (
        "principal-exec", "execution", "exec-grant-1", strategy)
    assert grant.operations == {"direct.reserve", "direct.dispatch"}
    assert type(resources.parent_settings) is GatewayParentSettings
    parent, = resources.parent_settings.grants
    declared = MarketInstrumentKey.from_dict(instrument)
    assert (parent.identity, parent.account_scope, parent.strategy_scope, parent.execution_domain_ref) == (
        grant.identity, grant.account_scope, strategy, grant.execution_domain_ref)
    assert (parent.actor_kind, parent.revision, parent.instruments) == ("execution", "exec-grant-1", {declared})
    assert parent.operations == frozenset(_PARENT_OPERATIONS)
    assert callable(resources.parent_authority_factory)
    assert resources.upgrade_parent_authority_from_v12 is False
    assert "principal-exec" not in resources.grants      # a delegate never gets a strategy grant
    doc["delegation"]["upgrade_parent_authority_from_v12"] = True
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert load_simnow_trading_resources(config).upgrade_parent_authority_from_v12 is True
    # A Risk-only bundle installs no parent authority at all.
    doc["delegation"] = {"delegates": [_risk_delegate(doc, strategy, instrument)]}
    file.write_text(json.dumps(doc), encoding="utf-8")
    risk_only = load_simnow_trading_resources(config)
    assert risk_only.parent_settings is None and risk_only.parent_authority_factory is None

    def changed(**updates):
        return dict(delegate, **updates)

    def granted(**updates):
        return changed(strategies={strategy: dict(delegate["strategies"][strategy], **updates)})
    risk = _risk_delegate(doc, strategy, instrument)
    no_parent = {name: item for name, item in delegate["strategies"][strategy].items()
                 if name != "parent_operations"}
    caplog.set_level(logging.ERROR, logger="bt_api_py.ctp_gateway_local_profile")
    for bad in (
            {"delegates": [changed(strategies={strategy: no_parent})]},           # parent grant is mandatory
            {"delegates": [dict(risk, strategies={strategy: dict(risk["strategies"][strategy],
                                                                parent_operations=["direct.parent.query"])})]},
            {"delegates": [granted(parent_operations=[])]},
            {"delegates": [granted(parent_operations=["direct.parent.query", "direct.parent.query"])]},
            {"delegates": [granted(parent_operations=["direct.reserve"])]},
            {"delegates": [granted(parent_operations="direct.parent.query")]},
            {"delegates": [changed(kind="monitor")]},
            {"delegates": [changed(identity=dict(delegate["identity"], role="application_client"))]},
            {"delegates": [delegate], "upgrade_parent_authority_from_v12": "yes"},
            {"delegates": [risk], "upgrade_parent_authority_from_v12": True},     # no parent to upgrade for
            {"delegates": [delegate], "risk_only_submit": [strategy]},          # Execution would keep SUBMIT
            {"delegates": [delegate], "parents": {}}):
        caplog.clear()
        doc["delegation"] = bad
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
            load_simnow_trading_resources(config)
        assert "principal-exec" not in caplog.text and strategy not in caplog.text
    # An unknown kind is named in the log (and only the code).
    caplog.clear()
    doc["delegation"] = {"delegates": [changed(kind="monitor")]}
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_simnow_trading_resources(config)
    assert [record.getMessage().rsplit(": ", 1)[1] for record in caplog.records] == ["DELEGATE_KIND_UNSUPPORTED"]


def test_bundle_parent_authority_is_same_day_and_reads_the_session_trading_day(tmp_path):
    from types import SimpleNamespace

    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
    from bt_api_gateway.direct_parent_authority import GatewayParentAuthority, GatewayParentError
    from bt_api_gateway.session_guard import LocalAccountSessionGuard

    from bt_api_py.ctp_gateway_local_profile import _parent_authority_factory

    (tmp_path / "guard").mkdir()
    guard = LocalAccountSessionGuard(tmp_path / "guard", "acct", "owner")
    journal = GatewayDirectCommandJournal.create(tmp_path / "journal", "acct", generation=guard.acquire())
    for enable in (journal.enable_controls, journal.enable_strategy_ledger, journal.enable_account_extensions,
                   journal.enable_parent_authority):
        enable(guard)
    day = {"value": "20300101"}
    session = SimpleNamespace(direct_provider=SimpleNamespace(trading_day=lambda: day["value"]))
    spec = {"account_scope": "acct", "strategy_scope": "strategy", "parent_ref": "parent",
            "registration_operation_id": "register", "execution_domain_ref": "domain",
            "instrument": {"provider": "ctp", "venue": "SHFE", "environment": "simnow", "product_type": "future",
                           "instrument_id": "rb2612", "native_symbol": "rb2612"},
            "quantity": "1", "quantity_unit": "lots", "side": "buy", "offset": "open", "limit_price": "10",
            "creation_trading_day": "20300101", "allowed_trading_days": ["20300101"], "expires_at_ns": 2 ** 62,
            "grant_revision": "v1", "target_position_ref": None, "plan_digest": "c" * 64, "plan_version": "v1",
            "time_in_force": "DAY"}
    access = {"principal": "exec", "actor_ref": "actor", "fence": _null_fence, "recheck": lambda: True}
    try:
        authority = _parent_authority_factory()(journal, guard, session)
        assert type(authority) is GatewayParentAuthority
        with pytest.raises(GatewayParentError, match="CROSS_DAY_UNAVAILABLE"):
            authority.register_parent(dict(spec, allowed_trading_days=["20300101", "20300102"],
                                           time_in_force="GTD"), **access)
        # Another trading day on the live session: registration refused (spec is for 20300101).
        day["value"] = "20300102"
        with pytest.raises(GatewayParentError, match="PARENT_EXPIRED"):
            authority.register_parent(spec, **access)
        # No session yet: the trading day is unknown and nothing is registered.
        missing = _parent_authority_factory()(journal, guard, SimpleNamespace())
        with pytest.raises(GatewayParentError, match="SOURCE_UNAVAILABLE"):
            missing.register_parent(spec, **access)
        day["value"] = "20300101"
        assert authority.register_parent(spec, **access)["state"] == "ACTIVE"
        assert authority._transfer_completed(None, spec) == "0"   # no internal transfer exists
    finally:
        guard.release()


def _null_fence():
    from contextlib import nullcontext

    return nullcontext()


def _composing_config(config, **options):
    from bt_api_py.service_config import parse_service_config

    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["options"].update(
        control_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True, **options)
    return raw, parse_service_config(raw)


def _composing_doc(doc):
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    doc["controls"] = {"operators": [_operator(doc)], "upgrade_controls_from_v9": True}
    doc["strategy_ledger"] = {"upgrade_from_v10": True,
                              "account_comparison": {"upgrade_from_v11": True, "refresh_seconds": 30}}
    doc["clients"][0]["symbols"] = [doc["returns"]["instruments"][0]["native_symbol"]]


def test_simnow_execution_delegate_bundle_composes_with_parent_authority_enabled(tmp_path, monkeypatch):
    from bt_api_py.ctp_gateway_local_profile import simnow_trading_profile_factory
    from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError
    from bt_api_py.service_config import parse_service_config

    doc, file, config = _fixture(tmp_path, monkeypatch)
    raw, config = _composing_config(config, delegation_enabled=True, parent_authority_enabled=True)
    _composing_doc(doc)
    strategy = doc["clients"][0]["identity"]["actor_ref"]
    doc["delegation"] = {"delegates": [_execution_delegate(doc, strategy, doc["returns"]["instruments"][0])]}
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert simnow_trading_profile_factory.create_deployment(config).state == "new"
    # The operator config must switch the parent authority on explicitly; the bundle alone does not.
    raw["components"]["gateway"]["settings"]["options"].pop("parent_authority_enabled")
    with pytest.raises(CtpGatewayTradingError, match="INVALID_TRADING_CONFIGURATION"):
        simnow_trading_profile_factory.create_deployment(parse_service_config(raw))


def _observation(doc, tmp_path, observers):
    return {"read_journal_path": str(tmp_path / "account-read.sqlite"),
            "observation_identity_path": str(tmp_path / "observation-identity.sqlite"),
            "expected_observation_issuer_id": "issuer-a", "state_scope": "account-state",
            "required_coverage": [{"product_type": "future", "margin_pool": "main"}],
            "observers": observers}


def _observer(doc):
    import zmq

    resource = doc["clients"][0]["resources"][0]
    public, _ = zmq.curve_keypair()
    return {"identity": {"role": "service", "principal_ref": "principal-observer", "actor_ref": "observer-service",
                         "delegate_ref": None},
            "curve_public_key": public.decode("ascii"),
            "resources": [{"instance_id": resource["instance_id"], "port": "service.account_read",
                           "scope_ref": resource["scope_ref"],
                           "scope": dict(resource["scope"], strategy_ref=None, run_id=None),
                           "operations": ["account.events.ack", "account.events.progress",
                                          "account.events.read_after"]}]}


def test_simnow_account_observation_section_is_optional_and_strict(tmp_path, monkeypatch):
    from bt_api_base.account_protocol import AccountResourceKey
    from bt_api_base.market_protocol import MarketInstrumentKey

    from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources
    from bt_api_py.ctp_gateway_trading import CtpGatewayAccountReadSettings

    doc, file, config = _fixture(tmp_path, monkeypatch)
    doc["settings"]["write_enabled"] = True
    doc["returns"] = _returns()
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    assert resources.account_read_settings is None and resources.account_read_principals == frozenset()
    observer = _observer(doc)
    doc["account_observation"] = _observation(doc, tmp_path, [observer])
    file.write_text(json.dumps(doc), encoding="utf-8")
    resources = load_simnow_trading_resources(config)
    read = resources.account_read_settings
    assert type(read) is CtpGatewayAccountReadSettings
    assert (read.journal_path, read.observation_identity_path) == (
        str(tmp_path / "account-read.sqlite"), str(tmp_path / "observation-identity.sqlite"))
    assert (read.state_scope, read.expected_observation_issuer_id) == ("account-state", "issuer-a")
    assert read.required_coverage == (AccountResourceKey("future", "main"),)
    assert read.observation_instruments == tuple(MarketInstrumentKey.from_dict(item)
                                                 for item in doc["returns"]["instruments"])
    assert (read.refresh_interval_seconds, read.max_staleness_seconds, read.request_timeout_seconds) == (
        1.0, 5.0, 2.0)
    assert resources.account_read_principals == {"principal-observer"}
    entry, = resources.authority.grants["principal-observer"]
    assert {resource.port for resource in entry.resources} == {"service.account_read"}
    assert "principal-observer" not in resources.grants and "principal-observer" not in (
        resources.control_grants or {})
    # Timings are optional and bounded by the read settings themselves.
    doc["account_observation"].update(refresh_interval_seconds=2, max_staleness_seconds=10,
                                      request_timeout_seconds=3)
    file.write_text(json.dumps(doc), encoding="utf-8")
    read = load_simnow_trading_resources(config).account_read_settings
    assert (read.refresh_interval_seconds, read.max_staleness_seconds, read.request_timeout_seconds) == (
        2.0, 10.0, 3.0)
    # A delegate that also lists an account read resource becomes a reader too.
    strategy = doc["clients"][0]["identity"]["actor_ref"]
    risk = _risk_delegate(doc, strategy, doc["returns"]["instruments"][0])
    risk["resources"].append(dict(observer["resources"][0]))
    doc["delegation"] = {"delegates": [risk]}
    doc["account_observation"] = _observation(doc, tmp_path, [])
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert load_simnow_trading_resources(config).account_read_principals == {"principal-risk"}
    del doc["delegation"]

    def changed(**updates):
        return dict(observer, **updates)

    def resource(**updates):
        return changed(resources=[dict(observer["resources"][0], **updates)])
    good = _observation(doc, tmp_path, [observer])
    client = doc["clients"][0]
    for bad in (
            dict(good, observers=[]),                                              # nobody would read
            dict(good, observers=[changed(identity=dict(observer["identity"], role="strategy_client"))]),
            dict(good, observers=[changed(identity=dict(observer["identity"],
                                                        principal_ref=client["identity"]["principal_ref"]))]),
            dict(good, observers=[changed(curve_public_key=client["curve_public_key"])]),
            dict(good, observers=[resource(port="service.direct_write")]),
            dict(good, observers=[changed(resources=[observer["resources"][0],      # reads, but also a
                                                     dict(observer["resources"][0],  # write-port grant
                                                          port="service.direct_write")])]),
            dict(good, observers=[resource(operations=["account.events.read_after", "direct.reserve"])]),
            dict(good, observers=[resource(operations=["account.facts.snapshot"])]),
            dict(good, observers=[resource(scope=client["resources"][0]["scope"])]),  # strategy-scoped
            dict(good, observers=[changed(resources=[])]),
            dict(good, observers=[dict(observer, extra=1)]),
            dict(good, observers=[observer, observer]),
            dict(good, read_journal_path="account-read.sqlite"),                  # not absolute
            dict(good, required_coverage=[]),
            dict(good, required_coverage=[{"product_type": "future"}]),
            dict(good, refresh_interval_seconds="1"),
            dict(good, refresh_interval_seconds=0),
            {name: item for name, item in good.items() if name != "state_scope"},
            dict(good, extra=1),
            [observer]):
        doc["account_observation"] = bad
        file.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
            load_simnow_trading_resources(config)
    # The section belongs to the trading profile only.
    doc["account_observation"] = good
    doc["settings"]["write_enabled"] = False
    del doc["returns"]
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError, match="^CTP_LOCAL_RESOURCES_INVALID$"):
        load_local_ctp_resources(config)


def test_simnow_account_observation_bundle_composes_with_the_read_port_bound(tmp_path, monkeypatch):
    from bt_api_py.ctp_gateway_local_profile import simnow_trading_profile_factory
    from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError
    from bt_api_py.service_config import parse_service_config

    doc, file, config = _fixture(tmp_path, monkeypatch)
    raw, _ = _composing_config(config)
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.account_read")
    _composing_doc(doc)
    doc["account_observation"] = _observation(doc, tmp_path, [_observer(doc)])
    file.write_text(json.dumps(doc), encoding="utf-8")
    assert simnow_trading_profile_factory.create_deployment(parse_service_config(raw)).state == "new"
    # Without the read port in the operator config the bundle is refused at composition.
    raw["components"]["gateway"]["settings"]["bind_ports"].remove("service.account_read")
    with pytest.raises(CtpGatewayTradingError, match="INVALID_TRADING_CONFIGURATION"):
        simnow_trading_profile_factory.create_deployment(parse_service_config(raw))
