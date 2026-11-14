"""Invalid collection entries fail before keys, services or I/O are touched."""

from copy import deepcopy
from pathlib import Path

import pytest

import bt_api_py.risk_service_config as config
def document():
    return {
        "schema": "bt_api_risk.service.v1",
        "service": {"service_id": "risk-service", "principal_ref": "risk-host",
                    "instance_id": "risk-a", "scope_ref": "risk-scope",
                    "rpc_bind": "tcp://example.invalid:1", "public_key_file": "server.pub",
                    "secret_key_file": "server.sec"},
        "account": {"route": "route", "provider": "ctp", "environment": "demo",
                    "account_scope": "synthetic-account", "execution_domain_ref": "paper:demo",
                    "instruments": {"rb": {"venue": "SHFE", "native_symbol": "rb",
                                           "product_type": "future"}}},
        "storage": {"directory": "state"}, "policy": {"file": "policy.yaml"},
        "principals": [
            {"principal_ref": "strategy", "role": "strategy", "actor_ref": "s",
             "strategy_refs": ["s"], "operations": ["risk.status"], "public_key_file": "s.pub"},
            {"principal_ref": "operator", "role": "operator", "actor_ref": "ops",
             "strategy_refs": [], "operations": ["risk.admin.status"], "public_key_file": "op.pub"},
        ],
        "gateway": {"service_id": "gateway", "principal_ref": "gateway-principal",
                    "instance_id": "g", "scope_ref": "scope", "rpc_endpoint": "tcp://example.invalid:2",
                    "server_public_key_file": "gateway.pub",
                    "delegate": {"principal_ref": "delegate", "actor_ref": "delegate-actor",
                                 "public_key_file": "delegate.pub", "secret_key_file": "delegate.sec",
                                 "grant_revisions": {"s": "revision"}},
                    "control": {"principal_ref": "control", "actor_ref": "control-actor",
                                "public_key_file": "control.pub", "secret_key_file": "control.sec"}},
        "deadlines": {"call_timeout_seconds": 5, "reservation_ttl_seconds": "10",
                      "dispatch_recheck_seconds": 5},
        "refresh": {"loop_timeout_ms": 20, "observation_poll_seconds": "0.2",
                    "maintain_seconds": 1, "dispatch_recheck_limit": 8},
    }


@pytest.fixture(autouse=True)
def no_real_paths(monkeypatch):
    monkeypatch.setattr(Path, "resolve", lambda self, *args, **kwargs: self)


def fail_if_key_read(*args, **kwargs):
    pytest.fail("invalid collection must be rejected before any key read")


def parse(doc):
    return config.parse_risk_service_config(doc, path=Path("/synthetic/risk.yaml"))


@pytest.mark.parametrize("role", [{}, [], None, True, 1, "", "unknown"])
@pytest.mark.parametrize("principal_index", [0, 1])
def test_invalid_role_is_a_finite_error_before_key_read(monkeypatch, role, principal_index):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["principals"][principal_index]["role"] = role
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_ROLE_INVALID"


@pytest.mark.parametrize("field", ["strategy_refs", "operations"])
@pytest.mark.parametrize("bad", [{}, [], None, True, 1, "", "x" * 257, "\ud800"])
@pytest.mark.parametrize("principal_index", [0, 1])
def test_invalid_principal_list_member_never_hashes_or_reads_keys(monkeypatch, field, bad, principal_index):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["principals"][principal_index][field] = [bad]
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_PRINCIPAL_INVALID"


@pytest.mark.parametrize("field", ["strategy_refs", "operations"])
@pytest.mark.parametrize("bad", [{}, None, True, "s", 1])
def test_principal_collections_must_be_lists(monkeypatch, field, bad):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["principals"][1][field] = bad
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_PRINCIPAL_INVALID"


@pytest.mark.parametrize("field", ["strategy_refs", "operations"])
def test_duplicate_principal_values_are_rejected_before_keys(monkeypatch, field):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    original = doc["principals"][0][field][0]
    doc["principals"][0][field] = [original, original]
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_PRINCIPAL_INVALID"


@pytest.mark.parametrize("field,values", [
    ("strategy_refs", ["unknown"]),
    ("operations", ["risk.unknown"]),
])
def test_unpermitted_principal_references_fail_before_keys(monkeypatch, field, values):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["principals"][0][field] = values
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_OPERATIONS_INVALID"


@pytest.mark.parametrize("role", ["strategy", "execution"])
def test_missing_gateway_grant_for_strategy_fails_before_keys(monkeypatch, role):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["principals"][0]["role"] = role
    if role == "execution":
        doc["principals"][0]["strategy_refs"] = ["unknown"]
    else:
        doc["gateway"]["delegate"]["grant_revisions"] = {"unknown": "revision"}
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_GRANT_REVISION_MISSING"


@pytest.mark.parametrize("bad", [{}, [], None, True, "revision"])
def test_invalid_grant_mapping_fails_before_keys(monkeypatch, bad):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["gateway"]["delegate"]["grant_revisions"] = bad
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_GRANT_REVISION_MISSING"


@pytest.mark.parametrize("bad", [{}, [], None, True, 1, "", "unknown", "rb" * 129, "\ud800"])
def test_invalid_market_instrument_is_rejected_before_any_key(monkeypatch, bad):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["gateway"]["market_endpoint"] = "tcp://example.invalid:1"
    doc["gateway"]["market"] = {
        "source_ref": "source", "entitlement_ref": "entitlement", "channel": "ticker",
        "instruments": [bad],
    }
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_MARKET_INVALID"


def test_duplicate_market_instrument_is_rejected_before_any_key(monkeypatch):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["gateway"]["market_endpoint"] = "tcp://example.invalid:1"
    doc["gateway"]["market"] = {
        "source_ref": "source", "entitlement_ref": "entitlement", "channel": "ticker",
        "instruments": ["rb", "rb"],
    }
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_MARKET_INVALID"


@pytest.mark.parametrize("bad", [{}, None, True, "rb", 1, []])
def test_market_instrument_collection_must_be_nonempty_list(monkeypatch, bad):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["gateway"]["market_endpoint"] = "tcp://example.invalid:1"
    doc["gateway"]["market"] = {
        "source_ref": "source", "entitlement_ref": "entitlement", "channel": "ticker",
        "instruments": bad,
    }
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_MARKET_INVALID"


@pytest.mark.parametrize("field", ["source_ref", "entitlement_ref", "channel"])
@pytest.mark.parametrize("bad", [{}, [], None, True, 1, "", "x" * 257, "\ud800"])
def test_market_declaration_invalid_field_fails_before_keys(monkeypatch, field, bad):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["gateway"]["market_endpoint"] = "tcp://example.invalid:1"
    doc["gateway"]["market"] = {
        "source_ref": "source", "entitlement_ref": "entitlement", "channel": "ticker",
        "instruments": ["rb"],
    }
    doc["gateway"]["market"][field] = bad
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_VALUE_INVALID"


def test_market_without_endpoint_is_rejected_before_keys(monkeypatch):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["gateway"]["market"] = {
        "source_ref": "source", "entitlement_ref": "entitlement", "channel": "ticker",
        "instruments": ["rb"],
    }
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_MARKET_ENDPOINT_REQUIRED"


@pytest.mark.parametrize("has_market,interval", [(True, None), (False, "1")])
def test_market_refresh_selection_mismatch_fails_before_keys(monkeypatch, has_market, interval):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    if has_market:
        doc["gateway"]["market_endpoint"] = "tcp://example.invalid:1"
        doc["gateway"]["market"] = {
            "source_ref": "source", "entitlement_ref": "entitlement", "channel": "ticker",
            "instruments": ["rb"],
        }
    if interval is not None:
        doc["refresh"]["market_poll_seconds"] = interval
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_REFRESH_INVALID"


@pytest.mark.parametrize("bad,code", [
    (True, "RISK_SERVICE_CONFIG_NUMBER_INVALID"),
    ({}, "RISK_SERVICE_CONFIG_NUMBER_INVALID"),
    (0, "RISK_SERVICE_CONFIG_NUMBER_INVALID"),
    (3601, "RISK_SERVICE_CONFIG_NUMBER_OUT_OF_RANGE"),
    (0.5, "RISK_SERVICE_CONFIG_FLOAT_REFUSED"),
])
def test_bad_market_refresh_interval_fails_before_keys(monkeypatch, bad, code):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["gateway"]["market_endpoint"] = "tcp://example.invalid:1"
    doc["gateway"]["market"] = {
        "source_ref": "source", "entitlement_ref": "entitlement", "channel": "ticker",
        "instruments": ["rb"],
    }
    doc["refresh"]["market_poll_seconds"] = bad
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == code


@pytest.mark.parametrize("bad", [{}, [], True, 1, "", "x" * 257, "\ud800"])
@pytest.mark.parametrize("has_market", [False, True])
def test_invalid_market_endpoint_fails_before_keys(monkeypatch, bad, has_market):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["gateway"]["market_endpoint"] = bad
    if has_market:
        doc["gateway"]["market"] = {
            "source_ref": "source", "entitlement_ref": "entitlement", "channel": "ticker",
            "instruments": ["rb"],
        }
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_VALUE_INVALID"


@pytest.mark.parametrize("key_path", [
    ("service", "public_key_file"), ("service", "secret_key_file"),
    ("principals", 1, "public_key_file"),
    ("gateway", "server_public_key_file"),
    ("gateway", "delegate", "public_key_file"), ("gateway", "delegate", "secret_key_file"),
    ("gateway", "control", "public_key_file"), ("gateway", "control", "secret_key_file"),
])
@pytest.mark.parametrize("bad", [[], {}, None, True, "", " bad ", "bad\x00key"])
def test_invalid_key_path_format_fails_before_any_key_read(monkeypatch, key_path, bad):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    node = doc
    for part in key_path[:-1]:
        node = node[part]
    node[key_path[-1]] = bad
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_PATH_INVALID"


def test_unresolved_key_path_fails_before_any_key_read(monkeypatch):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    monkeypatch.delenv("ITER64_MISSING_SYNTHETIC_KEY_DIR", raising=False)
    doc = document()
    doc["gateway"]["control"]["secret_key_file"] = "$ITER64_MISSING_SYNTHETIC_KEY_DIR/key"
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_PATH_UNRESOLVED"


@pytest.mark.parametrize("bad", [{}, None, True, "resource", 1, []])
def test_snapshot_resources_must_be_nonempty_list_before_keys(monkeypatch, bad):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["gateway"]["account_snapshot"] = {"resources": bad}
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_ACCOUNT_SNAPSHOT_INVALID"


@pytest.mark.parametrize("snapshot,code", [
    ([], "RISK_SERVICE_CONFIG_SHAPE_INVALID"),
    (True, "RISK_SERVICE_CONFIG_SHAPE_INVALID"),
    ({}, "RISK_SERVICE_CONFIG_KEY_MISSING"),
])
def test_snapshot_container_shape_fails_before_keys(monkeypatch, snapshot, code):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["gateway"]["account_snapshot"] = snapshot
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == code


@pytest.mark.parametrize("entry,code", [
    ({}, "RISK_SERVICE_CONFIG_KEY_MISSING"),
    ([], "RISK_SERVICE_CONFIG_SHAPE_INVALID"),
    (None, "RISK_SERVICE_CONFIG_SHAPE_INVALID"),
    (True, "RISK_SERVICE_CONFIG_SHAPE_INVALID"),
    ("resource", "RISK_SERVICE_CONFIG_SHAPE_INVALID"),
    ({"product_type": "future", "margin_pool": "main", "extra": "x"},
     "RISK_SERVICE_CONFIG_KEY_UNKNOWN"),
])
def test_snapshot_resource_shape_fails_before_keys(monkeypatch, entry, code):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["gateway"]["account_snapshot"] = {"resources": [entry]}
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == code


@pytest.mark.parametrize("field", ["product_type", "margin_pool"])
@pytest.mark.parametrize("bad", [{}, [], None, True, 1, "", "x" * 257, "\ud800"])
def test_snapshot_resource_invalid_reference_fails_before_keys(monkeypatch, field, bad):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    resource = {"product_type": "future", "margin_pool": "main"}
    resource[field] = bad
    doc["gateway"]["account_snapshot"] = {"resources": [resource]}
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_VALUE_INVALID"


def test_duplicate_snapshot_resources_fail_before_keys(monkeypatch):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    row = {"product_type": "future", "margin_pool": "main"}
    doc["gateway"]["account_snapshot"] = {"resources": [row, row.copy()]}
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_ACCOUNT_SNAPSHOT_INVALID"


def test_snapshot_requires_bound_account_reference_before_keys(monkeypatch):
    monkeypatch.setattr(config, "read_key_file", fail_if_key_read)
    doc = document()
    doc["gateway"]["account_snapshot"] = {
        "resources": [{"product_type": "future", "margin_pool": "main"}]
    }
    with pytest.raises(config.RiskServiceConfigError) as error:
        parse(doc)
    assert error.value.code == "RISK_SERVICE_CONFIG_ACCOUNT_SNAPSHOT_INVALID"


def test_valid_snapshot_resources_still_parse_with_synthetic_keys(monkeypatch):
    monkeypatch.setattr(config, "read_key_file", lambda path, *, secret: path.name.encode())
    doc = document()
    doc["account"]["account_scope"] = "account-" + "a" * 64
    doc["gateway"]["account_snapshot"] = {
        "resources": [
            {"product_type": "future", "margin_pool": "main"},
            {"product_type": "future", "margin_pool": "other"},
        ]
    }
    doc["refresh"]["account_snapshot_seconds"] = "1"
    doc["refresh"]["account_snapshot_max_age_seconds"] = "2"
    result = parse(doc)
    assert result.gateway.account_snapshot.resources == (("future", "main"), ("future", "other"))


def test_valid_principals_and_market_still_parse_with_synthetic_keys(monkeypatch):
    monkeypatch.setattr(config, "read_key_file", lambda path, *, secret: path.name.encode())
    doc = deepcopy(document())
    doc["gateway"]["market_endpoint"] = "tcp://example.invalid:1"
    doc["gateway"]["market"] = {
        "source_ref": "source", "entitlement_ref": "entitlement", "channel": "ticker",
        "instruments": ["rb"],
    }
    doc["refresh"]["market_poll_seconds"] = "0.2"
    result = parse(doc)
    assert result.gateway.market.instrument_ids == ("rb",)
    assert result.principals["strategy"].strategy_refs == frozenset({"s"})
