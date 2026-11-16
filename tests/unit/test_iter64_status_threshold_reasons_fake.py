"""``status`` threshold utilization stays unavailable with finite reasons.

UNIT/FAKE: a fake ledger; no service, network, account or provider. The
structure must be derived from the checks actually run, never invented numbers.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

from bt_api_risk.pretrade.engine import PretradeEngine


class FakeLedger:
    @contextmanager
    def transaction(self):
        yield self

    def cursor(self):
        return {"continuity": "VERIFIED", "epoch": 1, "last_sequence": 4,
                "verified_from_zero": True, "gap_reason": None}

    def bypass_count(self): return 0
    def permit_count(self): return 0
    def permits_in(self, states): return []
    def active_latches(self): return []
    def control_intents(self, states): return []
    def cancel_intents(self, states): return []
    def counters_for_day(self, day): return []


def engine(*, active=None, trading_day="20261115"):
    instance = object.__new__(PretradeEngine)
    instance._ledger = FakeLedger()
    instance._store = SimpleNamespace(load_error=None)
    instance._active = active

    def fact(name, expected):
        return trading_day if name == "trading_day" else None

    instance._fact = fact
    return instance


def reasons(summary):
    return {reason["code"]: reason["status"] for reason in summary["threshold_utilization"]["reasons"]}


def test_missing_policy_and_trading_day_are_reported_from_actual_checks():
    summary = engine(active=None, trading_day=None).status_summary()
    utilization = summary["threshold_utilization"]
    assert utilization["available"] is False
    assert utilization["status"] == "INCOMPLETE"
    assert utilization["scope"] == "local_ledger"
    assert reasons(summary)["policy_snapshot_unavailable"] == "UNAVAILABLE"
    assert reasons(summary)["trading_day_unavailable"] == "UNAVAILABLE"
    # No fabricated utilization number is present anywhere in the block.
    assert "utilization" not in json.dumps(utilization)
    assert all(set(reason) == {"code", "status"} for reason in utilization["reasons"])


def test_present_trading_day_is_unverified_not_unavailable():
    active = SimpleNamespace(
        version=1, policy=SimpleNamespace(policy_id="policy", digest="d" * 64, loss=None)
    )
    summary = engine(active=active).status_summary()
    codes = reasons(summary)
    assert "trading_day_unavailable" not in codes
    assert codes["trading_day_binding_unverified"] == "UNVERIFIED"
    # Coverage facts remain unproven, so the block stays unavailable.
    assert codes["account_source_coverage_unverified"] == "UNVERIFIED"
    assert codes["instrument_identity_incomplete"] == "UNVERIFIED"
    assert summary["threshold_utilization"]["available"] is False


def test_every_reason_uses_the_finite_status_vocabulary():
    summary = engine(active=None, trading_day=None).status_summary()
    for reason in summary["threshold_utilization"]["reasons"]:
        assert type(reason["code"]) is str and reason["code"]
        assert reason["status"] in ("UNAVAILABLE", "UNVERIFIED")
