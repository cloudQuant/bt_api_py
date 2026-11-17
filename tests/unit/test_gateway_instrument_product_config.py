"""Explicit operator product identities and resource grants; synthetic values only."""

import json

import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey

from bt_api_py.ctp_gateway_local_profile import (
    CtpLocalProfileError,
    load_local_ctp_resources,
    load_simnow_trading_resources,
)
from bt_api_py.gateway_instrument_product_codec import OPERATIONS
from bt_api_py.service_config import parse_service_config
from tests.unit.test_iter63_ctp_gateway_local_profile import _fixture, _returns

KEY = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb2701", "rb2701")


def configured(tmp_path, monkeypatch):
    doc, file, original = _fixture(tmp_path, monkeypatch)
    doc["returns"] = _returns()
    scope = dict(doc["clients"][0]["resources"][0]["scope"], strategy_ref=None, run_id=None)
    public, _ = zmq.curve_keypair()
    doc["instrument_product_observers"] = {
        "clients": [
            {
                "identity": {
                    "role": "service",
                    "principal_ref": "monitor-p",
                    "actor_ref": "monitor-a",
                    "delegate_ref": None,
                },
                "curve_public_key": public.decode(),
                "resources": [
                    {
                        "instance_id": "gateway-a",
                        "port": "service.instrument_product_read",
                        "scope_ref": "scope-a",
                        "scope": scope,
                        "operations": sorted(OPERATIONS),
                    }
                ],
            }
        ],
        "grants": [
            {
                "principal_ref": "monitor-p",
                "actor_ref": "monitor-a",
                "execution_domain_ref": scope["execution_domain_ref"],
                "account_ref": scope["account_ref"],
                "instruments": [KEY.to_dict()],
            }
        ],
    }
    raw = original.to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.instrument_product_read")
    file.write_text(json.dumps(doc), encoding="utf-8")
    return doc, file, parse_service_config(raw)


def test_monitor_product_config_is_independent_of_account_read(tmp_path, monkeypatch):
    doc, file, config = configured(tmp_path, monkeypatch)
    resources = load_simnow_trading_resources(config)
    assert resources.account_read_settings is None
    assert len(resources.instrument_product_grants) == 1
    assert {r.port for r in resources.authority.grants["monitor-p"][0].resources} == {
        "service.instrument_product_read"
    }


@pytest.mark.parametrize(
    "case",
    [
        "missing_grants",
        "missing_clients",
        "duplicate_key",
        "duplicate_principal",
        "duplicate_actor",
        "role",
        "delegate",
        "account",
        "domain",
        "actor",
        "empty_instruments",
        "partial_operations",
        "direct_port",
        "extra_scope",
        "disabled_port",
        "other_provider",
    ],
)
def test_bad_product_observer_configuration_rejected(tmp_path, monkeypatch, case):
    doc, file, config = configured(tmp_path, monkeypatch)
    section = doc["instrument_product_observers"]
    client = section["clients"][0]
    grant = section["grants"][0]
    if case == "missing_grants":
        del section["grants"]
    elif case == "missing_clients":
        del section["clients"]
    elif case == "duplicate_key":
        client["curve_public_key"] = doc["clients"][0]["curve_public_key"]
    elif case == "duplicate_principal":
        client["identity"]["principal_ref"] = doc["clients"][0]["identity"]["principal_ref"]
    elif case == "duplicate_actor":
        client["identity"]["actor_ref"] = grant["actor_ref"] = doc["clients"][0]["identity"][
            "actor_ref"
        ]
    elif case == "role":
        client["identity"]["role"] = "strategy_client"
    elif case == "delegate":
        client["identity"]["delegate_ref"] = "other"
    elif case in {"account", "domain", "actor"}:
        grant[
            {"account": "account_ref", "domain": "execution_domain_ref", "actor": "actor_ref"}[case]
        ] = "other"
    elif case == "empty_instruments":
        grant["instruments"] = []
    elif case == "partial_operations":
        client["resources"][0]["operations"] = ["instrument.product.begin.v1"]
    elif case == "direct_port":
        client["resources"][0]["port"] = "service.direct_write"
    elif case == "extra_scope":
        client["resources"][0]["scope"]["strategy_ref"] = "other"
    elif case == "other_provider":
        grant["instruments"][0]["provider"] = "other"
    elif case == "disabled_port":
        raw = config.to_public_dict()
        raw["components"]["gateway"]["settings"]["bind_ports"].remove(
            "service.instrument_product_read"
        )
        config = parse_service_config(raw)
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError):
        load_simnow_trading_resources(config)


def test_readonly_profile_rejects_product_observers(tmp_path, monkeypatch):
    doc, file, config = configured(tmp_path, monkeypatch)
    del doc["returns"]
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError):
        load_local_ctp_resources(config)


def configured_shared(tmp_path, monkeypatch):
    doc, file, config = configured(tmp_path, monkeypatch)
    product = doc["instrument_product_observers"]
    item = product["clients"][0]
    scope = item["resources"][0]["scope"]
    calendar_client = {
        "identity": dict(item["identity"]),
        "curve_public_key": item["curve_public_key"],
        "resources": [
            {
                "instance_id": "gateway-a",
                "port": "service.calendar_read",
                "scope_ref": "scope-a",
                "scope": dict(scope),
                "operations": ["calendar.monitor.windows.v1"],
            }
        ],
    }
    grant = {
        "principal_ref": "monitor-p",
        "actor_ref": "monitor-a",
        "execution_domain_ref": scope["execution_domain_ref"],
        "account_ref": scope["account_ref"],
        "route": "CTP___FUTURE",
        "venue": "SIM",
        "kind": "future",
        "product": "x",
        "contract": "x2601",
    }
    doc["calendars"] = {
        "root": "calendars",
        "mode": "SIMULATION_ONLY",
        "route": "CTP___FUTURE",
        "grants": [],
        "range_policies": [
            {
                "policy_id": "policy",
                "route": "CTP___FUTURE",
                "venue": "SIM",
                "kind": "future",
                "product": "x",
                "contract": "x2601",
                "max_advance_days": 0,
                "max_spill_days": 0,
            }
        ],
        "simulation_bootstrap": None,
        "monitor_clients": [calendar_client],
        "monitor_grants": [grant],
    }
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["bind_ports"].append("service.calendar_read")
    file.write_text(json.dumps(doc), encoding="utf-8")
    return doc, file, parse_service_config(raw)


def test_same_exact_monitor_identity_can_hold_both_independent_resources(tmp_path, monkeypatch):
    _, _, config = configured_shared(tmp_path, monkeypatch)
    result = load_simnow_trading_resources(config)
    resources = result.authority.grants["monitor-p"][0].resources
    assert {item.port for item in resources} == {
        "service.instrument_product_read",
        "service.calendar_read",
    }
    assert result.account_read_settings is None


@pytest.mark.parametrize(
    "case",
    [
        "key_changed",
        "principal_changed",
        "actor_changed",
        "scope_changed",
        "instance_changed",
        "scope_ref_changed",
        "duplicate_calendar",
        "duplicate_product",
        "duplicate_port",
    ],
)
def test_shared_monitor_rejects_mismatch_and_duplicates(tmp_path, monkeypatch, case):
    doc, file, config = configured_shared(tmp_path, monkeypatch)
    calendar = doc["calendars"]["monitor_clients"][0]
    if case == "key_changed":
        calendar["curve_public_key"] = zmq.curve_keypair()[0].decode()
    elif case == "principal_changed":
        calendar["identity"]["principal_ref"] = "other"
    elif case == "actor_changed":
        calendar["identity"]["actor_ref"] = "other"
    elif case == "scope_changed":
        calendar["resources"][0]["scope"]["account_ref"] = "other"
    elif case == "instance_changed":
        calendar["resources"][0]["instance_id"] = "other"
    elif case == "scope_ref_changed":
        calendar["resources"][0]["scope_ref"] = "other"
    elif case == "duplicate_calendar":
        doc["calendars"]["monitor_clients"].append(calendar.copy())
    elif case == "duplicate_product":
        doc["instrument_product_observers"]["clients"].append(
            doc["instrument_product_observers"]["clients"][0].copy()
        )
    elif case == "duplicate_port":
        calendar["resources"].append(calendar["resources"][0].copy())
    file.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CtpLocalProfileError):
        load_simnow_trading_resources(config)
