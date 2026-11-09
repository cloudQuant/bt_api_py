"""Fact observations are separate from transport/readiness state."""
import threading
from types import SimpleNamespace

from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance


def test_fact_status_never_claims_account_reconciliation_or_controls_readiness():
    maintenance = CtpGatewayReturnMaintenance.__new__(CtpGatewayReturnMaintenance)
    maintenance._lock = threading.Lock()
    maintenance._closing = threading.Event()
    maintenance._open, maintenance._status = True, "HEALTHY"
    counts = {"unattributed": 2, "conflict": 0, "verified_external": 1}
    maintenance._ledger = SimpleNamespace(account_fact_health=lambda: dict(counts))
    maintenance._refresh_fact_status()
    assert maintenance.status() == {"status": "HEALTHY", "reservation_open": True}
    assert maintenance.fact_status() == {"status": "INCOMPLETE", **counts}
    counts.update(unattributed=0, conflict=1)
    maintenance._refresh_fact_status()
    assert maintenance.fact_status()["status"] == "CONFLICT"
    counts.update(conflict=0)
    maintenance._refresh_fact_status()
    assert maintenance.fact_status()["status"] == "NO_UNRESOLVED_OBSERVED"
    maintenance._ledger = SimpleNamespace(account_fact_health=lambda: None)
    maintenance._refresh_fact_status()
    assert maintenance.fact_status() == {"status": "UNAVAILABLE", "unattributed": None,
                                         "conflict": None, "verified_external": None}
    maintenance._closing.set()
    assert maintenance.fact_status()["status"] == "UNAVAILABLE"
