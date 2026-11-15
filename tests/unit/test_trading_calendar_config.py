"""Experimental configuration parsing tests; no network, account or config file IO."""

from pathlib import Path
from types import MappingProxyType

import pytest

from bt_api_py.trading_calendar_config import (
    CalendarConfigError,
    CalendarConfigReadFailure,
    parse_trading_calendar_config,
)


def parse(config, path, **kwargs):
    options = {"gateway_enabled": False, "direct_write_enabled": False}
    options.update(kwargs)
    return parse_trading_calendar_config(config, config_file=path, **options)


def test_package_seed_is_explicit_and_has_no_daily_update_claim(tmp_path):
    result = parse(
        {"calendars": {"root": "package_seed"}}, tmp_path / "config.yaml", gateway_enabled=True
    )
    assert (result.status, result.reason, result.root_kind, result.root_path) == (
        "CONFIGURED",
        "package_seed",
        "package_seed",
        None,
    )
    assert result.daily_updates is False
    assert result.review_mode == "auto" and result.review_mode_source == "default"
    assert result.directory_policy_verified is False
    assert result.maturity == "EXPERIMENTAL"


@pytest.mark.parametrize("name", ["gateway_enabled", "direct_write_enabled"])
def test_missing_root_requires_explicit_configuration(name, tmp_path):
    result = parse({}, tmp_path / "config.yaml", **{name: True})
    assert result.status == "ERROR"
    assert result.reason == "calendar_root_unconfigured"
    assert result.root_kind == "unconfigured" and result.root_path is None
    assert result.maturity == "EXPERIMENTAL"


def test_unconfigured_nonwriting_behavior_remains_unknown(tmp_path):
    result = parse({"calendars": {}}, tmp_path / "config.yaml")
    assert result.status == "UNKNOWN"
    assert result.reason == "calendar_root_unconfigured"
    assert result.root_kind == "unconfigured" and result.root_path is None
    assert result.review_mode == "auto" and result.review_mode_source == "default"


@pytest.mark.parametrize("mode,source", [("auto", "explicit"), ("manual", "explicit")])
def test_explicit_review_modes_and_directory_policy_remain_unverified(tmp_path, mode, source):
    result = parse({"calendars": {"root": "data", "review_mode": mode}}, tmp_path / "config.yaml")
    assert (result.review_mode, result.review_mode_source) == (mode, source)
    assert result.directory_policy_verified is False
    assert result.daily_updates is None


def test_absolute_and_chinese_relative_paths_ignore_cwd(tmp_path, monkeypatch):
    config_path = tmp_path / "中文 文件夹" / "config.yaml"
    other_dir = tmp_path / "different cwd"
    other_dir.mkdir()
    relative = {"calendars": {"root": "../日历 数据"}}
    before = parse(relative, config_path)
    monkeypatch.chdir(other_dir)
    after = parse(relative, config_path)
    assert before.root_path == after.root_path == tmp_path / "日历 数据"
    absolute = tmp_path / "绝对 路径"
    assert parse({"calendars": {"root": str(absolute)}}, config_path).root_path == absolute
    assert before.root_kind == "directory" and before.daily_updates is None


def test_multilevel_root_and_config_paths_are_normalized_lexically(tmp_path, monkeypatch):
    config_path = tmp_path / "配置 目录" / "old" / ".." / "子目录" / "config.yaml"
    config = {"calendars": {"root": "../../日历 空间/期货/../数据"}}
    expected = tmp_path / "日历 空间" / "数据"
    before = parse(config, config_path)
    other = tmp_path / "cwd"
    other.mkdir()
    monkeypatch.chdir(other)
    after = parse(config, config_path)
    assert before.root_path == after.root_path == expected
    assert before.directory_policy_verified is False


def test_absolute_root_dot_segments_are_normalized(tmp_path):
    absolute = tmp_path / "目录" / ".." / "交易 日历"
    result = parse({"calendars": {"root": str(absolute)}}, tmp_path / "config.yaml")
    assert result.root_path == tmp_path / "交易 日历"


def test_home_expands_but_environment_variables_do_not(tmp_path):
    path = tmp_path / "config.yaml"
    home = parse({"calendars": {"root": "~/calendars"}}, path)
    literal = parse({"calendars": {"root": "$HOME/calendars"}}, path)
    assert home.root_path == Path.home() / "calendars"
    assert literal.root_path == tmp_path / "$HOME" / "calendars"


def test_only_exact_package_seed_is_special(tmp_path):
    result = parse({"calendars": {"root": "./package_seed"}}, tmp_path / "config.yaml")
    assert result.root_kind == "directory"
    assert result.root_path == tmp_path / "package_seed"


def test_other_top_level_settings_and_mapping_objects_are_accepted(tmp_path):
    config = MappingProxyType(
        {
            "other": {"unrelated": True},
            "calendars": MappingProxyType({"root": "data", "review_mode": "manual"}),
        }
    )
    assert parse(config, tmp_path / "config.yaml").review_mode == "manual"


@pytest.mark.parametrize(
    "config,code",
    [
        (None, "calendar_config_invalid"),
        ([], "calendar_config_invalid"),
        ({"calendars": None}, "calendar_config_invalid"),
        ({"calendars": []}, "calendar_config_invalid"),
        ({"calendars": {"root": None}}, "calendar_root_invalid"),
        ({"calendars": {"root": " "}}, "calendar_root_invalid"),
        ({"calendars": {"root": "bad\x00path"}}, "calendar_root_invalid"),
        ({"calendars": {"review_mode": None}}, "calendar_review_mode_invalid"),
        ({"calendars": {"review_mode": "AUTO"}}, "calendar_review_mode_invalid"),
        ({"calendars": {"review_mode": True}}, "calendar_review_mode_invalid"),
        ({"calendars": {"review_mod": "manual"}}, "calendar_config_unknown_field"),
        (CalendarConfigReadFailure("unreadable"), "calendar_config_unreadable"),
        (CalendarConfigReadFailure("invalid_format"), "calendar_config_invalid_format"),
        (CalendarConfigReadFailure("invalid"), "calendar_config_invalid_failure"),
    ],
)
def test_invalid_or_failed_config_never_defaults(config, code, tmp_path):
    with pytest.raises(CalendarConfigError) as exc:
        parse(config, tmp_path / "config.yaml")
    assert exc.value.code == code


@pytest.mark.parametrize("path", ["config.yaml", "", "/", "bad\x00path", None])
def test_config_file_must_be_absolute_file_path(path):
    with pytest.raises(CalendarConfigError, match="calendar_config_invalid_file_path"):
        parse({}, path)


@pytest.mark.parametrize(
    "option",
    [
        {"gateway_enabled": "false"},
        {"gateway_enabled": None},
        {"direct_write_enabled": 1},
        {"direct_write_enabled": None},
    ],
)
def test_enablement_is_explicit_boolean(option, tmp_path):
    with pytest.raises(CalendarConfigError, match="calendar_config_invalid_mode"):
        parse({}, tmp_path / "config.yaml", **option)
