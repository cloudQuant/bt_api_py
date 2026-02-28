#!/bin/bash
# Run tests script for bt_api_py
# Usage: ./run_tests.sh [--ctp] [-p|--parallel NUM]

set -eo pipefail

# Default values
RUN_CTP=false
PARALLEL=8

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
        -h|--help)
            echo "Usage: ./run_tests.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -c, --ctp              Run CTP related tests (default: false)"
            echo "  -p, --parallel NUM     Number of parallel processes (default: 8)"
            echo "                         Use 1 for single process, or any positive number"
            echo "  -h, --help             Show this help message"
            echo ""
            echo "Examples:"
            echo "  ./run_tests.sh                  # Run tests without CTP, 8 processes"
            echo "  ./run_tests.sh --ctp            # Run tests including CTP, 8 processes"
            echo "  ./run_tests.sh -p 1             # Single process, no CTP"
            echo "  ./run_tests.sh --ctp -p 8       # Include CTP, 8 processes"
            exit 0
            ;;
        *)
            echo "Error: Unknown option $1"
            echo "Use -h or --help for usage information"
            exit 1
            ;;
    esac
done

# Build pytest command
PYTEST_CMD="pytest -v"

# Add parallel option
if [ "$PARALLEL" -eq 1 ]; then
    echo "Running tests in single process mode..."
else
    echo "Running tests with $PARALLEL parallel processes..."
    PYTEST_CMD="$PYTEST_CMD -n $PARALLEL"
fi

# Add CTP exclusion/inclusion
if [ "$RUN_CTP" = false ]; then
    echo "Excluding CTP related tests..."
    PYTEST_CMD="$PYTEST_CMD --ignore=tests/test_ctp_feed.py"
else
    echo "Including CTP related tests..."
fi

# Print command
echo ""
echo "Command: $PYTEST_CMD"
echo ""

# Run tests (output to both terminal and log file)
echo "Log file: $LOG_FILE"
echo ""
eval "$PYTEST_CMD" 2>&1 | tee "$LOG_FILE"
exit ${PIPESTATUS[0]}
