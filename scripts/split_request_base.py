#!/usr/bin/env python3
"""
Script to split request_base.py into Mixin modules.
Reads the original file and generates all split files.
"""
import re
import os

SRC = '/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_okx/request_base.py'
MIXIN_DIR = '/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_okx/mixins'
OKX_DIR = '/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_okx'

with open(SRC) as f:
    all_lines = f.readlines()

total_lines = len(all_lines)

# ===== Section markers =====
section_markers = []
section_pattern = re.compile(r'# =+\s*(.*?)\s*=+')
for i, line in enumerate(all_lines):
    m = section_pattern.search(line)
    if m:
        section_markers.append((i + 1, m.group(1).strip()))

# ===== Map sections -> mixin files =====
MIXIN_MAP = {
    "Account APIs": "account_mixin",
    "Position APIs": "account_mixin",
    "Config APIs": "account_mixin",
    "Market Data APIs": "market_data_mixin",
    "Public Data APIs": "market_data_mixin",
    "Market Data APIs (continued)": "market_data_mixin",
    "Public Data APIs (Additional)": "market_data_mixin",
    "Missing Critical Public APIs": "market_data_mixin",
    "Trade APIs": "trade_mixin",
    "Algo Trading APIs": "trade_mixin",
    "Option Instrument Family Trades": "trade_mixin",
    "Option Trades": "trade_mixin",
    "24h Volume": "trade_mixin",
    "Call Auction Details": "trade_mixin",
    "Index Price": "trade_mixin",
    "Index Candles": "trade_mixin",
    "Mark Price Candles": "trade_mixin",
    "Index Candles History": "trade_mixin",
    "Mark Price Candles History": "trade_mixin",
    "Missing Trade APIs": "trade_mixin",
    "Trading Account APIs": "trading_account_mixin",
    "MMP (Market Maker Protection) APIs": "trading_account_mixin",
    "Bills History Archive APIs": "trading_account_mixin",
    "Trading Account Configuration APIs": "trading_account_mixin",
    "Additional Trading Account APIs": "trading_account_mixin",
    "Missing Trading Account APIs": "trading_account_mixin",
    "Trading Statistics APIs": "statistics_mixin",
    "Position Builder APIs": "statistics_mixin",
    "Missing Trading Statistics APIs": "statistics_mixin",
    "Grid Trading APIs": "grid_trading_mixin",
    "Missing Funding Account APIs": "funding_mixin",
    "Funding Account (P2) - Remaining Interfaces": "funding_mixin",
    "Missing Sub-account APIs": "sub_account_mixin",
    "Sub-account (P2) - Remaining Interfaces": "sub_account_mixin",
    "Copy Trading APIs": "copy_trading_mixin",
    "Copy Trading Public APIs": "copy_trading_mixin",
    "Spread Trading APIs": "spread_trading_mixin",
    "RFQ (Request for Quote) / Block Trading": "rfq_mixin",
    "Status/Announcement APIs": "status_mixin",
}

MIXIN_CLASS_NAMES = {
    'account_mixin': 'AccountMixin',
    'market_data_mixin': 'MarketDataMixin',
    'trade_mixin': 'TradeMixin',
    'trading_account_mixin': 'TradingAccountMixin',
    'statistics_mixin': 'StatisticsMixin',
    'grid_trading_mixin': 'GridTradingMixin',
    'funding_mixin': 'FundingMixin',
    'sub_account_mixin': 'SubAccountMixin',
    'copy_trading_mixin': 'CopyTradingMixin',
    'spread_trading_mixin': 'SpreadTradingMixin',
    'rfq_mixin': 'RfqMixin',
    'status_mixin': 'StatusMixin',
}

CONTAINER_IMPORTS = {
    'OkxTickerData': 'from bt_api_py.containers.tickers.okx_ticker import OkxTickerData',
    'OkxBarData': 'from bt_api_py.containers.bars.okx_bar import OkxBarData',
    'OkxOrderBookData': 'from bt_api_py.containers.orderbooks.okx_orderbook import OkxOrderBookData',
    'OkxFundingRateData': 'from bt_api_py.containers.fundingrates.okx_funding_rate import OkxFundingRateData',
    'OkxMarkPriceData': 'from bt_api_py.containers.markprices.okx_mark_price import OkxMarkPriceData',
    'OkxAccountData': 'from bt_api_py.containers.accounts.okx_account import OkxAccountData',
    'OkxOrderData': 'from bt_api_py.containers.orders.okx_order import OkxOrderData',
    'OkxRequestTradeData': 'from bt_api_py.containers.trades.okx_trade import OkxRequestTradeData',
    'OkxWssTradeData': 'from bt_api_py.containers.trades.okx_trade import OkxWssTradeData',
    'OkxPositionData': 'from bt_api_py.containers.positions.okx_position import OkxPositionData',
    'OkxSymbolData': 'from bt_api_py.containers.symbols.okx_symbol import OkxSymbolData',
}

# ===== Build line ranges for each section =====
sections = []
for idx, (line_no, name) in enumerate(section_markers):
    end_line = section_markers[idx + 1][0] - 1 if idx + 1 < len(section_markers) else total_lines
    sections.append((line_no, end_line, name))

# ===== Group sections by mixin =====
mixin_sections = {}
for start, end, name in sections:
    mixin_name = MIXIN_MAP.get(name)
    if mixin_name is None:
        print(f"WARNING: Unmapped section '{name}' at line {start}")
        continue
    mixin_sections.setdefault(mixin_name, []).append((start, end, name))

# ===== Collect code lines per mixin =====
mixin_code = {}
for mixin_name, secs in mixin_sections.items():
    code_lines = []
    for start, end, name in secs:
        code_lines.extend(all_lines[start - 1:end])
    mixin_code[mixin_name] = code_lines

# ===== Analyze normalize function definitions per mixin =====
def_pattern = re.compile(r'def (_\w+normalize_function)\(')
ref_pattern = re.compile(r'OkxRequestData\.(_\w+normalize_function)')

mixin_defined_norms = {}
mixin_external_norms = {}
for mixin_name, code_lines in mixin_code.items():
    defined = set()
    referenced = set()
    for line in code_lines:
        m = def_pattern.search(line)
        if m:
            defined.add(m.group(1))
        m = ref_pattern.search(line)
        if m:
            referenced.add(m.group(1))
    mixin_defined_norms[mixin_name] = defined
    mixin_external_norms[mixin_name] = referenced - defined

# ===== Create output directory =====
os.makedirs(MIXIN_DIR, exist_ok=True)

# ===== Generate normalizers.py (shared normalize functions) =====
# Only _generic_normalize_function is shared across mixins
normalizers_content = '''# -*- coding: utf-8 -*-
"""
Shared normalize functions for OKX API responses.
These are used across multiple mixin modules.
"""


def generic_normalize_function(input_data, extra_data):
    """Generic normalize function for OKX API responses.
    Extracts 'data' list and checks 'code' for status."""
    status = True if input_data.get("code") == '0' else False
    if 'data' not in input_data:
        return [], status
    data = input_data['data']
    if isinstance(data, list):
        return data, status
    return [data] if data else [], status
'''

normalizers_path = os.path.join(MIXIN_DIR, 'normalizers.py')
with open(normalizers_path, 'w') as f:
    f.write(normalizers_content)
print(f"Generated: {normalizers_path}")

# ===== Generate each mixin file =====
for mixin_name, code_lines in mixin_code.items():
    class_name = MIXIN_CLASS_NAMES[mixin_name]
    code_text = ''.join(code_lines)
    external_norms = mixin_external_norms[mixin_name]
    defined_norms = mixin_defined_norms[mixin_name]

    # Determine container imports needed
    needed_imports = []
    needed_imports.append('from bt_api_py.functions.utils import update_extra_data')

    for cls_name, imp_line in sorted(CONTAINER_IMPORTS.items()):
        if cls_name in code_text:
            needed_imports.append(imp_line)

    # If uses _generic_normalize_function, import from normalizers
    needs_generic = '_generic_normalize_function' in external_norms
    if needs_generic:
        needed_imports.append(
            'from bt_api_py.feeds.live_okx.mixins.normalizers import generic_normalize_function'
        )

    # Build mixin file content
    header = f'''# -*- coding: utf-8 -*-
"""
OKX API - {class_name}
Auto-generated from request_base.py
"""
{chr(10).join(needed_imports)}


class {class_name}:
    """Mixin providing OKX API methods."""
'''

    # Process the code lines:
    # 1. Remove leading 4-space indent from class body (they already have it)
    # 2. Replace OkxRequestData._xxx_normalize_function references:
    #    - For locally defined: replace with ClassName._xxx_normalize_function
    #    - For _generic_normalize_function: replace with generic_normalize_function
    processed_lines = []
    for line in code_lines:
        # Replace references to locally defined normalize functions
        for norm_name in defined_norms:
            old_ref = f'OkxRequestData.{norm_name}'
            new_ref = f'{class_name}.{norm_name}'
            line = line.replace(old_ref, new_ref)

        # Replace references to _generic_normalize_function
        if needs_generic:
            line = line.replace(
                'OkxRequestData._generic_normalize_function',
                'generic_normalize_function'
            )

        processed_lines.append(line)

    body = ''.join(processed_lines)

    file_content = header + body

    # Ensure file ends with newline
    if not file_content.endswith('\n'):
        file_content += '\n'

    filepath = os.path.join(MIXIN_DIR, f'{mixin_name}.py')
    with open(filepath, 'w') as f:
        f.write(file_content)
    print(f"Generated: {filepath} ({len(file_content.splitlines())} lines)")

# ===== Generate mixins/__init__.py =====
init_lines = ['# -*- coding: utf-8 -*-']
for mixin_name in sorted(MIXIN_CLASS_NAMES.keys()):
    class_name = MIXIN_CLASS_NAMES[mixin_name]
    init_lines.append(f'from bt_api_py.feeds.live_okx.mixins.{mixin_name} import {class_name}')

init_lines.append('')
init_lines.append('__all__ = [')
for mixin_name in sorted(MIXIN_CLASS_NAMES.keys()):
    init_lines.append(f'    "{MIXIN_CLASS_NAMES[mixin_name]}",')
init_lines.append(']')
init_lines.append('')

init_path = os.path.join(MIXIN_DIR, '__init__.py')
with open(init_path, 'w') as f:
    f.write('\n'.join(init_lines))
print(f"Generated: {init_path}")

# ===== Generate new request_base.py =====
# Base infrastructure: lines 1-201 + _generic_normalize_function
base_lines = all_lines[0:30]  # imports + empty line before class

# Build the new class definition
new_request_base = '''# -*- coding: utf-8 -*-
"""
OKX REST API request base class.
Handles authentication, signing, and all REST API methods.
API methods are organized into Mixin classes under the mixins/ package.
"""
import hmac
import base64
import time
import json
from urllib import parse
from bt_api_py.containers.exchanges.okx_exchange_data import OkxExchangeDataSwap
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.capability import Capability
from bt_api_py.error_framework import OKXErrorTranslator
from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitType, RateLimitScope
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_okx.mixins.normalizers import generic_normalize_function
'''

# Add mixin imports
for mixin_name in sorted(MIXIN_CLASS_NAMES.keys()):
    class_name = MIXIN_CLASS_NAMES[mixin_name]
    new_request_base += f'from bt_api_py.feeds.live_okx.mixins.{mixin_name} import {class_name}\n'

new_request_base += '\n\n'

# Build class definition with all mixins
mixin_list = ', '.join(MIXIN_CLASS_NAMES[k] for k in sorted(MIXIN_CLASS_NAMES.keys()))
new_request_base += f'class OkxRequestData({mixin_list}, Feed):\n'

# Add the base infrastructure methods (lines 32-201 from original, which is the class body)
# These are: _capabilities, __init__, _create_default_rate_limiter, translate_error,
# push_data_to_queue, signature, get_header, request, async_request, async_callback
base_body = ''.join(all_lines[31:201])  # lines 32-201 (0-indexed: 31-200)
new_request_base += base_body

# Add _generic_normalize_function as a static method
new_request_base += '''
    @staticmethod
    def _generic_normalize_function(input_data, extra_data):
        """Generic normalize function for OKX API responses.
        Extracts 'data' list and checks 'code' for status.
        Delegates to the shared normalizers module."""
        return generic_normalize_function(input_data, extra_data)
'''

# Write new request_base.py
# First backup the original
import shutil
backup_path = SRC + '.bak'
if not os.path.exists(backup_path):
    shutil.copy2(SRC, backup_path)
    print(f"\nBacked up original to: {backup_path}")

with open(SRC, 'w') as f:
    f.write(new_request_base)

print(f"\nGenerated new request_base.py ({len(new_request_base.splitlines())} lines)")
print("\n===== SPLIT COMPLETE =====")
print(f"Original: 12811 lines -> New base: {len(new_request_base.splitlines())} lines")
print(f"Mixin files: {len(mixin_code)} files in {MIXIN_DIR}/")
print(f"Backup saved: {backup_path}")

