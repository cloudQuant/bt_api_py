"""Gateway facade projections for an existing Risk permit with unavailable facts.

UNIT/FAKE only: the wire responses model Risk's contract; they do not exercise a
Risk ledger, parent corruption, a Gateway process, or an account.
"""

from bt_api_py.service_runtime_thread import ThreadedRuntimeError
from tests.unit.test_iter63_gateway_api import order
from tests.unit.test_iter64_risk_service_client import DIGEST, answer, facade

ROUTE = "CTP___FUTURE"
KEY = "same-key"


def unknown_reserved():
    return answer(
        "UNKNOWN",
        state="RESERVED",
        permit_id="permit-1",
        reservation_id="res-1",
        view_digest=DIGEST,
        code="risk_parent_unavailable",
    )


def test_lost_first_reserve_and_same_key_retry_keep_unknown(monkeypatch):
    # Model an existing, occupied reservation whose first Risk reply was lost;
    # the second response is UNKNOWN because the Risk parent facts are unusable.
    simulated_gateway_reserves = []

    def create_then_lose_reply():
        simulated_gateway_reserves.append(KEY)
        raise ThreadedRuntimeError("TIMEOUT_AFTER_START")

    api, host, wire, direct_gateway = facade(
        monkeypatch, replies=[create_then_lose_reply, unknown_reserved(), unknown_reserved()]
    )
    first = api.reserve_order(ROUTE, order())
    retried = api.reserve_order(ROUTE, order())

    assert first.outcome == retried.outcome == "UNKNOWN"
    assert retried.risk_denied is None and retried.record is None
    assert retried.risk_answer.state == "RESERVED"
    assert (retried.risk_answer.permit_id, retried.risk_answer.reservation_id) == (
        "permit-1",
        "res-1",
    )
    assert host.load_intent(KEY).request_key == KEY
    assert KEY in api._risk_keys and KEY not in api._risk_permits
    assert simulated_gateway_reserves == [KEY]
    assert [call[1] for call in wire.calls] == ["risk.order.reserve", "risk.order.reserve"]
    assert direct_gateway == []

    # A read-back UNKNOWN is not a permit to send or release the old order.
    assert api.dispatch_order(ROUTE, KEY).code == "RISK_PERMIT_UNAVAILABLE"
    release = api.release_order(ROUTE, KEY, operation_id="release-1")
    assert (release.outcome, release.code) == ("TRANSPORT_NOT_STARTED", "RISK_PERMIT_UNAVAILABLE")
    assert [call[1] for call in wire.calls] == [
        "risk.order.reserve",
        "risk.order.reserve",
        "risk.order.status",
    ]
    assert direct_gateway == [] and simulated_gateway_reserves == [KEY]


def test_known_permit_unknown_dispatch_and_release_preserve_identity(monkeypatch):
    api, _, wire, direct_gateway = facade(
        monkeypatch, replies=[answer("RESERVED"), unknown_reserved(), unknown_reserved()]
    )
    reserved = api.reserve_order(ROUTE, order())
    assert reserved.outcome == "OBSERVED" and reserved.record.state == "RESERVED"

    dispatched = api.dispatch_order(ROUTE, KEY)
    released = api.release_order(ROUTE, KEY, operation_id="release-1")
    assert dispatched.outcome == released.outcome == "UNKNOWN"
    assert dispatched.risk_denied is None and released.risk_denied is None
    assert dispatched.risk_answer.state == released.risk_answer.state == "RESERVED"
    assert api._risk_permits[KEY].permit_id == "permit-1"
    assert api._risk_permits[KEY].reservation_id == "res-1"
    assert [call[1] for call in wire.calls] == [
        "risk.order.reserve",
        "risk.order.dispatch",
        "risk.order.release",
    ]
    assert direct_gateway == []
