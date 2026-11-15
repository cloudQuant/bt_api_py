"""Pure-fake checks for Risk Web admin scope and registration binding."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bt_api_base.risk_service_protocol import RISK_ADMIN_OPERATIONS
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.risk_web_local_host import RISK_ADMIN_MOUNT, build_admin_caller
from bt_api_py.service_runtime import ServiceRuntimeError, ServiceRuntimeRequestNotSentError
from bt_api_py.service_runtime_thread import BindingSnapshot, ThreadedRuntimeError
from bt_api_risk.web.app import RiskAdminCallError
from _iter64_web_bound_fake import fake_bound_call


IDENTITY = WireIdentity(WireRole.APPLICATION_CLIENT, "operator", "web")
PEER = WireIdentity(WireRole.SERVICE, "risk", "risk-service")
SCOPE = WireScope("domain-a", account_ref="account-a")


def binding(scope=SCOPE, **changes):
    original = BindingSnapshot(
        registration_id="registration", service_epoch="epoch", active=True,
        mount=RISK_ADMIN_MOUNT, service_id="risk-service", instance_id="risk-instance",
        scope_ref="scope-ref", scope=scope, operations=frozenset(RISK_ADMIN_OPERATIONS),
        required=True, authenticated_peer=PEER, server_key_fingerprint="a" * 64,
    )
    return replace(original, **changes)


class FakeRuntime:
    call_bound = fake_bound_call

    def __init__(self):
        self.binding = binding()
        self.on_call = lambda: None
        self.error = None
        self.calls = []
        self.run_id = "runtime-run"

    def status(self):
        return SimpleNamespace(state="running", thread_alive=True, healthy=True,
                               identity=IDENTITY, bindings=(self.binding,), run_id=self.run_id)

    def call(self, mount, operation, arguments, **kwargs):
        self.calls.append((mount, operation, arguments, kwargs))
        self.on_call()
        if self.error is not None:
            raise self.error
        return {"status": "OBSERVED"}


def caller(runtime, scope=SCOPE):
    return build_admin_caller(runtime, identity=IDENTITY, scope=scope, scope_ref="scope-ref",
                              service_id="risk-service", instance_id="risk-instance", peer=PEER)


def result(call):
    with pytest.raises(RiskAdminCallError) as caught:
        call("risk.admin.status", {}, 1.0)
    return caught.value.kind, caught.value.code


FOREIGN_SCOPES = (
    WireScope("domain-b", account_ref="account-a"),
    WireScope("domain-a", account_ref="account-b"),
    WireScope("domain-a", account_refs=("account-a",)),
    WireScope("domain-a", account_ref="account-a", strategy_ref="s", run_id="r"),
    WireScope("domain-a", account_ref="account-a", manual_book_ref="manual"),
    WireScope("domain-a", account_ref="account-a", portfolio_ref="portfolio"),
    WireScope("domain-a", account_ref="account-a", portfolio_ref="portfolio", group_id="group", leg_id="leg"),
)


@pytest.mark.parametrize("foreign", FOREIGN_SCOPES)
def test_binding_scope_mismatch_is_not_sent(foreign):
    runtime = FakeRuntime()
    runtime.binding = binding(scope=foreign)
    assert result(caller(runtime)) == ("NOT_SENT", "ROUTE_UNAVAILABLE")
    assert runtime.calls == []


@pytest.mark.parametrize("foreign", FOREIGN_SCOPES)
def test_scope_change_during_call_is_unknown(foreign):
    runtime = FakeRuntime()
    runtime.on_call = lambda: setattr(runtime, "binding", binding(scope=foreign))
    assert result(caller(runtime))[0] == "UNKNOWN"
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("scope", FOREIGN_SCOPES[2:])
def test_configuration_requires_exact_single_account_scope(scope):
    with pytest.raises(ValueError, match="INVALID_RISK_ADMIN_ROUTE"):
        caller(FakeRuntime(), scope)


def test_equal_independent_scope_sends_once():
    runtime = FakeRuntime()
    runtime.binding = binding(scope=WireScope("domain-a", account_ref="account-a"))
    assert caller(runtime)("risk.admin.status", {}, 1.0) == {"status": "OBSERVED"}
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("run_id", [None, "", " padded", True, 1, "x" * 257, "\ud800"])
def test_missing_or_invalid_runtime_generation_is_not_sent(run_id):
    runtime = FakeRuntime()
    runtime.run_id = run_id
    assert result(caller(runtime)) == ("NOT_SENT", "ROUTE_UNAVAILABLE")
    assert runtime.calls == []


@pytest.mark.parametrize("required", [False, None, 1])
def test_registration_must_be_a_required_binding(required):
    runtime = FakeRuntime()
    runtime.binding = binding(required=required)
    assert result(caller(runtime)) == ("NOT_SENT", "ROUTE_UNAVAILABLE")
    assert runtime.calls == []


@pytest.mark.parametrize("run_id", [None, "", True])
def test_generation_becoming_unavailable_during_call_is_unknown(run_id):
    runtime = FakeRuntime()
    runtime.on_call = lambda: setattr(runtime, "run_id", run_id)
    assert result(caller(runtime))[0] == "UNKNOWN"
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("change", [
    lambda r: setattr(r, "binding", replace(r.binding, registration_id="other")),
    lambda r: setattr(r, "binding", replace(r.binding, service_epoch="other")),
    lambda r: setattr(r, "binding", replace(r.binding, server_key_fingerprint="b" * 64)),
    lambda r: setattr(r, "binding", replace(r.binding, operations=frozenset({"risk.admin.status"}))),
    lambda r: setattr(r, "binding", replace(r.binding, required=False)),
    lambda r: setattr(r, "binding", replace(r.binding, required=None)),
    lambda r: setattr(r, "run_id", "other-run"),
])
def test_binding_generation_change_is_unknown(change):
    runtime = FakeRuntime()
    runtime.on_call = lambda: change(runtime)
    assert result(caller(runtime))[0] == "UNKNOWN"
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("error", [ServiceRuntimeRequestNotSentError(),
                                    ThreadedRuntimeError("QUEUE_FULL"),
                                    ThreadedRuntimeError("TIMEOUT_AFTER_START")])
def test_changed_scope_during_error_is_unknown(error):
    runtime = FakeRuntime()
    runtime.error = error
    runtime.on_call = lambda: setattr(runtime, "binding", binding(scope=FOREIGN_SCOPES[0]))
    assert result(caller(runtime))[0] == "UNKNOWN"
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("code", [[], {}, None, True, 1, type("ForeignCode", (str,), {})("QUEUE_FULL")])
def test_malformed_threaded_error_code_is_finite_unknown(code):
    runtime = FakeRuntime()
    runtime.error = ThreadedRuntimeError(code)
    assert result(caller(runtime)) == ("UNKNOWN", "")
    assert len(runtime.calls) == 1


@pytest.mark.parametrize("code, expected", [("QUEUE_FULL", "NOT_SENT"),
                                           ("TIMEOUT_AFTER_START", "UNKNOWN"),
                                           ("OWNER_OUTCOME_UNKNOWN", "UNKNOWN")])
def test_valid_threaded_error_codes_keep_outcome(code, expected):
    runtime = FakeRuntime()
    runtime.error = ThreadedRuntimeError(code)
    assert result(caller(runtime))[0] == expected
    assert len(runtime.calls) == 1


def test_refusal_after_scope_change_is_unknown():
    runtime = FakeRuntime()
    error = ServiceRuntimeError("DENIED")
    error.service_reply_code = "DENIED"
    runtime.error = error
    runtime.on_call = lambda: setattr(runtime, "binding", binding(scope=FOREIGN_SCOPES[0]))
    assert result(caller(runtime))[0] == "UNKNOWN"


def test_original_scope_mutation_cannot_redirect_a_call():
    configured = WireScope("domain-a", account_ref="account-a")
    runtime = FakeRuntime()
    call = caller(runtime, configured)
    object.__setattr__(configured, "account_ref", "account-b")
    assert call("risk.admin.status", {}, 1.0) == {"status": "OBSERVED"}
    assert runtime.calls[0][3]["route"].scope.account_ref == "account-a"


def test_mutating_bound_scope_in_place_during_call_is_unknown():
    mutable_scope = WireScope("domain-a", account_ref="account-a")
    runtime = FakeRuntime()
    runtime.binding = binding(scope=mutable_scope)
    runtime.on_call = lambda: object.__setattr__(mutable_scope, "execution_domain_ref", "domain-b")
    assert result(caller(runtime))[0] == "UNKNOWN"
