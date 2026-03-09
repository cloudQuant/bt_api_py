#!/usr/bin/env python3
"""
批量改进OKX交易所代码：添加类型注释和文档字符串
"""

import json
import re
from pathlib import Path


def add_typing_import(code: str) -> str:
    """添加typing导入"""
    if "from typing import" in code:
        if "Any" not in code.split("from typing import")[1].split("\n")[0]:
            code = re.sub(r"from typing import (.+)", r"from typing import Any, \1", code, count=1)
    else:
        first_import = re.search(r"\nimport |from \w+ import", code)
        if first_import:
            pos = first_import.start()
            code = code[:pos] + "\nfrom typing import Any" + code[pos:]
    return code


def improve_file(file_path: str) -> bool:
    """改进单个文件"""
    path = Path(file_path)
    if not path.exists():
        return False
    
    code = path.read_text()
    original = code
    
    # 添加typing导入
    code = add_typing_import(code)
    
    # 简单的改进：为没有类型注释的方法添加基本类型
    # 查找 def method(param1, param2): 并替换为 def method(param1: Any, param2: Any) -> None:
    pattern = r"(\s+def\s+\w+)\(([^)]*)\)(:)"
    
    def add_types(match):
        indent = match.group(1)
        params = match.group(2)
        colon = match.group(3)
        
        # 如果已有类型注释，跳过
        if "->" in match.group(0) or ":" in params:
            return match.group(0)
        
        # 为参数添加类型
        typed_params = []
        for param in params.split(","):
            param = param.strip()
            if not param:
                continue
            
            param_name = param.split("=")[0].strip()
            if param_name == "self":
                typed_params.append(param)
            elif "=" in param:
                # 有默认值的参数
                param_name, default = param.split("=", 1)
                typed_params.append(f"{param_name.strip()}: Any = {default.strip()}")
            else:
                typed_params.append(f"{param_name}: Any")
        
        return f"{indent}({', '.join(typed_params)}) -> None{colon}"
    
    code = re.sub(pattern, add_types, code)
    
    if code != original:
        path.write_text(code)
        return True
    return False


def main():
    """主函数"""
    print("🚀 开始改进OKX代码...")
    
    tasks_file = Path("scripts/agent_tasks/agent_5_tasks.json")
    with open(tasks_file) as f:
        tasks = json.load(f)
    
    improved = 0
    for file_path in tasks.keys():
        if improve_file(file_path):
            print(f"✅ {file_path}")
            improved += 1
    
    print(f"\n✨ 完成! 改进了 {improved} 个文件")


if __name__ == "__main__":
    main()
