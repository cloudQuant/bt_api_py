"""Shared utility helpers for package paths, config loading, and dict parsing."""

from __future__ import annotations

import importlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import requests
import yaml
from dotenv import find_dotenv, load_dotenv


def _get_logger() -> Any:
    """Lazy logger initialization to avoid circular imports."""
    from bt_api_py.logging_factory import get_logger

    return get_logger("function")


_TRUTHY_STRINGS = {"true", "1", "yes", "y", "on"}
_FALSY_STRINGS = {"false", "0", "no", "n", "off"}


def _parse_env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        _get_logger().warning(f"Invalid integer env {name}={value!r}, using default {default}")
        return default


def _parse_env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value in (None, ""):
        return default

    normalized = value.strip().lower()
    if normalized in _TRUTHY_STRINGS:
        return True
    if normalized in _FALSY_STRINGS:
        return False

    _get_logger().warning(f"Invalid boolean env {name}={value!r}, using default {default}")
    return default


def get_public_ip() -> str | None:
    """Return the current public IP address, or ``None`` if both services fail."""
    try:
        response = requests.get("https://api.ipify.org", timeout=10)
        if response.status_code == 200:
            ip_text = response.text.strip()
            if ip_text:
                return ip_text
    except requests.RequestException as exc:
        _get_logger().error(f"Error occurred: {exc}")

    try:
        response = requests.get("https://api.myip.com", timeout=10)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, Mapping):
            ip = data.get("ip")
            return ip if isinstance(ip, str) else None
    except (requests.RequestException, ValueError) as exc:
        _get_logger().error(f"Error fetching IP: {exc}")
    return None


def get_package_path(package_name: str = "lv") -> str | None:
    """Return the filesystem path of an importable package."""
    try:
        package = importlib.import_module(package_name)
    except ModuleNotFoundError:
        _get_logger().error(f"Package {package_name} not found")
        return None

    package_file = getattr(package, "__file__", None)
    if package_file is not None:
        return str(Path(package_file).parent)

    package_paths = getattr(package, "__path__", None)
    if package_paths is None:
        return None

    for package_path in package_paths:
        return str(package_path)
    return None


def _resolve_config_root(data_root: str | Path | None = None) -> Path:
    if data_root is not None:
        return Path(data_root)

    package_path = get_package_path("bt_api_py")
    if package_path is None:
        raise RuntimeError("Package path for 'bt_api_py' is not available")
    return Path(package_path)


def get_project_log_path(log_filename: str) -> str:
    """Return the path to a log file under the project-level ``logs`` directory."""
    package_path = get_package_path("bt_api_py")
    project_root = Path(package_path).parent if package_path else Path.cwd()
    return str(project_root / "logs" / log_filename)


def read_yaml_file(file_name: str, data_root: str | Path | None = None) -> Any:
    """Read a YAML file from the package ``configs`` directory."""
    file_path = _resolve_config_root(data_root) / "configs" / file_name
    with file_path.open(encoding="utf-8") as file:
        return yaml.load(file, Loader=yaml.FullLoader)


def read_account_config() -> dict[str, Any]:
    """Load account configuration from ``.env`` using the project's expected schema."""
    env_loaded = False
    package_path = get_package_path("bt_api_py")
    if package_path:
        project_root = Path(package_path).parent
        env_file = project_root / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=True)
            env_loaded = True

    if not env_loaded:
        load_dotenv(find_dotenv(usecwd=True), override=True)

    http_proxy = os.environ.get("HTTP_PROXY", "") or os.environ.get("http_proxy", "")
    https_proxy = os.environ.get("HTTPS_PROXY", "") or os.environ.get("https_proxy", "")

    proxies: dict[str, str] | None = None
    async_proxy: str | None = None
    if http_proxy or https_proxy:
        proxies = {}
        if http_proxy:
            proxies["http"] = http_proxy
        if https_proxy:
            proxies["https"] = https_proxy
        async_proxy = https_proxy or http_proxy

    return {
        "okx": {
            "public_key": os.environ.get("OKX_API_KEY", ""),
            "private_key": os.environ.get("OKX_SECRET", ""),
            "passphrase": os.environ.get("OKX_PASSWORD", ""),
        },
        "binance": {
            "public_key": os.environ.get("BINANCE_API_KEY", ""),
            "private_key": os.environ.get("BINANCE_PASSWORD", ""),
        },
        "htx": {
            "public_key": os.environ.get("HTX_API_KEY", ""),
            "private_key": os.environ.get("HTX_SECRET", ""),
        },
        "ctp": {
            "broker_id": os.environ.get("CTP_BROKER_ID", "9999"),
            "user_id": os.environ.get("CTP_USER_ID", ""),
            "password": os.environ.get("CTP_PASSWORD", ""),
            "auth_code": os.environ.get("CTP_AUTH_CODE", ""),
            "app_id": os.environ.get("CTP_APP_ID", "simnow_client_test"),
            "md_front": os.environ.get("CTP_MD_FRONT", ""),
            "td_front": os.environ.get("CTP_TD_FRONT", ""),
        },
        "ib": {
            "host": os.environ.get("IB_HOST", "127.0.0.1"),
            "port": _parse_env_int("IB_PORT", 7497),
            "client_id": _parse_env_int("IB_CLIENT_ID", 1),
        },
        "ib_web": {
            "base_url": os.environ.get("IB_WEB_BASE_URL", "https://localhost:5000"),
            "account_id": os.environ.get("IB_WEB_ACCOUNT_ID", ""),
            "verify_ssl": _parse_env_bool("IB_WEB_VERIFY_SSL", False),
            "timeout": _parse_env_int("IB_WEB_TIMEOUT", 10),
            "access_token": os.environ.get("IB_WEB_ACCESS_TOKEN", ""),
            "client_id": os.environ.get("IB_WEB_CLIENT_ID", ""),
            "private_key_path": os.environ.get("IB_WEB_PRIVATE_KEY_PATH", ""),
            "cookie_source": os.environ.get("IB_WEB_COOKIE_SOURCE", ""),
            "cookie_browser": os.environ.get("IB_WEB_COOKIE_BROWSER", "chrome"),
        },
        "proxies": proxies,
        "async_proxy": async_proxy,
    }


def update_extra_data(extra_data: dict[str, Any] | None, **kwargs: Any) -> dict[str, Any]:
    """Merge ``kwargs`` into ``extra_data`` and return the resulting mapping."""
    updated = dict(extra_data) if extra_data is not None else {}
    updated.update(kwargs)
    return updated


def from_dict_get_string(
    content: Mapping[Any, Any], key: Any, default: str | None = None
) -> str | None:
    if key not in content:
        return default
    value = content[key]
    return value if isinstance(value, str) else str(value)


def from_dict_get_bool(
    content: Mapping[Any, Any], key: Any, default: bool | None = None
) -> bool | None:
    if key not in content:
        return default
    value = content[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUTHY_STRINGS:
            return True
        if normalized in _FALSY_STRINGS:
            return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise TypeError(f"value {value} is not considered")


def from_dict_get_float(
    content: Mapping[Any, Any], key: Any, default: float | None = None
) -> float | None:
    if key not in content:
        return default
    value = content[key]
    if value == "" or value is None:
        return None
    if isinstance(value, float):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def from_dict_get_int(
    content: Mapping[Any, Any], key: Any, default: int | None = None
) -> int | None:
    if key not in content:
        return default
    value = content[key]
    if value == "" or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _main() -> None:
    package_path = get_package_path("bt_api_py")
    print(package_path)
    public_ip = get_public_ip()
    if public_ip:
        print(f"Public IP address: {public_ip}")
    else:
        print("Failed to retrieve public IP address.")


if __name__ == "__main__":
    _main()
