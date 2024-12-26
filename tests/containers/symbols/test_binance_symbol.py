from btpy.containers.symbols.binance_symbol import BinanceSwapSymbolData, BinanceSpotSymbolData
import requests


def get_binance_swap_symbol_info():
    res = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo")
    result = res.json()
    return result


def get_binance_spot_symbol_info():
    res = requests.get("https://api.binance.com/fapi/v3/exchangeInfo")
    result = res.json()
    return result


def test_swap_binance_symbol():
    symbol_info = {"symbol": "BTCUSDT",
                   "pair": "BTCUSDT",
                   "contractType": "PERPETUAL",
                   "deliveryDate": 4133404800000,
                   "onboardDate": 1569398400000,
                   "status": "TRADING",
                   "maintMarginPercent": "2.5000",
                   "requiredMarginPercent": "5.0000",
                   "baseAsset": "BTC",
                   "quoteAsset": "USDT",
                   "marginAsset": "USDT",
                   "pricePrecision": 2,
                   "quantityPrecision": 3,
                   "baseAssetPrecision": 8,
                   "quotePrecision": 8,
                   "underlyingType": "COIN",
                   "underlyingSubType": ["PoW"],
                   "settlePlan": 0,
                   "triggerProtect": "0.0500",
                   "liquidationFee": "0.012500",
                   "marketTakeBound": "0.05",
                   "maxMoveOrderLimit": 10000,
                   "filters": [
                       {"filterType": "PRICE_FILTER", "tickSize": "0.10", "minPrice": "556.80", "maxPrice": "4529764"},
                       {"filterType": "LOT_SIZE", "maxQty": "1000", "minQty": "0.001", "stepSize": "0.001"},
                       {"minQty": "0.001", "filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "maxQty": "120"},
                       {"limit": 200, "filterType": "MAX_NUM_ORDERS"},
                       {"filterType": "MAX_NUM_ALGO_ORDERS", "limit": 10},
                       {"filterType": "MIN_NOTIONAL", "notional": "100"},
                       {"multiplierDecimal": "4", "multiplierDown": "0.9500", "filterType": "PERCENT_PRICE",
                        "multiplierUp": "1.0500"}],
                   "orderTypes": ["LIMIT", "MARKET", "STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET",
                                  "TRAILING_STOP_MARKET"],
                   "timeInForce": ["GTC", "IOC", "FOK", "GTX", "GTD"]}

    binance_symbol_data = BinanceSwapSymbolData(symbol_info, True)
    binance_symbol_data.init_data()
    assert binance_symbol_data.get_symbol_name() == "BTCUSDT"
    assert binance_symbol_data.get_price_unit() == 0.1
    assert binance_symbol_data.get_price_digital() == 2
    assert binance_symbol_data.get_min_price() == 556.80
    assert binance_symbol_data.get_max_price() == 4529764
    assert binance_symbol_data.get_qty_unit() == 0.001
    assert binance_symbol_data.get_qty_digital() == 3
    assert binance_symbol_data.get_max_qty() == 1000
    assert binance_symbol_data.get_min_qty() == 0.001
    assert binance_symbol_data.get_contract_multiplier() == 1.0


def test_spot_binance_symbol():
    symbol_info = {"symbol": "ETHBTC", "status": "TRADING",
                   "baseAsset": "ETH", "baseAssetPrecision": 8,
                   "quoteAsset": "BTC", "quotePrecision": 8,
                   "quoteAssetPrecision": 8,
                   "baseCommissionPrecision": 8,
                   "quoteCommissionPrecision": 8,
                   "orderTypes": ["LIMIT", "LIMIT_MAKER", "MARKET", "STOP_LOSS_LIMIT", "TAKE_PROFIT_LIMIT"],
                   "icebergAllowed": True, "ocoAllowed": True, "otoAllowed": True, "quoteOrderQtyMarketAllowed": True,
                   "allowTrailingStop": True, "cancelReplaceAllowed": True, "isSpotTradingAllowed": True,
                   "isMarginTradingAllowed": True,
                   "filters": [
                       {"filterType": "PRICE_FILTER", "minPrice": "0.00001000", "maxPrice": "922327.00000000",
                        "tickSize": "0.00001000"},
                       {"filterType": "LOT_SIZE", "minQty": "0.00010000", "maxQty": "100000.00000000",
                        "stepSize": "0.00010000"},
                       {"filterType": "ICEBERG_PARTS", "limit": 10},
                       {"filterType": "MARKET_LOT_SIZE", "minQty": "0.00000000", "maxQty": "3591.12374644",
                        "stepSize": "0.00000000"},
                       {"filterType": "TRAILING_DELTA", "minTrailingAboveDelta": 10, "maxTrailingAboveDelta": 2000,
                        "minTrailingBelowDelta": 10, "maxTrailingBelowDelta": 2000},
                       {"filterType": "PERCENT_PRICE_BY_SIDE", "bidMultiplierUp": "5", "bidMultiplierDown": "0.2",
                        "askMultiplierUp": "5", "askMultiplierDown": "0.2", "avgPriceMins": 5},
                       {"filterType": "NOTIONAL", "minNotional": "0.00010000", "applyMinToMarket": True,
                        "maxNotional": "9000000.00000000", "applyMaxToMarket": False, "avgPriceMins": 5},
                       {"filterType": "MAX_NUM_ORDERS", "maxNumOrders": 200},
                       {"filterType": "MAX_NUM_ALGO_ORDERS", "maxNumAlgoOrders": 5}], "permissions": [],
                   "permissionSets": [
                       ["SPOT", "MARGIN", "TRD_GRP_004", "TRD_GRP_005", "TRD_GRP_006", "TRD_GRP_008", "TRD_GRP_009",
                        "TRD_GRP_010",
                        "TRD_GRP_011", "TRD_GRP_012", "TRD_GRP_013", "TRD_GRP_014", "TRD_GRP_015", "TRD_GRP_016",
                        "TRD_GRP_017",
                        "TRD_GRP_018", "TRD_GRP_019", "TRD_GRP_020", "TRD_GRP_021", "TRD_GRP_022", "TRD_GRP_023",
                        "TRD_GRP_024",
                        "TRD_GRP_025", "TRD_GRP_026", "TRD_GRP_027", "TRD_GRP_028", "TRD_GRP_029", "TRD_GRP_030"]],
                   "defaultSelfTradePreventionMode": "EXPIRE_MAKER",
                   "allowedSelfTradePreventionModes": ["EXPIRE_TAKER", "EXPIRE_MAKER", "EXPIRE_BOTH"]}
    binance_symbol_data = BinanceSpotSymbolData(symbol_info, True)
    binance_symbol_data.init_data()
    assert binance_symbol_data.get_symbol_name() == "ETHBTC"
    assert binance_symbol_data.get_price_unit() == 0.00001000
    assert binance_symbol_data.get_price_digital() == 8
    assert binance_symbol_data.get_min_price() == 0.00001000
    assert binance_symbol_data.get_max_price() == 922327.00000000
    assert binance_symbol_data.get_qty_unit() == 0.00010000
    assert binance_symbol_data.get_qty_digital() == 8
    assert binance_symbol_data.get_max_qty() == 100000.00000000
    assert binance_symbol_data.get_min_qty() == 0.00010000
    assert binance_symbol_data.get_contract_multiplier() == 1.0

if __name__ == '__main__':
    # result = test_get_symbol_info()
    # print(result.keys())
    # print(result)
    test_swap_binance_symbol()
