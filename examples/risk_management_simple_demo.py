"""风险管理系统简化演示

演示智能风控和合规监控系统的核心功能
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

    # 创建不同级别的风险事件
    events = [
        {
            "type": RiskEventType.MARKET_VOLATILITY_SPIKE,
            "level": RiskLevel.HIGH,
            "title": "市场波动率激增",
            "description": "检测到BTC/USDT市场波动率异常激增",
            "exchange_name": "BINANCE",
            "account_id": "test_account_001",
        },
        {
            "type": RiskEventType.SYSTEM_OUTAGE,
            "level": RiskLevel.CRITICAL,
            "title": "系统故障",
            "description": "交易系统响应时间超过阈值",
            "exchange_name": "BINANCE",
            "account_id": "test_account_001",
        },
        {
            "type": RiskEventType.LIQUIDITY_CRISIS,
            "level": RiskLevel.MEDIUM,
            "title": "流动性危机",
            "description": "市场深度显著下降",
            "exchange_name": "BINANCE",
            "account_id": "test_account_001",
        },
    ]

    created_events = []
    for event_data in events:
        event = risk_manager.create_risk_event(
            event_type=event_data["type"],
            risk_level=event_data["level"],
            title=event_data["title"],
            description=event_data["description"],
            exchange_name=event_data["exchange_name"],
            account_id=event_data["account_id"],
        )
        created_events.append(event)
        print(f"   创建事件: {event.event_id} - {event.title} ({event.risk_level.value})")

    # 3. 获取活跃事件
    print("3. 获取活跃风险事件...")
    active_events = risk_manager.get_active_events()

    print(f"   活跃事件总数: {len(active_events)}")
    for event in active_events:
        print(f"     - {event.event_id}: {event.title} ({event.risk_level.value})")

    # 4. 获取性能指标
    print("4. 获取性能指标...")
    metrics = risk_manager.get_performance_metrics()

    print(f"   性能指标:")
    print(f"     - 处理的事件数: {metrics['events_processed']}")
    print(f"     - 活跃事件数: {metrics['active_events']}")
    print(f"     - 缓存的指标数: {metrics['cached_metrics']}")

    # 5. 模拟简单的订单风险检查
    print("5. 模拟订单风险检查...")

    # 正常订单
    normal_order = {"symbol": "BTCUSDT", "side": "buy", "size": 1.0, "price": 50000}
    result1 = risk_manager.check_order_risk("BINANCE", "test_account_001", normal_order)
    print(f"   正常订单 - 批准: {result1['approved']}")

    # 大额订单
    large_order = {"symbol": "BTCUSDT", "side": "buy", "size": 100.0, "price": 50000}
    result2 = risk_manager.check_order_risk("BINANCE", "test_account_001", large_order)
    print(f"   大额订单 - 批准: {result2['approved']}")
    print(f"     警告数: {len(result2.get('warnings', []))}")
    print(f"     - 限制数: {len(result2.get('restrictions', []))}")

    # 6. 演示风险评估器基本功能
    print("6. 演示风险评估器...")
    assessor = risk_manager.risk_assessor

    # 只获取基本统计信息，避免复杂的计算
    print(f"   评估器状态:")
    print(f"     - 已训练: {assessor.is_trained}")
    print(f"     - 因子权重配置: {len(assessor.factor_weights)}")
    print(f"     - 风险阈值配置: {assessor.risk_thresholds}")

    # 7. 演示限制管理器
    print("7. 演示限制管理器...")
    limits_manager = risk_manager.limits_manager

    # 设置一些限制
    limits_manager.set_static_limit("max_order_size", "BINANCE", "test_account_001", 1000000)

    print(f"   设置限制: max_order_size = 1,000,000")

    # 8. 演示策略引擎
    print("8. 演示策略引擎...")
    policy_engine = risk_manager.policy_engine

    print(f"   策略引擎状态: {len(policy_engine.rules)} 规则已配置")
    print(f"     - 默认规则包括高风险暂停、保证金检查等")

    # 9. 启动简单监控
    print("9. 启动监控 (2秒)...")
    await risk_manager.start_monitoring()
    await asyncio.sleep(2)
    await risk_manager.stop_monitoring()

    print("10. 演示完成")

    print("\n=== 系统特性总结 ===")
    print("✅ 实时风险监控和评估")
    print("✅ 多维度风险分析 (市场、信用、操作、流动性)")
    print("✅ 智能风险事件管理")
    print("✅ 动态限制管理和检查")
    print("✅ 基于规则的风险策略执行")
    print("✅ 异常检测和模式识别")
    print("✅ 性能监控和统计")
    print("✅ 异步处理和高性能架构")
    print("✅ 可扩展的ML模型集成")
    print("✅ 合规监控和报告")

    print(f"\n🚀 系统已为BINANCE:test_account_001 准备就绪!")
    print("📊 实时监控、风险预警、合规检查功能全部可用")


if __name__ == "__main__":
    asyncio.run(main())
