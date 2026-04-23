#!/usr/bin/env python3
"""
IBKR Gateway Cookie 提取工具

用法:
1. 在浏览器中打开 https://localhost:5000 并登录
2. 打开浏览器开发者工具 (F12)
3. 切换到 Application/应用程序 标签
4. 左侧找到 Cookies -> https://localhost:5000
5. 复制所有 cookie 值

或者使用此脚本自动从浏览器提取:
    python3 scripts/get_ibkr_cookie.py
"""

import json
import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def get_cookie_from_browser():
    """尝试从浏览器获取 IBKR Gateway cookie"""
    try:
        import browser_cookie3
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # 尝试连接 Gateway 验证 cookie
        import requests

        # 尝试每个可能的浏览器
        browsers = [
            ("Chrome", browser_cookie3.chrome),
            ("Firefox", browser_cookie3.firefox),
            ("Safari", browser_cookie3.safari),
            ("Edge", browser_cookie3.edge),
        ]

        for browser_name, browser_func in browsers:
            try:
                cj = browser_func(domain_name="localhost")
                # 构建 cookie 字典
                cookies = {cookie.name: cookie.value for cookie in cj}

                # 测试这些 cookies 是否对 Gateway 有效
                test_url = "https://localhost:5000/v1/api/portfolio/accounts"
                try:
                    response = requests.get(
                        test_url,
                        cookies=cookies,
                        verify=False,
                        timeout=5,
                        proxies={"http": None, "https": None},
                    )
                    if response.status_code == 200:
                        print(f"✓ 从 {browser_name} 找到有效的 cookies!")
                        return cookies
                except requests.exceptions.RequestException:
                    continue
            except Exception:
                continue

        print("✗ 未找到有效的 IBKR Gateway cookies")
        return None

    except ImportError:
        print("✗ browser-cookie3 未安装，请运行: pip install browser-cookie3")
        return None


def get_cookie_manual():
    """手动输入 cookie"""
    print("\n请手动从浏览器获取 cookie:")
    print("1. 在浏览器中打开 https://localhost:5000 并登录")
    print("2. 按 F12 打开开发者工具")
    print("3. 切换到 Network/网络 标签")
    print("4. 刷新页面，点击任意请求")
    print("5. 在 Request Headers 中找到 'Cookie:' 行")
    print("6. 复制整个 Cookie 值\n")

    cookie_str = input("请粘贴 Cookie 字符串: ").strip()

    if not cookie_str:
        return None

    from bt_api_py.functions.browser_cookies import extract_cookie_string

    return extract_cookie_string(cookie_str)


def save_cookie_to_file(cookies, output_path=None):
    """保存 cookies 到文件"""
    if output_path is None:
        project_root = Path(__file__).parent.parent
        output_path = project_root / "configs" / "ibkr_cookies.json"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(cookies, f, indent=2)

    print(f"\n✓ Cookies 已保存到: {output_path}")
    return output_path


def update_env_file(cookie_file_path):
    """更新 .env 文件"""
    project_root = Path(__file__).parent.parent
    env_file = project_root / ".env"

    cookie_source = f"file:{cookie_file_path}"

    if env_file.exists():
        with open(env_file) as f:
            lines = f.readlines()

        # 更新或添加 IB_WEB_COOKIE_SOURCE
        updated = False
        for i, line in enumerate(lines):
            if line.startswith("IB_WEB_COOKIE_SOURCE="):
                lines[i] = f"IB_WEB_COOKIE_SOURCE={cookie_source}\n"
                updated = True
                break

        if not updated:
            lines.append(f"\nIB_WEB_COOKIE_SOURCE={cookie_source}\n")

        with open(env_file, "w") as f:
            f.writelines(lines)

        print(f"✓ 已更新 .env 文件，设置 IB_WEB_COOKIE_SOURCE={cookie_source}")
    else:
        print("✗ .env 文件不存在，请手动添加:")
        print(f"  IB_WEB_COOKIE_SOURCE={cookie_source}")


def main():
    print("=" * 60)
    print("IBKR Gateway Cookie 提取工具")
    print("=" * 60)

    cookies = None

    # 尝试自动从浏览器获取
    print("\n[1] 尝试从浏览器自动提取...")
    cookies = get_cookie_from_browser()

    if not cookies:
        # 手动输入
        print("\n[2] 自动提取失败，请手动输入...")
        cookies = get_cookie_manual()

    if not cookies:
        print("\n✗ 无法获取 cookies")
        return 1

    print(f"\n获取到 {len(cookies)} 个 cookies:")
    for name, value in cookies.items():
        # 隐藏过长的值
        display_value = value[:30] + "..." if len(value) > 30 else value
        print(f"  - {name}: {display_value}")

    # 保存到文件
    cookie_file = save_cookie_to_file(cookies)

    # 更新 .env
    update_env_file(str(cookie_file))

    print("\n" + "=" * 60)
    print("完成! 现在可以运行测试了:")
    print("  pytest tests/feeds/test_live_ib_web_request_data.py -v")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
