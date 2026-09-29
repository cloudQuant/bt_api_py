"""Fake-only contracts for consuming an already committed I9 CTP OrderRef."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from dataclasses import dataclass, replace
from decimal import Decimal
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

import bt_api_py._execution_session as execution_session_module
from bt_api_py import (
    BtApi,
    CancelOrderRequest,
    CtpCancelIdentityBinding,
    CtpOrderIdentityBinding,
    NormalizedApiError,
    OrderRequest,
)
from bt_api_py._contracts.errors import CapabilityNotSupportedError
from bt_api_py._contracts.models import ForwardingConfig, OrderType, Side
from bt_api_py._execution_session import _ExecutionSession
from bt_api_py.forwarding.btapi_backend import ZmqBtApiBackend

VENUE = "CTP___FUTURE"
ACCOUNT = "acct_0123456789abcdef"
TRADING_DAY = "20260926"
MANAGED_INTENT = "intent.iter41.consume"
RUNTIME_ORDER_ID = "bt-managed-v1:" + "a" * 64
STRATEGY_ID = "iter41.ctp.candidate"


def digest(payload):
    body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


ACCOUNT_KEY = "account:" + digest(
    {"provider": "CTP", "environment": "simulation", "account_ref": ACCOUNT}
)
SCOPE_KEY = "scope:" + digest(
    {
        "provider": "CTP",
        "environment": "simulation",
        "account_ref": ACCOUNT,
        "strategy_id": STRATEGY_ID,
        "trading_day": TRADING_DAY,
    }
)


@dataclass(frozen=True, slots=True)
class ExecutionScope:
    provider: str
    environment: str
    account_ref: str
    strategy_id: str
    trading_day: str
    account_key: str
    key: str


ExecutionScope.__module__ = "bt_api_execution.contracts"


@dataclass(frozen=True, slots=True)
class CtpOrderIdentityReservation:
    account_key: str
    trading_day: str
    scope_key: str
    managed_intent_id: str
    runtime_order_id: str
    order_ref: str
    created_at_ns: int


CtpOrderIdentityReservation.__module__ = "bt_api_execution.store"


@dataclass(frozen=True, slots=True)
class CtpDispatchCorrelationKey:
    version: int
    account_key: str
    scope_key: str
    trading_day: str
    operation: str
    command_id: str
    request_payload_sha256: str
    reservation_managed_intent_id: str
    managed_action_id: str
    runtime_order_id: str
    order_ref: str
    cancel_target_exchange_id: str
    cancel_target_order_sys_id: str
    cancel_target_front_id: int
    cancel_target_session_id: int
    approval_use_id: str
    approval_digest: str
    session_binding_sha256: str
    session_generation_id: str
    dispatch_front_id: int
    dispatch_session_id: int
    native_request_id: int
    native_action_ref: int
    native_request_payload_sha256: str


CtpDispatchCorrelationKey.__module__ = "bt_api_execution.store"


@dataclass(frozen=True, slots=True)
class CtpDispatchCommand:
    account_key: str
    scope_key: str
    trading_day: str
    operation: str
    command_id: str
    request_payload: dict[str, object]
    request_payload_sha256: str
    native_request_payload: dict[str, object]
    native_request_payload_sha256: str
    reservation_managed_intent_id: str
    order_ref: str | None
    cancel_target_order_ref: str
    cancel_target_exchange_id: str
    cancel_target_order_sys_id: str
    cancel_target_front_id: int
    cancel_target_session_id: int
    approval_use_id: str
    approval_digest: str
    session_binding: dict[str, object]
    session_binding_sha256: str
    status: str
    created_at_ns: int
    correlation_key: CtpDispatchCorrelationKey


CtpDispatchCommand.__module__ = "bt_api_execution.store"


@dataclass(frozen=True, slots=True)
class CtpDispatchProjection:
    command_id: str
    operation: str
    command_status: str
    cancel_action: object | None = None


CtpDispatchProjection.__module__ = "bt_api_execution.store"


@dataclass(frozen=True, slots=True)
class CtpProjectedOrderState:
    provider_state: str | None = None
    terminal: bool | None = None
    source_kind: str | None = None
    updated_at_ns: int | None = None


CtpProjectedOrderState.__module__ = "bt_api_execution.store"


@dataclass(frozen=True, slots=True)
class CtpTargetOrderProjection:
    managed_intent_id: str
    runtime_order_id: str
    order_ref: str
    exchange_id: str
    order_sys_id: str
    front_id: int
    session_id: int
    order_state: CtpProjectedOrderState


CtpTargetOrderProjection.__module__ = "bt_api_execution.store"


@dataclass(frozen=True, slots=True)
class CtpCancelActionProjection:
    managed_action_id: str
    action_state: str | None
    terminal: bool | None
    source_kind: str | None
    updated_at_ns: int | None
    target_order: CtpTargetOrderProjection


CtpCancelActionProjection.__module__ = "bt_api_execution.store"


def scope(**changes):
    values = {
        "provider": "CTP",
        "environment": "simulation",
        "account_ref": ACCOUNT,
        "strategy_id": STRATEGY_ID,
        "trading_day": TRADING_DAY,
        "account_key": ACCOUNT_KEY,
        "key": SCOPE_KEY,
    }
    values.update(changes)
    values["account_key"] = "account:" + digest(
        {
            "provider": values["provider"],
            "environment": values["environment"],
            "account_ref": values["account_ref"],
        }
    )
    values["key"] = "scope:" + digest(
        {
            "provider": values["provider"],
            "environment": values["environment"],
            "account_ref": values["account_ref"],
            "strategy_id": values["strategy_id"],
            "trading_day": values["trading_day"],
        }
    )
    return ExecutionScope(**values)


def reservation(**changes):
    values = {
        "account_key": ACCOUNT_KEY,
        "trading_day": TRADING_DAY,
        "scope_key": SCOPE_KEY,
        "managed_intent_id": MANAGED_INTENT,
        "runtime_order_id": RUNTIME_ORDER_ID,
        "order_ref": "000000000137",
        "created_at_ns": 1780000000000000000,
    }
    values.update(changes)
    return CtpOrderIdentityReservation(**values)


def order_identity_binding(*, requested_scope=None, **changes):
    bound_scope = requested_scope or scope()
    values = {
        "environment": bound_scope.environment,
        "account_key": bound_scope.account_key,
        "trading_day": bound_scope.trading_day,
        "scope_key": bound_scope.key,
        "managed_intent_id": MANAGED_INTENT,
        "runtime_order_id": RUNTIME_ORDER_ID,
    }
    values.update(changes)
    return CtpOrderIdentityBinding(**values)


def managed_order_request(binding=None, **changes):
    values = {
        "symbol": "IF2610",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("1"),
        "price": Decimal("1"),
        "account_id": ACCOUNT,
        "client_order_id": "000000000137",
        "execution_cycle_id": "cycle-iter41-1",
        "execution_role": "entry",
        "strategy_identity_sha256": "d" * 64,
        "ctp_order_identity": binding,
    }
    values.update(changes)
    return OrderRequest(**values)


class ReadOnlyAuthority:
    def __init__(self, value, *, command=None, projection=None):
        self.value = value
        self.command = command
        self.projection = projection
        self.reads = 0
        self.command_reads = 0
        self.projection_reads = 0
        self.native_calls = 0
        self.queue_calls = 0

    def read_ctp_order_identity(self, requested_scope, managed_intent_id):
        self.reads += 1
        assert requested_scope.trading_day == TRADING_DAY
        assert managed_intent_id == self.value.managed_intent_id
        return self.value

    def read_ctp_dispatch_command(self, requested_scope, command_id):
        self.command_reads += 1
        assert requested_scope.trading_day == TRADING_DAY
        if self.command is None or self.command.command_id != command_id:
            return None
        return self.command

    def read_ctp_dispatch_projection(self, requested_scope, command_id):
        self.projection_reads += 1
        assert requested_scope.trading_day == TRADING_DAY
        if self.projection is None or self.projection.command_id != command_id:
            return None
        return self.projection

    def reserve_ctp_order_identity(self, *_args, **_kwargs):
        raise AssertionError("consumer must not allocate an OrderRef")

    def send_native(self, *_args, **_kwargs):
        self.native_calls += 1
        raise AssertionError("consumer must not call a native sender")

    def enqueue(self, *_args, **_kwargs):
        self.queue_calls += 1
        raise AssertionError("consumer must not publish a queue command")


ReadOnlyAuthority.__name__ = "SqliteExecutionStore"
ReadOnlyAuthority.__module__ = "bt_api_execution.store"


def session_for(path: Path):
    session = object.__new__(_ExecutionSession)
    session.config = {
        "require_order_journal": True,
        "market_data_only": False,
        "strategy_id": STRATEGY_ID,
        "strategy_identity_sha256": "d" * 64,
        "required_environments": {},
        "account_ids": {},
    }
    session.path = path
    session.closed = False
    session.mutex = threading.RLock()
    session.persistence_failed = False
    session.owner_token = "candidate-owner"
    session.owner_pid = os.getpid()
    session.fencing_epoch = 0
    session._arm_venue = VENUE
    session._ctp_execution_identity = {
        "provider": "CTP",
        "environment": "demo",
        "environment_profile": "simulation",
        "account_id": ACCOUNT,
        "account_fingerprint": ACCOUNT,
    }
    session._arm_proof = {
        "account_fingerprint": ACCOUNT,
        "trading_day": TRADING_DAY,
        "connection_generation": 4,
    }
    session._ctp_execution_authorization_context = None
    session._arm_proof_sha256 = "e" * 64
    session._last_arm_proof_sha256 = None
    session._recovery_mode = False
    session.used_ids = set()
    session.reserved_ids = set()
    session._ctp_order_identity_mirrors = {}
    session._ctp_cancel_identity_mirrors = {}
    session.cancel_calls = 0
    session.orders = {}
    session._reservation_only_cancel_unknowns = {}
    session.historical_unknown = set()
    session._assert_writer_lease = lambda _operation: None
    return session


def test_bound_managed_ctp_identity_rejects_allocator_before_local_side_effects(
    tmp_path, monkeypatch
):
    journal = tmp_path / "sdk-execution.jsonl"
    session = session_for(journal)
    session._arm_managed = True
    journal_calls = []
    write_guard_calls = []
    clock_calls = []
    session._journal = lambda *args, **kwargs: journal_calls.append((args, kwargs))
    session.require_write = lambda *args, **kwargs: write_guard_calls.append((args, kwargs))

    def forbidden_clock():
        clock_calls.append(True)
        raise AssertionError("managed CTP must reject before reading the local clock")

    monkeypatch.setattr(execution_session_module.time, "time_ns", forbidden_clock)

    with pytest.raises(NormalizedApiError) as caught:
        session.new_client_order_id(VENUE)

    assert caught.value.code == "ctp_order_identity_reservation_required"
    assert not journal.exists()
    assert session.reserved_ids == set()
    assert journal_calls == []
    assert write_guard_calls == []
    assert clock_calls == []


def test_no_session_ctp_allocator_is_rejected_but_unmanaged_fallbacks_remain(monkeypatch):
    api = object.__new__(BtApi)
    api._execution_session = None
    clock_calls = []

    def clock():
        clock_calls.append(True)
        return 137

    monkeypatch.setattr("bt_api_py.bt_api.time.time_ns", clock)

    with pytest.raises(NormalizedApiError) as caught:
        api.new_client_order_id(VENUE)

    assert caught.value.code == "ctp_order_identity_reservation_required"
    assert clock_calls == []

    # A no-session non-CTP caller keeps the legacy timestamp fallback.
    assert api.new_client_order_id("OKX___SWAP") == "000000000137"
    assert clock_calls == [True]


def test_unbound_legacy_ctp_session_keeps_its_local_allocator(monkeypatch):
    session = object.__new__(_ExecutionSession)
    session.mutex = threading.RLock()
    session._arm_managed = False
    session._arm_venue = None
    session._ctp_execution_identity = None
    session.config = {
        "strategy_id": STRATEGY_ID,
        "required_environments": {},
        "account_ids": {},
    }
    session.used_ids = set()
    session.reserved_ids = set()
    journal_rows = []
    session.require_write = lambda *args, **kwargs: None
    session._journal = lambda event, payload: journal_rows.append((event, payload))
    monkeypatch.setattr(execution_session_module.time, "time_ns", lambda: 137)

    client_id = session.new_client_order_id(VENUE)

    assert client_id == "000000000137"
    assert journal_rows == [
        (
            "client_id_reservation",
            {
                "exchange_name": VENUE,
                "account_id": VENUE,
                "client_order_id": client_id,
                "strategy_id": STRATEGY_ID,
            },
        )
    ]
    assert session.reserved_ids == {("CTP", "unverified", VENUE, client_id)}


def consume(
    session,
    authority,
    *,
    requested_scope=None,
    managed_intent_id=MANAGED_INTENT,
    runtime_order_id=RUNTIME_ORDER_ID,
):
    return session.consume_ctp_order_identity_reservation(
        VENUE,
        scope=requested_scope or scope(),
        identity_store=authority,
        managed_intent_id=managed_intent_id,
        runtime_order_id=runtime_order_id,
    )


def seed_local_mirror_fixture(session, value=None):
    """Install non-authorizing SDK mirror state for request-binding tests only."""
    value = value or reservation()
    mirror = execution_session_module.CtpOrderIdentityReservationMirror(
        value.account_key,
        value.trading_day,
        value.scope_key,
        value.managed_intent_id,
        value.runtime_order_id,
        value.order_ref,
        value.created_at_ns,
    )
    key = (mirror.account_key, mirror.trading_day, mirror.scope_key, mirror.managed_intent_id)
    session._ctp_order_identity_mirrors[key] = mirror
    session.reserved_ids.add(session._client_key(VENUE, ACCOUNT, mirror.order_ref))
    return mirror


def test_missing_i9_distribution_rejects_before_sdk_journal_or_authority_call(
    tmp_path, monkeypatch
):
    journal = tmp_path / "sdk-execution.jsonl"
    authority = ReadOnlyAuthority(reservation())

    def missing_distribution(name):
        assert name == "bt_api_execution"
        raise PackageNotFoundError(name)

    monkeypatch.setattr(execution_session_module.importlib_metadata, "version", missing_distribution)
    session = session_for(journal)

    with pytest.raises(NormalizedApiError) as caught:
        consume(session, authority)

    assert caught.value.code == "ctp_order_identity_read_port_unavailable"
    assert not journal.exists()
    assert authority.reads == 0
    assert authority.native_calls == 0
    assert authority.queue_calls == 0


def test_module_and_class_name_spoof_cannot_impersonate_installed_store(tmp_path, monkeypatch):
    journal = tmp_path / "sdk-execution.jsonl"
    authority = ReadOnlyAuthority(reservation())

    class ExpectedInstalledStore:
        def read_ctp_order_identity(self, requested_scope, managed_intent_id):
            pytest.fail("wrong-class object must be rejected before its method is read")

    # The fixture scope type stands in for the imported class so this test
    # reaches the exact store-type comparison even without I9 installed.
    monkeypatch.setattr(
        execution_session_module,
        "_installed_i9_ctp_order_identity_types",
        lambda: (ExecutionScope, ExpectedInstalledStore, CtpOrderIdentityReservation),
    )
    session = session_for(journal)

    with pytest.raises(NormalizedApiError) as caught:
        consume(session, authority)

    assert caught.value.code == "ctp_order_identity_read_port_unavailable"
    assert not journal.exists()
    assert authority.reads == 0
    assert authority.native_calls == 0
    assert authority.queue_calls == 0


def test_module_and_class_name_spoof_cannot_impersonate_installed_scope(tmp_path, monkeypatch):
    journal = tmp_path / "sdk-execution.jsonl"
    authority = ReadOnlyAuthority(reservation())

    class ExpectedInstalledScope:
        pass

    class ExpectedInstalledStore:
        def read_ctp_order_identity(self, requested_scope, managed_intent_id):
            pytest.fail("wrong-scope-type object must be rejected before the store call")

    monkeypatch.setattr(
        execution_session_module,
        "_installed_i9_ctp_order_identity_types",
        lambda: (
            ExpectedInstalledScope,
            ExpectedInstalledStore,
            CtpOrderIdentityReservation,
        ),
    )
    session = session_for(journal)

    with pytest.raises(NormalizedApiError) as caught:
        consume(session, authority)

    assert caught.value.code == "ctp_order_identity_scope_invalid"
    assert not journal.exists()
    assert authority.reads == 0


def test_module_and_class_name_spoof_cannot_impersonate_reservation(tmp_path, monkeypatch):
    journal = tmp_path / "sdk-execution.jsonl"
    authority = ReadOnlyAuthority(reservation())

    class ExpectedInstalledReservation:
        pass

    class ExpectedInstalledStore:
        def read_ctp_order_identity(self, requested_scope, managed_intent_id):
            return authority.read_ctp_order_identity(requested_scope, managed_intent_id)

    monkeypatch.setattr(
        execution_session_module,
        "_installed_i9_ctp_order_identity_types",
        lambda: (ExecutionScope, ExpectedInstalledStore, ExpectedInstalledReservation),
    )
    session = session_for(journal)

    with pytest.raises(NormalizedApiError):
        consume(session, ExpectedInstalledStore())

    assert not journal.exists()
    assert authority.reads == 1
    assert authority.native_calls == 0
    assert authority.queue_calls == 0


@pytest.mark.parametrize(
    "bad_reservation",
    [
        reservation(order_ref="not-a-12-digit-ref"),
        reservation(trading_day="20260925"),
        reservation(scope_key="scope:" + "f" * 64),
        reservation(runtime_order_id="bt-managed-v1:" + "f" * 64),
        reservation(created_at_ns=True),
    ],
)
def test_exact_i9_reservation_still_requires_matching_fields(
    tmp_path, monkeypatch, bad_reservation
):
    journal = tmp_path / "sdk-execution.jsonl"

    class ExpectedInstalledStore:
        def read_ctp_order_identity(self, requested_scope, managed_intent_id):
            return bad_reservation

    monkeypatch.setattr(
        execution_session_module,
        "_installed_i9_ctp_order_identity_types",
        lambda: (ExecutionScope, ExpectedInstalledStore, CtpOrderIdentityReservation),
    )
    session = session_for(journal)

    with pytest.raises(NormalizedApiError) as caught:
        consume(session, ExpectedInstalledStore())

    assert caught.value.code == "ctp_order_identity_authority_read_or_binding_invalid"
    assert not journal.exists()


def test_unexpected_i9_distribution_version_fails_closed_before_import_or_read(
    tmp_path, monkeypatch
):
    journal = tmp_path / "sdk-execution.jsonl"
    authority = ReadOnlyAuthority(reservation())
    monkeypatch.setattr(execution_session_module.importlib_metadata, "version", lambda _name: "0.1.0")
    session = session_for(journal)

    with pytest.raises(NormalizedApiError):
        consume(session, authority)

    assert not journal.exists()
    assert authority.reads == 0
    assert authority.native_calls == 0
    assert authority.queue_calls == 0


def test_mapping_is_not_accepted_as_installed_typed_authority_reservation(
    tmp_path, monkeypatch
):
    row = {
        "account_key": ACCOUNT_KEY,
        "trading_day": TRADING_DAY,
        "scope_key": SCOPE_KEY,
        "managed_intent_id": MANAGED_INTENT,
        "runtime_order_id": RUNTIME_ORDER_ID,
        "order_ref": "000000000137",
        "created_at_ns": 1780000000000000000,
    }

    class ExpectedInstalledReservation:
        pass

    class ExpectedInstalledStore:
        def read_ctp_order_identity(self, requested_scope, managed_intent_id):
            return row

    monkeypatch.setattr(
        execution_session_module,
        "_installed_i9_ctp_order_identity_types",
        lambda: (ExecutionScope, ExpectedInstalledStore, ExpectedInstalledReservation),
    )
    journal = tmp_path / "sdk-execution.jsonl"
    session = session_for(journal)

    with pytest.raises(NormalizedApiError):
        consume(session, ExpectedInstalledStore())
    assert not journal.exists()


def test_order_request_identity_round_trip_and_legacy_mapping_compatibility():
    request = managed_order_request(order_identity_binding())
    serialized = request.to_dict()

    assert serialized["ctp_order_identity"] == order_identity_binding().to_dict()
    assert OrderRequest.from_dict(serialized) == request

    legacy = dict(serialized)
    legacy.pop("ctp_order_identity")
    restored_legacy = OrderRequest.from_dict(legacy)
    assert restored_legacy.ctp_order_identity is None
    assert restored_legacy.client_order_id == request.client_order_id
    assert restored_legacy.account_id == request.account_id

    # Old source callers that omit the new optional field keep their old shape.
    assert (
        OrderRequest(
            symbol="IF2610",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("1"),
            account_id=ACCOUNT,
            client_order_id="legacy-id",
        ).ctp_order_identity
        is None
    )


def test_exact_mirror_binding_matches_every_order_identity_field(tmp_path):
    journal = tmp_path / "sdk-execution.jsonl"
    session = session_for(journal)
    session._arm_managed = True
    mirror = seed_local_mirror_fixture(session)
    request = managed_order_request(order_identity_binding(), client_order_id=mirror.order_ref)
    before = journal.read_bytes() if journal.exists() else None
    reserved_before = set(session.reserved_ids)

    session._require_ctp_order_identity("make_order", request)

    assert request.account_id == ACCOUNT
    assert mirror.account_key == request.ctp_order_identity.account_key
    assert mirror.trading_day == request.ctp_order_identity.trading_day
    assert mirror.scope_key == request.ctp_order_identity.scope_key
    assert mirror.managed_intent_id == request.ctp_order_identity.managed_intent_id
    assert mirror.runtime_order_id == request.ctp_order_identity.runtime_order_id
    assert mirror.order_ref == request.client_order_id
    assert request.ctp_order_identity.dispatch_authorized is False
    assert (journal.read_bytes() if journal.exists() else None) == before
    assert session.reserved_ids == reserved_before


@pytest.mark.parametrize(
    "mismatch",
    [
        "account_scope",
        "request_account",
        "trading_day",
        "scope",
        "managed_intent_id",
        "runtime_order_id",
        "order_ref",
    ],
)
def test_managed_ctp_request_identity_must_match_exact_mirror(tmp_path, mismatch):
    session = session_for(tmp_path / "sdk-execution.jsonl")
    session._arm_managed = True
    seed_local_mirror_fixture(session)

    binding = order_identity_binding()
    request_changes = {}
    if mismatch == "account_scope":
        binding = order_identity_binding(requested_scope=scope(account_ref="acct_fedcba9876543210"))
    elif mismatch == "request_account":
        request_changes["account_id"] = "acct_fedcba9876543210"
    elif mismatch == "trading_day":
        binding = order_identity_binding(requested_scope=scope(trading_day="20260925"))
    elif mismatch == "scope":
        binding = order_identity_binding(requested_scope=scope(strategy_id="iter41.ctp.other"))
    elif mismatch == "managed_intent_id":
        binding = order_identity_binding(managed_intent_id="intent.iter41.other")
    elif mismatch == "runtime_order_id":
        binding = order_identity_binding(runtime_order_id="bt-managed-v1:" + "f" * 64)
    else:
        request_changes["client_order_id"] = "000000000138"
    request = managed_order_request(binding, **request_changes)

    with pytest.raises(NormalizedApiError) as caught:
        session._require_ctp_order_identity("make_order", request)

    assert caught.value.code == "ctp_order_identity_binding_missing_or_mismatch"


@pytest.mark.parametrize(
    "include_binding, include_mirror",
    [(False, False), (False, True), (True, False)],
)
def test_missing_ctp_binding_or_mirror_rejects_before_journal_or_dispatch(
    tmp_path, include_binding, include_mirror
):
    journal = tmp_path / "sdk-execution.jsonl"
    session = session_for(journal)
    session._arm_managed = True
    if include_mirror:
        seed_local_mirror_fixture(session)
    journal_before = journal.read_bytes() if journal.exists() else None
    reserved_before = set(session.reserved_ids)
    request = managed_order_request(order_identity_binding() if include_binding else None)
    journal_calls = []
    write_calls = []
    request_id_calls = []
    queue_calls = []
    native_calls = []
    session._journal = lambda *args, **kwargs: journal_calls.append((args, kwargs))
    session._require_arm_scope = lambda *args, **kwargs: None
    session.require_write = lambda *args, **kwargs: write_calls.append((args, kwargs))

    def dispatch():
        queue_calls.append(True)
        native_calls.append(True)

    with pytest.raises(NormalizedApiError) as caught:
        session.invoke(
            "make_order",
            VENUE,
            request,
            dispatch,
            pre_dispatch=lambda _context: request_id_calls.append(True),
        )

    assert caught.value.code == "ctp_order_identity_binding_missing_or_mismatch"
    assert (journal.read_bytes() if journal.exists() else None) == journal_before
    assert journal_calls == []
    assert write_calls == []
    assert request_id_calls == []
    assert queue_calls == []
    assert native_calls == []
    assert session.reserved_ids == reserved_before


def test_mismatched_ctp_mirror_runtime_fails_before_request_id_or_native(tmp_path):
    session = session_for(tmp_path / "sdk-execution.jsonl")
    session._arm_managed = True
    seed_local_mirror_fixture(session)
    request = managed_order_request(
        order_identity_binding(runtime_order_id="bt-managed-v1:" + "f" * 64)
    )
    write_calls = []
    request_id_calls = []
    native_calls = []
    session._journal = lambda *_args, **_kwargs: pytest.fail("unexpected journal write")
    session._require_arm_scope = lambda *args, **kwargs: None
    session.require_write = lambda *args, **kwargs: write_calls.append((args, kwargs))

    with pytest.raises(NormalizedApiError) as caught:
        session.invoke(
            "make_order",
            VENUE,
            request,
            lambda: native_calls.append(True),
            pre_dispatch=lambda _context: request_id_calls.append(True),
        )

    assert caught.value.code == "ctp_order_identity_binding_missing_or_mismatch"
    assert write_calls == []
    assert request_id_calls == []
    assert native_calls == []


def test_exact_ctp_mirror_does_not_bypass_existing_write_gate(tmp_path):
    session = session_for(tmp_path / "sdk-execution.jsonl")
    session._arm_managed = True
    seed_local_mirror_fixture(session)
    request = managed_order_request(order_identity_binding())
    write_calls = []
    request_id_calls = []
    native_calls = []
    session._require_arm_scope = lambda *args, **kwargs: None

    def keep_write_gate_closed(*args, **kwargs):
        write_calls.append((args, kwargs))
        raise NormalizedApiError("make_order", "existing_write_gate_closed", definite_reject=True)

    session.require_write = keep_write_gate_closed

    with pytest.raises(NormalizedApiError) as caught:
        session.invoke(
            "make_order",
            VENUE,
            request,
            lambda: native_calls.append(True),
            pre_dispatch=lambda _context: request_id_calls.append(True),
        )

    assert caught.value.code == "existing_write_gate_closed"
    assert len(write_calls) == 1
    assert request_id_calls == []
    assert native_calls == []


def test_local_mirror_fixture_does_not_enable_allocator_or_dispatch(tmp_path):
    journal = tmp_path / "sdk-execution.jsonl"
    session = session_for(journal)
    session._arm_managed = True
    mirrored = seed_local_mirror_fixture(session)
    journal_before = journal.read_bytes() if journal.exists() else None
    reservations_before = set(session.reserved_ids)

    with pytest.raises(NormalizedApiError) as allocator_error:
        session.new_client_order_id(VENUE)

    assert allocator_error.value.code == "ctp_order_identity_reservation_required"

    session._require_arm_scope = lambda *args, **kwargs: None
    native_calls = []
    request_id_calls = []

    def dispatch():
        native_calls.append(True)
        return None

    request = OrderRequest(
        symbol="IF2610",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        price=Decimal("1"),
        account_id=ACCOUNT,
        client_order_id=mirrored.order_ref,
    )
    with pytest.raises(NormalizedApiError) as dispatch_error:
        session.invoke(
            "make_order",
            VENUE,
            request,
            dispatch,
            pre_dispatch=lambda _context: request_id_calls.append(True),
        )

    assert dispatch_error.value.code == "execution_identity_missing_or_mismatch"
    assert (journal.read_bytes() if journal.exists() else None) == journal_before
    assert session.reserved_ids == reservations_before
    assert native_calls == []
    assert request_id_calls == []


def ctp_cancel_command(command_id="cancel.command.1", **command_changes):
    native_request_id = 731
    native_action_ref = 997
    payload = {
        "InstrumentID": "IF2610",
        "OrderRef": "000000000137",
        "ExchangeID": "SHFE",
        "OrderSysID": "sys-order-17",
        "FrontID": 4,
        "SessionID": 91,
        "ActionFlag": "0",
        "RequestID": native_request_id,
    }
    native_payload = {**payload, "OrderActionRef": native_action_ref}
    session_binding = {
        "session_identity": "opaque-session-v1",
        "session_generation_id": "test-session-generation",
        "dispatch_front_id": 4,
        "dispatch_session_id": 91,
    }
    request_digest = digest(payload)
    session_digest = digest(session_binding)
    correlation = CtpDispatchCorrelationKey(
        version=2,
        account_key=ACCOUNT_KEY,
        scope_key=SCOPE_KEY,
        trading_day=TRADING_DAY,
        operation="CANCEL",
        command_id=command_id,
        request_payload_sha256=request_digest,
        reservation_managed_intent_id=MANAGED_INTENT,
        managed_action_id="cancel.action.1",
        runtime_order_id=RUNTIME_ORDER_ID,
        order_ref="000000000137",
        cancel_target_exchange_id="SHFE",
        cancel_target_order_sys_id="sys-order-17",
        cancel_target_front_id=4,
        cancel_target_session_id=91,
        approval_use_id="approval-use-1",
        approval_digest="f" * 64,
        session_binding_sha256=session_digest,
        session_generation_id="test-session-generation",
        dispatch_front_id=4,
        dispatch_session_id=91,
        native_request_id=native_request_id,
        native_action_ref=native_action_ref,
        native_request_payload_sha256=digest(native_payload),
    )
    values = {
        "account_key": ACCOUNT_KEY,
        "scope_key": SCOPE_KEY,
        "trading_day": TRADING_DAY,
        "operation": "CANCEL",
        "command_id": command_id,
        "request_payload": payload,
        "request_payload_sha256": request_digest,
        "native_request_payload": native_payload,
        "native_request_payload_sha256": digest(native_payload),
        "reservation_managed_intent_id": MANAGED_INTENT,
        "order_ref": None,
        "cancel_target_order_ref": "000000000137",
        "cancel_target_exchange_id": "SHFE",
        "cancel_target_order_sys_id": "sys-order-17",
        "cancel_target_front_id": 4,
        "cancel_target_session_id": 91,
        "approval_use_id": "approval-use-1",
        "approval_digest": "f" * 64,
        "session_binding": session_binding,
        "session_binding_sha256": session_digest,
        "status": "READY",
        "created_at_ns": 1780000000000000100,
        "correlation_key": correlation,
    }
    values.update(command_changes)
    command = CtpDispatchCommand(**values)
    action = CtpCancelActionProjection(
        managed_action_id=correlation.managed_action_id,
        action_state=None,
        terminal=None,
        source_kind=None,
        updated_at_ns=None,
        target_order=CtpTargetOrderProjection(
            managed_intent_id=correlation.reservation_managed_intent_id,
            runtime_order_id=correlation.runtime_order_id,
            order_ref=correlation.order_ref,
            exchange_id=correlation.cancel_target_exchange_id,
            order_sys_id=correlation.cancel_target_order_sys_id,
            front_id=correlation.cancel_target_front_id,
            session_id=correlation.cancel_target_session_id,
            order_state=CtpProjectedOrderState(),
        ),
    )
    return command, CtpDispatchProjection(command_id, "CANCEL", "READY", action)


def cancel_request(command_id="cancel.command.1", **changes):
    values = {
        "symbol": "IF2610",
        "account_id": ACCOUNT,
        "order_id": "sys-order-17",
        "client_order_id": "000000000137",
        "idempotency_key": command_id,
        "exchange_id": "SHFE",
        "front_id": 4,
        "session_id": 91,
        "order_ref": "000000000137",
    }
    values.update(changes)
    return CancelOrderRequest(**values)


def install_fake_i9_cancel_contract(monkeypatch):
    monkeypatch.setattr(
        execution_session_module,
        "_installed_i9_ctp_order_identity_types",
        lambda: (ExecutionScope, ReadOnlyAuthority, CtpOrderIdentityReservation),
    )
    monkeypatch.setattr(
        execution_session_module,
        "_installed_i9_ctp_dispatch_command_types",
        lambda: (
            CtpDispatchCommand,
            CtpDispatchCorrelationKey,
            CtpDispatchProjection,
            CtpCancelActionProjection,
            CtpTargetOrderProjection,
        ),
    )


def seed_sdk_order_mirror(session, authority):
    session._arm_managed = True
    session._journal = lambda *_args, **_kwargs: None
    return session.consume_ctp_order_identity_reservation(
        VENUE,
        scope=scope(),
        identity_store=authority,
        managed_intent_id=MANAGED_INTENT,
        runtime_order_id=RUNTIME_ORDER_ID,
    )


def test_i9_cancel_readback_echo_is_exact_and_never_authorizes(tmp_path, monkeypatch):
    install_fake_i9_cancel_contract(monkeypatch)
    command, projection = ctp_cancel_command()
    authority = ReadOnlyAuthority(reservation(), command=command, projection=projection)
    session = session_for(tmp_path / "sdk-execution.jsonl")
    seed_sdk_order_mirror(session, authority)
    journal_rows = []
    session._journal = lambda *args, **kwargs: journal_rows.append((args, kwargs))

    binding = session.consume_ctp_cancel_dispatch_command(
        VENUE,
        scope=scope(),
        identity_store=authority,
        command_id=command.command_id,
        request=cancel_request(),
    )

    assert type(binding) is CtpCancelIdentityBinding
    assert binding.dispatch_authorized is False
    assert binding.managed_intent_id == MANAGED_INTENT
    assert binding.managed_action_id == "cancel.action.1"
    assert binding.native_request_id == 731
    assert binding.native_action_ref == 997
    assert binding.native_action_ref != binding.native_request_id
    assert binding.native_request_payload_sha256 == command.native_request_payload_sha256
    assert binding.cancel_target_order_sys_id == "sys-order-17"
    assert session.cancel_calls == 0
    assert authority.command_reads == authority.projection_reads == 1
    assert authority.native_calls == authority.queue_calls == 0
    assert journal_rows == []

    bound_request = replace(cancel_request(), ctp_cancel_identity=binding)
    restored = CancelOrderRequest.from_dict(json.loads(json.dumps(bound_request.to_dict())))
    assert restored == bound_request
    assert type(restored.ctp_cancel_identity.native_request_id) is int
    assert type(restored.front_id) is int
    assert restored.ctp_cancel_identity.dispatch_authorized is False

    bad_mapping = binding.to_dict()
    bad_mapping["dispatch_front_id"] = True
    with pytest.raises(ValueError, match="dispatch_front_id"):
        CtpCancelIdentityBinding.from_dict(bad_mapping)
    with pytest.raises(ValueError, match="front_id"):
        replace(bound_request, front_id=True)
    empty_legacy_key = replace(cancel_request(idempotency_key=""), ctp_cancel_identity=binding)
    assert empty_legacy_key.ctp_cancel_identity is binding
    for invalid_key in (0, False, [], {}, "different-command"):
        with pytest.raises(ValueError, match="idempotency_key"):
            replace(cancel_request(idempotency_key=invalid_key), ctp_cancel_identity=binding)


def test_i9_cancel_readback_rejects_missing_typed_projection_before_echo(
    tmp_path, monkeypatch
):
    install_fake_i9_cancel_contract(monkeypatch)
    command, projection = ctp_cancel_command()
    authority = ReadOnlyAuthority(
        reservation(), command=command, projection=replace(projection, cancel_action=None)
    )
    session = session_for(tmp_path / "sdk-execution.jsonl")
    seed_sdk_order_mirror(session, authority)

    with pytest.raises(NormalizedApiError) as caught:
        session.consume_ctp_cancel_dispatch_command(
            VENUE,
            scope=scope(),
            identity_store=authority,
            command_id=command.command_id,
            request=cancel_request(),
        )

    assert caught.value.code == "ctp_cancel_identity_authority_read_or_binding_invalid"
    assert session._ctp_cancel_identity_mirrors == {}
    assert session.cancel_calls == 0
    assert authority.native_calls == authority.queue_calls == 0


@pytest.mark.parametrize(
    "change",
    [
        lambda command: replace(command, cancel_target_order_sys_id="other-sys-id"),
        lambda command: replace(
            command,
            correlation_key=replace(
                command.correlation_key,
                managed_action_id=MANAGED_INTENT,
            ),
        ),
    ],
    ids=["wrong-target", "wrong-action"],
)
def test_i9_cancel_readback_rejects_target_and_action_spoof_before_echo(
    tmp_path, monkeypatch, change
):
    install_fake_i9_cancel_contract(monkeypatch)
    command, projection = ctp_cancel_command()
    authority = ReadOnlyAuthority(reservation(), command=change(command), projection=projection)
    session = session_for(tmp_path / "sdk-execution.jsonl")
    seed_sdk_order_mirror(session, authority)

    with pytest.raises(NormalizedApiError) as caught:
        session.consume_ctp_cancel_dispatch_command(
            VENUE,
            scope=scope(),
            identity_store=authority,
            command_id=command.command_id,
            request=cancel_request(),
        )

    assert caught.value.code == "ctp_cancel_identity_authority_read_or_binding_invalid"
    assert session._ctp_cancel_identity_mirrors == {}
    assert authority.native_calls == authority.queue_calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("order_id", "different-sys-id"),
        ("client_order_id", "000000000999"),
        ("order_ref", "000000000999"),
        ("exchange_id", "DCE"),
        ("front_id", 5),
        ("session_id", 92),
    ],
    ids=["order-id", "client-order-id", "order-ref", "exchange", "front", "session"],
)
def test_i9_cancel_readback_rejects_request_scalar_conflict_and_missing_order_mirror(
    tmp_path, monkeypatch, field, value
):
    install_fake_i9_cancel_contract(monkeypatch)
    command, projection = ctp_cancel_command()
    authority = ReadOnlyAuthority(reservation(), command=command, projection=projection)
    session = session_for(tmp_path / "sdk-execution.jsonl")

    with pytest.raises(NormalizedApiError) as missing_mirror:
        session.consume_ctp_cancel_dispatch_command(
            VENUE,
            scope=scope(),
            identity_store=authority,
            command_id=command.command_id,
            request=cancel_request(),
        )
    assert missing_mirror.value.code == "ctp_cancel_identity_order_mirror_missing"
    assert session._ctp_cancel_identity_mirrors == {}

    seed_sdk_order_mirror(session, authority)
    with pytest.raises(NormalizedApiError) as target_conflict:
        session.consume_ctp_cancel_dispatch_command(
            VENUE,
            scope=scope(),
            identity_store=authority,
            command_id=command.command_id,
            request=cancel_request(**{field: value}),
        )
    assert target_conflict.value.code == "ctp_cancel_identity_authority_read_or_binding_invalid"
    assert session._ctp_cancel_identity_mirrors == {}
    assert session.cancel_calls == 0


def test_i9_cancel_readback_rejects_absent_store_and_caller_binding(tmp_path, monkeypatch):
    install_fake_i9_cancel_contract(monkeypatch)
    command, projection = ctp_cancel_command()
    authority = ReadOnlyAuthority(reservation(), command=command, projection=projection)
    session = session_for(tmp_path / "sdk-execution.jsonl")
    seed_sdk_order_mirror(session, authority)

    with pytest.raises(NormalizedApiError) as no_store:
        session.consume_ctp_cancel_dispatch_command(
            VENUE,
            scope=scope(),
            identity_store=object(),
            command_id=command.command_id,
            request=cancel_request(),
        )
    assert no_store.value.code == "ctp_cancel_identity_read_port_unavailable"

    forged = CtpCancelIdentityBinding(
        version=1,
        environment="simulation",
        account_id=ACCOUNT,
        instrument_id="IF2610",
        account_key=ACCOUNT_KEY,
        trading_day=TRADING_DAY,
        scope_key=SCOPE_KEY,
        managed_intent_id=MANAGED_INTENT,
        runtime_order_id=RUNTIME_ORDER_ID,
        order_ref="000000000137",
        command_id=command.command_id,
        request_payload_sha256=command.request_payload_sha256,
        managed_action_id="cancel.action.1",
        approval_use_id="approval-use-1",
        approval_digest="f" * 64,
        session_binding_sha256=command.session_binding_sha256,
        session_generation_id="test-session-generation",
        dispatch_front_id=4,
        dispatch_session_id=91,
        native_request_id=731,
        native_action_ref="731",
        cancel_target_order_ref="000000000137",
        cancel_target_exchange_id="SHFE",
        cancel_target_order_sys_id="sys-order-17",
        cancel_target_front_id=4,
        cancel_target_session_id=91,
    )
    with pytest.raises(NormalizedApiError) as caller_identity:
        session.consume_ctp_cancel_dispatch_command(
            VENUE,
            scope=scope(),
            identity_store=authority,
            command_id=command.command_id,
            request=replace(cancel_request(), ctp_cancel_identity=forged),
        )
    assert caller_identity.value.code == "ctp_cancel_identity_request_invalid_or_caller_supplied"
    assert authority.native_calls == authority.queue_calls == 0


def test_cancel_public_sync_async_paths_reject_echo_before_dispatch(tmp_path, monkeypatch):
    install_fake_i9_cancel_contract(monkeypatch)
    command, projection = ctp_cancel_command()
    authority = ReadOnlyAuthority(reservation(), command=command, projection=projection)
    session = session_for(tmp_path / "sdk-execution.jsonl")
    seed_sdk_order_mirror(session, authority)
    binding = session.consume_ctp_cancel_dispatch_command(
        VENUE,
        scope=scope(),
        identity_store=authority,
        command_id=command.command_id,
        request=cancel_request(),
    )
    request = replace(cancel_request(), ctp_cancel_identity=binding)
    api = object.__new__(BtApi)
    api._execution_session = session

    with pytest.raises(NormalizedApiError) as sync_result:
        api.cancel_order(VENUE, request, normalized=True)
    assert sync_result.value.code == "ctp_cancel_dispatch_handoff_unavailable"

    with pytest.raises(NormalizedApiError) as async_result:
        asyncio.run(api.async_cancel_order(VENUE, request, normalized=True))
    assert async_result.value.code == "ctp_cancel_dispatch_handoff_unavailable"

    no_session_api = object.__new__(BtApi)
    no_session_api._execution_session = None
    with pytest.raises(NormalizedApiError) as no_session_sync:
        no_session_api.cancel_order(VENUE, request)
    assert no_session_sync.value.code == "ctp_cancel_dispatch_handoff_unavailable"
    with pytest.raises(NormalizedApiError) as no_session_async:
        asyncio.run(no_session_api.async_cancel_order(VENUE, request))
    assert no_session_async.value.code == "ctp_cancel_dispatch_handoff_unavailable"

    with pytest.raises(NormalizedApiError) as wrong_venue_sync:
        no_session_api.cancel_order("SIM___SPOT", "IF2610", request=request)
    assert wrong_venue_sync.value.code == "ctp_cancel_identity_venue_mismatch"
    with pytest.raises(NormalizedApiError) as wrong_venue_async:
        asyncio.run(no_session_api.async_cancel_order("SIM___SPOT", request=request))
    assert wrong_venue_async.value.code == "ctp_cancel_identity_venue_mismatch"

    forwarder = ZmqBtApiBackend(
        ForwardingConfig(
            command_endpoint="inproc://commands",
            market_endpoint="inproc://market",
            private_endpoint="inproc://private",
            account_id="acct-1",
            strategy_id="strategy-1",
        )
    )
    forwarder._client = object()
    with pytest.raises(CapabilityNotSupportedError) as forwarded:
        forwarder.cancel_order("SIM___SPOT", request)
    assert forwarded.value.definite_reject is True
    assert forwarder._clients == {}
    assert authority.native_calls == authority.queue_calls == 0
    assert session.cancel_calls == 0
