from btpy.containers.incomes.binance_income import BinanceIncomeData


def test_binance_income():
    data = {"symbol": "BTCUSDT",
            "incomeType": "COMMISSION",
            "income": "-0.01000000",
            "asset": "USDT",
            "info": "COMMISSION",
            "time": 1570636800000,
            "tranId": "9689322392",
            "tradeId": 2059192}
    bi = BinanceIncomeData(data, "Binance", data['symbol'], "PERPETUAL", True)
    bi.init_data()
    assert bi.get_event_type() == "IncomeEvent"
    assert bi.get_server_time() == 1570636800000.0
    assert bi.get_symbol_name() == "BTCUSDT"
    assert bi.get_income_type() == "COMMISSION"
    assert bi.get_income_asset() == "USDT"
    assert bi.get_income_value() == -0.01000000


if __name__ == "__main__":
    test_binance_income()
