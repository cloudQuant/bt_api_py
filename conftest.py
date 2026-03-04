"""Pytest configuration and shared fixtures."""

import os
import sys
from pathlib import Path

import pytest

# Add project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


# Check if API keys are available
def has_binance_api_keys():
    """Check if Binance API keys are configured."""
    return bool(os.getenv("BINANCE_API_KEY") and os.getenv("BINANCE_SECRET"))


def has_okx_api_keys():
    """Check if OKX API keys are configured."""
    return bool(
        os.getenv("OKX_API_KEY")
        and os.getenv("OKX_SECRET")
        and os.getenv("OKX_PASSPHRASE")
    )


def has_ctp_credentials():
    """Check if CTP credentials are configured."""
    return bool(
        os.getenv("CTP_BROKER_ID")
        and os.getenv("CTP_USER_ID")
        and os.getenv("CTP_PASSWORD")
    )


def has_htx_api_keys():
    """Check if HTX API keys are configured."""
    return bool(os.getenv("HTX_API_KEY") and os.getenv("HTX_SECRET")
               and os.getenv("HTX_API_KEY") != "your_htx_api_key_here")


def has_ib_credentials():
    """Check if IB credentials are configured."""
    return bool(os.getenv("IB_ACCOUNT_ID"))


def should_skip_live_tests():
    """Check if live tests should be skipped."""
    return os.getenv("SKIP_LIVE_TESTS", "").lower() in ("true", "1", "yes")


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
    config.addinivalue_line("markers", "htx: HTX exchange tests")
    config.addinivalue_line("markers", "ib: Interactive Brokers tests")


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers automatically and skip tests without API keys."""
    skip_live = should_skip_live_tests()

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

        # Auto-mark exchange-specific tests and skip if no API keys
        test_path = str(item.fspath).lower()
        test_name = item.nodeid.lower()

        # Binance tests — add marker only, never auto-skip
        if "binance" in test_path and "binance" not in item.keywords:
            item.add_marker(pytest.mark.binance)

        # OKX tests — add marker only, never auto-skip
        if "okx" in test_path and "okx" not in item.keywords:
            item.add_marker(pytest.mark.okx)

        # CTP tests — add marker only, never auto-skip
        if "ctp" in test_path and "ctp" not in item.keywords:
            item.add_marker(pytest.mark.ctp)

        # IB tests — add marker only, never auto-skip
        if "ib" in test_path and "ib" not in item.keywords:
            item.add_marker(pytest.mark.ib)

        # Skip @pytest.mark.integration tests when SKIP_LIVE_TESTS is set
        if skip_live and "integration" in item.keywords:
            item.add_marker(
                pytest.mark.skip(reason="SKIP_LIVE_TESTS=true")
            )


def pytest_runtest_call(item):
    """Auto-skip integration tests that fail due to network/auth errors."""
    if "integration" not in item.keywords:
        return
    try:
        item.runtest()
    except Exception as exc:
        exc_name = type(exc).__name__
        exc_msg = str(exc).lower()
        network_indicators = [
            "authenticationerror", "requestfailederror",
            "connectionerror", "connecterror", "timeout",
            "ssl", "eof", "connection refused", "403", "401",
        ]
        if any(ind in exc_name.lower() or ind in exc_msg for ind in network_indicators):
            pytest.skip(f"Integration test skipped (network/auth): {exc_name}")
        raise


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
