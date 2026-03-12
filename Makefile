.PHONY: help install test test-cov test-fast test-unit test-integration test-performance test-contracts test-e2e clean lint format type-check security-scan docs analyze-coverage optimized-test

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
	@echo "  make lint             Run ruff linter"
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
	pip install -r requirements.txt

# Testing
test:
	./run_tests.sh

test-cov:
	./run_tests.sh --cov

test-fast:
	./run_tests.sh -m "not slow and not network"

test-unit:
	./run_tests.sh -m "unit"

test-integration:
	./run_tests.sh -m "integration"

test-ctp:
	./run_tests.sh --ctp

test-html:
	./run_tests.sh --html --cov

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
lint:
	@echo "Running ruff linter..."
	ruff check bt_api_py/ tests/

format:
	@echo "Formatting code with ruff..."
	ruff format bt_api_py/ tests/
	ruff check --fix bt_api_py/ tests/

type-check:
	@echo "Running mypy type checking..."
	mypy bt_api_py/

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
	find . -type f -name "*.so" ! -path "*/ctp/api/*" -delete

clean-test:
	@echo "Cleaning test artifacts..."
	rm -rf .pytest_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf logs/*.log
	rm -rf logs/*.html

clean-all: clean clean-test
	@echo "All cleaned!"
