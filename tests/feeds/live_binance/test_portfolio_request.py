"""
Tests for Binance Portfolio Margin API Request Implementation
测试 Binance 组合保证金 API 请求实现
"""

import queue

from bt_api_py.feeds.live_binance.portfolio import BinanceRequestDataPortfolio


def test_portfolio_request_init():
    """测试 Portfolio Request 初始化"""
    data_queue = queue.Queue()
    portfolio = BinanceRequestDataPortfolio(
        data_queue, public_key="test_public_key", private_key="test_private_key"
    )
    assert portfolio.asset_type == "PORTFOLIO"
    assert portfolio.logger_name == "binance_portfolio_feed.log"
    assert portfolio.exchange_name == "binance_portfolio"
    assert portfolio._params.rest_url == "https://api.binance.com"


def test_portfolio_request_has_methods():
    """测试 Portfolio Request 有所有必需方法"""
    data_queue = queue.Queue()
    portfolio = BinanceRequestDataPortfolio(data_queue)

    required_methods = [
        "get_portfolio_account",
        "get_portfolio_collateral_rate",
        "portfolio_transfer",
    ]

    for method in required_methods:
        assert hasattr(portfolio, method), f"Missing method: {method}"
        assert callable(getattr(portfolio, method)), f"Method not callable: {method}"


def test_portfolio_request_get_portfolio_account_params():
    """测试 get_portfolio_account 参数构建"""
    data_queue = queue.Queue()
    portfolio = BinanceRequestDataPortfolio(
        data_queue, public_key="test_key", private_key="test_secret"
    )

    path, params, extra_data = portfolio._get_portfolio_account()

    assert path == "GET /sapi/v1/portfolio/account"
    assert params == {}
    assert extra_data["request_type"] == "get_portfolio_account"


def test_portfolio_request_get_portfolio_collateral_rate_params():
    """测试 get_portfolio_collateral_rate 参数构建"""
    data_queue = queue.Queue()
    portfolio = BinanceRequestDataPortfolio(data_queue)

    path, params, extra_data = portfolio._get_portfolio_collateral_rate(asset_type="USDT")

    assert path == "GET /sapi/v1/portfolio/collateralRate"
    assert params["assetType"] == "USDT"
    assert extra_data["symbol_name"] == "USDT"


def test_portfolio_request_portfolio_transfer_params():
    """测试 portfolio_transfer 参数构建"""
    data_queue = queue.Queue()
    portfolio = BinanceRequestDataPortfolio(data_queue)

    path, params, extra_data = portfolio._portfolio_transfer(
        asset="USDT", amount=100, transfer_type="SPOT_TO_PORTFOLIO"
    )

    assert path == "POST /sapi/v1/portfolio/transfer"
    assert params["asset"] == "USDT"
    assert params["amount"] == 100
    assert params["type"] == "SPOT_TO_PORTFOLIO"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
