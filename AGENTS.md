# AGENTS.md - AI Agent Guidelines for bt_api_py

This document provides essential information for AI coding agents working in the bt_api_py repository.

## Project Overview

bt_api_py is a unified multi-exchange trading API framework supporting 73+ exchanges (Binance, OKX, CTP, IB, etc.) with synchronous, asynchronous, and WebSocket interfaces.

**Technology Stack:**
- Python 3.11+ (required)
- Key dependencies: pandas, numpy, aiohttp, websocket-client, httpx
- Testing: pytest with xdist, cov, timeout, html plugins
- Linting/Formatting: ruff (linter + formatter)
- Type checking: mypy

## Build/Lint/Test Commands

### Testing Commands

```bash
# Run all tests (excluding CTP) - DEFAULT
./run_tests.sh
# or: make test

# Run single test file
pytest tests/test_registry.py -v

# Run single test function
pytest tests/test_registry.py::TestRegistryInstance::test_register_and_create_feed -v

# Run tests with specific markers
pytest tests -m unit -v                    # Unit tests only
pytest tests -m "not slow and not network" -v  # Fast tests only
pytest tests -m binance -v                 # Binance-specific tests
pytest tests -m ctp -v                     # CTP tests

# Run tests in parallel (8 workers by default)
./run_tests.sh -p 8
# or: pytest tests -n 8

# Run tests with coverage
./run_tests.sh --cov
# or: make test-cov
# View report: open htmlcov/index.html

# Run specific test types
make test-unit          # Unit tests only
make test-fast          # Exclude slow/network tests
make test-integration   # Integration tests only

# Generate HTML test report
./run_tests.sh --html
# or: make test-html
```

### Linting and Formatting

```bash
# Format code (auto-fix)
make format
# or: ruff format bt_api_py/ tests/ && ruff check --fix bt_api_py/ tests/

# Check linting issues
make lint
# or: ruff check bt_api_py/ tests/

# Type checking
make type-check
# or: mypy bt_api_py/

# Run all quality checks
make check
```

### Installation

```bash
# Install in development mode
pip install -e ".[dev]"

# Install with all dependencies
make install-dev
```

### Pre-commit Hooks

```bash
# Install hooks (runs automatically on commit)
make pre-commit

# Run manually on all files
make pre-commit-run
```

## Code Style Guidelines

### General Rules

- **Line length:** 100 characters maximum
- **Python version:** 3.11+ required
- **Formatting:** Use `ruff format` (auto-formatting)
- **Quotes:** Double quotes preferred (enforced by ruff)
- **Indentation:** 4 spaces (no tabs)

### Imports

Imports are automatically organized by ruff (isort-like behavior):

```python
# Standard library
import asyncio
from typing import Any, Protocol

# Third-party
import pytest
from aiohttp import ClientSession

# Local imports
from bt_api_py.exceptions import BtApiError, ExchangeNotFoundError
from bt_api_py.registry import ExchangeRegistry
```

**Import rules:**
- Unused imports are automatically removed
- Imports are sorted and grouped
- Use `from module import Item` for specific items
- Avoid wildcard imports (`from module import *`) except in `__init__.py`

### Type Hints

Add type hints for public APIs:

```python
def calculate_position_size(
    account_balance: float,
    risk_percent: float,
    stop_loss_distance: float
) -> float:
    """Calculate position size based on risk management rules."""
    risk_amount = account_balance * (risk_percent / 100)
    return risk_amount / stop_loss_distance

# Use modern Python 3.11+ type syntax
def get_exchange_names(self) -> list[str]:
    return list(self._feed_classes.keys())

# Use union types with |
def get_stream_class(
    self, 
    exchange_name: str, 
    stream_type: str
) -> Any | None:
    return self._stream_classes.get(exchange_name, {}).get(stream_type)
```

### Docstrings

Use Google-style docstrings:

```python
def register_feed(self, exchange_name: str, feed_class: type) -> None:
    """Register a REST feed class for an exchange.

    Args:
        exchange_name: Exchange identifier (e.g., "BINANCE___SPOT", "CTP___FUTURE")
        feed_class: Feed subclass to register

    Raises:
        ValueError: If exchange_name is empty
    """
    self._feed_classes[exchange_name] = feed_class
```

### Naming Conventions

- **Classes:** PascalCase (`ExchangeRegistry`, `BinanceSpotFeed`)
- **Functions/Methods:** snake_case (`register_feed`, `get_ticker`)
- **Constants:** UPPER_SNAKE_CASE (`DEFAULT_TIMEOUT`, `MAX_RETRIES`)
- **Private attributes:** prefix with underscore (`_feed_classes`, `_default`)
- **Exception:** CTP classes may use CamelCase for attributes (N802, N803, N806 ignored)

### Error Handling

Use the custom exception hierarchy from `bt_api_py.exceptions`:

```python
from bt_api_py.exceptions import (
    BtApiError,
    ExchangeNotFoundError,
    OrderError,
    InsufficientBalanceError,
    InvalidSymbolError,
    RequestError,
    RateLimitError,
)

# Good: Use specific exceptions
def create_order(self, symbol: str, quantity: float) -> Order:
    if not self.has_balance(quantity):
        raise InsufficientBalanceError(
            exchange_name=self.exchange_name,
            symbol=symbol,
            required=quantity,
            available=self.balance
        )
    # ... order creation logic

# Good: Catch specific exceptions
try:
    order = api.create_order("BTCUSDT", 0.1)
except InsufficientBalanceError as e:
    logger.error(f"Not enough balance: {e}")
except OrderError as e:
    logger.error(f"Order failed: {e}")
```

**Exception hierarchy:**
- `BtApiError` - Base exception for all errors
  - `ExchangeNotFoundError` - Exchange not registered
  - `ExchangeConnectionError` - Connection failed
  - `RequestError` - REST request failed
  - `OrderError` - Order operation failed
    - `InsufficientBalanceError`
    - `InvalidOrderError`
    - `OrderNotFoundError`
  - `RateLimitError` - API rate limit exceeded
  - `InvalidSymbolError` - Invalid trading symbol
  - `WebSocketError` - WebSocket connection error

### Testing Patterns

**Test markers:**
```python
import pytest

@pytest.mark.unit
def test_fast_calculation():
    """Fast unit test with no external dependencies."""
    assert calculate_position_size(10000, 1, 100) == 1.0

@pytest.mark.integration
@pytest.mark.network
def test_api_call():
    """Integration test requiring network access."""
    response = api.get_ticker("BTCUSDT")
    assert response.last_price > 0

@pytest.mark.slow
def test_long_running():
    """Test that takes > 1 second."""
    pass

@pytest.mark.binance
def test_binance_specific():
    """Binance-specific test."""
    pass
```

**Test structure (Arrange-Act-Assert):**
```python
def test_order_creation():
    # Arrange
    exchange_name = "BINANCE___SPOT"
    registry = ExchangeRegistry()
    registry.register_feed(exchange_name, MockFeed)
    
    # Act
    feed = registry.create_feed(exchange_name, data_queue="test_queue")
    
    # Assert
    assert isinstance(feed, MockFeed)
    assert feed.data_queue == "test_queue"
```

**Using fixtures:**
```python
@pytest.fixture
def mock_registry():
    """Create a fresh registry for each test."""
    return ExchangeRegistry()

def test_with_fixture(mock_registry):
    mock_registry.register_feed("TEST", MockFeed)
    assert mock_registry.has_exchange("TEST")
```

### Data Classes and Containers

Use `AutoInitMixin` for data containers:

```python
from bt_api_py.containers.auto_init_mixin import AutoInitMixin

class OrderData(AutoInitMixin):
    """Order data container."""
    
    def __init__(self, order_info: dict, has_been_json_encoded: bool = False):
        self.event = "OrderEvent"
        self.order_info = order_info
        self.has_been_json_encoded = has_been_json_encoded
    
    def get_exchange_name(self) -> str:
        """Return exchange name."""
        raise NotImplementedError
```

### Registry Pattern

When adding a new exchange, use the registry pattern:

```python
from bt_api_py.registry import ExchangeRegistry
from bt_api_py.feeds.live_newexchange import NewExchangeFeed

# Register the exchange
ExchangeRegistry.register_feed("NEWEXCHANGE___SPOT", NewExchangeFeed)
ExchangeRegistry.register_stream("NEWEXCHANGE___SPOT", "market", NewExchangeMarketStream)
```

### Comments

This project uses both English and Chinese comments. When adding comments:
- Technical documentation: Prefer English
- Business logic explanations: Chinese is acceptable
- Keep comments concise and relevant
- Update comments when code changes

## Project Structure

```
bt_api_py/
├── bt_api_py/              # Main package
│   ├── bt_api.py          # Unified API entry point
│   ├── registry.py        # Exchange registry (singleton pattern)
│   ├── event_bus.py       # Event bus for pub/sub
│   ├── exceptions.py      # Custom exception hierarchy
│   ├── auth_config.py     # Authentication configurations
│   ├── containers/        # Data containers (20+ types)
│   │   ├── orders/       # Order data classes
│   │   ├── tickers/      # Ticker data classes
│   │   ├── bars/         # K-line data classes
│   │   └── ...
│   ├── feeds/             # Exchange adapters
│   │   ├── abstract_feed.py    # Abstract protocol
│   │   ├── live_binance/       # Binance implementation
│   │   ├── live_okx/           # OKX implementation
│   │   ├── live_ctp_feed.py    # CTP implementation
│   │   └── register_*.py       # Exchange registration modules
│   └── ctp/               # CTP C++ extensions
├── tests/                 # Test suite
│   ├── feeds/            # Feed tests
│   ├── containers/       # Container tests
│   └── test_*.py         # Core tests
├── docs/                  # Documentation (MkDocs)
├── Makefile              # Convenient commands
├── run_tests.sh          # Test runner script
└── pyproject.toml        # Project configuration
```

## Important Configuration Files

- **pyproject.toml** - Project metadata, dependencies, tool configs
- **Makefile** - Convenient command shortcuts
- **run_tests.sh** - Advanced test runner with logging
- **.pre-commit-config.yaml** - Pre-commit hooks configuration
- **conftest.py** - Pytest fixtures and configuration

## Common Tasks

### Running a Single Test

```bash
# Specific test file
pytest tests/test_registry.py -v

# Specific test class
pytest tests/test_registry.py::TestRegistryInstance -v

# Specific test function
pytest tests/test_registry.py::TestRegistryInstance::test_register_and_create_feed -v

# With print output
pytest tests/test_registry.py::test_function -v -s
```

### Debugging Tests

```python
import pytest

def test_debugging():
    value = calculate_something()
    pytest.set_trace()  # Debugger breakpoint
    assert value > 0
```

### Adding a New Exchange

1. Create feed class in `bt_api_py/feeds/live_{exchange}/`
2. Implement `AbstractVenueFeed` protocol
3. Register in `bt_api_py/feeds/register_{exchange}.py`
4. Add tests in `tests/feeds/test_{exchange}.py`
5. Mark tests with `@pytest.mark.{exchange}`
6. Update documentation

## Pre-commit Checks

Before committing, ensure:
1. Code is formatted: `make format`
2. No linting errors: `make lint`
3. Type checks pass: `make type-check`
4. Tests pass: `make test`
5. Or run all: `make check && make test`

## Key Design Patterns

- **Registry Pattern**: Exchanges are registered without modifying core code
- **Event Bus**: Pub/sub for market data and order events
- **Protocol-based Abstract Classes**: Use `Protocol` for type checking
- **AutoInitMixin**: Automatic data initialization for containers
- **Async/Sync Hybrid**: Both sync and async APIs available

## Additional Resources

- **Documentation**: https://cloudquant.github.io/bt_api_py/
- **Contributing Guide**: CONTRIBUTING.md
- **README**: README.md (Chinese), README.en.md (English)
- **Issues**: https://github.com/cloudQuant/bt_api_py/issues
