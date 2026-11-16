"""Trusted CTP factory with a real background clock and synthetic SNTP."""
import pytest

from bt_api_py import clock_verification
from bt_api_py.clock_verification import ClockVerifier
from bt_api_py.gateway_clock_history import GatewayClockHistoryError
from bt_api_py.gateway_server_clock_refresh import (
    GatewayServerClockRefresher,
    GatewayServerClockRefreshSettings,
    GatewayServerClockSnapshot,
)
from tests.test_iter63_ctp_gateway_trading import FakeSession, _resources, _wait
from tests.unit.test_iter63_ctp_gateway_market_profile import setup


@pytest.mark.parametrize('capacity', [0, 8])
def test_factory_owner_runs_real_clock_with_explicit_optional_history(tmp_path, monkeypatch, capacity):
    monkeypatch.setattr(clock_verification, '_sntp_query', lambda *_: (5, 100))
    monkeypatch.setattr(FakeSession, 'market_provider', property(lambda _self: object()), raising=False)
    started = []
    original_start = GatewayServerClockRefresher.start
    def track_real_start(refresher):
        started.append(refresher)
        return original_start(refresher)
    monkeypatch.setattr(GatewayServerClockRefresher, 'start', track_real_start)
    build, _, _, authority, _ = setup(tmp_path, monkeypatch)
    clock_settings = GatewayServerClockRefreshSettings(5_000_000, 1, .02, history_capacity=capacity)
    profile = build(clock_verifier_factory=lambda: ClockVerifier(servers=('trusted',), validity_seconds=5),
                    clock_allowed_servers=frozenset({'trusted'}), clock_refresh_settings=clock_settings)
    resources = _resources(profile, authority)
    try:
        assert len(started) == 1 and type(started[0]) is GatewayServerClockRefresher
        refresher = started[0]
        owner = resources.values['binding']._owner
        def read():
            sample = refresher.current()
            try:
                page = refresher.read_history(after_sequence=0, limit=10)
            except GatewayClockHistoryError as error:
                return sample, error.code
            return sample, page
        def published():
            value = owner.submit_service_call(read).result(timeout_seconds=2)
            return value if type(value[0]) is GatewayServerClockSnapshot else None
        _wait(published)
        sample, result = published()
        assert type(sample) is GatewayServerClockSnapshot
        if capacity:
            assert result.available and result.records
            assert any(record.kind == 'SAMPLE' for record in result.records)
            assert all(record.snapshot.pid == sample.pid for record in result.records
                       if record.snapshot is not None)
        else:
            assert result == 'CLOCK_HISTORY_DISABLED'
    finally:
        resources.close()
    assert not started[0]._worker


@pytest.mark.parametrize('value', [True, -1, 1, 4097, 2.0])
def test_clock_history_settings_reject_invalid_capacity(value):
    with pytest.raises(ValueError, match='INVALID_GATEWAY_SERVER_CLOCK'):
        GatewayServerClockRefreshSettings(100, 1, 1, history_capacity=value)
