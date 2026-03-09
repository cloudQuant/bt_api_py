#!/usr/bin/env python3
"""批量改进代码质量 - 精确版本，只处理任务文件中的文件"""

import json
import re
from pathlib import Path
from typing import Any


def add_return_type_to_init(code: str) -> str:
    """为 __init__ 方法添加返回类型 -> None"""
    lines = code.split("\n")
    modified = False

    for i, line in enumerate(lines):
        # 查找 __init__ 方法定义
        if "def __init__(" in line and ") -> " not in line:
            # 找到方法定义的结束（可能跨多行）
            if "):" in line:
                lines[i] = line.replace("):", ") -> None:")
                modified = True
            elif ") :" in line:
                lines[i] = line.replace(") :", ") -> None :")
                modified = True
            else:
                # 多行定义，找到最后一行
                for j in range(i + 1, min(i + 10, len(lines))):
                    if "):" in lines[j]:
                        lines[j] = lines[j].replace("):", ") -> None:")
                        modified = True
                        break
                    elif ") :" in lines[j]:
                        lines[j] = lines[j].replace(") :", ") -> None :")
                        modified = True
                        break

    return "\n".join(lines) if modified else code


def add_type_hints_to_params(code: str, params: list[tuple[str, str]]) -> str:
    """为参数添加类型注释"""
    for param_name, param_type in params:
        # 使用正则表达式匹配参数
        # 匹配: param_name= 或 param_name, 或 param_name): 等
        # 但不匹配已有类型注释的: param_name:
        pattern = rf"\b{re.escape(param_name)}\b(?!\s*:)"

        # 在方法定义行中替换
        def replace_param(match):
            full_line = code[max(0, match.start() - 100) : match.end() + 100]
            # 检查是否在方法定义中
            if "def " in full_line:
                return f"{param_name}: {param_type}"
            return match.group(0)

        code = re.sub(pattern, replace_param, code, count=1)

    return code


def enhance_or_add_docstring(code: str, method_name: str, line_num: int) -> str:
    """完善或添加文档注释"""
    lines = code.split("\n")
    line_idx = line_num - 1

    if line_idx >= len(lines):
        return code

    # 找到方法定义
    method_line = lines[line_idx]
    if "def " not in method_line:
        return code

    # 提取方法签名
    method_match = re.search(r"def\s+(\w+)\s*\((.*?)\)", method_line, re.DOTALL)
    if not method_match:
        # 可能是多行定义，尝试读取更多行
        full_def = "\n".join(lines[line_idx : min(line_idx + 5, len(lines))])
        method_match = re.search(r"def\s+(\w+)\s*\((.*?)\)", full_def, re.DOTALL)
        if not method_match:
            return code

    method_name_found = method_match.group(1)
    params_str = method_match.group(2)

    # 解析参数
    params = parse_method_params(params_str)

    # 找到方法定义的最后一行
    def_end_line = line_idx
    for i in range(line_idx, min(line_idx + 10, len(lines))):
        if "):" in lines[i] or ") ->" in lines[i]:
            def_end_line = i
            break

    # 检查下一行是否已有文档注释
    next_line_idx = def_end_line + 1
    if next_line_idx < len(lines) and '"""' in lines[next_line_idx]:
        # 已有文档注释，需要完善
        return enhance_existing_docstring(lines, next_line_idx, method_name_found, params)
    else:
        # 添加新的文档注释
        return add_new_docstring(lines, def_end_line, method_name_found, params)


def parse_method_params(params_str: str) -> list[tuple[str, str | None]]:
    """解析方法参数"""
    params = []
    if not params_str.strip():
        return params

    # 简单解析
    param_list = params_str.split(",")
    for param in param_list:
        param = param.strip()
        if not param or param in ("self", "cls", "*args", "**kwargs"):
            continue

        # 分离参数名和默认值
        if "=" in param:
            parts = param.split("=", 1)
            param_name = parts[0].split(":")[0].strip()
            default_value = parts[1].strip() if len(parts) > 1 else None
        else:
            param_name = param.split(":")[0].strip()
            default_value = None

        if param_name:
            params.append((param_name, default_value))

    return params


def enhance_existing_docstring(
    lines: list[str], start_idx: int, method_name: str, params: list[tuple[str, str | None]]
) -> str:
    """完善已有的文档注释"""
    # 找到文档注释的结束位置
    end_idx = start_idx
    quote_count = lines[start_idx].count('"""')

    if quote_count >= 2:
        # 单行文档注释
        end_idx = start_idx
    else:
        # 多行文档注释
        for i in range(start_idx + 1, min(start_idx + 20, len(lines))):
            if '"""' in lines[i]:
                end_idx = i
                break

    # 获取缩进
    indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    indent_str = " " * indent

    # 生成完整的 Google 风格文档注释
    new_docstring_lines = generate_google_docstring_lines(method_name, params, indent_str)

    # 替换原有文档注释
    lines[start_idx : end_idx + 1] = new_docstring_lines

    return "\n".join(lines)


def add_new_docstring(
    lines: list[str], def_end_line: int, method_name: str, params: list[tuple[str, str | None]]
) -> str:
    """添加新的文档注释"""
    # 获取缩进
    indent = len(lines[def_end_line]) - len(lines[def_end_line].lstrip())
    indent_str = " " * indent

    # 生成文档注释
    docstring_lines = generate_google_docstring_lines(method_name, params, indent_str)

    # 在方法定义后插入文档注释
    for i, doc_line in enumerate(reversed(docstring_lines)):
        lines.insert(def_end_line + 1, doc_line)

    return "\n".join(lines)


def generate_google_docstring_lines(
    method_name: str, params: list[tuple[str, str | None]], indent: str
) -> list[str]:
    """生成 Google 风格的文档注释行"""
    lines = []

    # 简短描述
    desc = get_method_description(method_name)
    lines.append(f'{indent}"""{desc}')

    # Args 部分
    if params:
        lines.append(f"{indent}")
        lines.append(f"{indent}Args:")
        for param_name, default_value in params:
            param_desc = get_param_description(param_name)
            if default_value:
                lines.append(f"{indent}    {param_name}: {param_desc} Defaults to {default_value}.")
            else:
                lines.append(f"{indent}    {param_name}: {param_desc}")

    # Returns 部分
    return_desc = get_return_description(method_name)
    if return_desc:
        lines.append(f"{indent}")
        lines.append(f"{indent}Returns:")
        lines.append(f"{indent}    {return_desc}")

    lines.append(f'{indent}"""')

    return lines


def get_method_description(method_name: str) -> str:
    """获取方法描述"""
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
        "init_data": "Initialize data from raw input.",
        "get_all_data": "Get all data as a dictionary.",
        "get_exchange_name": "Get exchange name.",
        "get_asset_type": "Get asset type.",
        "get_symbol_name": "Get symbol name.",
        "get_server_time": "Get server timestamp.",
        "get_local_update_time": "Get local update timestamp.",
        "get_funding_rate_symbol_name": "Get funding rate symbol name.",
        "get_pre_funding_rate": "Get previous funding rate.",
        "get_pre_funding_time": "Get previous funding time.",
        "get_next_funding_rate": "Get next funding rate.",
        "get_next_funding_time": "Get next funding time.",
        "get_max_funding_rate": "Get maximum funding rate.",
        "get_min_funding_rate": "Get minimum funding rate.",
        "get_current_funding_rate": "Get current funding rate.",
        "get_current_funding_time": "Get current funding time.",
        "get_settlement_funding_rate": "Get settlement funding rate.",
        "get_settlement_status": "Get settlement status.",
        "get_method": "Get HTTP method.",
        "get_symbol": "Get trading symbol.",
        "can_use_grant_type": "Check if grant type can be used.",
        "has_scope": "Check if has scope.",
        "is_expired": "Check if expired.",
        "is_valid": "Check if valid.",
        "get_expires_in": "Get expires in seconds.",
        "add_permission": "Add permission.",
        "remove_permission": "Remove permission.",
        "has_permission": "Check if has permission.",
        "add_role": "Add role.",
        "remove_role": "Remove role.",
        "has_role": "Check if has role.",
        "is_locked": "Check if locked.",
        "to_dict": "Convert to dictionary.",
        "create_user": "Create a new user.",
        "get_user": "Get user by ID.",
        "create_role": "Create a new role.",
        "get_role": "Get role by ID.",
        "assign_role": "Assign role to user.",
        "revoke_role": "Revoke role from user.",
        "check_permission": "Check user permission.",
        "require_permission": "Require permission decorator.",
        "create_session": "Create user session.",
        "validate_session": "Validate session.",
        "revoke_session": "Revoke session.",
        "lock_user": "Lock user account.",
        "unlock_user": "Unlock user account.",
        "record_login_attempt": "Record login attempt.",
        "get_user_permissions": "Get user permissions.",
        "audit_access": "Audit access attempt.",
        "verify_integrity": "Verify data integrity.",
        "from_dict": "Create instance from dictionary.",
        "log_event": "Log audit event.",
        "subscribe": "Subscribe to events.",
        "unsubscribe": "Unsubscribe from events.",
        "verify_log_integrity": "Verify log integrity.",
        "search_events": "Search audit events.",
        "get_compliance_report": "Get compliance report.",
        "cleanup_old_events": "Cleanup old events.",
        "run_compliance_check": "Run compliance check.",
        "generate_compliance_report": "Generate compliance report.",
        "create_key_manager": "Create key manager.",
        "get_encryption_manager": "Get encryption manager instance.",
        "initialize_encryption_manager": "Initialize encryption manager.",
        "generate_key": "Generate encryption key.",
        "get_key": "Get encryption key.",
        "rotate_key": "Rotate encryption key.",
        "delete_key": "Delete encryption key.",
        "encrypt": "Encrypt data.",
        "decrypt": "Decrypt data.",
        "rotate_keys": "Rotate all keys.",
        "generate_key_pair": "Generate key pair.",
        "encrypt_with_public_key": "Encrypt with public key.",
        "decrypt_with_private_key": "Decrypt with private key.",
        "create_identity": "Create identity.",
        "verify_password": "Verify password.",
        "authenticate_user": "Authenticate user.",
        "create_group": "Create group.",
        "add_user_to_group": "Add user to group.",
        "remove_user_from_group": "Remove user from group.",
        "get_user_groups": "Get user groups.",
        "get_group_members": "Get group members.",
        "update_identity": "Update identity.",
        "suspend_user": "Suspend user.",
        "activate_user": "Activate user.",
        "search_identities": "Search identities.",
        "get_identity_by_username": "Get identity by username.",
        "get_identity": "Get identity by ID.",
        "get_group": "Get group by ID.",
        "sync_with_external_provider": "Sync with external provider.",
        "generate_saml_metadata": "Generate SAML metadata.",
        "get_identity_summary": "Get identity summary.",
        "detect_failed_login": "Detect failed login.",
        "detect_suspicious_access_pattern": "Detect suspicious access pattern.",
        "detect_unauthorized_access_attempt": "Detect unauthorized access attempt.",
        "analyze_login_anomaly": "Analyze login anomaly.",
        "detect_data_exfiltration": "Detect data exfiltration.",
        "get_threat_summary": "Get threat summary.",
        "resolve_threat": "Resolve threat.",
        "auto_respond_to_threat": "Auto respond to threat.",
        "establish_baseline": "Establish baseline.",
        "is_baseline_anomaly": "Check if baseline anomaly.",
        "classify_data": "Classify data.",
        "mask_data": "Mask data.",
        "anonymize_data": "Anonymize data.",
        "encrypt_sensitive_data": "Encrypt sensitive data.",
        "decrypt_sensitive_data": "Decrypt sensitive data.",
        "register_data_subject": "Register data subject.",
        "record_consent": "Record consent.",
        "withdraw_consent": "Withdraw consent.",
        "request_right_to_be_forgotten": "Request right to be forgotten.",
        "process_data_deletion": "Process data deletion.",
        "check_retention_policies": "Check retention policies.",
        "generate_data_protection_report": "Generate data protection report.",
        "integrate_with_bt_api": "Integrate with bt_api.",
        "secure_create_feed": "Securely create feed.",
        "get_security_status": "Get security status.",
        "create_security_config_from_env": "Create security config from environment.",
        "get_security_framework": "Get security framework instance.",
        "initialize_security_framework": "Initialize security framework.",
        "create_alert": "Create alert.",
        "get_alerts": "Get alerts.",
        "acknowledge_alert": "Acknowledge alert.",
        "resolve_alert": "Resolve alert.",
        "add_alert_handler": "Add alert handler.",
        "get_monitoring_summary": "Get monitoring summary.",
        "get_ssl_context": "Get SSL context.",
        "validate_certificate": "Validate certificate.",
        "create_backup_config": "Create backup config.",
        "create_recovery_plan": "Create recovery plan.",
        "initiate_backup": "Initiate backup.",
        "initiate_recovery": "Initiate recovery.",
        "get_recovery_status": "Get recovery status.",
        "test_recovery_plan": "Test recovery plan.",
        "generate_recovery_report": "Generate recovery report.",
        "async_get_tick": "Asynchronously get tick data.",
        "async_get_ticker": "Asynchronously get ticker data.",
        "async_get_depth": "Asynchronously get depth data.",
        "async_get_kline": "Asynchronously get kline data.",
        "async_get_account": "Asynchronously get account info.",
        "async_get_balance": "Asynchronously get balance.",
        "async_make_order": "Asynchronously create order.",
        "get_trades": "Get trades data.",
        "get_mfa_provider": "Get MFA provider instance.",
        "initialize_mfa_provider": "Initialize MFA provider.",
        "log_audit_event": "Log audit event.",
        "get_audit_logger": "Get audit logger instance.",
        "initialize_audit_logger": "Initialize audit logger.",
        "register_client": "Register OAuth client.",
        "register_user": "Register OAuth user.",
        "generate_authorization_code": "Generate authorization code.",
        "validate_authorization_code": "Validate authorization code.",
        "generate_access_token": "Generate access token.",
        "validate_access_token": "Validate access token.",
        "refresh_access_token": "Refresh access token.",
        "revoke_token": "Revoke token.",
        "introspect_token": "Introspect token.",
        "generate_jwt_id_token": "Generate JWT ID token.",
        "validate_jwt": "Validate JWT token.",
        "get_client_info": "Get client info.",
        "get_user_info": "Get user info.",
        "cleanup_expired_tokens": "Cleanup expired tokens.",
        "api_key": "Get API key.",
        "api_secret": "Get API secret.",
        "async_callback": "Asynchronous callback handler.",
        "async_get_exchange_info": "Asynchronously get exchange info.",
        "get_all_tickers": "Get all tickers.",
        "async_get_all_tickers": "Asynchronously get all tickers.",
        "async_get_deals": "Asynchronously get deals.",
        "get_data": "Get data.",
        "async_get_data": "Asynchronously get data.",
    }

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
        "account_info": "Account information dictionary.",
        "grant_type": "OAuth grant type.",
        "scope": "OAuth scope.",
        "client_id": "OAuth client ID.",
        "client_secret": "OAuth client secret.",
        "redirect_uri": "OAuth redirect URI.",
        "code": "Authorization code.",
        "refresh_token": "Refresh token.",
        "access_token": "Access token.",
        "token_type": "Token type.",
        "expires_in": "Expiration time in seconds.",
        "username": "Username.",
        "password": "Password.",
        "role_id": "Role ID.",
        "role_name": "Role name.",
        "permission": "Permission string.",
        "session_id": "Session ID.",
        "attributes": "User attributes.",
        "event_type": "Event type.",
        "event_data": "Event data.",
        "key_id": "Key ID.",
        "key_type": "Key type.",
        "plaintext": "Plaintext data.",
        "ciphertext": "Ciphertext data.",
        "public_key": "Public key.",
        "private_key": "Private key.",
        "identity_id": "Identity ID.",
        "group_id": "Group ID.",
        "threat_id": "Threat ID.",
        "threat_type": "Threat type.",
        "threat_level": "Threat level.",
        "data_type": "Data type.",
        "data_content": "Data content.",
        "consent_type": "Consent type.",
        "retention_period": "Retention period.",
        "alert_id": "Alert ID.",
        "alert_type": "Alert type.",
        "severity": "Alert severity.",
        "certificate": "Certificate data.",
        "backup_id": "Backup ID.",
        "recovery_plan_id": "Recovery plan ID.",
    }
    return descriptions.get(param_name, f"The {param_name.replace('_', ' ')}.")


def get_return_description(method_name: str) -> str:
    """获取返回值描述"""
    if method_name.startswith("get_") or method_name in ["request", "async_request", "from_dict"]:
        return "Dictionary containing the response data."
    elif method_name.startswith("verify_"):
        return "True if verification succeeds, False otherwise."
    elif (
        method_name.startswith("is_")
        or method_name.startswith("has_")
        or method_name.startswith("can_")
    ):
        return "True if condition is met, False otherwise."
    elif method_name.startswith("check_"):
        return "True if check passes, False otherwise."
    elif method_name in ["cancel_order", "disable_mfa", "delete_key", "revoke_token"]:
        return "True if operation succeeds, False otherwise."
    elif method_name in [
        "connect",
        "disconnect",
        "subscribe",
        "unsubscribe",
        "push_data_to_queue",
        "log_event",
        "record_login_attempt",
        "cleanup_old_events",
    ]:
        return "None."
    elif method_name in ["make_order", "create_order"]:
        return "Order ID if successful, None otherwise."
    elif method_name == "__init__":
        return ""
    elif method_name in [
        "generate_key",
        "create_user",
        "create_role",
        "create_group",
        "create_identity",
        "create_session",
        "create_backup_config",
        "create_recovery_plan",
        "create_alert",
    ]:
        return "Created object or ID."
    elif method_name in ["encrypt", "encrypt_with_public_key"]:
        return "Encrypted data."
    elif method_name in ["decrypt", "decrypt_with_private_key"]:
        return "Decrypted data."
    elif method_name in ["generate_key_pair"]:
        return "Tuple of (private_key, public_key)."
    elif method_name in [
        "get_key",
        "get_user",
        "get_role",
        "get_group",
        "get_identity",
        "get_client_info",
        "get_user_info",
    ]:
        return "Object if found, None otherwise."
    elif method_name in ["to_dict", "get_all_data"]:
        return "Dictionary representation."
    else:
        return "Result of the operation."


def infer_param_type(param_name: str) -> str:
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
        "account_info": "dict[str, Any]",
        "grant_type": "str",
        "scope": "str",
        "client_id": "str",
        "client_secret": "str",
        "redirect_uri": "str",
        "code": "str",
        "refresh_token": "str",
        "access_token": "str",
        "token_type": "str",
        "expires_in": "int",
        "username": "str",
        "password": "str",
        "role_id": "str",
        "role_name": "str",
        "permission": "str",
        "session_id": "str",
        "event_type": "str",
        "event_data": "dict[str, Any]",
        "key_id": "str",
        "key_type": "str",
        "plaintext": "bytes",
        "ciphertext": "bytes",
        "public_key": "bytes",
        "private_key": "bytes",
        "identity_id": "str",
        "group_id": "str",
        "threat_id": "str",
        "threat_type": "str",
        "threat_level": "str",
        "data_type": "str",
        "data_content": "Any",
        "consent_type": "str",
        "retention_period": "int",
        "alert_id": "str",
        "alert_type": "str",
        "severity": "str",
        "certificate": "bytes",
        "backup_id": "str",
        "recovery_plan_id": "str",
    }
    return type_hints.get(param_name, "Any")


def process_file(file_path: str, issues: list[str]) -> bool:
    """处理单个文件的所有问题"""
    path = Path(file_path)
    if not path.exists():
        print(f"✗ 文件不存在: {file_path}")
        return False

    try:
        code = path.read_text(encoding="utf-8")
        original_code = code

        # 解析问题
        type_hint_issues = []
        docstring_issues = []

        for issue in issues:
            match = re.match(r"(TypeHint|Docstring): Line (\d+): (.+)", issue)
            if match:
                issue_type, line_num_str, desc = match.groups()
                line_num = int(line_num_str)
                if issue_type == "TypeHint":
                    type_hint_issues.append((line_num, desc))
                elif issue_type == "Docstring":
                    docstring_issues.append((line_num, desc))

        # 处理类型注释问题
        for line_num, desc in sorted(type_hint_issues, key=lambda x: x[0], reverse=True):
            if "missing return annotation" in desc:
                code = add_return_type_to_init(code)
            elif "missing annotation for param" in desc:
                param_match = re.search(r"param '(\w+)'", desc)
                if param_match:
                    param_name = param_match.group(1)
                    param_type = infer_param_type(param_name)
                    code = add_type_hints_to_params(code, [(param_name, param_type)])

        # 处理文档注释问题
        for line_num, desc in sorted(docstring_issues, key=lambda x: x[0], reverse=True):
            method_match = re.search(r"(\w+)\.", desc)
            if method_match:
                method_name = method_match.group(1)
                code = enhance_or_add_docstring(code, method_name, line_num)

        if code != original_code:
            path.write_text(code, encoding="utf-8")
            print(f"✓ {file_path}")
            return True
        else:
            print(f"○ {file_path} (无改动)")
            return False

    except Exception as e:
        print(f"✗ 处理失败 {file_path}: {e}")
        import traceback

        traceback.print_exc()
        return False


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
        if process_file(file_path, issues):
            success_count += 1

    print(f"\n完成! 成功改进 {success_count}/{len(tasks)} 个文件")


if __name__ == "__main__":
    main()
