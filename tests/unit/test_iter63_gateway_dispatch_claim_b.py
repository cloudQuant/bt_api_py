"""Decision-B native dispatch claims through the real Gateway composition.

Real journal, guard, dispatcher, SDK coordinator and claim port; only the
provider is synthetic. A SENT result means the synthetic native call ran; it
is not venue acceptance or account evidence.
"""

import threading
import time

import pytest
from bt_api_base.direct_place_protocol import PreparedPlaceRequest
from bt_api_base.direct_write_protocol import DirectSubmitResult
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import (
    DirectDispatchError,
    GatewayDirectDispatcher,
    NativeDispatchOutcome,
)
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.gateway_direct_dispatch import compose_gateway_direct_dispatcher
from bt_api_py.gateway_dispatch_claim import (
    GATEWAY_DISPATCH_COVERAGE,
    DispatchClaimError,
    GatewayDispatchClaimCoordinator,
    GatewayJournalDispatchChangePort,
    GatewayNativeDispatchClaimPort,
    seal_gateway_dispatch_coverage,
)
from bt_api_py.write_admission import WriteAdmission
from tests.unit.test_iter63_gateway_prepared_place import ROUTE, PreparedProvider, make_request

WIRED = frozenset(label for labels in GATEWAY_DISPATCH_COVERAGE.values() for label in labels)


class ClaimingProvider(PreparedProvider):
    """Calls the claim check right before its synthetic native send."""

    def __init__(self):
        super().__init__()
        self.before_native = lambda: None
        self.on_prepare = lambda: None
        self.checks = 0
        self.sequence = 0

    def prepare_order(self, request):
        # Distinct native order refs per order, as a real provider allocates.
        from dataclasses import replace

        prepared = super().prepare_order(request)
        self.on_prepare()
        self.sequence += 1
        ref = str(self.sequence)
        identity = replace(prepared.identity, client_order_id=ref)
        prepared = PreparedPlaceRequest(
            "prep-" + ref, identity, prepared.symbol, prepared.trading_day,
            prepared.session_generation, dict(prepared.request_fields),
            ("place-key-" + ref,), dict(prepared.source_binding))
        self.prepared[-1] = prepared
        return prepared

    def submit_prepared_order(self, prepared, grant, verifier, *, native_market_recheck=None):
        self.before_native()
        try:
            if native_market_recheck is None or native_market_recheck() is False:
                raise ValueError("claim check missing")
            self.checks += 1
        except Exception:
            return DirectSubmitResult(False, prepared.identity, "synthetic_not_sent")
        return super().submit_prepared_order(prepared, grant, verifier)


class HookAdmission(WriteAdmission):
    def __init__(self, routes):
        super().__init__(routes)
        self.on_issue = lambda: None

    def issue(self, *args, **kwargs):
        self.on_issue()
        return super().issue(*args, **kwargs)


class Harness:
    def __init__(self, tmp_path, *, seal=True, **port_options):
        guard_dir = tmp_path / "guard"
        guard_dir.mkdir()
        self.guard = LocalAccountSessionGuard(guard_dir, "account-a", "owner")
        generation = self.guard.acquire()
        self.journal = GatewayDirectCommandJournal.create(
            tmp_path / "journal.sqlite", "account-a", generation=generation)
        self.provider = ClaimingProvider()
        self.admission = HookAdmission({ROUTE: True})
        self.writes = DirectWriteCoordinator(self.admission, lambda _: self.provider)
        self.claims, sealer = GatewayDispatchClaimCoordinator.with_coverage_sealer(
            "account-a", generation)
        self.journal.install_dispatch_change_port(
            guard=self.guard, port=GatewayJournalDispatchChangePort(self.claims))
        if seal:
            seal_gateway_dispatch_coverage(self.claims, sealer, wired=WIRED)
        self.authorized = True
        self.port = GatewayNativeDispatchClaimPort(self.claims, journal_id="journal-a",
                                                   **port_options)
        self.dispatcher = compose_gateway_direct_dispatcher(
            self.writes, ROUTE, self.journal, self.guard, lambda _record: self.authorized,
            expected_local_provider=self.provider, prepared_place=True, claim_port=self.port)

    def reserve(self, key="key-a"):
        request = make_request()
        payload = dict(request.to_dict(), idempotency_key=key, client_order_id="order-" + key)
        return self.journal.reserve("strategy-a", key, "principal-a", payload)

    def dispatch(self, row, **kwargs):
        return self.dispatcher.dispatch(row.strategy_scope, row.request_key, row.principal,
                                        row.reservation_id, row.digest, row.version, **kwargs)

    def close(self):
        self.guard.release()


@pytest.fixture
def harness(tmp_path):
    value = Harness(tmp_path)
    yield value
    value.close()


def test_unsealed_coordinator_refuses_before_any_marker(tmp_path):
    h = Harness(tmp_path, seal=False)
    try:
        row = h.reserve()
        assert h.dispatcher.claims_available() is False
        with pytest.raises(DirectDispatchError, match="DISPATCH_CLAIM_UNAVAILABLE"):
            h.dispatch(row)
        assert h.journal.get("strategy-a", "key-a", "principal-a") == row
        assert h.provider.prepared == [] and h.provider.sends == []
    finally:
        h.close()


def test_sealed_claim_sends_once_and_retires_the_guard(harness):
    row = harness.reserve()
    result = harness.dispatch(row)
    assert result.status == "SENT"
    assert len(harness.provider.sends) == 1 and harness.provider.checks == 1
    assert harness.claims.capacity_status()["commands"] == 0
    # The durable marker left RESERVED: re-dispatch only observes.
    again = harness.dispatch(row)
    assert again.status == "SENT" and len(harness.provider.sends) == 1


def test_revocation_after_marker_but_before_claim_is_not_sent(harness):
    # Decision B: the claim now precedes the marker, so there are two cases.
    # (a) revocation between the initial check and the claim validation.
    row = harness.reserve()
    original_claim = harness.port.claim_native

    def revoke_then_claim(record, identity, validate):
        harness.authorized = False
        return original_claim(record, identity, validate)

    harness.port.claim_native = revoke_then_claim
    with pytest.raises(DirectDispatchError, match="ADMISSION_DENIED"):
        harness.dispatch(row)
    del harness.port.claim_native
    assert harness.journal.get("strategy-a", "key-a", "principal-a").status == "RESERVED"
    assert harness.provider.prepared == [] and harness.provider.sends == []
    assert harness.claims.capacity_status()["commands"] == 0

    # (b) revocation after the claim but before the marker commit.
    harness.authorized = True
    second = harness.reserve("key-b")

    def revoke_on_prepare():
        harness.authorized = False

    harness.provider.on_prepare = revoke_on_prepare
    with pytest.raises(DirectDispatchError, match="PRE_DISPATCH_FAILED"):
        harness.dispatch(second)
    assert harness.journal.get("strategy-a", "key-b", "principal-a").status == "RESERVED"
    assert harness.provider.sends == [] and harness.provider.checks == 0
    assert harness.claims.capacity_status()["commands"] == 0


def test_revocation_after_claim_completes_without_waiting_for_native(harness):
    row = harness.reserve()
    registry_lock = threading.RLock()  # the production Registry lock is an RLock
    revoked = threading.Event()

    def fence(_row):
        from contextlib import contextmanager

        @contextmanager
        def scope():
            with registry_lock:
                yield lambda: None
        return scope()

    def native_in_progress():
        # A revocation now needs the "Registry" lock and the registry domain;
        # neither may be held by the in-flight native call (decision B).
        def revoke():
            with registry_lock:
                token = harness.claims.begin_change("registry")
                harness.claims.finish_change_current(token)
            revoked.set()

        worker = threading.Thread(target=revoke, daemon=True)
        worker.start()
        # Record instead of asserting here: a blocked revocation must fail the
        # test, not deadlock it while the native call holds a lock.
        completed_during_native.append(revoked.wait(2.0))

    completed_during_native = []
    harness.provider.before_native = native_in_progress
    result = harness.dispatch(row, request_fence=fence)
    assert completed_during_native == [True], "revocation waited for the native call"
    # The claim preceded the revocation: the request stays in flight.
    assert result.status == "SENT" and len(harness.provider.sends) == 1


def test_claim_older_than_send_delay_is_not_sent(tmp_path):
    h = Harness(tmp_path, max_send_delay_ns=1_000_000)
    try:
        row = h.reserve()
        h.provider.before_native = lambda: time.sleep(0.02)
        result = h.dispatch(row)
        assert result.status == "NOT_SENT" and h.provider.sends == []
    finally:
        h.close()


def test_journal_writer_active_during_claim_is_retried_then_refused(tmp_path):
    h = Harness(tmp_path, retry_budget_ns=20_000_000)
    try:
        row = h.reserve()
        # A journal-held control/parent writer that is still in progress when
        # the claim (which now precedes the marker) is attempted.
        token = h.claims.begin_change("control")
        with pytest.raises(DirectDispatchError, match="DISPATCH_CLAIM_BUSY"):
            h.dispatch(row)
        assert h.journal.get("strategy-a", "key-a", "principal-a").status == "RESERVED"
        assert h.provider.prepared == [] and h.provider.sends == []
        h.claims.finish_change_current(token)
        # Once the writer finished, the SAME row dispatches normally.
        assert h.dispatch(row).status == "SENT"
        assert len(h.provider.sends) == 1
    finally:
        h.close()


def test_every_journal_transaction_invalidates_both_journal_domains(harness):
    snapshot = harness.claims.capture()
    harness.reserve()  # one ordinary journal write transaction
    status = dict((d, s) for d, s, _p in harness.claims.coverage_status()["domains"])
    assert status["parent_facts"] == "READY" and status["control"] == "READY"
    epochs = {name: state[0] for name, state in harness.claims._domains.items()}
    assert snapshot.versions != tuple(
        (name, epochs[name], status[name]) for name, _e, _s in snapshot.versions)
    harness.claims.discard_snapshot(snapshot)


def test_provider_reaching_native_without_claim_faults_dispatcher(tmp_path):
    guard_dir = tmp_path / "guard"
    guard_dir.mkdir()
    guard = LocalAccountSessionGuard(guard_dir, "account-a", "owner")
    generation = guard.acquire()
    journal = GatewayDirectCommandJournal.create(
        tmp_path / "journal.sqlite", "account-a", generation=generation)
    claims, sealer = GatewayDispatchClaimCoordinator.with_coverage_sealer("account-a", generation)
    seal_gateway_dispatch_coverage(claims, sealer, wired=WIRED)
    port = GatewayNativeDispatchClaimPort(claims, journal_id="journal-a")
    provider = PreparedProvider()
    try:
        def rogue_native(payload, commit_port, host_scope):
            from bt_api_base.direct_write_protocol import PreDispatchRecord, WriteOperation
            prepared = provider.prepare_order(make_request())
            record = PreDispatchRecord(
                operation=WriteOperation.PLACE, host_scope=host_scope, exchange_name=ROUTE,
                symbol=prepared.symbol, identity=prepared.identity, target_identity=None,
                request_summary=dict(payload), trading_day=prepared.trading_day,
                session_generation=prepared.session_generation,
                created_at_utc="2030-01-01T00:00:00Z")
            marker = commit_port.commit(record)
            # Bypass native_fence entirely.
            return NativeDispatchOutcome(True, prepared.identity, marker)

        dispatcher = GatewayDirectDispatcher(
            journal, ROUTE, guard, rogue_native, lambda _identity: ("synthetic-key",),
            lambda _record: True, claim_port=port)
        row = journal.reserve("strategy-a", "key-a", "principal-a", make_request().to_dict())
        result = dispatcher.dispatch(row.strategy_scope, row.request_key, row.principal,
                                     row.reservation_id, row.digest, row.version)
        assert result.status == "SENT"  # recorded honestly as attempted
        assert dispatcher.claims_available() is False
        payload = dict(make_request().to_dict(), idempotency_key="key-b", client_order_id="b")
        second = journal.reserve("strategy-a", "key-b", "principal-a", payload)
        with pytest.raises(DirectDispatchError, match="DISPATCH_CLAIM_UNAVAILABLE"):
            dispatcher.dispatch(second.strategy_scope, second.request_key, second.principal,
                                second.reservation_id, second.digest, second.version)
    finally:
        guard.release()


def test_claim_port_rejects_test_and_foreign_inputs(harness):
    with pytest.raises(DispatchClaimError, match="INVALID_CLAIM_PORT"):
        GatewayNativeDispatchClaimPort(harness.claims, journal_id="j",
                                       candidate_validity_ns=3_000_000_000)
    other, _sealer = GatewayDispatchClaimCoordinator.with_coverage_sealer("account-a", "x")
    with pytest.raises(DispatchClaimError, match="INVALID_CLAIM"):
        GatewayNativeDispatchClaimPort(other, journal_id="j").native_check(object())
