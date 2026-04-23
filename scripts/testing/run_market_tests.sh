#!/bin/bash
# 行情数据测试运行脚本
# 用于运行不需要鉴权的公开行情数据测试

set -e

echo "======================================"
echo "  行情数据测试运行器"
echo "======================================"
echo ""

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 帮助信息
show_help() {
    echo "用法: $0 [选项] [测试类型]"
    echo ""
    echo "测试类型:"
    echo "  ticker       - Ticker/Tick 行情数据测试"
    echo "  kline        - K线/Bar数据测试"
    echo "  orderbook    - 订单簿/深度数据测试"
    echo "  public_trade - 公开成交记录测试"
    echo "  all          - 所有行情数据测试（默认）"
    echo ""
    echo "选项:"
    echo "  -v, --verbose    详细输出"
    echo "  -n WORKERS       并行worker数量 (默认: 4)"
    echo "  --html           生成HTML报告"
    echo "  -h, --help       显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 ticker              # 只测试ticker数据"
    echo "  $0 kline -v            # 测试kline数据，详细输出"
    echo "  $0 all -n 8 --html     # 测试所有行情数据，8个worker，生成HTML报告"
}

# 默认参数
VERBOSE_ARGS=()
WORKERS=4
HTML_REPORT_ARGS=()
TEST_TYPE="all"

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--verbose)
            VERBOSE_ARGS=(-v)
            shift
            ;;
        -n)
            WORKERS=$2
            shift 2
            ;;
        --html)
            HTML_REPORT_ARGS=(--html=reports/market_tests.html --self-contained-html)
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        ticker|kline|orderbook|public_trade|all)
            TEST_TYPE=$1
            shift
            ;;
        *)
            echo -e "${RED}错误: 未知选项 '$1'${NC}"
            show_help
            exit 1
            ;;
    esac
done

# 构建测试标记
case $TEST_TYPE in
    ticker)
        MARKER="ticker"
        DESC="Ticker/Tick 行情数据"
        ;;
    kline)
        MARKER="kline"
        DESC="K线/Bar 数据"
        ;;
    orderbook)
        MARKER="orderbook"
        DESC="订单簿/深度数据"
        ;;
    public_trade)
        MARKER="public_trade"
        DESC="公开成交记录"
        ;;
    all)
        MARKER="ticker or kline or orderbook or public_trade"
        DESC="所有行情数据"
        ;;
esac

echo -e "${GREEN}测试类型: ${DESC}${NC}"
echo -e "${GREEN}标记: ${MARKER}${NC}"
echo -e "${GREEN}并行度: ${WORKERS} workers${NC}"
echo ""

# 创建报告目录
mkdir -p reports

# 运行测试
echo -e "${YELLOW}开始测试...${NC}"
echo ""

CMD=(pytest tests -m "$MARKER" -n "$WORKERS" "${VERBOSE_ARGS[@]}" "${HTML_REPORT_ARGS[@]}" --tb=short)

echo "执行命令: ${CMD[*]}"
echo ""

"${CMD[@]}"

EXIT_CODE=$?

# 结果
echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✅ 测试通过！${NC}"
    if [ ${#HTML_REPORT_ARGS[@]} -gt 0 ]; then
        echo -e "${GREEN}📊 HTML报告已生成: reports/market_tests.html${NC}"
    fi
else
    echo -e "${RED}❌ 测试失败，退出码: ${EXIT_CODE}${NC}"
fi

exit $EXIT_CODE
