"""A blocked observed order source is a finite gap, not a crashed owner loop.

The real Gateway once raised a bare ``RuntimeError("OBSERVED_SOURCE_GAP")`` from
``CtpGatewayReturnMaintenance.poll``; the poll handler then classified it as
``CALLBACK_FAILED`` (no ``code`` attribute) and printed a full traceback on every
tick. A source gap is now a value-limited typed error whose ``code`` survives the
poll handler, so the owner records the gap and keeps running.
"""
import logging
import threading
from types import SimpleNamespace

from bt_api_py.ctp_gateway_return_maintenance import (
    CtpGatewayReturnMaintenance,
    CtpGatewayReturnMaintenanceError,
)


def _maintenance():
    maintenance = CtpGatewayReturnMaintenance.__new__(CtpGatewayReturnMaintenance)
    maintenance._lock = threading.Lock()
    maintenance._closing = threading.Event()
    maintenance._open, maintenance._status = True, "HEALTHY"
    maintenance._fact_health = {"status": "UNAVAILABLE", "unattributed": None,
                                "conflict": None, "verified_external": None}
    maintenance._recovery_fence_generation = None
    maintenance._last_poll_ok = True
    maintenance._last_query_failure = None
    maintenance._stale_seen = False
    maintenance._done = set()
    maintenance._pair_seen = False
    maintenance.refresh_generation = lambda: None
    maintenance._provider = SimpleNamespace(
        clear_instrument_status_return_recovery=lambda: None
    )
    return maintenance


def test_observed_source_gap_is_typed_error_not_bare_runtime_error():
    error = CtpGatewayReturnMaintenanceError("OBSERVED_SOURCE_GAP")
    assert type(error) is CtpGatewayReturnMaintenanceError
    assert isinstance(error, RuntimeError)
    assert error.code == "OBSERVED_SOURCE_GAP"
    # A value-free code: no account, path, or secret payload on the error.
    assert str(error) == "OBSERVED_SOURCE_GAP"
    assert not hasattr(error, "value")
    # Unknown codes collapse to the finite fallback, never leak a raw value.
    assert CtpGatewayReturnMaintenanceError("UNKNOWN").code == "CALLBACK_FAILED"


def test_poll_records_gap_and_owner_does_not_crash(caplog):
    maintenance = _maintenance()
    maintenance._session = SimpleNamespace(return_buffer=SimpleNamespace(
        status=lambda: SimpleNamespace(gap=False, record_count=0, last_sequence=0),
        offer=lambda **_: None,
        ack=lambda _: None,
    ))
    source = SimpleNamespace(blocked=True, invalidate=lambda: None)
    maintenance._observed_order_source = source

    # poll() must not raise: the gap is classified and recorded, not re-raised.
    maintenance.poll()

    assert maintenance.status()["status"] == "OBSERVED_SOURCE_GAP"
    assert maintenance.status()["reservation_open"] is False
    assert maintenance._last_poll_ok is False
    # The gap is recorded once per distinct cause, never as CALLBACK_FAILED.
    with caplog.at_level(logging.WARNING, logger="bt_api_py.ctp_gateway_return_maintenance"):
        maintenance.poll()
        maintenance.poll()
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["CTP return query callbacks failed: CtpGatewayReturnMaintenanceError(OBSERVED_SOURCE_GAP)"]


def test_poll_blocked_source_reports_gap_status_and_closes_reservation():
    maintenance = _maintenance()
    maintenance._session = SimpleNamespace(return_buffer=SimpleNamespace(
        status=lambda: SimpleNamespace(gap=False, record_count=0, last_sequence=0),
        offer=lambda **_: None,
        ack=lambda _: None,
    ))
    source = SimpleNamespace(blocked=True, invalidate=lambda: None)
    maintenance._observed_order_source = source
    maintenance.poll()
    # status() reflects the durable gap regardless of which poll path saw it.
    assert maintenance.status() == {"status": "OBSERVED_SOURCE_GAP", "reservation_open": False}
