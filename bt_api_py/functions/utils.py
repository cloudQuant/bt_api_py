import importlib
import os
import sys
from pathlib import Path

import requests
import yaml
from dotenv import find_dotenv, load_dotenv

# Lazy import to avoid circular dependency
# from bt_api_py.logging_factory import get_logger
# logger = get_logger("function")


def _get_logger():
    """Lazy logger initialization to avoid circular imports."""
    from bt_api_py.logging_factory import get_logger

    return get_logger("function")


def get_public_ip():
    try:
        # 使用一个查询公共 IP 地址的服务
        response = requests.get("https://api.ipify.org", timeout=10)
        # 如果请求成功，返回响应的文本内容，即当前设备的公共 IP 地址
        if response.status_code == 200:
            return response.text
    except Exception as e:
        _get_logger().error(f"Error occurred: {e}", exc_info=True)

        try:
            response = requests.get("https://api.myip.com", timeout=10)
            response.raise_for_status()  # 检查请求是否成功
            data = response.json()
            return data.get("ip")
        except requests.RequestException as e:
            _get_logger().error(f"Error fetching IP: {e}", exc_info=True)

            return None
    # 如果发生任何异常或请求失败，返回 None
    return None


def get_package_path(package_name="lv"):
    """获取包的路径值
    :param package_name: 包的名称
    :return: 返回的路径值
    """
    try:
        importlib.import_module(package_name)
        package = sys.modules[package_name]
    except KeyError:
        _get_logger().error(f"Package {package_name} not found")

        return None
    if package.__file__ is not None:
        return os.path.dirname(package.__file__)
    else:
        return package.__path__.__dict__["_path"][0]


def get_project_log_path(log_filename):
    """获取项目根目录下 logs/ 文件夹中的日志路径。
    项目根目录 = bt_api_py 包的上一级目录。
    :param log_filename: 日志文件名 (e.g. "htx_spot_feed.log")
    :return: 完整日志路径 (e.g. "/path/to/project/logs/htx_spot_feed.log")
    """
    package_path = get_package_path("bt_api_py")
    project_root = str(Path(package_path).parent) if package_path else os.getcwd()
    log_dir = os.path.join(project_root, "logs")
    return os.path.join(log_dir, log_filename)


def read_yaml_file(file_name, data_root=None):
    """读取放在btpy根目录中的yaml文件
    :param data_root: 文件所在目录
    :param file_name: 文件名称
    :return: 返回的yaml文件的内容
    """
    if data_root is None:
        package_path = get_package_path("bt_api_py")
        file_path = package_path + "/configs/" + file_name
    else:
        file_path = data_root + "/configs/" + file_name
    with open(file_path) as file:
        file_content = yaml.load(file, Loader=yaml.FullLoader)
    return file_content


def read_account_config():
    """从 .env 文件读取账号配置，返回与 read_yaml_file("account_config.yaml") 相同格式的字典。
    .env 文件查找顺序: 项目根目录（bt_api_py 包的上一级）、当前工作目录。
    """
    # 尝试从项目根目录加载 .env
    env_loaded = False
    package_path = get_package_path("bt_api_py")
    if package_path:
        project_root = Path(package_path).parent
        env_file = project_root / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=True)
            env_loaded = True
    if not env_loaded:
        # 回退: 从当前工作目录向上查找 .env
        load_dotenv(find_dotenv(usecwd=True), override=True)

    # 代理配置
    http_proxy = os.environ.get("HTTP_PROXY", "") or os.environ.get("http_proxy", "")
    https_proxy = os.environ.get("HTTPS_PROXY", "") or os.environ.get("https_proxy", "")
    proxies = None
    async_proxy = None
    if http_proxy or https_proxy:
        proxies = {}
        if http_proxy:
            proxies["http"] = http_proxy
        if https_proxy:
            proxies["https"] = https_proxy
        async_proxy = https_proxy or http_proxy

    config = {
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
            "port": int(os.environ.get("IB_PORT", "7497")),
            "client_id": int(os.environ.get("IB_CLIENT_ID", "1")),
        },
        "ib_web": {
            "base_url": os.environ.get("IB_WEB_BASE_URL", "https://localhost:5000"),
            "account_id": os.environ.get("IB_WEB_ACCOUNT_ID", ""),
            "verify_ssl": os.environ.get("IB_WEB_VERIFY_SSL", "false").lower() == "true",
            "timeout": int(os.environ.get("IB_WEB_TIMEOUT", "10")),
            "access_token": os.environ.get("IB_WEB_ACCESS_TOKEN", ""),
            "client_id": os.environ.get("IB_WEB_CLIENT_ID", ""),
            "private_key_path": os.environ.get("IB_WEB_PRIVATE_KEY_PATH", ""),
            "username": os.environ.get("IB_WEB_USERNAME", ""),
            "password": os.environ.get("IB_WEB_PASSWORD", ""),
            "login_mode": os.environ.get("IB_WEB_LOGIN_MODE", "paper"),
            "login_browser": os.environ.get("IB_WEB_LOGIN_BROWSER", "chrome"),
            "login_headless": os.environ.get("IB_WEB_LOGIN_HEADLESS", "false").lower()
            == "true",
            "login_timeout": int(os.environ.get("IB_WEB_LOGIN_TIMEOUT", "180")),
            "cookie_output": os.environ.get("IB_WEB_COOKIE_OUTPUT", ""),
            "cookie_source": os.environ.get("IB_WEB_COOKIE_SOURCE", ""),
            "cookie_browser": os.environ.get("IB_WEB_COOKIE_BROWSER", "chrome"),
            "cookie_path": os.environ.get("IB_WEB_COOKIE_PATH", "/sso"),
        },
        "proxies": proxies,
        "async_proxy": async_proxy,
    }
    return config


def update_extra_data(extra_data, **kwargs):
    """
    update extra_data using kwargs
    :param extra_data: extra_data is None or dict
    :param kwargs: kwargs is dict
    :return: extra_data, dict
    """
    if extra_data is None:
        extra_data = kwargs
    else:
        extra_data.update(kwargs)
    return extra_data


def from_dict_get_string(content, key, default=None):
    if key not in content:
        return default
    else:
        val = content[key]
        if isinstance(val, str):
            return val
        else:
            return str(val)


def from_dict_get_bool(content, key, default=None):
    if key not in content:
        return default
    else:
        value = content[key]
        if isinstance(value, bool):
            return value
        elif isinstance(value, str):
            return value == "true"
        else:
            raise TypeError(f"value {value} is not considered")


def from_dict_get_float(content, key, default=None):
    if key not in content:
        return default
    value = content[key]
    if value == "" or value is None:
        return None
    elif isinstance(value, float):
        return value
    else:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


def from_dict_get_int(content, key, default=None):
    if key not in content:
        return default
    value = content[key]
    if value == "" or value is None:
        return None
    elif isinstance(value, int):
        return value
    else:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


if __name__ == "__main__":
    print(get_package_path("bt_api_py"))
    # 获取并打印当前设备的公共 IP 地址
    public_ip = get_public_ip()
    if public_ip:
        print(f"Public IP address: {public_ip}")
    else:
        print("Failed to retrieve public IP address.")
