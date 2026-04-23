#!/usr/bin/env python3
"""
代码质量检查工具 - 检查类型注释和Google风格文档注释

检查项目:
1. 类型注释: 函数参数和返回值的类型提示
2. Google风格文档注释: Args, Returns, Raises等部分

Usage:
    python scripts/check_code_quality.py [directory] [options]

Examples:
    # 检查整个bt_api_py目录
    python scripts/check_code_quality.py bt_api_py

    # 只检查特定目录
    python scripts/check_code_quality.py bt_api_py/feeds

    # 只检查类型注释
    python scripts/check_code_quality.py bt_api_py --check type-hints

    # 只检查文档注释
    python scripts/check_code_quality.py bt_api_py --check docstrings

    # 生成详细报告
    python scripts/check_code_quality.py bt_api_py --verbose
"""

import argparse
import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FunctionInfo:
    """函数信息"""

    name: str
    line_number: int
    has_return_annotation: bool = False
    missing_param_annotations: list[str] = field(default_factory=list)
    has_docstring: bool = False
    docstring_sections: dict[str, bool] = field(default_factory=dict)
    is_method: bool = False
    class_name: str | None = None


@dataclass
class ClassInfo:
    """类信息"""

    name: str
    line_number: int
    has_docstring: bool = False
    methods: list[FunctionInfo] = field(default_factory=list)


@dataclass
class FileInfo:
    """文件信息"""

    filepath: str
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    has_module_docstring: bool = False


@dataclass
class QualityReport:
    """质量报告"""

    total_files: int = 0
    total_functions: int = 0
    total_classes: int = 0
    total_methods: int = 0

    functions_with_full_annotations: int = 0
    functions_with_docstrings: int = 0
    classes_with_docstrings: int = 0

    files_without_module_docstring: list[str] = field(default_factory=list)
    missing_type_hints: dict[str, list[tuple[str, int, str]]] = field(default_factory=dict)
    missing_docstrings: dict[str, list[tuple[str, int, str]]] = field(default_factory=dict)


class CodeQualityChecker:
    """代码质量检查器"""

    # Google风格docstring的必需部分
    GOOGLE_DOCSTRING_SECTIONS = ["Args", "Returns", "Raises", "Example", "Note"]

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.report = QualityReport()

    def check_directory(
        self, directory: str, exclude_dirs: list[str] | None = None
    ) -> QualityReport:
        """检查目录下所有Python文件

        Args:
            directory: 要检查的目录路径
            exclude_dirs: 要排除的目录列表

        Returns:
            QualityReport: 质量检查报告
        """
        if exclude_dirs is None:
            exclude_dirs = ["__pycache__", ".git", "node_modules", "venv", ".venv"]

        directory_path = Path(directory)
        if not directory_path.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")

        # 查找所有Python文件
        python_files = []
        for root, dirs, files in os.walk(directory):
            # 排除指定目录
            dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith(".")]

            for file in files:
                if file.endswith(".py"):
                    python_files.append(Path(root) / file)

        if self.verbose:
            print(f"Found {len(python_files)} Python files to check\n")

        # 检查每个文件
        for filepath in python_files:
            try:
                file_info = self.check_file(str(filepath))
                self._update_report(str(filepath), file_info)
            except Exception as e:
                if self.verbose:
                    print(f"Error checking {filepath}: {e}")

        return self.report

    def check_file(self, filepath: str) -> FileInfo:
        """检查单个Python文件

        Args:
            filepath: 文件路径

        Returns:
            FileInfo: 文件检查信息
        """
        with open(filepath, encoding="utf-8") as f:
            source = f.read()

        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            if self.verbose:
                print(f"Syntax error in {filepath}: {e}")
            return FileInfo(filepath=filepath)

        file_info = FileInfo(filepath=filepath)

        # 检查模块docstring
        module_docstring = ast.get_docstring(tree)
        file_info.has_module_docstring = module_docstring is not None

        # 遍历AST
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                # 顶层函数
                if not self._is_method(node, tree):
                    func_info = self._check_function(node)
                    file_info.functions.append(func_info)

            elif isinstance(node, ast.ClassDef):
                # 类定义
                class_info = self._check_class(node)
                file_info.classes.append(class_info)

        return file_info

    def _is_method(
        self, func_node: ast.FunctionDef | ast.AsyncFunctionDef, tree: ast.Module
    ) -> bool:
        """判断函数是否是类的方法"""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if item is func_node:
                        return True
        return False

    def _check_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, class_name: str | None = None
    ) -> FunctionInfo:
        """检查函数的类型注释和文档注释

        Args:
            node: AST函数节点
            class_name: 所属类名（如果是方法）

        Returns:
            FunctionInfo: 函数检查信息
        """
        func_info = FunctionInfo(
            name=node.name,
            line_number=node.lineno,
            is_method=class_name is not None,
            class_name=class_name,
        )

        # 检查返回值类型注释
        func_info.has_return_annotation = node.returns is not None

        # 检查参数类型注释
        args = node.args
        all_args = []

        # 位置参数
        for arg in args.args:
            # 跳过self和cls
            if arg.arg in ("self", "cls"):
                continue
            all_args.append(arg)

        # 仅关键字参数
        all_args.extend(args.kwonlyargs)

        # *args
        if args.vararg:
            all_args.append(args.vararg)

        # **kwargs
        if args.kwarg:
            all_args.append(args.kwarg)

        # 检查每个参数是否有类型注释
        for arg in all_args:
            if arg.annotation is None:
                func_info.missing_param_annotations.append(arg.arg)

        # 检查docstring
        docstring = ast.get_docstring(node)
        func_info.has_docstring = docstring is not None

        if docstring:
            func_info.docstring_sections = self._check_google_docstring(docstring)

        return func_info

    def _check_class(self, node: ast.ClassDef) -> ClassInfo:
        """检查类的文档注释

        Args:
            node: AST类节点

        Returns:
            ClassInfo: 类检查信息
        """
        class_info = ClassInfo(name=node.name, line_number=node.lineno)

        # 检查类docstring
        docstring = ast.get_docstring(node)
        class_info.has_docstring = docstring is not None

        # 检查类中的方法
        for item in node.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                # 跳过特殊方法（__init__等）如果不需要检查
                if not item.name.startswith("_") or item.name in ("__init__", "__call__"):
                    method_info = self._check_function(item, class_name=node.name)
                    class_info.methods.append(method_info)

        return class_info

    def _check_google_docstring(self, docstring: str) -> dict[str, bool]:
        """检查Google风格docstring的各个部分

        Args:
            docstring: 文档字符串

        Returns:
            dict: 包含各部分是否存在的信息
        """
        sections = {}

        # 检查Args部分
        sections["has_args"] = bool(re.search(r"\bArgs:\s*$", docstring, re.MULTILINE))

        # 检查Returns部分
        sections["has_returns"] = bool(re.search(r"\bReturns:\s*$", docstring, re.MULTILINE))

        # 检查Raises部分
        sections["has_raises"] = bool(re.search(r"\bRaises:\s*$", docstring, re.MULTILINE))

        # 检查Example部分
        sections["has_example"] = bool(re.search(r"\bExample:\s*$", docstring, re.MULTILINE))

        # 检查Yields部分（用于生成器）
        sections["has_yields"] = bool(re.search(r"\bYields:\s*$", docstring, re.MULTILINE))

        return sections

    def _update_report(self, filepath: str, file_info: FileInfo) -> None:
        """更新质量报告

        Args:
            filepath: 文件路径
            file_info: 文件检查信息
        """
        self.report.total_files += 1

        # 检查模块docstring
        if not file_info.has_module_docstring:
            self.report.files_without_module_docstring.append(filepath)

        # 统计函数
        for func in file_info.functions:
            self._update_function_stats(filepath, func, is_method=False)

        # 统计类和方法
        for cls in file_info.classes:
            self.report.total_classes += 1

            if cls.has_docstring:
                self.report.classes_with_docstrings += 1
            else:
                self._add_missing_docstring(filepath, cls.name, cls.line_number, "class")

            # 统计方法
            for method in cls.methods:
                self._update_function_stats(filepath, method, is_method=True)

    def _update_function_stats(self, filepath: str, func: FunctionInfo, is_method: bool) -> None:
        """更新函数统计信息

        Args:
            filepath: 文件路径
            func: 函数信息
            is_method: 是否是方法
        """
        if is_method:
            self.report.total_methods += 1
        else:
            self.report.total_functions += 1

        # 检查类型注释
        has_full_annotations = (
            func.has_return_annotation and len(func.missing_param_annotations) == 0
        )

        if has_full_annotations:
            self.report.functions_with_full_annotations += 1
        else:
            # 记录缺失的类型注释
            if not func.has_return_annotation:
                location = f"{func.class_name}.{func.name}" if func.class_name else func.name
                func_type = "method" if is_method else "function"
                self._add_missing_type_hint(
                    filepath, location, func.line_number, f"{func_type} missing return annotation"
                )

            for param in func.missing_param_annotations:
                location = f"{func.class_name}.{func.name}" if func.class_name else func.name
                func_type = "method" if is_method else "function"
                self._add_missing_type_hint(
                    filepath,
                    location,
                    func.line_number,
                    f"{func_type} missing annotation for param '{param}'",
                )

        # 检查docstring
        if func.has_docstring:
            self.report.functions_with_docstrings += 1

            # 检查Google风格docstring的完整性
            if not self._is_google_docstring_complete(func):
                location = f"{func.class_name}.{func.name}" if func.class_name else func.name
                func_type = "method" if is_method else "function"
                self._add_missing_docstring(
                    filepath,
                    location,
                    func.line_number,
                    f"{func_type} has incomplete Google-style docstring",
                )
        else:
            location = f"{func.class_name}.{func.name}" if func.class_name else func.name
            func_type = "method" if is_method else "function"
            self._add_missing_docstring(
                filepath, location, func.line_number, f"{func_type} missing docstring"
            )

    def _is_google_docstring_complete(self, func: FunctionInfo) -> bool:
        """判断Google风格docstring是否完整

        Args:
            func: 函数信息

        Returns:
            bool: docstring是否完整
        """
        sections = func.docstring_sections

        # 如果有参数，应该有Args部分
        if len(func.missing_param_annotations) > 0 or True:  # 简化：所有函数都应该有Args
            if not sections.get("has_args", False):
                return False

        # 如果有返回值注释，应该有Returns部分
        if func.has_return_annotation:
            if not sections.get("has_returns", False):
                return False

        return True

    def _add_missing_type_hint(self, filepath: str, location: str, line: int, issue: str) -> None:
        """添加缺失类型注释的记录"""
        if filepath not in self.report.missing_type_hints:
            self.report.missing_type_hints[filepath] = []
        self.report.missing_type_hints[filepath].append((location, line, issue))

    def _add_missing_docstring(self, filepath: str, location: str, line: int, issue: str) -> None:
        """添加缺失文档注释的记录"""
        if filepath not in self.report.missing_docstrings:
            self.report.missing_docstrings[filepath] = []
        self.report.missing_docstrings[filepath].append((location, line, issue))


class ReportPrinter:
    """报告打印器"""

    def __init__(self, report: QualityReport, verbose: bool = False):
        self.report = report
        self.verbose = verbose

    def print_summary(self) -> None:
        """打印摘要报告"""
        print("=" * 80)
        print("代码质量检查报告")
        print("=" * 80)
        print()

        print("📊 统计信息:")
        print(f"  检查文件总数: {self.report.total_files}")
        print(f"  顶层函数总数: {self.report.total_functions}")
        print(f"  类总数: {self.report.total_classes}")
        print(f"  方法总数: {self.report.total_methods}")
        print()

        # 类型注释统计
        total_funcs = self.report.total_functions + self.report.total_methods
        if total_funcs > 0:
            type_coverage = (self.report.functions_with_full_annotations / total_funcs) * 100
            print("✨ 类型注释覆盖率:")
            print(
                f"  完整类型注释的函数: {self.report.functions_with_full_annotations}/{total_funcs}"
            )
            print(f"  覆盖率: {type_coverage:.1f}%")
            print()

        # 文档注释统计
        if total_funcs > 0:
            doc_coverage = (self.report.functions_with_docstrings / total_funcs) * 100
            print("📝 文档注释覆盖率:")
            print(f"  有文档注释的函数: {self.report.functions_with_docstrings}/{total_funcs}")
            print(f"  覆盖率: {doc_coverage:.1f}%")
            print()

        if self.report.total_classes > 0:
            class_doc_coverage = (
                self.report.classes_with_docstrings / self.report.total_classes
            ) * 100
            print(
                f"  有文档注释的类: {self.report.classes_with_docstrings}/{self.report.total_classes}"
            )
            print(f"  类覆盖率: {class_doc_coverage:.1f}%")
            print()

        # 模块docstring
        if self.report.files_without_module_docstring:
            print(f"⚠️  缺少模块文档注释的文件: {len(self.report.files_without_module_docstring)}")
            if self.verbose:
                for filepath in self.report.files_without_module_docstring[:10]:
                    print(f"  - {filepath}")
                if len(self.report.files_without_module_docstring) > 10:
                    print(
                        f"  ... 还有 {len(self.report.files_without_module_docstring) - 10} 个文件"
                    )
            print()

    def print_missing_type_hints(self, limit: int = 20) -> None:
        """打印缺失类型注释的详情

        Args:
            limit: 每个文件显示的最大问题数
        """
        if not self.report.missing_type_hints:
            print("✅ 所有函数都有完整的类型注释！")
            return

        print("=" * 80)
        print(f"❌ 缺失类型注释的问题 (共 {len(self.report.missing_type_hints)} 个文件)")
        print("=" * 80)
        print()

        count = 0
        for filepath, issues in sorted(self.report.missing_type_hints.items()):
            if count >= limit:
                print(f"\n... 还有 {len(self.report.missing_type_hints) - limit} 个文件未显示")
                break

            print(f"📄 {filepath}")
            for location, line, issue in issues[:10]:  # 每个文件最多显示10个问题
                print(f"  Line {line:4d}: {location} - {issue}")
            if len(issues) > 10:
                print(f"  ... 还有 {len(issues) - 10} 个问题")
            print()
            count += 1

    def print_missing_docstrings(self, limit: int = 20) -> None:
        """打印缺失文档注释的详情

        Args:
            limit: 每个文件显示的最大问题数
        """
        if not self.report.missing_docstrings:
            print("✅ 所有函数都有文档注释！")
            return

        print("=" * 80)
        print(f"❌ 缺失文档注释的问题 (共 {len(self.report.missing_docstrings)} 个文件)")
        print("=" * 80)
        print()

        count = 0
        for filepath, issues in sorted(self.report.missing_docstrings.items()):
            if count >= limit:
                print(f"\n... 还有 {len(self.report.missing_docstrings) - limit} 个文件未显示")
                break

            print(f"📄 {filepath}")
            for location, line, issue in issues[:10]:  # 每个文件最多显示10个问题
                print(f"  Line {line:4d}: {location} - {issue}")
            if len(issues) > 10:
                print(f"  ... 还有 {len(issues) - 10} 个问题")
            print()
            count += 1

    def export_to_file(self, output_file: str) -> None:
        """导出报告到文件

        Args:
            output_file: 输出文件路径
        """
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("代码质量检查报告\n")
            f.write("=" * 80 + "\n\n")

            # 统计信息
            f.write("统计信息:\n")
            f.write(f"检查文件总数: {self.report.total_files}\n")
            f.write(f"顶层函数总数: {self.report.total_functions}\n")
            f.write(f"类总数: {self.report.total_classes}\n")
            f.write(f"方法总数: {self.report.total_methods}\n\n")

            # 类型注释
            f.write("缺失类型注释:\n")
            f.write("-" * 80 + "\n")
            for filepath, issues in sorted(self.report.missing_type_hints.items()):
                f.write(f"\n{filepath}\n")
                for location, line, issue in issues:
                    f.write(f"  Line {line}: {location} - {issue}\n")

            # 文档注释
            f.write("\n\n缺失文档注释:\n")
            f.write("-" * 80 + "\n")
            for filepath, issues in sorted(self.report.missing_docstrings.items()):
                f.write(f"\n{filepath}\n")
                for location, line, issue in issues:
                    f.write(f"  Line {line}: {location} - {issue}\n")

        print(f"✅ 报告已导出到: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="检查Python代码的类型注释和Google风格文档注释",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 检查整个bt_api_py目录
  python scripts/check_code_quality.py bt_api_py
  
  # 只检查类型注释
  python scripts/check_code_quality.py bt_api_py --check type-hints
  
  # 只检查文档注释
  python scripts/check_code_quality.py bt_api_py --check docstrings
  
  # 详细模式
  python scripts/check_code_quality.py bt_api_py --verbose
  
  # 导出到文件
  python scripts/check_code_quality.py bt_api_py --output report.txt
        """,
    )

    parser.add_argument("directory", help="要检查的目录路径")
    parser.add_argument(
        "--check", choices=["type-hints", "docstrings", "all"], default="all", help="检查类型"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")
    parser.add_argument("--output", "-o", help="导出报告到文件")
    parser.add_argument("--limit", type=int, default=20, help="显示问题的文件数量限制（默认20）")
    parser.add_argument("--exclude", nargs="+", help="要排除的目录", default=[])

    args = parser.parse_args()

    # 执行检查
    checker = CodeQualityChecker(verbose=args.verbose)
    report = checker.check_directory(args.directory, exclude_dirs=args.exclude)

    # 打印报告
    printer = ReportPrinter(report, verbose=args.verbose)
    printer.print_summary()

    if args.check in ["type-hints", "all"]:
        printer.print_missing_type_hints(limit=args.limit)

    if args.check in ["docstrings", "all"]:
        printer.print_missing_docstrings(limit=args.limit)

    # 导出报告
    if args.output:
        printer.export_to_file(args.output)


if __name__ == "__main__":
    main()
