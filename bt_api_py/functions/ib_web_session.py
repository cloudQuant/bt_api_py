from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import requests
import urllib3
from dotenv import dotenv_values

from bt_api_py.functions.browser_cookies import get_ibkr_cookies, save_cookies_to_file
from bt_api_py.logging_factory import get_logger

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
_logger = get_logger("ib_web_session")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_local_path(path_value: str | Path, base_dir: str | Path | None = None) -> Path:
    raw_path = Path(path_value).expanduser()
    if raw_path.is_absolute():
        return raw_path
    base_path = Path(base_dir).expanduser() if base_dir else project_root()
    return (base_path / raw_path).resolve()


def to_relative_path(path_value: str | Path, base_dir: str | Path | None = None) -> str:
    target = resolve_local_path(path_value, base_dir=base_dir)
    root = Path(base_dir).expanduser().resolve() if base_dir else project_root().resolve()
    try:
        rel_path = target.relative_to(root)
        return rel_path.as_posix()
    except ValueError:
        return os.path.relpath(target, root).replace("\\", "/")


def normalize_cookie_source(cookie_source: str | None, base_dir: str | Path | None = None) -> str:
    value = str(cookie_source or "").strip()
    if not value:
        return ""
    if value in {"browser", "env"}:
        return value
    if ";" in value and "=" in value:
        return value
    path_value = value[5:] if value.startswith("file:") else value
    resolved = resolve_local_path(path_value, base_dir=base_dir)
    return f"file:{resolved}"


def default_cookie_output(base_dir: str | Path | None = None) -> Path:
    return resolve_local_path(Path("configs") / "ibkr_cookies.json", base_dir=base_dir)


def load_ib_web_settings(
    overrides: dict[str, Any] | None = None,
    base_dir: str | Path | None = None,
    env_file: str | Path | None = None,
) -> dict[str, Any]:
    env_path = (
        resolve_local_path(env_file, base_dir=base_dir) if env_file else project_root() / ".env"
    )
    env_values = dotenv_values(env_path)
    data = dict(overrides or {})

    def pick(name: str, default: Any = "") -> Any:
        value = data.get(name)
        if value not in (None, ""):
            return value
        env_value = os.environ.get(name)
        if env_value not in (None, ""):
            return env_value
        file_value = env_values.get(name)
        if file_value not in (None, ""):
            return file_value
        return default

    def pick_bool(name: str, default: bool = False) -> bool:
        value = pick(name, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def pick_int(name: str, default: int) -> int:
        value = pick(name, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    resolved_base_dir = Path(base_dir).expanduser() if base_dir else project_root()
    cookie_output_value = str(pick("cookie_output", pick("IB_WEB_COOKIE_OUTPUT", "")) or "").strip()
    cookie_source_value = str(pick("cookie_source", pick("IB_WEB_COOKIE_SOURCE", "")) or "").strip()
    cookie_output_path = (
        resolve_local_path(cookie_output_value, base_dir=resolved_base_dir)
        if cookie_output_value
        else default_cookie_output(base_dir=resolved_base_dir)
    )
    normalized_cookie_source = normalize_cookie_source(
        cookie_source_value, base_dir=resolved_base_dir
    )
    return {
        "base_url": str(
            pick("base_url", pick("IB_WEB_BASE_URL", "https://localhost:5000/v1/api"))
        ).strip(),
        "account_id": str(pick("account_id", pick("IB_WEB_ACCOUNT_ID", ""))).strip(),
        "verify_ssl": pick_bool("verify_ssl", pick_bool("IB_WEB_VERIFY_SSL", False)),
        "timeout": pick_int("timeout", pick_int("IB_WEB_TIMEOUT", 10)),
        "cookie_source": normalized_cookie_source,
        "cookie_browser": str(
            pick("cookie_browser", pick("IB_WEB_COOKIE_BROWSER", "chrome"))
        ).strip()
        or "chrome",
        "cookie_path": str(pick("cookie_path", pick("IB_WEB_COOKIE_PATH", "/sso"))).strip()
        or "/sso",
        "username": str(pick("username", pick("IB_WEB_USERNAME", ""))).strip(),
        "password": str(pick("password", pick("IB_WEB_PASSWORD", ""))).strip(),
        "login_mode": str(pick("login_mode", pick("IB_WEB_LOGIN_MODE", "paper"))).strip().lower()
        or "paper",
        "login_browser": str(pick("login_browser", pick("IB_WEB_LOGIN_BROWSER", "chrome")))
        .strip()
        .lower()
        or "chrome",
        "login_headless": pick_bool("login_headless", pick_bool("IB_WEB_LOGIN_HEADLESS", False)),
        "login_timeout": pick_int("login_timeout", pick_int("IB_WEB_LOGIN_TIMEOUT", 180)),
        "cookie_output": str(cookie_output_path),
        "cookie_output_relative": to_relative_path(cookie_output_path, base_dir=resolved_base_dir),
        "cookie_base_dir": str(resolved_base_dir),
        "env_file": str(env_path),
    }


def gateway_origin(base_url: str) -> str:
    value = str(base_url or "").rstrip("/")
    if value.endswith("/v1/api"):
        return value[:-7]
    return value


def api_base_url(base_url: str) -> str:
    value = str(base_url or "").rstrip("/")
    if value.endswith("/v1/api"):
        return value
    return value + "/v1/api"


def auth_status(
    base_url: str, cookies: dict[str, str], verify_ssl: bool = False, timeout: int = 10
) -> requests.Response:
    return requests.post(
        f"{api_base_url(base_url)}/iserver/auth/status",
        cookies=cookies,
        verify=verify_ssl,
        timeout=timeout,
        proxies={"http": None, "https": None},
    )


def auth_response_is_authenticated(response: requests.Response) -> bool:
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("authenticated", False) or payload.get("connected", False))


def fetch_accounts(
    base_url: str, cookies: dict[str, str], verify_ssl: bool = False, timeout: int = 10
) -> list[dict[str, Any]]:
    response = requests.get(
        f"{api_base_url(base_url)}/portfolio/accounts",
        cookies=cookies,
        verify=verify_ssl,
        timeout=timeout,
        proxies={"http": None, "https": None},
    )
    if response.status_code != 200:
        return []
    data = response.json()
    return data if isinstance(data, list) else []


def pick_account_id(accounts: list[dict[str, Any]], login_mode: str) -> str:
    if login_mode == "paper":
        for account in accounts:
            for key in ("accountId", "id", "accountIdKey"):
                value = str(account.get(key) or "")
                if value.upper().startswith("DU"):
                    return value
    for account in accounts:
        for key in ("accountId", "id", "accountIdKey"):
            value = str(account.get(key) or "")
            if value:
                return value
    return ""


def current_cookie_payload(settings: dict[str, Any]) -> dict[str, str]:
    source = str(settings.get("cookie_source") or "")
    if not source:
        return {}
    return get_ibkr_cookies(
        base_url=str(settings.get("base_url") or "https://localhost:5000/v1/api"),
        cookie_source=source,
        browser=str(settings.get("cookie_browser") or "chrome"),
        cookie_path=str(settings.get("cookie_path") or "/sso"),
    )


def cookies_are_authenticated(settings: dict[str, Any], cookies: dict[str, str]) -> bool:
    if not cookies:
        return False
    try:
        response = auth_status(
            str(settings.get("base_url") or "https://localhost:5000/v1/api"),
            cookies,
            verify_ssl=bool(settings.get("verify_ssl", False)),
            timeout=int(settings.get("timeout", 10)),
        )
    except requests.RequestException:
        return False
    return auth_response_is_authenticated(response)


def _first_visible(page, selectors: list[str]):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 5)
            for index in range(count):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    return candidate
        except Exception:
            continue
    return None


def _click_mode(page, login_mode: str) -> bool:
    try:
        paper_switch = page.locator("input[name='paperSwitch']")
        if paper_switch.count() > 0:
            checked = paper_switch.first.is_checked()
            if login_mode == "paper" and not checked:
                paper_switch.first.check(timeout=2000, force=True)
                return True
            if login_mode == "live" and checked:
                paper_switch.first.uncheck(timeout=2000, force=True)
                return True
            return False
    except Exception:
        pass
    patterns = [re.compile("paper", re.I), re.compile("demo", re.I), re.compile("sim", re.I)]
    if login_mode == "live":
        patterns = [re.compile("live", re.I), re.compile("real", re.I)]
    for pattern in patterns:
        for getter in (
            lambda pattern=pattern: page.get_by_text(pattern),
            lambda pattern=pattern: page.get_by_role("tab", name=pattern),
            lambda pattern=pattern: page.get_by_role("button", name=pattern),
            lambda pattern=pattern: page.get_by_role("radio", name=pattern),
            lambda pattern=pattern: page.get_by_label(pattern),
        ):
            try:
                locator = getter()
                if locator.count() > 0 and locator.first.is_visible():
                    locator.first.click(timeout=1500)
                    return True
            except Exception:
                continue
    try:
        select_locator = _first_visible(page, ["select"])
        if select_locator is None:
            return False
        text = select_locator.inner_text().lower()
        if login_mode == "paper":
            for option in ("paper", "demo", "sim"):
                if option in text:
                    try:
                        select_locator.select_option(label=re.compile(option, re.I))
                        return True
                    except Exception:
                        continue
        if login_mode == "live" and "live" in text:
            try:
                select_locator.select_option(label=re.compile("live", re.I))
                return True
            except Exception:
                return False
    except Exception:
        return False
    return False


def _fill_credentials(page, username: str, password: str) -> None:
    username_locator = _first_visible(
        page,
        [
            "input[name='username']",
            "input[id='username']",
            "input[autocomplete='username']",
            "input[type='email']",
            "input[type='text']",
            "input:not([type])",
        ],
    )
    password_locator = _first_visible(
        page,
        [
            "input[name='password']",
            "input[id='password']",
            "input[autocomplete='current-password']",
            "input[type='password']",
        ],
    )
    if username_locator is None or password_locator is None:
        raise RuntimeError("Unable to locate login form fields on Client Portal Gateway page")
    username_locator.fill(username)
    password_locator.fill(password)


def _submit_login(page) -> None:
    for getter in (
        lambda: page.get_by_role("button", name=re.compile("log ?in|sign ?in|continue", re.I)),
        lambda: page.get_by_text(re.compile("log ?in|sign ?in|continue", re.I)),
    ):
        try:
            locator = getter()
            if locator.count() > 0 and locator.first.is_visible():
                locator.first.click(timeout=3000)
                return
        except Exception:
            continue
    submit_locator = _first_visible(
        page, ["button[type='submit']", "input[type='submit']", "button"]
    )
    if submit_locator is None:
        raise RuntimeError("Unable to locate login submit button on Client Portal Gateway page")
    submit_locator.click(timeout=3000)


def _cookie_dict_from_context(context, url: str) -> dict[str, str]:
    cookie_items = context.cookies([url])
    return {
        str(item.get("name") or ""): str(item.get("value") or "")
        for item in cookie_items
        if item.get("name")
    }


def login_and_save_cookies(settings: dict[str, Any]) -> dict[str, Any]:
    username = str(settings.get("username") or "")
    password = str(settings.get("password") or "")
    if not username or not password:
        raise RuntimeError("IB_WEB_USERNAME and IB_WEB_PASSWORD are required")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("playwright is required. Install: pip install playwright") from None
    login_mode = str(settings.get("login_mode") or "paper")
    browser_name = str(settings.get("login_browser") or "chrome")
    launch_headless = bool(settings.get("login_headless", False))
    launch_timeout = int(settings.get("login_timeout", 180))
    base_url = str(settings.get("base_url") or "https://localhost:5000/v1/api")
    verify_ssl = bool(settings.get("verify_ssl", False))
    timeout = int(settings.get("timeout", 10))
    configured_account_id = str(settings.get("account_id") or "").strip()
    settle_timeout = max(int(settings.get("login_settle_timeout", 8) or 8), 0)
    cookie_output = str(
        settings.get("cookie_output")
        or default_cookie_output(base_dir=settings.get("cookie_base_dir"))
    )
    cookie_output_path = resolve_local_path(cookie_output, base_dir=settings.get("cookie_base_dir"))
    with sync_playwright() as playwright:
        browser_type = playwright.chromium
        launch_kwargs: dict[str, Any] = {"headless": launch_headless}
        if browser_name == "firefox":
            browser_type = playwright.firefox
        elif browser_name == "webkit":
            browser_type = playwright.webkit
        elif browser_name == "edge":
            launch_kwargs["channel"] = "msedge"
        elif browser_name == "chrome":
            launch_kwargs["channel"] = "chrome"
        try:
            browser = browser_type.launch(**launch_kwargs)
        except Exception:
            launch_kwargs.pop("channel", None)
            browser = browser_type.launch(**launch_kwargs)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.goto(gateway_origin(base_url), wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        mode_changed = _click_mode(page, login_mode)
        if mode_changed:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            page.wait_for_timeout(1000)
        _fill_credentials(page, username, password)
        page.wait_for_timeout(300)
        _submit_login(page)
        deadline = time.time() + launch_timeout
        cookies: dict[str, str] = {}
        last_status = None
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            cookies = _cookie_dict_from_context(context, gateway_origin(base_url))
            if not cookies:
                continue
            try:
                response = auth_status(base_url, cookies, verify_ssl=verify_ssl, timeout=timeout)
            except requests.RequestException:
                continue
            last_status = response.status_code
            if response.status_code == 200:
                try:
                    accounts = fetch_accounts(
                        base_url, cookies, verify_ssl=verify_ssl, timeout=timeout
                    )
                except requests.RequestException:
                    continue
                account_id = pick_account_id(accounts, login_mode) or configured_account_id
                if not account_id:
                    continue
                if settle_timeout > 0:
                    settle_deadline = min(time.time() + settle_timeout, deadline)
                    stable = False
                    while time.time() < settle_deadline:
                        page.wait_for_timeout(2000)
                        refreshed_cookies = _cookie_dict_from_context(
                            context, gateway_origin(base_url)
                        )
                        if not refreshed_cookies:
                            continue
                        try:
                            stable_response = auth_status(
                                base_url,
                                refreshed_cookies,
                                verify_ssl=verify_ssl,
                                timeout=timeout,
                            )
                        except requests.RequestException:
                            continue
                        if stable_response.status_code != 200:
                            continue
                        try:
                            stable_accounts = fetch_accounts(
                                base_url,
                                refreshed_cookies,
                                verify_ssl=verify_ssl,
                                timeout=timeout,
                            )
                        except requests.RequestException:
                            stable_accounts = []
                        stable_account_id = (
                            pick_account_id(stable_accounts, login_mode)
                            or account_id
                            or configured_account_id
                        )
                        if stable_account_id:
                            cookies = refreshed_cookies
                            account_id = stable_account_id
                            stable = True
                            break
                    if not stable:
                        continue
                save_cookies_to_file(cookies, str(cookie_output_path))
                browser.close()
                return {
                    "cookies": cookies,
                    "cookie_output": str(cookie_output_path),
                    "cookie_output_relative": to_relative_path(
                        cookie_output_path, base_dir=settings.get("cookie_base_dir")
                    ),
                    "cookie_source": normalize_cookie_source(
                        f"file:{cookie_output_path}", base_dir=settings.get("cookie_base_dir")
                    ),
                    "account_id": account_id,
                    "status_code": response.status_code,
                    "used_login": True,
                }
        browser.close()
    raise RuntimeError(f"Timed out waiting for authenticated session, last status={last_status}")


def ensure_authenticated_session(
    overrides: dict[str, Any] | None = None,
    base_dir: str | Path | None = None,
    env_file: str | Path | None = None,
) -> dict[str, Any]:
    settings = load_ib_web_settings(overrides=overrides, base_dir=base_dir, env_file=env_file)
    cookies = current_cookie_payload(settings)
    if cookies and cookies_are_authenticated(settings, cookies):
        account_id = str(settings.get("account_id") or "")
        if not account_id:
            accounts = fetch_accounts(
                str(settings.get("base_url") or "https://localhost:5000/v1/api"),
                cookies,
                verify_ssl=bool(settings.get("verify_ssl", False)),
                timeout=int(settings.get("timeout", 10)),
            )
            account_id = pick_account_id(accounts, str(settings.get("login_mode") or "paper"))
        cookie_output = str(
            settings.get("cookie_output")
            or default_cookie_output(base_dir=settings.get("cookie_base_dir"))
        )
        save_cookies_to_file(cookies, cookie_output)
        return {
            "cookies": cookies,
            "cookie_output": cookie_output,
            "cookie_output_relative": str(settings.get("cookie_output_relative") or ""),
            "cookie_source": normalize_cookie_source(
                f"file:{cookie_output}", base_dir=settings.get("cookie_base_dir")
            ),
            "account_id": account_id,
            "status_code": 200,
            "used_login": False,
        }
    return login_and_save_cookies(settings)


def upsert_env_file(env_path: str | Path, updates: dict[str, str]) -> None:
    path = Path(env_path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    for index, line in enumerate(lines):
        for key, value in list(remaining.items()):
            if line.startswith(f"{key}="):
                lines[index] = f"{key}={value}"
                remaining.pop(key, None)
                break
    for key, value in remaining.items():
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
