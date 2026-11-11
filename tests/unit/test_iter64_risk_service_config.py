"""Risk service configuration ``bt_api_risk.service.v1`` (64.09). UNIT evidence, temp files only."""

from __future__ import annotations

import copy
import json
import os
import stat
import sys

import pytest
import zmq

from bt_api_py.risk_service_config import (
    RiskServiceConfigError,
    ensure_private_directory,
    load_document,
    load_risk_service_config,
    parse_risk_service_config,
)

KEYS = ("risk_server", "strategy", "operator", "gateway_server", "risk_delegate", "risk_control")


def write_keys(root):
    key_dir = root / "keys"
    key_dir.mkdir(exist_ok=True)
    pairs = {}
    for name in KEYS:
        public, secret = zmq.curve_keypair()
        pairs[name] = (public, secret)
        (key_dir / (name + ".public")).write_bytes(public)
        secret_path = key_dir / (name + ".secret")
        secret_path.write_bytes(secret)
        os.chmod(secret_path, 0o600)
    return pairs


def document():
    return {
        "schema": "bt_api_risk.service.v1",
        "service": {
            "service_id": "risk-service", "principal_ref": "risk-host", "instance_id": "risk-a",
            "scope_ref": "risk-scope", "rpc_bind": "tcp://127.0.0.1:7210",
            "public_key_file": "keys/risk_server.public",
            "secret_key_file": "keys/risk_server.secret",
        },
        "account": {
            "route": "CTP___FUTURE", "provider": "ctp", "environment": "simnow",
            "account_scope": "account-a", "execution_domain_ref": "paper:demo",
            "instruments": {"rb2701": {"venue": "SHFE", "native_symbol": "rb2701",
                                       "product_type": "future", "multiplier": "10",
                                       "tick_size": "1"}},
        },
        "storage": {"directory": "state"},
        "policy": {"file": "risk_policy.yaml"},
        "principals": [
            {"principal_ref": "principal-a", "role": "strategy", "actor_ref": "strategy-a",
             "strategy_refs": ["strategy-a"],
             "operations": ["risk.order.reserve", "risk.order.dispatch", "risk.order.release",
                            "risk.order.status", "risk.status"],
             "public_key_file": "keys/strategy.public"},
            {"principal_ref": "operator-1", "role": "operator", "actor_ref": "ops",
             "strategy_refs": [], "operations": ["risk.admin.status", "risk.latch.release"],
             "public_key_file": "keys/operator.public"},
        ],
        "gateway": {
            "service_id": "gateway-service", "principal_ref": "gateway-principal",
            "instance_id": "gateway-a", "scope_ref": "scope-a",
            "rpc_endpoint": "tcp://127.0.0.1:7200",
            "server_public_key_file": "keys/gateway_server.public",
            "delegate": {"principal_ref": "risk-principal", "actor_ref": "risk-delegate",
                         "public_key_file": "keys/risk_delegate.public",
                         "secret_key_file": "keys/risk_delegate.secret",
                         "grant_revisions": {"strategy-a": "risk-v1"}},
            "control": {"principal_ref": "risk-control", "actor_ref": "risk-control-actor",
                        "public_key_file": "keys/risk_control.public",
                        "secret_key_file": "keys/risk_control.secret"},
        },
        "deadlines": {"call_timeout_seconds": 5, "reservation_ttl_seconds": "10"},
        "refresh": {"loop_timeout_ms": 20, "observation_poll_seconds": "0.2",
                    "maintain_seconds": 1},
    }


def parse(root, doc):
    return parse_risk_service_config(doc, path=root / "risk_service.yaml")


def code_of(root, doc):
    with pytest.raises(RiskServiceConfigError) as raised:
        parse(root, doc)
    return raised.value.code


def test_full_document_parses_with_paths_relative_to_the_config_file(tmp_path):
    pairs = write_keys(tmp_path)
    config = parse(tmp_path, document())
    assert config.storage_directory == tmp_path / "state"
    assert config.policy_file == tmp_path / "risk_policy.yaml"
    assert config.risk_database == tmp_path / "state" / "risk.sqlite"
    assert config.reservation_ttl_seconds == 10.0 and config.call_timeout_seconds == 5.0
    assert config.principals["principal-a"].public_key == pairs["strategy"][0]
    assert config.gateway.delegate.secret_key == pairs["risk_delegate"][1]
    binding = config.instrument_for("SHFE", "rb2701")
    assert binding.instrument_id == "rb2701" and binding.key.native_symbol == "rb2701"
    assert config.instrument_for("SHFE", "hc2701") is None
    # Key material never appears in a representation.
    text = repr(config) + repr(config.gateway) + repr(config.principals["principal-a"])
    for public, secret in pairs.values():
        assert public.decode() not in text and secret.decode() not in text


def test_yaml_file_with_home_and_environment_expansion(tmp_path, monkeypatch):
    write_keys(tmp_path)
    monkeypatch.setenv("RISK_IT_STATE", str(tmp_path / "elsewhere"))
    doc = document()
    doc["storage"]["directory"] = "${RISK_IT_STATE}/state"
    path = tmp_path / "risk_service.yaml"
    import yaml

    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    config = load_risk_service_config(path)
    assert config.storage_directory == tmp_path / "elsewhere" / "state"
    monkeypatch.setenv("HOME", str(tmp_path))
    doc["storage"]["directory"] = "~/home-state"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert load_risk_service_config(path).storage_directory == tmp_path / "home-state"


def test_unresolved_environment_variable_is_refused(tmp_path, monkeypatch):
    write_keys(tmp_path)
    monkeypatch.delenv("RISK_IT_UNSET", raising=False)
    doc = document()
    doc["storage"]["directory"] = "$RISK_IT_UNSET/state"
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_PATH_UNRESOLVED"


@pytest.mark.parametrize(
    "path, value, code",
    [
        (("extra",), 1, "RISK_SERVICE_CONFIG_KEY_UNKNOWN"),
        (("service", "extra"), 1, "RISK_SERVICE_CONFIG_KEY_UNKNOWN"),
        (("gateway", "delegate", "extra"), 1, "RISK_SERVICE_CONFIG_KEY_UNKNOWN"),
        (("refresh", "unknown_interval"), 1, "RISK_SERVICE_CONFIG_KEY_UNKNOWN"),
        (("schema",), "bt_api_risk.service.v2", "RISK_SERVICE_CONFIG_SCHEMA_UNSUPPORTED"),
        (("deadlines", "reservation_ttl_seconds"), 31, "RISK_SERVICE_CONFIG_NUMBER_OUT_OF_RANGE"),
        (("deadlines", "reservation_ttl_seconds"), 0, "RISK_SERVICE_CONFIG_NUMBER_INVALID"),
        (("deadlines", "call_timeout_seconds"), 2.5, "RISK_SERVICE_CONFIG_FLOAT_REFUSED"),
        (("deadlines", "call_timeout_seconds"), True, "RISK_SERVICE_CONFIG_NUMBER_INVALID"),
        (("refresh", "loop_timeout_ms"), "20", "RISK_SERVICE_CONFIG_NUMBER_INVALID"),
    ],
)
def test_unknown_keys_bad_numbers_and_schema_are_refused(tmp_path, path, value, code):
    write_keys(tmp_path)
    doc = document()
    target = doc
    for name in path[:-1]:
        target = target[name]
    target[path[-1]] = value
    assert code_of(tmp_path, doc) == code


@pytest.mark.parametrize("section, key", [("deadlines", "call_timeout_seconds"),
                                          ("refresh", "maintain_seconds"),
                                          ("gateway", "control"), ("storage", "directory")])
def test_required_values_have_no_default(tmp_path, section, key):
    write_keys(tmp_path)
    doc = document()
    del doc[section][key]
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_KEY_MISSING"


def test_yaml_float_and_duplicate_keys_are_refused():
    with pytest.raises(RiskServiceConfigError) as raised:
        load_document("a: 0.2\n")
    assert raised.value.code == "RISK_SERVICE_CONFIG_FLOAT_REFUSED"
    with pytest.raises(RiskServiceConfigError) as raised:
        load_document("a: 1\na: 2\n")
    assert raised.value.code == "RISK_SERVICE_CONFIG_DUPLICATE_KEY"
    with pytest.raises(RiskServiceConfigError) as raised:
        load_document('{"a": 1, "a": 2}')
    assert raised.value.code == "RISK_SERVICE_CONFIG_DUPLICATE_KEY"
    with pytest.raises(RiskServiceConfigError) as raised:
        load_document('{"a": 1.5}')
    assert raised.value.code == "RISK_SERVICE_CONFIG_FLOAT_REFUSED"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_secret_key_files_must_be_owner_only(tmp_path):
    write_keys(tmp_path)
    os.chmod(tmp_path / "keys" / "risk_delegate.secret", 0o640)
    assert code_of(tmp_path, document()) == "RISK_SERVICE_KEY_FILE_PERMISSIONS"
    os.chmod(tmp_path / "keys" / "risk_delegate.secret", 0o600)
    (tmp_path / "keys" / "strategy.public").write_bytes(b"short")
    assert code_of(tmp_path, document()) == "RISK_SERVICE_KEY_FILE_INVALID"
    (tmp_path / "keys" / "strategy.public").unlink()
    assert code_of(tmp_path, document()) == "RISK_SERVICE_KEY_FILE_UNAVAILABLE"


def test_principal_table_roles_operations_and_grants(tmp_path):
    write_keys(tmp_path)
    doc = document()
    doc["principals"][0]["strategy_refs"] = ["strategy-a", "strategy-b"]
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_OPERATIONS_INVALID"
    doc = document()
    doc["principals"][0]["operations"].append("risk.policy.publish")
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_OPERATIONS_INVALID"
    doc = document()
    doc["principals"][1]["operations"].append("risk.order.reserve")
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_OPERATIONS_INVALID"
    doc = document()
    doc["principals"][1]["role"] = "admin"
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_ROLE_INVALID"
    doc = document()
    doc["principals"][1]["public_key_file"] = "keys/strategy.public"
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_PRINCIPAL_DUPLICATE"
    doc = document()
    doc["gateway"]["delegate"]["grant_revisions"] = {"strategy-b": "risk-v1"}
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_GRANT_REVISION_MISSING"
    doc = document()
    doc["gateway"]["control"]["public_key_file"] = "keys/risk_delegate.public"
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_GATEWAY_IDENTITY_REUSED"


def test_optional_sources_need_their_refresh_intervals(tmp_path):
    write_keys(tmp_path)
    doc = document()
    doc["gateway"]["market"] = {"source_ref": "ctp-md", "entitlement_ref": "e", "channel": "tick",
                                "instruments": ["rb2701"]}
    doc["gateway"]["market_endpoint"] = "tcp://127.0.0.1:7201"
    assert code_of(tmp_path, copy.deepcopy(doc)) == "RISK_SERVICE_CONFIG_REFRESH_INVALID"
    doc["refresh"]["market_poll_seconds"] = "0.5"
    assert parse(tmp_path, doc).gateway.market.instrument_ids == ("rb2701",)
    del doc["gateway"]["market_endpoint"]
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_MARKET_ENDPOINT_REQUIRED"
    doc = document()
    doc["refresh"]["strategy_financial_seconds"] = 5
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_REFRESH_INVALID"
    doc["refresh"]["strategy_financial_max_age_seconds"] = 15
    config = parse(tmp_path, doc)
    assert config.strategy_financial_seconds == 5.0
    assert config.strategy_financial_max_age_seconds == 15.0


def test_trusted_rules_shape(tmp_path):
    write_keys(tmp_path)
    doc = document()
    doc["rules"] = {"trusted_implementations": {"pkg.module:Rule": 2}}
    assert dict(parse(tmp_path, doc).trusted_rules) == {"pkg.module:Rule": 2}
    doc["rules"] = {"trusted_implementations": {"no-colon": 1}}
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_RULES_INVALID"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_storage_directory_is_created_owner_only(tmp_path):
    target = tmp_path / "a" / "state"
    ensure_private_directory(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    (tmp_path / "file").write_text("x")
    with pytest.raises(RiskServiceConfigError) as raised:
        ensure_private_directory(tmp_path / "file")
    assert raised.value.code == "RISK_SERVICE_STORAGE_INVALID"


def test_config_module_does_not_import_the_risk_core():
    import subprocess

    probe = (
        "import sys, bt_api_py.risk_service_config, bt_api_py.risk_service_component, "
        "bt_api_py.risk_service_gateway; "
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('bt_api_risk'))))"
    )
    out = subprocess.run(
        [sys.executable, "-c", "import json; " + probe], capture_output=True, text=True, check=True
    )
    assert json.loads(out.stdout) == []
