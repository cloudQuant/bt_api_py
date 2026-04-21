#!/usr/bin/env python3
"""批量删除 core 中的重复交易所实现"""
import shutil
from pathlib import Path

root = Path("/Users/yunjinqi/Documents/new_projects/bt_api_py")

# 有插件包的交易所
plugin_exchanges = [
    "bigone", "bingx", "bitfinex", "bitso", "bitstamp", "bitvavo", "buda",
    "cryptocom", "ctp", "dydx", "exmo", "foxbit", "gateio", "gemini",
    "hitbtc", "htx", "hyperliquid", "ib_web", "kraken", "mexc", "mt5",
    "okx", "phemex", "poloniex", "upbit", "yobit"
]

deleted = []
errors = []

for ex in plugin_exchanges:
    # 1. 删除 containers/exchanges/{ex}_exchange_data.py
    exchange_data_file = root / f"bt_api_py/containers/exchanges/{ex}_exchange_data.py"
    if exchange_data_file.exists():
        try:
            exchange_data_file.unlink()
            deleted.append(f"containers/exchanges/{ex}_exchange_data.py")
        except Exception as e:
            errors.append(f"containers/exchanges/{ex}_exchange_data.py: {e}")

    # 2. 删除 exchange_registers/register_{ex}.py
    register_file = root / f"bt_api_py/exchange_registers/register_{ex}.py"
    if register_file.exists():
        try:
            register_file.unlink()
            deleted.append(f"exchange_registers/register_{ex}.py")
        except Exception as e:
            errors.append(f"exchange_registers/register_{ex}.py: {e}")

    # 3. 删除 feeds/live_{ex}/ 目录
    feeds_dir = root / f"bt_api_py/feeds/live_{ex}"
    if feeds_dir.exists():
        try:
            shutil.rmtree(feeds_dir)
            deleted.append(f"feeds/live_{ex}/")
        except Exception as e:
            errors.append(f"feeds/live_{ex}/: {e}")

    # 4. 删除 errors/{ex}_translator.py (如果存在)
    # 注意：bitfinex 的文件名是 bitfinex_error_translator.py
    if ex == "bitfinex":
        translator_file = root / "bt_api_py/errors/bitfinex_error_translator.py"
    else:
        translator_file = root / f"bt_api_py/errors/{ex}_translator.py"
    
    if translator_file.exists():
        try:
            translator_file.unlink()
            deleted.append(f"errors/{translator_file.name}")
        except Exception as e:
            errors.append(f"errors/{translator_file.name}: {e}")

    # 5. 删除 configs/{ex}.yaml
    config_file = root / f"bt_api_py/configs/{ex}.yaml"
    if config_file.exists():
        try:
            config_file.unlink()
            deleted.append(f"configs/{ex}.yaml")
        except Exception as e:
            errors.append(f"configs/{ex}.yaml: {e}")

print(f"成功删除 {len(deleted)} 个文件/目录:")
for item in deleted[:20]:
    print(f"  - {item}")
if len(deleted) > 20:
    print(f"  ... 还有 {len(deleted) - 20} 个")

if errors:
    print(f"\n删除失败 {len(errors)} 个:")
    for err in errors:
        print(f"  - {err}")
