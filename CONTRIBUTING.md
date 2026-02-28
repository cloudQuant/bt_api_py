# Contributing to bt_api_py

Thank you for your interest in contributing to bt_api_py! This guide will help you get started.

## Development Setup

### 1. Clone and Install

```bash
git clone https://github.com/cloudQuant/bt_api_py.git
cd bt_api_py

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install in development mode with all dependencies
make install-dev
# Or manually:
pip install -e ".[dev]"
pip install -r requirements.txt
```

### 2. Verify Installation

```bash
# Run tests
make test

# Run with coverage
make test-cov
```

## Development Workflow

### Running Tests

We use pytest with several useful options:

```bash
# Quick commands via Makefile
make test              # All tests (excluding CTP)
make test-fast         # Only fast tests
make test-unit         # Only unit tests
make test-cov          # With coverage report
make test-html         # Generate HTML report

# Direct script usage
./run_tests.sh --help  # See all options
./run_tests.sh --cov   # Run with coverage
./run_tests.sh -m unit # Run specific markers
```

### Test Markers

Use pytest markers to categorize tests:

```python
import pytest

@pytest.mark.unit
def test_fast_unit():
    """Fast test with no external dependencies"""
    pass

@pytest.mark.integration
@pytest.mark.network
def test_api_call():
    """Integration test requiring network"""
    pass

@pytest.mark.slow
def test_long_running():
    """Test that takes > 1 second"""
    pass
```

Available markers: `unit`, `integration`, `slow`, `network`, `ctp`, `binance`, `okx`, `ib`

### Code Quality

We use ruff for linting and formatting, and mypy for type checking:

```bash
# Format code
make format

# Run linter
make lint

# Type checking
make type-check

# Run all checks
make check
```

### Code Style Guidelines

- **Line length**: 100 characters max
- **Formatting**: Use `ruff format` (automatic via `make format`)
- **Imports**: Organized automatically by ruff
- **Type hints**: Add type hints for public APIs
- **Docstrings**: Use Google-style docstrings

Example:

```python
def calculate_position_size(
    account_balance: float,
    risk_percent: float,
    stop_loss_distance: float
) -> float:
    """Calculate position size based on risk management rules.
    
    Args:
        account_balance: Total account balance in base currency
        risk_percent: Risk percentage (0-100)
        stop_loss_distance: Distance to stop loss in price units
        
    Returns:
        Position size in units
        
    Raises:
        ValueError: If risk_percent is invalid
    """
    if not 0 < risk_percent <= 100:
        raise ValueError("risk_percent must be between 0 and 100")
    
    risk_amount = account_balance * (risk_percent / 100)
    return risk_amount / stop_loss_distance
```

## Project Structure

```
bt_api_py/
├── bt_api_py/          # Main package
│   ├── feeds/          # Exchange feed implementations
│   ├── containers/     # Data containers
│   ├── ctp/            # CTP futures API
│   └── ...
├── tests/              # Test suite
│   ├── feeds/          # Feed tests
│   ├── containers/     # Container tests
│   └── ...
├── docs/               # Documentation
├── Makefile            # Common commands
└── run_tests.sh        # Test runner script
```

## Pull Request Process

1. **Create a branch**: `git checkout -b feature/your-feature-name`

2. **Make your changes**:
   - Write tests for new functionality
   - Ensure all tests pass: `make test`
   - Format code: `make format`
   - Check code quality: `make check`

3. **Commit your changes**:
   ```bash
   git add .
   git commit -m "feat: add new feature description"
   ```
   
   Use conventional commit messages:
   - `feat:` New feature
   - `fix:` Bug fix
   - `docs:` Documentation changes
   - `test:` Test additions/changes
   - `refactor:` Code refactoring
   - `perf:` Performance improvements
   - `chore:` Maintenance tasks

4. **Push and create PR**:
   ```bash
   git push origin feature/your-feature-name
   ```
   Then create a pull request on GitHub.

## Testing Guidelines

### Writing Good Tests

1. **Test one thing**: Each test should verify one specific behavior
2. **Use descriptive names**: `test_order_submission_with_invalid_price_raises_error`
3. **Arrange-Act-Assert pattern**:
   ```python
   def test_order_creation():
       # Arrange
       account = Account(balance=10000)
       
       # Act
       order = account.create_order(symbol="BTCUSDT", quantity=0.1)
       
       # Assert
       assert order.symbol == "BTCUSDT"
       assert order.quantity == 0.1
   ```

4. **Use fixtures for setup**:
   ```python
   @pytest.fixture
   def mock_exchange():
       return MockExchange(api_key="test_key")
   
   def test_with_fixture(mock_exchange):
       result = mock_exchange.get_balance()
       assert result > 0
   ```

5. **Handle flaky network tests**:
   ```python
   @pytest.mark.network
   @pytest.mark.flaky(reruns=3, reruns_delay=1)
   def test_api_endpoint():
       response = api.get_data()
       assert response.status_code == 200
   ```

### Test Coverage

- Aim for >80% coverage for new code
- View coverage report: `make test-cov` then open `htmlcov/index.html`
- Critical paths should have 100% coverage

## Common Tasks

### Adding a New Exchange Feed

1. Create feed class in `bt_api_py/feeds/live_{exchange}/`
2. Implement required methods (connect, subscribe, etc.)
3. Add tests in `tests/feeds/test_live_{exchange}_*.py`
4. Add marker: `@pytest.mark.{exchange}`
5. Update documentation

### Debugging Tips

- Use VS Code debugger (F5) with provided launch configurations
- Add breakpoints in tests: `pytest.set_trace()`
- Run single test: `pytest tests/test_file.py::test_function -v -s`
- Check logs in `logs/` directory

## Getting Help

- **Issues**: Open an issue on GitHub
- **Discussions**: Use GitHub Discussions for questions
- **Email**: yunjinqi@gmail.com

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
