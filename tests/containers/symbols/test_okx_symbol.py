import json
import time
import pytest
from bt_api_py.containers.symbols.okx_symbol import OkxSymbolData

# 测试用OKX品种数据
json_info = {
    "code": "0",
    "data": [
        {
            "auctionEndTime": "",
            "baseCcy": "BTC",
            "ctMult": "100",
            "ctType": "",
            "ctVal": "",
            "ctValCcy": "",
            "expTime": "",
            "futureSettlement": False,
            "instFamily": "",
            "instId": "BTC-EUR",
            "instType": "SPOT",
            "lever": "125",
            "listTime": "1704876947000",
            "lotSz": "0.0001",
            "maxIcebergSz": "9999999999.0000000000000000",
            "maxLmtAmt": "1000",
            "maxLmtSz": "1000",
            "maxMktAmt": "1000000",
            "maxMktSz": "1000",
            "maxStopSz": "1000000",
            "maxTriggerSz": "9999999999.0000000000000000",
            "maxTwapSz": "9999999999.0000000000000000",
            "minSz": "0.01",
            "optType": "",
            "quoteCcy": "EUR",
            "settleCcy": "",
            "state": "live",
            "ruleType": "normal",
            "stk": "",
            "tickSz": "1",
            "uly": ""
        }
    ],
    "msg": ""
}

def test_symbol_instance():
    # 模拟API返回的原始数据（JSON字符串）
    raw_data = json.dumps(json_info)
    symbol_instance = OkxSymbolData(raw_data, has_been_json_encoded=False)
    symbol_instance.init_data()
    # 验证基础属性
    assert symbol_instance.get_exchange_name() == "OKX"
    assert symbol_instance.get_symbol_name() == "BTC-EUR"
    assert symbol_instance.get_asset_type() == "SPOT"
    assert symbol_instance.get_base_asset() == "BTC"
    assert symbol_instance.get_quote_asset() == "EUR"
    # 验证计算属性
    assert symbol_instance.get_price_unit() == 1
    assert symbol_instance.get_price_digital() == 1  # 1 / 0.1
    assert symbol_instance.get_qty_unit() == 0.0001
    assert symbol_instance.get_qty_digital() == 10000  # 1 / 0.0001
    assert symbol_instance.get_min_qty() == 0.01
    assert symbol_instance.get_max_qty() == 1000
    # 验证衍生属性
    assert symbol_instance.get_contract_multiplier() == 100.0
    assert symbol_instance.get_max_leverage() == 125.0
    assert symbol_instance.get_symbol_status() == "live"
    assert symbol_instance.get_symbol_trading_type() == "normal"
    assert symbol_instance.get_contract_type() is ""  # SWAP合约没有ctType
    # 验证时间相关属性
    assert isinstance(symbol_instance.get_local_update_time(), float)
    assert time.time() - symbol_instance.get_local_update_time() < 5  # 5秒内更新
# 运行测试的命令：pytest tests/containers/symbols/test_okx_symbol.py -v