"""Read-only projection of the coordinator's strong correlation identities."""

import json

import pytest
from bt_api_base.direct_write_protocol import DirectWriteError, NativeOrderIdentity

from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.write_admission import WriteAdmission

ROUTE = "CTP___FUTURE"


class Provider:
    def __init__(self, scoped):
        self.execution_ids_trading_day_scoped = scoped

    def __getattr__(self, name):
        raise AssertionError(f"unexpected provider operation: {name}")


def coordinator(scoped=True):
    return DirectWriteCoordinator(WriteAdmission(), lambda _: Provider(scoped))


def identity(day="20261106", **fields):
    return NativeOrderIdentity(ROUTE, "ref", {"trading_day": day, **fields})


def decode(keys):
    return [json.loads(key) for key in keys]


def test_ctp_session_and_venue_keys_are_day_scoped_and_weak_key_is_excluded():
    coord = coordinator()
    fields = {"front_id": 1, "session_id": 7, "exchange_id": "SHFE", "order_sys_id": "S1"}
    first = coord.strong_identity_keys(ROUTE, identity("20261106", **fields))
    second = coord.strong_identity_keys(ROUTE, identity("20261107", **fields))
    assert first == tuple(sorted(set(first)))
    assert decode(first) == [
        ["session", ROUTE, "20261106", 1, 7, "ref"],
        ["venue", ROUTE, "20261106", "SHFE", "S1"],
    ]
    assert decode(second) == [
        ["session", ROUTE, "20261107", 1, 7, "ref"],
        ["venue", ROUTE, "20261107", "SHFE", "S1"],
    ]
    assert set(first).isdisjoint(second)


def test_venue_only_and_unscoped_strong_day_key():
    coord = coordinator()
    venue = NativeOrderIdentity(
        ROUTE, None, {"trading_day": "20261106", "exchange_id": "SHFE", "order_sys_id": "S1"}
    )
    assert decode(coord.strong_identity_keys(ROUTE, venue)) == [
        ["venue", ROUTE, "20261106", "SHFE", "S1"]
    ]
    assert decode(coord.strong_identity_keys(ROUTE, identity())) == [
        ["day", ROUTE, "20261106", "ref"]
    ]


def test_other_route_session_and_venue_keys_do_not_rotate_with_day():
    coord = coordinator(scoped=False)
    fields = {"front_id": 1, "session_id": 7, "exchange_id": "SHFE", "order_sys_id": "S1"}
    first = coord.strong_identity_keys(ROUTE, identity("20261106", **fields))
    second = coord.strong_identity_keys(ROUTE, identity("20261107", **fields))
    assert first == second
    assert decode(first) == [["session", ROUTE, 1, 7, "ref"], ["venue", ROUTE, "SHFE", "S1"]]


def test_weak_only_and_absent_keys_are_rejected():
    coord = coordinator()
    for item in (
        identity(front_id=1),
        NativeOrderIdentity(ROUTE, None, {"trading_day": "20261106"}),
    ):
        with pytest.raises(DirectWriteError, match="direct_identity_strong_key_unavailable"):
            coord.strong_identity_keys(ROUTE, item)


@pytest.mark.parametrize("day", [None, "", "2026-11-06", "2026110x", 20261106])
def test_scoped_day_missing_or_bad_is_refused(day):
    coord = coordinator()
    with pytest.raises(DirectWriteError, match="direct_identity_trading_day_unreadable"):
        coord.strong_identity_keys(ROUTE, identity(day, front_id=1, session_id=7))


def test_route_conflict_does_not_even_create_provider():
    coord = DirectWriteCoordinator(WriteAdmission(), lambda _: pytest.fail("provider touched"))
    with pytest.raises(DirectWriteError, match="direct_identity_route_mismatch"):
        coord.strong_identity_keys("OTHER", identity(front_id=1, session_id=7))


@pytest.mark.parametrize("component", [True, 1.0, float("nan"), "x" * 257, "\ud800", 2**900])
def test_bad_or_oversized_strong_key_fails_closed(component):
    coord = coordinator()
    with pytest.raises(DirectWriteError, match="direct_identity_strong_key_invalid"):
        coord.strong_identity_keys(ROUTE, identity(front_id=component, session_id=7))


def test_ascii_canonical_json_and_serialized_length_limit():
    coord = coordinator()
    item = identity(front_id=1, session_id=7, order_ref="中")
    assert coord.strong_identity_keys(ROUTE, item) == (
        '["session","CTP___FUTURE","20261106",1,7,"\\u4e2d"]',
    )
    with pytest.raises(DirectWriteError, match="direct_identity_strong_key_invalid"):
        coord.strong_identity_keys(ROUTE, identity(front_id=1, session_id=7, order_ref="中" * 45))


def test_projection_does_not_register_deduplicate_or_grant(monkeypatch):
    coord = coordinator()
    monkeypatch.setattr(coord.admission, "issue", lambda *args: pytest.fail("grant issued"))
    coord.strong_identity_keys(ROUTE, identity(front_id=1, session_id=7))
    correlator = coord._correlators[ROUTE]
    assert not correlator._by_key and not correlator._weak_keys and not correlator._seen


@pytest.mark.parametrize("declaration", ["true", None, 1])
def test_invalid_day_scope_declaration_is_rejected(declaration):
    coord = DirectWriteCoordinator(WriteAdmission(), lambda _: Provider(declaration))
    with pytest.raises(DirectWriteError, match="direct_write_provider_declaration_invalid"):
        coord.strong_identity_keys(ROUTE, identity(front_id=1, session_id=7))


def test_unreadable_day_scope_declaration_is_rejected():
    class BrokenProvider:
        @property
        def execution_ids_trading_day_scoped(self):
            raise RuntimeError("unreadable")

    coord = DirectWriteCoordinator(WriteAdmission(), lambda _: BrokenProvider())
    with pytest.raises(DirectWriteError, match="direct_write_provider_declaration_invalid"):
        coord.strong_identity_keys(ROUTE, identity(front_id=1, session_id=7))
