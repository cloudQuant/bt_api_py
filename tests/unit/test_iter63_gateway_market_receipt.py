"""Local market receipts preserve cursor association and never confer authority."""
from dataclasses import asdict, replace
import os
import threading
import time
from types import MappingProxyType

import pytest
from bt_api_base.service_protocol import envelope_to_wire_mapping

from bt_api_py.gateway_strategy_market import _clock_observation_from_wire, _received_event_from_wire
from bt_api_py.service_market import MarketReceiveReceipt, ServiceMarketError
from bt_api_transport_zmq.service_v2 import ServiceReplyReceipt
from tests.unit.test_service_market import _KEY, _close_all, _make_client, _test_source_descriptor


def _receipt(event, *, source='pub'):
    return MarketReceiveReceipt(source, event.header.stream_id, event.header.stream_epoch,
        event.header.sequence, event.header.message_id, time.time_ns(), time.monotonic_ns(),
        os.getpid(), threading.get_ident(), *(() if source == 'pub' else ('request', 'reply')))


def test_exact_receipt_roundtrip_and_rejection_of_cross_frame(tmp_path):
    client, runtime, registry, market, _, _ = _make_client(tmp_path)
    try:
        client.start()
        event = market.publish(_KEY, 'one')
    finally:
        _close_all(client, runtime, registry)
    receipt = _receipt(event)
    result = _received_event_from_wire({'event': envelope_to_wire_mapping(event), 'receipt': asdict(receipt)})
    assert result.event == event and result.receipt == receipt
    for altered in (replace(receipt, sequence=2), replace(receipt, pid=os.getpid()+1),
                    replace(receipt, source='replay'), replace(receipt, received_monotonic_ns=time.monotonic_ns()+10**12)):
        with pytest.raises(ValueError, match='INVALID_MARKET_RECEIPT'):
            _received_event_from_wire({'event': envelope_to_wire_mapping(event), 'receipt': asdict(altered)})
    assert _received_event_from_wire({'event': envelope_to_wire_mapping(event), 'receipt': None}).receipt is None


def test_queue_keeps_each_receipt_with_its_own_event(tmp_path):
    client, runtime, registry, market, _, _ = _make_client(tmp_path)
    try:
        client.start()
        first = market.publish(_KEY, 'one')
        second = market.publish(_KEY, 'two')
        one, two = _receipt(first), _receipt(second, source='replay')
        assert client._enqueue(first, receipt=one)
        assert client._enqueue(second, receipt=two)
        assert client.poll_with_receipt(0) == (first, one)
        assert client.poll_with_receipt(0) == (second, two)
        assert client._last_delivered_receipt is two
        client.poll_with_receipt(0)
        assert client._last_delivered_receipt is None
    finally:
        _close_all(client, runtime, registry)


def test_clock_observation_binds_same_subscription_registration_and_reply(tmp_path, monkeypatch):
    client, runtime, registry, market, _, _ = _make_client(tmp_path, require_source_descriptor=True)
    descriptor = _test_source_descriptor()
    market.source_descriptor = descriptor
    try:
        client.start()
        current = client._binding
        binding = replace(current, rpc_scope=replace(current.rpc_scope, account_ref='account'),
                          operations=current.operations | {'market.clock_observation'})
        original_binding, original_rpc = client._check_binding, client._rpc
        monkeypatch.setattr(client, '_check_binding', lambda: binding)
        calls = []
        def rpc(operation, arguments, **kwargs):
            calls.append((operation, arguments, kwargs))
            receipt = ServiceReplyReceipt('request', 'reply', 'request', operation,
                time.time_ns(), time.monotonic_ns(), os.getpid(), threading.get_ident())
            source = descriptor.to_dict()
            source['key'] = MappingProxyType(source['key'])
            return MappingProxyType({'market_source': MappingProxyType(source),
                                     'account_scope': 'account'}), receipt
        monkeypatch.setattr(client, '_rpc', rpc)
        result = client.clock_observation()
        assert result.source_descriptor == descriptor and result.account_ref == 'account'
        assert calls[0][0] == 'market.clock_observation'
        assert calls[0][1] == {'key': _KEY.to_dict()}
        assert calls[0][2]['include_receive_receipt'] is True
        client.clock_observation(epoch=descriptor.epoch, sequence=5, message_id='frame-5')
        assert calls[1][1] == {'key': _KEY.to_dict(), 'epoch': descriptor.epoch,
                               'sequence': 5, 'message_id': 'frame-5'}
        for bad in ({'epoch': 'wrong', 'sequence': 5, 'message_id': 'frame-5'},
                    {'epoch': descriptor.epoch, 'sequence': True, 'message_id': 'frame-5'},
                    {'epoch': descriptor.epoch, 'sequence': 5}):
            with pytest.raises(ServiceMarketError, match='CLOCK_EVIDENCE_INVALID'):
                client.clock_observation(**bad)
        assert len(calls) == 2
        raw = {'observation': result.observation, 'receipt': asdict(result.receipt),
               'request_started_monotonic_ns': result.request_started_monotonic_ns,
               'registration_id': result.registration_id, 'service_epoch': result.service_epoch,
               'account_ref': result.account_ref, 'source_descriptor': descriptor.to_dict()}
        wrapped = _clock_observation_from_wire(raw)
        assert wrapped.source_descriptor == descriptor and wrapped.account_ref == 'account'
        raw['receipt']['correlation_id'] = 'different-request'
        with pytest.raises(ValueError, match='INVALID_GATEWAY_CLOCK_OBSERVATION'):
            _clock_observation_from_wire(raw)
        monkeypatch.setattr(client, '_rpc', lambda *_args, **_kwargs: (
            {'market_source': descriptor.to_dict(), 'account_scope': 'another-account'},
            replace(result.receipt, received_wall_ns=time.time_ns(), received_monotonic_ns=time.monotonic_ns())))
        with pytest.raises(ServiceMarketError, match='CLOCK_EVIDENCE_SCOPE'):
            client.clock_observation()
    finally:
        if 'original_binding' in locals():
            monkeypatch.setattr(client, '_check_binding', original_binding)
            monkeypatch.setattr(client, '_rpc', original_rpc)
        _close_all(client, runtime, registry)
