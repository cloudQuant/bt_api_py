"""Risk service configuration ``bt_api_risk.service.v1`` (64.09). UNIT evidence, temp files only."""

from __future__ import annotations

import copy
import json
import os
import stat
import sys
from decimal import Inexact, Rounded, localcontext
from pathlib import Path

import pytest
import zmq

import bt_api_py.risk_service_config as risk_config
from bt_api_py.risk_service_config import (
    RiskServiceConfigError,
    ensure_private_directory,
    load_document,
    load_risk_service_config,
    parse_risk_service_config,
)

KEYS = ("risk_server", "strategy", "operator", "gateway_server", "risk_delegate", "risk_control")

# Synthetic config location used with monkeypatched key readers. It must be an
# absolute path on the host platform: the SDK's Windows path guard rejects
# root-relative paths such as ``/synthetic`` before field validation runs.
SYNTHETIC_CONFIG_PATH = (
    Path("C:/synthetic/risk_service.yaml") if os.name == "nt"
    else Path("/synthetic/risk_service.yaml")
)


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
        "deadlines": {"call_timeout_seconds": 5, "reservation_ttl_seconds": "10",
                      "dispatch_recheck_seconds": 5},
        "refresh": {"loop_timeout_ms": 20, "observation_poll_seconds": "0.2",
                    "maintain_seconds": 1, "dispatch_recheck_limit": 8},
    }


def parse(root, doc):
    return parse_risk_service_config(doc, path=root / "risk_service.yaml")


def code_of(root, doc):
    with pytest.raises(RiskServiceConfigError) as raised:
        parse(root, doc)
    return raised.value.code


def synthetic_refresh_document(monkeypatch):
    # All identities are synthetic; no configuration, key, or other file is read.
    monkeypatch.setattr(risk_config, "read_key_file", lambda path, *, secret: path.name.encode())
    monkeypatch.setattr(Path, "resolve", lambda self, *args, **kwargs: self)
    doc = document()
    doc["account"]["account_scope"] = "account-" + "a" * 64
    doc["gateway"]["market_endpoint"] = "tcp://example.invalid:1"
    doc["gateway"]["market"] = {
        "source_ref": "market", "entitlement_ref": "entitlement", "channel": "tick",
        "instruments": ["rb2701"],
    }
    doc["gateway"]["account_snapshot"] = {
        "resources": [{"product_type": "future", "margin_pool": "default"}]
    }
    doc["refresh"].update({
        "market_poll_seconds": "1", "account_snapshot_seconds": "1",
        "account_snapshot_max_age_seconds": "1", "strategy_financial_seconds": "1",
        "strategy_financial_max_age_seconds": "1",
    })
    return doc


@pytest.mark.parametrize(
    "section, field, maximum",
    [
        ("deadlines", "call_timeout_seconds", 300),
        ("refresh", "observation_poll_seconds", 3600),
        ("refresh", "maintain_seconds", 3600),
        ("refresh", "market_poll_seconds", 3600),
        ("refresh", "account_snapshot_seconds", 3600),
        ("refresh", "strategy_financial_seconds", 3600),
    ],
)
def test_seconds_fields_reject_float_underflow_and_preserve_bounds(monkeypatch, section, field, maximum):
    doc = synthetic_refresh_document(monkeypatch)
    config_path = SYNTHETIC_CONFIG_PATH
    for value in ("1e-999", "1e-999999999999999999999"):
        doc[section][field] = value
        with pytest.raises(RiskServiceConfigError) as raised:
            parse_risk_service_config(doc, path=config_path)
        assert raised.value.code == "RISK_SERVICE_CONFIG_NUMBER_INVALID"
    for value, expected in (("0.25", 0.25), ("5e-324", 5e-324), (str(maximum), float(maximum))):
        doc[section][field] = value
        assert getattr(parse_risk_service_config(doc, path=config_path), field) == expected
    doc[section][field] = str(maximum + 1)
    with pytest.raises(RiskServiceConfigError) as raised:
        parse_risk_service_config(doc, path=config_path)
    assert raised.value.code == "RISK_SERVICE_CONFIG_NUMBER_OUT_OF_RANGE"


@pytest.mark.parametrize("field", [
    "account_snapshot_max_age_seconds", "strategy_financial_max_age_seconds",
])
def test_freshness_fields_require_one_quantized_nanosecond(monkeypatch, field):
    doc = synthetic_refresh_document(monkeypatch)
    config_path = SYNTHETIC_CONFIG_PATH
    for value in ("1e-999", "5e-324", "1e-10", "9.999999999999999e-10",
                  "0.00000000099999999999999999", "1.99999999999999999999e-9"):
        doc["refresh"][field] = value
        with pytest.raises(RiskServiceConfigError) as raised:
            parse_risk_service_config(doc, path=config_path)
        assert raised.value.code == "RISK_SERVICE_CONFIG_NUMBER_INVALID"
    for value, expected_ns in (("1e-9", 1), ("1.5e-9", 1),
                               ("2.00000000000000000001e-9", 2), ("0.25", 250_000_000),
                               ("86400", 86_400_000_000_000)):
        doc["refresh"][field] = value
        seconds = getattr(parse_risk_service_config(doc, path=config_path), field)
        assert int(seconds * 1_000_000_000) == expected_ns
        assert seconds == float(value)
    doc["refresh"][field] = "86401"
    with pytest.raises(RiskServiceConfigError) as raised:
        parse_risk_service_config(doc, path=config_path)
    assert raised.value.code == "RISK_SERVICE_CONFIG_NUMBER_OUT_OF_RANGE"


@pytest.mark.parametrize("field", [
    "account_snapshot_max_age_seconds", "strategy_financial_max_age_seconds",
])
def test_freshness_comparison_ignores_decimal_context(monkeypatch, field):
    doc = synthetic_refresh_document(monkeypatch)
    config_path = SYNTHETIC_CONFIG_PATH
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        doc["refresh"][field] = "0.00000000099999999999999999"
        with pytest.raises(RiskServiceConfigError) as raised:
            parse_risk_service_config(doc, path=config_path)
        assert raised.value.code == "RISK_SERVICE_CONFIG_NUMBER_INVALID"
        doc["refresh"][field] = "1.5e-9"
        seconds = getattr(parse_risk_service_config(doc, path=config_path), field)
        assert int(seconds * 1_000_000_000) == 1


@pytest.mark.parametrize("field, maximum", [
    ("reservation_ttl_seconds", 30), ("dispatch_recheck_seconds", 3600),
])
def test_deadline_nanosecond_conversion_does_not_expand_input(monkeypatch, field, maximum):
    doc = synthetic_refresh_document(monkeypatch)
    config_path = SYNTHETIC_CONFIG_PATH
    for value in ("1e-999", "5e-324", "1e-10", "0.00000000099999999999999999",
                  "1.99999999999999999999e-9"):
        doc["deadlines"][field] = value
        with pytest.raises(RiskServiceConfigError) as raised:
            parse_risk_service_config(doc, path=config_path)
        assert raised.value.code == "RISK_SERVICE_CONFIG_NUMBER_INVALID"
    for value, expected_ns in (("1e-9", 1), ("1.5e-9", 1),
                               (str(maximum), maximum * 1_000_000_000)):
        doc["deadlines"][field] = value
        seconds = getattr(parse_risk_service_config(doc, path=config_path), field)
        assert seconds == float(value)
        assert int(seconds * 1_000_000_000) == expected_ns
    doc["deadlines"][field] = str(maximum + 1)
    with pytest.raises(RiskServiceConfigError) as raised:
        parse_risk_service_config(doc, path=config_path)
    assert raised.value.code == "RISK_SERVICE_CONFIG_NUMBER_OUT_OF_RANGE"


@pytest.mark.parametrize("field", ["reservation_ttl_seconds", "dispatch_recheck_seconds"])
def test_deadline_nanosecond_comparison_ignores_decimal_context(monkeypatch, field):
    doc = synthetic_refresh_document(monkeypatch)
    config_path = SYNTHETIC_CONFIG_PATH
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        doc["deadlines"][field] = "1.99999999999999999999e-9"
        with pytest.raises(RiskServiceConfigError) as raised:
            parse_risk_service_config(doc, path=config_path)
        assert raised.value.code == "RISK_SERVICE_CONFIG_NUMBER_INVALID"
        doc["deadlines"][field] = "1.5e-9"
        seconds = getattr(parse_risk_service_config(doc, path=config_path), field)
        assert int(seconds * 1_000_000_000) == 1


def test_disabled_optional_freshness_remains_none_with_fake_keys(monkeypatch):
    synthetic_refresh_document(monkeypatch)  # installs fake key reader and path resolution
    config = parse_risk_service_config(document(), path=SYNTHETIC_CONFIG_PATH)
    assert config.account_snapshot_max_age_seconds is None
    assert config.strategy_financial_max_age_seconds is None


def test_same_native_alias_config_keeps_full_keys_and_refuses_ambiguous_lookup(monkeypatch):
    doc = synthetic_refresh_document(monkeypatch)
    doc["account"]["instruments"]["rb-option"] = {
        "venue": "SHFE", "native_symbol": "rb2701", "product_type": "option"
    }
    config = parse_risk_service_config(doc, path=SYNTHETIC_CONFIG_PATH)
    future, option = config.instruments["rb2701"], config.instruments["rb-option"]
    assert future.key != option.key
    assert config.instrument_for("SHFE", "rb2701") is None
    assert config.instrument_for("SHFE", "unknown") is None


def test_seconds_rejects_float_overflow_without_a_maximum():
    with pytest.raises(RiskServiceConfigError) as raised:
        risk_config._seconds("1e999")
    assert raised.value.code == "RISK_SERVICE_CONFIG_NUMBER_INVALID"


@pytest.mark.parametrize("error", [OverflowError, ValueError])
def test_seconds_maps_float_conversion_errors(monkeypatch, error):
    class ConversionFailure:
        def __float__(self):
            raise error

    monkeypatch.setattr(risk_config, "_decimal", lambda value: ConversionFailure())
    with pytest.raises(RiskServiceConfigError) as raised:
        risk_config._seconds("1")
    assert raised.value.code == "RISK_SERVICE_CONFIG_NUMBER_INVALID"


def test_full_document_parses_with_paths_relative_to_the_config_file(tmp_path):
    pairs = write_keys(tmp_path)
    config = parse(tmp_path, document())
    assert config.storage_directory == tmp_path / "state"
    assert config.policy_file == tmp_path / "risk_policy.yaml"
    assert config.risk_database == tmp_path / "state" / "risk.sqlite"
    assert config.reservation_ttl_seconds == 10.0 and config.call_timeout_seconds == 5.0
    assert config.dispatch_recheck_seconds == 5.0 and config.dispatch_recheck_limit == 8
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
    # Windows ntpath.expanduser resolves ~ from USERPROFILE/HOMEPATH, not HOME.
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOMEPATH", str(tmp_path))
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
        (("deadlines", "dispatch_recheck_seconds"), 0, "RISK_SERVICE_CONFIG_NUMBER_INVALID"),
        (("deadlines", "dispatch_recheck_seconds"), 3601, "RISK_SERVICE_CONFIG_NUMBER_OUT_OF_RANGE"),
        (("deadlines", "dispatch_recheck_seconds"), 2.5, "RISK_SERVICE_CONFIG_FLOAT_REFUSED"),
        (("refresh", "dispatch_recheck_limit"), 0, "RISK_SERVICE_CONFIG_NUMBER_INVALID"),
        (("refresh", "dispatch_recheck_limit"), "8", "RISK_SERVICE_CONFIG_NUMBER_INVALID"),
        (("refresh", "dispatch_recheck_limit"), 1025, "RISK_SERVICE_CONFIG_NUMBER_INVALID"),
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
                                          ("deadlines", "dispatch_recheck_seconds"),
                                          ("refresh", "dispatch_recheck_limit"),
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


def test_yaml_merge_preserves_disjoint_mapping_and_sequence_semantics():
    result = load_document(
        "first: &first {a: 1}\nsecond: &second {b: 2}\n"
        "single: &single {<<: *first, c: 3}\n"
        "combined: {<<: [*single, *second], d: 4}\n"
        "sequence: &sequence [1, 2]\nsequence_alias: *sequence\n"
    )
    assert result["single"] == {"a": 1, "c": 3}
    assert result["combined"] == {"a": 1, "b": 2, "c": 3, "d": 4}
    assert result["sequence_alias"] == [1, 2]


def test_yaml_empty_merges_and_nested_alias_preserve_mapping():
    result = load_document(
        "empty_map: &empty_map {}\n"
        "empty_seq: &empty_seq []\n"
        "direct_map: {<<: {}, a: 1}\n"
        "direct_seq: {<<: [], b: 2}\n"
        "first: &first {<<: *empty_map, c: 3}\n"
        "second: &second {<<: *empty_seq, d: 4}\n"
        "nested: {<<: [*first, *second], e: 5}\n"
    )
    assert result["direct_map"] == {"a": 1}
    assert result["direct_seq"] == {"b": 2}
    assert result["first"] == {"c": 3}
    assert result["second"] == {"d": 4}
    assert result["nested"] == {"c": 3, "d": 4, "e": 5}


@pytest.mark.parametrize("source", [
    "base: &base {a: 1}\nmerged: {<<: *base, a: 2}\n",
    "first: &first {a: 1}\nsecond: &second {a: 2}\nmerged: {<<: [*first, *second]}\n",
])
def test_yaml_merge_duplicate_keys_remain_refused(source):
    with pytest.raises(RiskServiceConfigError) as raised:
        load_document(source)
    assert raised.value.code == "RISK_SERVICE_CONFIG_DUPLICATE_KEY"


@pytest.mark.parametrize("source", [
    "value: &self [*self]\n",
    "value: &self {<<: *self}\n",
    "value: &a {nested: &b [*a]}\n",
    "value: {<<: [1]}\n",
    "value: " + "[" * 70 + "0" + "]" * 70,
])
def test_yaml_cycles_and_excess_depth_are_refused(source):
    with pytest.raises(RiskServiceConfigError) as raised:
        load_document(source)
    assert raised.value.code == "RISK_SERVICE_CONFIG_INVALID_DOCUMENT"


def test_yaml_alias_graph_budget_stops_before_construction(monkeypatch):
    import yaml

    monkeypatch.setattr(risk_config, "_MAX_YAML_WORK", 100)
    visits = []
    original_charge = risk_config._YamlBudget.charge
    original_construct = yaml.SafeLoader.construct_object
    constructions = []

    def charge(budget, count):
        visits.append((budget.used, count))
        return original_charge(budget, count)

    def construct(loader, node, *args, **kwargs):
        constructions.append(1)
        return original_construct(loader, node, *args, **kwargs)

    monkeypatch.setattr(risk_config._YamlBudget, "charge", charge)
    monkeypatch.setattr(yaml.SafeLoader, "construct_object", construct)
    source = "n0: &n0 [1]\n" + "".join(
        f"n{i}: &n{i} [*n{i - 1}, *n{i - 1}]\n" for i in range(1, 9)
    )
    with pytest.raises(RiskServiceConfigError) as raised:
        load_document(source)
    assert raised.value.code == "RISK_SERVICE_CONFIG_INVALID_DOCUMENT"
    assert not constructions
    assert all(used <= 100 for used, _ in visits)
    assert visits[-1][0] + visits[-1][1] > 100


def test_yaml_merge_work_is_charged_before_copy(monkeypatch):
    source = "base: &base {a: 1, b: 2}\nmerged: {<<: *base, c: 3}\n"
    calls = []
    original_charge = risk_config._YamlBudget.charge

    def charge(budget, count):
        calls.append((budget.used, count))
        return original_charge(budget, count)

    monkeypatch.setattr(risk_config._YamlBudget, "charge", charge)
    assert load_document(source)["merged"] == {"a": 1, "b": 2, "c": 3}
    # The final charge reserves the merged and retained pairs before allocation.
    limit, count = calls[-1]
    assert count == 3
    monkeypatch.setattr(risk_config, "_MAX_YAML_WORK", limit + count - 1)
    calls.clear()
    with pytest.raises(RiskServiceConfigError) as raised:
        load_document(source)
    assert raised.value.code == "RISK_SERVICE_CONFIG_INVALID_DOCUMENT"
    assert calls[-1] == (limit, count)


def test_yaml_internal_errors_are_not_exposed(monkeypatch):
    import yaml

    def fail(*args, **kwargs):
        raise RuntimeError("sensitive input")

    monkeypatch.setattr(yaml.SafeLoader, "compose_node", fail)
    with pytest.raises(RiskServiceConfigError) as raised:
        load_document("a: 1\n")
    assert str(raised.value) == "RISK_SERVICE_CONFIG_INVALID_DOCUMENT"


def test_yaml_traceback_does_not_display_input_on_parser_error():
    import traceback

    marker = "synthetic_private_marker_12345"
    with pytest.raises(RiskServiceConfigError) as raised:
        load_document(f"a: !{marker} value\n")
    assert raised.value.code == "RISK_SERVICE_CONFIG_INVALID_DOCUMENT"
    formatted = "".join(traceback.format_exception(raised.value))
    assert marker not in formatted


def test_json_loading_is_unaffected_by_yaml_work_limit(monkeypatch):
    monkeypatch.setattr(risk_config, "_MAX_YAML_WORK", 0)
    assert load_document('{"items": [1, {"ok": true}]}') == {"items": [1, {"ok": True}]}
    for source, code in [('{"a": 1, "a": 2}', "RISK_SERVICE_CONFIG_DUPLICATE_KEY"),
                         ('{"a": 1.5}', "RISK_SERVICE_CONFIG_FLOAT_REFUSED")]:
        with pytest.raises(RiskServiceConfigError) as raised:
            load_document(source)
        assert raised.value.code == code


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
