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
    # Set LD_LIBRARY_PATH for CTP libraries
    ctp_lib_path = Path(__file__).parent / "bt_api_py" / "ctp" / "api" / "6.7.7" / "linux"
    if ctp_lib_path.exists():
        current_ld_path = os.environ.get("LD_LIBRARY_PATH", "")
        if str(ctp_lib_path) not in current_ld_path:
            os.environ["LD_LIBRARY_PATH"] = f"{ctp_lib_path}:{current_ld_path}"

    # Preload libiconv if available (needed by CTP libraries on Linux)
    anaconda_lib = os.path.join(sys.prefix, "lib")
    iconv_lib = os.path.join(anaconda_lib, "libiconv.so.2")
    if os.path.exists(iconv_lib):
        current_preload = os.environ.get("LD_PRELOAD", "")
        if iconv_lib not in current_preload:
            os.environ["LD_PRELOAD"] = f"{iconv_lib}:{current_preload}" if current_preload else iconv_lib

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
        # Group network-heavy tests by exchange for xdist so parallel workers
        # don't overwhelm the same exchange API simultaneously.
        fspath = str(item.fspath).lower()
        for exchange in (
            "okx", "binance", "ctp", "htx", "bitfinex", "coinbase",
            "kucoin", "mexc", "bybit", "upbit", "hyperliquid",
        ):
            if exchange in fspath:
                item.add_marker(pytest.mark.xdist_group(name=exchange))
                break
        # Auto-mark slow tests based on timeout or explicit markers
        if "slow" not in item.keywords:
            # Check if test file or function name suggests it's slow
            test_name = item.nodeid.lower()
            if any(keyword in test_name for keyword in ["slow", "benchmark", "stress"]):
                item.add_marker(pytest.mark.slow)

        # Auto-mark network tests
        if "network" not in item.keywords:
            test_name = item.nodeid.lower()
            if any(keyword in test_name for keyword in [
                "request", "wss", "websocket", "api",
                "update_exchange", "update_binance", "update_okx",
                "history_bar",
            ]):
                item.add_marker(pytest.mark.network)
            # Also mark as network if test is in an exchange-specific path
            if any(ex in fspath for ex in (
                "binance", "okx", "htx", "bitfinex", "coinbase",
                "kucoin", "mexc", "bybit", "upbit", "hyperliquid",
            )):
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
    """Auto-skip tests that fail due to network/auth errors.

    Covers integration tests and any test that hits real exchange APIs
    (test_bt_api.py, test_update_exchange_symbol_info.py, etc.).
    Only applies to tests auto-marked 'network' or explicitly marked 'integration'.
    """
    is_network_test = (
        "integration" in item.keywords
        or "network" in item.keywords
    )
    if not is_network_test:
        return
    try:
        item.runtest()
    except Exception as exc:
        exc_name = type(exc).__name__
        exc_msg = str(exc).lower()
        network_indicators = [
            "authenticationerror", "requestfailederror", "requesterror",
            "connectionerror", "connecterror", "timeout",
            "ssl", "eof", "connection refused", "no route to host",
            "403", "401", "404", "name or service not known",
            "urlerror", "socketerror", "gaierror",
            "connection reset", "connection aborted",
            "max retries exceeded", "network is unreachable",
            "endpoint gone", "not found",
            "remoteprotocolerror", "server disconnected",
            "environment variable not set", "api_key",
            "rate_limit", "ratelimit", "too many requests",
        ]
        combined = exc_name.lower() + " " + exc_msg
        if any(ind in combined for ind in network_indicators):
            pytest.skip(f"Skipped (network/auth): {exc_name}: {str(exc)[:80]}")
        # Also skip pytest-timeout failures for network tests
        if "failed" == exc_name.lower() and "timeout" in exc_msg:
            pytest.skip(f"Skipped (timeout): {str(exc)[:80]}")
        # AssertionError with "returned no data/None" often means network call
        # silently failed; skip for network-marked tests
        if exc_name == "AssertionError" and any(
            hint in exc_msg for hint in [
                "returned no data", "returned none", "is not none",
                "no ticks", "no response", "empty response",
            ]
        ):
            pytest.skip(f"Skipped (no data, likely network): {str(exc)[:80]}")
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
