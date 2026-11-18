"""Unit tests guarding legacy calendar ports against 2026-constant bypass and mock laundering."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from bt_api_base.futures_expected_calendar_protocol import (
    FuturesExpectedCalendarError,
    FuturesExpectedCalendarPort,
    FuturesExpectedCalendarRequest,
    FuturesExpectedCalendarSnapshot,
    FuturesExpectedCalendarStatus,
    FuturesExpectedTradingDay,
    SelectedFuturesExpectedCalendarRoute,
)
from bt_api_base.futures_market_protocol import FuturesProductKey
from bt_api_base.trading_session_protocol import (
    TradingPhase,
    TradingSessionSchedulePort,
    TradingSessionState,
    TradingWindow,
)
from bt_api_py import trading_calendar_store as store_module
from bt_api_py.bt_api import BtApi
from bt_api_py.futures_expected_calendar_composition import (
    FuturesExpectedCalendarCompositionError,
    FuturesExpectedCalendarProviderRegistry,
)
from bt_api_py.trading_calendar_package_seed import install_package_seed
from tests.unit.test_trading_calendar_runtime_mixed_query import publish as publish_mixed


def _make_ctp_api() -> BtApi:
    api = BtApi()
    api.exchange_feeds["CTP___FUTURE"] = object()
    return api


def _sample_request(trading_day: date = date(2026, 11, 17)) -> FuturesExpectedCalendarRequest:
    return FuturesExpectedCalendarRequest(
        products=(FuturesProductKey(venue="SHFE", product_id="rb"),),
        current_trading_days=(FuturesExpectedTradingDay(venue="SHFE", trading_day=trading_day),),
        as_of_utc=datetime(2026, 11, 17, 1, 0, tzinfo=UTC),
    )


def _publish_bare_store(root: Path, *, sources: list[dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    content: dict[str, Any] = {
        "trading_days": {"SHFE": {"2026-11-17": {"is_trading_day": True}}},
        "trading_hours": {},
        "exceptions": {},
        "sources": sources,
    }
    store = store_module.TradingCalendarStore(str(root))
    policy = store.initialize_review_policy(config_read_succeeded=True)
    difference: dict[str, Any] = {}
    claim = store_module.PolicyPublication(
        policy.directory_id,
        policy.revision,
        policy.mode,
        store_module.calendar_snapshot_digest(content),
        difference,
        store_module._sha(store_module._canonical(difference)),
    )
    result = store.publish(
        content,
        publication_id="bare_pub",
        version_id="bare_ver_1",
        expected_current=store_module.CurrentPrecondition.absent(),
        policy_publication=claim,
    )
    assert result.visibility == "COMMITTED", result


def _install_seed(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    store_module.TradingCalendarStore(str(root)).initialize_review_policy(
        config_read_succeeded=True
    )
    result = install_package_seed(str(root))
    assert result.status == "INSTALLED", result


def test_unconfigured_ctp_route_does_not_bypass_with_2026_constants() -> None:
    api = _make_ctp_api()
    req = _sample_request(date(2026, 5, 4))  # 2026 State Council holiday in legacy constants

    port = api.futures_expected_calendar("CTP___FUTURE")
    snap = port.build(req)
    assert snap.status is FuturesExpectedCalendarStatus.UNAVAILABLE
    assert snap.blockers == ("FUTURES_EXPECTED_CALENDAR_ROOT_UNCONFIGURED",)
    assert snap.venue_calendars == ()
    assert snap.product_days == ()
    assert snap.sources == ()
    assert port.validate(snap) is True

    with pytest.raises(
        FuturesExpectedCalendarCompositionError,
        match="FUTURES_EXPECTED_CALENDAR_PROVIDER_UNAVAILABLE",
    ):
        port.expected_trading_days("SHFE", 2026)

    schedule = api.trading_session_schedule("CTP___FUTURE")
    state = schedule.session_state(
        "CTP:SHFE:future:rb2610",
        datetime(2026, 11, 17, 1, 30, tzinfo=UTC),
    )
    assert state.phase is TradingPhase.UNKNOWN
    assert state.reason == "calendar_root_unconfigured"
    assert state.trading_day is None
    assert (
        schedule.windows(
            "CTP:SHFE:future:rb2610",
            datetime(2026, 11, 17, 0, 0, tzinfo=UTC),
            datetime(2026, 11, 17, 8, 0, tzinfo=UTC),
        )
        == ()
    )


def test_configured_missing_or_empty_store_fails_closed(tmp_path: Path) -> None:
    missing_root = tmp_path / "does_not_exist"
    empty_root = tmp_path / "empty_store"
    empty_root.mkdir()
    store_module.TradingCalendarStore(str(empty_root))

    for label, root in (("missing", missing_root), ("empty", empty_root)):
        api = _make_ctp_api()
        api.configure_trading_calendar_status(route_roots={"CTP___FUTURE": str(root)})

        port = api.futures_expected_calendar("CTP___FUTURE")
        snap = port.build(_sample_request())
        assert snap.status is FuturesExpectedCalendarStatus.UNAVAILABLE, label
        assert snap.blockers == ("FUTURES_EXPECTED_CALENDAR_CURRENT_UNAVAILABLE",), label
        assert port.validate(snap) is True

        schedule = api.trading_session_schedule("CTP___FUTURE")
        state = schedule.session_state(
            "CTP:SHFE:future:rb2610",
            datetime(2026, 11, 17, 1, 30, tzinfo=UTC),
        )
        assert state.phase is TradingPhase.UNKNOWN, label
        assert state.reason == "calendar_current_unavailable", label
        assert (
            schedule.windows(
                "CTP:SHFE:future:rb2610",
                datetime(2026, 11, 17, 0, 0, tzinfo=UTC),
                datetime(2026, 11, 17, 8, 0, tzinfo=UTC),
            )
            == ()
        ), label


def test_synthetic_mock_package_seed_is_not_falsified_as_official_complete(
    tmp_path: Path,
) -> None:
    _install_seed(tmp_path)
    api = _make_ctp_api()
    api.configure_trading_calendar_status(route_roots={"CTP___FUTURE": str(tmp_path)})

    # Explicit status/simulation query API still reports unverified simulation seed explicitly.
    status = api.trading_calendar_status("CTP___FUTURE")
    assert status.status == "OBSERVED_UNAUTHENTICATED"
    assert status.coverage_status == "DECLARED_UNVERIFIED"
    assert status.source_authentication == "NOT_PROVEN"

    sim_query = api.trading_calendar_query(
        "CTP___FUTURE",
        mode="SIMULATION_ONLY",
        venue="SHFE",
        kind="future",
        product="rb",
        contract="rb2610",
        trading_day=date(2026, 11, 17),
    )
    assert sim_query.environment == "SIMULATION_ONLY"
    assert sim_query.data_origin in {"SYNTHETIC_MOCK", "UNSPECIFIED"}
    assert sim_query.source_authentication == "NOT_PROVEN"

    # Legacy ports MUST fail closed and never upgrade SYNTHETIC_MOCK to official COMPLETE.
    port = api.futures_expected_calendar("CTP___FUTURE")
    snap = port.build(_sample_request())
    assert snap.status is FuturesExpectedCalendarStatus.UNAVAILABLE
    assert snap.blockers == ("FUTURES_EXPECTED_CALENDAR_SIMULATION_SOURCE_UNVERIFIED",)
    assert snap.venue_calendars == ()
    assert snap.sources == ()

    with pytest.raises(
        FuturesExpectedCalendarCompositionError,
        match="FUTURES_EXPECTED_CALENDAR_PROVIDER_UNAVAILABLE",
    ):
        port.expected_trading_days("SHFE", 2026)

    schedule = api.trading_session_schedule("CTP___FUTURE")
    state = schedule.session_state(
        "CTP:SHFE:future:rb2610",
        datetime(2026, 11, 17, 1, 30, tzinfo=UTC),
    )
    assert state.phase is TradingPhase.UNKNOWN
    assert state.reason == "calendar_simulation_source_unverified"
    assert state.trading_day is None
    assert (
        schedule.windows(
            "CTP:SHFE:future:rb2610",
            datetime(2026, 11, 17, 0, 0, tzinfo=UTC),
            datetime(2026, 11, 17, 8, 0, tzinfo=UTC),
        )
        == ()
    )


@pytest.mark.parametrize("mixed_version", [False, True])
def test_simulation_and_multi_source_mixed_files_fail_closed_in_legacy_ports(
    tmp_path: Path, mixed_version: bool
) -> None:
    publish_mixed(tmp_path, mixed_version=mixed_version)
    api = _make_ctp_api()
    api.configure_trading_calendar_status(route_roots={"CTP___FUTURE": str(tmp_path)})

    # Explicit SIMULATION_ONLY query remains usable on the simulation file store.
    sim_query = api.trading_calendar_query(
        "CTP___FUTURE",
        mode="SIMULATION_ONLY",
        venue="SHFE",
        kind="future",
        product="pfuture",
        contract="mock_shfe_future_contract",
        trading_day=date(2027, 1, 4),
    )
    assert sim_query.environment == "SIMULATION_ONLY"
    assert sim_query.source_authentication == "NOT_PROVEN"

    # Legacy expected-calendar and session-schedule ports MUST NOT present this as official.
    port = api.futures_expected_calendar("CTP___FUTURE")
    snap = port.build(_sample_request(date(2027, 1, 4)))
    assert snap.status is FuturesExpectedCalendarStatus.UNAVAILABLE
    assert snap.blockers == ("FUTURES_EXPECTED_CALENDAR_SIMULATION_SOURCE_UNVERIFIED",)
    assert snap.venue_calendars == ()

    schedule = api.trading_session_schedule("CTP___FUTURE")
    state = schedule.session_state(
        "CTP:SHFE:future:pfuture2701",
        datetime(2027, 1, 4, 1, 30, tzinfo=UTC),
    )
    assert state.phase is TradingPhase.UNKNOWN
    assert state.reason == "calendar_simulation_source_unverified"


def test_bare_unverified_file_store_fails_closed_without_simulation_label(tmp_path: Path) -> None:
    _publish_bare_store(
        tmp_path,
        sources=[
            {"id": "custom_mirror_a", "description": "local mirror A"},
            {"id": "custom_mirror_b", "description": "local mirror B"},
        ],
    )
    api = _make_ctp_api()
    api.configure_trading_calendar_status(route_roots={"CTP___FUTURE": str(tmp_path)})

    port = api.futures_expected_calendar("CTP___FUTURE")
    snap = port.build(_sample_request())
    assert snap.status is FuturesExpectedCalendarStatus.UNAVAILABLE
    assert snap.blockers == ("FUTURES_EXPECTED_CALENDAR_SOURCE_UNVERIFIED",)

    schedule = api.trading_session_schedule("CTP___FUTURE")
    state = schedule.session_state(
        "CTP:SHFE:future:rb2610",
        datetime(2026, 11, 17, 1, 30, tzinfo=UTC),
    )
    assert state.phase is TradingPhase.UNKNOWN
    assert state.reason == "calendar_source_unverified"


def test_reconfiguration_invalidates_cached_ports_and_schedules(tmp_path: Path) -> None:
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    _install_seed(root_a)
    _publish_bare_store(root_b, sources=[{"id": "mirror", "description": "local mirror"}])

    api = _make_ctp_api()
    req = _sample_request()

    # 1. Start unconfigured -> acquire old port & schedule
    old_port = api.futures_expected_calendar("CTP___FUTURE")
    old_snap = old_port.build(req)
    assert old_snap.blockers == ("FUTURES_EXPECTED_CALENDAR_ROOT_UNCONFIGURED",)
    assert old_port.validate(old_snap) is True
    old_schedule = api.trading_session_schedule("CTP___FUTURE")

    # 2. Configure root_a -> old handles become stale/invalidated; new handles see root_a
    api.configure_trading_calendar_status(route_roots={"CTP___FUTURE": str(root_a)})
    assert old_port.validate(old_snap) is False
    assert old_port.build(req).blockers == ("FUTURES_EXPECTED_CALENDAR_CONFIGURATION_CHANGED",)
    with pytest.raises(
        FuturesExpectedCalendarCompositionError,
        match="FUTURES_EXPECTED_CALENDAR_ROUTE_CHANGED",
    ):
        old_port.expected_trading_days("SHFE", 2026)
    assert (
        old_schedule.session_state(
            "CTP:SHFE:future:rb2610", datetime(2026, 11, 17, 1, 30, tzinfo=UTC)
        ).reason
        == "calendar_configuration_changed"
    )

    port_a = api.futures_expected_calendar("CTP___FUTURE")
    assert port_a.build(req).blockers == (
        "FUTURES_EXPECTED_CALENDAR_SIMULATION_SOURCE_UNVERIFIED",
    )
    schedule_a = api.trading_session_schedule("CTP___FUTURE")
    assert (
        schedule_a.session_state(
            "CTP:SHFE:future:rb2610", datetime(2026, 11, 17, 1, 30, tzinfo=UTC)
        ).reason
        == "calendar_simulation_source_unverified"
    )

    # 3. Reconfigure root_a -> root_b -> old_a handles invalidated; new_b handles see bare store
    api.configure_trading_calendar_status(route_roots={"CTP___FUTURE": str(root_b)})
    assert (
        port_a.build(req).blockers == ("FUTURES_EXPECTED_CALENDAR_CONFIGURATION_CHANGED",)
    )
    assert (
        schedule_a.session_state(
            "CTP:SHFE:future:rb2610", datetime(2026, 11, 17, 1, 30, tzinfo=UTC)
        ).reason
        == "calendar_configuration_changed"
    )
    port_b = api.futures_expected_calendar("CTP___FUTURE")
    assert port_b.build(req).blockers == ("FUTURES_EXPECTED_CALENDAR_SOURCE_UNVERIFIED",)
    schedule_b = api.trading_session_schedule("CTP___FUTURE")
    assert (
        schedule_b.session_state(
            "CTP:SHFE:future:rb2610", datetime(2026, 11, 17, 1, 30, tzinfo=UTC)
        ).reason
        == "calendar_source_unverified"
    )

    # 4. Clear configuration -> fresh handles see root_unconfigured again
    api.configure_trading_calendar_status(route_roots={})
    assert (
        api.futures_expected_calendar("CTP___FUTURE").build(req).blockers
        == ("FUTURES_EXPECTED_CALENDAR_ROOT_UNCONFIGURED",)
    )
    assert (
        api.trading_session_schedule("CTP___FUTURE")
        .session_state(
            "CTP:SHFE:future:rb2610", datetime(2026, 11, 17, 1, 30, tzinfo=UTC)
        )
        .reason
        == "calendar_root_unconfigured"
    )


def test_concurrent_reconfiguration_mid_read_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    _install_seed(root_a)
    _install_seed(root_b)

    api = _make_ctp_api()
    api.configure_trading_calendar_status(route_roots={"CTP___FUTURE": str(root_a)})
    port = api.futures_expected_calendar("CTP___FUTURE")

    orig_read = store_module.TradingCalendarStore.read_current_with_consistent_receipts

    def mutating_read(self: store_module.TradingCalendarStore, *args: Any, **kwargs: Any) -> Any:
        result = orig_read(self, *args, **kwargs)
        api.configure_trading_calendar_status(route_roots={"CTP___FUTURE": str(root_b)})
        return result

    monkeypatch.setattr(
        store_module.TradingCalendarStore,
        "read_current_with_consistent_receipts",
        mutating_read,
    )

    snap = port.build(_sample_request())
    assert snap.status is FuturesExpectedCalendarStatus.UNAVAILABLE
    assert snap.blockers == ("FUTURES_EXPECTED_CALENDAR_CONFIGURATION_CHANGED",)

    api.configure_trading_calendar_status(route_roots={"CTP___FUTURE": str(root_a)})
    schedule_a = api.trading_session_schedule("CTP___FUTURE")
    state = schedule_a.session_state(
        "CTP:SHFE:future:rb2610", datetime(2026, 11, 17, 1, 30, tzinfo=UTC)
    )
    assert state.phase is TradingPhase.UNKNOWN
    assert state.reason == "calendar_configuration_changed"


def test_custom_non_ctp_route_and_replaced_reader_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _CustomSchedule(TradingSessionSchedulePort):
        def session_state(self, instrument_id: str, at_utc: datetime) -> TradingSessionState:
            return TradingSessionState(
                instrument_id=instrument_id,
                at_utc=at_utc,
                phase=TradingPhase.CONTINUOUS,
                trading_day=date(2026, 11, 17),
                current_window=None,
                next_continuous=None,
                calendar_ref="custom.test",
                reason="custom_ok",
            )

        def windows(
            self, instrument_id: str, start_utc: datetime, end_utc: datetime
        ) -> tuple[TradingWindow, ...]:
            return ()

    class _CustomExpectedPort(FuturesExpectedCalendarPort):
        def expected_trading_days(self, venue: str, year: int) -> tuple[date, ...]:
            return (date(2026, 11, 17),)

        def build(
            self, request: FuturesExpectedCalendarRequest
        ) -> FuturesExpectedCalendarSnapshot:
            raise FuturesExpectedCalendarError("custom_not_used")

        def validate(self, snapshot: FuturesExpectedCalendarSnapshot) -> bool:
            return True

    # Unconfigured non-CTP test double preserves normal delegate behavior.
    monkeypatch.setattr(
        "bt_api_py.provider_entrypoints.load_route_entrypoint",
        lambda group, route: (lambda _feed: _CustomSchedule()),
    )
    api = _make_ctp_api()
    custom_sched = api.trading_session_schedule("CUSTOM___FUTURE")
    assert isinstance(custom_sched, _CustomSchedule)

    # Custom route explicitly configured with file root respects file config (does not run custom).
    _install_seed(tmp_path)
    api.configure_trading_calendar_status(route_roots={"CUSTOM___FUTURE": str(tmp_path)})
    guarded_sched = api.trading_session_schedule("CUSTOM___FUTURE")
    assert not isinstance(guarded_sched, _CustomSchedule)
    assert (
        guarded_sched.session_state(
            "CUSTOM:SHFE:future:rb2610",
            datetime(2026, 11, 17, 1, 30, tzinfo=UTC),
        ).reason
        == "calendar_simulation_source_unverified"
    )

    # Custom FuturesExpectedCalendarProviderRegistry works when unconfigured.
    custom_registry = FuturesExpectedCalendarProviderRegistry()
    custom_registry.register("CUSTOM___FUTURE", lambda _route: _CustomExpectedPort())
    api2 = _make_ctp_api()
    route = SelectedFuturesExpectedCalendarRoute(
        exchange_name="CUSTOM___FUTURE",
        client=object(),
        environment="test",
    )
    monkeypatch.setattr(api2, "_resolve_futures_expected_calendar_route", lambda _name: route)
    router = api2._futures_expected_calendar_capability_router()
    router._registry = custom_registry  # verify custom provider still works unconfigured
    assert router.expected_calendar("CUSTOM___FUTURE").expected_trading_days("SHFE", 2026) == (
        date(2026, 11, 17),
    )

    # Replaced _trading_calendar_status_reader fails closed on both ports.
    api2._trading_calendar_status_reader = object()  # type: ignore[assignment]
    degraded_sched = api2.trading_session_schedule("CTP___FUTURE")
    assert (
        degraded_sched.session_state(
            "CTP:SHFE:future:rb2610",
            datetime(2026, 11, 17, 1, 30, tzinfo=UTC),
        ).reason
        == "calendar_status_reader_unavailable"
    )
    with pytest.raises(
        FuturesExpectedCalendarCompositionError,
        match="FUTURES_EXPECTED_CALENDAR_PROVIDER_UNAVAILABLE",
    ):
        api2.futures_expected_calendar("CTP___FUTURE")


def test_file_backed_snapshot_validation_rejects_forged_copies(tmp_path: Path) -> None:
    _install_seed(tmp_path)
    api = _make_ctp_api()
    api.configure_trading_calendar_status(route_roots={"CTP___FUTURE": str(tmp_path)})
    port = api.futures_expected_calendar("CTP___FUTURE")
    snap = port.build(_sample_request())
    assert port.validate(snap) is True
    forged = replace(snap, blockers=("FUTURES_EXPECTED_CALENDAR_ROOT_UNCONFIGURED",))
    assert port.validate(forged) is False
