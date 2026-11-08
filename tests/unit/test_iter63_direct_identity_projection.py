"""Provider-free projection of the existing strong correlation keys."""

import json

import pytest
from bt_api_base.direct_write_protocol import DirectWriteError, NativeOrderIdentity

from bt_api_py.direct_write import DirectWriteCoordinator, project_direct_strong_identity_keys
from bt_api_py.write_admission import WriteAdmission

ROUTE = "CTP___FUTURE"


def identity(day="20300101", **extra):
    fields = {"trading_day": day, "front_id": 1, "session_id": 7, "order_ref": "123"}
    fields.update(extra)
    return NativeOrderIdentity(ROUTE, "123", fields, 1)


def project(value, scoped=True):
    return project_direct_strong_identity_keys(value, day_scoped_ids=scoped)


def test_extra_native_metadata_does_not_change_session_key():
    original = identity()
    enriched = identity(instrument_id="rb", exchange_id="SHFE", offset="open")
    assert project(original) == project(enriched)
    with_venue = identity(exchange_id="SHFE", order_sys_id="s1")
    assert set(project(original)) < set(project(with_venue))
    assert [json.loads(value)[0] for value in project(with_venue)] == ["session", "venue"]
    assert project(identity(provider_metric=1.5)) == project(original)


def test_day_scope_changes_session_and_venue_keys_only_when_declared():
    first = identity("20300101", exchange_id="SHFE", order_sys_id="s1")
    second = identity("20300102", exchange_id="SHFE", order_sys_id="s1")
    assert set(project(first)).isdisjoint(project(second))
    assert project(first, False) == project(second, False)


def test_weak_day_ref_cannot_become_a_strong_candidate():
    weak = NativeOrderIdentity(ROUTE, "123", {"trading_day": "20300101", "front_id": 1}, 1)
    with pytest.raises(DirectWriteError, match="direct_identity_strong_key_unavailable"):
        project(weak)
    # Without a partial session field this is the correlator's strong day key.
    day_only = NativeOrderIdentity(ROUTE, "123", {"trading_day": "20300101"}, 1)
    assert json.loads(project(day_only)[0]) == ["day", ROUTE, "20300101", "123"]


@pytest.mark.parametrize("scope", [1, 0, None, "true", [], object()])
def test_scope_must_be_an_exact_bool(scope):
    with pytest.raises(DirectWriteError, match="direct_identity_strong_key_invalid"):
        project_direct_strong_identity_keys(identity(), day_scoped_ids=scope)


@pytest.mark.parametrize("part", [True, 1.0, float("nan"), "x" * 257, "\ud800", 2**900])
def test_unsafe_strong_key_components_are_refused(part):
    with pytest.raises(DirectWriteError, match="direct_identity_strong_key_invalid"):
        project(identity(front_id=part))


@pytest.mark.parametrize("field", ["front_id", "session_id", "exchange_id", "order_sys_id"])
@pytest.mark.parametrize("bad", [True, 1.5, "\ud800"])
def test_malformed_partial_candidates_are_invalid_even_without_strong_key(field, bad):
    partial = NativeOrderIdentity(ROUTE, "123", {"trading_day": "20300101", field: bad}, 1)
    with pytest.raises(DirectWriteError, match="direct_identity_strong_key_invalid"):
        project(partial)
    coordinator = DirectWriteCoordinator(WriteAdmission(), lambda _: object())
    with pytest.raises(DirectWriteError, match="direct_identity_strong_key_invalid"):
        coordinator.strong_identity_keys(ROUTE, partial)


def test_absent_partial_candidates_remain_unavailable():
    partial = NativeOrderIdentity(
        ROUTE, "123", {"trading_day": "20300101", "front_id": 1, "session_id": None}, 1
    )
    with pytest.raises(DirectWriteError, match="direct_identity_strong_key_unavailable"):
        project(partial)


def test_invalid_identity_is_rejected_with_finite_error():
    for bad in (None, {}, object()):
        with pytest.raises(DirectWriteError, match="direct_identity_strong_key_invalid"):
            project(bad)
    tampered = identity()
    object.__setattr__(tampered, "fields", None)
    with pytest.raises(DirectWriteError, match="direct_identity_strong_key_invalid"):
        project(tampered)

    class UnreadableFields:
        def get(self, name):
            raise RuntimeError("private source error")

    object.__setattr__(tampered, "fields", UnreadableFields())
    with pytest.raises(DirectWriteError, match="direct_identity_strong_key_invalid"):
        project(tampered)
    for field, bad in (("exchange_name", 9), ("client_order_id", 9)):
        tampered = identity()
        object.__setattr__(tampered, field, bad)
        with pytest.raises(DirectWriteError, match="direct_identity_strong_key_invalid"):
            project(tampered)


def test_scoped_day_requires_valid_ascii_day_and_unscoped_does_not():
    for bad_day in (None, "bad", 20300101):
        value = identity(bad_day)
        with pytest.raises(DirectWriteError, match="direct_identity_trading_day_unreadable"):
            project(value)
        assert project(value, False)


def test_projection_has_no_provider_or_correlator_registration(monkeypatch):
    monkeypatch.setattr(
        DirectWriteCoordinator, "provider", lambda *_: pytest.fail("provider touched")
    )
    keys = project(identity())
    assert len(keys) == 1 and json.loads(keys[0]) == ["session", ROUTE, "20300101", 1, 7, "123"]
