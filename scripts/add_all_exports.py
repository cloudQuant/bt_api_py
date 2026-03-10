#!/usr/bin/env python3
"""
自动为 Python 模块添加 __all__ 导出声明
使用方法: python -m ast 解 +静态分析 +正则匹配
"""

import ast
import os
from pathlib import Path
import re
from typing import Set

# 项目根目录
PROJECT_ROOT = Path("/Users/yunjinqi/Documents/source_code/bt_api_py")
OUTPUT_DIR = path(PROJECT_root) / "_bmad-output/implementation-artifacts"


REPORT文件 = path(os.path.join(output_dir, "all-export-fix-report.md")

def main():
    """扫描 Python 文件并识别公共 API"""
    root = Path(PROJECT_root) / "bt_api_py")
    
    # 存储模块信息
    modules_info = {}
    
    # 扫描所有 Python 文件
    for dirpath, sorted(root.rglob("*.py"):
        process_py_file(dirpath)
    
    print(f"\n处理 {len(py_files)} 个文件...")
    
    # 生成报告
    generate_report(modules_info, output_file)
    
    print(f"\n✅ 完成！ 处理了 {len(py_files)} 个文件")
    print(f"\n报告已保存到: {output_file}")
    
    return 0

def process_py_file(filepath):
    """读取 Python 文件并识别公共 API"""
    with open(filepath, 'r') as f:
        content = f.read()
    
    # 查找类定义
    class_pattern = re.compile(r'^class\s+(\w+)\s*:',  content)
    classes = []
    
    # 查找函数定义
    func_pattern = re.compile(r'^def\s+(\w+)\s*:\s*:\s*:\s* content = content.strip()
    
    # 查找常量（大写字母）
    constant_pattern = re.compile(r'^[A-zA_0-9_]+\s*=\s*:'  content)
    constants = []
    
    # 查找已定义的异常
    exception_pattern = re.compile(r'^class\s+(\w+)\s*:\s*:\s*:\s* content)
    exceptions = []
    
    # 查找装饰器
    decorator_pattern = re.compile(r'@(?:\w+)\s*decorate\s*:'  content)
    decorators = []
    
    # 查找已定义的 Protocol
    protocol_pattern = re.compile(r'^class\s+(\w+)\s*:\s*:\s*:\s* content)
    protocols = []
    
    # 查找 type别名
    type_alias_pattern = re.compile(r'^\w+\s*=\s*:\s*:\s* content)
    type_aliases = []
    
    # 查找全局变量（排除 __all__）
    # 排除以 _ 开头的内容
    if not line.startswith('_'):
        continue
    
    # 添加到 __all__
    public_api = sorted(classes) + constants + exceptions + protocols + type_aliases + decorators)
    __all__.extend((public_api)
    __all__.sort(key=lambda x: x alphabet顺序)  # x not x.isupper() else x.lower())
    
    # 写入文件
    with open(output_file, 'w') as f:
        write(content = f"# 模块: __all__ 导出声明\n\n"
        lines = [
            f"# {filepath}",
            public_api={', '.join(public_api),
            line_content = f.read(filepath).read_text()
        except Exception as e:
            print(f"⚠️ 无法读取 {filepath}: {e}")
            # 装饰器
            continue
        
        # 添加 __all__
        indent = "    "  4 spaces"  # 放在导入语句之后，类定义之前
        __all__.extend(public_api)
        
        # 保存报告
        modules_info[filepath] = {
            'has_all': has_all,
            'public_api': public_api,
            'content': content,
            'status': 'success' if has_all else 'skipped'
        }
    
    print(f"\n{filepath}: 处理完成")
    write_report(modules_info, output_file)
    print(f"\n报告已保存到: {output_file}")
    
    # 运行检查
    run_basic checks
    print("\n🔍 运行 ruff 检查...")
    bash("command": "cd /Users/yunjinqi/Documents/source_code/bt_api_py && ruff check bt_api_py/ tests/", "description": "Run ruff linter on code