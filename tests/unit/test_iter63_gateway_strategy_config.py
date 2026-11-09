"""Public Gateway configuration and providerless session composition."""
import copy
from pathlib import Path

import pytest
import yaml

from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_strategy_config import (
    GatewayStrategyConfigError,
    parse_gateway_strategy_config,
)
from bt_api_py.gateway_strategy_instrument import INSTRUMENT_OPERATIONS
from tests.unit.test_iter63_gateway_strategy_client import Host, parts
from tests.unit.test_gateway_strategy_account import settings as account_settings


@pytest.fixture
def raw():
    path = Path(__file__).resolve().parents[2] / "examples/services/gateway-strategy-client.example.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_public_example_creates_inert_owned_api_without_provider(raw, monkeypatch):
    import bt_api_py.gateway_strategy_client as client

    def forbidden(*args, **kwargs):
        pytest.fail("runtime or transport constructed before start")

    monkeypatch.setattr(client, "ServiceClientRuntime", forbidden)
    monkeypatch.setattr(client, "ZmqTransportFactory", forbidden)
    config = parse_gateway_strategy_config(raw)
    api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=parts()["security_profiles"])
    assert type(api) is GatewayBtApi
    assert api.list_exchanges() == ["CTP___FUTURE"]
    assert api._session.status().state == "new"
    assert config.instruments_by_ref["main"].native_symbol == "IF2612"
    api.close()
    assert api.lifecycle_state == "closed"


def test_detaches_nested_input_and_property_results(raw):
    config = parse_gateway_strategy_config(raw)
    expected = config.to_public_dict()
    raw["client_config"]["registration"]["strategy_ref"] = "other"
    raw["instruments_by_ref"]["main"]["venue"] = "other"
    supplied = config.to_public_dict()
    supplied["route"]["scope"]["run_id"] = "other"
    assert config.to_public_dict() == expected
    assert config.binding.strategy_scope == "strategy"
    assert config.instruments_by_ref["main"].venue == "CFFEX"
    with pytest.raises(TypeError):
        config.instruments_by_ref["other"] = config.instruments_by_ref["main"]


@pytest.mark.parametrize("edit", [
    lambda x: x.update(unexpected=True),
    lambda x: x.update(schema_version=True),
    lambda x: x["binding"].update(account_scope="wrong"),
    lambda x: x["route"]["identity"].update(principal_ref="wrong"),
    lambda x: x["peer"].update(actor_ref="wrong"),
    lambda x: x["execution_time_policy"].update(timezone="Not/AZone"),
    lambda x: x["execution_time_policy"].update(route="wrong"),
    lambda x: x["instruments_by_ref"]["main"].pop("venue"),
    lambda x: x["client_config"]["communication"]["profiles"]["transport"].update(secret_key="no"),
    lambda x: x.update(requested_operations=["direct.reserve"]),
    lambda x: x.update(requested_operations=["direct.events.progress", "direct.events.progress"]),
    lambda x: x["client_config"]["communication"]["profiles"]["transport"].update(backend="other"),
])
def test_invalid_or_inconsistent_public_fields_rejected(raw, edit):
    edit(raw)
    with pytest.raises(GatewayStrategyConfigError, match="^INVALID_GATEWAY_STRATEGY_CONFIG$"):
        parse_gateway_strategy_config(raw)


def test_missing_secret_is_rejected_without_connect(raw):
    config = parse_gateway_strategy_config(raw)
    with pytest.raises(ValueError, match="INVALID_CLIENT_CONFIGURATION"):
        config.create_session(host=Host(), cancel_host=Host(), security_profiles={})


def test_optional_settings_use_existing_types_and_reach_real_session(raw):
    bindings = raw["client_config"]["clients"]["gateway"]["settings"]["bindings"]
    bindings.append(dict(bindings[0], mount="store.account"))
    raw["client_config"]["communication"]["profiles"]["transport"]["endpoints"]["market_connect"] = "tcp://127.0.0.1:12346"
    account = account_settings()
    raw["account_settings"] = {
        "expected_account": account.expected_account.to_dict(),
        "required_coverage": [value.to_dict() for value in account.required_coverage],
        "requested_operations": sorted(account.requested_operations),
        "require_open_orders": False,
    }
    raw["market_settings"]["subscriptions"]["main"]["queue_capacity"] = 17
    raw["instrument_settings"] = {
        "instruments": [copy.deepcopy(raw["instruments_by_ref"]["main"])],
        "requested_operations": sorted(INSTRUMENT_OPERATIONS),
    }
    config = parse_gateway_strategy_config(raw)
    session = config.create_session(host=Host(), cancel_host=Host(), security_profiles=parts()["security_profiles"])
    assert config.account_settings == account
    assert config.market_settings.subscriptions["main"].queue_capacity == 17
    assert config.instrument_settings.instruments[0].native_symbol == "IF2612"
    assert session.account_client is not None
    assert session.market_client is not None
    assert session.instruments is not None
    assert session.status().state == "new"
    session.close()


def test_created_api_requires_ownership_and_config_repr_hides_values(raw):
    config = parse_gateway_strategy_config(raw)
    assert "principal" not in repr(config)
    with pytest.raises(ValueError, match="CREATED_SESSION_REQUIRES_OWNERSHIP"):
        config.create_api(host=Host(), cancel_host=Host(), security_profiles=parts()["security_profiles"], owns_session=False)


def test_host_binding_conflict_stops_before_rpc(raw, monkeypatch):
    from tests.unit.test_iter63_gateway_api import order

    class MismatchedHost(Host):
        def save_intent(self, *args, **kwargs):
            raise ValueError("binding mismatch")

    session = parse_gateway_strategy_config(raw).create_session(
        host=MismatchedHost(), cancel_host=Host(), security_profiles=parts()["security_profiles"]
    )
    client = session.command_client
    monkeypatch.setattr(client, "_route_ready", lambda *_: True)
    monkeypatch.setattr(client, "_rpc", lambda *_a, **_kw: pytest.fail("must not call RPC"))
    result = client.reserve(order())
    assert result.outcome == "TRANSPORT_NOT_STARTED"
    assert result.code == "INTENT_NOT_DURABLE"
    session.close()


def test_clock_permission_and_instrument_binding_are_explicit(raw):
    config = parse_gateway_strategy_config(raw)
    subscription = config.market_settings.subscriptions["main"]
    assert subscription.key.instrument == config.instruments_by_ref["main"]
    assert "market.clock_observation" in config.market_settings.requested_operations
    assert {"direct.reserve", "direct.dispatch", "direct.command_status", "direct.cancel", "direct.cancel_status"} <= config.requested_operations
    raw["market_settings"]["requested_operations"].remove("market.clock_observation")
    without_clock = parse_gateway_strategy_config(raw)
    assert "market.clock_observation" not in without_clock.market_settings.requested_operations
    raw["market_settings"]["subscriptions"]["main"]["key"]["instrument"].pop("venue")
    with pytest.raises(GatewayStrategyConfigError, match="INVALID_GATEWAY_STRATEGY_CONFIG"):
        parse_gateway_strategy_config(raw)


def test_cancel_permission_requires_cancel_host(raw):
    config = parse_gateway_strategy_config(raw)
    with pytest.raises(ValueError, match="INVALID_REQUESTED_OPERATIONS"):
        config.create_session(host=Host(), security_profiles=parts()["security_profiles"])


def test_public_example_binds_bt_gateway_tick_adapter(raw):
    """The declared subscription fits the actual BT adapter without I/O."""
    from types import SimpleNamespace
    adapter_module = pytest.importorskip("backtrader.service.gateway_market_adapter")

    config = parse_gateway_strategy_config(raw)
    setting = config.market_settings.subscriptions["main"]
    assert setting.key.channel == "tick"
    assert setting.key.depth is None and setting.key.timeframe is None
    assert setting.require_source_descriptor is True
    # Server must provide the source descriptor; a fabricated client-side
    # descriptor or clock proof is not supplied by this public example.
    assert setting.expected_source_identity is None
    assert setting.key.instrument == config.instruments_by_ref["main"]
    api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=parts()["security_profiles"])
    store = SimpleNamespace(gateway_strategy_config=config, gateway_api=api)
    adapter = adapter_module.GatewayMarketAdapter(store, "IF2612", SimpleNamespace())
    assert adapter._key == setting.key
    assert adapter._projector is not None
    api.close()


def test_bt_adapter_fails_closed_without_real_subscription_contract(raw):
    from types import SimpleNamespace
    adapter_module = pytest.importorskip("backtrader.service.gateway_market_adapter")

    for change in ("missing_descriptor", "wrong_instrument", "wrong_channel"):
        candidate = copy.deepcopy(raw)
        sub = candidate["market_settings"]["subscriptions"]["main"]
        if change == "missing_descriptor":
            sub.pop("require_source_descriptor")
        elif change == "wrong_instrument":
            sub["key"]["instrument"]["venue"] = "other"
        else:
            sub["key"]["channel"] = "trades"
        config = parse_gateway_strategy_config(candidate)
        api = config.create_api(host=Host(), cancel_host=Host(), security_profiles=parts()["security_profiles"])
        store = SimpleNamespace(gateway_strategy_config=config, gateway_api=api)
        with pytest.raises(ValueError, match="GATEWAY_MARKET_"):
            adapter_module.GatewayMarketAdapter(store, "IF2612", SimpleNamespace())
        api.close()


def test_strategy_finance_currency_is_explicit_exact_and_does_not_grant(raw):
    config = parse_gateway_strategy_config(raw)
    assert config.strategy_finance_currency == "CNY"
    assert "direct.strategy_financial" in config.requested_operations
    raw["strategy_finance_currency"] = "cNy"
    raw["requested_operations"].remove("direct.strategy_financial")
    config = parse_gateway_strategy_config(raw)
    assert config.strategy_finance_currency == "cNy"
    assert "direct.strategy_financial" not in config.requested_operations
    raw.pop("strategy_finance_currency")
    assert parse_gateway_strategy_config(raw).strategy_finance_currency is None


@pytest.mark.parametrize("currency", [None, "", " CNY", "CNY ", "CN\nY", "A" * 257, 1, True])
def test_strategy_finance_currency_rejects_invalid_values(raw, currency):
    raw["strategy_finance_currency"] = currency
    with pytest.raises(GatewayStrategyConfigError, match="INVALID_GATEWAY_STRATEGY_CONFIG"):
        parse_gateway_strategy_config(raw)
