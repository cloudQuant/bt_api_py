"""
Interactive Brokers 交易所注册模块
将 IB Stock/Future 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册

注意: IB feed 目前为骨架实现，需要安装 ib_insync 并完善具体方法后才能使用
"""
from bt_api_py.registry import ExchangeRegistry
from bt_api_py.feeds.live_ib_feed import (
    IbRequestDataStock,
    IbRequestDataFuture,
    IbDataStream,
    IbAccountStream,
)
from bt_api_py.containers.exchanges.ib_exchange_data import (
    IbExchangeDataStock,
    IbExchangeDataFuture,
)


from bt_api_py.balance_utils import simple_balance_handler as _ib_balance_handler


def _ib_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """IB 通用订阅处理函数"""
    bt_api.log("IB subscribe handler called - skeleton implementation", level="warning")
    raise NotImplementedError(
        "IB subscribe handler not yet implemented. "
        "Install ib_insync and complete the IbDataStream/IbAccountStream implementations."
    )


def register_ib():
    """注册 IB Stock 和 Future 到全局 ExchangeRegistry"""
    # Stock
    ExchangeRegistry.register_feed("IB___STK", IbRequestDataStock)
    ExchangeRegistry.register_exchange_data("IB___STK", IbExchangeDataStock)
    ExchangeRegistry.register_balance_handler("IB___STK", _ib_balance_handler)
    ExchangeRegistry.register_stream("IB___STK", "subscribe", _ib_subscribe_handler)
    ExchangeRegistry.register_stream("IB___STK", "market", IbDataStream)
    ExchangeRegistry.register_stream("IB___STK", "account", IbAccountStream)

    # Future
    ExchangeRegistry.register_feed("IB___FUT", IbRequestDataFuture)
    ExchangeRegistry.register_exchange_data("IB___FUT", IbExchangeDataFuture)
    ExchangeRegistry.register_balance_handler("IB___FUT", _ib_balance_handler)
    ExchangeRegistry.register_stream("IB___FUT", "subscribe", _ib_subscribe_handler)
    ExchangeRegistry.register_stream("IB___FUT", "market", IbDataStream)
    ExchangeRegistry.register_stream("IB___FUT", "account", IbAccountStream)


# 模块导入时自动注册
register_ib()
