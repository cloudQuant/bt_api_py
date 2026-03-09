#!/usr/bin/env python3
"""
批量改进 bars 文件的代码质量
"""

import json
import re
from pathlib import Path


def process_bars_file(filepath: Path) -> bool:
    """处理单个 bars 文件"""
    print(f"Processing {filepath}...")
    
    try:
        content = filepath.read_text()
        original_content = content
        
        # 获取类名
        class_match = re.search(r'class (\w+BarData)\(BarData\):', content)
        if not class_match:
            print(f"  No BarData class found in {filepath}")
            return False
        
        class_name = class_match.group(1)
        
        # 添加类型注释到        pattern = r'(    def __init__\(self, bar_info, symbol_name, asset_type, has_been_json_encoded=False\):)'
        replacement = r'''    def __init__(
        self,
        bar_info: str | dict[str, any],
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:'''
        
        content = re.sub(pattern, replacement, content)
        
        # 添加文档字符串
        pattern = r'(    def __init__\([^)]+\) -> None:\n)(        super\(\).__init__\)'
        docstring = f'''        """Initialize {class_name} bar data container.

        Args:
            bar_info: Raw bar data from API (JSON string or dict).
            symbol_name: Trading symbol name.
            asset_type: Asset type (e.g., "SPOT", "FUTURE").
            has_been_json_encoded: Whether bar_info is already parsed.
        """
'''
        
        replacement = r'\1' + docstring + r'\2'
        content = re.sub(pattern, replacement, content)
        
        # 为 init_data 添加返回类型
        pattern = r'(    def init_data\(self\):)'
        replacement = r'    def init_data(self) -> "' + class_name + '":'
        content = re.sub(pattern, replacement, content)
        
        # 添加文档字符串
        pattern = r'(    def init_data\(self\)[^:]+:\n        """Parse \w+ bar response\.""")'
        replacement = r'''    def init_data(self) -> "''' + class_name + r'''":
        """Parse bar data response.

        Returns:
            Self instance after parsing bar data.
        """'''
        
        content = re.sub(pattern, replacement, content)
        
        # 添加类型注释到其他方法
        content = add_type_hints_to_methods(content)
        
        # 只有在内容改变时才写入
        if content != original_content:
            filepath.write_text(content)
            print(f"  ✓ Updated {filepath}")
            return True
        else:
            print(f"  - No changes needed for {filepath}")
            return False
    
    except Exception as e:
        print(f"  ✗ Error processing {filepath}: {e}")
        return False


def add_type_hints_to_methods(content: str) -> str:
    """为各种方法添加类型注释"""
    # 添加导入（如果还没有）
    if 'from typing import Any' not in content:
        if 'import json\nimport time' in content:
            content = re.sub(
                r'(import json\nimport time)',
                r'import json\nimport time\nfrom typing import Any',
                content,
            )
        else:
            # 在开头添加导入
            lines = content.split('\n')
            for i, range(len(lines)):
                if lines[i].startswith('import'):
                    lines.insert(i, 'from typing import Any')
                    content = '\n'.join(lines)
                    break
    
    # 添加返回类型到各个 getter 方法
    getter_patterns = [
        (r'(    def get_all_data\(self\):)', r'    def get_all_data(self) -> dict[str, Any]:'),
        (r'(    def get_open\(self\):)', r'    def get_open(self) -> float | None:'),
        (r'(    def get_high\(self\):)', r'    def get_high(self) -> float | None:'),
        (r'(    def get_low\(self\):)', r'    def get_low(self) -> float | None:'),
        (r'(    def get_close\(self\):)', r'    def get_close(self) -> float | None:'),
        (r'(    def get_volume\(self\):)', r'    def get_volume(self) -> float | None:'),
        (r'(    def get_timestamp\(self\):)', r'    def get_timestamp(self) -> float | None:'),
        (r'(    def get_time\(self\):)', r'    def get_time(self) -> float | None:'),
        (r'(    def is_valid\(self\):)', r'    def is_valid(self) -> bool:'),
        (r'(    def get_range\(self\):)', r'    def get_range(self) -> float:'),
        (r'(    def get_mid_price\(self\):)', r'    def get_mid_price(self) -> float:'),
    ]
    
    for pattern, replacement in getter_patterns:
        content = re.sub(pattern, replacement, content)
    
    # 为每个方法添加文档字符串
    doc_patterns = [
        (
            r'(    def get_all_data\(self\)[^:]+:\n        )"""\)',
            r'''    def get_all_data(self) -> dict[str, Any]:
        """Get all bar data as dictionary.
        """''',
        ),
        (
            r'(    def get_open\(self\)[^:]+:\n        )"""\)',
            r'''    def get_open(self) -> float | None:
        """Get opening price.
        """''',
        ),
        (
            r'(    def get_high\(self\)[^:]+:\n        )"""\)',
            r'''    def get_high(self) -> float | None:
        """Get highest price.
        """''',
        ),
        (
            r'(    def get_low\(self\)[^:]+:\n        )"""\)',
            r'''    def get_low(self) -> float | None:
        """Get lowest price.
        """''',
        ),
        (
            r'(    def get_close\(self\)[^:]+:\n        )"""\)',
            r'''    def get_close(self) -> float | None:
        """Get closing price.
        """''',
        ),
        (
            r'(    def get_volume\(self\)[^:]+:\n        )"""\)',
            r'''    def get_volume(self) -> float | None:
        """Get trading volume.
        """''',
        ),
        (
            r'(    def get_timestamp\(self\)[^:]+:\n        )"""\)',
            r'''    def get_timestamp(self) -> float | None:
        """Get bar timestamp.
        """''',
        ),
        (
            r'(    def get_time\(self\)[^:]+:\n        )"""\)',
            r'''    def get_time(self) -> float | None:
        """Get bar time as timestamp.
        """''',
        ),
        (
            r'(    def is_valid\(self\)[^:]+:\n        )"""\)',
            r'''    def is_valid(self) -> bool:
        """Check if bar data is valid.
        """''',
        ),
        (
            r'(    def get_range\(self\)[^:]+:\n        )"""\)',
            r'''    def get_range(self) -> float:
        """Get price range (high - low).
        """''',
        ),
        (
            r'(    def get_mid_price\(self\)[^:]+:\n        )"""\)',
            r'''    def get_mid_price(self) -> float:
        """Get mid price ((bid + ask) / 2).
        """''',
        ),
    ]
    
    for pattern, replacement in doc_patterns:
        content = re.sub(pattern, replacement, content)
    
    return content


def main():
    """主函数"""
    bars_dir = Path("bt_api_py/containers/bars")
    
    # 获取所有需要处理的文件
    bar_files = [
        f for f in bars_dir.glob("*.py") if f.name != "__init__.py" and f.name != "bar.py"
    ]
    
    print(f"Found {len(bar_files)} bar files to process\n")
    
    updated_count = 0
    for filepath in sorted(bar_files):
        if process_bars_file(filepath):
            updated_count += 1
    
    print(f"\n✓ Updated {updated_count} out of {len(bar_files)} files")


if __name__ == "__main__":
    main()
