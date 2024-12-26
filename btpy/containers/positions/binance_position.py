import time
import json
from btpy.containers.positions.position import PositionData
from btpy.functions.utils import from_dict_get_float, from_dict_get_string, from_dict_get_bool


class BinancePositionData(PositionData):
    """保存持仓信息"""

    def __init__(self, position_info, symbol_name, asset_type, has_been_json_encoded):
        super(BinancePositionData, self).__init__(position_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.local_update_time = time.time()  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.position_data = position_info if has_been_json_encoded else None
        self.server_time = None
        self.margin_type = None
        self.is_isolated = None
        self.is_auto_add_margin = None
        self.leverage = None
        self.max_notional_value = None
        self.position_symbol_name = None
        self.position_volume = None
        self.position_side = None
        self.avg_price = None
        self.mark_price = None
        self.position_unrealized_pnl = None
        self.position_realized_pnl = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        raise NotImplementedError

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "server_time": self.server_time,
                "margin_type": self.margin_type,
                "is_isolated": self.is_isolated,
                "is_auto_add_margin": self.is_auto_add_margin,
                "leverage": self.leverage,
                "max_notional_value": self.max_notional_value,
                "position_symbol_name": self.position_symbol_name,
                "position_volume": self.position_volume,
                "position_side": self.position_side,
                "avg_price": self.avg_price,
                "mark_price": self.mark_price,
                "position_unrealized_pnl": self.position_unrealized_pnl,
                "position_realized_pnl": self.position_realized_pnl,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        """# 交易所名称"""
        return self.exchange_name

    def get_asset_type(self):
        """# 资产类型"""
        return self.asset_type

    def get_symbol_name(self):
        return self.symbol_name

    def get_server_time(self):
        """# 服务器时间戳"""
        return self.server_time

    def get_local_update_time(self):
        """# 本地时间戳"""
        return self.local_update_time

    def get_account_id(self):
        """# 账户id"""
        return None

    def get_position_id(self):
        """# 持仓id"""
        return None

    def get_is_isolated(self):
        """# 是否是逐仓模式"""
        return self.is_isolated

    def get_margin_type(self):
        """# 保证金类型"""
        return self.margin_type

    def get_is_auto_add_margin(self):
        """# 是否可以自动增加保证金"""
        return self.is_auto_add_margin

    def get_leverage(self):
        """# 杠杆倍率"""
        return self.leverage

    def get_max_notional_value(self):
        """# 当前杠杆下用户可用的最大名义价值"""
        return self.max_notional_value

    def get_position_symbol_name(self):
        """# 仓位的品种名称"""
        return self.position_symbol_name

    def get_position_volume(self):
        """# 持仓数量"""
        return self.position_volume

    def get_position_side(self):
        """# 持仓方向"""
        return self.position_side

    def get_trade_num(self):
        """# trade的个数"""
        return None

    def get_avg_price(self):
        """# 持仓成本价"""
        return self.avg_price

    def get_mark_price(self):
        """# 标记价格"""
        return self.mark_price

    def get_liquidation_price(self):
        """# 清算价格"""
        return None

    def get_initial_margin(self):
        """# 当前所需起始保证金(基于最新标记价格)"""
        return None

    def get_maintenance_margin(self):
        """# 维持保证金"""
        return None

    def open_order_initial_margin(self):
        """# 当前挂单所需起始保证金(基于最新标记价格)"""
        return None

    def get_position_initial_margin(self):
        """# 持仓所需起始保证金(基于最新标记价格)"""
        return None

    def get_position_commission(self):
        """# 这个position交易所耗费的手续费"""
        return None

    def get_position_realized_pnl(self):
        """# 已经实现的利润"""
        return self.position_realized_pnl

    def get_position_unrealized_pnl(self):
        """# 持仓未实现盈亏"""
        return self.position_unrealized_pnl

    def get_position_funding_value(self):
        """# 总的资金费率"""
        return None


class BinanceRequestPositionData(BinancePositionData):
    """保存持仓信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.position_data = json.loads(self.position_info)['data']
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.position_data, 'updateTime')
        self.margin_type = from_dict_get_string(self.position_data, "marginType")
        self.is_isolated = True if self.margin_type == "isolated" else False
        self.is_auto_add_margin = from_dict_get_bool(self.position_data, "isAutoAddMargin")
        self.leverage = from_dict_get_float(self.position_data, "leverage")
        self.max_notional_value = from_dict_get_float(self.position_data, "maxNotionalValue")
        self.position_symbol_name = from_dict_get_string(self.position_data, "symbol")
        self.position_volume = from_dict_get_float(self.position_data, "positionAmt")
        self.position_side = from_dict_get_string(self.position_data, "positionSide")
        self.avg_price = from_dict_get_float(self.position_data, "entryPrice")
        self.mark_price = from_dict_get_float(self.position_data, "markPrice")
        self.position_unrealized_pnl = from_dict_get_float(self.position_data, "unRealizedProfit")
        self.has_been_init_data = True
        return self


class BinanceWssPositionData(BinancePositionData):
    """保存持仓信息"""

    def init_data(self):
        if not self.has_been_json_encoded:
            self.position_data = json.loads(self.position_info)['data']
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = None
        self.margin_type = from_dict_get_string(self.position_data, 'mt')
        self.is_isolated = True if self.margin_type == "isolated" else False
        self.is_auto_add_margin = None
        self.leverage = None
        self.max_notional_value = None
        self.position_symbol_name = from_dict_get_string(self.position_data, "s")
        self.position_volume = from_dict_get_float(self.position_data, "pa")
        self.position_side = from_dict_get_string(self.position_data, "ps")
        self.avg_price = from_dict_get_float(self.position_data, "ep")
        self.mark_price = None
        self.position_unrealized_pnl = from_dict_get_float(self.position_data, 'up')
        self.position_realized_pnl = from_dict_get_float(self.position_data, 'cr')
        self.has_been_init_data = True
        return self
