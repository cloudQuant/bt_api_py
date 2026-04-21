#!/usr/bin/env python3
"""批量迁移 core 引用到插件包"""
import re
from pathlib import Path

root = Path("/Users/yunjinqi/Documents/new_projects/bt_api_py")

# 有插件包的交易所（已去除之前清理的 bybit/kucoin/bitget）
plugin_exchanges = [
    "bigone", "bingx", "bitfinex", "bitso", "bitstamp", "bitvavo", "buda",
    "cryptocom", "ctp", "dydx", "exmo", "foxbit", "gateio", "gemini",
    "hitbtc", "htx", "hyperliquid", "ib_web", "kraken", "mexc", "mt5",
    "okx", "phemex", "poloniex", "upbit", "yobit"
]

def replace_imports_in_file(filepath: Path) -> bool:
    """替换文件中的 import，返回是否修改"""
    try:
        content = filepath.read_text(encoding='utf-8')
    except Exception:
        return False
    
    original = content
    
    for ex in plugin_exchanges:
        # containers/exchanges/{ex}_exchange_data
        content = re.sub(
            rf'from bt_api_py\.containers\.exchanges\.({ex}_exchange_data|{ex.title()}ExchangeData)',
            f'from bt_api_{ex}.exchange_data',
            content
        )
        content = re.sub(
            rf'from bt_api_py\.containers\.exchanges\.({ex})_exchange_data import',
            f'from bt_api_{ex}.exchange_data import',
            content
        )
        # feeds/live_{ex}
        content = re.sub(
            rf'from bt_api_py\.feeds\.live_{ex}',
            f'from bt_api_{ex}.feeds.live_{ex}',
            content
        )
        # exchange_registers
        content = re.sub(
            rf'from bt_api_py\.exchange_registers\.register_{ex}',
            f'from bt_api_{ex}.registry_registration',
            content
        )
        # errors/translator
        content = re.sub(
            rf'from bt_api_py\.errors\.({ex}_translator)',
            f'from bt_api_{ex}.errors.{ex}_translator',
            content
        )
    
    if content != original:
        filepath.write_text(content, encoding='utf-8')
        return True
    return False

# 处理 tests 目录
tests_dir = root / "tests"
modified = 0
for pyfile in tests_dir.rglob("*.py"):
    if replace_imports_in_file(pyfile):
        modified += 1
        print(f"MODIFIED: {pyfile.relative_to(root)}")

# 处理 examples 目录
examples_dir = root / "examples"
for pyfile in examples_dir.rglob("*.py"):
    if replace_imports_in_file(pyfile):
        modified += 1
        print(f"MODIFIED: {pyfile.relative_to(root)}")

print(f"\n总共修改 {modified} 个文件")
