"""
浏览器 Cookie 提取工具

从已登录的浏览器中提取 IBKR Gateway 的认证 cookie，支持:
1. 从浏览器 Cookie 数据库读取 (Chrome, Firefox, Safari, Edge)
2. 从手动提供的 Cookie 字符串解析
3. 从 Cookie 文件读取

依赖: pip install browser-cookie3
"""

import json
from pathlib import Path
from urllib.parse import urlparse


def extract_cookie_string(cookie_str: str) -> dict[str, str]:
    """从 Cookie 字符串解析为字典
    Args:
        cookie_str: Cookie 字符串，格式如 "key1=value1; key2=value2"
    Returns:
        Dict[str, str]: Cookie 字典
    """
    cookies = {}
    if not cookie_str:
        return cookies

    # 简单解析
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


def get_cookies_from_browser(
    domain: str = "localhost:5000", browser: str = "chrome", path: str = None
) -> dict[str, str]:
    """从浏览器提取指定域名的 cookies

    Args:
        domain: 目标域名，如 "localhost:5000" 或 "api.interactivebrokers.com"
        browser: 浏览器类型 (chrome, firefox, safari, edge)
        path: Cookie 路径过滤 (如 "/sso" 用于 IBKR Gateway)

    Returns:
        Dict[str, str]: Cookie 字典
    """
    try:
        import browser_cookie3
    except ImportError:
        raise ImportError("browser-cookie3 is required. Install: pip install browser-cookie3")

    cookie_jar = None
    domain_parts = domain.split(":")[0]  # 去掉端口号

    try:
        if browser.lower() == "chrome":
            cookie_jar = browser_cookie3.chrome(domain_name=domain_parts)
        elif browser.lower() == "firefox":
            cookie_jar = browser_cookie3.firefox(domain_name=domain_parts)
        elif browser.lower() == "safari":
            cookie_jar = browser_cookie3.safari(domain_name=domain_parts)
        elif browser.lower() == "edge":
            cookie_jar = browser_cookie3.edge(domain_name=domain_parts)
        else:
            # 尝试所有浏览器
            for browser_func in [browser_cookie3.chrome, browser_cookie3.firefox]:
                try:
                    cookie_jar = browser_func(domain_name=domain_parts)
                    if cookie_jar:
                        break
                except Exception:
                    continue
    except Exception:
        # 如果失败，返回空字典
        return {}

    if not cookie_jar:
        return {}

    # 构建 cookie 字典
    cookies = {}
    for cookie in cookie_jar:
        # 如果指定了 path，只获取该路径的 cookies
        if path:
            # 对于 IBKR，/sso 路径的 cookie 是关键
            if path in cookie.path:
                cookies[cookie.name] = cookie.value
        else:
            cookies[cookie.name] = cookie.value

    return cookies


def get_cookies_from_file(file_path: str) -> dict[str, str]:
    """从 JSON 文件读取 cookies

    Args:
        file_path: Cookie 文件路径，JSON 格式: {"key1": "value1", "key2": "value2"}

    Returns:
        Dict[str, str]: Cookie 字典
    """
    file_path = Path(file_path).expanduser()
    if not file_path.exists():
        return {}

    try:
        with open(file_path) as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            elif isinstance(data, list):
                # 浏览器导出的格式可能是 list
                return {
                    item.get("name", item.get("key")): item.get("value")
                    for item in data
                    if isinstance(item, dict)
                }
    except Exception:
        return {}

    return {}


def get_cookies_from_netscape(file_path: str) -> dict[str, str]:
    """从 Netscape Cookie 格式文件读取 (curl -c 的输出格式)

    Args:
        file_path: Netscape 格式 cookie 文件路径

    Returns:
        Dict[str, str]: Cookie 字典
    """
    file_path = Path(file_path).expanduser()
    if not file_path.exists():
        return {}

    cookies = {}
    try:
        with open(file_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    # Netscape 格式: domain \t flag \t path \t secure \t expiration \t name \t value
                    name = parts[5]
                    value = parts[6]
                    cookies[name] = value
    except Exception:
        pass

    return cookies


def get_ibkr_cookies(
    base_url: str = "https://localhost:5000",
    cookie_source: str | None = None,
    browser: str = "chrome",
    cookie_path: str = "/sso",
) -> dict[str, str]:
    """获取 IBKR Gateway 所需的 cookies

    Args:
        base_url: Gateway URL，如 "https://localhost:5000"
        cookie_source: Cookie 来源
            - None: 自动从浏览器读取
            - "browser": 从浏览器读取
            - "env": 从环境变量 IB_WEB_COOKIES 读取
            - "file:<path>": 从文件读取，如 "file:/path/to/cookies.json"
            - 字符串: 直接是 cookie 字符串
        browser: 浏览器类型 (当 cookie_source="browser" 时使用)
        cookie_path: Cookie 路径，默认 "/sso" (IBKR Gateway 的认证路径)

    Returns:
        Dict[str, str]: Cookie 字典
    """
    cookies = {}

    if cookie_source is None or cookie_source == "browser":
        cookies = get_cookies_from_browser(
            domain=urlparse(base_url).netloc, browser=browser, path=cookie_path
        )
    elif cookie_source == "env":
        import os

        cookie_str = os.environ.get("IB_WEB_COOKIES", "")
        cookies = extract_cookie_string(cookie_str)
    elif cookie_source.startswith("file:"):
        file_path = cookie_source[5:]
        if file_path.endswith(".txt"):
            cookies = get_cookies_from_netscape(file_path)
        else:
            cookies = get_cookies_from_file(file_path)
    elif isinstance(cookie_source, str):
        # 直接是 cookie 字符串
        cookies = extract_cookie_string(cookie_source)

    return cookies


def save_cookies_to_file(cookies: dict[str, str], file_path: str):
    """将 cookies 保存到 JSON 文件

    Args:
        cookies: Cookie 字典
        file_path: 目标文件路径
    """
    file_path = Path(file_path).expanduser()
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, "w") as f:
        json.dump(cookies, f, indent=2)


def export_browser_cookies_to_file(
    output_path: str, domain: str = "localhost:5000", browser: str = "chrome"
):
    """从浏览器导出 cookies 到文件

    Args:
        output_path: 输出文件路径
        domain: 目标域名
        browser: 浏览器类型
    """
    cookies = get_cookies_from_browser(domain=domain, browser=browser)
    save_cookies_to_file(cookies, output_path)
    return cookies


def cookies_to_header(cookies: dict[str, str]) -> str:
    """将 Cookie 字典转换为 HTTP Header 格式

    Args:
        cookies: Cookie 字典

    Returns:
        str: Cookie Header 字符串，如 "key1=value1; key2=value2"
    """
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


if __name__ == "__main__":
    # 测试代码
    import sys

    if len(sys.argv) > 1:
        # 从浏览器导出 cookies
        output_file = sys.argv[1]
        cookies = export_browser_cookies_to_file(output_file)
        print(f"Exported {len(cookies)} cookies to {output_file}")
    else:
        # 打印当前可用的 cookies
        cookies = get_ibkr_cookies()
        print(f"Found {len(cookies)} cookies for localhost:5000:")
        for name, value in cookies.items():
            # 隐藏过长的值
            display_value = value[:20] + "..." if len(value) > 20 else value
            print(f"  {name}: {display_value}")
