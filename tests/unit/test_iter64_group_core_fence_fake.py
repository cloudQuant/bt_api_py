"""Group composition and original-fence checks without host, disk or network."""
from decimal import Decimal
import importlib.util
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from bt_api_base.service_protocol import WireScope
from bt_api_execution import (
    AccountScopedGroupAdmissionAdapter, ExecutionScope, GroupPermitBinding,
    SqliteExecutionStore,
)
from bt_api_risk import (
    AccountScope, DispatchEvidenceClass, DurableRiskAdmissionAdapter, DurableRiskGate,
    InstrumentRiskMetadata, InstrumentRiskRegistry, RiskPolicy,
)
from bt_api_risk.risk_order_journal import RiskFenceExitCleanup, RiskOrderJournal as RiskCore
from bt_api_py.bt_api import BtApi
from bt_api_py.service_execution_group_authority import ExecutionGroupAuthorityGuard
from bt_api_py.service_execution_group_proofs import (
    GroupProofDispatchSession, GroupProofJournal, GroupProofRuntimeBundle,
    GroupProofSource, GroupProofSourceError, GroupProofTarget,
)
from bt_api_py.service_group_reservations import GroupReservationTarget
from bt_api_py.service_risk_order_entry import RiskBtApiProviderContract, RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal as LegacySdkJournal


_SYNTHETIC_STATES = []


def test_session_reuses_real_core_borrow_implementation_with_fake_resources(monkeypatch):
    import bt_api_risk.risk_order_journal as journal_module
    helper_path = (Path(__file__).parents[2] / "bt_api/bt_api_risk/tests"
                   / "test_iter64_provisional_fence_entry_fake.py")
    spec = importlib.util.spec_from_file_location("iter64_fake_fence_helpers", helper_path)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    owners, uncertain, quarantined = {}, [], set()
    _SYNTHETIC_STATES.append((owners, uncertain, quarantined, helpers))
    monkeypatch.setattr(journal_module, "_FENCE_PROVISIONAL_OWNERS", owners)
    monkeypatch.setattr(journal_module, "_FENCE_ENTRY_UNCERTAIN", uncertain)
    monkeypatch.setattr(journal_module, "_FENCE_ENTRY_QUARANTINED_KEYS", quarantined)
    monkeypatch.setattr(journal_module, "_FENCE_ENTRY_QUARANTINED_ALL", False)
    core, lock, file, active = helpers.fake_journal(monkeypatch)
    manager = core.writer_fence(helpers.SCOPE, blocking=False)
    fence = manager.__enter__()
    query_calls = helpers.fake_borrow_database(monkeypatch)
    session = object.__new__(GroupProofDispatchSession)
    session.target = SimpleNamespace(risk_order_journal=core)
    session._risk_fence_context = manager
    session._risk_fence = fence
    session._risk_fence_uncertain = False
    session._context_token = None
    session._assert_risk_fence()
    assert query_calls[0][1] == (helpers.SCOPE.key,)
    assert query_calls[-1] == "close"
    assert (lock.acquires, lock.releases, file.closes) == (1, 0, 0)
    session._release_risk_fence()
    assert (lock.acquires, lock.releases, file.closes) == (1, 1, 1)
    assert not active and not owners


def setup_bundle():
    scope = AccountScope("fixture", "provider-account", "simulation")
    execution_scope = ExecutionScope("fixture", "simulation", "account-a", "strategy-a", "20261015")
    wire = WireScope("fixture", account_ref="account-a", strategy_ref="strategy-a")
    meta = InstrumentRiskMetadata(
        instrument="BTC-USD", metadata_version="fixture-metadata-v1", as_of_ns=1,
        expires_at_ns=10, tick_size=Decimal("0.01"), quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"), max_gross_notional=Decimal("100000"),
        quantity_unit="base", valuation_unit="USD",
    )
    risk_target = RiskOrderTarget(
        account_scope=scope, strategy_id="strategy-a", exchange_name="FIXTURE___SPOT",
        provider_account_id="provider-account", registry=InstrumentRiskRegistry((meta,)),
    )
    contract = RiskBtApiProviderContract(
        exchange_name="FIXTURE___SPOT", profile_id="fixture-profile-v2",
        order_schema="fixture-order-v2", fill_schema="fixture-fill-v2",
        client_order_id_query=True, client_order_id_query_unique=True,
        complete_order_snapshot=True, complete_fill_history=True,
        provider_accepts_client_order_id=True,
        provider_status_mapping=(("cancelled", "canceled"), ("expired", "expired"),
            ("filled", "completed"), ("open", "accepted"), ("partial", "partial"),
            ("rejected", "rejected")),
        supported_order_types=("LIMIT",), supported_time_in_force=("IOC",),
        supported_position_effects=("OPEN",),
        normalizer_schema="bt-api-order-source-provenance-v6", order_revision_field="order_revision",
        fill_revision_field="fill_revision", fill_watermark_field="fill_watermark",
        fee_amount_field="fee_amount", fee_currency_field="fee_currency",
        quote_currency_by_instrument=(("BTC-USD", "USD"),),
    )
    core = object.__new__(RiskCore)
    target_args = dict(execution_scope=execution_scope, risk_target=risk_target,
        provider_contract=contract, bt_api=object.__new__(BtApi),
        execution_store=object.__new__(SqliteExecutionStore), risk_order_journal=core,
        evidence_class=DispatchEvidenceClass.SIMULATION_JOURNAL)
    target = GroupProofTarget(**target_args)
    proof_journal = object.__new__(GroupProofJournal)
    proof_journal._GroupProofJournal__append_capability = object()
    targets = MappingProxyType({wire: target})
    source = GroupProofSource(proof_journal, targets)
    gate = object.__new__(DurableRiskGate)
    gate._policy = RiskPolicy("group-test", Decimal("1000"), max_increase_count=10,
                              require_strategy_allocation=True, notional_unit="USD")
    gate._execution_journal_authority = source
    admission = DurableRiskAdmissionAdapter(gate, lambda _: None)
    account = AccountScopedGroupAdmissionAdapter(admission, account_scope=scope)
    reservations = MappingProxyType({wire: GroupReservationTarget(scope, admission)})
    return target_args, source, gate, admission, account, reservations, targets


def test_target_requires_exact_core_not_legacy_subclass():
    args, *_ = setup_bundle()
    args["risk_order_journal"] = object.__new__(LegacySdkJournal)
    with pytest.raises(GroupProofSourceError, match="GROUP_PROOF_TARGET_INVALID"):
        GroupProofTarget(**args)


def test_bundle_requires_exact_gate_authority_and_rechecks_it():
    _, source, gate, admission, account, reservations, targets = setup_bundle()
    gate._execution_journal_authority = object()
    with pytest.raises(GroupProofSourceError, match="GROUP_RISK_PROOF_SOURCE_IDENTITY_MISMATCH"):
        GroupProofRuntimeBundle(admission, account, reservations, targets, source)
    gate._execution_journal_authority = source
    bundle = GroupProofRuntimeBundle(admission, account, reservations, targets, source)
    bundle._assert_reservations_only_policy()
    gate._execution_journal_authority = object()
    with pytest.raises(GroupProofSourceError, match="GROUP_RISK_PROOF_SOURCE_IDENTITY_MISMATCH"):
        bundle._assert_reservations_only_policy()


def test_bundle_rejects_adapter_gate_replacement_with_same_policy():
    _, source, gate, admission, account, reservations, targets = setup_bundle()
    bundle = GroupProofRuntimeBundle(admission, account, reservations, targets, source)
    other = object.__new__(DurableRiskGate)
    other._policy = gate._policy
    other._execution_journal_authority = source
    admission._gate = other
    with pytest.raises(GroupProofSourceError, match="GROUP_RISK_PROOF_SOURCE_IDENTITY_MISMATCH"):
        bundle._assert_reservations_only_policy()


def test_session_passes_original_context_and_fence_and_never_reacquires(monkeypatch):
    args, *_ = setup_bundle()
    core = args["risk_order_journal"]
    manager, fence, seen = object(), object(), []
    monkeypatch.setattr(RiskCore, "assert_borrowed_fence", lambda self, m, f: seen.append((self, m, f)))
    monkeypatch.setattr(RiskCore, "writer_fence", lambda *args, **kwargs: pytest.fail("unexpected reacquire"))
    session = object.__new__(GroupProofDispatchSession)
    session.target = SimpleNamespace(risk_order_journal=core)
    session._risk_fence_context = manager
    session._risk_fence = fence
    session._risk_fence_uncertain = False
    session._assert_risk_fence()
    assert seen == [(core, manager, fence)]
    assert not session._risk_fence_uncertain


def test_session_current_checks_authorization_lease_and_original_fence(monkeypatch):
    args, *_ = setup_bundle()
    core = args["risk_order_journal"]
    manager, fence, events = object(), object(), []
    monkeypatch.setattr(RiskCore, "assert_borrowed_fence",
                        lambda self, m, f: events.append(("fence", self, m, f)))
    session = object.__new__(GroupProofDispatchSession)
    session._risk_fence_context = manager
    session._risk_fence = fence
    session._risk_fence_uncertain = False
    session._recovery_hold = None
    session._writer_lease = object()
    session._authorization = object()
    session.source = SimpleNamespace(runtime_bundle=SimpleNamespace(
        _assert_reservations_only_policy=lambda: events.append("policy")))
    session.target = SimpleNamespace(risk_order_journal=core, execution_scope=object(),
        execution_store=SimpleNamespace(assert_writer_lease=lambda *args: events.append("lease")))
    session.active_authority = SimpleNamespace(
        assert_current_group_leg_authorization=lambda **kwargs: events.append("authorization"))
    session.principal_ref = "principal"
    session.identity = session.wire_scope = session.intent = session.binding = object()
    session._assert_current()
    assert events == ["policy", "lease", "authorization", ("fence", core, manager, fence)]


def test_borrow_failure_does_not_exit_original_owner(monkeypatch):
    args, *_ = setup_bundle()
    calls = []
    manager = SimpleNamespace(__exit__=lambda *_: calls.append("exit"))
    session = object.__new__(GroupProofDispatchSession)
    session.target = SimpleNamespace(risk_order_journal=args["risk_order_journal"])
    session._risk_fence_context = manager
    session._risk_fence = object()
    session._risk_fence_uncertain = False
    session._context_token = None
    def fail(*_):
        raise RuntimeError("lost fence")
    monkeypatch.setattr(RiskCore, "assert_borrowed_fence", fail)
    with pytest.raises(RuntimeError):
        session._assert_risk_fence()
    with pytest.raises(GroupProofSourceError, match="GROUP_RISK_FENCE_RELEASE_UNAVAILABLE"):
        session._release_risk_fence()
    assert calls == []
    assert session._risk_fence_uncertain


@pytest.mark.parametrize("failure", ["borrow", "prepare", "exit", "consume", "no_receipt"])
def test_failed_or_interrupted_close_retains_owner_and_is_never_retried(failure):
    calls = []
    def borrow(*_):
        calls.append("borrow")
        if failure == "borrow":
            raise RuntimeError("borrow unavailable")
    def prepare(*_):
        calls.append("prepare")
        if failure == "prepare":
            raise KeyboardInterrupt("interrupted prepare")
    def exit_fence(*_):
        calls.append("exit")
        if failure == "exit":
            raise KeyboardInterrupt("interrupted exit")
    def consume(*_):
        calls.append("consume")
        if failure == "consume":
            raise KeyboardInterrupt("interrupted consume")
        return None if failure == "no_receipt" else RiskFenceExitCleanup.COMPLETE
    manager = SimpleNamespace(__exit__=exit_fence)
    fence = object()
    session = object.__new__(GroupProofDispatchSession)
    session.target = SimpleNamespace(risk_order_journal=SimpleNamespace(
        assert_borrowed_fence=borrow, prepare_borrowed_fence_close=prepare,
        consume_borrowed_fence_close=consume))
    session._risk_fence_context = manager
    session._risk_fence = fence
    session._risk_fence_uncertain = False
    session._context_token = None
    expected = KeyboardInterrupt if failure in {"prepare", "exit", "consume"} else GroupProofSourceError
    with pytest.raises(expected):
        session._release_risk_fence()
    with pytest.raises(GroupProofSourceError, match="GROUP_RISK_FENCE_RELEASE_UNAVAILABLE"):
        session._release_risk_fence()
    assert calls.count("exit") <= 1
    assert session._risk_fence_context is manager
    assert session._risk_fence is fence
    assert session._risk_fence_uncertain


@pytest.mark.parametrize("failure", [None, "first", "read", "read_code", "second", "read_second",
                                    "no_receipt", "prepare_interrupt", "exit_interrupt",
                                    "consume_interrupt"])
def test_unclaimed_receipt_acquires_once_and_borrows_before_and_after(monkeypatch, failure):
    args, source, *_ = setup_bundle()
    core = args["risk_order_journal"]
    target = GroupProofTarget(**args)
    fence, events = object(), []
    class Manager:
        def __enter__(self):
            events.append("enter")
            return fence
        def __exit__(self, *_):
            events.append("exit")
            if failure == "exit_interrupt":
                raise KeyboardInterrupt("synthetic exit interruption")
    manager = Manager()
    monkeypatch.setattr(RiskCore, "writer_fence", lambda self, scope: manager)
    def borrow(self, m, f):
        events.append((m, f))
        if (failure == "first" and len(events) == 2
                or failure in {"second", "read_second"} and len(events) == 3):
            raise RuntimeError("synthetic borrow failure")
    monkeypatch.setattr(RiskCore, "assert_borrowed_fence", borrow)
    def prepare(self, m, f):
        events.append("prepare")
        if failure == "prepare_interrupt":
            raise KeyboardInterrupt("synthetic prepare interruption")
    monkeypatch.setattr(RiskCore, "prepare_borrowed_fence_close", prepare)
    def consume(self, m, f):
        events.append("consume")
        if failure == "consume_interrupt":
            raise KeyboardInterrupt("synthetic consume interruption")
        return None if failure == "no_receipt" else RiskFenceExitCleanup.COMPLETE
    monkeypatch.setattr(RiskCore, "consume_borrowed_fence_close", consume)
    binding = GroupPermitBinding(
        group_id="group", leg_id="leg", portfolio_ref="portfolio", strategy_id="strategy-a",
        account_ref="account-a", coordinator_principal_ref="principal", permit_id="permit",
        risk_scope_fingerprint="a" * 64, group_fingerprint="a" * 64,
        policy_fingerprint="a" * 64, reserved_intent_hash="a" * 64,
        exposure_profile_fingerprint="a" * 64, actual_child_intent_hash="a" * 64,
        order_intent_hash="a" * 64, deadline_ns=100,
    )
    monkeypatch.setattr(GroupProofSource, "_validate_call", lambda self, **kwargs: (target, None))
    monkeypatch.setattr(GroupProofSource, "_map_intent", lambda self, **kwargs: (object(), binding))
    def read(self, **kwargs):
        if failure in {"read", "read_second"}:
            raise RuntimeError("synthetic read failure")
        if failure == "read_code":
            raise GroupProofSourceError("GROUP_SOURCE_STATUS_INVALID")
        return "receipt"
    monkeypatch.setattr(GroupProofSource, "_read_receipt_locked", read)
    kwargs = dict(binding=binding, intent=object(), permit=object(),
        principal_ref="p", identity=object(), wire_scope=object(), service_context=object(),
        active_authority=object.__new__(ExecutionGroupAuthorityGuard))
    if failure is None:
        assert source.read_receipt(**kwargs) == "receipt"
        assert events == ["enter", (manager, fence), (manager, fence), "prepare", "exit", "consume"]
    elif failure == "read":
        with pytest.raises(GroupProofSourceError, match="GROUP_RECEIPT_READ_UNAVAILABLE"):
            source.read_receipt(**kwargs)
        assert events == ["enter", (manager, fence), (manager, fence), "prepare", "exit", "consume"]
    elif failure == "read_code":
        with pytest.raises(GroupProofSourceError, match="GROUP_SOURCE_STATUS_INVALID"):
            source.read_receipt(**kwargs)
        assert events == ["enter", (manager, fence), (manager, fence), "prepare", "exit", "consume"]
    elif failure.endswith("_interrupt"):
        with pytest.raises(KeyboardInterrupt):
            source.read_receipt(**kwargs)
        assert events.count("exit") == (0 if failure == "prepare_interrupt" else 1)
    else:
        with pytest.raises(GroupProofSourceError, match="GROUP_RISK_FENCE_RELEASE_UNAVAILABLE"):
            source.read_receipt(**kwargs)
        assert events.count("exit") == (1 if failure == "no_receipt" else 0)
