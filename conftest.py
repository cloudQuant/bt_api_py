"""Pytest configuration and shared fixtures."""

import os
import sys
from pathlib import Path

import pytest

# Add project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def pytest_configure(config):
    """Configure pytest with custom settings."""
    # Register custom markers
    config.addinivalue_line(
        "markers", "unit: Unit tests (fast, no external dependencies)"
    )
    config.addinivalue_line(
        "markers", "integration: Integration tests (may require external services)"
    )
    config.addinivalue_line("markers", "slow: Slow tests (> 1s)")
    config.addinivalue_line("markers", "network: Tests requiring network access")
    config.addinivalue_line("markers", "ctp: CTP related tests")
    config.addinivalue_line("markers", "binance: Binance exchange tests")
    config.addinivalue_line("markers", "okx: OKX exchange tests")
    config.addinivalue_line("markers", "ib: Interactive Brokers tests")


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers automatically."""
    for item in items:
        # Auto-mark slow tests based on timeout or explicit markers
        if "slow" not in item.keywords:
            # Check if test file or function name suggests it's slow
            test_name = item.nodeid.lower()
            if any(keyword in test_name for keyword in ["slow", "benchmark", "stress"]):
                item.add_marker(pytest.mark.slow)
        
        # Auto-mark network tests
        if "network" not in item.keywords:
            test_name = item.nodeid.lower()
            if any(keyword in test_name for keyword in ["request", "wss", "websocket", "api"]):
                item.add_marker(pytest.mark.network)
        
        # Auto-mark exchange-specific tests
        test_path = str(item.fspath).lower()
        if "binance" in test_path and "binance" not in item.keywords:
            item.add_marker(pytest.mark.binance)
        if "okx" in test_path and "okx" not in item.keywords:
            item.add_marker(pytest.mark.okx)
        if "ctp" in test_path and "ctp" not in item.keywords:
            item.add_marker(pytest.mark.ctp)
        if "ib" in test_path and "ib" not in item.keywords:
            item.add_marker(pytest.mark.ib)


@pytest.fixture(scope="session")
def project_root_path():
    """Return the project root path."""
    return Path(__file__).parent


@pytest.fixture(scope="session")
def test_data_dir(project_root_path):
    """Return the test data directory."""
    return project_root_path / "tests" / "data"


@pytest.fixture(autouse=True)
def reset_environment():
    """Reset environment variables before each test."""
    original_env = os.environ.copy()
    yield
    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def mock_api_response():
    """Provide a mock API response for testing."""
    return {
        "status": "success",
        "data": {"key": "value"},
        "timestamp": 1234567890,
    }


# Performance optimization: reuse expensive fixtures
@pytest.fixture(scope="session")
def shared_test_config():
    """Shared test configuration to avoid repeated loading."""
    return {
        "test_mode": True,
        "timeout": 30,
        "retry_count": 3,
    }
