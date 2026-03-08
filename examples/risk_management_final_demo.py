"""风险管理系统核心演示

只展示基本功能，避免复杂错误
"""

import asyncio
from bt_api_py.risk_management.core.risk_manager import RiskManager
from bt_api_py.risk_management.containers.risk_events import RiskEvent, RiskLevel, RiskEventType


async def main():
    """主函数 - 演示风险管理系统"""
    print("=== bt_api_py 智能风控和合规监控系统演示 ===\n")

    # 1. 初始化风险管理器
    print("1. 初始化风险管理器...")
    risk_manager = RiskManager()

    # 2. 创建风险事件
    print("2. 创建风险事件...")

    event = risk_manager.create_risk_event(
        event_type=RiskEventType.MARKET_VOLATILITY_SPIKE,
        risk_level=RiskLevel.HIGH,
        title="市场波动率激增",
        description="检测到异常市场波动",
        exchange_name="BINANCE",
        account_id="demo_account",
    )

    print(f"   创建事件: {event.event_id}")
    print(f"   事件类型: {event.event_type}")
    print(f"   风险级别: {event.risk_level.value}")

    # 3. 模拟订单风险检查
    print("3. 模拟订单风险检查...")

    # 先获取或创建风险指标
    try:
        risk_metrics = risk_manager.get_risk_metrics("BINANCE", "demo_account")
        if risk_metrics is None:
            # 如果没有指标，创建一个默认的
            from bt_api_py.risk_management.containers.risk_metrics import RiskMetrics

            risk_metrics = RiskMetrics(
                {
                    "exchange_name": "BINANCE",
                    "account_id": "demo_account",
                    "market_risk": {
                        "value_at_risk_1d": 10000.0,
                    },
                    "credit_risk": {
                        "credit_score": 750,
                    },
                    "operational_risk": {
                        "system_health_score": 0.9,
                    },
                    "liquidity_risk": {
                        "liquidity_score": 0.8,
                    },
                    "compliance_risk": {
                        "compliance_score": 0.95,
                    },
                }
            )
    except Exception as e:
        print(f"   获取风险指标时出错: {e}")
        risk_metrics = None

    # 然后检查订单风险
    result = risk_manager.check_order_risk(
        exchange_name="BINANCE",
        account_id="demo_account",
        order_data={
            "symbol": "BTCUSDT",
            "side": "buy",
            "size": 1.0,
            "price": 50000,
        },
        current_metrics=risk_metrics,
    )

    print(f"   订单检查结果:")
    print(f"     - 批准: {result['approved']}")
    print(f"     - 警告数: {len(result.get('warnings', []))}")
    print(f"     - 限制数: {len(result.get('restrictions', []))}")
    print(f"     - 缓解措施需求: {result.get('mitigation_required', False)}")

    # 4. 获取活跃事件
    print("4. 获取活跃风险事件...")
    active_events = risk_manager.get_active_events("BINANCE", "demo_account")
    print(f"   活跃事件数量: {len(active_events)}")

    # 5. 获取性能指标
    print("5. 获取性能指标...")
    metrics = risk_manager.get_performance_metrics()
    print(f"   处理的事件数: {metrics['events_processed']}")
    print(f"     活跃事件数: {metrics['active_events']}")
    print(f"     平均处理时间: {metrics['average_processing_time_ms']:.2f}ms")

    # 6. 启动监控
    print("6. 启动监控 (2秒)...")
    await risk_manager.start_monitoring()
    await asyncio.sleep(2)
    await risk_manager.stop_monitoring()

    print("\n=== 系统功能演示完成 ===")
    print("✅ 风险事件管理")
    print("✅ 订单风险检查")
    print("✅ 实时监控")
    print("✅ 性能统计")
    print("✅ 异步处理")

    print(f"\n🚀 系统已为 BINANCE:demo_account 准备就绪!")


if __name__ == "__main__":
    asyncio.run(main())
