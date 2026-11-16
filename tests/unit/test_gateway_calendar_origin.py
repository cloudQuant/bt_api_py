"""Origin declarations remain snapshot-bound and never authenticate a venue."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest
from bt_api_ctp.calendar_snapshot import CalendarSnapshot

from bt_api_py.gateway_calendar_codec import (
    validate_calendar_response,
    validate_calendar_windows_response,
)
from bt_api_py.gateway_calendar_service import (
    CalendarGrant,
    GatewayCalendarService,
    ReceiptCheckedCalendarStore,
)
from bt_api_py.gateway_strategy_calendar import (
    GatewayCalendarQueryResult,
    GatewayCalendarWindowsResult,
)
from bt_api_py.trading_calendar_binding import SimulationCalendarBinding, _covered_source
from bt_api_py.trading_calendar_package_seed import install_package_seed
from bt_api_py.trading_calendar_range_simulation import CalendarRangePolicy
from bt_api_py.trading_calendar_runtime_status import TradingCalendarStatusReader
from bt_api_py.trading_calendar_store import (
    CalendarStoreError,
    TradingCalendarStore,
    calendar_snapshot_digest,
)
from tests.calendar_test_store import FixtureCalendarStore
from tests.unit.test_gateway_calendar_service import CONTEXT
from tests.unit.test_service_registry import _make_registry
from tests.unit.test_trading_calendar_binding import FakeGate, binding, content, decision, publish
from tests.unit.test_trading_calendar_package_seed import seed_store
from tests.unit.test_trading_calendar_runtime_mixed_query import publish as publish_mixed

DAY = date(2026, 1, 5)
SCOPE = {
    "route": "route-a",
    "venue": "CFFEX",
    "kind": "future",
    "product": "SYNTH_CFFEX_FUTURE",
    "contract": "SYNTH_CFFEX_FUTURE_MAIN",
}


def seed_binding(root):
    return SimulationCalendarBinding(
        mode="SIMULATION_ONLY",
        store=ReceiptCheckedCalendarStore(TradingCalendarStore(root)),
        snapshot_factory=CalendarSnapshot,
        gate=FakeGate(),
        route="route-a",
        source_generation="gen-1",
        trading_day=DAY,
        last_sequence=1,
    )


def test_package_seed_service_codec_and_client_preserve_origin(tmp_path):
    root, store = seed_store(tmp_path)
    assert install_package_seed(root).status == "INSTALLED"
    bound = seed_binding(root)
    assert bound.status().data_origin == "SYNTHETIC_MOCK"
    assert _covered_source(store.read_current())
    grant = CalendarGrant("principal", "domain", "account", "strategy", "run", *SCOPE.values())
    policy = CalendarRangePolicy("policy", *SCOPE.values(), 0, 0)
    registry = _make_registry(tmp_path / "registry.sqlite")
    service = GatewayCalendarService(grants=frozenset({grant}), range_policies=frozenset({policy}))
    service.bind_registry(registry)
    service.rebind(bound)
    try:
        day = service.handle(
            "calendar.query.v1",
            context=CONTEXT,
            arguments={"mode": "SIMULATION_ONLY", **SCOPE, "trading_day": DAY.isoformat()},
        )
        window = service.handle(
            "calendar.windows.v1",
            context=CONTEXT,
            arguments={
                "mode": "SIMULATION_ONLY",
                **SCOPE,
                "start_utc": "2026-01-05T01:30:00Z",
                "end_utc": "2026-01-05T02:00:00Z",
            },
        )
        for wire, validate, result in (
            (day, validate_calendar_response, GatewayCalendarQueryResult),
            (window, validate_calendar_windows_response, GatewayCalendarWindowsResult),
        ):
            assert wire["status"] == "COMPLETE"
            wire = validate(json.loads(json.dumps(wire)))
            parsed = result.from_wire(wire)
            assert parsed.binding["data_origin"] == "SYNTHETIC_MOCK"
            assert parsed.binding["calendar_ref"] == bound.status().calendar_ref
            assert (parsed.source_authentication, parsed.trade_authorization) == (
                "NOT_PROVEN",
                "NONE",
            )
            for invalid in ("AUTHENTICATED", None, []):
                bad = deepcopy(wire)
                bad["binding"]["data_origin"] = invalid
                with pytest.raises(ValueError):
                    validate(bad)
    finally:
        registry.close()


def test_direct_current_query_reports_same_snapshot_origin_and_rejects_tampering(
    tmp_path, monkeypatch
):
    root, store = seed_store(tmp_path)
    assert install_package_seed(root).status == "INSTALLED"
    reader = TradingCalendarStatusReader()
    reader.configure({"route-a": str(root)})
    scope = {key: value for key, value in SCOPE.items() if key != "route"}

    def query():
        return reader.query("route-a", mode="SIMULATION_ONLY", trading_day=DAY, **scope)

    observed = query()
    captured = store.read_current()
    assert observed.status == "COMPLETE"
    assert observed.data_origin == "SYNTHETIC_MOCK"
    assert observed.calendar_ref == captured.version_id
    assert observed.version_sha256 == captured.version_sha256
    assert (observed.source_authentication, observed.account_use) == (
        "NOT_PROVEN",
        "NOT_AUTHORIZED",
    )

    original = TradingCalendarStore.read_current_with_consistent_receipts

    def altered(store, **kwargs):
        current = original(store, **kwargs)
        return replace(current, snapshot=replace(current.snapshot, coverage_manifest_bytes=b"{}"))

    monkeypatch.setattr(TradingCalendarStore, "read_current_with_consistent_receipts", altered)
    rejected = query()
    assert rejected.status == "UNKNOWN" and rejected.reason == "CALENDAR_UNAVAILABLE"
    assert rejected.data_origin == "UNSPECIFIED"


def test_missing_mixed_and_captured_switch_use_only_the_bound_snapshot(tmp_path):
    seed_root, store = seed_store(tmp_path)
    assert install_package_seed(seed_root).status == "INSTALLED"
    seed = store.read_current()
    legacy_root = tmp_path / "old"
    legacy = FixtureCalendarStore(legacy_root)
    assert publish(legacy, "old", content()).visibility == "COMMITTED"
    old = legacy.read_current()
    mixed_root = tmp_path / "mixed"
    mixed_root.mkdir()
    publish_mixed(mixed_root, mixed_version=True)
    mixed = TradingCalendarStore(mixed_root).read_current()

    class Switch:
        current = seed

        def read_current(self):
            return self.current

    source = Switch()
    bound = binding(source)
    first = bound.capture_read_view(mode="SIMULATION_ONLY")
    assert first.status().data_origin == "SYNTHETIC_MOCK"
    source.current = mixed
    assert bound.activate(decision()).outcome == "ACTIVATED"
    second = bound.capture_read_view(mode="SIMULATION_ONLY")
    assert second.status().data_origin == "AKSHARE_MOCK"
    source.current = old
    assert (
        bound.activate(decision(day=date(2026, 1, 3), sequence=3, event_id="event3")).outcome
        == "ACTIVATED"
    )
    third = bound.capture_read_view(mode="SIMULATION_ONLY")
    assert third.status().data_origin == "UNSPECIFIED"
    assert (
        first.status().data_origin == "SYNTHETIC_MOCK"
        and second.status().data_origin == "AKSHARE_MOCK"
    )
    assert (
        len(
            {
                first.status().version_sha256,
                second.status().version_sha256,
                third.status().version_sha256,
            }
        )
        == 3
    )


@pytest.mark.parametrize("origin", ["UNSPECIFIED", "AKSHARE_MOCK"])
def test_old_and_mixed_origins_survive_both_service_and_client_paths(tmp_path, origin):
    root = tmp_path / "data"
    root.mkdir()
    if origin == "UNSPECIFIED":
        assert publish(FixtureCalendarStore(root), "old", content()).visibility == "COMMITTED"
        scope = {
            "route": "route-a",
            "venue": "SIM",
            "kind": "future",
            "product": "x",
            "contract": "x2601",
        }
        day, advance = date(2026, 1, 1), 0
    else:
        publish_mixed(root, mixed_version=True)
        scope = {
            "route": "route-a",
            "venue": "SHFE",
            "kind": "future",
            "product": "pfuture",
            "contract": "mock_shfe_future_contract",
        }
        day, advance = date(2027, 1, 1), 3
    bound = SimulationCalendarBinding(
        mode="SIMULATION_ONLY",
        store=ReceiptCheckedCalendarStore(TradingCalendarStore(root)),
        snapshot_factory=CalendarSnapshot,
        gate=FakeGate(),
        route="route-a",
        source_generation="gen-1",
        trading_day=day,
        last_sequence=1,
    )
    assert bound.status().data_origin == origin
    grant = CalendarGrant("principal", "domain", "account", "strategy", "run", *scope.values())
    policy = CalendarRangePolicy("policy", *scope.values(), advance, 0)
    registry = _make_registry(tmp_path / "registry.sqlite")
    service = GatewayCalendarService(grants=frozenset({grant}), range_policies=frozenset({policy}))
    service.bind_registry(registry)
    service.rebind(bound)
    try:
        single = service.handle(
            "calendar.query.v1",
            context=CONTEXT,
            arguments={"mode": "SIMULATION_ONLY", **scope, "trading_day": day.isoformat()},
        )
        multiple = service.handle(
            "calendar.windows.v1",
            context=CONTEXT,
            arguments={
                "mode": "SIMULATION_ONLY",
                **scope,
                "start_utc": f"{day}T01:30:00Z",
                "end_utc": f"{day}T02:00:00Z",
            },
        )
        for wire, result in (
            (single, GatewayCalendarQueryResult),
            (multiple, GatewayCalendarWindowsResult),
        ):
            assert wire["status"] == "COMPLETE", wire
            parsed = result.from_wire(json.loads(json.dumps(wire)))
            assert parsed.binding["data_origin"] == origin
            assert parsed.binding["version_sha256"] == bound.status().version_sha256
            assert (parsed.source_authentication, parsed.trade_authorization) == (
                "NOT_PROVEN",
                "NONE",
            )
    finally:
        registry.close()


def test_origin_missing_corrupt_unsupported_and_conflicting_declarations(tmp_path):
    root, store = seed_store(tmp_path)
    assert install_package_seed(root).status == "INSTALLED"
    source = store.read_current()
    original = json.loads(source.coverage_manifest_bytes)
    for changes in (
        {"data_origin": "REAL"},
        {"data_origin": None},
        {"schema": "bt_api.calendar-simulation-coverage/9"},
        {"schema": "bt_api.calendar-simulation-coverage/1"},
        None,
    ):
        manifest = dict(original)
        if changes is None:
            del manifest["data_origin"]
        else:
            manifest.update(changes)
        payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(payload).hexdigest()
        binding = json.loads(source.binding_bytes)
        binding["manifest_sha256"] = digest
        bbytes = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
        with pytest.raises(CalendarStoreError):
            calendar_snapshot_digest(
                source.content,
                coverage_manifest_bytes=payload,
                coverage_manifest_sha256=digest,
                binding_bytes=bbytes,
                binding_sha256=hashlib.sha256(bbytes).hexdigest(),
            )
        with pytest.raises(ValueError):
            _covered_source(
                replace(
                    source,
                    coverage_manifest_bytes=payload,
                    coverage_manifest_sha256=digest,
                    binding_bytes=bbytes,
                    binding_sha256=hashlib.sha256(bbytes).hexdigest(),
                )
            )
    with pytest.raises(ValueError):
        _covered_source(
            replace(source, coverage_manifest_bytes=source.coverage_manifest_bytes + b" ")
        )
    with pytest.raises(CalendarStoreError, match="conflict"):
        calendar_snapshot_digest(
            source.content,
            coverage_manifest_bytes=source.coverage_manifest_bytes,
            coverage_manifest_sha256=source.coverage_manifest_sha256,
            binding_bytes=source.binding_bytes,
            binding_sha256=source.binding_sha256,
            simulation_provenance={"schema": "invalid"},
        )
    with pytest.raises(ValueError):
        _covered_source(replace(source, simulation_provenance={"schema": "invalid"}))
