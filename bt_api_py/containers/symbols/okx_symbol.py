import time
import json
from bt_api_py.functions.utils import from_dict_get_string, from_dict_get_float, from_dict_get_bool
from bt_api_py.containers.symbols.symbol import SymbolData


class OkxSymbolData(SymbolData):
    """https://www.okx.com/docs-v5/zh/#trading-account-rest-api-get-instruments"""
    def __init__(self, symbol_info, has_been_json_encoded):
        super(OkxSymbolData, self).__init__(symbol_info, has_been_json_encoded)
        self.event = "OkxSymbolEvent"
        self.local_update_time = time.time()  # 本地时间戳
        self.exchange_name = "OKX"
        self.symbol_name = None
        self.asset_type = None
        self.symbol_data = self.symbol_info if has_been_json_encoded else None
        self.server_time = None
        self.fee_digital = None
        self.fee_currency = None
        self.time_in_force = None
        self.order_types = None
        self.quote_asset_digital = None
        self.base_asset_digital = None
        self.min_qty = None
        self.max_qty = None
        self.qty_digital = None
        self.qty_unit = None
        self.max_price = None
        self.min_price = None
        self.price_digital = None
        self.price_unit = None
        self.contract_multiplier = None
        self.min_amount = None
        self.quote_asset = None
        self.base_asset = None
        self.required_margin_percent = None
        self.maintain_margin_percent = None
        self.all_data = None
        self.underlying_index_name = None
        self.underlying_symbol_name = None
        self.max_twap_qty = None
        self.max_market_amount = None
        self.max_limit_amount = None
        self.max_market_qty = None
        self.max_limit_qty = None
        self.symbol_trading_type = None
        self.symbol_status = None
        self.contract_type = None
        self.max_leverage = None
        self.delist_time = None
        self.auction_end_time = None
        self.max_iceberg_qty = None
        self.max_stop_qty = None
        self.future_settlement = None
        self.list_time = None
        self.option_strike_price = None
        self.option_type = None
        self.contract_notional_value = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.symbol_info = json.loads(self.symbol_info)
            self.symbol_data = self.symbol_info['data'][0]
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.symbol_name = from_dict_get_string(self.symbol_data, "instId")
        self.asset_type = from_dict_get_string(self.symbol_data, "instType")
        self.base_asset = from_dict_get_string(self.symbol_data, "baseCcy")
        self.quote_asset = from_dict_get_string(self.symbol_data, "quoteCcy")
        self.contract_multiplier = from_dict_get_float(self.symbol_data, "ctMult")
        self.contract_notional_value = from_dict_get_float(self.symbol_data, "ctVal")
        self.min_amount = from_dict_get_float(self.symbol_data, "notional")
        self.price_unit = from_dict_get_float(self.symbol_data, "tickSz")
        print("self.symbol_data = ", self.symbol_data)
        print("self.price_unit = ", self.price_unit)
        self.price_digital = 1/self.price_unit
        self.qty_unit = from_dict_get_float(self.symbol_data, 'lotSz')
        self.qty_digital = 1/self.qty_unit
        self.max_qty = from_dict_get_float(self.symbol_data, "maxLmtAmt")
        self.min_qty = from_dict_get_float(self.symbol_data, "minSz")
        self.fee_currency = from_dict_get_string(self.symbol_data, "settleCcy")
        self.underlying_index_name = from_dict_get_string(self.symbol_data, "uly")
        self.underlying_symbol_name = from_dict_get_string(self.symbol_data, "instFamily")
        self.option_type = from_dict_get_string(self.symbol_data, "optType")
        self.option_strike_price = from_dict_get_float(self.symbol_data, "stk")
        self.list_time = from_dict_get_float(self.symbol_data, "listTime")
        self.auction_end_time = from_dict_get_float(self.symbol_data, "auctionEndTime")
        self.delist_time = from_dict_get_float(self.symbol_data, "expTime")
        self.max_leverage = from_dict_get_float(self.symbol_data, "lever")
        self.contract_type = from_dict_get_string(self.symbol_data, "ctType")
        self.symbol_status = from_dict_get_string(self.symbol_data, "state")
        self.symbol_trading_type = from_dict_get_string(self.symbol_data, "ruleType")
        self.max_limit_qty = from_dict_get_float(self.symbol_data, "maxLmtSz")
        self.max_market_qty = from_dict_get_float(self.symbol_data, "maxMktSz")
        self.max_limit_amount = from_dict_get_float(self.symbol_data, "maxLmtAmt")
        self.max_market_amount = from_dict_get_float(self.symbol_data, "maxMktAmt")
        self.max_twap_qty = from_dict_get_float(self.symbol_data, "maxTwapSz")
        self.max_iceberg_qty = from_dict_get_float(self.symbol_data, "maxIcebergSz")
        self.max_stop_qty = from_dict_get_float(self.symbol_data, "maxStopSz")
        self.future_settlement = from_dict_get_bool(self.symbol_data, "futureSettlement")
        self.has_been_init_data = True

    def get_local_update_time(self):
        return self.local_update_time

    def get_underlying_symbol_name(self):
        return self.underlying_symbol_name

    def get_underlying_index_name(self):
        return self.underlying_index_name

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "server_time": getattr(self, "server_time", None),
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "fee_currency": self.fee_currency,
                "min_amount": self.min_amount,
                "min_qty": self.min_qty,
                "max_qty": self.max_qty,
                "qty_digital": self.qty_digital,
                "qty_unit": self.qty_unit,
                "price_digital": self.price_digital,
                "price_unit": self.price_unit,
                "contract_multiplier": self.contract_multiplier,
                "quote_asset": self.quote_asset,
                "base_asset": self.base_asset,
                "underlying_index_name": self.underlying_index_name,
                "underlying_symbol_name": self.underlying_symbol_name,
                "option_type": self.option_type,
                "option_strike_price": self.option_strike_price,
                "list_time": self.list_time,
                "auction_end_time": self.auction_end_time,
                "delist_time": self.delist_time,
                "max_leverage": self.max_leverage,
                "contract_type": self.contract_type,
                "symbol_status": self.symbol_status,
                "symbol_trading_type": self.symbol_trading_type,
                "max_limit_qty": self.max_limit_qty,
                "max_market_qty": self.max_market_qty,
                "max_limit_amount": self.max_limit_amount,
                "max_market_amount": self.max_market_amount,
                "max_twap_qty": self.max_twap_qty,
                "max_iceberg_qty": self.max_iceberg_qty,
                "max_stop_qty": self.max_stop_qty,
                "future_settlement": self.future_settlement,
            }
        return self.all_data

    def get_maintain_margin_percent(self):
        return self.maintain_margin_percent

    def get_required_margin_percent(self):
        return self.required_margin_percent

    def get_base_asset(self):
        return self.base_asset

    def get_quote_asset(self):
        return self.quote_asset

    def get_contract_multiplier(self):
        return self.contract_multiplier

    def get_price_unit(self):
        return self.price_unit

    def get_price_digital(self):
        return self.price_digital

    def get_max_price(self):
        return self.max_price

    def get_min_price(self):
        return self.min_price

    def get_min_amount(self):
        return self.min_amount

    def get_qty_unit(self):
        return self.qty_unit

    def get_qty_digital(self):
        return self.qty_digital

    def get_min_qty(self):
        return self.min_qty

    def get_max_qty(self):
        return self.max_qty

    def get_base_asset_digital(self):
        return self.base_asset_digital

    def get_quote_asset_digital(self):
        return self.quote_asset_digital

    def get_order_types(self):
        return self.order_types

    def get_time_in_force(self):
        return self.time_in_force

    def get_fee_digital(self):
        return self.fee_digital

    def get_fee_currency(self):
        return self.fee_currency

    def get_server_time(self):
        return self.server_time

    def get_exchange_name(self):
        return self.exchange_name

    def get_symbol_status(self):
        return self.symbol_status

    def get_symbol_trading_type(self):
        return self.symbol_trading_type

    def get_contract_type(self):
        return self.contract_type

    def get_max_leverage(self):
        return self.max_leverage

    def get_max_limit_amount(self):
        return self.max_limit_amount

    def get_max_market_amount(self):
        return self.max_market_amount

    def get_max_limit_qty(self):
        return self.max_limit_qty

    def get_max_market_qty(self):
        return self.max_market_qty

    def get_max_twap_qty(self):
        return self.max_twap_qty

    def get_max_iceberg_qty(self):
        return self.max_iceberg_qty

    def get_max_stop_qty(self):
        return self.max_stop_qty

    def get_future_settlement(self):
        return self.future_settlement

    def get_list_time(self):
        return self.list_time

    def get_auction_end_time(self):
        return self.auction_end_time

    def get_delist_time(self):
        return self.delist_time

    def get_option_strike_price(self):
        return self.option_strike_price

    def get_option_type(self):
        return self.option_type

    def get_contract_notional_value(self):
        return self.contract_notional_value

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()