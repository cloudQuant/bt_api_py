"""Provider-declared direct-write uncertainty, exercised without accounts."""

from types import SimpleNamespace

import pytest

import bt_api_base.direct_write_protocol as protocol
from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.write_admission import WriteAdmission


class Provider:
    def __init__(self, route, calls, policy):
        self.route, self.calls, self.policy = route, calls, policy
        self.error = None
        self.reads = 0

    @property
    def direct_write_error_policy(self):
        self.reads += 1
        if isinstance(self.policy, Exception):
            raise self.policy
        return self.policy

    def session_generation(self):
        self.calls.append("session")
        return 1

    def reserve_order(self, request):
        self.calls.append("reserve")
        identity = protocol.NativeOrderIdentity(self.route, "ref", {}, 1)
        return protocol.DirectOrderReservation(identity, "symbol", "r")

    def submit_order(self, reservation, request, grant, verifier):
        self.calls.append("submit")
        verifier.consume(
            grant,
            exchange_name=self.route,
            operation=protocol.WriteOperation.PLACE,
            session_generation=1,
        )
        if self.error:
            raise self.error
        return protocol.DirectSubmitResult(True, reservation.identity)

    def cancel_order(self, target, symbol, grant, verifier):
        self.calls.append("cancel")
        verifier.consume(
            grant,
            exchange_name=self.route,
            operation=protocol.WriteOperation.CANCEL,
            session_generation=1,
        )
        if self.error:
            raise self.error
        return protocol.DirectSubmitResult(True, target)

    def drain_execution_events(self, max_events):
        return ()


class Port:
    def __init__(self, calls):
        self.calls = calls

    def commit(self, record):
        self.calls.append("commit")
        return "marker"


def policy(place=frozenset(), cancel=frozenset()):
    cls = getattr(protocol, "DirectWriteErrorPolicy", None)
    if cls is None:  # The pre-change coordinator ignores this declaration.
        return SimpleNamespace(place_unknown_codes=place, cancel_unknown_codes=cancel)
    return cls(place_unknown_codes=place, cancel_unknown_codes=cancel)


def setup(route="REMOTE", declaration=None):
    calls = []
    provider = Provider(route, calls, declaration if declaration is not None else policy())
    admission = WriteAdmission({route: True})
    coordinator = DirectWriteCoordinator(admission, lambda _: provider)
    return coordinator, provider, calls


def invoke(coordinator, route, calls, operation):
    if operation == "place":
        return coordinator.place(route, object(), commit_port=Port(calls), host_scope="s")
    target = protocol.NativeOrderIdentity(route, "ref", {}, 1)
    return coordinator.cancel(route, target, "symbol", commit_port=Port(calls), host_scope="s")


@pytest.mark.parametrize("operation", ["place", "cancel"])
@pytest.mark.parametrize("code,expected", [("remote_timeout", None), ("definite_reject", False)])
def test_remote_classification(operation, code, expected):
    coordinator, provider, calls = setup(
        declaration=policy(frozenset({"remote_timeout"}), frozenset({"remote_timeout"}))
    )
    provider.error = protocol.DirectWriteError(code)
    outcome = invoke(coordinator, "REMOTE", calls, operation)
    assert outcome.sent is expected and outcome.error_code == code
    assert "commit" in calls


@pytest.mark.parametrize("operation", ["place", "cancel"])
@pytest.mark.parametrize("declaration", [object(), RuntimeError("getter failed")])
def test_invalid_declaration_has_no_write_side_effects(operation, declaration):
    coordinator, provider, calls = setup(declaration=declaration)
    with pytest.raises(
        protocol.DirectWriteError, match="direct_write_provider_declaration_invalid"
    ):
        invoke(coordinator, "REMOTE", calls, operation)
    assert calls == []


@pytest.mark.parametrize("operation", ["place", "cancel"])
def test_missing_declaration_has_no_write_side_effects(operation):
    calls = []
    provider = SimpleNamespace()
    coordinator = DirectWriteCoordinator(WriteAdmission({"REMOTE": True}), lambda _: provider)
    assert coordinator.provider("REMOTE") is provider  # read paths do not require write policy
    with pytest.raises(
        protocol.DirectWriteError, match="direct_write_provider_declaration_invalid"
    ):
        invoke(coordinator, "REMOTE", calls, operation)
    assert calls == []


def test_policy_is_cached_per_route_and_per_coordinator():
    calls = []
    first = Provider("A", calls, policy(frozenset({"timeout"})))
    second = Provider("B", calls, policy())
    first.error = second.error = protocol.DirectWriteError("timeout")
    coordinator = DirectWriteCoordinator(
        WriteAdmission({"A": True, "B": True}), {"A": first, "B": second}.__getitem__
    )
    assert invoke(coordinator, "A", calls, "place").sent is None
    first.policy = policy()
    assert invoke(coordinator, "A", calls, "place").sent is None
    assert invoke(coordinator, "B", calls, "place").sent is False
    assert first.reads == second.reads == 1
    other = DirectWriteCoordinator(WriteAdmission({"A": True}), lambda _: first)
    assert invoke(other, "A", calls, "place").sent is False
    assert first.reads == 2


@pytest.mark.parametrize("operation", ["place", "cancel"])
def test_admission_denial_stays_definitely_unsent(operation):
    coordinator, provider, calls = setup(
        declaration=policy(frozenset({"denied"}), frozenset({"denied"}))
    )
    provider.error = protocol.WriteAdmissionDenied("denied")
    assert invoke(coordinator, "REMOTE", calls, operation).sent is False


@pytest.mark.parametrize("operation", ["place", "cancel"])
def test_tampered_policy_rejected_before_any_write_side_effect(operation):
    declaration = policy()
    object.__setattr__(declaration, "cancel_unknown_codes", {"mutable"})
    coordinator, provider, calls = setup(declaration=declaration)
    with pytest.raises(
        protocol.DirectWriteError, match="direct_write_provider_declaration_invalid"
    ):
        invoke(coordinator, "REMOTE", calls, operation)
    assert calls == []


@pytest.mark.parametrize("operation", ["place", "cancel"])
def test_builtin_uncertain_exceptions_remain_unknown(operation):
    from bt_api_binance.direct_write_provider import (
        BinanceDirectOutcomeUnknown,
        BinanceDirectWriteProvider,
    )
    from bt_api_ctp.direct_write_provider import CtpDirectWriteProvider
    from bt_api_okx.direct_write_provider import OkxDirectOutcomeUnknown, OkxDirectWriteProvider

    for cls, error in (
        (
            CtpDirectWriteProvider,
            (
                protocol.DirectWriteError("ctp_direct_submit_outcome_unknown")
                if operation == "place"
                else RuntimeError("ctp cancel")
            ),
        ),
        (OkxDirectWriteProvider, OkxDirectOutcomeUnknown("unknown")),
        (BinanceDirectWriteProvider, BinanceDirectOutcomeUnknown("unknown")),
    ):
        coordinator, provider, calls = setup(declaration=cls.direct_write_error_policy)
        provider.error = error
        assert invoke(coordinator, "REMOTE", calls, operation).sent is None


def test_unknown_codes_are_specific_to_each_operation():
    declaration = policy(frozenset({"place_timeout"}), frozenset({"cancel_timeout"}))
    for operation, code, expected in (
        ("place", "place_timeout", None),
        ("cancel", "place_timeout", False),
        ("cancel", "cancel_timeout", None),
        ("place", "cancel_timeout", False),
    ):
        coordinator, provider, calls = setup(declaration=declaration)
        provider.error = protocol.DirectWriteError(code)
        assert invoke(coordinator, "REMOTE", calls, operation).sent is expected


@pytest.mark.parametrize("operation", ["place", "cancel"])
def test_invalid_policy_never_issues_grant(operation, monkeypatch):
    coordinator, _, calls = setup(declaration=object())
    monkeypatch.setattr(coordinator.admission, "issue", lambda *args: calls.append("grant"))
    with pytest.raises(
        protocol.DirectWriteError, match="direct_write_provider_declaration_invalid"
    ):
        invoke(coordinator, "REMOTE", calls, operation)
    assert calls == []
