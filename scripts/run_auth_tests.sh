#!/bin/bash
# 需要鉴权的测试运行脚本
# 用于运行需要账户配置的测试

set -e

echo "======================================"
echo "  鉴权测试运行器"
echo "======================================"
echo ""

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 帮助信息
show_help() {
    echo "用法: $0 [选项] [测试类型]"
    echo ""
    echo "测试类型:"
    echo "  account   - 账户和余额测试"
    echo "  order     - 订单管理测试"
    echo "  position  - 持仓管理测试"
    echo "  trade     - 私有成交记录测试"
    echo "  all       - 所有鉴权测试（默认）"
    echo ""
    echo "选项:"
    echo "  -v, --verbose    详细输出"
    echo "  -n WORKERS       并行worker数量 (默认: 4)"
    echo "  --html           生成HTML报告"
    echo "  --dry-run        只显示测试列表，不执行"
    echo "  -h, --help       显示此帮助信息"
    echo ""
    echo "环境变量:"
    echo "  TEST_EXCHANGES   指定要测试的交易所 (逗号分隔，如: binance,okx)"
    echo "  SKIP_SLOW        跳过慢速测试 (true/false, 默认: true)"
    echo ""
    echo "示例:"
    echo "  $0 account                      # 只测试账户功能"
    echo "  $0 order -v                     # 测试订单功能，详细输出"
    echo "  $0 all -n 8 --html              # 测试所有鉴权功能，8个worker，生成HTML报告"
    echo "  TEST_EXCHANGES=binance $0 all   # 只测试Binance交易所"
}

# 默认参数
VERBOSE=""
WORKERS=4
HTML_REPORT=""
DRY_RUN=""
TEST_TYPE="all"

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--verbose)
            VERBOSE="-v"
            shift
            ;;
        -n)
            WORKERS=$2
            shift 2
            ;;
        --html)
            HTML_REPORT="--html=reports/auth_tests.html --self-contained-html"
            shift
            ;;
        --dry-run)
            DRY_RUN="--collect-only"
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        account|order|position|trade|all)
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
    account)
        MARKER="auth_account"
        DESC="账户和余额"
        ;;
    order)
        MARKER="auth_order"
        DESC="订单管理"
        ;;
    position)
        MARKER="auth_position"
        DESC="持仓管理"
        ;;
    trade)
        MARKER="auth_private_trade"
        DESC="私有成交记录"
        ;;
    all)
        MARKER="auth_account or auth_order or auth_position or auth_private_trade"
        DESC="所有鉴权功能"
        ;;
esac

echo -e "${GREEN}测试类型: ${DESC}${NC}"
echo -e "${GREEN}标记: ${MARKER}${NC}"
echo -e "${GREEN}并行度: ${WORKERS} workers${NC}"

if [ -n "$TEST_EXCHANGES" ]; then
    echo -e "${BLUE}指定交易所: ${TEST_EXCHANGES}${NC}"
fi

if [ "$SKIP_SLOW" = "true" ]; then
    echo -e "${YELLOW}跳过慢速测试${NC}"
    MARKER="${MARKER} and not slow"
fi

echo ""

# 检查账户配置
if [ ! -f ".env" ] && [ ! -f "account_config.json" ]; then
    echo -e "${RED}⚠️  警告: 未找到账户配置文件 (.env 或 account_config.json)${NC}"
    echo -e "${YELLOW}   某些测试可能会被跳过${NC}"
    echo ""
fi

# 创建报告目录
mkdir -p reports

# 运行测试
if [ -n "$DRY_RUN" ]; then
    echo -e "${YELLOW}收集测试（dry-run模式）...${NC}"
    echo ""
else
    echo -e "${YELLOW}开始测试...${NC}"
fi
echo ""

CMD="pytest tests -m \"${MARKER}\" -n ${WORKERS} ${VERBOSE} ${HTML_REPORT} ${DRY_RUN} --tb=short"

echo "执行命令: $CMD"
echo ""

eval $CMD

EXIT_CODE=$?

# 结果
echo ""
if [ -n "$DRY_RUN" ]; then
    echo -e "${GREEN}✅ 测试收集完成${NC}"
elif [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✅ 测试通过！${NC}"
    if [ -n "$HTML_REPORT" ]; then
        echo -e "${GREEN}📊 HTML报告已生成: reports/auth_tests.html${NC}"
    fi
else
    echo -e "${RED}❌ 测试失败，退出码: ${EXIT_CODE}${NC}"
    echo -e "${YELLOW}提示: 检查账户配置和网络连接${NC}"
fi

exit $EXIT_CODE
