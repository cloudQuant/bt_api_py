"""Order-time source+local clock checks: synthetic evidence, no network."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
import threading
import time

import pytest

from bt_api_base.market_tick_protocol import bind_market_tick_event
from bt_api_base.service_protocol import WIRE_PROTOCOL_VERSION, WireEnvelope, WireHeader, WireIdentity, WireMessageKind, WireRole, WireScope
from bt_api_py.clock_verification import ClockVerifier, GatewayClockVerifier, GatewayTradeClockQualifier
from bt_api_py.gateway_strategy_market import (GatewayMarketClockObservation, GatewayMarketReceivedEvent,
                                                GatewayMarketSubscriptionClient)
from bt_api_py.service_market import MarketReceiveReceipt
from tests.test_iter62_clock_verification import SOURCE, _unqualified_event


def make_case(monkeypatch):
    monkeypatch.setattr('bt_api_py.clock_verification._sntp_query', lambda host, timeout: (0, 2_000_000))
    local = GatewayClockVerifier(ClockVerifier(servers=('synthetic',)))
    assert local.refresh() is not None
    now = datetime.now(timezone.utc)
    china = now.astimezone(timezone(timedelta(hours=8)))
    original = _unqualified_event(now - timedelta(milliseconds=20), now)
    event = bind_market_tick_event(replace(original.data, trading_date=china.date().isoformat()), SOURCE, original.cursor)
    identity = WireIdentity(WireRole.SERVICE, 'principal', 'actor')
    header = WireHeader(protocol_version=WIRE_PROTOCOL_VERSION, kind=WireMessageKind.EVENT, message_id='event', correlation_id=None,
        identity=identity, scope=WireScope('domain'), operation='market.event', schema_id='market.event',
        schema_version=1, sent_at_utc=now.isoformat().replace('+00:00', 'Z'), stream_id=SOURCE.stream_id,
        stream_epoch=SOURCE.epoch, sequence=1)
    frame = WireEnvelope(header, {'key': SOURCE.key.to_dict(), 'data': event.data.to_dict()})
    receipt = MarketReceiveReceipt('pub', SOURCE.stream_id, SOURCE.epoch, 1, 'event',
        time.time_ns(), time.monotonic_ns(), os.getpid(), threading.get_ident())
    client = GatewayMarketSubscriptionClient(None, 'main')
    issued = GatewayMarketReceivedEvent(frame, receipt)
    client._issued_received_event = issued
    wall, mono = time.time_ns(), time.monotonic_ns()
    login = wall - 1_000_000_000
    observation = {'schema_version': 1, 'provider': 'ctp', 'account_scope': 'account', 'venue': SOURCE.key.instrument.venue,
        'service_generation': 'owner-generation-3', 'td_connection_generation': 4, 'td_source_ref': 'source-1',
        'trading_day': china.strftime('%Y%m%d'),
        'venue_time': datetime.fromtimestamp(login / 1e9, timezone(timedelta(hours=8))).strftime('%H:%M:%S'),
        'login_received_wall_ns': login, 'source_observed_wall_ns': wall - 500_000,
        'age_ns': 1_000_000_000, 'remaining_ns': 200_000_000_000, 'market_source': SOURCE.to_dict(),
        'source_calibration': {'schema_version': 1, 'method': 'sntp', 'offset_ns': 0,
            'error_bound_ns': 2_000_000, 'sample_age_ns': 2_000_000_000, 'remaining_ns': 200_000_000_000}}
    def read_clock(**_):
        current = GatewayMarketClockObservation(json.dumps(observation), mono-1_000_000, wall, mono,
            'request', 'reply', 'registration', 'service-epoch', 'account', SOURCE,
            os.getpid(), threading.get_ident())
        client._issued_clock_observation = current
        return current
    monkeypatch.setattr(client, 'clock_observation', read_clock)
    return GatewayTradeClockQualifier(client, local), event, receipt, observation, client


def test_fresh_issued_pub_with_current_dual_calibration_is_qualified(monkeypatch):
    qualifier, event, receipt, _, _ = make_case(monkeypatch)
    result = qualifier.qualify(event, receipt)
    assert result.ok and result.reason is None
    assert result.client_received_monotonic_ns == receipt.received_monotonic_ns
    assert result.event_time_utc is not None and result.latency_seconds < 2
    assert event.data.execution_eligible is False  # provider event is not rewritten


def test_forged_receipt_and_replay_fail_closed(monkeypatch):
    qualifier, event, receipt, _, _ = make_case(monkeypatch)
    assert qualifier.qualify(event, replace(receipt)).reason == 'CLOCK_BINDING_UNAVAILABLE'
    assert qualifier.qualify(event, replace(receipt, source='replay')).reason == 'REALTIME_RECEIPT_UNAVAILABLE'


def as_tick_observation(event, observation):
    observation.pop('venue_time')
    observation.pop('login_received_wall_ns')
    observation.update(schema_version=2, observation_kind='native_md_tick',
        venue_observed_at_utc=event.data.provider_event_time_utc,
        source_event_received_wall_ns=observation['source_observed_wall_ns'] - 10_000_000,
        provider_sequence=event.data.provider_sequence,
        provider_subscription_epoch=event.data.provider_subscription_epoch, age_ns=10_000_000)


def test_v2_tick_evidence_is_bound_to_the_actual_frame(monkeypatch):
    qualifier, event, receipt, observation, _ = make_case(monkeypatch)
    as_tick_observation(event, observation)
    assert qualifier.qualify(event, receipt).ok
    assert qualifier.qualify(event, replace(receipt)).reason == 'CLOCK_BINDING_UNAVAILABLE'
    assert qualifier.qualify(event, replace(receipt, source='replay')).reason == 'REALTIME_RECEIPT_UNAVAILABLE'


@pytest.mark.parametrize('field, replacement', [
    ('provider_sequence', 999), ('provider_subscription_epoch', 999),
    ('venue_observed_at_utc', '2000-01-01T00:00:00Z'),
    ('venue_observed_at_utc', {}), ('observation_kind', 'login'),
    ('source_event_received_wall_ns', 0), ('schema_version', 3),
])
def test_v2_wrong_or_forged_source_fails_closed(monkeypatch, field, replacement):
    qualifier, event, receipt, observation, _ = make_case(monkeypatch)
    as_tick_observation(event, observation)
    observation[field] = replacement
    assert not qualifier.qualify(event, receipt).ok


def test_v2_old_or_missing_source_fields_fail_closed(monkeypatch):
    qualifier, event, receipt, observation, _ = make_case(monkeypatch)
    as_tick_observation(event, observation)
    del observation['provider_subscription_epoch']
    assert qualifier.qualify(event, receipt).reason == 'SOURCE_CLOCK_INVALID'


def test_clock_request_uses_issued_cursor_only(monkeypatch):
    _, _, receipt, _, client = make_case(monkeypatch)
    calls = []
    monkeypatch.setattr(client, 'clock_observation', GatewayMarketSubscriptionClient.clock_observation.__get__(client))
    monkeypatch.setattr(client, '_call', lambda method, args, *_: calls.append((method, args)))
    client.clock_observation(receipt=receipt)
    assert calls == [('clock_observation', {'epoch': receipt.stream_epoch,
                    'sequence': receipt.sequence, 'message_id': receipt.message_id})]
    with pytest.raises(ValueError, match='INVALID_MARKET_RECEIPT'):
        client.clock_observation(receipt=replace(receipt))
    assert len(calls) == 1


def test_unreliable_source_clock_and_stale_event_fail_closed(monkeypatch):
    qualifier, event, receipt, observation, _ = make_case(monkeypatch)
    observation['source_calibration']['error_bound_ns'] = 900_000_000
    assert qualifier.qualify(event, receipt).reason == 'SOURCE_CLOCK_INVALID'
    observation['source_calibration']['error_bound_ns'] = 2_000_000
    observation['service_generation'] = 3
    assert qualifier.qualify(event, receipt).reason == 'SOURCE_CLOCK_INVALID'
    observation['service_generation'] = 'owner-generation-3'
    observation['remaining_ns'] = 1
    assert qualifier.qualify(event, receipt).reason == 'SOURCE_CLOCK_EXPIRED'
    observation['remaining_ns'] = 200_000_000_000
    observation['source_observed_wall_ns'] -= 8_000_000_000
    assert qualifier.qualify(event, receipt).reason == 'CLOCK_DOMAINS_INCONSISTENT'


def test_wall_jump_and_missing_local_calibration_fail_closed(monkeypatch):
    qualifier, event, receipt, _, _ = make_case(monkeypatch)
    monkeypatch.setattr('bt_api_py.clock_verification.time.time_ns', lambda: receipt.received_wall_ns + 9_000_000_000)
    assert qualifier.qualify(event, receipt).reason == 'CLIENT_CLOCK_UNAVAILABLE'


def test_untrusted_query_callback_cannot_be_accepted_as_sntp():
    with pytest.raises(ValueError, match='INVALID_GATEWAY_CLOCK_VERIFIER'):
        GatewayClockVerifier(ClockVerifier(servers=('fake',), query=lambda *_: (0, 0)))


def test_empty_poll_keeps_latest_issued_frame_but_failed_poll_clears_it(monkeypatch):
    _, _, _, _, client = make_case(monkeypatch)
    previously_issued = client._issued_received_event
    monkeypatch.setattr(client, '_call', lambda *_args, **_kwargs: None)
    assert client.poll_with_receipt() is None
    assert client._issued_received_event is previously_issued
    def fail(*_args, **_kwargs):
        raise RuntimeError('synthetic failure')
    monkeypatch.setattr(client, '_call', fail)
    with pytest.raises(RuntimeError, match='synthetic failure'):
        client.poll_with_receipt()
    assert client._issued_received_event is None


def test_explicit_background_clock_maintenance_can_stop(monkeypatch):
    monkeypatch.setattr('bt_api_py.clock_verification._sntp_query', lambda *_: (0, 1_000_000))
    clock = GatewayClockVerifier(ClockVerifier(servers=('synthetic',)))
    clock.start_background_refresh(interval_seconds=1)
    deadline = time.monotonic() + 2
    while clock._sample is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert clock._sample is not None
    assert clock.stop_background_refresh(timeout_seconds=2)
    assert clock.evidence_for(time.time_ns(), time.monotonic_ns()) is None


def test_background_clock_start_failure_can_be_retried(monkeypatch):
    monkeypatch.setattr('bt_api_py.clock_verification._sntp_query', lambda *_: (0, 1_000_000))
    clock = GatewayClockVerifier(ClockVerifier(servers=('synthetic',)))
    with monkeypatch.context() as context:
        context.setattr(threading.Thread, 'start', lambda _self: (_ for _ in ()).throw(RuntimeError('no thread')))
        with pytest.raises(RuntimeError, match='no thread'):
            clock.start_background_refresh(interval_seconds=1)
    clock.start_background_refresh(interval_seconds=1)
    assert clock.stop_background_refresh(timeout_seconds=2)
