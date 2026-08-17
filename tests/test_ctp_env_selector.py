from datetime import datetime

from bt_api_py.ctp_env_selector import get_ctp_fronts, select_ctp_fronts


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
