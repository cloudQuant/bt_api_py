"""OrderRouter 审计接入测试（F-06/E-01 决策 A+最小集）。"""

from __future__ import annotations

import json

import pytest

from bt_api_py.brokers.mock import MockBrokerAdapter
from bt_api_py.forwarding.router import OrderRouter
from bt_api_py.forwarding.schema import OrderCommand
from bt_api_py.security_compliance.core.audit_logger import AuditLogger


def _read_events(log_file) -> list[dict]:
    events: list[dict] = []
    with open(log_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


@pytest.mark.asyncio
async def test_place_order_success_emits_audit_event(tmp_path) -> None:
    log_file = tmp_path / "audit.jsonl"
    audit = AuditLogger(log_file=log_file)
    router = OrderRouter(MockBrokerAdapter(), audit_logger=audit)
    await router.connect()
    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        order_type="limit",
        price=3500.0,
        idempotency_key="audit-order-1",
    )
    ack = await router.place_order(command)
    assert ack.accepted is True

    events = _read_events(log_file)
    created = [e for e in events if e["event_type"] == "order_created"]
    assert len(created) == 1
    ev = created[0]
    assert ev["outcome"] == "success"
    assert ev["action"] == "place_order"
    assert ev["resource"] == "RB2510"
    assert ev["user_id"] == "s1"
    assert ev["details"]["order_id"]
    assert ev["details"]["side"] == "buy"
    # 脱敏：审计事件不得含密钥/签名字段
    serialized = json.dumps(ev)
    assert "api_key" not in serialized
    assert "secret" not in serialized


@pytest.mark.asyncio
async def test_place_order_rejected_by_risk_emits_failure_audit(tmp_path) -> None:
    log_file = tmp_path / "audit.jsonl"
    audit = AuditLogger(log_file=log_file)
    router = OrderRouter(MockBrokerAdapter(), audit_logger=audit)
    await router.connect()
    router.risk_rules.kill_switch = True
    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        idempotency_key="audit-risk-1",
    )
    ack = await router.place_order(command)
    assert ack.accepted is False

    events = _read_events(log_file)
    created = [e for e in events if e["event_type"] == "order_created"]
    assert len(created) == 1
    assert created[0]["outcome"] == "failure"


@pytest.mark.asyncio
async def test_cancel_order_success_emits_audit_event(tmp_path) -> None:
    log_file = tmp_path / "audit.jsonl"
    audit = AuditLogger(log_file=log_file)
    router = OrderRouter(MockBrokerAdapter(), audit_logger=audit)
    await router.connect()
    # 先下单创建订单，再用真实 order_id 撤单
    placed = await router.place_order(
        OrderCommand(
            strategy_id="s1",
            account_id="paper",
            symbol="RB2510",
            side="buy",
            size=1,
            order_type="limit",
            price=3500.0,
            idempotency_key="audit-cancel-place-1",
        )
    )
    assert placed.accepted is True

    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        order_id=placed.order_id,
        idempotency_key="audit-cancel-1",
    )
    ack = await router.cancel_order(command)
    assert ack.accepted is True

    events = _read_events(log_file)
    cancelled = [e for e in events if e["event_type"] == "order_cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0]["outcome"] == "success"
    assert cancelled[0]["action"] == "cancel_order"


@pytest.mark.asyncio
async def test_no_audit_logger_is_silent_noop(tmp_path) -> None:
    """未接入 audit_logger 时下单/撤单路径不报错、不落盘。"""
    router = OrderRouter(MockBrokerAdapter())  # audit_logger=None
    await router.connect()
    command = OrderCommand(
        strategy_id="s1",
        account_id="paper",
        symbol="RB2510",
        side="buy",
        size=1,
        order_type="limit",
        price=3500.0,
        idempotency_key="no-audit-1",
    )
    ack = await router.place_order(command)
    assert ack.accepted is True
