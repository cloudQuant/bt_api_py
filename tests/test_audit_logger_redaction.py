"""AuditLogger 脱敏 + 真原子写 + 文件权限测试（E-02）。"""

from __future__ import annotations

import json
import os

import pytest

from bt_api_py.security_compliance.core.audit_logger import (
    AuditEvent,
    AuditLogger,
    EventType,
)


def test_audit_details_are_redacted(tmp_path) -> None:
    """details 中含 key/secret/token/password 的键值必须被掩码。"""
    log_file = tmp_path / "audit.log"
    logger = AuditLogger(log_file)
    event = AuditEvent(
        event_type=EventType.USER_LOGIN,
        user_id="user123",
        details={
            "api_key": "secret123",
            "amount": 1.0,
            "nested": {"access_token": "tok123", "note": "visible"},
        },
    )
    logger.log_event(event)

    content = log_file.read_text(encoding="utf-8")
    assert "secret123" not in content
    assert "tok123" not in content
    # 非敏感字段应保留
    assert "visible" in content
    # 掩码占位符存在
    assert "REDACTED" in content


def test_audit_write_leaves_no_temp_files(tmp_path) -> None:
    """原子写后无 .tmp 中间文件残留，最终文件是完整 JSON。"""
    log_file = tmp_path / "audit.log"
    logger = AuditLogger(log_file)
    logger.log_event(AuditEvent(event_type=EventType.USER_LOGIN))
    logger.log_event(AuditEvent(event_type=EventType.USER_LOGOUT))

    leftovers = [p for p in tmp_path.iterdir() if p.name != "audit.log"]
    assert leftovers == []
    # 最终文件每行都是完整 JSON
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)


def test_audit_file_permissions_0600(tmp_path) -> None:
    """审计落盘文件权限必须为 0600。"""
    log_file = tmp_path / "audit.log"
    logger = AuditLogger(log_file)
    logger.log_event(AuditEvent(event_type=EventType.USER_LOGIN))

    mode = os.stat(log_file).st_mode & 0o777
    assert mode == 0o600


def test_encryption_key_rejected(tmp_path) -> None:
    """未实现加密时传入 encryption_key 必须明确报错而非静默明文。"""
    with pytest.raises(NotImplementedError):
        AuditLogger(tmp_path / "audit.log", encryption_key="not-supported")
