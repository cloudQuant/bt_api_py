import time
import json
from bt_api_py.functions.utils import from_dict_get_string, from_dict_get_float, from_dict_get_int
from bt_api_py.containers.symbols.symbol import SymbolData


class BinanceSwapSymbolData(SymbolData):
    def __init__(self, symbol_info, has_been_json_encoded):
        super(BinanceSwapSymbolData, self).__init__(symbol_info, has_been_json_encoded)
        self.event = "BinanceSymbolEvent"
        self.local_update_time = time.time()  # 本地时间戳
        self.exchange_name = "BINANCE"
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
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.symbol_info = json.loads(self.symbol_info)
            self.symbol_data = self.symbol_info['symbols']
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.symbol_name = from_dict_get_string(self.symbol_info, "symbol")
        self.asset_type = from_dict_get_string(self.symbol_info, "contractType")
        self.maintain_margin_percent = from_dict_get_float(self.symbol_info, "maintMarginPercent")
        self.required_margin_percent = from_dict_get_float(self.symbol_info, "requiredMarginPercent")
        self.base_asset = from_dict_get_string(self.symbol_info, "baseAsset")
        self.quote_asset = from_dict_get_string(self.symbol_info, "quoteAsset")
        self.contract_multiplier = 1
        self.min_amount = from_dict_get_float(self.symbol_info["filters"][5], "notional")
        self.price_unit = from_dict_get_float(self.symbol_info["filters"][0], "tickSize")
        self.price_digital = from_dict_get_int(self.symbol_info, "pricePrecision")
        self.min_price = from_dict_get_float(self.symbol_info["filters"][0], "minPrice")
        self.max_price = from_dict_get_float(self.symbol_info["filters"][0], "maxPrice")
        self.qty_unit = from_dict_get_float(self.symbol_info["filters"][1], "stepSize")
        self.qty_digital = from_dict_get_int(self.symbol_info, "quantityPrecision")
        self.max_qty = from_dict_get_float(self.symbol_info["filters"][1], "maxQty")
        self.min_qty = from_dict_get_float(self.symbol_info["filters"][1], "minQty")
        self.base_asset_digital = from_dict_get_int(self.symbol_info, "baseAssetPrecision")
        self.quote_asset_digital = from_dict_get_int(self.symbol_info, "quoteAssetPrecision")
        self.order_types = self.symbol_info["orderTypes"]
        self.time_in_force = self.symbol_info["timeInForce"]
        self.fee_currency = from_dict_get_string(self.symbol_info, "quoteAsset")
        self.fee_digital = from_dict_get_int(self.symbol_info, "quotePrecision")
        self.has_been_init_data = True

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "server_time": self.server_time,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "fee_digital": self.fee_digital,
                "fee_currency": self.fee_currency,
                "time_in_force": self.time_in_force,
                "order_types": self.order_types,
                "quote_asset_digital": self.quote_asset_digital,
                "base_asset_digital": self.base_asset_digital,
                "min_amount": self.min_amount,
                "min_qty": self.min_qty,
                "max_qty": self.max_qty,
                "qty_digital": self.qty_digital,
                "qty_unit": self.qty_unit,
                "max_price": self.max_price,
                "min_price": self.min_price,
                "price_digital": self.price_digital,
                "price_unit": self.price_unit,
                "contract_multiplier": self.contract_multiplier,
                "quote_asset": self.quote_asset,
                "base_asset": self.base_asset,
                "require_margin_percent": self.required_margin_percent,
                "maintain_margin_percent": self.maintain_margin_percent
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

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()


class BinanceSpotSymbolData(SymbolData):
    def __init__(self, symbol_info, has_been_json_encoded):
        super(BinanceSpotSymbolData, self).__init__(symbol_info, has_been_json_encoded)
        self.event = "BinanceSymbolEvent"
        self.local_update_time = time.time()  # 本地时间戳
        self.exchange_name = "BINANCE"
        self.symbol_name = None
        self.asset_type = "SPOT"
        self.symbol_data = self.symbol_info if has_been_json_encoded else None
        self.server_time = None
        self.fee_digital = None
        self.fee_currency = None
        self.time_in_force = None
        self.order_types = None
        self.quote_asset_digital = None
        self.base_asset_digital = None
        self.min_amount = None
        self.min_qty = None
        self.max_qty = None
        self.qty_digital = None
        self.qty_unit = None
        self.max_price = None
        self.min_price = None
        self.price_digital = None
        self.price_unit = None
        self.contract_multiplier = None
        self.quote_asset = None
        self.base_asset = None
        self.required_margin_percent = None
        self.maintain_margin_percent = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.symbol_info = json.loads(self.symbol_info)
            self.symbol_data = self.symbol_info['symbols']
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.symbol_name = from_dict_get_string(self.symbol_info, "symbol")
        self.base_asset = from_dict_get_string(self.symbol_info, "baseAsset")
        self.quote_asset = from_dict_get_string(self.symbol_info, "quoteAsset")
        self.contract_multiplier = 1
        self.price_unit = from_dict_get_float(self.symbol_info["filters"][0], "tickSize")
        self.price_digital = from_dict_get_int(self.symbol_info, "quotePrecision")
        self.min_price = from_dict_get_float(self.symbol_info["filters"][0], "minPrice")
        self.max_price = from_dict_get_float(self.symbol_info["filters"][0], "maxPrice")
        self.min_amount = from_dict_get_float(self.symbol_info["filters"][6], "minNotional")
        self.qty_unit = from_dict_get_float(self.symbol_info["filters"][1], "stepSize")
        self.qty_digital = from_dict_get_int(self.symbol_info, "baseAssetPrecision")
        self.max_qty = from_dict_get_float(self.symbol_info["filters"][1], "maxQty")
        self.min_qty = from_dict_get_float(self.symbol_info["filters"][1], "minQty")
        self.base_asset_digital = from_dict_get_int(self.symbol_info, "baseAssetPrecision")
        self.quote_asset_digital = from_dict_get_int(self.symbol_info, "quoteAssetPrecision")
        self.fee_digital = max(self.base_asset_digital, self.quote_asset_digital)
        self.order_types = self.symbol_info["orderTypes"]
        self.has_been_init_data = True

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "server_time": self.server_time,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "order_types": self.order_types,
                "quote_asset_digital": self.quote_asset_digital,
                "base_asset_digital": self.base_asset_digital,
                "min_amount": self.min_amount,
                "min_qty": self.min_qty,
                "max_qty": self.max_qty,
                "qty_digital": self.qty_digital,
                "qty_unit": self.qty_unit,
                "max_price": self.max_price,
                "min_price": self.min_price,
                "price_digital": self.price_digital,
                "price_unit": self.price_unit,
                "quote_asset": self.quote_asset,
                "base_asset": self.base_asset,
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

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()