import os
import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

from bt_api_py import ctp_env_selector
from bt_api_py.ctp_env_selector import apply_ctp_env, get_ctp_fronts, select_ctp_fronts


class _YamlModule(ModuleType):
    safe_load: Mock


def test_load_default_fronts_logs_safe_diagnostic_on_yaml_failure(monkeypatch, tmp_path):
    payload_secret = "yaml-payload-secret"
    path_secret = "yaml-path-secret"
    config_path = tmp_path / f"{path_secret}.yaml"
    config_path.write_text(payload_secret, encoding="utf-8")
    exception_text = f"parse failure for {payload_secret} at {config_path}"

    yaml = _YamlModule("yaml")
    yaml.safe_load = Mock(side_effect=ValueError(exception_text))
    logger = Mock()
    monkeypatch.setitem(sys.modules, "yaml", yaml)
    monkeypatch.setattr(ctp_env_selector, "_FRONTS_CONFIG_PATH", config_path)
    monkeypatch.setattr(ctp_env_selector, "logger", logger, raising=False)

    defaults = ctp_env_selector._load_default_fronts()

    assert defaults == {
        "set1": {
            "td_front": "tcp://182.254.243.31:30001",
            "md_front": "tcp://182.254.243.31:30011",
        },
        "set2": {
            "td_front": "tcp://182.254.243.31:40001",
            "md_front": "tcp://182.254.243.31:40011",
        },
    }
    yaml.safe_load.assert_called_once()
    logger.debug.assert_called_once()
    assert logger.debug.call_args.kwargs == {}
    diagnostic = " ".join(str(value) for value in logger.debug.call_args.args)
    assert logger.debug.call_args.args[-1] == "ValueError"
    assert exception_text not in diagnostic
    assert payload_secret not in diagnostic
    assert path_secret not in diagnostic
    assert str(config_path) not in diagnostic


def test_select_ctp_fronts_auto_uses_set1_during_regular_session(monkeypatch):
    monkeypatch.setenv("CTP_SET1_TD_FRONT_2", "tcp://set1-td")
    monkeypatch.setenv("CTP_SET1_MD_FRONT_2", "tcp://set1-md")

    result = select_ctp_fronts(
        env="auto",
        now=datetime(2026, 6, 18, 10, 0, 0),
        set1_group="2",
        apply_env=False,
    )

    assert result.env_name == "set1_group2"
    assert result.td_front == "tcp://set1-td"
    assert result.md_front == "tcp://set1-md"
    assert result.selection_reason == "auto_regular_trading_session"


def test_select_ctp_fronts_auto_uses_set2_outside_regular_session(monkeypatch):
    monkeypatch.setenv("CTP_SET2_TD_FRONT", "tcp://set2-td")
    monkeypatch.setenv("CTP_SET2_MD_FRONT", "tcp://set2-md")

    result = select_ctp_fronts(
        env="auto",
        now=datetime(2026, 6, 20, 10, 0, 0),
        apply_env=False,
    )

    assert result.env_name == "set2_7x24"
    assert result.to_dict()["selected_ctp_env"] == "set2_7x24"
    assert result.selection_reason == "auto_outside_regular_session"


def test_get_ctp_fronts_keeps_tuple_api(monkeypatch):
    monkeypatch.setenv("CTP_SET2_TD_FRONT", "tcp://tuple-td")
    monkeypatch.setenv("CTP_SET2_MD_FRONT", "tcp://tuple-md")

    assert get_ctp_fronts(env="set2") == ("tcp://tuple-td", "tcp://tuple-md", "set2_7x24")


def test_apply_ctp_env_auto_detect_pins_plugin_profile(monkeypatch):
    captured = {}
    selector = ModuleType("bt_api_ctp.ctp_env_selector")

    def select_reachable_ctp_environment(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            td_front="tcp://fixture-td",
            md_front="tcp://fixture-md",
            profile="set2_7x24_vpn",
        )

    selector.__dict__["select_reachable_ctp_environment"] = select_reachable_ctp_environment
    package = ModuleType("bt_api_ctp")
    monkeypatch.setitem(sys.modules, "bt_api_ctp", package)
    monkeypatch.setitem(sys.modules, "bt_api_ctp.ctp_env_selector", selector)
    monkeypatch.setenv("CTP_ENV", "set2")
    monkeypatch.delenv("CTP_TD_FRONT", raising=False)
    monkeypatch.delenv("CTP_MD_FRONT", raising=False)
    monkeypatch.delenv("CTP_ENV_PROFILE", raising=False)

    result = apply_ctp_env(auto_detect_fronts=True, timeout=0.25)

    assert result == ("tcp://fixture-td", "tcp://fixture-md", "set2_7x24_vpn")
    assert captured == {"env": "set2", "profile": None, "timeout": 0.25}
    assert os.environ["CTP_TD_FRONT"] == "tcp://fixture-td"
    assert os.environ["CTP_MD_FRONT"] == "tcp://fixture-md"
    assert os.environ["CTP_ENV_PROFILE"] == "set2_7x24_vpn"
