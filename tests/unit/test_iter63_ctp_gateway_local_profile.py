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
    for invalid in (6, "3", True, None):
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
