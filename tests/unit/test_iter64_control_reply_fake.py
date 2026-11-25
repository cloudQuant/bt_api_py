"""Synthetic Gateway control replies; no host, transport, or account resources."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bt_api_py.risk_service_gateway import GatewayControlAdapter
from tests.unit._iter64_control_binding_fake import (
    CONTROL_FINGERPRINT,
    BoundControlRuntimeMixin,
    control_route,
    seed_cause_binding,
    seed_operation_binding,
)


class FakeRuntime(BoundControlRuntimeMixin):
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def status(self):
        binding = SimpleNamespace(active=True, mount="direct", service_id="gateway",
                                  instance_id="instance", scope_ref="scope",
                                  operations=frozenset({"direct.control.apply",
                                                        "direct.control.status",
                                                        "direct.control.cancel_scope"}))
        return SimpleNamespace(state="running", healthy=True, bindings=[binding])

    def call(self, mount, operation, arguments, **kwargs):
        self.calls.append((operation, arguments))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def adapter(replies):
    runtime = FakeRuntime(replies)
    control = GatewayControlAdapter(
        runtime,
        mount="direct",
        service_id="gateway",
        instance_id="instance",
        scope_ref="scope",
        account_ref="account",
        timeout_seconds=1,
        route=control_route(
            "account", scope_ref="scope", service_id="gateway", instance_id="instance"
        ),
        server_key_fingerprint=CONTROL_FINGERPRINT,
    )
    seed_cause_binding(control, "cause")
    seed_operation_binding(control, "op")
    return control, runtime


def apply(value):
    return value.apply(cause_id="cause", mode="STOP_NEW", scope_kind="account", scope_value="")


def cancel(value, scope_kind="account", scope_value=""):
    return value.cancel_scope(operation_id="op", scope_kind=scope_kind, scope_value=scope_value)


def response(**changes):
    value = {"status": "OBSERVED", "operation_id": "op", "scope_kind": "account",
             "scope_value": "", "issued": 0,
             "counts": {"QUEUED": 0, "SENT": 0, "NOT_SENT": 0, "UNKNOWN": 0, "TERMINAL": 0}}
    value.update(changes)
    return value


@pytest.mark.parametrize("active", [None, 0, 1, 0.0, 1.0, "true"])
def test_apply_rejects_invalid_active(active):
    instance, _ = adapter([{"status": "OBSERVED", "cause_id": "cause", "version": 1, "active": active}])
    reply = apply(instance)
    assert (reply.outcome, reply.code) == ("UNKNOWN", "INVALID_OR_UNAVAILABLE_REPLY")


def test_apply_rejects_missing_active_and_wrong_cause():
    instance, _ = adapter([{"status": "OBSERVED", "cause_id": "cause", "version": 1},
                           {"status": "OBSERVED", "cause_id": "other", "version": 1, "active": True}])
    for _ in range(2):
        reply = apply(instance)
        assert (reply.outcome, reply.code) == ("UNKNOWN", "INVALID_OR_UNAVAILABLE_REPLY")


@pytest.mark.parametrize("active", [True, False])
def test_apply_preserves_bool(active):
    version = 1 if active else 2
    instance, _ = adapter([{"status": "OBSERVED", "cause_id": "cause", "version": version, "active": active}])
    reply = apply(instance)
    assert reply.outcome == "OBSERVED" and reply.active is active and reply.version == version
    assert (reply.scope_kind, reply.scope_value, reply.mode, reply.origin) == (None, None, None, None)


@pytest.mark.parametrize("field", ["status", "cause_id", "version", "active"])
def test_apply_rejects_each_missing_field(field):
    raw = {"status": "OBSERVED", "cause_id": "cause", "version": 1, "active": True}
    del raw[field]
    instance, _ = adapter([raw])
    reply = apply(instance)
    assert (reply.outcome, reply.code) == ("UNKNOWN", "INVALID_OR_UNAVAILABLE_REPLY")


@pytest.mark.parametrize("version", [-1, 0, True, False, "1", 1.0, None])
def test_apply_rejects_invalid_version(version):
    instance, _ = adapter([{"status": "OBSERVED", "cause_id": "cause", "version": version, "active": True}])
    reply = apply(instance)
    assert (reply.outcome, reply.code) == ("UNKNOWN", "INVALID_OR_UNAVAILABLE_REPLY")


def test_apply_rejects_extra_field_and_preserves_valid_inactive_version():
    instance, _ = adapter([{"status": "OBSERVED", "cause_id": "cause", "version": 1, "active": True, "extra": 0},
                           {"status": "OBSERVED", "cause_id": "cause", "version": 2, "active": False}])
    reply = apply(instance)
    assert (reply.outcome, reply.code) == ("UNKNOWN", "INVALID_OR_UNAVAILABLE_REPLY")
    reply = apply(instance)
    assert (reply.outcome, reply.active, reply.version) == ("OBSERVED", False, 2)


@pytest.mark.parametrize("active,version", [(True, 2), (False, 1), (True, 3), (False, 3)])
def test_apply_rejects_inconsistent_state_version(active, version):
    instance, _ = adapter([{"status": "OBSERVED", "cause_id": "cause", "version": version, "active": active}])
    reply = apply(instance)
    assert (reply.outcome, reply.code) == ("UNKNOWN", "INVALID_OR_UNAVAILABLE_REPLY")


def cause(active=1, **changes):
    value = {"account": "account", "cause_id": "cause", "scope_kind": "account", "scope_value": "",
             "mode": "STOP_NEW", "origin": "operator", "generation": "gen", "active": active,
             "version": 1 if active else 2, "resume_id": None if active else "resume"}
    value.update(changes)
    return value


@pytest.mark.parametrize("active", [0, 1, False, True])
def test_status_accepts_int_and_bool_active(active):
    row = cause(active)
    instance, _ = adapter([{"status": "OBSERVED", "cause": row}])
    reply = instance.status(cause_id="cause")
    assert (reply.outcome, reply.active, reply.version) == ("OBSERVED", bool(active), row["version"])


@pytest.mark.parametrize("active", [0.0, 1.0, -1, 2, None, "1"])
def test_status_rejects_invalid_active(active):
    instance, _ = adapter([{"status": "OBSERVED", "cause": cause(active=active)}])
    reply = instance.status(cause_id="cause")
    assert (reply.outcome, reply.code) == ("UNKNOWN", "INVALID_OR_UNAVAILABLE_REPLY")


@pytest.mark.parametrize("version", [None, -1, 0, True, False, 1.0, "1"])
def test_status_rejects_invalid_version(version):
    instance, _ = adapter([{"status": "OBSERVED", "cause": cause(version=version)}])
    reply = instance.status(cause_id="cause")
    assert (reply.outcome, reply.code) == ("UNKNOWN", "INVALID_OR_UNAVAILABLE_REPLY")


@pytest.mark.parametrize("raw", [
    {"status": "OBSERVED", "cause": cause(cause_id="other")},
    {"status": "OBSERVED", "cause": cause(), "version": 1},
    {"status": "OBSERVED", "cause": None},
    {"status": "OBSERVED"},
    {"status": "NOT_FOUND"},
    {"status": "NOT_FOUND", "cause_id": "other"},
    {"status": "NOT_FOUND", "cause_id": "cause", "version": 1},
])
def test_status_rejects_invalid_shapes_or_cause(raw):
    instance, _ = adapter([raw])
    reply = instance.status(cause_id="cause")
    assert (reply.outcome, reply.code) == ("UNKNOWN", "INVALID_OR_UNAVAILABLE_REPLY")


def test_status_not_found_maps_to_inactive_observation():
    instance, _ = adapter([{"status": "NOT_FOUND", "cause_id": "cause"}])
    reply = instance.status(cause_id="cause")
    assert reply.outcome == "OBSERVED" and reply.cause_id == "cause" and reply.active is False
    assert (reply.scope_kind, reply.scope_value, reply.mode, reply.origin) == (None, None, None, None)


@pytest.mark.parametrize("field", list(cause()))
def test_status_rejects_each_missing_cause_field(field):
    row = cause()
    del row[field]
    instance, _ = adapter([{"status": "OBSERVED", "cause": row}])
    reply = instance.status(cause_id="cause")
    assert (reply.outcome, reply.code) == ("UNKNOWN", "INVALID_OR_UNAVAILABLE_REPLY")


@pytest.mark.parametrize("row", [
    cause(extra=1), cause(account="other"), cause(active=1, version=2),
    cause(active=0, version=1), cause(active=1, version=3),
    cause(active=1, resume_id="resume"), cause(active=0, resume_id=None),
    cause(active=0, resume_id=""), cause(mode="CANCEL_ONLY"), cause(origin=0),
    cause(generation=""), cause(scope_kind="account", scope_value="bad"),
    cause(scope_kind="instrument", scope_value="bad"), cause(scope_kind="other"),
    cause(active=1, releaser="system:release"), cause(active=0, releaser="bad"),
])
def test_status_rejects_invalid_complete_cause(row):
    instance, _ = adapter([{"status": "OBSERVED", "cause": row}])
    reply = instance.status(cause_id="cause")
    assert (reply.outcome, reply.code) == ("UNKNOWN", "INVALID_OR_UNAVAILABLE_REPLY")


def test_status_accepts_released_cause_with_releaser():
    instance, _ = adapter([{"status": "OBSERVED", "cause": cause(active=0, releaser="system:release")}])
    reply = instance.status(cause_id="cause")
    assert (reply.outcome, reply.active, reply.version) == ("OBSERVED", False, 2)


@pytest.mark.parametrize("kind,value", [("instrument", "SHFE:rb"), ("strategy", "strat")])
def test_status_accepts_valid_scope_shapes_without_inferring_expected_scope(kind, value):
    instance, _ = adapter([{"status": "OBSERVED", "cause": cause(scope_kind=kind, scope_value=value)}])
    assert instance.status(cause_id="cause").outcome == "OBSERVED"


@pytest.mark.parametrize("row", [
    cause(scope_kind="account", scope_value="", mode="STOP_NEW", origin="operator:first"),
    cause(scope_kind="instrument", scope_value="SHFE:rb", mode="DRAIN", origin="system:other"),
    cause(scope_kind="strategy", scope_value="strat", mode="FREEZE", origin="another-origin"),
])
def test_status_projects_only_fields_in_verified_cause(row):
    instance, _ = adapter([{"status": "OBSERVED", "cause": row}])
    reply = instance.status(cause_id="cause")
    assert reply.outcome == "OBSERVED"
    assert (reply.scope_kind, reply.scope_value, reply.mode, reply.origin) == (
        row["scope_kind"], row["scope_value"], row["mode"], row["origin"])


def invalid_replies():
    for field in ("status", "operation_id", "scope_kind", "scope_value", "issued", "counts"):
        value = response()
        del value[field]
        yield value
    yield response(extra=1)
    yield response(status="OTHER")
    yield response(operation_id="other")
    yield response(scope_kind="instrument", scope_value="SHFE:rb")
    yield response(scope_value="wrong")
    for issued in (-1, 257, True, 1.0, "1", None):
        yield response(issued=issued)
    yield response(issued=1)  # Cannot issue more cancels than frozen targets.
    yield response(counts=[])
    for count in (-1, True, 1.0, "1", None, 2**63):
        counts = response()["counts"]
        counts["QUEUED"] = count
        yield response(counts=counts)
    counts = response()["counts"]
    del counts["QUEUED"]
    yield response(counts=counts)
    counts = response()["counts"]
    counts["OTHER"] = 0
    yield response(counts=counts)


@pytest.mark.parametrize("raw", list(invalid_replies()))
def test_cancel_rejects_invalid_reply_and_drops_old_cache(raw):
    instance, runtime = adapter([raw])
    instance.last_cancel_status["op"] = {"old": True}
    reply = cancel(instance)
    assert (reply.outcome, reply.code) == ("UNKNOWN", "INVALID_OR_UNAVAILABLE_REPLY")
    assert "op" not in instance.last_cancel_status
    assert len(runtime.calls) == 1


def test_cancel_valid_empty_and_multi_target_copy_and_scope_binding():
    empty = response()
    counts = {"QUEUED": 3, "SENT": 2, "NOT_SENT": 1, "UNKNOWN": 1, "TERMINAL": 2}
    multi = response(issued=4, counts=counts)
    expected = deepcopy(multi)
    instance, runtime = adapter([empty, multi])
    assert cancel(instance).outcome == "OBSERVED"
    assert instance.last_cancel_status["op"] == empty
    reply = cancel(instance)
    assert (reply.outcome, reply.code) == ("UNKNOWN", "CANCEL_SCOPE_INCOMPLETE")
    multi["counts"]["QUEUED"] = 999
    assert instance.last_cancel_status["op"] == expected
    assert runtime.calls[-1][1] == {"operation_id": "op", "limit": 256}
    reply = cancel(instance, "instrument", "SHFE:rb")
    assert (reply.outcome, reply.code) == ("REQUEST_DENIED", "RISK_CONTROL_SCOPE_UNSUPPORTED")
    assert len(runtime.calls) == 2


@pytest.mark.parametrize("state", ["QUEUED", "UNKNOWN", "NOT_SENT"])
def test_cancel_incomplete_counts_cached_without_retry(state):
    counts = response()["counts"]
    counts[state] = 1
    raw = response(counts=counts)
    instance, runtime = adapter([raw])
    reply = cancel(instance)
    assert (reply.outcome, reply.code) == ("UNKNOWN", "CANCEL_SCOPE_INCOMPLETE")
    assert instance.last_cancel_status["op"] == raw
    assert len(runtime.calls) == 1


def test_cancel_more_than_one_page_remains_unknown_without_retry():
    counts = {"QUEUED": 1, "SENT": 256, "NOT_SENT": 0, "UNKNOWN": 0, "TERMINAL": 0}
    raw = response(issued=256, counts=counts)
    instance, runtime = adapter([raw])
    reply = cancel(instance)
    assert (reply.outcome, reply.code) == ("UNKNOWN", "CANCEL_SCOPE_INCOMPLETE")
    assert instance.last_cancel_status["op"] == raw
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("sent,terminal", [(0, 0), (1, 0), (0, 1), (256, 5)])
def test_cancel_only_sent_or_terminal_counts_are_observed(sent, terminal):
    counts = {"QUEUED": 0, "SENT": sent, "NOT_SENT": 0, "UNKNOWN": 0, "TERMINAL": terminal}
    raw = response(issued=min(sent, 256), counts=counts)
    instance, runtime = adapter([raw])
    assert cancel(instance).outcome == "OBSERVED"
    assert instance.last_cancel_status["op"] == raw
    assert len(runtime.calls) == 1


def test_cancel_transport_failure_drops_old_cache_only_for_current_operation():
    instance, runtime = adapter([RuntimeError("unavailable")])
    instance.last_cancel_status.update({"op": {"old": True}, "other": {"old": True}})
    assert cancel(instance).outcome == "UNKNOWN"
    assert instance.last_cancel_status == {"other": {"old": True}}
    assert len(runtime.calls) == 1
