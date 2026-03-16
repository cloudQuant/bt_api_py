from __future__ import annotations

from datetime import datetime

from bt_api_py.ctp_env_selector import apply_ctp_env, get_ctp_fronts


def test_get_ctp_fronts_prefers_set1_during_weekday_session(monkeypatch) -> None:
    monkeypatch.setenv("CTP_SET1_GROUP", "2")
    monkeypatch.setenv("CTP_SET1_TD_FRONT_2", "tcp://set1-td")
    monkeypatch.setenv("CTP_SET1_MD_FRONT_2", "tcp://set1-md")

    td, md, env_name = get_ctp_fronts(now=datetime(2026, 3, 16, 10, 0, 0))

    assert (td, md, env_name) == ("tcp://set1-td", "tcp://set1-md", "set1_group2")


def test_get_ctp_fronts_uses_set2_outside_trading_hours(monkeypatch) -> None:
    monkeypatch.setenv("CTP_SET2_TD_FRONT", "tcp://set2-td")
    monkeypatch.setenv("CTP_SET2_MD_FRONT", "tcp://set2-md")

    td, md, env_name = get_ctp_fronts(now=datetime(2026, 3, 16, 16, 30, 0))

    assert (td, md, env_name) == ("tcp://set2-td", "tcp://set2-md", "set2_7x24")


def test_get_ctp_fronts_treats_saturday_early_morning_as_set1(monkeypatch) -> None:
    monkeypatch.setenv("CTP_SET1_TD_FRONT_1", "tcp://set1-td")
    monkeypatch.setenv("CTP_SET1_MD_FRONT_1", "tcp://set1-md")

    td, md, env_name = get_ctp_fronts(now=datetime(2026, 3, 21, 1, 30, 0))

    assert (td, md, env_name) == ("tcp://set1-td", "tcp://set1-md", "set1_group1")


def test_apply_ctp_env_uses_env_override(monkeypatch) -> None:
    monkeypatch.setenv("CTP_ENV", "set2")
    monkeypatch.setenv("CTP_SET2_TD_FRONT", "tcp://override-td")
    monkeypatch.setenv("CTP_SET2_MD_FRONT", "tcp://override-md")

    td, md, env_name = apply_ctp_env()

    assert (td, md, env_name) == ("tcp://override-td", "tcp://override-md", "set2_7x24")
