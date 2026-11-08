"""Offline session-bound settlement write admission and marker fences."""

from __future__ import annotations

from dataclasses import replace

import pytest
from bt_api_base.direct_write_protocol import (
    SettlementObservation,
    SettlementStatus,
    SettlementWriteBinding,
    WriteAdmissionDenied,
    WriteOperation,
)

from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.write_admission import WriteAdmission
from tests.test_iter62_direct_write import ROUTE, FakePort, FakeProvider


def setup(enabled=True):
    calls = []
    provider = FakeProvider(calls)
    provider.settlement = SettlementStatus.UNCONFIRMED
    admission = WriteAdmission({ROUTE: enabled})
    return DirectWriteCoordinator(admission, lambda _: provider), provider, calls, admission


def _observe(provider):
    return provider.observe_settlement()


def test_success_records_exact_observation_and_consumes_bound_grant():
    coordinator, provider, calls, _ = setup()
    observation = _observe(provider)
    port = FakePort(calls)
    confirm = provider.confirm_settlement
    captured = []

    def confirm_bound(grant, verifier, *, observation):
        captured.append((grant.settlement_binding, observation))
        return confirm(grant, verifier, observation=observation)

    provider.confirm_settlement = confirm_bound
    provider.observe_settlement = lambda: pytest.fail("unexpected second observation")
    assert (
        coordinator.ensure_settlement(
            ROUTE, commit_port=port, host_scope="scope", observation=observation
        )
        is SettlementStatus.CONFIRMED
    )
    assert calls == ["commit", "confirm_settlement"]
    binding, supplied = captured[0]
    assert binding.observation is supplied is observation
    assert binding.marker_id == "marker-1"
    record = port.records[0]
    assert record.trading_day == observation.trading_day
    assert record.session_generation == observation.session_generation
    assert dict(record.request_summary) == {
        "account_binding": observation.account_binding,
        "observation_id": observation.observation_id,
    }


def test_session_change_after_observe_before_commit_never_sends():
    coordinator, provider, calls, _ = setup()
    read = provider.observe_settlement

    def stale():
        observation = read()
        provider.generation += 1
        return observation

    provider.observe_settlement = stale
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == []


def test_session_change_during_commit_preserves_marker_without_send():
    coordinator, provider, calls, _ = setup()

    class ChangingPort(FakePort):
        def commit(self, record):
            marker = super().commit(record)
            provider.generation += 1
            return marker

    port = ChangingPort(calls)
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=port, host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == ["commit"] and len(port.records) == 1


def test_session_change_during_grant_discards_unconsumed_grant():
    coordinator, provider, calls, admission = setup()
    issue = admission.issue
    captured = []

    def stale_issue(*args, **kwargs):
        grant = issue(*args, **kwargs)
        captured.append(grant)
        provider.generation += 1
        return grant

    admission.issue = stale_issue
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == ["commit"] and len(captured) == 1
    with pytest.raises(WriteAdmissionDenied, match="invalid_or_consumed"):
        admission.consume(
            captured[0],
            exchange_name=ROUTE,
            operation=WriteOperation.SETTLEMENT_CONFIRM,
            session_generation=3,
            settlement_binding=captured[0].settlement_binding,
        )


@pytest.mark.parametrize("value", [None, 1, "false", object()])
def test_missing_or_invalid_required_declaration_is_unknown(value):
    coordinator, provider, calls, _ = setup()
    provider.settlement_required = value
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == []


def test_explicit_no_settlement_step_never_reads_or_writes():
    coordinator, provider, calls, _ = setup(enabled=False)
    provider.settlement_required = False

    def forbidden():
        raise AssertionError("read is forbidden")

    provider.observe_settlement = forbidden
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.CONFIRMED
    )
    assert calls == []


def test_stale_confirmed_observation_is_not_trusted():
    coordinator, provider, calls, _ = setup()
    provider.settlement = SettlementStatus.CONFIRMED
    observation = _observe(provider)
    provider.generation += 1
    assert (
        coordinator.ensure_settlement(
            ROUTE, commit_port=FakePort(calls), host_scope="scope", observation=observation
        )
        is SettlementStatus.UNKNOWN
    )
    assert calls == []


def test_nonboolean_validator_and_bad_status_fail_closed():
    coordinator, provider, calls, _ = setup()
    provider.validate_settlement_observation = lambda _: 1
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == []
    provider.validate_settlement_observation = lambda _: True
    malformed = _observe(provider)
    object.__setattr__(malformed, "status", "unconfirmed")
    assert (
        coordinator.ensure_settlement(
            ROUTE, commit_port=FakePort(calls), host_scope="scope", observation=malformed
        )
        is SettlementStatus.UNKNOWN
    )
    assert calls == []


def test_missing_or_copied_binding_and_copied_grant_cannot_be_consumed():
    _, provider, _, admission = setup()
    observation = _observe(provider)
    binding = SettlementWriteBinding(observation, "marker-a")
    with pytest.raises(WriteAdmissionDenied, match="settlement_binding_invalid"):
        admission.issue(ROUTE, WriteOperation.SETTLEMENT_CONFIRM, 3)
    with pytest.raises(WriteAdmissionDenied, match="settlement_binding_invalid"):
        admission.issue(ROUTE, WriteOperation.PLACE, 3, settlement_binding=binding)
    grant = admission.issue(ROUTE, WriteOperation.SETTLEMENT_CONFIRM, 3, settlement_binding=binding)
    with pytest.raises(WriteAdmissionDenied, match="settlement_binding_invalid"):
        admission.consume(
            grant,
            exchange_name=ROUTE,
            operation=WriteOperation.SETTLEMENT_CONFIRM,
            session_generation=3,
            settlement_binding=replace(binding),
        )
    grant = admission.issue(ROUTE, WriteOperation.SETTLEMENT_CONFIRM, 3, settlement_binding=binding)
    with pytest.raises(WriteAdmissionDenied, match="invalid_or_consumed"):
        admission.consume(
            replace(grant),
            exchange_name=ROUTE,
            operation=WriteOperation.SETTLEMENT_CONFIRM,
            session_generation=3,
            settlement_binding=binding,
        )


def test_disabled_admission_raises_before_marker():
    coordinator, _, calls, _ = setup(enabled=False)
    with pytest.raises(WriteAdmissionDenied, match="write_admission_disabled"):
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
    assert calls == []


@pytest.mark.parametrize("day", ["2026116", "20260230", "2026-1106", "２０２６１１０６"])
def test_settlement_observation_rejects_invalid_trading_day(day):
    with pytest.raises(ValueError):
        SettlementObservation(ROUTE, "account-a", day, 3, SettlementStatus.UNCONFIRMED, "obs-a")


@pytest.mark.parametrize("provider_success", [True, False])
def test_cleanup_failure_after_commit_is_unknown(provider_success):
    coordinator, provider, calls, admission = setup()

    def cleanup_failure(_grant):
        raise RuntimeError("private cleanup detail")

    admission.revoke_grant = cleanup_failure
    if not provider_success:

        def provider_failure(*args, **kwargs):
            raise RuntimeError("private provider detail")

        provider.confirm_settlement = provider_failure
    result = coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
    assert result is SettlementStatus.UNKNOWN
    assert calls[0] == "commit"


def test_session_change_during_admission_check_never_commits():
    coordinator, provider, calls, admission = setup()
    original = admission.is_enabled

    def check(route):
        result = original(route)
        provider.generation += 1
        return result

    admission.is_enabled = check
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == []


def test_provider_replacement_during_confirmation_is_unknown():
    coordinator, provider, calls, _ = setup()
    original = provider.confirm_settlement

    def replaced(grant, verifier, *, observation):
        result = original(grant, verifier, observation=observation)
        coordinator._providers[ROUTE] = FakeProvider([])
        return result

    provider.confirm_settlement = replaced
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == ["commit", "confirm_settlement"]


@pytest.mark.parametrize(
    "module_name,class_name",
    [
        ("bt_api_okx.direct_write_provider", "OkxDirectWriteProvider"),
        ("bt_api_binance.direct_write_provider", "BinanceDirectWriteProvider"),
    ],
)
def test_no_step_provider_keeps_general_protocol_without_settlement_methods(
    module_name, class_name
):
    from bt_api_base.direct_write_protocol import DirectWriteProvider, SettlementRequiredProvider

    module = pytest.importorskip(module_name)
    provider_type = getattr(module, class_name)
    provider = provider_type.__new__(provider_type)
    assert provider.settlement_required is False
    assert isinstance(provider, DirectWriteProvider)
    assert not isinstance(provider, SettlementRequiredProvider)
    coordinator = DirectWriteCoordinator(WriteAdmission(), lambda _: provider)
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=None, host_scope="scope")
        is SettlementStatus.CONFIRMED
    )


@pytest.mark.parametrize("generation", [0, -1, True, "3"])
def test_settlement_observation_rejects_invalid_generation(generation):
    with pytest.raises(ValueError):
        SettlementObservation(
            ROUTE, "account-a", "20261106", generation, SettlementStatus.UNCONFIRMED, "obs-a"
        )


def test_post_commit_admission_error_is_unknown_and_never_sends():
    coordinator, _, calls, admission = setup()

    def denied(*args, **kwargs):
        raise WriteAdmissionDenied("write_admission_disabled")

    admission.issue = denied
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == ["commit"]


def test_provider_replacement_during_validation_never_commits():
    coordinator, provider, calls, _ = setup()

    def replaced(_observation):
        coordinator._providers[ROUTE] = FakeProvider([])
        return True

    provider.validate_settlement_observation = replaced
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == []


@pytest.mark.parametrize("required", [None, False, 1, "invalid"])
def test_declaration_change_during_observation_is_unknown_without_commit(required):
    coordinator, provider, calls, _ = setup()
    original = provider.observe_settlement

    def read_then_change():
        observation = original()
        provider.settlement_required = required
        return observation

    provider.observe_settlement = read_then_change
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == []


def test_declaration_change_during_validation_is_unknown_without_commit():
    coordinator, provider, calls, _ = setup()

    def validate_then_change(_observation):
        provider.settlement_required = False
        return True

    provider.validate_settlement_observation = validate_then_change
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == []


@pytest.mark.parametrize("required", [None, False, 1])
def test_declaration_change_during_marker_commit_is_unknown_without_send(required):
    coordinator, provider, calls, _ = setup()

    class ChangingPort(FakePort):
        def commit(self, record):
            marker = super().commit(record)
            provider.settlement_required = required
            return marker

    port = ChangingPort(calls)
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=port, host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == ["commit"] and len(port.records) == 1


def test_declaration_change_during_issue_revokes_grant_without_send():
    coordinator, provider, calls, admission = setup()
    original = admission.issue
    issued = []

    def issue_then_change(*args, **kwargs):
        grant = original(*args, **kwargs)
        issued.append(grant)
        provider.settlement_required = None
        return grant

    admission.issue = issue_then_change
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == ["commit"] and len(issued) == 1
    with pytest.raises(WriteAdmissionDenied, match="invalid_or_consumed"):
        admission.consume(
            issued[0],
            exchange_name=ROUTE,
            operation=WriteOperation.SETTLEMENT_CONFIRM,
            session_generation=3,
            settlement_binding=issued[0].settlement_binding,
        )


def test_declaration_getter_exception_during_validation_is_unknown():
    calls = []

    class GetterProvider(FakeProvider):
        @property
        def settlement_required(self):
            self.reads += 1
            if self.reads > 1:
                raise RuntimeError("private getter error")
            return True

    provider = GetterProvider(calls)
    provider.reads = 0
    provider.settlement = SettlementStatus.UNCONFIRMED
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _: provider)
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == []


def test_declaration_change_during_confirmation_is_unknown_after_send():
    coordinator, provider, calls, _ = setup()
    original = provider.confirm_settlement

    def confirm_then_change(grant, verifier, *, observation):
        result = original(grant, verifier, observation=observation)
        provider.settlement_required = False
        return result

    provider.confirm_settlement = confirm_then_change
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == ["commit", "confirm_settlement"]


def test_readonly_observe_does_not_return_changed_declaration():
    coordinator, provider, _, _ = setup()
    original = provider.observe_settlement

    def read_then_change():
        observation = original()
        provider.settlement_required = False
        return observation

    provider.observe_settlement = read_then_change
    assert coordinator.observe_settlement(ROUTE) is None


def test_declaration_getter_cannot_swap_provider_while_returning_true():
    calls = []

    class GetterProvider(FakeProvider):
        @property
        def settlement_required(self):
            self.reads += 1
            if self.reads == 2:
                coordinator._providers[ROUTE] = FakeProvider([])
            return True

    provider = GetterProvider(calls)
    provider.reads = 0
    provider.settlement = SettlementStatus.UNCONFIRMED
    coordinator = DirectWriteCoordinator(WriteAdmission({ROUTE: True}), lambda _: provider)
    assert (
        coordinator.ensure_settlement(ROUTE, commit_port=FakePort(calls), host_scope="scope")
        is SettlementStatus.UNKNOWN
    )
    assert calls == []
