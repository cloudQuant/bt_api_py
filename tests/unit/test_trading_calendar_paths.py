"""Cross-platform lexical path contracts for calendar roots and Windows syntax.

These are lexical checks only: no filesystem identity, symlink, network share or
native Windows run is established. Every platform branch asserts the documented
behaviour; nothing is silently skipped. Real Windows execution remains NOT_RUN.
"""

import os
from pathlib import Path

import pytest

from bt_api_py.risk_service_config import (
    RiskServiceConfigError,
    _resolve_windows_path,
    resolve_path,
)
from bt_api_py.trading_calendar_config import CalendarConfigError, parse_trading_calendar_config
from bt_api_py.trading_calendar_package_seed import install_package_seed

WINDOWS_BASE = r"C:\Risk Config"
UNC = r"\\server\share\calendars"


def parse(root, config_dir, **flags):
    options = {"gateway_enabled": False, "direct_write_enabled": False}
    options.update(flags)
    config = {} if root is _MISSING else {"calendars": {"root": root}}
    return parse_trading_calendar_config(
        config, config_file=Path(config_dir) / "config.yaml", **options
    )


_MISSING = object()


# ---------------------------------------------------------------------------
# Calendar root parsing: relative, absolute, home, spaces, Chinese, long, UNC
# ---------------------------------------------------------------------------


def test_relative_root_resolves_against_config_directory(tmp_path):
    result = parse("calendar data/交易日历", tmp_path)
    assert result.root_kind == "directory"
    assert result.root_path == Path(tmp_path / "calendar data" / "交易日历")


def test_absolute_root_with_spaces_and_chinese_is_kept(tmp_path):
    root = tmp_path / "交易所 数据" / "calendars"
    result = parse(str(root), tmp_path)
    assert result.root_path == root
    assert result.directory_policy_verified is False


def test_home_root_expands_to_the_documented_home(tmp_path, monkeypatch):
    home = tmp_path / "home dir"
    monkeypatch.setenv("HOME", str(home))
    result = parse("~/calendars", tmp_path)
    assert result.root_path == home / "calendars"


def test_unresolvable_home_root_is_rejected_not_left_literal(tmp_path):
    with pytest.raises(CalendarConfigError, match="^calendar_root_invalid_home$"):
        parse("~definitely_no_such_user_970df", tmp_path)


def test_dot_relative_home_prefix_is_not_the_package_seed(tmp_path):
    result = parse("./package_seed", tmp_path)
    assert result.root_kind == "directory"
    assert result.root_path == Path(tmp_path / "package_seed")


def test_exact_package_seed_is_recognized_without_a_directory(tmp_path):
    result = parse("package_seed", tmp_path)
    assert (result.root_kind, result.root_path, result.daily_updates) == (
        "package_seed",
        None,
        False,
    )


def test_long_root_path_is_lexically_accepted(tmp_path):
    long_component = "d" * 300
    root = tmp_path / long_component / "calendars"
    result = parse(str(root), tmp_path)
    assert result.root_path == root
    assert len(str(result.root_path)) > 260


def test_unc_root_documented_behaviour(tmp_path):
    result = parse(UNC, tmp_path)
    if os.name == "nt":
        # Windows treats a UNC share as absolute; no existence is checked here.
        assert result.root_path == Path(UNC)
    else:
        # POSIX has no drive/root for this string, so it is a relative name.
        assert result.root_path == Path(os.path.normpath(tmp_path / Path(UNC)))


def test_windows_drive_absolute_root_documented_behaviour(tmp_path):
    result = parse(r"C:\calendars", tmp_path)
    if os.name == "nt":
        assert result.root_path == Path(r"C:\calendars")
    else:
        assert result.root_path == Path(os.path.normpath(tmp_path / Path(r"C:\calendars")))


@pytest.mark.parametrize("root", ["", "   ", "a\x00b"])
def test_empty_or_nul_roots_are_rejected(root, tmp_path):
    with pytest.raises(CalendarConfigError, match="^calendar_root_invalid$"):
        parse(root, tmp_path)


# ---------------------------------------------------------------------------
# Installer roots: absolute only, no silent home or environment expansion
# ---------------------------------------------------------------------------


def test_installer_rejects_relative_and_home_roots_without_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for value in ("calendars", "~/calendars", "~", ".", "..", "a/../b"):
        result = install_package_seed(value, dry_run=True)
        assert (result.status, result.reason) == ("NOT_COMMITTED", "SEED_ROOT_INVALID")
    assert not (tmp_path / "home").exists()


def test_installer_keeps_environment_variables_literal(tmp_path, monkeypatch):
    monkeypatch.setenv("BT_CAL_ROOT", str(tmp_path / "expanded"))
    literal = str(tmp_path / "%BT_CAL_ROOT%")
    result = install_package_seed(literal, dry_run=True)
    assert (result.status, result.reason) == ("STAGED", "SEED_STAGED")
    assert result.store_root == literal
    assert not (tmp_path / "expanded").exists()


def test_installer_accepts_spaces_chinese_and_long_paths(tmp_path):
    roots = [tmp_path / "日历 数据", tmp_path / ("a" * 120) / ("b" * 120) / ("c" * 40)]
    for root in roots:
        result = install_package_seed(root, dry_run=True)
        assert (result.status, result.reason) == ("STAGED", "SEED_STAGED")
        assert result.store_root == str(root)
        assert not root.exists()
    assert len(str(roots[-1])) > 260


def test_installer_unc_root_documented_behaviour(tmp_path):
    result = install_package_seed(UNC, dry_run=True)
    if os.name == "nt":
        # Lexically absolute; the staged call performs no filesystem access.
        assert (result.status, result.reason) == ("STAGED", "SEED_STAGED")
        assert result.store_root == UNC
    else:
        assert (result.status, result.reason) == ("NOT_COMMITTED", "SEED_ROOT_INVALID")


@pytest.mark.parametrize("value", ["", " calendars", "a\x00b", b"/tmp/x", 7, None, object()])
def test_installer_rejects_malformed_roots(value):
    result = install_package_seed(value, dry_run=True)
    assert (result.status, result.reason) == ("NOT_COMMITTED", "SEED_ROOT_INVALID")


# ---------------------------------------------------------------------------
# Existing Windows lexical resolver (f552c61e) extended coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"state dir\risk.sqlite", r"C:\Risk Config\state dir\risk.sqlite"),
        (r"状态\风险.sqlite", r"C:\Risk Config\状态\风险.sqlite"),
        (r"\\服务器\共享\政策.yaml", r"\\服务器\共享\政策.yaml"),
        (r"\\?\C:\data\file.yaml", r"\\?\C:\data\file.yaml"),
        ("x" * 300 + r"\state", "C:\\Risk Config\\" + "x" * 300 + r"\state"),
        (r"D:\日历 数据\policy.yaml", r"D:\日历 数据\policy.yaml"),
    ],
)
def test_windows_lexical_resolution_handles_unicode_spaces_long_and_unc(raw, expected):
    assert _resolve_windows_path(raw, WINDOWS_BASE) == expected


def test_windows_long_result_exceeds_the_legacy_limit():
    resolved = _resolve_windows_path("y" * 280 + r"\state", WINDOWS_BASE)
    assert len(resolved) > 260


@pytest.mark.parametrize("raw", [r"C:state", r"D:state", "C:", r"\state", r"/state"])
def test_windows_drive_and_root_relative_paths_stay_rejected(raw):
    with pytest.raises(RiskServiceConfigError, match="^RISK_SERVICE_CONFIG_PATH_INVALID$"):
        _resolve_windows_path(raw, WINDOWS_BASE)


@pytest.mark.parametrize(
    "raw", [r"%BT_UNSET_VAR%\state", r"$BT_UNSET_VAR\state", r"${BT_UNSET_VAR}\state"]
)
def test_windows_unset_environment_variables_stay_rejected(monkeypatch, raw):
    monkeypatch.delenv("BT_UNSET_VAR", raising=False)
    with pytest.raises(RiskServiceConfigError, match="^RISK_SERVICE_CONFIG_PATH_UNRESOLVED$"):
        _resolve_windows_path(raw, WINDOWS_BASE)


@pytest.mark.parametrize("raw", ["~no_such_user_970df\\state", r"~\state"])
def test_windows_unresolvable_home_is_rejected_not_literal(monkeypatch, raw):
    for name in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RiskServiceConfigError, match="^RISK_SERVICE_CONFIG_PATH_UNRESOLVED$"):
        _resolve_windows_path(raw, WINDOWS_BASE)


def test_windows_relative_path_requires_an_absolute_base():
    with pytest.raises(RiskServiceConfigError, match="^RISK_SERVICE_CONFIG_PATH_INVALID$"):
        _resolve_windows_path(r"state", r"D:config")


# ---------------------------------------------------------------------------
# POSIX resolver: documented expansion and literal retention
# ---------------------------------------------------------------------------


def test_posix_resolver_expands_home_and_keeps_spaces_and_chinese(tmp_path, monkeypatch):
    home = tmp_path / "home dir"
    monkeypatch.setenv("HOME", str(home))
    base = Path(tmp_path / "配置")
    assert resolve_path("~/state", base) == home / "state"
    assert resolve_path("状态 数据/risk.sqlite", base) == base / "状态 数据" / "risk.sqlite"


def test_posix_resolver_rejects_unexpanded_dollar_variables(monkeypatch):
    monkeypatch.delenv("BT_UNSET_VAR", raising=False)
    with pytest.raises(RiskServiceConfigError, match="^RISK_SERVICE_CONFIG_PATH_UNRESOLVED$"):
        resolve_path("$BT_UNSET_VAR/state", Path("/risk"))


def test_posix_resolver_keeps_percent_delimiters_literal():
    assert resolve_path("%BT_UNSET_VAR%/state", Path("/risk")) == Path("/risk/%BT_UNSET_VAR%/state")


def test_posix_resolver_accepts_long_absolute_paths(tmp_path):
    root = tmp_path / ("z" * 300)
    assert resolve_path(str(root), Path("/risk")) == root
    assert len(str(root)) > 260
