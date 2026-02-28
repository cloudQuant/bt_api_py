"""
CTP 持仓数据容器
对应 CTP 的 CThostFtdcInvestorPositionField 结构体
"""

from bt_api_py.containers.positions.position import PositionData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_int, from_dict_get_string

# CTP 持仓多空方向
CTP_POS_DIRECTION_MAP = {
    "1": "net",
    "2": "long",
    "3": "short",
}


class CtpPositionData(PositionData):
    """CTP 持仓数据"""

    def __init__(
        self, position_info, symbol_name=None, asset_type="FUTURE", has_been_json_encoded=False
    ):
        super().__init__(position_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "CTP"
        self._initialized = False
        # CTP 持仓字段
        self.instrument_id = None
        self.position_direction = None  # long / short / net
        self.position_volume = None  # 持仓量
        self.today_position = None  # 今仓
        self.yd_position = None  # 昨仓
        self.open_cost = None  # 开仓成本
        self.position_cost = None  # 持仓成本
        self.use_margin = None  # 占用保证金
        self.position_profit = None  # 持仓盈亏
        self.close_profit = None  # 平仓盈亏
        self.settlement_price = None  # 结算价
        self.exchange_id = None  # 交易所代码

    def init_data(self):
        if self._initialized:
            return self
        info = self.position_info
        if isinstance(info, dict):
            self.instrument_id = from_dict_get_string(info, "InstrumentID")
            pos_dir_char = from_dict_get_string(info, "PosiDirection", "1")
            self.position_direction = CTP_POS_DIRECTION_MAP.get(pos_dir_char, "net")
            self.position_volume = from_dict_get_int(info, "Position", 0)
            self.today_position = from_dict_get_int(info, "TodayPosition", 0)
            self.yd_position = from_dict_get_int(info, "YdPosition", 0)
            self.open_cost = from_dict_get_float(info, "OpenCost", 0.0)
            self.position_cost = from_dict_get_float(info, "PositionCost", 0.0)
            self.use_margin = from_dict_get_float(info, "UseMargin", 0.0)
            self.position_profit = from_dict_get_float(info, "PositionProfit", 0.0)
            self.close_profit = from_dict_get_float(info, "CloseProfit", 0.0)
            self.settlement_price = from_dict_get_float(info, "SettlementPrice", 0.0)
            self.exchange_id = from_dict_get_string(info, "ExchangeID")
        self._initialized = True
        return self

    def get_exchange_name(self):
        return self.exchange_name

    def get_asset_type(self):
        return self.asset_type

    def get_symbol_name(self):
        return self.instrument_id or self.symbol_name

    def get_position_volume(self):
        return self.position_volume or 0

    def get_avg_price(self):
        if self.position_volume and self.position_volume > 0:
            return self.position_cost / self.position_volume
        return 0.0

    def get_mark_price(self):
        return self.settlement_price

    def get_liquidation_price(self):
        return None  # CTP 没有强平价概念

    def get_initial_margin(self):
        return self.use_margin

    def get_maintain_margin(self):
        return self.use_margin

    def get_position_unrealized_pnl(self):
        return self.position_profit or 0.0

    def get_position_funding_value(self):
        return 0.0  # CTP 没有资金费率

    def get_position_direction(self):
        """持仓方向: long / short / net"""
        return self.position_direction

    def get_today_position(self):
        """今仓量"""
        return self.today_position or 0

    def get_yesterday_position(self):
        """昨仓量"""
        return self.yd_position or 0

    def get_all_data(self):
        return {
            "exchange_name": self.exchange_name,
            "instrument_id": self.instrument_id,
            "position_direction": self.position_direction,
            "position_volume": self.position_volume,
            "today_position": self.today_position,
            "yd_position": self.yd_position,
            "use_margin": self.use_margin,
            "position_profit": self.position_profit,
            "settlement_price": self.settlement_price,
            "exchange_id": self.exchange_id,
        }
