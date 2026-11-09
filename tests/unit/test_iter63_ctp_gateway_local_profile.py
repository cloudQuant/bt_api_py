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
