"""
Upbit 交易所注册模块
将 Upbit Spot 的 feed 类、流式数据类、交易所配置类注册到全局 ExchangeRegistry
导入此模块即可完成注册
"""

from bt_api_py.containers.exchanges.upbit_exchange_data import UpbitExchangeDataSpot
from bt_api_py.feeds.live_upbit.spot import UpbitSpotFeed
from bt_api_py.registry import ExchangeRegistry


def _upbit_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """Upbit 订阅处理函数"""
    exchange_data = UpbitExchangeDataSpot()

    # 提取要订阅的频道
    topic_list = [i["topic"] for i in topics]

    # WebSocket 连接参数
    wss_kwargs = dict(exchange_params.items())
    wss_kwargs["wss_name"] = "upbit_spot_data"
    wss_kwargs["wss_url"] = exchange_data.wss_url
    wss_kwargs["exchange_data"] = exchange_data
    wss_kwargs["topics"] = topics

    # 创建并启动 WebSocket Feed
    feed = UpbitSpotFeed(config=exchange_params)
    feed.ws_queue = data_queue

    # 根据订阅的主题处理
    if "ticker" in topic_list:
        # 订阅 ticker 数据
        for topic in topics:
            if topic["topic"] == "ticker":
                symbols = topic["codes"] if "codes" in topic else [exchange_params.get("symbol", "KRW-BTC")]
                for symbol in symbols:
                    feed.subscribe_ticker(symbol)

    if "trade" in topic_list:
        # 订阅成交数据
        for topic in topics:
            if topic["topic"] == "trade":
                symbols = topic["codes"] if "codes" in topic else [exchange_params.get("symbol", "KRW-BTC")]
                for symbol in symbols:
                    feed.subscribe_trades(symbol)

    if "orderbook" in topic_list:
        # 订阅订单簿数据
        for topic in topics:
            if topic["topic"] == "orderbook":
                symbols = topic["codes"] if "codes" in topic else [exchange_params.get("symbol", "KRW-BTC")]
                for symbol in symbols:
                    feed.subscribe_orderbook(symbol)

    # 启动 WebSocket 连接
    feed.connect_ws()
    bt_api.log("Upbit WebSocket started")


def register_upbit():
    """注册 Upbit Spot 到全局 ExchangeRegistry"""
    # 注册 Feed 类
    ExchangeRegistry.register_feed("UPBIT___SPOT", UpbitSpotFeed)

    # 注册 Exchange Data 类
    ExchangeRegistry.register_exchange_data("UPBIT___SPOT", UpbitExchangeDataSpot)

    # 注册订阅处理函数
    ExchangeRegistry.register_stream("UPBIT___SPOT", "subscribe", _upbit_subscribe_handler)


# 模块导入时自动注册
register_upbit()