"""The CTP trading profile accepts strategy cash settings only where they can work."""
from dataclasses import replace
from decimal import Decimal

import pytest

from bt_api_py import ctp_gateway_trading as module
from bt_api_py.ctp_gateway_strategy_financial import CtpStrategyFinancialSettings
from bt_api_py.gateway_direct_service import GatewayControlGrant
from bt_api_py.service_config import parse_service_config
from bt_api_py.strategy_rates import parse_strategy_rates
from tests.test_iter63_ctp_gateway_trading import CLIENT_A, _read_profile

RATES = parse_strategy_rates(b"""schema_version: 1
currency: CNY
products:
  IF:
    multiplier: 300
    margin_ratio: {long: "0.12", short: "0.12"}
    commission:
      open: {per_lot: "0", ratio: "0.000023"}
      close: {per_lot: "0", ratio: "0.000023"}
      close_today: {per_lot: "0", ratio: "0.00023"}
""")


def test_strategy_cash_settings_need_the_financial_option_and_exclude_a_custom_source(tmp_path, monkeypatch):
    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch)
    financial = CtpStrategyFinancialSettings("CNY", RATES, {CLIENT_A.actor_ref: Decimal("500000.00")})

    def build(config, **extra):
        return module.build_ctp_gateway_trading_profile(
            config, authority, tmp_path / "registry.sqlite", profile_ref="cash", settings=settings,
            grants=grants, request_feed_factory=lambda: None, market_stream_factory=lambda *_: None,
            assert_admitted=lambda _: True, account_read_settings=read,
            account_read_principals=frozenset({CLIENT_A.principal_ref}),
            return_settings=module.CtpGatewayReturnSettings(read.observation_instruments), **extra)

    build(config)                                               # the plain profile is valid
    # Without the journal's financial tables nothing could store a snapshot.
    with pytest.raises(module.CtpGatewayTradingError, match="^INVALID_STRATEGY_FINANCIAL_SOURCE$"):
        build(config, strategy_financial_settings=financial)
    raw = config.to_public_dict()
    raw["components"]["gateway"]["settings"]["options"].update(
        dispatch_enabled=True, cancel_enabled=True, control_enabled=True, strategy_ledger_enabled=True,
        account_extensions_enabled=True, account_facts_financial_enabled=True)
    enabled = parse_service_config(raw)
    settings = replace(settings, write_enabled=True)
    operations = frozenset({"direct.control.status"})
    journal = {"control_grants": {CLIENT_A.principal_ref: GatewayControlGrant(
                   CLIENT_A.actor_ref, True, frozenset(), operations)},
               "upgrade_controls_from_v9": True, "upgrade_strategy_ledger_from_v10": True,
               "upgrade_account_extensions_from_v11": True, "upgrade_parent_authority_from_v12": True,
               "upgrade_account_facts_financial_from_v13": True}
    build(enabled, **journal)
    build(enabled, strategy_financial_settings=financial, **journal)
    with pytest.raises(module.CtpGatewayTradingError, match="^INVALID_STRATEGY_FINANCIAL_SOURCE$"):
        build(enabled, strategy_financial_settings={"budgets": {}}, **journal)
    # One producer per journal: the profile's own or a caller's, never both.
    with pytest.raises(module.CtpGatewayTradingError, match="^INVALID_STRATEGY_FINANCIAL_SOURCE$"):
        build(enabled, strategy_financial_settings=financial,
              strategy_financial_source_factory=lambda *_args: None, **journal)
