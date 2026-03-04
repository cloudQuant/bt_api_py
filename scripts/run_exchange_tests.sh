#!/usr/bin/env bash
# run_exchange_tests.sh — 运行单个交易所的所有测试
#
# 用法：
#   bash scripts/run_exchange_tests.sh <exchange_name> [--network] [额外pytest参数]
#
# 示例：
#   bash scripts/run_exchange_tests.sh binance             # 仅 mock/单元测试
#   bash scripts/run_exchange_tests.sh binance --network   # 含网络/live 测试
#   bash scripts/run_exchange_tests.sh okx --network -v
#   bash scripts/run_exchange_tests.sh bybit
#   bash scripts/run_exchange_tests.sh ctp
#
# 参数：
#   <exchange_name>   交易所名称（小写），如 binance, okx, bybit, ctp, hyperliquid …
#   --network         同时运行需要网络/真实API的 live_* 测试（默认跳过）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [[ $# -lt 1 ]]; then
    echo "用法: bash scripts/run_exchange_tests.sh <exchange_name> [--network] [pytest参数]"
    echo ""
    echo "可用的交易所名称示例："
    echo "  binance okx htx ctp ib bybit bitget kraken kucoin coinbase"
    echo "  mexc hyperliquid dydx bitfinex upbit gemini gateio hitbtc poloniex"
    echo "  bequant bigone bingx bitbank bitbns bitflyer bithumb bitstamp"
    echo "  btcturk buda bybit bydfi coincheck coindcx coinex coinone"
    echo "  exmo foxbit gateio giottus independent_reserve korbit latoken"
    echo "  luno mercado_bitcoin phemex ripio satoshitango swyftx valr wazirx yobit zaif zebpay"
    exit 1
fi

EXCHANGE="$1"
shift

INCLUDE_NETWORK=false
EXTRA_ARGS=()
for arg in "$@"; do
    if [[ "$arg" == "--network" ]]; then
        INCLUDE_NETWORK=true
    else
        EXTRA_ARGS+=("$arg")
    fi
done

echo "============================================================"
echo "  bt_api_py 交易所测试: $EXCHANGE"
echo "  网络测试: $INCLUDE_NETWORK"
echo "  项目目录: $PROJECT_ROOT"
echo "============================================================"

# 收集相关测试路径
TEST_PATHS=()

# 1. tests/feeds/test_<exchange>.py
for f in "tests/feeds/test_${EXCHANGE}.py" \
          "tests/feeds/test_${EXCHANGE}_*.py" \
          "tests/feeds/test_*_${EXCHANGE}_*.py"; do
    for match in $f; do
        [[ -e "$match" ]] && TEST_PATHS+=("$match")
    done
done

# 2. tests/feeds/test_live_<exchange>*.py  (只在 --network 时包含)
if $INCLUDE_NETWORK; then
    for f in "tests/feeds/test_live_${EXCHANGE}*.py"; do
        for match in $f; do
            [[ -e "$match" ]] && TEST_PATHS+=("$match")
        done
    done
fi

# 3. tests/feeds/<exchange>/ 目录
for d in "tests/feeds/${EXCHANGE}" "tests/feeds/live_${EXCHANGE}"; do
    [[ -d "$d" ]] && TEST_PATHS+=("$d")
done

# 4. 特殊: ctp -> tests/test_ctp_feed.py
if [[ "$EXCHANGE" == "ctp" ]]; then
    [[ -e "tests/test_ctp_feed.py" ]] && TEST_PATHS+=("tests/test_ctp_feed.py")
fi

# 5. tests/feeds/test_<exchange>_integration.py
for f in "tests/feeds/test_${EXCHANGE}_integration.py" \
          "tests/test_${EXCHANGE}_integration.py"; do
    [[ -e "$f" ]] && TEST_PATHS+=("$f")
done

if [[ ${#TEST_PATHS[@]} -eq 0 ]]; then
    echo "[警告] 未找到交易所 '$EXCHANGE' 的测试文件。"
    echo "已搜索以下路径（无匹配）："
    echo "  tests/feeds/test_${EXCHANGE}.py"
    echo "  tests/feeds/test_${EXCHANGE}_*.py"
    echo "  tests/feeds/test_live_${EXCHANGE}*.py"
    echo "  tests/feeds/${EXCHANGE}/"
    exit 1
fi

echo "测试文件/目录："
for p in "${TEST_PATHS[@]}"; do
    echo "  $p"
done
echo ""

# 构建 pytest 命令
PYTEST_ARGS=("${TEST_PATHS[@]}")

if ! $INCLUDE_NETWORK; then
    PYTEST_ARGS+=("-m" "not network")
fi

PYTEST_ARGS+=("${EXTRA_ARGS[@]}")

python -m pytest "${PYTEST_ARGS[@]}"

EXIT_CODE=$?

echo ""
echo "============================================================"
if [[ $EXIT_CODE -eq 0 ]]; then
    echo "  ✅ $EXCHANGE 测试全部通过"
else
    echo "  ❌ $EXCHANGE 测试存在失败 (exit code: $EXIT_CODE)"
fi
echo "============================================================"

exit $EXIT_CODE
