#!/bin/bash
# Run tests script for bt_api_py
# Usage: ./run_tests.sh [OPTIONS]

set -eo pipefail

# Default values
RUN_CTP=false
PARALLEL=8
COVERAGE=false
HTML_REPORT=false
MARKERS=""
FAST_MODE=false

# Log setup
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/test_$(date '+%Y%m%d_%H%M%S').log"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -c|--ctp)
            RUN_CTP=true
            shift
            ;;
        -p|--parallel)
            if [[ -n "$2" && "$2" =~ ^[0-9]+$ ]]; then
                PARALLEL="$2"
                shift 2
            else
                echo "Error: --parallel requires a number argument"
                exit 1
            fi
            ;;
        --cov|--coverage)
            COVERAGE=true
            shift
            ;;
        --html)
            HTML_REPORT=true
            shift
            ;;
        -f|--fast)
            FAST_MODE=true
            shift
            ;;
        -m|--markers)
            if [[ -n "$2" ]]; then
                MARKERS="$2"
                shift 2
            else
                echo "Error: --markers requires an argument"
                exit 1
            fi
            ;;
        -h|--help)
            echo "Usage: ./run_tests.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -c, --ctp              Run CTP related tests (default: false)"
            echo "  -p, --parallel NUM     Number of parallel processes (default: 8)"
            echo "  -f, --fast             Run non-network, non-slow tests only (8 workers)"
            echo "  --cov, --coverage      Generate coverage report"
            echo "  --html                 Generate HTML test report (requires pytest-html)"
            echo "  -m, --markers EXPR     Run tests matching marker expression"
            echo "                         Examples: 'unit', 'not slow', 'network and binance'"
            echo "  -h, --help             Show this help message"
            echo ""
            echo "Available markers:"
            echo "  unit, integration, slow, network, ctp, binance, okx, ib"
            echo ""
            echo "Examples:"
            echo "  ./run_tests.sh                      # Run all tests (no CTP), 8 processes"
            echo "  ./run_tests.sh --fast               # Run fast tests only (no network/slow)"
            echo "  ./run_tests.sh --ctp --cov          # Run with CTP and coverage"
            echo "  ./run_tests.sh -m unit               # Run only unit tests"
            echo "  ./run_tests.sh -m 'not slow' --cov   # Fast tests with coverage"
            echo "  ./run_tests.sh --html                # Generate HTML report"
            exit 0
            ;;
        *)
            echo "Error: Unknown option $1"
            echo "Use -h or --help for usage information"
            exit 1
            ;;
    esac
done

# --fast implies markers for non-network, non-slow
if [ "$FAST_MODE" = true ]; then
    if [ -n "$MARKERS" ]; then
        echo "Warning: --fast overrides -m/--markers"
    fi
    MARKERS="not network and not slow"
    echo "Fast mode: running non-network, non-slow tests only"
fi

# Build pytest command
PYTEST_CMD=(pytest -v)

# Add parallel option
if [ "$PARALLEL" -eq 1 ]; then
    echo "Running tests in single process mode..."
else
    echo "Running tests with $PARALLEL parallel processes..."
    PYTEST_CMD+=(-n "$PARALLEL")
fi

# Add CTP exclusion/inclusion
if [ "$RUN_CTP" = false ]; then
    echo "Excluding CTP related tests..."
    PYTEST_CMD+=(--ignore=tests/test_ctp_feed.py)
else
    echo "Including CTP related tests..."
fi

# Add coverage options
if [ "$COVERAGE" = true ]; then
    echo "Enabling coverage reporting..."
    PYTEST_CMD+=(--cov=bt_api_py --cov-report=term-missing --cov-report=html)
fi

# Add HTML report
if [ "$HTML_REPORT" = true ]; then
    echo "Enabling HTML test report..."
    HTML_REPORT_FILE="logs/report_$(date '+%Y%m%d_%H%M%S').html"
    PYTEST_CMD+=("--html=$HTML_REPORT_FILE" --self-contained-html)
fi

# Add marker filtering
if [ -n "$MARKERS" ]; then
    echo "Filtering tests with markers: $MARKERS"
    PYTEST_CMD+=(-m "$MARKERS")
fi

# Print command
echo ""
printf "Command:"
printf " %q" "${PYTEST_CMD[@]}"
echo ""
echo ""

# Run tests (output to both terminal and log file)
echo "Log file: $LOG_FILE"
echo ""
"${PYTEST_CMD[@]}" 2>&1 | tee "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}

# Analyze log for failures and errors (short summary lines only, with error detail)
if [ -f "$LOG_FILE" ]; then
    FAILED_LINES=$(grep "FAILED tests/.* - " "$LOG_FILE" 2>/dev/null | sort -u || true)
    ERROR_LINES=$(grep "ERROR tests/.* - " "$LOG_FILE" 2>/dev/null | sort -u || true)
    FAILED_COUNT=$(echo "$FAILED_LINES" | grep -c . 2>/dev/null) || FAILED_COUNT=0
    ERROR_COUNT=$(echo "$ERROR_LINES" | grep -c . 2>/dev/null) || ERROR_COUNT=0
    FAILED_COUNT=$((FAILED_COUNT + 0))
    ERROR_COUNT=$((ERROR_COUNT + 0))
    if [ "$FAILED_COUNT" -gt 0 ] || [ "$ERROR_COUNT" -gt 0 ]; then
        echo ""
        echo "========== FAILURES & ERRORS SUMMARY =========="
        echo ""
        if [ -n "$FAILED_LINES" ]; then
            echo "--- FAILED tests ---"
            echo "$FAILED_LINES" | sed 's/^/  /'
        fi
        if [ -n "$ERROR_LINES" ]; then
            echo ""
            echo "--- ERROR tests ---"
            echo "$ERROR_LINES" | sed 's/^/  /'
        fi
        echo ""
        echo "Total: $FAILED_COUNT failed, $ERROR_COUNT errors"
        echo "Log file: $LOG_FILE"
        echo "================================================"
    fi
fi

exit "$EXIT_CODE"
