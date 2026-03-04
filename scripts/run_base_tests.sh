#!/usr/bin/env bash
# run_base_tests.sh — 运行所有基础（非交易所网络）测试
#
# 用法：
#   bash scripts/run_base_tests.sh            # 正常运行
#   bash scripts/run_base_tests.sh -v         # 详细输出
#   bash scripts/run_base_tests.sh -n 4       # 4进程并行
#
# 覆盖范围：
#   tests/                      (基础测试：bt_api, event_bus, registry, auto_init_mixin…)
#   tests/containers/           (数据容器单元测试)
#   tests/functions/            (工具函数测试)
#   tests/feeds/test_*.py       (各交易所 mock/单元测试，排除 live_* 和 integration)
#   tests/test_ctp_feed.py      (CTP 基础测试)
#
# 排除范围：
#   tests/feeds/test_live_*.py  (需要真实 API 密钥/网络)
#   tests/integration/          (集成测试)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

EXTRA_ARGS=("$@")

echo "============================================================"
echo "  bt_api_py 基础测试套件"
echo "  项目目录: $PROJECT_ROOT"
echo "============================================================"

python -m pytest \
    tests/test_bt_api.py \
    tests/test_bt_api_unified.py \
    tests/test_event_bus.py \
    tests/test_registry.py \
    tests/test_registry_and_balance.py \
    tests/test_auto_init_mixin.py \
    tests/test_stage0_infrastructure.py \
    tests/test_stage1_exchange_integration.py \
    tests/test_ctp_feed.py \
    tests/containers/ \
    tests/functions/ \
    "tests/feeds/" \
    --ignore="tests/feeds/live_binance" \
    --ignore="tests/feeds/dydx" \
    --ignore="tests/feeds/hyperliquid" \
    --ignore="tests/feeds/live_upbit" \
    --ignore="tests/feeds/live_uniswap" \
    --ignore="tests/feeds/balancer" \
    --ignore="tests/feeds/cow_swap" \
    --ignore="tests/feeds/curve" \
    --ignore="tests/feeds/gmx" \
    --ignore="tests/feeds/pancakeswap" \
    --ignore="tests/feeds/poloniex" \
    --ignore="tests/feeds/raydium" \
    --ignore="tests/feeds/sushiswap" \
    -k "not (test_live or live_test)" \
    --ignore-glob="tests/feeds/test_live_*.py" \
    -m "not network" \
    "${EXTRA_ARGS[@]}" \
    2>&1

echo "============================================================"
echo "  基础测试完成"
echo "============================================================"
