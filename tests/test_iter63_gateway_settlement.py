"""Synthetic Gateway settlement owner, SQLite and local guard only."""

import threading
import time

import pytest
from bt_api_base.direct_write_protocol import (
    DirectWriteErrorPolicy,
    SettlementObservation,
    SettlementStatus,
    WriteOperation,
)
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.gateway_settlement import GatewaySettlementController
from bt_api_py.write_admission import WriteAdmission


class SettlementClaimUnavailable(AssertionError):
    """Only the explicitly disabled Gateway settlement claim is expected."""


class DispatchClaimUnavailable(AssertionError):
    """Only the explicitly disabled Gateway order claim is expected."""


class Provider:
    settlement_required = True
    execution_ids_trading_day_scoped = False
    direct_write_error_policy = DirectWriteErrorPolicy(frozenset(), frozenset())

    def __init__(self):
        self.day, self.generation, self.account = "20300101", 1, "native-acct"
        self.state = SettlementStatus.UNCONFIRMED
        self.serial = 0
        self.sends = []
        self.on_confirm = lambda: None
        self.return_status = SettlementStatus.CONFIRMED

    def observe_settlement(self):
        self.serial += 1
        return SettlementObservation(
            "TEST", self.account, self.day, self.generation, self.state, f"obs-{self.serial}"
        )

    def validate_settlement_observation(self, observation):
        return (
            type(observation) is SettlementObservation
            and observation.exchange_name == "TEST"
            and observation.account_binding == self.account
            and observation.trading_day == self.day
            and observation.session_generation == self.generation
            and observation.status is self.state
        )

    def confirm_settlement(self, grant, verifier, *, observation):
        verifier.consume(
            grant,
            exchange_name="TEST",
            operation=WriteOperation.SETTLEMENT_CONFIRM,
            session_generation=self.generation,
            settlement_binding=grant.settlement_binding,
        )
        self.sends.append((self.day, observation.observation_id))
        self.on_confirm()
        return self.return_status


def setup_owner(tmp_path, provider, *, admission=True, gate=None, native=None, claim_port=None):
    directory = tmp_path / "guards"
    directory.mkdir(exist_ok=True)
    guard = LocalAccountSessionGuard(directory, "acct", "test-owner")
    generation = guard.acquire()
    path = tmp_path / "journal.sqlite"
    if path.exists():
        with __import__("sqlite3").connect(path) as db:
            journal_id, revision = db.execute(
                "SELECT journal_id,revision FROM direct_journal_meta"
            ).fetchone()
        journal = GatewayDirectCommandJournal(
            path,
            "acct",
            generation=generation,
            expected_journal_id=journal_id,
            minimum_revision=revision,
        )
    else:
        journal = GatewayDirectCommandJournal.create(path, "acct", generation=generation)
    coordinator = DirectWriteCoordinator(WriteAdmission({"TEST": admission}), lambda _: provider)
    coordinator.provider("TEST")
    dispatcher = GatewayDirectDispatcher(
        journal,
        "TEST",
        guard,
        native or (lambda *_: None),
        lambda _: ("x",),
        gate or (lambda _: True),
        claim_port=claim_port,
    )
    controller = GatewaySettlementController(
        coordinator,
        "TEST",
        expected_local_provider=provider,
        expected_account_binding="native-acct",
        journal=journal,
        dispatcher=dispatcher,
    )
    return guard, journal, coordinator, controller


def test_commit_precedes_confirm_readback_reconnect_and_next_day(tmp_path):
    provider = Provider()
    guard, journal, coordinator, controller = setup_owner(tmp_path, provider)
    try:

        def confirm():
            row = journal.settlement_get("TEST", provider.day)
            assert row.status == "ATTEMPTING" and row.marker_id
            provider.state = SettlementStatus.CONFIRMED

        provider.on_confirm = confirm
        result = controller.tick()
        if result.state == 'CONFIRMATION_AUTHORITY_UNAVAILABLE':
            raise SettlementClaimUnavailable(result.state)
        assert result.observed_ready and controller.ready and len(provider.sends) == 1
        assert journal.settlement_get("TEST", provider.day).status == "CONFIRMED"
        provider.generation = 2
        assert not controller.ready
        assert controller.tick().observed_ready and len(provider.sends) == 1
        provider.state = SettlementStatus.UNCONFIRMED
        assert not controller.ready
        assert controller.tick().state == "READBACK_REQUIRED" and len(provider.sends) == 1
        provider.day = "20300102"
        assert controller.tick().observed_ready and len(provider.sends) == 2
    finally:
        guard.release()


def test_crash_restart_unconfirmed_does_not_resend_and_later_readback(tmp_path):
    provider = Provider()
    provider.return_status = SettlementStatus.UNKNOWN
    guard, journal, _, controller = setup_owner(tmp_path, provider)
    first = controller.tick()
    if first.state == 'CONFIRMATION_AUTHORITY_UNAVAILABLE':
        guard.release()
        raise SettlementClaimUnavailable(first.state)
    assert not first.observed_ready and len(provider.sends) == 1
    assert journal.settlement_get("TEST", provider.day).status == "UNKNOWN"
    guard.release()
    provider.generation = 2
    guard2, reopened, _, controller2 = setup_owner(tmp_path, provider)
    try:
        assert controller2.tick().state == "READBACK_REQUIRED" and len(provider.sends) == 1
        provider.state = SettlementStatus.CONFIRMED
        assert controller2.tick().observed_ready and len(provider.sends) == 1
        assert reopened.settlement_get("TEST", provider.day).status == "CONFIRMED"
    finally:
        guard2.release()


def test_lost_commit_reply_and_mark_failure_never_resend(tmp_path, monkeypatch):
    provider = Provider()
    guard, journal, _, controller = setup_owner(tmp_path, provider)
    original = journal._commit

    def lost(db):
        original(db)
        journal._commit = original
        raise OSError("synthetic lost commit reply")

    monkeypatch.setattr(journal, "_commit", lost)
    try:
        # First read is settlement_get; inject the fault only when beginning claim.
        original_begin = journal.settlement_begin

        def fail_reply(*args, **kwargs):
            journal._commit = lost
            return original_begin(*args, **kwargs)

        monkeypatch.setattr(journal, "_commit", original)
        monkeypatch.setattr(journal, "settlement_begin", fail_reply)
        first = controller.tick()
        if first.state == 'CONFIRMATION_AUTHORITY_UNAVAILABLE':
            raise SettlementClaimUnavailable(first.state)
        assert not first.observed_ready
        assert (
            not provider.sends and journal.settlement_get("TEST", provider.day).status == "UNKNOWN"
        )
        assert controller.tick().state == "READBACK_REQUIRED" and not provider.sends
    finally:
        guard.release()


def test_admission_and_native_account_fail_closed(tmp_path):
    provider = Provider()
    guard, journal, coordinator, controller = setup_owner(tmp_path, provider, admission=False)
    try:
        assert controller.tick().state == "ADMISSION_DISABLED"
        assert journal.settlement_get("TEST", provider.day) is None and not provider.sends
        provider.account = "different-native-account"
        assert controller.tick().state == "OBSERVATION_UNAVAILABLE"
        assert not controller.ready and not provider.sends
    finally:
        guard.release()


def test_mark_unknown_failure_keeps_existing_attempt_and_never_resends(tmp_path, monkeypatch):
    provider = Provider()
    provider.return_status = SettlementStatus.UNKNOWN
    guard, journal, _, controller = setup_owner(tmp_path, provider)
    original = journal.settlement_mark_unknown
    monkeypatch.setattr(
        journal,
        "settlement_mark_unknown",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("synthetic")),
    )
    try:
        try:
            result = controller.tick()
        except OSError:
            pass
        else:
            if result.state == 'CONFIRMATION_AUTHORITY_UNAVAILABLE':
                raise SettlementClaimUnavailable(result.state)
            pytest.fail('expected settlement mark failure')
        assert not controller.ready and len(provider.sends) == 1
        assert journal.settlement_get("TEST", provider.day).status == "ATTEMPTING"
        monkeypatch.setattr(journal, "settlement_mark_unknown", original)
        assert controller.tick().state == "READBACK_REQUIRED"
        assert len(provider.sends) == 1
    finally:
        guard.release()


def test_owner_maintenance_blocks_without_releasing_guard_and_stops_on_close(tmp_path):
    from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources

    entered, release = threading.Event(), threading.Event()
    ticks, owner_threads = [], []

    def factory():
        provider = Provider()
        guard, journal, _, controller = setup_owner(tmp_path, provider)

        def maintain():
            ticks.append(time.monotonic())
            owner_threads.append(threading.get_ident())
            entered.set()
            assert release.wait(5)
            controller.tick()

        return GatewayDirectOwnerResources(
            controller._dispatcher, journal, guard.release, maintain, 0.01
        )

    owner = GatewayDirectOwner(
        factory, queue_size=1, max_queue_wait_seconds=2, busy_timeout_seconds=2
    )
    try:
        assert owner.start(5) and entered.wait(5)
        assert owner.status().running and owner.close(0.05) is False
        assert len(ticks) == 1
    finally:
        release.set()
        assert owner.close(5)
    assert len(ticks) == 1 and len(set(owner_threads)) == 1
    assert owner_threads[0] != threading.get_ident()


def test_owner_maintenance_baseexception_closes_and_finalizes_pending(tmp_path):
    from bt_api_py.gateway_direct_owner import (
        GatewayDirectOwner,
        GatewayDirectOwnerResources,
        GatewayDispatchCommand,
    )

    entered, release, cleaned = threading.Event(), threading.Event(), threading.Event()
    finalized = threading.Event()

    def factory():
        provider = Provider()
        guard, journal, _, controller = setup_owner(tmp_path, provider)

        def maintain():
            controller.invalidate()
            entered.set()
            assert release.wait(5)
            raise KeyboardInterrupt()

        def close():
            guard.release()
            cleaned.set()

        return GatewayDirectOwnerResources(controller._dispatcher, journal, close, maintain, 0.01)

    owner = GatewayDirectOwner(
        factory, queue_size=1, max_queue_wait_seconds=2, busy_timeout_seconds=2
    )
    try:
        assert owner.start(5) and entered.wait(5)
        command = GatewayDispatchCommand("s", "k", "p", "r", "a" * 64, 1)
        assert (
            owner.enqueue(command, request_fence=lambda _: None, finalize=finalized.set) == "QUEUED"
        )
        release.set()
        assert cleaned.wait(5) and finalized.is_set()
        assert owner.status().failure_category == "maintenance_failed"
        assert not owner.status().accepting
    finally:
        release.set()
        assert owner.close(5)


def test_dispatch_gate_uses_current_confirmed_ticket(tmp_path):
    from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord
    from bt_api_gateway.direct_dispatch import DirectDispatchError, NativeDispatchOutcome

    provider, sends, holder = Provider(), [], {}

    def native(payload, port, account):
        identity = NativeOrderIdentity("TEST", "ref-1", {"day": "20300101"}, 1)
        record = PreDispatchRecord(
            operation=WriteOperation.PLACE,
            host_scope=account,
            exchange_name="TEST",
            symbol="rb",
            identity=identity,
            target_identity=None,
            request_summary=payload,
            trading_day="20300101",
            session_generation=1,
            created_at_utc="synthetic",
        )
        marker = port.commit(record)
        sends.append(marker)
        return NativeDispatchOutcome(True, identity, marker)

    from iter63_claim_support import SyntheticClaimPort, claiming

    guard, journal, _, controller = setup_owner(
        tmp_path, provider, gate=lambda row: holder["controller"].ready,
        native=claiming(native), claim_port=SyntheticClaimPort(),
    )
    holder["controller"] = controller
    try:
        row = journal.reserve("s", "key", "p", {"symbol": "rb"})

        def dispatch():
            try:
                return controller._dispatcher.dispatch(
                    "s", "key", "p", row.reservation_id, row.digest, row.version
                )
            except DirectDispatchError as error:
                if error.code == 'DISPATCH_CLAIM_UNAVAILABLE':
                    raise DispatchClaimUnavailable(error.code) from error
                raise

        with pytest.raises(DirectDispatchError, match="ADMISSION_DENIED"):
            dispatch()
        assert not sends and journal.get("s", "key", "p").status == "RESERVED"
        provider.state = SettlementStatus.CONFIRMED
        assert controller.tick().observed_ready
        provider.generation = 2
        with pytest.raises(DirectDispatchError, match="ADMISSION_DENIED"):
            dispatch()
        assert not sends and journal.get("s", "key", "p").status == "RESERVED"
        assert controller.tick().observed_ready
        assert dispatch().status == "SENT" and sends == [row.reservation_id]
    finally:
        guard.release()


def test_ready_rechecks_guard_but_status_is_cached_diagnostic(tmp_path):
    provider = Provider()
    provider.state = SettlementStatus.CONFIRMED
    guard, _, _, controller = setup_owner(tmp_path, provider)
    assert controller.tick().observed_ready and controller.ready
    original = provider.validate_settlement_observation
    calls = []

    def validator(observation):
        calls.append(threading.get_ident())
        return original(observation)

    provider.validate_settlement_observation = validator
    values = []
    thread = threading.Thread(
        target=lambda: values.append((controller.status.observed_ready, controller.ready))
    )
    thread.start()
    thread.join(2)
    assert not thread.is_alive() and values == [(True, False)] and calls == []
    guard.release()
    assert controller.status.observed_ready  # cached only, never an authorization
    assert not controller.ready


@pytest.mark.parametrize("required", [False, None])
def test_controller_rejects_non_settlement_routes(tmp_path, required):
    provider = Provider()
    guard, journal, coordinator, existing = setup_owner(tmp_path, provider)
    try:
        provider.settlement_required = required
        with pytest.raises(ValueError, match="INVALID_SETTLEMENT_COMPOSITION"):
            GatewaySettlementController(
                coordinator,
                "TEST",
                expected_local_provider=provider,
                expected_account_binding="native-acct",
                journal=journal,
                dispatcher=existing._dispatcher,
            )
    finally:
        guard.release()


def test_forked_controller_does_not_touch_inherited_locked_state(tmp_path):
    import os
    import select
    import signal

    if not hasattr(os, "fork"):
        pytest.skip("fork unavailable")
    guard, _, _, controller = setup_owner(tmp_path, Provider())
    read_fd, write_fd = os.pipe()
    controller._lock.acquire()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(read_fd)
            result = [str(controller.ready), controller.status.state]
            for action in (controller.invalidate, controller.tick):
                try:
                    action()
                    result.append("allowed")
                except RuntimeError as error:
                    result.append(str(error))
            os.write(write_fd, "|".join(result).encode())
            os._exit(0)
        except BaseException:
            os._exit(1)
    os.close(write_fd)
    try:
        readable, _, _ = select.select([read_fd], [], [], 3)
        if not readable:
            os.kill(pid, signal.SIGKILL)  # only this test's own child
            pytest.fail("forked child blocked on inherited lock")
        data = os.read(read_fd, 4096).decode()
        _, code = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(code) == 0
        assert data == "False|WRONG_PROCESS|SETTLEMENT_WRONG_PROCESS|SETTLEMENT_WRONG_PROCESS"
    finally:
        controller._lock.release()
        os.close(read_fd)
        with __import__("contextlib").suppress(ChildProcessError):
            os.waitpid(pid, 0)
        guard.release()


@pytest.mark.parametrize("change", ["session", "admission"])
def test_precommit_recheck_rolls_back_when_ticket_or_admission_changes(
    tmp_path, monkeypatch, change
):
    provider = Provider()
    guard, journal, coordinator, controller = setup_owner(tmp_path, provider)
    original = journal.settlement_begin

    def changing(*args, **kwargs):
        callback = kwargs["before_commit"]

        def changed():
            if change == "session":
                provider.generation += 1
            else:
                coordinator.admission._enabled["TEST"] = False
            callback()

        kwargs["before_commit"] = changed
        return original(*args, **kwargs)

    monkeypatch.setattr(journal, "settlement_begin", changing)
    try:
        first = controller.tick()
        if first.state == 'CONFIRMATION_AUTHORITY_UNAVAILABLE':
            raise SettlementClaimUnavailable(first.state)
        assert not first.observed_ready
        assert not provider.sends
        assert journal.settlement_get("TEST", provider.day) is None
    finally:
        guard.release()


def test_owner_maintenance_runs_between_busy_queued_tasks(tmp_path):
    from bt_api_py.gateway_direct_owner import (
        GatewayDirectOwner,
        GatewayDirectOwnerResources,
        GatewayDispatchCommand,
    )

    sequence, initialized = [], threading.Event()

    def factory():
        guard, journal, _, controller = setup_owner(tmp_path, Provider())
        dispatcher = controller._dispatcher

        def dispatch(*_args, **_kwargs):
            sequence.append(("dispatch", threading.get_ident()))
            time.sleep(0.02)

        dispatcher.dispatch = dispatch

        def maintain():
            sequence.append(("maintain", threading.get_ident()))
            initialized.set()

        return GatewayDirectOwnerResources(dispatcher, journal, guard.release, maintain, 0.005)

    owner = GatewayDirectOwner(
        factory, queue_size=5, max_queue_wait_seconds=5, busy_timeout_seconds=2
    )
    try:
        assert owner.start(5) and initialized.wait(5)
        completed = [threading.Event() for _ in range(3)]
        for index, done in enumerate(completed):
            command = GatewayDispatchCommand("s", f"k{index}", "p", f"r{index}", "a" * 64, 1)
            assert (
                owner.enqueue(command, request_fence=lambda _: None, finalize=done.set) == "QUEUED"
            )
        assert all(done.wait(5) for done in completed)
    finally:
        assert owner.close(5)
    names = [name for name, _ in sequence]
    assert names.count("dispatch") == 3 and names.count("maintain") >= 3
    assert names.index("maintain", names.index("dispatch") + 1) < len(names) - 1
    assert len({thread for _, thread in sequence}) == 1


def test_always_due_maintenance_cannot_starve_queued_work(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import bt_api_py.gateway_direct_owner as owner_module
    from bt_api_py.gateway_direct_owner import (
        GatewayDirectOwner,
        GatewayDirectOwnerResources,
        GatewayDispatchCommand,
    )

    # At this magnitude 0.001 rounds away, so maintenance is continuously due.
    monkeypatch.setattr(owner_module, "time", SimpleNamespace(monotonic=lambda: 1e15))
    initialized = threading.Event()
    seen = []

    def factory():
        guard, journal, _, controller = setup_owner(tmp_path, Provider())
        dispatcher = controller._dispatcher
        dispatcher.dispatch = lambda *_a, **_k: seen.append("dispatch")

        def maintain():
            seen.append("maintain")
            initialized.set()

        return GatewayDirectOwnerResources(dispatcher, journal, guard.release, maintain, 0.001)

    owner = GatewayDirectOwner(
        factory, queue_size=5, max_queue_wait_seconds=5, busy_timeout_seconds=2
    )
    try:
        assert owner.start(5) and initialized.wait(5)
        done = [threading.Event() for _ in range(3)]
        for index, event in enumerate(done):
            cmd = GatewayDispatchCommand("s", f"k{index}", "p", f"r{index}", "a" * 64, 1)
            assert owner.enqueue(cmd, request_fence=lambda _: None, finalize=event.set) == "QUEUED"
        assert all(event.wait(5) for event in done)
    finally:
        assert owner.close(5)
    assert seen.count("dispatch") == 3


def test_settlement_confirmation_does_not_clear_order_unknown(tmp_path):
    provider = Provider()
    provider.state = SettlementStatus.CONFIRMED
    guard, journal, _, controller = setup_owner(tmp_path, provider)
    try:
        row = journal.reserve("strategy", "order", "principal", {"symbol": "rb"})
        native = {
            "exchange_name": "TEST",
            "client_order_id": "client-1",
            "session_generation": 1,
            "fields_json": "{}",
        }
        started = journal.begin_dispatch(
            "strategy",
            "order",
            row.reservation_id,
            row.digest,
            "principal",
            row.version,
            native,
            ("strong-1",),
        )
        journal.finish(
            "strategy",
            "order",
            row.reservation_id,
            row.digest,
            "principal",
            started.record.version,
            native,
            ("strong-1",),
            "UNKNOWN",
        )
        assert controller.tick().observed_ready
        assert journal.get("strategy", "order", "principal").status == "UNKNOWN"
    finally:
        guard.release()


def test_owner_rejects_submillisecond_maintenance_interval_and_cleans_up(tmp_path):
    from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources

    cleaned = threading.Event()

    def factory():
        guard, journal, _, controller = setup_owner(tmp_path, Provider())

        def close():
            guard.release()
            cleaned.set()

        return GatewayDirectOwnerResources(
            controller._dispatcher, journal, close, lambda: None, 1e-300
        )

    owner = GatewayDirectOwner(
        factory, queue_size=1, max_queue_wait_seconds=1, busy_timeout_seconds=1
    )
    assert not owner.start(5)
    assert cleaned.wait(5)
    assert owner.close(5)


@pytest.mark.parametrize("required", [False, None, 1, "true"])
def test_ready_revokes_when_provider_settlement_declaration_changes(tmp_path, required):
    provider = Provider()
    provider.state = SettlementStatus.CONFIRMED
    guard, _, _, controller = setup_owner(tmp_path, provider)
    try:
        assert controller.tick().observed_ready and controller.ready
        provider.settlement_required = required
        assert not controller.ready
    finally:
        guard.release()


def test_ready_revokes_when_provider_settlement_declaration_raises(tmp_path):
    class BrokenDeclaration(Provider):
        broken = False

        @property
        def settlement_required(self):
            if self.broken:
                raise RuntimeError("synthetic")
            return True

    provider = BrokenDeclaration()
    provider.state = SettlementStatus.CONFIRMED
    guard, _, _, controller = setup_owner(tmp_path, provider)
    try:
        assert controller.tick().observed_ready and controller.ready
        provider.broken = True
        assert not controller.ready
    finally:
        guard.release()


def test_ready_rechecks_guard_after_ticket_validation(tmp_path):
    provider = Provider()
    provider.state = SettlementStatus.CONFIRMED
    guard, _, _, controller = setup_owner(tmp_path, provider)
    assert controller.tick().observed_ready
    original = provider.validate_settlement_observation

    def releases_guard(observation):
        valid = original(observation)
        guard.release()
        return valid

    provider.validate_settlement_observation = releases_guard
    assert not controller.ready


@pytest.mark.parametrize("change", ["session", "admission"])
def test_sqlite_write_lock_wait_rechecks_ticket_and_admission(tmp_path, monkeypatch, change):
    import sqlite3

    provider = Provider()
    guard, journal, coordinator, controller = setup_owner(tmp_path, provider)
    locked, release, entered, returned, cancelled = (threading.Event() for _ in range(5))
    errors = []

    def hold_lock():
        try:
            with sqlite3.connect(tmp_path / "journal.sqlite", timeout=5) as db:
                db.execute("BEGIN IMMEDIATE")
                locked.set()
                assert release.wait(5)
                db.commit()
        except BaseException as error:
            errors.append(error)
            locked.set()

    locker = threading.Thread(target=hold_lock)
    changer = None
    original = journal.settlement_begin

    def waiting(*args, **kwargs):
        locker.start()
        assert locked.wait(5) and not errors
        entered.set()
        try:
            return original(*args, **kwargs)
        finally:
            returned.set()

    def change_while_waiting():
        try:
            assert entered.wait(5)
            if cancelled.is_set():
                return
            time.sleep(0.1)
            assert not returned.is_set(), "settlement_begin did not wait for the SQLite writer"
            if change == "session":
                provider.generation += 1
            else:
                coordinator.admission._enabled["TEST"] = False
        except BaseException as error:
            errors.append(error)
        finally:
            release.set()

    monkeypatch.setattr(journal, "settlement_begin", waiting)
    changer = threading.Thread(target=change_while_waiting)
    changer.start()
    try:
        first = controller.tick()
        if first.state == 'CONFIRMATION_AUTHORITY_UNAVAILABLE':
            raise SettlementClaimUnavailable(first.state)
        assert not first.observed_ready
        assert not provider.sends
        assert journal.settlement_get("TEST", provider.day) is None
    finally:
        release.set()
        cancelled.set()
        entered.set()
        if locker.ident is not None:
            locker.join(5)
        changer.join(5)
        guard.release()
    assert not locker.is_alive() and not changer.is_alive() and not errors
