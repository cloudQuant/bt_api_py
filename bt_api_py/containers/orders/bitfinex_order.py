import json
import time

from bt_api_py.containers.orders.order import OrderData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_int, from_dict_get_string


class BitfinexOrderData(OrderData):
    """保存 Bitfinex 订单信息"""

    def __init__(self, order_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(order_info, has_been_json_encoded)
        self.exchange_name = "BITFINEX"  # 交易所名称
        self.local_update_time = time.time()  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type  # 订单的类型
        self.order_data = order_info if has_been_json_encoded else None
        self.order_id = None
        self.group_id = None
        self.client_order_id = None
        self.symbol = None
        self.mts_create = None
        self.mts_update = None
        self.amount = None
        self.amount_orig = None
        self.type = None
        self.type_prev = None
        self.flags = None
        self.status = None
        self.price = None
        self.price_avg = None
        self.price_trail = None
        self.price_aux_limit = None
        self.notify = None
        self.hidden = None
        self.placed_id = None
        self.routing = None
        self.meta = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            if isinstance(self.order_info, str):
                self.order_data = json.loads(self.order_info)
            else:
                self.order_data = self.order_info
            self.has_been_json_encoded = True

        if self.has_been_init_data:
            return self

        if isinstance(self.order_data, list) and len(self.order_data) >= 16:
            # Bitfinex 订单格式:
            # [ID, GID, CID, SYMBOL, MTS_CREATE, MTS_UPDATE, AMOUNT, AMOUNT_ORIG, TYPE,
            #  TYPE_PREV, ..., FLAGS, STATUS, ..., PRICE, PRICE_AVG, ..., AUX_PRICE]
            self.order_id = from_dict_get_int(self.order_data[0], None)
            self.group_id = from_dict_get_int(self.order_data[1], None)
            self.client_order_id = from_dict_get_int(self.order_data[2], None)
            self.symbol = from_dict_get_string(self.order_data[3], "")
            self.mts_create = from_dict_get_int(self.order_data[4], None)
            self.mts_update = from_dict_get_int(self.order_data[5], None)
            self.amount = from_dict_get_float(self.order_data[6], 0.0)
            self.amount_orig = from_dict_get_float(self.order_data[7], 0.0)
            self.type = from_dict_get_string(self.order_data[8], "")
            self.type_prev = from_dict_get_string(self.order_data[9], "")
            self.flags = from_dict_get_int(self.order_data[10], 0)
            self.status = from_dict_get_string(self.order_data[11], "")
            self.price = from_dict_get_float(self.order_data[12], 0.0)
            self.price_avg = from_dict_get_float(self.order_data[13], 0.0)
            self.price_trail = from_dict_get_float(self.order_data[14], 0.0)
            self.price_aux_limit = from_dict_get_float(self.order_data[15], 0.0)

            # 处理额外字段（如果存在）
            if len(self.order_data) > 16:
                self.notify = from_dict_get_int(self.order_data[16], 0)
            if len(self.order_data) > 17:
                self.hidden = from_dict_get_int(self.order_data[17], 0)
            if len(self.order_data) > 18:
                self.placed_id = from_dict_get_int(self.order_data[18], None)
            if len(self.order_data) > 19:
                self.routing = from_dict_get_string(self.order_data[19], "")
            if len(self.order_data) > 20:
                self.meta = self.order_data[20]  # 通常是字典

        elif isinstance(self.order_data, dict):
            # 处理字典格式的响应
            self.order_id = from_dict_get_int(self.order_data, "id", None)
            self.group_id = from_dict_get_int(self.order_data, "gid", None)
            self.client_order_id = from_dict_get_int(self.order_data, "cid", None)
            self.symbol = from_dict_get_string(self.order_data, "symbol", "")
            self.mts_create = from_dict_get_int(self.order_data, "mts_create", None)
            self.mts_update = from_dict_get_int(self.order_data, "mts_update", None)
            self.amount = from_dict_get_float(self.order_data, "amount", 0.0)
            self.amount_orig = from_dict_get_float(self.order_data, "amount_orig", 0.0)
            self.type = from_dict_get_string(self.order_data, "type", "")
            self.type_prev = from_dict_get_string(self.order_data, "type_prev", "")
            self.flags = from_dict_get_int(self.order_data, "flags", 0)
            self.status = from_dict_get_string(self.order_data, "status", "")
            self.price = from_dict_get_float(self.order_data, "price", 0.0)
            self.price_avg = from_dict_get_float(self.order_data, "price_avg", 0.0)
            self.price_trail = from_dict_get_float(self.order_data, "price_trail", 0.0)
            self.price_aux_limit = from_dict_get_float(self.order_data, "aux_price", 0.0)
            self.notify = from_dict_get_int(self.order_data, "notify", 0)
            self.hidden = from_dict_get_int(self.order_data, "hidden", 0)
            self.placed_id = from_dict_get_int(self.order_data, "placed_id", None)
            self.routing = from_dict_get_string(self.order_data, "routing", "")
            self.meta = self.order_data.get("meta")

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "order_id": self.order_id,
                "group_id": self.group_id,
                "client_order_id": self.client_order_id,
                "symbol": self.symbol,
                "mts_create": self.mts_create,
                "mts_update": self.mts_update,
                "amount": self.amount,
                "amount_orig": self.amount_orig,
                "type": self.type,
                "type_prev": self.type_prev,
                "flags": self.flags,
                "status": self.status,
                "price": self.price,
                "price_avg": self.price_avg,
                "price_trail": self.price_trail,
                "price_aux_limit": self.price_aux_limit,
                "notify": self.notify,
                "hidden": self.hidden,
                "placed_id": self.placed_id,
                "routing": self.routing,
                "meta": self.meta,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        return self.exchange_name

    def get_local_update_time(self):
        return self.local_update_time

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_order_id(self):
        return self.order_id

    def get_group_id(self):
        return self.group_id

    def get_client_order_id(self):
        return self.client_order_id

    def get_symbol(self):
        return self.symbol

    def get_mts_create(self):
        return self.mts_create

    def get_mts_update(self):
        return self.mts_update

    def get_amount(self):
        return self.amount

    def get_amount_orig(self):
        return self.amount_orig

    def get_type(self):
        return self.type

    def get_type_prev(self):
        return self.type_prev

    def get_flags(self):
        return self.flags

    def get_status(self):
        return self.status

    def get_price(self):
        return self.price

    def get_price_avg(self):
        return self.price_avg

    def get_price_trail(self):
        return self.price_trail

    def get_price_aux_limit(self):
        return self.price_aux_limit

    def get_notify(self):
        return self.notify

    def get_hidden(self):
        return self.hidden

    def get_placed_id(self):
        return self.placed_id

    def get_routing(self):
        return self.routing

    def get_meta(self):
        return self.meta

    def get_side(self):
        """获取订单方向 (BUY/SELL)"""
        if self.type:
            return "BUY" if self.type.upper().startswith("BUY") or self.amount > 0 else "SELL"
        return None

    def get_order_state(self):
        """获取订单状态"""
        return self.status

    def is_active(self):
        """检查订单是否活跃"""
        return self.status in ["ACTIVE", "PARTIALLY FILLED", "OPEN"]

    def is_filled(self):
        """检查订单是否完全成交"""
        return self.status == "FILLED"

    def is_cancelled(self):
        """检查订单是否已取消"""
        return self.status == "CANCELED"

    def get_filled_amount(self):
        """获取已成交数量"""
        return (
            self.amount_orig - self.amount
            if self.amount_orig is not None and self.amount is not None
            else 0
        )

    def get_fill_percentage(self):
        """获取成交百分比"""
        if self.amount_orig is None or self.amount_orig == 0:
            return 0
        filled = self.get_filled_amount()
        return (filled / self.amount_orig) * 100


class BitfinexWssOrderData(BitfinexOrderData):
    """保存 Bitfinex WebSocket 订单信息"""

    pass  # WebSocket 订单格式与 REST API 相同


class BitfinexRequestOrderData(BitfinexOrderData):
    """保存 Bitfinex REST API 订单信息"""

    pass  # REST API 订单格式直接处理
