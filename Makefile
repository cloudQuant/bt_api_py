.PHONY: help install test test-cov test-fast test-unit test-integration test-performance test-contracts test-e2e clean lint lint-all lint-submodules lint-stat format type-check type-check-l1 security-scan docs analyze-coverage optimized-test quality-ratchet quality-ratchet-update

# 质量门禁范围（迭代07）。与 scripts/ci/check_quality_ratchet.py 的 default_scope()
# 必须保持一致；tests/scripts/test_check_quality_ratchet.py 会断言两者相等。
SUB_SRC       := $(wildcard bt_api/bt_api_*/src)
SUB_TESTS     := $(wildcard bt_api/bt_api_*/tests)
CORE_SCOPE    := bt_api_py tests
QUALITY_SCOPE := $(CORE_SCOPE) scripts examples $(SUB_SRC) $(SUB_TESTS)

# Default target
help:
	@echo "bt_api_py - Makefile Commands"
	@echo ""
	@echo "Installation:"
	@echo "  make install          Install package in development mode"
	@echo "  make install-dev      Install with all dev dependencies"
	@echo ""
	@echo "Testing:"
	@echo "  make test             Run all tests (excluding CTP)"
	@echo "  make test-cov         Run tests with coverage report"
	@echo "  make test-fast        Run only fast tests (exclude slow/network)"
	@echo "  make test-unit        Run only unit tests"
	@echo "  make test-integration Run only integration tests"
	@echo "  make test-performance Run performance tests"
	@echo "  make test-contracts   Run property-based tests"
	@echo "  make test-e2e         Run end-to-end tests"
	@echo "  make test-ctp         Run CTP tests"
	@echo "  make test-html        Run tests and generate HTML report"
	@echo "  make optimized-test   Run optimized test suite"
	@echo "  make analyze-coverage Analyze test coverage gaps"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint             Run ruff linter (core scope: bt_api_py + tests)"
	@echo "  make lint-submodules  Run ruff linter on bt_api/*/src + bt_api/*/tests"
	@echo "  make lint-all         Run ruff linter on the full gated scope"
	@echo "  make lint-stat        Ruff statistics for the full gated scope"
	@echo "  make quality-ratchet  Verify lint debt did not increase (CI gate)"
	@echo "  make quality-ratchet-update  Refresh the ratchet snapshot when debt decreased"
	@echo "  make format           Format code with ruff"
	@echo "  make type-check       Run mypy type checking"
	@echo "  make check            Run all checks (lint + type-check)"
	@echo "  make security-scan    Run bandit security scan"
	@echo "  make pre-commit       Install pre-commit hooks"
	@echo "  make pre-commit-run   Run pre-commit on all files"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean            Remove build artifacts and cache"
	@echo "  make clean-test       Remove test artifacts"
	@echo "  make clean-all        Remove all generated files"

# Installation
install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"

# Testing
test:
	./scripts/run_tests.sh

test-cov:
	./scripts/run_tests.sh --cov

test-fast:
	./scripts/run_tests.sh -m "not slow and not network"

test-unit:
	./scripts/run_tests.sh -m "unit"

test-integration:
	./scripts/run_tests.sh -m "integration"

test-ctp:
	./scripts/run_tests.sh --ctp

test-html:
	./scripts/run_tests.sh --html --cov

test-performance:
	@echo "Running performance tests..."
	pytest tests/performance/ --tb=short -v

test-contracts:
	@echo "Running property-based tests..."
	pytest tests/contracts/ --tb=short -v

test-e2e:
	@echo "Running end-to-end tests..."
	pytest tests/e2e/ --tb=short -v

optimized-test:
	@echo "Running optimized test suite..."
	chmod +x scripts/run_optimized_tests.sh
	./scripts/run_optimized_tests.sh

analyze-coverage:
	@echo "Analyzing test coverage..."
	python scripts/analyze_coverage.py

# Code Quality
# `lint` 保持"主包范围"，当前为绿，供日常快速自查；
# 子仓与 examples/scripts 的存量债由 `lint-all` / `make quality-ratchet` 呈现，
# 并由 CI 的棘轮保证"只降不升"（见 docs/迭代计划/迭代07-代码质量提升/）。
lint:
	@echo "Running ruff linter (core scope)..."
	ruff check $(CORE_SCOPE)

lint-submodules:
	@echo "Running ruff linter (submodules)..."
	ruff check $(SUB_SRC) $(SUB_TESTS)

lint-all:
	@echo "Running ruff linter (full scope: core + submodules + scripts + examples)..."
	ruff check $(QUALITY_SCOPE)

lint-stat:
	@echo "Ruff statistics (full scope)..."
	ruff check $(QUALITY_SCOPE) --statistics

quality-ratchet:
	@echo "Checking the quality ratchet (lint debt may only decrease)..."
	python scripts/ci/check_quality_ratchet.py

quality-ratchet-update:
	@echo "Refreshing the quality ratchet snapshot (only when the debt decreased)..."
	python scripts/ci/check_quality_ratchet.py --update

format:
	@echo "Formatting code with ruff..."
	ruff format bt_api_py/ tests/
	ruff check --fix bt_api_py/ tests/

type-check:
	@echo "Running mypy type checking..."
	mypy bt_api_py/
	@echo "NOTE: submodule mypy scope is tracked as 迭代07 Task 13 (M4)."

# L1 子仓的 mypy 门禁（迭代07 M4-Task13）。
MYPY_L1_SUBMODULES := bt_api/bt_api_binance/src bt_api/bt_api_okx/src bt_api/bt_api_bybit/src bt_api/bt_api_gateio/src bt_api/bt_api_hyperliquid/src

type-check-l1:
	@echo "Running mypy on L1 submodules (binance/okx/bybit/gateio/hyperliquid)..."
	@for d in $(MYPY_L1_SUBMODULES); do \
	  printf "  %-34s " "$$d"; \
	  PYTHONPATH="$$d:bt_api/bt_api_base/src" mypy "$$d" --ignore-missing-imports || exit 1; \
	done

check: lint type-check
	@echo "All checks passed!"

security-scan:
	@echo "Running bandit security scan..."
	bandit -r bt_api_py/ -c pyproject.toml

pre-commit:
	@echo "Installing pre-commit hooks..."
	pre-commit install
	@echo "Pre-commit hooks installed! They will run automatically on git commit."

pre-commit-run:
	@echo "Running pre-commit on all files..."
	pre-commit run --all-files

# Cleanup
clean:
	@echo "Cleaning build artifacts..."
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.so" -delete

clean-test:
	@echo "Cleaning test artifacts..."
	rm -rf .pytest_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf logs/*.log
	rm -rf logs/*.html

clean-all: clean clean-test
	@echo "All cleaned!"
