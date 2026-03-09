#!/usr/bin/env python3
"""
批量改进代码质量脚本
为指定文件添加类型注释和Google风格文档字符串
"""

import json
import re
import sys
from pathlib import Path
from typing import Any


def add_type_hints_to_params(code: str, params: list[tuple[str, str]]) -> str:
    """为函数参数添加类型提示"""
    for param_name, param_type in params:
        # 匹配参数定义
        pattern = rf"(\s+{param_name}\s*)(?=:)"
        if re.search(pattern, code):
            code = re.sub(pattern, f"\\1 {param_name}: {param_type},", code)
    return code


def add_return_annotation(code: str, return_type: str, func_name: str) -> str:
    """为函数添加返回类型注释"""
    # 查找函数定义行
    pattern = rf"(def\s+{func_name}\s*\([^)]*\)\s*:"
    match = re.search(pattern, code)
    if match:
        indent = match.group(1)
        func_def = match.group(0)
        # 检查是否已经有返回类型
        if "-> " not in func_def:
            code = code.replace(func_def, func_def + f" -> {return_type}:", 1)
    return code


def add_google_docstring(code: str, func_name: str, description: str, 
                          params: dict[str, str] | None = None, 
                          returns: str | None = None) -> str:
    """添加Google风格文档字符串"""
    # 查找函数定义
    pattern = rf"(    def\s+{func_name}\s*\([^)]*\)\s*:"
    match = re.search(pattern, code)
    if not match:
        return code
    
    indent = match.group(1)
    func_def_end = match.end()
    
    # 构建文档字符串
    docstring_lines = ['    """' + description]
    if params:
        docstring_lines.append("")
        docstring_lines.append("        Args:")
        for param_name, param_desc in params.items():
            docstring_lines.append(f"            {param_name}: {param_desc}.")
    if returns:
        docstring_lines.append("")
        docstring_lines.append("        Returns:")
        docstring_lines.append(f"            {returns}.")
    docstring_lines.append('        """')
    
    docstring = "\n".join(docstring_lines)
    
    # 插入文档字符串
    lines = code.split("\n")
    insert_pos = func_def_end
    lines.insert(insert_pos, docstring)
    
    return "\n".join(lines)


def improve_simple_container_file(file_path: Path) -> None:
    """改进简单的容器文件"""
    print(f"改进文件: {file_path}")
    content = file_path.read_text()
    
    # 基本改进 - 确保有类型导入
    if "from typing import Any" not in content:
        # 在文件开头添加类型导入
        lines = content.split("\n")
        import_pos = 0
        for i, line in enumerate(lines[:20]):
            if line.startswith("import") or line.startswith("from"):
                import_pos = i + 1
            if line.strip() and not line.startswith("#") and not line.startswith("import") and not line.startswith("from"):
                break
        
        lines.insert(import_pos, "from typing import Any\n")
        content = "\n".join(lines)
    
    # 保存文件
    file_path.write_text(content)
    print(f"✓ 完成: {file_path.name}")


def main():
    # 读取任务文件
    task_file = Path("scripts/agent_tasks_even/agent_8_tasks.json")
    with open(task_file) as "r") as f:
        tasks = json.load(f)
    
    print(f"总共需要改进 {len(tasks)} 个文件\n")
    
    # 改进每个文件
    for file_path_str in tasks.keys():
        file_path = Path(file_path_str)
        if file_path.exists():
            try:
                improve_simple_container_file(file_path)
            except Exception as e:
                print(f"❌ 错误处理 {file_path}: {e}")
        else:
            print(f"⚠ 文件不存在: {file_path}")
    
    print("\n所有文件改进完成！")


if __name__ == "__main__":
    main()
