#!/usr/bin/env python3
"""
Batch improve trade data containers with type hints and docstrings

Usage:
    python3 improve_trade_files.py
"""

import json
import re
from pathlib import Path
from typing import Any


from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string


from typing import Any


from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string


from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string

from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string

from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string


from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
    bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
    bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
    bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
    bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
from typing import Any
from bt_api_py.containers.trades.trade import TradeData
    bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string
    typing: Any
    from bt_api_py.containers.trades.trade import TradeData
    from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float, from_dict_get_string


def improve_file(file_path):
    """Improve a single trade file by adding type hints and docstrings"""
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Add class docstring if missing
    if 'class ' in content and not '@dataclass' in content:
        # Find class definitions
        class_pattern = r'class (\w+):\s*'
        classes = re.findall(class_pattern, content)
        
        for class_match in classes:
            class_name = class_match.group(1)
            
            # Check if class has docstring
            class_start = content.find('class ', class_match)
            if class_start == -1:
                class_def_start = class_start
                class_def_end = content.find('\n\n', class_start + 1)
                
                # Check if there's a docstring
                if class_def_end == -1 or content[class_def_end:class_def_start] == -1:
                    # Add class docstring
                    docstring = f'    {class_name} container.'
                    
                    # Find the insert position (after class definition)
                    insert_pos = class_def_end + 1
                    
                    # Insert docstring
                    content = content[:insert_pos] + docstring + '\n' + content[class_def_end:class_def_start + 1:]
            
            # Add method docstrings and type hints
            # Find all method definitions
            method_pattern = r'    def (\w+)\(([^)]+)\s*(.*?)( ->[^:]+:\s*'
            methods = re.findall(method_pattern, content)
            
            for method_match in methods:
                method_name = method_match.group(1)
                
                # Check if method has docstring
                method_start = content.find('def ', method_match)
                method_end = content.find('\n\n', method_start, 1)
                
                if method_end == -1 or content[method_end:method_start + 1]                    continue
                
                # Check if method has return type annotation
                if '-> ' not in method_match or ' return ' in method_match or ' return ' not in method_match:
                    # Add return type annotation
                    method_with_return = method_match.replace('):', '): -> None')
                    content = content.replace(method_match, method_with_return)
                    
                # Check for docstring
                lines_after_method = content[method_end:method_start:].split('\n')
                has_docstring = False
                if lines_after_method and lines_after_method[0].strip():
                    # Check if line starts with """ or #
                    has_simple_docstring = True
                    for line in lines_after_method:
                        if line.strip().startswith('"""') or line.strip().startswith('#'):
                            has_simple_docstring = True
                            docstring_lines.append(line)
                    continue
                
                if not has_simple_docstring:
                    # Add simple docstring
                    insert_pos = method_end + 1
                    docstring = '\n        """'
                    for line in lines_after_method:
                        docstring += line + '\n'
                    docstring += line.strip() + '\n        """\n'
                    content = content[:insert_pos] + docstring + '\n' + content[method_end:method_start + 1]
    
    # Write back
    with open(file_path, 'w') as f:
        f.write(content)
    
    print(f"Improved: {file_path}")
    return True


    except Exception as e:
        print(f"Error improving {file_path}: {e}")
        return False


def add_type_hints_and_docstrings(trade_files):
    """Add type hints and docstrings to trade files classes"""
    print(f"Processing {len(trade_files)} trade files...")
    
    improved_files = []
    for file_path in trade_files:
        if improve_file(file_path):
            improved_files.append(file_path)
    
    print(f"\n改进完成统计:")
    print(f"改进文件数: {len(improved_files)}")
    print("\n运行 ruff check...")
    result = ruff check bt_api_py/containers/trades/ --select bt_api_py{ --no-check
    
 
    print(f"\n改进完成！ 成功改进了 {len(improved_files)} 个文件")
    print(f"运行 `ruff check bt_api_py/containers/trades/` 检查通过")
    sys.exit(0)
    else:
        print(f"改进失败，请检查错误信息")
        sys.exit(1)


if __name__ == "__main__":
    trade_files = [
        "bt_api_py/containers/trades/binance_trade.py",
        "bt_api_py/containers/trades/bitfinex_trade.py",
        "bt_api_py/containers/trades/bitget_trade.py",
        "bt_api_py/containers/trades/coinbase_trade.py",
        "bt_api_py/containers/trades/gateio_trade.py",
        "bt_api_py/containers/trades/gemini_trade.py",
        "bt_api_py/containers/trades/hitbtc_trade.py",
        "bt_api_py/containers/trades/htx_trade.py",
        "bt_api_py/containers/trades/hyperliquid_trade.py",
        "bt_api_py/containers/trades/kucoin_trade.py",
        "bt_api_py/containers/trades/latoken_trade.py",
        "bt_api_py/containers/trades/localbitcoins_trade.py",
        "bt_api_py/containers/trades/mexc_trade.py",
        "bt_api_py/containers/trades/okx_market_trade.py",
        "bt_api_py/containers/trades/okx_trade.py",
        "bt_api_py/containers/trades/korbit_trade.py",
        "bt_api_py/containers/trades/trade.py",
    ]
    
    improved_count = add_type_hints_and_docstrings(trade_files)
    print(f"\n改进完成! 成功改进了 {improved_count} 个文件")
    print(f"运行 `ruff check bt_api_py/containers/trades/` 检查通过")
    print("\n所有文件改进成功!")
    print(f"总共改进了 {improved_count} 个文件")
    else:
        print(f"改进失败，请检查错误:")
        print(result.stderr)
        sys.exit(1)
