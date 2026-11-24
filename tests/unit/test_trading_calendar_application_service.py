"""Registry-backed application reads over temporary synthetic calendars only."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from datetime import date, timedelta
from pathlib import Path
from threading import Event, Thread

import pytest
from bt_api_base.service_protocol import (
    WIRE_PROTOCOL_VERSION,
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)

from bt_api_py import _calendar_fdops
from bt_api_py import trading_calendar_review_bundle as review_bundle
from bt_api_py import trading_calendar_store as _calendar_store_module
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    ServiceCallContext,
    ServiceCapability,
    ServiceRegistry,
)
from bt_api_py.trading_calendar_application_client import TradingCalendarApplicationClient
from bt_api_py.trading_calendar_application_service import (
    CALENDAR_APPLICATION_OPERATIONS,
    CALENDAR_APPLICATION_PORT,
    CALENDAR_CANDIDATE_READ_OPERATION,
    CALENDAR_CURRENT_READ_OPERATION,
    SIMULATION_ONLY,
    CalendarApplicationGrant,
    CalendarDirectoryBinding,
    TradingCalendarApplicationService,
    assemble_trading_calendar_application_service,
)
from bt_api_py.trading_calendar_preview_reports import TRUSTED_LOCAL_POSIX
from bt_api_py.trading_calendar_store import (
    CalendarSnapshot,
    ReceiptConsistentCurrent,
    TradingCalendarStore,
)
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_trading_calendar_binding import content as current_content
from tests.unit.test_trading_calendar_binding import publish as publish_current

_APP_PRINCIPAL = "calendar-web-service"
_APP_ACTOR = "calendar-web-runtime"
_APP_SCOPE = WireScope("simulation-calendar")
_INSTANCE = "calendar-application"
_SCOPE_REF = "calendar-web-read"
_VENUES = ("SHFE", "INE", "DCE", "CZCE", "CFFEX", "GFEX")
_START = date(2026, 1, 5)
_SOURCE_MARKER = "fictional-source-text-must-not-be-returned"
_CONTRACT_MARKER = "fictional-contract-marker-2701"


def _candidate(days: int = 2) -> dict:
    end = _START + timedelta(days=days - 1)
    shards = []
    for venue in _VENUES:
        layers = {
            "sources": [{"id": "synthetic", "description": "fictional"}],
            "trading_days": [
                {
                    "venue": venue,
                    "day": (_START + timedelta(days=index)).isoformat(),
                    "status": "closed",
                    "source_ids": ["synthetic"],
                }
                for index in range(days)
            ],
            "trading_hours": [],
            "exceptions": [],
        }
        requirements = []
        for kind in ("future", "option"):
            product = "p" + kind
            contract = _CONTRACT_MARKER if venue == "SHFE" and kind == "future" else venue.lower() + kind + "2701"
            layers["trading_hours"].append(
                {
                    "venue": venue,
                    "kind": kind,
                    "product": product,
                    "contracts": [contract],
                    "valid_from": _START.isoformat(),
                    "valid_to": end.isoformat(),
                    "day_windows": [
                        {
                            "id": "day",
                            "phase": "continuous",
                            "start": "09:00",
                            "end": "10:00",
                            "end_day_offset": 0,
                        }
                    ],
                    "night_windows": [],
                    "night_mappings": [],
                    "windows_complete": True,
                    "exceptions_complete": True,
                    "source_ids": ["synthetic"],
                }
            )
            requirements.append(
                {
                    "kind": kind,
                    "product": product,
                    "contract": contract,
                    "start_day": _START.isoformat(),
                    "end_day": end.isoformat(),
                }
            )
        shards.append(
            {
                "venue": venue,
                "coverage_start": _START.isoformat(),
                "coverage_end": end.isoformat(),
                "layers": layers,
                "requirements": requirements,
                "unresolved": [],
            }
        )
    return {"shards": shards, "source_texts": {"synthetic": _SOURCE_MARKER}}


def _tree_pin(root: Path) -> dict[str, tuple[int, str]]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            raw = path.read_bytes()
            result[path.relative_to(root).as_posix()] = (len(raw), hashlib.sha256(raw).hexdigest())
        elif path.is_dir():
            result[path.relative_to(root).as_posix() + "/"] = (0, "DIRECTORY")
    return result


class _ApplicationFixture:
    def __init__(self, *, tmp_path: Path, service: TradingCalendarApplicationService, registry: ServiceRegistry):
        self.tmp_path = tmp_path
        self.service = service
        self.registry = registry
        self.registration = self._register()
        assert self.registration["ok"] is True

    def _request(self, operation: str, payload: dict, *, control: bool = False, identity=None, scope=None):
        return WireEnvelope(
            header=WireHeader(
                protocol_version=WIRE_PROTOCOL_VERSION,
                kind=WireMessageKind.REQUEST,
                message_id=f"calendar-test-{_next_message_id()}-{operation}",
                correlation_id=None,
                identity=identity or WireIdentity(WireRole.APPLICATION_CLIENT, _APP_PRINCIPAL, _APP_ACTOR),
                scope=scope or _APP_SCOPE,
                operation=operation,
                schema_id="sdk.service.control" if control else "sdk.service.call",
                schema_version=1,
                sent_at_utc="2026-11-24T00:00:00Z",
            ),
            payload=payload,
        )

    def _register(self):
        return self.registry.handle(
            _APP_PRINCIPAL,
            self._request(
                "service.register",
                {
                    "registration_key": "calendar-registration-key",
                    "client_session_id": "calendar-client-session",
                    "instance_id": _INSTANCE,
                    "port": CALENDAR_APPLICATION_PORT,
                    "scope_ref": _SCOPE_REF,
                    "operations": tuple(sorted(CALENDAR_APPLICATION_OPERATIONS)),
                },
                control=True,
            ),
        )

    def call(self, operation: str, arguments: dict, *, identity=None, scope=None):
        result = self.registration["result"]
        return self.registry.handle(
            _APP_PRINCIPAL,
            self._request(
                operation,
                {
                    "registration_id": result["registration_id"],
                    "service_epoch": result["service_epoch"],
                    "instance_id": _INSTANCE,
                    "arguments": arguments,
                },
                identity=identity,
                scope=scope,
            ),
        )


_MESSAGE_ID = 0


def _next_message_id() -> int:
    global _MESSAGE_ID
    _MESSAGE_ID += 1
    return _MESSAGE_ID


def _make_application(tmp_path: Path, store_roots: tuple[tuple[str, Path], ...], *, grant_directory="calendar-a"):
    directories = tuple(
        CalendarDirectoryBinding(directory_ref, TradingCalendarStore(root))
        for directory_ref, root in store_roots
    )
    grant = CalendarApplicationGrant(
        principal_ref=_APP_PRINCIPAL,
        actor_ref=_APP_ACTOR,
        execution_domain_ref=_APP_SCOPE.execution_domain_ref,
        directory_ref=grant_directory,
        operations=CALENDAR_APPLICATION_OPERATIONS,
    )
    registry = _make_registry(tmp_path)
    service = assemble_trading_calendar_application_service(
        registry=registry,
        instance_id=_INSTANCE,
        directories=directories,
        grants=frozenset({grant}),
    )
    return service, registry


def _make_registry(tmp_path: Path, *, include_unrelated_port: bool = False) -> ServiceRegistry:
    capability = ServiceCapability(
        _INSTANCE,
        CALENDAR_APPLICATION_PORT,
        CALENDAR_APPLICATION_OPERATIONS,
        write_operations=frozenset(),
    )
    resource = ResourceGrant(
        _INSTANCE,
        CALENDAR_APPLICATION_PORT,
        _SCOPE_REF,
        _APP_SCOPE,
        CALENDAR_APPLICATION_OPERATIONS,
    )
    capabilities = [capability]
    resources = [resource]
    if include_unrelated_port:
        other_operations = frozenset({"health.read.v1"})
        capabilities.append(
            ServiceCapability(
                _INSTANCE,
                "service.health",
                other_operations,
                write_operations=frozenset(),
            )
        )
        resources.append(
            ResourceGrant(
                _INSTANCE,
                "service.health",
                "calendar-health-read",
                _APP_SCOPE,
                other_operations,
            )
        )
    registry_root = tmp_path / "registry"
    registry_root.mkdir(exist_ok=True, parents=True)
    registry = ServiceRegistry(
        registry_root / "service.sqlite",
        service_id="calendar-application-fixture",
        capabilities=tuple(capabilities),
        grants={_APP_PRINCIPAL: (PrincipalGrant(
            WireIdentity(WireRole.APPLICATION_CLIENT, _APP_PRINCIPAL, _APP_ACTOR), tuple(resources)
        ),)},
        policy_version="test-policy-v1",
        requirements_version="test-requirements-v1",
        lease_seconds=120,
        clock=lambda: 100.0,
    )
    return registry


def test_assembly_keeps_other_read_only_ports_on_same_instance(tmp_path):
    calendar_root = tmp_path / "calendar-a"
    calendar_root.mkdir()
    registry = _make_registry(tmp_path, include_unrelated_port=True)
    service = assemble_trading_calendar_application_service(
        registry=registry,
        instance_id=_INSTANCE,
        directories=(CalendarDirectoryBinding("calendar-a", TradingCalendarStore(calendar_root)),),
        grants=frozenset(
            {
                CalendarApplicationGrant(
                    principal_ref=_APP_PRINCIPAL,
                    actor_ref=_APP_ACTOR,
                    execution_domain_ref=_APP_SCOPE.execution_domain_ref,
                    directory_ref="calendar-a",
                    operations=CALENDAR_APPLICATION_OPERATIONS,
                )
            }
        ),
    )
    fixture = _ApplicationFixture(tmp_path=tmp_path, service=service, registry=registry)

    response = fixture.call(CALENDAR_CURRENT_READ_OPERATION, {"directory_ref": "calendar-a"})

    assert response["ok"] is True
    assert response["result"]["status"] == "UNKNOWN"
    assert response["result"]["reason"] == "CURRENT_UNAVAILABLE_OR_INVALID"
    registry.close()


@contextlib.contextmanager
def _temporary_fixture_lock(store: TradingCalendarStore, *, check_migration: bool = False):
    """Fixture-only unlocked writer setup for a temp tree; never for app reads."""
    store._directory(store.root, create=True)
    lock_fd = _calendar_fdops.open(
        store.root / ".lock",
        os.O_RDWR | os.O_CREAT | _calendar_fdops.O_NOFOLLOW,
        0o600,
    )
    _calendar_fdops.close(lock_fd)
    store._layout(create=True)
    yield


def _seed_temp_current(tmp_path: Path, monkeypatch):
    """Write one fully synthetic store through owner code under temp-only fixture locks."""
    with monkeypatch.context() as patch:
        patch.setattr(_calendar_store_module.TradingCalendarStore, "_writer_lock", _temporary_fixture_lock)
        patch.setattr(_calendar_store_module.TradingCalendarStore, "_readonly_lock", _temporary_fixture_lock)
        fixture_store = FixtureCalendarStore(tmp_path)
        return publish_current(fixture_store, "synthetic-v1", current_content())


def test_candidate_owner_read_is_bound_redacted_and_non_mutating(tmp_path, monkeypatch):
    calendar_root = tmp_path / "calendar-a"
    calendar_root.mkdir()
    candidate_id = "a" * 32
    retained = review_bundle.build_once(
        root=str(calendar_root),
        candidate_id=candidate_id,
        input_data=_candidate(),
        filesystem=TRUSTED_LOCAL_POSIX,
        emit=lambda _event: None,
    )
    assert retained["status"] == "COMMITTED"
    service, registry = _make_application(tmp_path, (("calendar-a", calendar_root),))
    fixture = _ApplicationFixture(tmp_path=tmp_path, service=service, registry=registry)
    before = _tree_pin(calendar_root)
    owner_calls = []
    original_show = review_bundle.show

    def observed_show(**kwargs):
        owner_calls.append((Path(kwargs["root"]), kwargs["candidate_id"], kwargs["filesystem"]))
        return original_show(**kwargs)

    monkeypatch.setattr(review_bundle, "show", observed_show)
    response = fixture.call(
        CALENDAR_CANDIDATE_READ_OPERATION,
        {"directory_ref": "calendar-a", "candidate_id": candidate_id},
    )

    assert response["ok"] is True
    assert owner_calls == [(calendar_root, candidate_id, TRUSTED_LOCAL_POSIX)]
    result = response["result"]
    assert result["status"] == "CANDIDATE_VISIBLE_UNAUTHENTICATED"
    assert result["mode"] == SIMULATION_ONLY
    assert result["review_status"] == "STRUCTURALLY_READY_UNVERIFIED"
    assert result["declared_scope_count"] == 12
    assert {row["venue"] for row in result["declared_ranges"]} == set(_VENUES)
    assert result["principal_binding"] == "TRUSTED_REGISTRY_CONTEXT"
    assert result["actor_authentication"] == "NOT_PROVEN"
    assert result["source_authentication"] == "NOT_PROVEN"
    assert result["coverage_authentication"] == "NOT_PROVEN"
    assert result["publication"] == "NOT_AUTHORIZED"
    encoded = json.dumps(result, sort_keys=True)
    assert _SOURCE_MARKER not in encoded
    assert _CONTRACT_MARKER not in encoded
    assert str(calendar_root) not in encoded
    assert not (calendar_root / "CURRENT.json").exists()
    assert _tree_pin(calendar_root) == before

    registry.close()


def test_caller_cannot_choose_root_actor_or_ungranted_directory(tmp_path, monkeypatch):
    first_root, second_root = tmp_path / "calendar-a", tmp_path / "calendar-b"
    first_root.mkdir()
    second_root.mkdir()
    candidate_id = "b" * 32
    assert review_bundle.build_once(
        root=str(second_root),
        candidate_id=candidate_id,
        input_data=_candidate(),
        filesystem=TRUSTED_LOCAL_POSIX,
        emit=lambda _event: None,
    )["status"] == "COMMITTED"
    service, registry = _make_application(
        tmp_path, (("calendar-a", first_root), ("calendar-b", second_root))
    )
    fixture = _ApplicationFixture(tmp_path=tmp_path, service=service, registry=registry)
    calls = []
    original_show = review_bundle.show

    def observed_show(**kwargs):
        calls.append(kwargs["root"])
        return original_show(**kwargs)

    monkeypatch.setattr(review_bundle, "show", observed_show)
    unauthorized = fixture.call(
        CALENDAR_CANDIDATE_READ_OPERATION,
        {"directory_ref": "calendar-b", "candidate_id": candidate_id},
    )
    assert unauthorized["ok"] is True
    assert unauthorized["result"]["status"] == "UNKNOWN"
    assert unauthorized["result"]["reason"] == "UNAVAILABLE_OR_UNAUTHORIZED"
    assert calls == []

    malformed = fixture.call(
        CALENDAR_CANDIDATE_READ_OPERATION,
        {
            "directory_ref": "calendar-a",
            "candidate_id": candidate_id,
            "calendar_root": str(second_root),
            "actor_ref": "attacker",
        },
    )
    assert malformed["result"]["status"] == "UNKNOWN"
    assert malformed["result"]["reason"] == "INVALID_REQUEST"
    assert calls == []

    forged_context = ServiceCallContext(
        identity=WireIdentity(WireRole.APPLICATION_CLIENT, _APP_PRINCIPAL, _APP_ACTOR),
        scope=_APP_SCOPE,
        registration_id="forged-registration",
        principal_ref=_APP_PRINCIPAL,
        result_fits=lambda _result: True,
    )
    with pytest.raises(ValueError, match="REGISTRY_CONTEXT_REQUIRED"):
        service.handle(
            CALENDAR_CANDIDATE_READ_OPERATION,
            context=forged_context,
            arguments={"directory_ref": "calendar-a", "candidate_id": candidate_id},
        )
    assert calls == []
    registry.close()


def test_registry_rejects_wrong_identity_scope_and_operation_before_owner_read(tmp_path, monkeypatch):
    calendar_root = tmp_path / "calendar-a"
    calendar_root.mkdir()
    _service, registry = _make_application(tmp_path, (("calendar-a", calendar_root),))
    fixture = _ApplicationFixture(tmp_path=tmp_path, service=_service, registry=registry)
    owner_calls = []

    def forbidden_owner_read(**kwargs):
        owner_calls.append(kwargs)
        raise AssertionError("unauthorized call reached the owner")

    monkeypatch.setattr(review_bundle, "show", forbidden_owner_read)
    args = {"directory_ref": "calendar-a", "candidate_id": "c" * 32}
    wrong_identity = fixture.call(
        CALENDAR_CANDIDATE_READ_OPERATION,
        args,
        identity=WireIdentity(WireRole.APPLICATION_CLIENT, _APP_PRINCIPAL, "spoofed-actor"),
    )
    wrong_scope = fixture.call(
        CALENDAR_CANDIDATE_READ_OPERATION,
        args,
        scope=WireScope("ungranted-simulation-domain"),
    )
    wrong_operation = fixture.call("calendar.application.publish.v1", args)

    assert wrong_identity["ok"] is False and wrong_identity["code"] == "DENIED"
    assert wrong_scope["ok"] is False and wrong_scope["code"] == "SCOPE_DENIED"
    assert wrong_operation["ok"] is False and wrong_operation["code"] == "CAPABILITY_UNAVAILABLE"
    assert owner_calls == []
    registry.close()


def test_typed_client_consumes_registered_owner_candidate_read(tmp_path):
    calendar_root = tmp_path / "calendar-a"
    calendar_root.mkdir()
    candidate_id = "f" * 32
    assert review_bundle.build_once(
        root=str(calendar_root),
        candidate_id=candidate_id,
        input_data=_candidate(),
        filesystem=TRUSTED_LOCAL_POSIX,
        emit=lambda _event: None,
    )["status"] == "COMMITTED"
    before = _tree_pin(calendar_root)
    service, registry = _make_application(tmp_path, (("calendar-a", calendar_root),))
    fixture = _ApplicationFixture(tmp_path=tmp_path, service=service, registry=registry)
    dispatcher_calls = []

    def dispatch(operation, arguments, *, timeout_seconds):
        dispatcher_calls.append((operation, dict(arguments), timeout_seconds))
        return fixture.call(operation, dict(arguments))

    client = TradingCalendarApplicationClient(dispatch, directory_ref="calendar-a")
    try:
        result = client.read_candidate(candidate_id)
    finally:
        registry.close()

    assert result.status == "CANDIDATE_VISIBLE_UNAUTHENTICATED"
    assert result.candidate_id == candidate_id
    assert result.mode == SIMULATION_ONLY
    assert result.source_authentication == "NOT_PROVEN"
    assert result.publication == "NOT_AUTHORIZED"
    assert dispatcher_calls == [
        (
            CALENDAR_CANDIDATE_READ_OPERATION,
            {"directory_ref": "calendar-a", "candidate_id": candidate_id},
            1.5,
        )
    ]
    assert _tree_pin(calendar_root) == before


def test_current_owner_missing_read_fails_closed_without_creation(tmp_path, monkeypatch):
    calendar_root = tmp_path / "calendar-a"
    calendar_root.mkdir()
    service, registry = _make_application(tmp_path, (("calendar-a", calendar_root),))
    fixture = _ApplicationFixture(tmp_path=tmp_path, service=service, registry=registry)
    before = _tree_pin(calendar_root)
    owner_calls = []
    original_read = TradingCalendarStore.read_current_with_consistent_receipts

    def observed_read(self, *, budget=None):
        owner_calls.append(self.root)
        return original_read(self, budget=budget)

    monkeypatch.setattr(TradingCalendarStore, "read_current_with_consistent_receipts", observed_read)

    response = fixture.call(CALENDAR_CURRENT_READ_OPERATION, {"directory_ref": "calendar-a"})

    assert response["ok"] is True
    assert owner_calls == [calendar_root]
    result = response["result"]
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "CURRENT_UNAVAILABLE_OR_INVALID"
    assert result["mode"] == SIMULATION_ONLY
    assert result["source_authentication"] == "NOT_PROVEN"
    assert result["publication"] == "NOT_AUTHORIZED"
    assert not (calendar_root / "CURRENT.json").exists()
    assert _tree_pin(calendar_root) == before
    registry.close()


def test_current_success_uses_real_owner_read_and_preserves_temp_tree(tmp_path, monkeypatch):
    calendar_root = tmp_path / "calendar-a"
    calendar_root.mkdir()
    published = _seed_temp_current(calendar_root, monkeypatch)
    assert published.visibility == "COMMITTED"
    expected = TradingCalendarStore(calendar_root).read_current_with_consistent_receipts()
    service, registry = _make_application(tmp_path, (("calendar-a", calendar_root),))
    fixture = _ApplicationFixture(tmp_path=tmp_path, service=service, registry=registry)
    before = _tree_pin(calendar_root)
    owner_calls = []
    original_read = TradingCalendarStore.read_current_with_consistent_receipts

    def observed_real_read(self, *, budget=None):
        owner_calls.append((self.root, budget))
        return original_read(self, budget=budget)

    monkeypatch.setattr(TradingCalendarStore, "read_current_with_consistent_receipts", observed_real_read)
    response = fixture.call(CALENDAR_CURRENT_READ_OPERATION, {"directory_ref": "calendar-a"})

    assert response["ok"] is True
    result = response["result"]
    assert result["status"] == "CURRENT_VISIBLE_UNAUTHENTICATED"
    assert result["content_sha256"] == expected.snapshot.content_sha256
    assert result["receipt_consistency"] == "LOCAL_RECEIPTS_CONSISTENT_UNAUTHENTICATED"
    assert result["mode"] == SIMULATION_ONLY
    assert result["receipt_authentication"] == "NOT_PROVEN"
    assert result["source_authentication"] == "NOT_PROVEN"
    assert result["coverage_authentication"] == "NOT_PROVEN"
    assert result["actor_authentication"] == "NOT_PROVEN"
    assert result["publication"] == "NOT_AUTHORIZED"
    assert "fictional unit-test source" not in json.dumps(result)
    assert len(owner_calls) == 1 and owner_calls[0][0] == calendar_root
    assert owner_calls[0][1].max_total_bytes == 32 * 1024 * 1024
    assert _tree_pin(calendar_root) == before
    registry.close()


def test_current_projection_allowlists_synthetic_typed_owner_result(tmp_path, monkeypatch):
    calendar_root = tmp_path / "calendar-a"
    calendar_root.mkdir()
    service, registry = _make_application(tmp_path, (("calendar-a", calendar_root),))
    fixture = _ApplicationFixture(tmp_path=tmp_path, service=service, registry=registry)
    marker = "synthetic-current-content-must-not-escape"
    snapshot = CalendarSnapshot(
        publication_id="fixture-publication",
        sequence=1,
        version_id="fixture-version",
        content_sha256="a" * 64,
        content={"private_fixture_marker": marker},
        coverage_status="DECLARED_UNVERIFIED",
        publication_sha256="b" * 64,
        integrity_status="STORE_INTEGRITY_ONLY",
        semantics_status="NOT_VERIFIED",
    )
    current = ReceiptConsistentCurrent(snapshot=snapshot)
    observed_roots = []

    def synthetic_owner_read(self, *, budget):
        observed_roots.append(self.root)
        assert budget.max_file_bytes == 8 * 1024 * 1024
        return current

    monkeypatch.setattr(TradingCalendarStore, "read_current_with_consistent_receipts", synthetic_owner_read)
    response = fixture.call(CALENDAR_CURRENT_READ_OPERATION, {"directory_ref": "calendar-a"})

    assert response["ok"] is True
    result = response["result"]
    assert observed_roots == [calendar_root]
    assert result["status"] == "CURRENT_VISIBLE_UNAUTHENTICATED"
    assert result["mode"] == SIMULATION_ONLY
    assert result["content_sha256"] == "a" * 64
    assert result["receipt_consistency"] == "LOCAL_RECEIPTS_CONSISTENT_UNAUTHENTICATED"
    assert result["receipt_authentication"] == "NOT_PROVEN"
    assert result["principal_binding"] == "TRUSTED_REGISTRY_CONTEXT"
    assert result["actor_authentication"] == "NOT_PROVEN"
    assert result["identity_authentication"] == "NOT_PROVEN"
    assert result["runtime_binding"] == "NOT_PROVEN"
    assert result["source_authentication"] == "NOT_PROVEN"
    assert result["coverage_authentication"] == "NOT_PROVEN"
    assert result["publication"] == "NOT_AUTHORIZED"
    assert marker not in json.dumps(result)
    registry.close()


def test_owner_read_concurrency_is_bounded_with_finite_busy_result(tmp_path, monkeypatch):
    calendar_root = tmp_path / "calendar-a"
    calendar_root.mkdir()
    candidate_id = "d" * 32
    assert review_bundle.build_once(
        root=str(calendar_root),
        candidate_id=candidate_id,
        input_data=_candidate(),
        filesystem=TRUSTED_LOCAL_POSIX,
        emit=lambda _event: None,
    )["status"] == "COMMITTED"
    service, first_registry = _make_application(tmp_path, (("calendar-a", calendar_root),))
    first = _ApplicationFixture(tmp_path=tmp_path, service=service, registry=first_registry)
    second_registry = _make_registry(tmp_path / "second")
    service.register_handlers(second_registry, instance_id=_INSTANCE)
    second = _ApplicationFixture(tmp_path=tmp_path, service=service, registry=second_registry)
    entered, release = Event(), Event()
    owner_calls = []
    original_show = review_bundle.show

    def blocked_first_read(**kwargs):
        owner_calls.append(kwargs["candidate_id"])
        entered.set()
        assert release.wait(5)
        return original_show(**kwargs)

    monkeypatch.setattr(review_bundle, "show", blocked_first_read)
    first_response = []
    thread = Thread(
        target=lambda: first_response.append(
            first.call(
                CALENDAR_CANDIDATE_READ_OPERATION,
                {"directory_ref": "calendar-a", "candidate_id": candidate_id},
            )
        )
    )
    thread.start()
    try:
        assert entered.wait(5)
        overloaded = second.call(
            CALENDAR_CANDIDATE_READ_OPERATION,
            {"directory_ref": "calendar-a", "candidate_id": candidate_id},
        )
    finally:
        release.set()
        thread.join(5)

    assert not thread.is_alive()
    assert len(first_response) == 1
    assert first_response[0]["result"]["status"] == "CANDIDATE_VISIBLE_UNAUTHENTICATED"
    assert overloaded["ok"] is True
    assert overloaded["result"]["status"] == "UNKNOWN"
    assert overloaded["result"]["reason"] == "BUSY"
    assert overloaded["result"]["mode"] == SIMULATION_ONLY
    assert owner_calls == [candidate_id]
    first_registry.close()
    second_registry.close()


def test_service_rejects_write_capability_and_unsupported_mode(tmp_path):
    store_root = tmp_path / "calendar"
    store_root.mkdir()
    service, registry = _make_application(tmp_path, (("calendar-a", store_root),))
    registry.close()

    identity = WireIdentity(WireRole.APPLICATION_CLIENT, _APP_PRINCIPAL, _APP_ACTOR)
    scope = _APP_SCOPE
    write_operation = "calendar.application.publish.v1"
    write_capability = ServiceCapability(
        _INSTANCE,
        CALENDAR_APPLICATION_PORT,
        CALENDAR_APPLICATION_OPERATIONS | {write_operation},
        write_operations=frozenset({write_operation}),
    )
    write_resource = ResourceGrant(
        _INSTANCE,
        CALENDAR_APPLICATION_PORT,
        _SCOPE_REF,
        scope,
        CALENDAR_APPLICATION_OPERATIONS | {write_operation},
    )
    write_registry = ServiceRegistry(
        tmp_path / "write-registry.sqlite",
        service_id="calendar-write-capability-fixture",
        capabilities=(write_capability,),
        grants={_APP_PRINCIPAL: (PrincipalGrant(identity, (write_resource,)),)},
        policy_version="test-policy-v1",
        requirements_version="test-requirements-v1",
    )
    with pytest.raises(ValueError, match="EXACT_READ_ONLY_CAPABILITY"):
        service.register_handlers(write_registry, instance_id=_INSTANCE)
    write_registry.close()

    with pytest.raises(ValueError, match="INVALID_CALENDAR_DIRECTORY_BINDING"):
        CalendarDirectoryBinding("calendar-live", TradingCalendarStore(store_root), mode="OFFICIAL")
