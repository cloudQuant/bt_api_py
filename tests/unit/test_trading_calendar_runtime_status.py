"""Read-only instance status checks using a real local experimental store."""

import os

import pytest

from bt_api_py import trading_calendar_runtime_status as module
from bt_api_py.bt_api import BtApi
from tests.calendar_test_store import FixtureCalendarStore

try:
    import fcntl
except ImportError:
    fcntl = None

pytestmark = pytest.mark.skipif(fcntl is None, reason="local store locking requires POSIX")


def publish(store, number):
    content = {
        "sources": [{"source": number}],
        "trading_days": [{"day": number}],
        "trading_hours": [{"hours": number}],
        "exceptions": [],
    }
    assert (
        store.publish(content, publication_id=f"p{number}", version_id=f"v{number}").visibility
        == "COMMITTED"
    )


def test_explicit_routes_are_isolated_and_current_is_never_runtime_binding(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    a, b = FixtureCalendarStore(first), FixtureCalendarStore(second)
    publish(a, 1)
    publish(b, 2)
    api, other = BtApi(debug=False), BtApi(debug=False)
    assert api.trading_calendar_status("CTP___FUTURE").reason == "ROUTE_UNCONFIGURED"
    roots = {"CTP___FUTURE": str(first), "OTHER": str(second)}
    api.configure_trading_calendar_status(route_roots=roots)
    roots["OTHER"] = str(first)
    assert other.trading_calendar_status("CTP___FUTURE").status == "UNKNOWN"
    first_status = api.trading_calendar_status("CTP___FUTURE")
    assert first_status.status == "OBSERVED_UNAUTHENTICATED"
    assert (first_status.publication_id, first_status.version_id) == ("p1", "v1")
    assert first_status.bound == "NOT_BOUND"
    assert first_status.source_authentication == "NOT_PROVEN"
    assert first_status.route_mapping == "CALLER_DECLARED_ONLY"
    assert api.trading_calendar_status("OTHER").publication_id == "p2"
    publish(a, 3)
    newer = api.trading_calendar_status("CTP___FUTURE")
    assert (newer.publication_id, newer.bound) == ("p3", "NOT_BOUND")
    assert first_status.publication_id == "p1"
    api.configure_trading_calendar_status(route_roots={})
    assert api.trading_calendar_status("CTP___FUTURE").reason == "ROUTE_UNCONFIGURED"


@pytest.mark.parametrize(
    "bad", [None, {"": "/tmp"}, {"route": "relative"}, {"route": "/tmp/../bad"}, {1: "/tmp"}]
)
def test_invalid_config_is_rejected_without_changing_existing_state(tmp_path, bad):
    store = FixtureCalendarStore(tmp_path)
    publish(store, 1)
    api = BtApi(debug=False)
    api.configure_trading_calendar_status(route_roots={"route": str(tmp_path)})
    with pytest.raises(ValueError):
        api.configure_trading_calendar_status(route_roots=bad)
    assert api.trading_calendar_status("route").publication_id == "p1"
    assert api.trading_calendar_status(None).reason == "INVALID_ROUTE"


def test_pending_corrupt_and_busy_store_are_unknown(tmp_path):
    store = FixtureCalendarStore(tmp_path)
    publish(store, 1)
    api = BtApi(debug=False)
    api.configure_trading_calendar_status(route_roots={"route": str(tmp_path)})
    lock = os.open(tmp_path / ".lock", os.O_RDWR)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert api.trading_calendar_status("route").status == "UNKNOWN"
    finally:
        os.close(lock)
    pending = tmp_path / "PENDING.json"
    pending.write_bytes(b"{}")
    assert api.trading_calendar_status("route").reason == "CURRENT_UNAVAILABLE"
    pending.unlink()
    assert api.trading_calendar_status("route").publication_id == "p1"
    (tmp_path / "CURRENT.json").write_bytes(b"invalid")
    assert api.trading_calendar_status("route").reason == "CURRENT_UNAVAILABLE"


def test_concurrent_configuration_change_invalidates_observation(tmp_path, monkeypatch):
    store = FixtureCalendarStore(tmp_path)
    publish(store, 1)
    api = BtApi(debug=False)
    api.configure_trading_calendar_status(route_roots={"route": str(tmp_path)})
    original = module.TradingCalendarStore.read_current_with_consistent_receipts

    def reconfigure(store, **kwargs):
        result = original(store, **kwargs)
        api.configure_trading_calendar_status(route_roots={"route": str(tmp_path)})
        return result

    monkeypatch.setattr(
        module.TradingCalendarStore, "read_current_with_consistent_receipts", reconfigure
    )
    assert api.trading_calendar_status("route").reason == "CONFIGURATION_CHANGED"


def test_existing_provider_schedule_is_unaffected(monkeypatch):
    from bt_api_py import provider_entrypoints

    calls = []
    schedule = object()
    monkeypatch.setattr(
        provider_entrypoints,
        "load_route_entrypoint",
        lambda *args: lambda feed: calls.append(feed) or schedule,
    )
    api = BtApi(debug=False)
    assert api.trading_session_schedule("route") is schedule
    assert api.trading_calendar_status("route").status == "UNKNOWN"
    assert api.trading_session_schedule("route") is schedule
    assert len(calls) == 1


def test_read_limits_keep_complete_chain_and_never_truncate(tmp_path, monkeypatch):
    store = FixtureCalendarStore(tmp_path)
    publish(store, 1)
    publish(store, 2)
    read = store.read_current_with_consistent_receipts
    assert read().snapshot.publication_id == "p2"
    assert (
        read(budget=module.CalendarReadBudget(8_000_000, 64_000_000, 2)).snapshot.publication_id
        == "p2"
    )
    from bt_api_py.trading_calendar_store import CalendarStoreError

    for budget in (
        module.CalendarReadBudget(8_000_000, 64_000_000, 1),
        module.CalendarReadBudget(8_000_000, 100, 10),
        module.CalendarReadBudget(10, 64_000_000, 10),
    ):
        with pytest.raises(CalendarStoreError):
            read(budget=budget)
    api = BtApi(debug=False)
    api.configure_trading_calendar_status(route_roots={"route": str(tmp_path)})
    monkeypatch.setattr(module, "_READ_BUDGET", module.CalendarReadBudget(10, 64_000_000, 10))
    assert api.trading_calendar_status("route").reason == "CURRENT_UNAVAILABLE"
    assert read().snapshot.publication_id == "p2"


def test_read_budget_validation():
    for values in ((0, 1, 1), (1, -1, 1), (1, 1, True), (1.5, 1, 1)):
        with pytest.raises(ValueError):
            module.CalendarReadBudget(*values)


def test_configuration_token_is_instance_local_and_invalidated_by_reconfiguration(tmp_path):
    first = module.TradingCalendarStatusReader()
    second = module.TradingCalendarStatusReader()
    token = first.configuration_token()
    assert first.configuration_is_current(token) is True
    assert second.configuration_is_current(token) is False
    first.configure({"route": str(tmp_path)})
    assert first.configuration_is_current(token) is False
    current = first.configuration_token()
    assert first.configuration_is_current(current) is True
    with pytest.raises(ValueError):
        first.configure({"route": "relative"})
    assert first.configuration_is_current(current) is True
    first.configure({"route": str(tmp_path)})
    assert first.configuration_is_current(current) is False


def test_facade_configuration_token_delegates_without_exposing_reader(tmp_path):
    api, other = BtApi(debug=False), BtApi(debug=False)
    token = api.trading_calendar_configuration_token()
    assert api.trading_calendar_configuration_is_current(token) is True
    assert other.trading_calendar_configuration_is_current(token) is False
    api.configure_trading_calendar_status(route_roots={"route": str(tmp_path)})
    assert api.trading_calendar_configuration_is_current(token) is False
    api._trading_calendar_status_reader = object()
    assert api.trading_calendar_configuration_is_current(token) is False
    with pytest.raises(ValueError):
        api.trading_calendar_configuration_token()


def test_oversized_current_is_rejected_by_status_without_reading_payload(tmp_path, monkeypatch):
    store = FixtureCalendarStore(tmp_path)
    publish(store, 1)
    target = tmp_path / "versions" / "v1.json"
    with target.open("r+b") as stream:
        stream.truncate(9 * 1024 * 1024)
    original = module.TradingCalendarStore._read_bounded
    saw_large = []

    def observed(self, path, limit):
        if path == target:
            saw_large.append((path.stat().st_size, limit))
        return original(self, path, limit)

    monkeypatch.setattr(module.TradingCalendarStore, "_read_bounded", observed)
    api = BtApi(debug=False)
    api.configure_trading_calendar_status(route_roots={"route": str(tmp_path)})
    assert api.trading_calendar_status("route").reason == "CURRENT_UNAVAILABLE"
    assert saw_large == [(9 * 1024 * 1024, 8 * 1024 * 1024)]
