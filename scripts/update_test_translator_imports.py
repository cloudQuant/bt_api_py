#!/usr/bin/env python3
"""更新测试文件中的 translator import"""
import re
from pathlib import Path

root = Path("/Users/yunjinqi/Documents/new_projects/bt_api_py")

# 需要更新的交易所（有 translator 测试的）
exchange_translators = {
    "bigone": "bt_api_bigone",
    "bingx": "bt_api_bingx",
    "bitfinex": "bt_api_bitfinex",
    "bitso": "bt_api_bitso",
    "bitstamp": "bt_api_bitstamp",
    "bitvavo": "bt_api_bitvavo",
    "buda": "bt_api_buda",
    "cryptocom": "bt_api_cryptocom",
    "ctp": "bt_api_ctp",
    "dydx": "bt_api_dydx",
    "exmo": "bt_api_exmo",
    "foxbit": "bt_api_foxbit",
    "gateio": "bt_api_gateio",
    "gemini": "bt_api_gemini",
    "hitbtc": "bt_api_hitbtc",
    "htx": "bt_api_htx",
    "hyperliquid": "bt_api_hyperliquid",
    "ib_web": "bt_api_ib_web",
    "kraken": "bt_api_kraken",
    "mexc": "bt_api_mexc",
    "mt5": "bt_api_mt5",
    "okx": "bt_api_okx",
    "phemex": "bt_api_phemex",
    "poloniex": "bt_api_poloniex",
    "upbit": "bt_api_upbit",
    "yobit": "bt_api_yobit",
}

def update_file(filepath: Path) -> bool:
    try:
        content = filepath.read_text(encoding='utf-8')
    except Exception:
        return False
    
    original = content
    
    for ex, pkg in exchange_translators.items():
        # 处理不同的大小写形式
        translator_class = f"{ex.replace('_', '').title()}ErrorTranslator"
        # bigone -> Bigone, ib_web -> IbWeb
        if ex == "ib_web":
            translator_class = "IBWebErrorTranslator"
        elif ex == "okx":
            translator_class = "OKXErrorTranslator"
        elif ex == "htx":
            translator_class = "HTXErrorTranslator"
        elif ex == "ctp":
            translator_class = "CTPErrorTranslator"
        else:
            translator_class = "".join(word.title() for word in ex.split("_")) + "ErrorTranslator"
        
        # 替换 import 语句
        # from bt_api_py.errors.{ex}_translator import {TranslatorClass}
        old_import = f"from bt_api_py.errors.{ex}_translator import {translator_class}"
        new_import = f"from {pkg}.errors.{ex}_translator import {translator_class}"
        content = content.replace(old_import, new_import)
        
        # 处理 bitfinex 特殊文件名
        if ex == "bitfinex":
            old_import2 = f"from bt_api_py.errors.bitfinex_error_translator import {translator_class}"
            new_import2 = f"from {pkg}.errors.bitfinex_translator import {translator_class}"
            content = content.replace(old_import2, new_import2)
    
    if content != original:
        filepath.write_text(content, encoding='utf-8')
        return True
    return False

# 处理 tests/errors 目录
errors_dir = root / "tests/errors"
modified = 0
for pyfile in errors_dir.glob("*_translator.py"):
    if update_file(pyfile):
        modified += 1
        print(f"MODIFIED: {pyfile.name}")

print(f"\n总共修改 {modified} 个测试文件")
