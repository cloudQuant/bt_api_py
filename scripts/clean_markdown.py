#!/usr/bin/env python3
"""
Markdown 格式清理脚本
用于修复项目中 Markdown 文件的格式问题，使其符合标准规范
"""

import os
import re
import sys
import argparse
from pathlib import Path
from typing import List, Tuple


class MarkdownCleaner:
    """Markdown 格式清理器"""

    def __init__(self):
        self.fixes_applied = 0
        self.files_processed = 0

    def clean_file(self, file_path: Path) -> Tuple[bool, List[str]]:
        """
        清理单个 Markdown 文件

        Args:
            file_path: 文件路径

        Returns:
            (是否有修改, 修改列表)
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            try:
                with open(file_path, "r", encoding="gbk") as f:
                    content = f.read()
            except UnicodeDecodeError:
                return False, [f"无法读取文件编码: {file_path}"]

        original_content = content
        fixes = []

        # 1. 修复行尾空格
        if re.search(r" +$", content, re.MULTILINE):
            content = re.sub(r" +$", "", content, flags=re.MULTILINE)
            fixes.append("移除行尾空格")

        # 2. 修复多余的空行（超过2个连续空行）
        if re.search(r"\n{4,}", content):
            content = re.sub(r"\n{3,}", "\n\n", content)
            fixes.append("修复多余空行")

        # 3. 修复标题格式
        # 3a. 确保 # 后有空格
        if re.search(r"^#{1,6}[^ #\n]", content, re.MULTILINE):
            content = re.sub(r"^(#{1,6})([^ #\n])", r"\1 \2", content, flags=re.MULTILINE)
            fixes.append("修复标题空格")

        # 3b. 修复标题周围空行 (MD022)
        # 标题前需要空行（除非是文档开头）
        if re.search(r"[^\n]\n(#{1,6} )", content):
            content = re.sub(r"([^\n])\n(#{1,6} )", r"\1\n\n\2", content)
            fixes.append("修复标题前空行")

        # 标题后需要空行（除非紧跟另一个标题或文档结尾）
        if re.search(r"(#{1,6} [^\n]*)\n([^#\n])", content):
            content = re.sub(r"(#{1,6} [^\n]*)\n([^#\n\s])", r"\1\n\n\2", content)
            fixes.append("修复标题后空行")

        # 4. 修复列表格式
        # 4a. 确保 - * + 后有空格
        if re.search(r"^( *)[-*+][^ \n]", content, re.MULTILINE):
            content = re.sub(r"^( *)([-*+])([^ \n])", r"\1\2 \3", content, flags=re.MULTILINE)
            fixes.append("修复列表项空格")

        # 4b. 统一列表样式为 dash (MD004)
        if re.search(r"^( *)\*( )", content, re.MULTILINE):
            content = re.sub(r"^( *)\*( )", r"\1-\2", content, flags=re.MULTILINE)
            fixes.append("统一列表样式为dash")

        if re.search(r"^( *)\+( )", content, re.MULTILINE):
            content = re.sub(r"^( *)\+( )", r"\1-\2", content, flags=re.MULTILINE)
            fixes.append("统一列表样式为dash")

        # 修复特殊情况：* *text** 格式
        if re.search(r"^\* \*([^*]+)\*\*", content, re.MULTILINE):
            content = re.sub(r"^\* \*([^*]+)\*\*", r"**\1**", content, flags=re.MULTILINE)
            fixes.append("修复错误的强调格式")

        # 4c. 修复列表周围空行 (MD032)
        # 更精确的列表检测和修复，支持无序列表和有序列表
        lines = content.split("\n")
        new_lines = []
        i = 0

        while i < len(lines):
            line = lines[i]

            # 检查当前行是否是列表项（无序列表或有序列表）
            is_unordered_list = re.match(r"^( *)-[ \t]", line)
            is_ordered_list = re.match(r"^( *)\d+\.[ \t]", line)
            is_list_item = is_unordered_list or is_ordered_list

            if is_list_item:
                # 检查前一行是否需要空行
                if (
                    i > 0
                    and new_lines
                    and new_lines[-1].strip() != ""
                    and not re.match(r"^( *)-[ \t]", new_lines[-1])
                    and not re.match(r"^( *)\d+\.[ \t]", new_lines[-1])
                    and not re.match(r"^#{1,6} ", new_lines[-1])
                ):
                    new_lines.append("")
                    fixes.append("修复列表前空行")

                # 添加当前列表项
                new_lines.append(line)

                # 查找列表的结束
                j = i + 1
                while j < len(lines):
                    next_line = lines[j]
                    if re.match(r"^( *)-[ \t]", next_line) or re.match(
                        r"^( *)\d+\.[ \t]", next_line
                    ):
                        # 还是列表项，继续
                        new_lines.append(next_line)
                        j += 1
                    elif next_line.strip() == "":
                        # 空行，继续
                        new_lines.append(next_line)
                        j += 1
                    else:
                        # 非列表项，列表结束
                        break

                # 检查列表后是否需要空行
                if (
                    j < len(lines)
                    and lines[j].strip() != ""
                    and not re.match(r"^#{1,6} ", lines[j])
                    and (not new_lines or new_lines[-1].strip() != "")
                ):
                    new_lines.append("")
                    fixes.append("修复列表后空行")

                i = j
            else:
                new_lines.append(line)
                i += 1

        new_content = "\n".join(new_lines)
        if new_content != content:
            content = new_content

        # 5. 修复代码块格式（确保前后有空行）
        # 修复代码块前缺少空行
        if re.search(r"[^\n]\n```", content):
            content = re.sub(r"([^\n])\n(```)", r"\1\n\n\2", content)
            fixes.append("修复代码块前空行")

        # 修复代码块后缺少空行
        if re.search(r"```\n[^\n]", content):
            content = re.sub(r"(```)\n([^\n])", r"\1\n\n\2", content)
            fixes.append("修复代码块后空行")

        # 6. 修复链接格式（移除多余空格）
        if re.search(r"\[ +([^\]]+) +\]", content):
            content = re.sub(r"\[ +([^\]]+) +\]", r"[\1]", content)
            fixes.append("修复链接格式")

        # 7. 修复表格格式
        # 确保表格前后有空行
        if re.search(r"[^\n]\n\|", content):
            content = re.sub(r"([^\n])\n(\|)", r"\1\n\n\2", content)
            fixes.append("修复表格前空行")

        if re.search(r"\|[^\n]*\n[^\n|]", content):
            content = re.sub(r"(\|[^\n]*)\n([^\n|])", r"\1\n\n\2", content)
            fixes.append("修复表格后空行")

        # 8. 修复文件结尾（确保以单个换行符结尾）
        if not content.endswith("\n"):
            content += "\n"
            fixes.append("添加文件结尾换行符")
        elif content.endswith("\n\n"):
            content = content.rstrip("\n") + "\n"
            fixes.append("修复文件结尾多余换行符")

        # 9. 修复中英文混排（在中文和英文之间添加空格）
        # 中文后跟英文数字
        if re.search(r"[\u4e00-\u9fff][a-zA-Z0-9]", content):
            content = re.sub(r"([\u4e00-\u9fff])([a-zA-Z0-9])", r"\1 \2", content)
            fixes.append("修复中英文混排（中文后）")

        # 英文数字后跟中文
        if re.search(r"[a-zA-Z0-9][\u4e00-\u9fff]", content):
            content = re.sub(r"([a-zA-Z0-9])([\u4e00-\u9fff])", r"\1 \2", content)
            fixes.append("修复中英文混排（英文后）")

        # 10. 修复强调格式（**text** 和 *text*）
        # 确保强调符号紧贴内容
        if re.search(r"\*\* +([^*]+) +\*\*", content):
            content = re.sub(r"\*\* +([^*]+) +\*\*", r"**\1**", content)
            fixes.append("修复粗体格式")

        if re.search(r"(?<!\*)\* +([^*]+) +\*(?!\*)", content):
            content = re.sub(r"(?<!\*)\* +([^*]+) +\*(?!\*)", r"*\1*", content)
            fixes.append("修复斜体格式")

        # 11. 修复其他 markdownlint 规则
        # MD009: 行尾空格（已在步骤1处理）
        # MD010: 硬制表符
        if "\t" in content:
            content = content.replace("\t", "    ")
            fixes.append("替换制表符为空格")

        # MD012: 多个连续空行（已在步骤2处理，但加强检查）
        if re.search(r"\n\s*\n\s*\n\s*\n", content):
            content = re.sub(r"\n\s*\n\s*\n\s*\n+", "\n\n", content)
            fixes.append("修复多个连续空行")

        # MD018: 标题前没有空格（已在步骤3处理）
        # MD019: 标题前有多个空格
        if re.search(r"^#{1,6}  +", content, re.MULTILINE):
            content = re.sub(r"^(#{1,6})  +", r"\1 ", content, flags=re.MULTILINE)
            fixes.append("修复标题多余空格")

        # MD023: 标题缩进
        if re.search(r"^ +#{1,6}", content, re.MULTILINE):
            content = re.sub(r"^ +(#{1,6})", r"\1", content, flags=re.MULTILINE)
            fixes.append("修复标题缩进")

        # MD029: 有序列表编号
        # 修复有序列表编号，使用递增编号 1. 2. 3. 格式
        lines = content.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i]

            # 检查是否是有序列表项
            match = re.match(r"^( *)\d+\. (.+)", line)
            if match:
                indent = match.group(1)

                # 收集连续的同级有序列表项（不跨越非列表内容）
                list_items = []
                j = i

                while j < len(lines):
                    current_line = lines[j]
                    current_match = re.match(r"^( *)\d+\. (.+)", current_line)

                    if current_match and current_match.group(1) == indent:
                        # 同级列表项
                        list_items.append((j, current_match.group(2)))
                    elif current_line.strip() == "":
                        # 空行，可能是列表项之间的分隔，继续查找
                        pass
                    elif current_match and len(current_match.group(1)) > len(indent):
                        # 更深层级的子列表，跳过
                        pass
                    else:
                        # 遇到非列表内容或更浅层级，结束当前列表
                        break

                    j += 1

                # 重新编号同级列表项
                has_changes = False
                for idx, (line_idx, text) in enumerate(list_items, 1):
                    new_line = f"{indent}{idx}. {text}"
                    if lines[line_idx] != new_line:
                        lines[line_idx] = new_line
                        has_changes = True

                if has_changes:
                    fixes.append("修复有序列表编号")

                # 移动到下一个未处理的行
                if list_items:
                    # 跳过到最后一个处理的列表项之后
                    i = list_items[-1][0] + 1
                else:
                    i += 1
            else:
                i += 1

        new_content = "\n".join(lines)
        if new_content != content:
            content = new_content

        # MD034: 裸URL
        # 将裸URL包装在 <> 中
        if re.search(r"(?<!<)https?://[^\s<>]+(?!>)", content):
            content = re.sub(r"(?<!<)(https?://[^\s<>]+)(?!>)", r"<\1>", content)
            fixes.append("修复裸URL格式")

        # MD040: 代码块语言标识
        # 为没有语言标识的代码块添加标识
        if re.search(r"^```\s*$", content, re.MULTILINE):
            content = re.sub(r"^```\s*$", "```bash", content, flags=re.MULTILINE)
            fixes.append("添加代码块语言标识")

        # 检查是否有修改
        has_changes = content != original_content

        if has_changes:
            # 写回文件
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.fixes_applied += len(fixes)

        return has_changes, fixes

    def find_markdown_files(
        self, directory: Path, exclude_patterns: List[str] = None
    ) -> List[Path]:
        """
        查找目录中的所有 Markdown 文件

        Args:
            directory: 搜索目录
            exclude_patterns: 排除的模式列表

        Returns:
            Markdown 文件路径列表
        """
        if exclude_patterns is None:
            exclude_patterns = [
                "node_modules",
                ".git",
                "venv",
                ".venv",
                "__pycache__",
                ".pytest_cache",
            ]

        markdown_files = []

        for md_file in directory.rglob("*.md"):
            # 检查是否应该排除
            should_exclude = False
            for pattern in exclude_patterns:
                if pattern in str(md_file):
                    should_exclude = True
                    break

            if not should_exclude:
                markdown_files.append(md_file)

        return sorted(markdown_files)

    def clean_directory(self, directory: Path, dry_run: bool = False) -> None:
        """
        清理目录中的所有 Markdown 文件

        Args:
            directory: 目标目录
            dry_run: 是否为试运行模式
        """
        markdown_files = self.find_markdown_files(directory)

        if not markdown_files:
            print("未找到 Markdown 文件")
            return

        print(f"找到 {len(markdown_files)} 个 Markdown 文件")

        if dry_run:
            print("\n🔍 试运行模式 - 不会修改文件\n")
        else:
            print("\n🔧 开始清理 Markdown 文件\n")

        for file_path in markdown_files:
            relative_path = file_path.relative_to(directory)

            if dry_run:
                # 试运行模式：只检查不修改
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                except UnicodeDecodeError:
                    try:
                        with open(file_path, "r", encoding="gbk") as f:
                            content = f.read()
                    except UnicodeDecodeError:
                        print(f"❌ {relative_path} - 无法读取文件编码")
                        continue

                # 检查是否需要修复
                needs_fix = False
                issues = []

                if re.search(r" +$", content, re.MULTILINE):
                    issues.append("行尾空格")
                    needs_fix = True

                if re.search(r"\n{4,}", content):
                    issues.append("多余空行")
                    needs_fix = True

                if re.search(r"^#{1,6}[^ #\n]", content, re.MULTILINE):
                    issues.append("标题格式")
                    needs_fix = True

                if re.search(r"^( *)[-*+][^ \n]", content, re.MULTILINE):
                    issues.append("列表格式")
                    needs_fix = True

                # 检查列表周围空行问题
                lines = content.split("\n")
                for i, line in enumerate(lines):
                    # 检查有序列表和无序列表
                    if re.match(r"^( *)-[ \t]", line) or re.match(r"^( *)\d+\.[ \t]", line):
                        # 检查前一行
                        if (
                            i > 0
                            and lines[i - 1].strip() != ""
                            and not re.match(r"^( *)-[ \t]", lines[i - 1])
                            and not re.match(r"^( *)\d+\.[ \t]", lines[i - 1])
                            and not re.match(r"^#{1,6} ", lines[i - 1])
                        ):
                            issues.append("列表前缺少空行")
                            needs_fix = True
                            break

                if needs_fix:
                    print(f"⚠️  {relative_path} - 需要修复: {', '.join(issues)}")
                else:
                    print(f"✅ {relative_path} - 格式正确")
            else:
                # 实际清理模式
                has_changes, fixes = self.clean_file(file_path)
                self.files_processed += 1

                if has_changes:
                    print(f"🔧 {relative_path} - 已修复: {', '.join(fixes)}")
                else:
                    print(f"✅ {relative_path} - 无需修复")

        if not dry_run:
            print(f"\n📊 清理完成:")
            print(f"   处理文件: {self.files_processed}")
            print(f"   应用修复: {self.fixes_applied}")
        else:
            print(f"\n📊 检查完成: {len(markdown_files)} 个文件")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Markdown 格式清理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  python clean_markdown.py                    # 清理当前目录
  python clean_markdown.py --dry-run          # 试运行模式
  python clean_markdown.py /path/to/docs      # 清理指定目录
  python clean_markdown.py README.md          # 清理单个文件
        """,
    )

    parser.add_argument(
        "path", nargs="?", default=".", help="要清理的文件或目录路径（默认: 当前目录）"
    )

    parser.add_argument("--dry-run", action="store_true", help="试运行模式，只检查不修改文件")

    parser.add_argument("--version", action="version", version="Markdown Cleaner 1.0.0")

    args = parser.parse_args()

    # 解析路径
    target_path = Path(args.path).resolve()

    if not target_path.exists():
        print(f"❌ 错误: 路径不存在 - {target_path}")
        sys.exit(1)

    cleaner = MarkdownCleaner()

    try:
        if target_path.is_file():
            # 处理单个文件
            if target_path.suffix.lower() != ".md":
                print(f"❌ 错误: 不是 Markdown 文件 - {target_path}")
                sys.exit(1)

            print(f"🔧 清理文件: {target_path.name}")

            if args.dry_run:
                print("🔍 试运行模式 - 不会修改文件")
                # 这里可以添加单文件的试运行逻辑
            else:
                has_changes, fixes = cleaner.clean_file(target_path)
                if has_changes:
                    print(f"✅ 已修复: {', '.join(fixes)}")
                else:
                    print("✅ 无需修复")
        else:
            # 处理目录
            print(f"🔧 清理目录: {target_path}")
            cleaner.clean_directory(target_path, args.dry_run)

    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断操作")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
