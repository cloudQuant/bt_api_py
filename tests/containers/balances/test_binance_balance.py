from btpy.containers.balances.binance_balance import (BinanceSwapWssBalanceData,
                                                      BinanceSwapRequestBalanceData,
                                                      BinanceSpotRequestBalanceData,
                                                      BinanceSpotWssBalanceData)


def test_binance_spot_wss_balance_data():
    data = {'a': 'USDT', 'f': '29.24200000', 'l': '6.75800000'}
    spot_wss_data = BinanceSpotWssBalanceData(data, "USDT", "SPOT", True)
    spot_wss_data.init_data()
    assert spot_wss_data.get_margin() == 29.24200000 + 6.75800000
    assert spot_wss_data.get_used_margin() == 6.75800000
    assert spot_wss_data.get_available_margin() == 29.24200000


def test_binance_spot_request_balance():
    data = {'asset': 'BTC', 'free': '0.00000000', 'locked': '0.00000000'}
    bal = BinanceSpotRequestBalanceData(data, "BTC", "SPOT", True)
    bal.init_data()
    assert bal is not None
    assert isinstance(bal, BinanceSpotRequestBalanceData)
    assert bal.get_symbol_name() == "BTC"


def test_binance_request_account_balance():
    data = {
        "asset": "USDT",  # 资产
        "walletBalance": "23.72469206",  # 余额
        "unrealizedProfit": "0.00000000",  # 未实现盈亏
        "marginBalance": "23.72469206",  # 保证金余额
        "maintMargin": "0.00000000",  # 维持保证金
        "initialMargin": "0.00000000",  # 当前所需起始保证金
        "positionInitialMargin": "0.00000000",  # 持仓所需起始保证金(基于最新标记价格)
        "openOrderInitialMargin": "0.00000000",  # 当前挂单所需起始保证金(基于最新标记价格)
        "crossWalletBalance": "23.72469206",  # 全仓账户余额
        "crossUnPnl": "0.00000000",  # 全仓持仓未实现盈亏
        "availableBalance": "126.72469206",  # 可用余额
        "maxWithdrawAmount": "23.72469206",  # 最大可转出余额
        "marginAvailable": 'true',  # 是否可用作联合保证金
        "updateTime": 1625474304765  # 更新时间
    }
    symbol = data['asset']
    asset_type = data['asset']
    data = BinanceSwapRequestBalanceData(data, symbol, asset_type, True)
    data.init_data()
    assert data.get_position_initial_margin() == 0.0
    assert data.get_unrealized_profit() == 0.0


def test_binance_request_balance():
    data = {
        "accountAlias": "SgsR",  # 账户唯一识别码
        "asset": "USDT",  # 资产
        "balance": "122607.35137903",  # 总余额
        "crossWalletBalance": "23.72469206",  # 全仓余额
        "crossUnPnl": "0.00000000",  # 全仓持仓未实现盈亏
        "availableBalance": "23.72469206",  # 下单可用余额
        "maxWithdrawAmount": "23.72469206",  # 最大可转出余额
        "marginAvailable": 'true',  # 是否可用作联合保证金
        "updateTime": 1617939110373
    }

    bo = BinanceSwapRequestBalanceData(data, "USDT", "SWAP", True)
    bo.init_data()
    assert isinstance(bo, BinanceSwapRequestBalanceData)
    assert bo.get_account_id() == data["accountAlias"]
    assert bo.get_server_time() == float(data["updateTime"])
    assert bo.get_max_withdraw_amount() == float(data["maxWithdrawAmount"])
    assert bo.get_margin() == float(data["balance"])
    assert bo.get_available_margin() == float(data["availableBalance"])
    assert bo.get_unrealized_profit() == float(data["crossUnPnl"])


def test_binance_wss_balance():
    data = {
        "a": "USDT",  # 资产名称
        "wb": "122624.12345678",  # 钱包余额
        "cw": "100.12345678",  # 除去逐仓仓位保证金的钱包余额
        "bc": "50.12345678"  # 除去盈亏与交易手续费以外的钱包余额改变量
    }

    bo = BinanceSwapWssBalanceData(data, "USDT", "SWAP", True)
    bo.init_data()
    assert bo.get_margin() == float(data["wb"])
    assert bo.get_account_type() == data["a"]


if __name__ == "__main__":
    test_binance_request_balance()
    test_binance_wss_balance()
