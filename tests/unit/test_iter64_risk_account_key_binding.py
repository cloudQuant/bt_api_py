"""Account snapshot read bound by the hashed account reference (64.09, plan 64 section 6.1).

The Risk service reads the operator account snapshot without holding the raw
account or sub-account number: ``ServiceAccountClient`` can be pinned by the
``AccountKey.account_key_id`` digest instead of the full key. UNIT evidence:
the real ServiceRegistry with an in-process transport, temp files only.
"""

from __future__ import annotations

import copy
import json

import pytest
from bt_api_base.account_protocol import AccountKey, AccountResourceKey

from bt_api_py.gateway_strategy_account import GatewayAccountClientSettings, GatewayAccountOwner
from bt_api_py.risk_service_config import load_risk_service_config
from bt_api_py.service_account import ServiceAccountClient, ServiceAccountError
from tests.unit.test_iter64_risk_service_config import code_of, document, parse, write_keys
from tests.unit.test_service_account import (
    _ACCOUNT,
    _ACCOUNT_REF,
    _COVERAGE,
    _close_all,
    _make_client,
    _observation,
    _reply,
)

# The Gateway's account key carries a sub-account (as the CTP Gateway's does).
_WITH_SUBACCOUNT = AccountKey("synthetic", "unit", "paper", "account-one", "9999")
_OTHER_SUBACCOUNT = AccountKey("synthetic", "unit", "paper", "account-one", "8888")


def _keyed(runtime, clock, key_id, **kwargs):
    return ServiceAccountClient(
        runtime,
        "store.account",
        account_ref=_ACCOUNT_REF,
        required_coverage=_COVERAGE,
        snapshot_format="generic",
        monotonic_clock=clock,
        expected_account_key_id=key_id,
        **kwargs,
    )


def test_full_key_binding_is_unchanged_and_refuses_a_subaccount_key(tmp_path):
    client, runtime, registry, source, _transport, _clock = _make_client(tmp_path)
    try:
        assert client.expected_account == _ACCOUNT
        assert client.expected_account_key_id == _ACCOUNT.account_key_id
        assert client.refresh().observation.account_key == _ACCOUNT
        # The whole key is compared: the same account with a sub-account is another key.
        source.response = _reply(_observation(account=_WITH_SUBACCOUNT))
        with pytest.raises(ServiceAccountError) as raised:
            client.refresh()
        assert raised.value.code == "ACCOUNT_IDENTITY_MISMATCH"
    finally:
        _close_all(client, runtime, registry)


def test_digest_binding_accepts_the_gateway_key_with_its_subaccount(tmp_path):
    client, runtime, registry, source, _transport, clock = _make_client(tmp_path)
    keyed = _keyed(runtime, clock, _WITH_SUBACCOUNT.account_key_id)
    try:
        assert keyed.expected_account is None
        assert keyed.expected_account_key_id == _WITH_SUBACCOUNT.account_key_id
        source.response = _reply(_observation(account=_WITH_SUBACCOUNT))
        view = keyed.refresh()
        assert view.observation.account_key == _WITH_SUBACCOUNT
        assert keyed.healthy is True
        # Another sub-account of the same account number is another account: refused.
        other = _keyed(runtime, clock, _OTHER_SUBACCOUNT.account_key_id)
        with pytest.raises(ServiceAccountError) as raised:
            other.refresh()
        assert raised.value.code == "ACCOUNT_IDENTITY_MISMATCH"
        assert other.healthy is False
        other.close()
        # The same digest client refuses a later observation of another key.
        source.response = _reply(_observation(account=_OTHER_SUBACCOUNT))
        with pytest.raises(ServiceAccountError) as raised:
            keyed.refresh()
        assert raised.value.code == "ACCOUNT_IDENTITY_MISMATCH"
    finally:
        keyed.close()
        _close_all(client, runtime, registry)


@pytest.mark.parametrize(
    "binding",
    [
        {},  # neither
        {"expected_account": _ACCOUNT, "expected_account_key_id": _ACCOUNT.account_key_id},
        {"expected_account_key_id": "account-a"},  # not a key digest
        {"expected_account_key_id": "account-" + "A" * 64},
        {"expected_account_key_id": _ACCOUNT.account_key_id + " "},
        {"expected_account_key_id": b"account-" + b"0" * 64},
        {"expected_account": _ACCOUNT.to_dict()},
    ],
)
def test_binding_needs_exactly_one_valid_identity(tmp_path, binding):
    client, runtime, registry, _source, _transport, clock = _make_client(tmp_path)
    try:
        with pytest.raises(ServiceAccountError) as raised:
            ServiceAccountClient(
                runtime,
                "store.account",
                account_ref=_ACCOUNT_REF,
                required_coverage=_COVERAGE,
                snapshot_format="generic",
                monotonic_clock=clock,
                **binding,
            )
        assert raised.value.code == "INVALID_EXPECTED_ACCOUNT"
    finally:
        _close_all(client, runtime, registry)


def test_gateway_account_settings_carry_exactly_one_binding_to_the_owner(tmp_path):
    coverage = (AccountResourceKey("future", "futures"),)
    operations = frozenset({"account.snapshot.typed"})
    digest = _WITH_SUBACCOUNT.account_key_id
    settings = GatewayAccountClientSettings(
        None, coverage, operations, expected_account_key_id=digest
    )
    assert settings.expected_account is None and settings.expected_account_key_id == digest
    legacy = GatewayAccountClientSettings(_WITH_SUBACCOUNT, coverage, operations)
    assert legacy.expected_account == _WITH_SUBACCOUNT and legacy.expected_account_key_id is None
    for bad in (
        dict(expected_account=None),
        dict(expected_account=_WITH_SUBACCOUNT, expected_account_key_id=digest),
        dict(expected_account=None, expected_account_key_id="account-a"),
    ):
        with pytest.raises(ValueError, match="INVALID_ACCOUNT_SETTINGS"):
            GatewayAccountClientSettings(
                required_coverage=coverage, requested_operations=operations, **bad
            )
    # The owner hands the digest binding to its ServiceAccountClient unchanged.
    client, runtime, registry, _source, _transport, _clock = _make_client(tmp_path)
    owner = GatewayAccountOwner(settings, account_ref=_ACCOUNT_REF, scope_ref="account-scope-v1")
    try:
        owner.start(runtime)
        assert owner._client.expected_account is None
        assert owner._client.expected_account_key_id == digest
    finally:
        owner.close()
        _close_all(client, runtime, registry)


def _snapshot_doc():
    doc = document()
    doc["account"]["account_scope"] = _WITH_SUBACCOUNT.account_key_id
    doc["gateway"]["account_snapshot"] = {
        "resources": [{"product_type": "future", "margin_pool": "futures"}]
    }
    doc["refresh"]["account_snapshot_seconds"] = 1
    doc["refresh"]["account_snapshot_max_age_seconds"] = 5
    return doc


@pytest.mark.parametrize(
    ("extra", "value"),
    [("account_id", "account-one"), ("subaccount_id", "9999"), ("venue", "SHFE")],
)
def test_risk_snapshot_config_names_no_account(tmp_path, extra, value):
    write_keys(tmp_path)
    doc = _snapshot_doc()
    config = parse(tmp_path, copy.deepcopy(doc))
    assert config.gateway.account_snapshot.resources == (("future", "futures"),)
    assert set(vars(type(config.gateway.account_snapshot))["__dataclass_fields__"]) == {
        "resources"
    }
    doc["gateway"]["account_snapshot"][extra] = value
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_KEY_UNKNOWN"


def test_risk_snapshot_needs_the_hashed_account_reference_as_scope(tmp_path):
    write_keys(tmp_path)
    doc = _snapshot_doc()
    doc["account"]["account_scope"] = "account-a"
    assert code_of(tmp_path, doc) == "RISK_SERVICE_CONFIG_ACCOUNT_SNAPSHOT_INVALID"


def test_risk_host_binds_the_snapshot_read_by_the_account_scope_digest(tmp_path, monkeypatch):
    import bt_api_py.gateway_strategy_account as account_module
    from bt_api_py.risk_service_host import RiskServiceHost

    write_keys(tmp_path)
    path = tmp_path / "risk_service.json"
    path.write_text(json.dumps(_snapshot_doc()), encoding="utf-8")
    config = load_risk_service_config(path)
    built = []
    original = account_module.GatewayAccountClientSettings

    def capture(*args, **kwargs):
        value = original(*args, **kwargs)
        built.append(value)
        return value

    monkeypatch.setattr(account_module, "GatewayAccountClientSettings", capture)
    runtime = RiskServiceHost(config)._control_runtime()
    try:
        (settings,) = built
        assert settings.expected_account is None
        assert settings.expected_account_key_id == config.account_scope
        assert settings.required_coverage == (AccountResourceKey("future", "futures"),)
    finally:
        runtime.close(timeout_seconds=5)
