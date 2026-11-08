"""Synthetic native integration for the owner-thread instrument service."""
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bt_api/bt_api_ctp/tests"))
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.event_journal import GatewayEventJournal
from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
from bt_api_gateway.service_component import GatewayReadService, GatewayServiceAccountScope
from test_iter63_gateway_instrument_query import KEY, row
from test_iter63_gateway_session import KEY as ACCOUNT
from test_iter63_gateway_session import prepared as ctp_prepared  # noqa: F401

from bt_api_py.gateway_direct_owner import GatewayDirectOwner
from bt_api_py.gateway_instrument_service import INSTRUMENT_OPERATIONS, GatewayInstrumentService
from bt_api_py.service_registry import (
    ActiveRegistrationContext,
    ServiceCallContext,
    ServiceResultTooLargeError,
)


class Call:
    def __init__(self, callback):
        self.callback, self.done, self.value, self.error = callback, False, None, None
    def cancel(self):
        if self.done:
            return False
        self.done, self.error = True, RuntimeError("cancelled")
        return True
    def result(self, *, timeout_seconds):
        assert timeout_seconds == 0 and self.done
        if self.error:
            raise self.error
        return self.value
    def execute(self):
        if not self.done:
            try:
                self.value = self.callback()
            except Exception as error:
                self.error = error
            self.done = True


@pytest.fixture
def harness(ctp_prepared, tmp_path, monkeypatch):  # noqa: F811
    create, _, _, _, natives, streams, _, fake_md = ctp_prepared
    session = create()
    session.connect(generation="g1", timeout=1)
    native = natives[0]
    requests = []
    def query(field, request_id):
        requests.append((field.InstrumentID, field.ExchangeID))
        native.client._handle_query_callback("instruments", row(), None, request_id, False)
        native.client._handle_query_callback("instruments", None, None, request_id, True)
        return 0
    native.ReqQryInstrument = query
    account = ACCOUNT.account_key_id
    journal = GatewayDirectCommandJournal.create(tmp_path / "direct", account, generation="g1")
    event = GatewayEventJournal(tmp_path / "event")
    principal = GatewayPrincipal("p", frozenset({account}), frozenset({"state"}), frozenset({GatewayCommandKind.READ}))
    account_scope = GatewayServiceAccountScope(account, "domain", account,
        account_read_strategy_scope="state", shared_account_facts_scope="state",
        shared_fact_strategy_refs=frozenset({"strategy"}))
    read = GatewayReadService(event, accounts={account: account_scope}, principals={"p": principal})
    owner = object.__new__(GatewayDirectOwner)
    owner._thread = threading.current_thread()
    owner_binding = SimpleNamespace(generation="g1", account_scope=account)
    calls = []
    monkeypatch.setattr(GatewayDirectOwner, "binding", lambda _: owner_binding)
    monkeypatch.setattr(GatewayDirectOwner, "is_bound_to", lambda _, candidate: candidate is journal)
    monkeypatch.setattr(GatewayDirectOwner, "claim_service_port", lambda *_: None)
    def submit(_, callback):
        call = Call(callback)
        calls.append(call)
        return call
    monkeypatch.setattr(GatewayDirectOwner, "submit_service_call", submit)
    service = GatewayInstrumentService(owner, session, journal, read, (KEY,), frozenset({"p"}))
    context = ServiceCallContext(WireIdentity(WireRole.STRATEGY_CLIENT, "p", "strategy"),
        WireScope("domain", account_ref=account, strategy_ref="strategy", run_id="run"),
        "registration", "p", lambda _: True)
    active = ActiveRegistrationContext(context, "gateway", "service.account_read", "scope", INSTRUMENT_OPERATIONS,
        frozenset(), "epoch", "policy", "requirements", time.time() + 60)
    yield SimpleNamespace(service=service, ctx=context, active=active, calls=calls, requests=requests,
                          native=native, session=session, streams=streams, md=fake_md)
    service.begin_close()
    assert session.close()


def call(h, operation, opid="a" * 32, **fields):
    return h.service.handle("account.instrument_spec." + operation, context=h.ctx,
                            arguments={"operation_id": opid, **fields})


def advance(h, contexts=None):
    h.service.maintain_registrations((h.active,) if contexts is None else contexts)
    for item in h.calls:
        item.execute()


def test_real_reader_same_trader_and_each_ready_rechecked(harness):
    h = harness
    assert call(h, "begin", instrument=KEY.to_dict())["status"] == "pending"
    assert call(h, "begin", instrument=KEY.to_dict())["status"] == "pending"
    for _ in range(200):
        advance(h)
        status = call(h, "status")
        if status["status"] == "ready":
            break
        time.sleep(.002)
    else:
        pytest.fail("synthetic native query did not finish")
    assert h.requests == [("rb2701", "SHFE")]
    assert h.streams == [] and h.md.instances == []
    snap = status["snapshot"]
    assert snap["instrument"] == KEY.to_dict()
    assert snap["session_generation"] == "g1" and 0 < snap["remaining_seconds"] <= 35
    assert snap["spec"]["spec"]["min_quantity"] == "3"
    assert snap["spec"]["spec"]["max_quantity"] == "100"
    assert call(h, "status")["status"] == "pending"
    advance(h)
    advance(h)
    assert call(h, "status")["status"] == "ready"


def test_precancel_and_scope_capacity_and_budget(harness):
    h = harness
    assert call(h, "cancel")["status"] == "cancelled"
    assert call(h, "begin", instrument=KEY.to_dict())["status"] == "cancelled"
    assert not h.requests
    foreign = replace(h.ctx, registration_id="other")
    assert h.service.handle("account.instrument_spec.status", context=foreign,
                            arguments={"operation_id": "a" * 32})["status"] == "not_found"
    denied = replace(h.ctx, result_fits=lambda _: False)
    with pytest.raises(ServiceResultTooLargeError):
        h.service.handle("account.instrument_spec.begin", context=denied,
            arguments={"operation_id": "b" * 32, "instrument": KEY.to_dict()})
    assert call(h, "status", "b" * 32)["status"] == "not_found"


def test_revoke_while_queued_does_not_start_query(harness):
    h = harness
    call(h, "begin", instrument=KEY.to_dict())
    h.service.maintain_registrations((h.active,))
    assert len(h.calls) == 1 and not h.calls[0].done
    h.service.maintain_registrations(())
    h.calls[0].execute()
    h.service.maintain_registrations(())
    assert not h.requests


def test_relogin_rejects_old_observation_and_new_job_uses_new_reader(harness):
    from test_iter62_settlement_readback_on_write import TODAY

    from tests.integration.test_iter63_ctp_gateway_return_maintenance import _relogin_same_spi
    h = harness
    call(h, "begin", instrument=KEY.to_dict())
    for _ in range(200):
        advance(h)
        if call(h, "status")["status"] == "ready":
            break
        time.sleep(.002)
    else:
        pytest.fail("initial query did not finish")
    # The first validated observation has been consumed. A new delivery
    # cycle must check the old provider binding again after the new login.
    _relogin_same_spi(h.native, TODAY, 20)
    h.session.refresh_session()  # owner maintenance rebinds the account reader
    assert call(h, "status")["status"] == "pending"
    for _ in range(30):
        advance(h)
        value = call(h, "status")
        if value["status"] == "failed":
            break
    assert value["status"] == "failed" and value["snapshot"] is None
    second = "b" * 32
    assert call(h, "begin", second, instrument=KEY.to_dict())["status"] == "pending"
    for _ in range(200):
        advance(h)
        value = call(h, "status", second)
        if value["status"] != "pending":
            break
        time.sleep(.002)
    assert value["status"] == "ready", [(type(c.error).__name__, str(c.error)) for c in h.calls if c.error]
    assert len(h.requests) == 2 and h.streams == []


def test_relogin_cleans_old_queued_native_handle(harness):
    from test_iter62_settlement_readback_on_write import _login
    h = harness
    call(h, "begin", instrument=KEY.to_dict())
    # The begin callback has made an owner-bound reader and queued its native
    # handle, but the provider has not polled/sent it yet.
    advance(h)
    h.service.maintain_registrations((h.active,))
    job = h.service._jobs[(h.ctx.registration_id, "a" * 32)]
    assert job.handle is not None
    _login(h.native.client, h.native, session_id=20)
    h.session.refresh_session()
    assert call(h, "cancel")["status"] == "cancelled"
    for _ in range(10):
        for queued in h.calls:
            queued.execute()
        advance(h)
        if job.handle is None and job.call is None:
            break
    assert job.handle is None
    assert not h.requests


def test_nine_sequential_jobs_and_pending_limit(harness):
    h = harness
    for number in range(9):
        opid = f"{number:032x}"
        assert call(h, "begin", opid, instrument=KEY.to_dict())["status"] == "pending"
        for _ in range(200):
            advance(h)
            value = call(h, "status", opid)
            if value["status"] != "pending":
                break
            time.sleep(.002)
        assert value["status"] == "ready"
    assert len(h.requests) == 9
    # Finished jobs do not consume the eight concurrent pending slots.
    for number in range(100, 108):
        call(h, "begin", f"{number:032x}", instrument=KEY.to_dict())
    with pytest.raises(ValueError, match="CAPACITY_EXCEEDED"):
        call(h, "begin", f"{108:032x}", instrument=KEY.to_dict())


def test_terminal_limit_and_retention_do_not_renew(harness):
    h = harness
    for number in range(64):
        assert call(h, "cancel", f"{number:032x}")["status"] == "cancelled"
    with pytest.raises(ValueError, match="CAPACITY_EXCEEDED"):
        call(h, "cancel", f"{64:032x}")
    job = h.service._jobs[(h.ctx.registration_id, f"{0:032x}")]
    first = job.terminal_at
    assert call(h, "cancel", f"{0:032x}")["status"] == "cancelled"
    assert call(h, "begin", f"{0:032x}", instrument=KEY.to_dict())["status"] == "cancelled"
    assert job.terminal_at == first
    job.terminal_at = time.monotonic() - 61
    advance(h)
    assert call(h, "status", f"{0:032x}")["status"] == "not_found"
    assert call(h, "cancel", f"{64:032x}")["status"] == "cancelled"


def test_owner_close_cleans_begin_handle_before_result_consumed(harness):
    h = harness
    call(h, "begin", instrument=KEY.to_dict())
    advance(h)  # callback completed; its result has not been consumed
    job = h.service._jobs[(h.ctx.registration_id, "a" * 32)]
    assert job.handle is not None and job.call is not None
    h.service.begin_close()
    h.service.cleanup_on_owner()
    assert job.handle is None
    assert not h.requests


def test_owner_close_retry_preserves_unreleased_handle(harness):
    h = harness
    call(h, "begin", instrument=KEY.to_dict())
    advance(h)
    job = h.service._jobs[(h.ctx.registration_id, "a" * 32)]
    reader, handle = job.reader, job.handle
    h.service.begin_close()
    locked, release = threading.Event(), threading.Event()

    def hold_native_lock():
        with h.native.client._query_state_lock:
            locked.set()
            release.wait(5)

    thread = threading.Thread(target=hold_native_lock)
    thread.start()
    assert locked.wait(2)
    try:
        assert h.service.cleanup_on_owner() is False
        assert job.reader is reader and job.handle is handle
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    assert h.service.cleanup_on_owner() is True
    assert job.handle is None


def test_owner_close_cleans_terminal_poll_before_result_consumed(harness):
    h = harness
    call(h, "begin", instrument=KEY.to_dict())
    advance(h)
    # Consume begin and queue the poll. Execute polls until provider completion,
    # but leave the terminal poll callback result unconsumed by maintenance.
    for _ in range(200):
        h.service.maintain_registrations((h.active,))
        pending = h.calls[-1]
        pending.execute()
        if pending.value is not None and not isinstance(pending.value, tuple):
            break
        time.sleep(.002)
    else:
        pytest.fail("terminal poll did not complete")
    job = h.service._jobs[(h.ctx.registration_id, "a" * 32)]
    assert job.handle is not None
    h.service.begin_close()
    assert h.service.cleanup_on_owner() is True
    assert job.handle is None


@pytest.mark.parametrize("initial_state", ["pending", "ready"])
def test_oversized_cancel_preserves_existing_job(harness, initial_state):
    h = harness
    call(h, "begin", instrument=KEY.to_dict())
    advance(h)
    job = h.service._jobs[(h.ctx.registration_id, "a" * 32)]
    if initial_state == "ready":
        for _ in range(200):
            advance(h)
            if job.state == "ready":
                break
            time.sleep(.002)
        else:
            pytest.fail("synthetic query did not reach ready")
    assert job.state == initial_state and job.handle is not None
    before = (job.state, job.code, job.terminal_at, job.cancel_requested,
              job.reader, job.handle, job.checked_at, job.call)
    denied = replace(h.ctx, result_fits=lambda _: False)
    with pytest.raises(ServiceResultTooLargeError):
        h.service.handle("account.instrument_spec.cancel", context=denied,
                         arguments={"operation_id": "a" * 32})
    assert (job.state, job.code, job.terminal_at, job.cancel_requested,
            job.reader, job.handle, job.checked_at, job.call) == before
    assert call(h, "cancel")["status"] == "cancelled"


def _checked_unconsumed_job(h):
    call(h, "begin", instrument=KEY.to_dict())
    job = h.service._jobs[(h.ctx.registration_id, "a" * 32)]
    for _ in range(200):
        advance(h)
        if job.state == "ready":
            break
        time.sleep(.002)
    else:
        pytest.fail("synthetic query did not reach ready")
    assert call(h, "status")["status"] == "pending"
    for _ in range(200):
        advance(h)
        if job.checked_at is not None:
            return job
        time.sleep(.002)
    pytest.fail("owner validation did not complete")


def test_relogin_after_owner_check_before_status_never_releases_old_ready(harness):
    from test_iter62_settlement_readback_on_write import _login
    h = harness
    job = _checked_unconsumed_job(h)
    assert job.checked_at is not None
    _login(h.native.client, h.native, session_id=20)
    h.session.refresh_session()
    value = call(h, "status")
    assert value["status"] == "failed" and value["snapshot"] is None


def test_busy_source_probe_returns_pending_and_schedules_new_check(harness):
    h = harness
    job = _checked_unconsumed_job(h)
    locked, release = threading.Event(), threading.Event()

    def hold_native_state():
        with h.native.client._query_state_lock:
            locked.set()
            release.wait(5)

    thread = threading.Thread(target=hold_native_state)
    thread.start()
    assert locked.wait(2)
    try:
        start = time.monotonic()
        value = call(h, "status")
        assert time.monotonic() - start < 1
        assert value["status"] == "pending" and value["snapshot"] is None
        assert job.checked_at is None and job.check_requested
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
    for _ in range(200):
        advance(h)
        value = call(h, "status")
        if value["status"] == "ready":
            break
        time.sleep(.002)
    assert value["status"] == "ready"
