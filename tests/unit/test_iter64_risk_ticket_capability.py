"""Local ticket-call capability checks with fake clients and temporary state."""

from types import MethodType

import pytest

from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_direct_client import build_gateway_order_intent
from tests.unit.test_iter63_gateway_api import order
from tests.unit.test_iter64_risk_service_client import facade as _facade

ROUTE = "CTP___FUTURE"
VERSION = "local-first-send-ticket-v1"
HOST_VERSION = "local-first-send-host-v1"


def _fake_save(_host, *_args, **_kwargs):
    raise AssertionError("SDK capability discovery must not persist an intent")


def _fake_host_contract(host):
    return (HOST_VERSION, MethodType(_fake_save, host),
            host.create_risk_reserve_intent, host.consume_risk_reserve_ticket)


def facade(monkeypatch, **kwargs):
    api, host, wire, gateway = _facade(monkeypatch, **kwargs)
    monkeypatch.setattr(host, "risk_first_send_host_contract", MethodType(_fake_host_contract, host), raising=False)
    return api, host, wire, gateway


def _old_reserve(self, exchange_name, request, *, ttl_seconds=10.0, timeout_seconds=5.0):
    raise AssertionError("legacy reserve must not be called by capability discovery")


def test_discovery_is_inert_and_returned_callable_consumes_ticket_once(monkeypatch):
    api, host, wire, gateway = facade(monkeypatch)
    before = (dict(host.intents), dict(host._risk_tickets), list(wire.calls), list(gateway))
    version, reserve, host_contract = api.risk_first_send_reserve_contract()
    assert version == VERSION and callable(reserve) and reserve.__self__ is api
    assert host_contract[0] == HOST_VERSION
    assert all(type(method) is MethodType and method.__self__ is host for method in host_contract[1:])
    assert (dict(host.intents), dict(host._risk_tickets), wire.calls, gateway) == before

    request = order()
    intent = build_gateway_order_intent(api._binding_object, api._route_object, request, ttl_seconds=10.0)
    ticket = host.create_risk_reserve_intent(intent)
    result = reserve(ROUTE, request, first_send_ticket=ticket)
    assert result.outcome == "UNKNOWN"
    assert host.load_intent(intent.request_key) == intent
    assert host._risk_tickets == {}
    assert wire.calls == [] and gateway == []


def test_one_declared_host_contract_shares_save_and_consume(monkeypatch):
    api, host, wire, gateway = _facade(monkeypatch)
    version, reserve, host_contract = api.risk_first_send_reserve_contract()
    request = order()
    intent = build_gateway_order_intent(api._binding_object, api._route_object, request, ttl_seconds=10.0)
    ticket = host_contract[1](intent, local_seq=1, bt_ref=1, snapshot={})
    assert version == VERSION and ticket is not None and host.load_intent(intent.request_key) == intent
    assert reserve(ROUTE, request, first_send_ticket=ticket).outcome == "UNKNOWN"
    assert host._risk_tickets == {} and intent.request_key in api._risk_uncertain_keys
    assert wire.calls == [] and gateway == []


def test_no_risk_client_has_no_ticket_contract(monkeypatch):
    api, host, _wire, gateway = facade(monkeypatch, risk=False)
    assert api.risk_first_send_reserve_contract() is None
    assert host.intents == {} and host._risk_tickets == {} and gateway == []


def test_legacy_host_without_explicit_contract_is_not_supported(monkeypatch):
    api, host, wire, gateway = _facade(monkeypatch)
    monkeypatch.setattr(host, "risk_first_send_host_contract", None)
    assert callable(host.create_risk_reserve_intent)
    assert callable(host.consume_risk_reserve_ticket)
    assert api.risk_first_send_reserve_contract() is None
    assert host.intents == {} and host._risk_tickets == {} and wire.calls == [] and gateway == []


@pytest.mark.parametrize("ticket", [None, object()])
def test_public_risk_reserve_with_old_host_stays_unknown(monkeypatch, ticket):
    api, host, wire, gateway = _facade(monkeypatch)
    monkeypatch.setattr(host, "risk_first_send_host_contract", None)
    result = api.reserve_order(ROUTE, order(), first_send_ticket=ticket)
    assert (result.outcome, result.code) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN")
    assert host.intents == {} and host._risk_tickets == {} and wire.calls == [] and gateway == []


def test_public_risk_reserve_retains_route_validation_for_old_host(monkeypatch):
    api, host, wire, gateway = _facade(monkeypatch)
    monkeypatch.setattr(host, "risk_first_send_host_contract", None)
    with pytest.raises(ValueError, match="^GATEWAY_ROUTE_MISMATCH$"):
        api.reserve_order("wrong-route", order())
    assert host.intents == {} and wire.calls == [] and gateway == []


def test_public_nonrisk_reserve_keeps_direct_path(monkeypatch):
    api, _host, _wire, gateway = facade(monkeypatch, risk=False)
    result = api.reserve_order(ROUTE, order())
    assert result.outcome == "REQUEST_DENIED"
    assert [call[0] for call in gateway] == ["reserve"]


def test_public_risk_reserve_no_fallback_when_check_changes_risk(monkeypatch):
    api, host, wire, gateway = _facade(monkeypatch)
    monkeypatch.setattr(api, "_check", lambda _exchange_name: setattr(api, "_risk", None))
    result = api.reserve_order(ROUTE, order())
    assert (result.outcome, result.code) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN")
    assert host.intents == {} and wire.calls == [] and gateway == []


def test_public_risk_reserve_old_host_declaration_error_is_unknown(monkeypatch):
    api, host, wire, gateway = _facade(monkeypatch)
    effects = []

    def bad_declaration(_host):
        effects.append("synthetic effect")
        raise RuntimeError("private detail")

    monkeypatch.setattr(host, "risk_first_send_host_contract", MethodType(bad_declaration, host))
    result = api.reserve_order(ROUTE, order())
    assert (result.outcome, result.code) == ("UNKNOWN", "RISK_OUTCOME_UNKNOWN")
    assert effects == ["synthetic effect"]
    assert host.intents == {} and wire.calls == [] and gateway == []


@pytest.mark.parametrize("bad", [None, (), (HOST_VERSION,), ("unknown", None, None, None),
                                 [HOST_VERSION, None, None, None]])
def test_invalid_host_contract_is_not_supported(monkeypatch, bad):
    api, host, wire, gateway = _facade(monkeypatch)
    monkeypatch.setattr(host, "risk_first_send_host_contract", MethodType(lambda _host: bad, host), raising=False)
    assert api.risk_first_send_reserve_contract() is None
    assert host.intents == {} and host._risk_tickets == {} and wire.calls == [] and gateway == []


@pytest.mark.parametrize("slot", [1, 2, 3])
def test_host_contract_rejects_foreign_bound_handle(monkeypatch, slot):
    api, host, wire, gateway = _facade(monkeypatch)
    _other_api, other_host, _other_wire, _other_gateway = _facade(monkeypatch)
    foreign = _fake_host_contract(other_host)[slot]

    def declare(_host):
        contract = list(_fake_host_contract(host))
        contract[slot] = foreign
        return tuple(contract)

    monkeypatch.setattr(host, "risk_first_send_host_contract", MethodType(declare, host), raising=False)
    assert api.risk_first_send_reserve_contract() is None
    assert host.intents == {} and host._risk_tickets == {} and wire.calls == [] and gateway == []


def test_raising_host_declaration_is_unavailable_without_exception_detail(monkeypatch):
    api, host, wire, gateway = _facade(monkeypatch)

    def declare(_host):
        raise RuntimeError("private host detail")

    monkeypatch.setattr(host, "risk_first_send_host_contract", MethodType(declare, host), raising=False)
    assert api.risk_first_send_reserve_contract() is None
    assert host.intents == {} and host._risk_tickets == {} and wire.calls == [] and gateway == []


def test_raising_host_declaration_property_is_not_supported(monkeypatch):
    api, host, wire, gateway = _facade(monkeypatch)

    def broken_property(_host):
        raise RuntimeError("private property detail")

    monkeypatch.setattr(type(host), "risk_first_send_host_contract", property(broken_property), raising=False)
    assert api.risk_first_send_reserve_contract() is None
    assert host.intents == {} and host._risk_tickets == {} and wire.calls == [] and gateway == []


def test_host_declaration_reentry_cannot_switch_facade_host(monkeypatch):
    api, host, wire, gateway = _facade(monkeypatch)
    _other_api, other_host, _other_wire, _other_gateway = _facade(monkeypatch)

    def declare(_host):
        api._host = other_host
        return _fake_host_contract(host)

    monkeypatch.setattr(host, "risk_first_send_host_contract", MethodType(declare, host), raising=False)
    assert api.risk_first_send_reserve_contract() is None
    assert host.intents == {} and host._risk_tickets == {} and wire.calls == [] and gateway == []


def test_frozen_ticket_contract_rejects_host_replacement(monkeypatch):
    api, host, wire, gateway = facade(monkeypatch)
    _other_api, other_host, _other_wire, _other_gateway = facade(monkeypatch)
    _version, reserve, host_contract = api.risk_first_send_reserve_contract()
    assert all(method.__self__ is host for method in host_contract[1:])
    api._host = other_host
    with pytest.raises(RuntimeError, match="^RISK_TICKET_CONTRACT_CHANGED$"):
        reserve(ROUTE, order(), first_send_ticket=object())
    assert host.intents == {} and other_host.intents == {} and wire.calls == [] and gateway == []


@pytest.mark.parametrize("name", ["reserve_order", "_risk_reserve"])
def test_instance_replacement_is_not_inferred_as_support(monkeypatch, name):
    api, host, wire, gateway = facade(monkeypatch)
    monkeypatch.setattr(api, name, MethodType(_old_reserve, api))
    assert api.risk_first_send_reserve_contract() is None
    assert host.intents == {} and host._risk_tickets == {}
    assert wire.calls == [] and gateway == []


@pytest.mark.parametrize("name", ["reserve_order", "_risk_reserve"])
def test_class_replacement_is_not_inferred_as_support(monkeypatch, name):
    api, host, wire, gateway = facade(monkeypatch)
    monkeypatch.setattr(GatewayBtApi, name, _old_reserve)
    assert api.risk_first_send_reserve_contract() is None
    assert host.intents == {} and host._risk_tickets == {}
    assert wire.calls == [] and gateway == []


@pytest.mark.parametrize("change", ["reserve_order", "_risk_reserve", "risk_client"])
def test_bound_capability_rechecks_before_call(monkeypatch, change):
    api, host, wire, gateway = facade(monkeypatch)
    _version, reserve, _host_contract = api.risk_first_send_reserve_contract()
    if change == "risk_client":
        api._risk = None
    else:
        monkeypatch.setattr(api, change, MethodType(_old_reserve, api))
    with pytest.raises(RuntimeError, match="^RISK_TICKET_CONTRACT_CHANGED$"):
        reserve(ROUTE, order(), first_send_ticket=object())
    assert host.intents == {} and host._risk_tickets == {}
    assert wire.calls == [] and gateway == []


@pytest.mark.parametrize("name", ["reserve_order", "_risk_reserve"])
def test_bound_capability_rechecks_class_implementation(monkeypatch, name):
    api, host, wire, gateway = facade(monkeypatch)
    _version, reserve, _host_contract = api.risk_first_send_reserve_contract()
    monkeypatch.setattr(GatewayBtApi, name, _old_reserve)
    with pytest.raises(RuntimeError, match="^RISK_TICKET_CONTRACT_CHANGED$"):
        reserve(ROUTE, order(), first_send_ticket=object())
    assert host.intents == {} and host._risk_tickets == {}
    assert wire.calls == [] and gateway == []


def test_bound_capability_cannot_be_rebound_to_another_instance(monkeypatch):
    api, host, wire, gateway = facade(monkeypatch)
    other, other_host, other_wire, other_gateway = facade(monkeypatch)
    _version, reserve, _host_contract = api.risk_first_send_reserve_contract()
    with pytest.raises(RuntimeError, match="^RISK_TICKET_CONTRACT_CHANGED$"):
        reserve.__func__(other, ROUTE, order(), first_send_ticket=object())
    assert host.intents == {} and host._risk_tickets == {}
    assert other_host.intents == {} and other_host._risk_tickets == {}
    assert wire.calls == [] and gateway == []
    assert other_wire.calls == [] and other_gateway == []


@pytest.mark.parametrize("ticket", [None, object()])
def test_reentrant_check_cannot_switch_to_direct_reserve(monkeypatch, ticket):
    api, host, wire, gateway = facade(monkeypatch)
    _version, reserve, _host_contract = api.risk_first_send_reserve_contract()
    checks = []

    def change_risk(_exchange_name):
        checks.append("called")
        api._risk = None

    monkeypatch.setattr(api, "_check", change_risk)
    with pytest.raises(RuntimeError, match="^RISK_TICKET_CONTRACT_CHANGED$"):
        reserve(ROUTE, order(), first_send_ticket=ticket)
    assert checks == ["called"]
    assert host.intents == {} and host._risk_tickets == {}
    assert wire.calls == [] and gateway == []


def test_reentrant_check_cannot_replace_ticket_consumer(monkeypatch):
    api, host, wire, gateway = facade(monkeypatch)
    _version, reserve, _host_contract = api.risk_first_send_reserve_contract()
    substituted_calls = []

    def substituted(*args, **kwargs):
        substituted_calls.append((args, kwargs))

    def change_consumer(_exchange_name):
        api._risk_reserve = substituted

    monkeypatch.setattr(api, "_check", change_consumer)
    with pytest.raises(RuntimeError, match="^RISK_TICKET_CONTRACT_CHANGED$"):
        reserve(ROUTE, order(), first_send_ticket=object())
    assert substituted_calls == [] and host.intents == {} and host._risk_tickets == {}
    assert wire.calls == [] and gateway == []


def test_check_side_effect_is_not_erased_or_retried_after_contract_change(monkeypatch):
    api, host, wire, gateway = facade(monkeypatch)
    _version, reserve, _host_contract = api.risk_first_send_reserve_contract()
    callbacks = []

    def change_after_side_effect(_exchange_name):
        callbacks.append("synthetic external effect")
        api._risk = None

    monkeypatch.setattr(api, "_check", change_after_side_effect)
    with pytest.raises(RuntimeError, match="^RISK_TICKET_CONTRACT_CHANGED$"):
        reserve(ROUTE, order(), first_send_ticket=object())
    # The effect is deliberately retained; callers must not infer NOT_SENT.
    assert callbacks == ["synthetic external effect"]
    assert host.intents == {} and wire.calls == [] and gateway == []


def test_host_callback_contract_change_preserves_consumed_intent(monkeypatch):
    api, host, wire, gateway = facade(monkeypatch)
    request = order()
    intent = build_gateway_order_intent(api._binding_object, api._route_object, request, ttl_seconds=10.0)
    ticket = host.create_risk_reserve_intent(intent)
    original_consume = host.consume_risk_reserve_ticket
    consumed = []

    def consume_then_change(_host, candidate, token):
        result = original_consume(candidate, token)
        consumed.append(result)
        api._risk = None
        return result

    monkeypatch.setattr(host, "consume_risk_reserve_ticket", MethodType(consume_then_change, host))
    _version, reserve, _host_contract = api.risk_first_send_reserve_contract()
    with pytest.raises(RuntimeError, match="^RISK_TICKET_CONTRACT_CHANGED$"):
        reserve(ROUTE, request, first_send_ticket=ticket)
    assert consumed == [True] and host.load_intent(intent.request_key) == intent
    assert host._risk_tickets == {} and intent.request_key in api._risk_uncertain_keys
    assert wire.calls == [] and gateway == []


def test_reentrant_host_read_cannot_consume_after_facade_host_changes(monkeypatch):
    api, host, wire, gateway = facade(monkeypatch)
    _other_api, other_host, _other_wire, _other_gateway = facade(monkeypatch)
    request = order()
    intent = build_gateway_order_intent(api._binding_object, api._route_object, request, ttl_seconds=10.0)
    ticket = host.create_risk_reserve_intent(intent)
    load = host.load_intent
    consume_calls = []
    consume = host.consume_risk_reserve_ticket

    def counted_consume(_host, candidate, token):
        consume_calls.append(candidate.request_key)
        return consume(candidate, token)

    monkeypatch.setattr(host, "consume_risk_reserve_ticket", MethodType(counted_consume, host))
    _version, reserve, _host_contract = api.risk_first_send_reserve_contract()

    def load_then_change(key):
        existing = load(key)
        api._host = other_host
        return existing

    monkeypatch.setattr(host, "load_intent", load_then_change)
    with pytest.raises(RuntimeError, match="^RISK_TICKET_CONTRACT_CHANGED$"):
        reserve(ROUTE, request, first_send_ticket=ticket)
    assert consume_calls == [] and load(intent.request_key) == intent
    assert host._risk_tickets[intent.request_key][1] is ticket
    assert intent.request_key in api._risk_uncertain_keys
    assert wire.calls == [] and gateway == []


def test_declared_host_guard_refuses_replaced_consumer_without_retry(monkeypatch):
    api, host, wire, gateway = _facade(monkeypatch)
    original_consume = host.consume_risk_reserve_ticket
    calls = []

    def checked_consume(bound_host, candidate, token):
        current = getattr(bound_host, "consume_risk_reserve_ticket", None)
        if (type(current) is not MethodType or current.__self__ is not bound_host
                or current.__func__ is not original_consume.__func__):
            raise RuntimeError("HOST_TICKET_CONTRACT_CHANGED")
        return original_consume(candidate, token)

    def declare(bound_host):
        return (HOST_VERSION, MethodType(_fake_save, bound_host),
                bound_host.create_risk_reserve_intent, MethodType(checked_consume, bound_host))

    monkeypatch.setattr(host, "risk_first_send_host_contract", MethodType(declare, host), raising=False)
    request = order()
    intent = build_gateway_order_intent(api._binding_object, api._route_object, request, ttl_seconds=10.0)
    ticket = host.create_risk_reserve_intent(intent)
    _version, reserve, _host_contract = api.risk_first_send_reserve_contract()

    def replacement(_host, candidate, token):
        calls.append((candidate, token))
        return True

    monkeypatch.setattr(host, "consume_risk_reserve_ticket", MethodType(replacement, host))
    assert reserve(ROUTE, request, first_send_ticket=ticket).outcome == "UNKNOWN"
    assert reserve(ROUTE, request, first_send_ticket=ticket).outcome == "UNKNOWN"
    assert calls == [] and host.load_intent(intent.request_key) == intent
    assert host._risk_tickets[intent.request_key][1] is ticket
    assert intent.request_key in api._risk_uncertain_keys
    assert wire.calls == [] and gateway == []


def test_old_api_and_undeclared_wrapper_cannot_inherit_support():
    class OldFacade:
        reserve_order = _old_reserve

    class UndeclaredWrapper(GatewayBtApi):
        pass

    old = OldFacade()
    assert getattr(old, "risk_first_send_reserve_contract", None) is None
    wrapper = object.__new__(UndeclaredWrapper)
    assert wrapper.risk_first_send_reserve_contract() is None


def test_undeclared_wrapper_property_is_not_evaluated():
    class RaisingWrapper(GatewayBtApi):
        @property
        def reserve_order(self):
            raise AssertionError("untrusted property must not be read")

    wrapper = object.__new__(RaisingWrapper)
    assert wrapper.risk_first_send_reserve_contract() is None


def test_explicit_wrapper_must_supply_its_own_contract():
    class ExplicitWrapper(GatewayBtApi):
        def risk_first_send_reserve_contract(self):
            return VERSION, self.reserve_with_ticket, "explicit-wrapper-host-contract"

        def reserve_with_ticket(self, exchange_name, request, *, first_send_ticket):
            return exchange_name, request, first_send_ticket

    wrapper = object.__new__(ExplicitWrapper)
    version, reserve, host_contract = wrapper.risk_first_send_reserve_contract()
    ticket = object()
    assert version == VERSION and reserve("route", "request", first_send_ticket=ticket) == (
        "route", "request", ticket
    )
    assert host_contract == "explicit-wrapper-host-contract"
    # Current BT facade binding separately requires exact GatewayBtApi type.
