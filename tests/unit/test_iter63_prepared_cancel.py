"""Offline prepared cancel coordination; no native network or account."""
from dataclasses import replace

import pytest

from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
from bt_api_base.direct_write_protocol import (
    DirectSubmitResult, DirectWriteError, NativeOrderIdentity, WriteAdmissionDenied, WriteOperation,
)
from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.write_admission import WriteAdmission

ROUTE = "CTP"
TARGET = NativeOrderIdentity(ROUTE, "ref-old", {"trading_day": "20261107", "front_id": 1, "session_id": 2}, 3)


class Provider:
    def __init__(self):
        self.calls = []
        self.generation = 5
        self.day = "20261108"
        self.result = DirectSubmitResult(True, TARGET)
        self.prepared = None
        self.prepare_hook = None
        self.submit_hook = None

    def session_generation(self):
        return self.generation

    def trading_day(self):
        return self.day

    def prepare_cancel_order(self, target, symbol):
        self.calls.append("prepare")
        self.prepared = PreparedCancelRequest("reservation", target, symbol, self.day, self.generation,
                                             {"RequestID": 42, "OrderActionRef": 42}, ("attempt-42",))
        if self.prepare_hook:
            self.prepare_hook(self.prepared)
        return self.prepared

    def release_cancel_reservation(self, prepared):
        self.calls.append("release")
        assert prepared is self.prepared

    def submit_prepared_cancel(self, prepared, grant, verifier):
        self.calls.append("submit")
        assert prepared is self.prepared
        verifier.consume(grant, exchange_name=ROUTE, operation=WriteOperation.CANCEL,
                         session_generation=self.generation)
        if self.submit_hook:
            self.submit_hook(prepared)
        return self.result

    def cancel_order(self, *args):
        raise AssertionError("legacy cancel must not run")


class Port:
    def __init__(self, provider):
        self.provider = provider
        self.record = None
        self.hook = None
        self.marker = "marker"

    def commit(self, record):
        self.provider.calls.append("commit")
        self.record = record
        assert record.prepared_cancel is self.provider.prepared
        if self.hook:
            self.hook(record)
        return self.marker


@pytest.fixture
def setup():
    provider = Provider()
    admission = WriteAdmission({ROUTE: True})
    coordinator = DirectWriteCoordinator(admission, lambda route: provider)
    return coordinator, provider, admission, Port(provider)


def call(setup):
    coordinator, _, _, port = setup
    return coordinator.cancel_prepared(ROUTE, TARGET, "IF2612", commit_port=port, host_scope="strategy")


def test_prepare_commit_authorize_submit_exact_original_target_and_new_day(setup):
    coordinator, provider, _, port = setup
    outcome = call(setup)
    assert outcome.sent is True and outcome.marker_id == "marker" and outcome.identity == TARGET
    assert provider.calls == ["prepare", "commit", "submit"]
    assert port.record.trading_day == "20261108"
    assert port.record.target_identity.fields["trading_day"] == "20261107"
    assert port.record.prepared_cancel is provider.prepared
    assert coordinator._admission._outstanding == {}


def test_commit_failure_releases_without_send(setup):
    _, provider, _, port = setup
    port.marker = None
    outcome = call(setup)
    assert outcome.sent is False and outcome.error_code == "pre_dispatch_commit_failed"
    assert provider.calls == ["prepare", "commit", "release"]


@pytest.mark.parametrize("fault", ["generation", "day", "grant", "record", "prepared"])
def test_pre_submit_mutations_fail_closed_and_release(setup, fault):
    _, provider, admission, port = setup
    def change(record):
        if fault == "generation":
            provider.generation = 6
        elif fault == "day":
            provider.day = "20261109"
        elif fault == "grant":
            admission._enabled[ROUTE] = False
        elif fault == "record":
            object.__setattr__(record, "host_scope", "changed")
        else:
            object.__setattr__(record.prepared_cancel, "symbol", "other")
    port.hook = change
    outcome = call(setup)
    assert outcome.sent is False and outcome.marker_id == "marker"
    assert provider.calls == ["prepare", "commit", "release"]
    assert admission._outstanding == {}


def test_bad_preparation_is_released_before_commit(setup):
    _, provider, _, _ = setup
    provider.prepare_hook = lambda prepared: object.__setattr__(prepared, "symbol", "other")
    outcome = call(setup)
    assert outcome.sent is False and outcome.marker_id is None
    assert provider.calls == ["prepare", "release"]


def test_missing_provider_ports_never_falls_back():
    class OldProvider:
        def cancel_order(self, *args):
            raise AssertionError("legacy cancel must not run")
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda route: OldProvider())
    with pytest.raises(DirectWriteError, match="direct_prepared_cancel_unavailable"):
        coordinator.cancel_prepared(ROUTE, TARGET, "IF2612", commit_port=object(), host_scope="strategy")


@pytest.mark.parametrize("sent", [True, False, None])
def test_exact_provider_results_keep_three_outcomes(setup, sent):
    _, provider, _, _ = setup
    provider.result = DirectSubmitResult(sent, TARGET)
    assert call(setup).sent is sent
    assert provider.calls == ["prepare", "commit", "submit"]


@pytest.mark.parametrize("result", [DirectSubmitResult(True, None), DirectSubmitResult(1, TARGET),
    DirectSubmitResult(True, replace(TARGET, client_order_id="other")), object()])
def test_invalid_provider_result_is_unknown(setup, result):
    _, provider, _, _ = setup
    provider.result = result
    outcome = call(setup)
    assert outcome.sent is None and outcome.error_code == "direct_cancel_outcome_unknown"
    assert provider.calls == ["prepare", "commit", "submit"]


def test_submit_exception_is_unknown_no_release_or_retry(setup):
    _, provider, admission, _ = setup
    def fail(_prepared):
        raise RuntimeError("native outcome uncertain")
    provider.submit_hook = fail
    outcome = call(setup)
    assert outcome.sent is None and outcome.marker_id == "marker"
    assert provider.calls == ["prepare", "commit", "submit"]
    assert admission._outstanding == {}


def test_disabled_admission_does_not_prepare():
    provider = Provider()
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: False}), lambda route: provider)
    with pytest.raises(WriteAdmissionDenied):
        coordinator.cancel_prepared(ROUTE, TARGET, "IF2612", commit_port=Port(provider), host_scope="strategy")
    assert provider.calls == []


@pytest.mark.parametrize("fault", ["generation", "admission", "prepared", "record"])
def test_grant_issue_reentry_cannot_send_and_revokes_grant(setup, monkeypatch, fault):
    _, provider, admission, port = setup
    issue = admission.issue
    def changed_issue(*args, **kwargs):
        grant = issue(*args, **kwargs)
        if fault == "generation":
            provider.generation += 1
        elif fault == "admission":
            admission._enabled[ROUTE] = False
        elif fault == "prepared":
            object.__setattr__(provider.prepared, "symbol", "other")
        else:
            object.__setattr__(port.record, "host_scope", "changed")
        return grant
    monkeypatch.setattr(admission, "issue", changed_issue)
    outcome = call(setup)
    assert outcome.sent is False and outcome.marker_id == "marker"
    assert provider.calls == ["prepare", "commit", "release"]
    assert admission._outstanding == {}


def test_untrusted_exception_after_submit_is_always_unknown(setup):
    _, provider, _, _ = setup
    def fail(_prepared):
        raise DirectWriteError("undeclared_error")
    provider.submit_hook = fail
    outcome = call(setup)
    assert outcome.sent is None and outcome.error_code == "direct_cancel_outcome_unknown"
    assert provider.calls == ["prepare", "commit", "submit"]
