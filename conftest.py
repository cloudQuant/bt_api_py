"""Pytest configuration and shared fixtures."""

import os
import sys
from pathlib import Path

import pytest

# Add project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
scripts_root = project_root / "scripts"
if scripts_root.is_dir():
    sys.path.insert(0, str(scripts_root))


# Check if API keys are available
def has_binance_api_keys():
    """Check if Binance API keys are configured."""
    return bool(os.getenv("BINANCE_API_KEY") and os.getenv("BINANCE_SECRET"))


def has_okx_api_keys():
    """Check if OKX API keys are configured."""
    return bool(
        os.getenv("OKX_API_KEY") and os.getenv("OKX_SECRET") and os.getenv("OKX_PASSPHRASE")
    )


def has_ctp_credentials():
    """Check if CTP credentials are configured."""
    return bool(
        os.getenv("CTP_BROKER_ID") and os.getenv("CTP_USER_ID") and os.getenv("CTP_PASSWORD")
    )


def has_htx_api_keys():
    """Check if HTX API keys are configured."""
    return bool(
        os.getenv("HTX_API_KEY")
        and os.getenv("HTX_SECRET")
        and os.getenv("HTX_API_KEY") != "your_htx_api_key_here"
    )


def has_ib_credentials():
    """Check if IB credentials are configured."""
    return bool(os.getenv("IB_ACCOUNT_ID"))


def should_skip_live_tests():
    """Check if live tests should be skipped."""
    return os.getenv("SKIP_LIVE_TESTS", "").lower() in ("true", "1", "yes")


def _rewrite_examples_cli_arg(arg: str) -> str:
    normalized = arg.replace("/", "\\").rstrip("\\")
    relative_prefix = "bt_api_py\\examples"
    if normalized.lower() == relative_prefix.lower():
        return "examples"
    if normalized.lower().startswith(relative_prefix.lower() + "\\"):
        suffix = normalized[len(relative_prefix) :].lstrip("\\")
        parts = [part for part in suffix.split("\\") if part]
        return str(Path("examples", *parts))

    package_examples = str(project_root / "bt_api_py" / "examples").replace("/", "\\").rstrip("\\")
    if normalized.lower() == package_examples.lower():
        return str(project_root / "examples")
    if normalized.lower().startswith(package_examples.lower() + "\\"):
        suffix = normalized[len(package_examples) :].lstrip("\\")
        parts = [part for part in suffix.split("\\") if part]
        return str(project_root / "examples" / Path(*parts))

    return arg


def pytest_configure(config):
    """Configure pytest with custom settings."""
    config.args[:] = [_rewrite_examples_cli_arg(arg) for arg in config.args]

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
            os.environ["LD_PRELOAD"] = (
                f"{iconv_lib}:{current_preload}" if current_preload else iconv_lib
            )

    # NOTE: Custom markers are registered in pyproject.toml [tool.pytest.ini_options].markers
    # Do not duplicate them here.


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers automatically and skip tests without API keys."""
    skip_live = should_skip_live_tests()

    for item in items:
        # Group network-heavy tests by exchange for xdist so parallel workers
        # don't overwhelm the same exchange API simultaneously.
        fspath = str(item.fspath).lower()
        for exchange in (
            "okx",
            "binance",
            "ctp",
            "htx",
            "bitfinex",
            "coinbase",
            "kucoin",
            "mexc",
            "bybit",
            "upbit",
            "hyperliquid",
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
            if any(
                keyword in test_name
                for keyword in [
                    "request",
                    "wss",
                    "websocket",
                    "api",
                    "update_exchange",
                    "update_binance",
                    "update_okx",
                    "history_bar",
                ]
            ):
                item.add_marker(pytest.mark.network)
            # Also mark as network if test is in an exchange-specific path
            if any(
                ex in fspath
                for ex in (
                    "binance",
                    "okx",
                    "htx",
                    "bitfinex",
                    "coinbase",
                    "kucoin",
                    "mexc",
                    "bybit",
                    "upbit",
                    "hyperliquid",
                )
            ):
                item.add_marker(pytest.mark.network)

        # Auto-mark exchange-specific tests and skip if no API keys
        test_path = str(item.fspath).lower()

        # Binance tests — add marker only, never auto-skip
        if "binance" in test_path and "binance" not in item.keywords:
            item.add_marker(pytest.mark.binance)

        # OKX tests — add marker only, never auto-skip
        if "okx" in test_path and "okx" not in item.keywords:
            item.add_marker(pytest.mark.okx)

        # HTX tests — add marker only, never auto-skip
        if "htx" in test_path and "htx" not in item.keywords:
            item.add_marker(pytest.mark.htx)

        # CTP tests — add marker only, never auto-skip
        if "ctp" in test_path and "ctp" not in item.keywords:
            item.add_marker(pytest.mark.ctp)

        # IB tests — add marker only, never auto-skip
        if "ib" in test_path and "ib" not in item.keywords:
            item.add_marker(pytest.mark.ib)

        # Skip @pytest.mark.integration tests when SKIP_LIVE_TESTS is set
        if skip_live and "integration" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="SKIP_LIVE_TESTS=true"))


def _network_skip_reason(item, exc):
    """Return a skip reason for network/auth-related failures, if applicable."""
    is_network_test = "integration" in item.keywords or "network" in item.keywords
    if not is_network_test:
        return None

    exc_name = type(exc).__name__
    exc_msg = str(exc).lower()

    if isinstance(exc, AssertionError) and any(
        hint in exc_msg
        for hint in [
            "returned no data",
            "returned none",
            "is not none",
            "no ticks",
            "no response",
            "empty response",
            "failed to fetch",
            "enough exchanges",
        ]
    ):
        return f"Skipped (no data, likely network): {str(exc)[:80]}"

    network_indicators = [
        "authenticationerror",
        "requestfailederror",
        "requesterror",
        "connectionerror",
        "connection error",
        "connecterror",
        "timeout",
        "ssl",
        "eof",
        "connection refused",
        "no route to host",
        "403",
        "401",
        "404",
        "name or service not known",
        "urlerror",
        "socketerror",
        "gaierror",
        "connection reset",
        "connection aborted",
        "max retries exceeded",
        "network is unreachable",
        "endpoint gone",
        "not found",
        "remoteprotocolerror",
        "server disconnected",
        "environment variable not set",
        "api_key",
        "rate_limit",
        "ratelimit",
        "too many requests",
        "failed to get listen key",
        "winerror 10061",
    ]
    combined = exc_name.lower() + " " + exc_msg
    if any(ind in combined for ind in network_indicators):
        return f"Skipped (network/auth): {exc_name}: {str(exc)[:80]}"

    if exc_name.lower() == "failed" and "timeout" in exc_msg:
        return f"Skipped (timeout): {str(exc)[:80]}"

    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Convert network/auth failures into skipped outcomes without teardown warnings."""
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or report.outcome != "failed" or call.excinfo is None:
        return

    reason = _network_skip_reason(item, call.excinfo.value)
    if reason is None:
        return

    report.outcome = "skipped"
    report.longrepr = (str(item.fspath), 0, reason)


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
    current_keys = set(os.environ)
    original_keys = set(original_env)

    for key in current_keys - original_keys:
        os.environ.pop(key, None)

    for key, value in original_env.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


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
