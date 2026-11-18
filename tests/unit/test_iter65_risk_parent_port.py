from decimal import Decimal
from types import SimpleNamespace

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_py.risk_parent_port import RiskParentPort
from bt_api_py.risk_service_client import RiskParentCallResult


def payload(**spec_overrides):
    spec = {
        "parent_ref": "p-1", "account_scope": "acct", "strategy_scope": "s",
        "execution_domain_ref": "paper:simnow",
        "instrument": MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb2701").to_dict(),
        "quantity": "5", "quantity_unit": "lots", "side": "buy", "offset": "open",
        "limit_price": "5", "creation_trading_day": "20261118",
        "allowed_trading_days": ["20261118"],
    }
    spec.update(spec_overrides)
    return {
        "spec": spec,
        "strategy": {"strategy_ref": "s", "run_id": "run-1"},
    }


class Client:
    def __init__(self, result):
        self.result = result
        self.seen = []
        self._route = SimpleNamespace(scope=SimpleNamespace(run_id="run-1"))

    def available(self):
        return True

    def parent_reserve(self, view):
        assert type(view).__name__ == "RiskParentView"
        assert view.route == "CTP___FUTURE"
        self.seen.append(view)
        return self.result


def test_reserved_parent_maps_to_accepted():
    port = RiskParentPort(Client(RiskParentCallResult("ANSWERED", parent_outcome="RESERVED")),
                          route="CTP___FUTURE")
    assert port.available() is True
    assert port.reserve_parent(payload()) == {"status": "ACCEPTED", "code": None}


def test_denied_and_unknown_are_not_accepted():
    denied = RiskParentPort(Client(RiskParentCallResult("ANSWERED", parent_outcome="DENIED", code="risk_security_unavailable")), route="CTP___FUTURE")
    assert denied.reserve_parent(payload())["status"] == "REJECTED"
    unknown = RiskParentPort(Client(RiskParentCallResult("UNKNOWN", code="RISK_PARENT_UNAVAILABLE")), route="CTP___FUTURE")
    assert unknown.reserve_parent(payload())["status"] == "UNKNOWN"
    assert denied.reserve_child({}) == {"status": "UNKNOWN", "code": "risk_parent_not_reserved"}


def test_absolute_deadline_upgrades_request_to_schema_v2_with_lease():
    client = Client(RiskParentCallResult("ANSWERED", parent_outcome="RESERVED"))
    port = RiskParentPort(client, route="CTP___FUTURE")
    expires_at_ns = 2_000_000_000_000_000_000
    assert port.reserve_parent(payload(expires_at_ns=expires_at_ns)) == {
        "status": "ACCEPTED", "code": None}
    (view,) = client.seen
    assert view.schema_version == 2
    assert view.valid_until_ns == expires_at_ns
    assert view.offset == "OPEN"
    mapping = view.to_mapping()
    assert mapping["schema_version"] == 2 and mapping["valid_until_ns"] == expires_at_ns
    assert mapping["schema"] == "bt_api.risk_service.v2"


def test_without_deadline_stays_schema_v1():
    client = Client(RiskParentCallResult("ANSWERED", parent_outcome="RESERVED"))
    port = RiskParentPort(client, route="CTP___FUTURE")
    port.reserve_parent(payload())
    (view,) = client.seen
    assert view.schema_version == 1 and view.valid_until_ns is None
    assert view.to_mapping()["schema"] == "bt_api.risk_service.v1"


def test_child_reserve_dispatch_gate_on_formal_reservation():
    reserved = RiskParentPort(Client(RiskParentCallResult("ANSWERED", parent_outcome="RESERVED")),
                              route="CTP___FUTURE")
    rejected = RiskParentPort(Client(RiskParentCallResult("ANSWERED", parent_outcome="DENIED",
                                                          code="risk_security_unavailable")),
                              route="CTP___FUTURE")
    # Before any RESERVED answer, a child stays fail-closed as "not reserved".
    assert rejected.reserve_child({"parent_ref": "p-1"}) == {
        "status": "UNKNOWN", "code": "risk_parent_not_reserved"}
    assert rejected.dispatch_child({"parent_ref": "p-1"}) == {
        "status": "UNKNOWN", "code": "risk_parent_not_reserved"}
    # A REJECTED parent never becomes reserved, even on a later child.
    rejected.reserve_parent(payload())
    assert rejected.reserve_child({"parent_ref": "p-1"})["code"] == "risk_parent_not_reserved"
    # After a formal RESERVED, children are gated but the execution-origin child
    # forward path is still not fabricated.
    reserved.reserve_parent(payload())
    assert reserved.reserve_child({"parent_ref": "p-1"}) == {
        "status": "UNKNOWN", "code": "risk_parent_child_unavailable"}
    assert reserved.dispatch_child({"parent_ref": "p-1"}) == {
        "status": "UNKNOWN", "code": "risk_parent_child_unavailable"}
    # A different parent still fails closed even though p-1 is reserved.
    assert reserved.reserve_child({"parent_ref": "p-2"})["code"] == "risk_parent_not_reserved"
