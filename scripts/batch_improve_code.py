#!/usr/bin/env python3
"""批量改进代码质量 - 添加类型注释和文档注释"""

import json
import re
from pathlib import Path


def improve_file(file_path: str, issues: list[str]) -> bool:
    """改进单个文件的代码质量"""
    path = Path(file_path)
    if not path.exists():
        print(f"✗ 文件不存在: {file_path}")
        return False

    try:
        content = path.read_text(encoding="utf-8")
        lines = content.split("\n")

        # 解析问题，按行号分组
        issues_by_line = {}
        for issue in issues:
            match = re.match(r"(TypeHint|Docstring): Line (\d+): (.+)", issue)
            if match:
                issue_type, line_num_str, desc = match.groups()
                line_num = int(line_num_str)
                if line_num not in issues_by_line:
                    issues_by_line[line_num] = []
                issues_by_line[line_num].append((issue_type, desc))

        # 从后往前处理，避免行号偏移
        total_changes = 0
        for line_num in sorted(issues_by_line.keys(), reverse=True):
            for issue_type, desc in issues_by_line[line_num]:
                if issue_type == "TypeHint":
                    changes = fix_type_hint(lines, line_num, desc)
                    total_changes += changes
                elif issue_type == "Docstring":
                    changes = fix_docstring(lines, line_num, desc)
                    total_changes += changes

        if total_changes > 0:
            path.write_text("\n".join(lines), encoding="utf-8")
            print(f"✓ {file_path} ({total_changes} 处改进)")
            return True
        else:
            print(f"○ {file_path} (无需改进)")
            return False

    except Exception as e:
        print(f"✗ 处理失败 {file_path}: {e}")
        return False


def fix_type_hint(lines: list[str], line_num: int, desc: str) -> int:
    """修复类型注释问题"""
    line_idx = line_num - 1
    if line_idx >= len(lines):
        return 0

    original_line = lines[line_idx]
    line = original_line

    # 添加返回类型注释
    if "missing return annotation" in desc:
        # 查找 def 语句
        if "def " in line and ") -> " not in line:
            # 在 ): 之前添加 -> None
            if "):" in line:
                line = line.replace("):", ") -> None:")
            elif ") :" in line:
                line = line.replace(") :", ") -> None :")

    # 添加参数类型注释
    elif "missing annotation for param" in desc:
        param_match = re.search(r"param '(\w+)'", desc)
        if param_match:
            param_name = param_match.group(1)
            # 推断参数类型
            param_type = infer_type(param_name)
            # 为参数添加类型注释
            # 匹配 param_name= 或 param_name, 或 param_name):
            pattern = rf"\b{re.escape(param_name)}\b(?!\s*:)"
            if f"{param_name}:" not in line:
                line = re.sub(pattern, f"{param_name}: {param_type}", line, count=1)

    if line != original_line:
        lines[line_idx] = line
        return 1

    return 0


def fix_docstring(lines: list[str], line_num: int, desc: str) -> int:
    """修复文档注释问题"""
    line_idx = line_num - 1
    if line_idx >= len(lines):
        return 0

    # 检查下一行是否已有文档注释
    if line_idx + 1 < len(lines):
        next_line = lines[line_idx + 1].strip()
        if next_line.startswith('"""'):
            # 已有文档注释，需要完善
            return enhance_docstring(lines, line_num, desc)
        else:
            # 没有文档注释，需要添加
            return add_docstring(lines, line_num, desc)

    return 0


def enhance_docstring(lines: list[str], line_num: int, desc: str) -> int:
    """完善已有的文档注释"""
    line_idx = line_num - 1
    if line_idx + 1 >= len(lines):
        return 0

    # 找到文档注释的结束位置
    start_idx = line_idx + 1
    end_idx = start_idx

    for i in range(start_idx, min(start_idx + 20, len(lines))):
        if '"""' in lines[i] and i > start_idx:
            end_idx = i
            break

    # 提取方法名和签名
    method_line = lines[line_idx]
    method_match = re.search(r"def\s+(\w+)\s*\((.*?)\)", method_line, re.DOTALL)

    if not method_match:
        return 0

    method_name = method_match.group(1)
    params_str = method_match.group(2)

    # 解析参数
    params = parse_params(params_str)

    # 生成完整的文档注释
    indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    indent_str = " " * indent

    new_docstring = generate_google_docstring(method_name, params, indent_str)

    # 替换原有文档注释
    lines[start_idx : end_idx + 1] = [new_docstring]

    return 1


def add_docstring(lines: list[str], line_num: int, desc: str) -> int:
    """添加文档注释"""
    line_idx = line_num - 1
    if line_idx >= len(lines):
        return 0

    # 提取方法名和签名
    method_line = lines[line_idx]
    method_match = re.search(r"def\s+(\w+)\s*\((.*?)\)", method_line, re.DOTALL)

    if not method_match:
        return 0

    method_name = method_match.group(1)
    params_str = method_match.group(2)

    # 解析参数
    params = parse_params(params_str)

    # 生成文档注释
    indent = len(method_line) - len(method_line.lstrip())
    indent_str = " " * indent

    docstring = generate_google_docstring(method_name, params, indent_str)

    # 插入文档注释
    lines.insert(line_idx + 1, docstring)

    return 1


def parse_params(params_str: str) -> list[tuple[str, str | None]]:
    """解析函数参数"""
    params = []
    if not params_str.strip():
        return params

    # 简单解析，不处理嵌套括号
    param_list = params_str.split(",")
    for param in param_list:
        param = param.strip()
        if not param or param in ("self", "cls"):
            continue

        # 分离参数名和默认值
        if "=" in param:
            parts = param.split("=")
            param_name = parts[0].split(":")[0].strip()
            default_value = parts[1].strip() if len(parts) > 1 else None
        else:
            param_name = param.split(":")[0].strip()
            default_value = None

        # 移除类型注释
        param_name = param_name.split(":")[0].strip()

        if param_name:
            params.append((param_name, default_value))

    return params


def generate_google_docstring(
    method_name: str, params: list[tuple[str, str | None]], indent: str
) -> str:
    """生成 Google 风格的文档注释"""
    # 根据方法名生成简短描述
    desc = generate_method_description(method_name)

    lines = [f'{indent}"""{desc}']

    # 添加 Args 部分
    if params:
        lines.append(f"{indent}")
        lines.append(f"{indent}Args:")
        for param_name, default_value in params:
            param_desc = get_param_description(param_name)
            if default_value:
                lines.append(f"{indent}    {param_name}: {param_desc} Defaults to {default_value}.")
            else:
                lines.append(f"{indent}    {param_name}: {param_desc}")

    # 添加 Returns 部分
    return_desc = get_return_description(method_name)
    if return_desc:
        lines.append(f"{indent}")
        lines.append(f"{indent}Returns:")
        lines.append(f"{indent}    {return_desc}")

    lines.append(f'{indent}"""')

    return "\n".join(lines)


def generate_method_description(method_name: str) -> str:
    """根据方法名生成描述"""
    descriptions = {
        "__init__": "Initialize the instance.",
        "get_ticker": "Get ticker data for a symbol.",
        "get_tick": "Get tick data for a symbol.",
        "get_depth": "Get order book depth data.",
        "get_kline": "Get K-line/candlestick data.",
        "get_balance": "Get account balance.",
        "get_account": "Get account information.",
        "make_order": "Create a new order.",
        "cancel_order": "Cancel an existing order.",
        "query_order": "Query order details.",
        "get_open_orders": "Get all open orders.",
        "get_deals": "Get recent deals/trades.",
        "get_exchange_info": "Get exchange information.",
        "get_server_time": "Get server time.",
        "get_symbols": "Get available trading symbols.",
        "request": "Make an HTTP request.",
        "async_request": "Make an asynchronous HTTP request.",
        "push_data_to_queue": "Push data to the queue.",
        "connect": "Establish connection.",
        "disconnect": "Close connection.",
        "is_connected": "Check if connected.",
        "setup_totp": "Setup TOTP authentication.",
        "verify_totp": "Verify TOTP token.",
        "setup_hotp": "Setup HOTP authentication.",
        "verify_hotp": "Verify HOTP token.",
        "generate_webauthn_registration_options": "Generate WebAuthn registration options.",
        "verify_webauthn_registration": "Verify WebAuthn registration.",
        "generate_webauthn_authentication_options": "Generate WebAuthn authentication options.",
        "verify_webauthn_authentication": "Verify WebAuthn authentication.",
        "disable_mfa": "Disable MFA for a user.",
        "get_mfa_status": "Get MFA status for a user.",
        "regenerate_backup_codes": "Regenerate backup codes.",
        "is_mfa_enabled": "Check if MFA is enabled.",
        "get_available_mfa_methods": "Get available MFA methods.",
    }

    # 转换方法名到描述
    if method_name in descriptions:
        return descriptions[method_name]

    # 默认描述
    words = method_name.replace("_", " ").split()
    return f"{' '.join(word.capitalize() for word in words)}."


def get_param_description(param_name: str) -> str:
    """获取参数描述"""
    descriptions = {
        "user_id": "Unique identifier for the user.",
        "symbol": "Trading symbol/pair.",
        "data_queue": "Queue for data transmission.",
        "kwargs": "Additional keyword arguments.",
        "args": "Additional positional arguments.",
        "extra_data": "Additional data to include.",
        "timeout": "Request timeout in seconds.",
        "path": "API endpoint path.",
        "params": "Query parameters.",
        "body": "Request body data.",
        "vol": "Order volume/quantity.",
        "volume": "Order volume/quantity.",
        "price": "Order price.",
        "order_type": "Type of order (limit, market, etc.).",
        "offset": "Order offset (open, close).",
        "client_order_id": "Client-specified order ID.",
        "order_id": "Exchange order ID.",
        "count": "Number of items to retrieve.",
        "period": "Time period/interval.",
        "limit": "Maximum number of items.",
        "account_name": "Account name for display.",
        "token": "Authentication token.",
        "backup_code": "Backup code for MFA.",
        "secret": "Secret key.",
        "data": "Data dictionary.",
        "request_data": "Request data dictionary.",
        "future": "Async future object.",
        "funding_rate_info": "Funding rate information.",
        "symbol_name": "Symbol name.",
        "asset_type": "Asset type (spot, futures, etc.).",
        "has_been_json_encoded": "Whether data is JSON encoded.",
        "attributes": "Additional attributes.",
        "updates": "Update dictionary.",
        "context_attrs": "Context attributes.",
        "details": "Additional details.",
        "encryption_manager": "Encryption manager instance.",
        "audit_logger": "Audit logger instance.",
        "bt_api_instance": "bt_api instance.",
        "func": "Function to decorate.",
        "is_sign": "Whether to sign the request.",
        "amount": "Amount.",
        "post_only": "Post-only flag.",
    }
    return descriptions.get(param_name, f"The {param_name.replace('_', ' ')}.")


def get_return_description(method_name: str) -> str:
    """获取返回值描述"""
    if method_name.startswith("get_") or method_name in ["request", "async_request"]:
        return "Dictionary containing the response data."
    elif method_name.startswith("verify_"):
        return "True if verification succeeds, False otherwise."
    elif method_name.startswith("is_"):
        return "True if condition is met, False otherwise."
    elif method_name.startswith("has_"):
        return "True if has the item, False otherwise."
    elif method_name.startswith("check_"):
        return "True if check passes, False otherwise."
    elif method_name in ["cancel_order", "disable_mfa"]:
        return "True if operation succeeds, False otherwise."
    elif method_name in ["connect", "disconnect"]:
        return "None."
    elif method_name == "push_data_to_queue":
        return "None."
    elif method_name == "make_order":
        return "Order ID if successful, None otherwise."
    elif method_name == "__init__":
        return ""
    else:
        return "Result of the operation."


def infer_type(param_name: str) -> str:
    """推断参数类型"""
    type_hints = {
        "kwargs": "Any",
        "args": "Any",
        "user_id": "str",
        "token": "str",
        "symbol": "str",
        "data": "dict[str, Any]",
        "config": "dict[str, Any]",
        "attributes": "dict[str, Any]",
        "updates": "dict[str, Any]",
        "context_attrs": "dict[str, Any]",
        "details": "dict[str, Any]",
        "encryption_manager": "EncryptionManager | None",
        "audit_logger": "AuditLogger | None",
        "bt_api_instance": "Any",
        "func": "Callable",
        "future": "asyncio.Future",
        "request_data": "dict[str, Any]",
        "funding_rate_info": "dict[str, Any]",
        "symbol_name": "str",
        "asset_type": "str",
        "has_been_json_encoded": "bool",
        "data_queue": "str | None",
        "path": "str",
        "params": "dict[str, Any] | None",
        "body": "dict[str, Any] | None",
        "extra_data": "dict[str, Any] | None",
        "timeout": "int",
        "vol": "float",
        "volume": "float",
        "price": "float",
        "order_type": "str",
        "offset": "str",
        "client_order_id": "str | None",
        "order_id": "str",
        "count": "int",
        "period": "str",
        "limit": "int",
        "is_sign": "bool",
        "amount": "float",
        "post_only": "bool",
        "account_name": "str | None",
        "backup_code": "str | None",
        "secret": "str | None",
    }
    return type_hints.get(param_name, "Any")


def main():
    """主函数"""
    task_file = Path("scripts/agent_tasks/agent_10_tasks.json")
    if not task_file.exists():
        print("任务文件不存在")
        return

    tasks = json.loads(task_file.read_text(encoding="utf-8"))

    print(f"开始批量改进 {len(tasks)} 个文件...\n")

    success_count = 0
    for i, (file_path, issues) in enumerate(tasks.items(), 1):
        print(f"[{i}/{len(tasks)}] ", end="")
        if improve_file(file_path, issues):
            success_count += 1

    print(f"\n完成! 成功改进 {success_count}/{len(tasks)} 个文件")


if __name__ == "__main__":
    main()
