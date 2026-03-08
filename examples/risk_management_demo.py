"""风险管理系统集成示例

演示如何使用智能风控和合规监控系统
"""

import time
import asyncio
from decimal import Decimal

from bt_api_py.risk_management.core.risk_manager import RiskManager
from bt_api_py.risk_management.containers.risk_events import RiskEvent, RiskLevel, RiskEventType


async def main():
    """主函数 - 演示风险管理系统"""
    print("=== bt_api_py 智能风控和合规监控系统演示 ===\n")

    # 1. 初始化风险管理器
    print("1. 初始化风险管理器...")
    risk_manager = RiskManager()

    # 2. 模拟账户数据
    print("2. 设置测试账户数据...")
    exchange_name = "BINANCE"
    account_id = "test_account_001"

    # 3. 创建风险事件
    print("3. 创建风险事件...")

    # 创建一个高风险事件
    high_risk_event = risk_manager.create_risk_event(
        event_type=RiskEventType.MARKET_VOLATILITY_SPIKE,
        risk_level=RiskLevel.HIGH,
        title="市场波动率激增",
        description="检测到BTC/USDT市场波动率异常激增，可能是市场操纵或重大新闻事件",
        exchange_name=exchange_name,
        account_id=account_id,
        impact_assessment="可能影响当前仓位价值15-25%",
        severity_score=0.8,
        urgency_score=0.7,
        likelihood_score=0.6,
        affected_symbols=["BTCUSDT", "ETHUSDT"],
        affected_accounts=[account_id],
        detection_method="statistical_analysis",
        source_system="risk_monitoring",
    )

    print(f"   创建高风险事件: {high_risk_event.event_id}")
    print(f"   风险级别: {high_risk_event.risk_level}")
    print(f"   事件类型: {high_risk_event.event_type}")

    # 创建一个严重风险事件
    critical_risk_event = risk_manager.create_risk_event(
        event_type=RiskEventType.SYSTEM_OUTAGE,
        risk_level=RiskLevel.CRITICAL,
        title="系统性能严重下降",
        description="交易系统响应时间超过阈值，可能影响交易执行",
        exchange_name=exchange_name,
        account_id=account_id,
        severity_score=0.9,
        urgency_score=0.9,
        likelihood_score=0.8,
        detection_method="real_time_monitoring",
    )

    print(f"   创建严重风险事件: {critical_risk_event.event_id}")
    print(f"   风险级别: {critical_risk_event.risk_level}")

    # 4. 模拟订单风险检查
    print("4. 模拟订单风险检查...")

    # 低风险订单
    low_risk_order = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "size": 1.0,
        "price": 50000,
        "type": "limit",
    }

    order_check_result = risk_manager.check_order_risk(
        exchange_name=exchange_name, account_id=account_id, order_data=low_risk_order
    )

    print(f"   低风险订单检查结果:")
    print(f"     - 批准: {order_check_result['approved']}")
    print(f"     - 风险级别: {order_check_result.get('risk_level', 'UNKNOWN')}")
    print(f"     - 风险评分: {order_check_result.get('risk_score', 0)}")
    print(f"     - 警告数量: {len(order_check_result.get('warnings', []))}")

    # 高风险订单 (大额)
    high_risk_order = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "size": 100.0,  # 100 BTC = 500万
        "price": 50000,
        "type": "limit",
    }

    high_risk_result = risk_manager.check_order_risk(
        exchange_name=exchange_name, account_id=account_id, order_data=high_risk_order
    )

    print(f"   高风险订单检查结果:")
    print(f"     - 批准: {high_risk_result['approved']}")
    print(f"     - 需要缓解措施: {high_risk_result.get('mitigation_required', False)}")
    print(f"     - 限制数量: {len(high_risk_result.get('restrictions', []))}")

    # 5. 获取活跃风险事件
    print("5. 获取活跃风险事件...")
    active_events = risk_manager.get_active_events(
        exchange_name=exchange_name, account_id=account_id
    )

    print(f"   活跃风险事件数量: {len(active_events)}")
    for event in active_events:
        print(f"     - {event.event_id}: {event.title} ({event.risk_level})")

    # 6. 获取性能指标
    print("6. 获取性能指标...")
    performance_metrics = risk_manager.get_performance_metrics()

    print(f"   性能指标:")
    print(f"     - 处理的事件数: {performance_metrics['events_processed']}")
    print(f"     - 风险评估数: {performance_metrics['risk_assessments']}")
    print(f"     - 违规检测数: {performance_metrics['violations_detected']}")
    print(f"     - 平均处理时间: {performance_metrics['average_processing_time_ms']:.2f}ms")
    print(f"     - 活跃事件数: {performance_metrics['active_events']}")
    print(f"     - 缓存的指标数: {performance_metrics['cached_metrics']}")

    # 7. 启动实时监控
    print("7. 启动实时监控...")
    await risk_manager.start_monitoring()

    print("   实时监控已启动，运行3秒...")

    # 模拟一些活动
    for i in range(3):
        await asyncio.sleep(1)
        print(f"   监控中... ({i + 1}/3)")

    # 停止监控
    print("8. 停止实时监控...")
    await risk_manager.stop_monitoring()
    print("   实时监控已停止")

    # 9. 演示风险评估器
    print("9. 演示风险评估器...")
    risk_assessor = risk_manager.risk_assessor

    # 模拟风险指标数据
    mock_risk_metrics_data = {
        "exchange_name": exchange_name,
        "account_id": account_id,
        "market_risk": {
            "value_at_risk_1d": 10000,
            "value_at_risk_10d": 30000,
            "expected_shortfall": 12000,
            "volatility": 0.3,
            "beta": 1.2,
            "correlation_matrix": {},
            "position_concentration": {"herfindahl_index": 0.4},
        },
        "credit_risk": {
            "credit_score": 750,
            "probability_of_default": 0.01,
            "credit_utilization": 0.4,
        },
        "operational_risk": {
            "system_health_score": 0.9,
            "error_rate": 0.01,
            "system_availability": 0.99,
        },
        "liquidity_risk": {
            "liquidity_score": 0.8,
            "bid_ask_spread": 5,
            "market_depth": 1000000,
        },
        "compliance_risk": {
            "compliance_score": 0.95,
            "kyc_status": "VERIFIED",
            "aml_flags": [],
        },
    }

    from bt_api_py.risk_management.containers.risk_metrics import RiskMetrics

    mock_risk_metrics = RiskMetrics(mock_risk_metrics_data)

    assessment_result = risk_assessor.assess_risk(mock_risk_metrics)

    print(f"   风险评估结果:")
    print(f"     - 风险评分: {assessment_result.score}")
    print(f"     - 风险级别: {assessment_result.level}")
    print(f"     - 置信度: {assessment_result.confidence}")
    print(f"     - 建议措施数量: {len(assessment_result.recommendations)}")

    for recommendation in assessment_result.recommendations:
        print(f"       * {recommendation}")

    # 10. 获取风险评估统计
    print("10. 获取风险评估统计...")
    risk_stats = risk_assessor.get_risk_statistics()

    print(f"   风险评估统计:")
    print(f"     - 总评估次数: {risk_stats['assessment_stats']['total_assessments']}")
    print(f"     - 平均风险评分: {risk_stats['assessment_stats']['average_score']}")
    print(f"     - 评分分布: {risk_stats['assessment_stats']['score_distribution']}")

    print("\n=== 演示完成 ===")
    print("\n智能风控和合规监控系统的主要特性:")
    print("✓ 实时风险监控和评估")
    print("✓ 多维度风险分析 (市场、信用、操作、流动性、合规)")
    print("✓ 机器学习驱动的风险预测")
    print("✓ 智能限制管理和动态调整")
    print("✅ 事件驱动的风险策略执行")
    print("✅ 异常检测和模式识别")
    print("✅ 合规监控和监管报告")
    print("✅ 高性能和可扩展架构")

    print(f"\n系统已为 {exchange_name} 的账户 {account_id} 准备就绪!")
    print("可以开始实时风险监控和合规管理。")


if __name__ == "__main__":
    asyncio.run(main())
