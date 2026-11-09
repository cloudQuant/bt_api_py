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


def test_failed_return_queries_are_logged_once_per_cause(caplog):
    """I63-E097: a persistent QUERY_INCOMPLETE must name its query failure."""
    import logging

    from bt_api_ctp.gateway_return_query import CtpGatewayReturnQueryError

    maintenance = CtpGatewayReturnMaintenance.__new__(CtpGatewayReturnMaintenance)
    maintenance._lock = threading.Lock()
    maintenance._closing = threading.Event()
    maintenance._open, maintenance._status = True, "HEALTHY"
    maintenance._done, maintenance._next_kind, maintenance._next_query = set(), "orders", 0.0
    maintenance._last_query_failure = None
    maintenance._settings = SimpleNamespace(query_interval_seconds=0.0, query_timeout_seconds=1.0)
    maintenance.refresh_generation = lambda: None
    failures = [CtpGatewayReturnQueryError("QUERY_TIMEOUT")] * 3 + [RuntimeError("acct 12345678")]

    def read_and_ingest(kind, timeout):
        raise failures.pop(0)

    maintenance._ingestor = SimpleNamespace(read_and_ingest=read_and_ingest)
    with caplog.at_level(logging.WARNING, logger="bt_api_py.ctp_gateway_return_maintenance"):
        for _ in range(4):
            maintenance.query()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [
        "CTP return query orders failed: CtpGatewayReturnQueryError code=QUERY_TIMEOUT",
        "CTP return query trades failed: RuntimeError code=None",
    ]
    assert maintenance.status()["status"] == "QUERY_FAILED" and maintenance._done == set()
    assert "12345678" not in caplog.text
