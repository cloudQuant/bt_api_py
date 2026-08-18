"""Capability report contract tests (Task 1.3)."""

from __future__ import annotations

from bt_api_py._contracts.capabilities import SUPPORT_STATUSES, CapabilityReport


def test_capability_report_fields() -> None:
    report = CapabilityReport(
        exchange_name="BINANCE___SPOT",
        status="installed",
        operations={"get_tick": True, "make_order": False},
    )
    assert report.exchange_name == "BINANCE___SPOT"
    assert report.status == "installed"
    assert report.supports("get_tick") is True
    assert report.supports("make_order") is False
    assert report.supports("unknown_op") is False


def test_capability_report_is_frozen() -> None:
    import dataclasses

    import pytest

    report = CapabilityReport(exchange_name="X", status="installed")
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.status = "certified"  # type: ignore[misc]


def test_support_statuses_cover_fr08_tiers() -> None:
    assert set(SUPPORT_STATUSES) == {
        "installed",
        "loadable",
        "certified",
        "experimental",
        "retired",
    }
