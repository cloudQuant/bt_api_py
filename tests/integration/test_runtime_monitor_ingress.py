"""Real standalone Monitor host and bounded Runtime relay observations."""
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_monitor.durable import DurableOutbox
from bt_api_run_contract import EventKind, OperatorRole
from bt_api_runtime.control_auth import OperatorIdentity, SingleOperatorAuthenticator
from bt_api_runtime.control_ledger import ControlDatabase
from bt_api_runtime.control_service import ExecutionResult, RuntimeControlService
from bt_api_runtime.events import EventStore
from bt_api_runtime.supervision import SystemClock
from bt_api_runtime.telemetry_relay import TelemetryRelay
from bt_api_py.runtime_monitor_bridge import RuntimeMonitorBridge, RuntimeMonitorBridgeError
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime import ClientCurveKeys
from tests.integration import test_monitor_service_process as monitor_fixture

_SCOPE = WireScope('synthetic-domain', account_ref='synthetic-account')
_TYPE = 'runtime.command_accepted.v1'


class Clock:
    def utc_now(self):
        return datetime.now(timezone.utc).isoformat()


def _bridge(doc, keys, store, *, scope=_SCOPE, peer=None):
    listener = doc['ingress']
    raw_runtime = monitor_fixture.client(listener, keys['events'][0], keys['producer'],
                                         'producer', 'events.append')
    return RuntimeMonitorBridge(
        config=parse_service_config(raw_runtime._config),
        identity=WireIdentity(WireRole.SERVICE, 'producer', 'producer-service'),
        scope=scope, scope_ref=listener['scope_ref'],
        expected_peer=peer or WireIdentity(WireRole.SERVICE, listener['principal_ref'], listener['service_id']),
        curve_keys=ClientCurveKeys(keys['events'][0], *keys['producer']),
        event_store=store, run_id='runtime-run', timeout_seconds=2)


def _deployment(tmp_path):
    path, doc, keys = monitor_fixture.deployment(tmp_path)
    doc['storage']['event_types'] = [_TYPE]
    path.write_text(yaml.safe_dump(doc))
    return path, doc, keys


def _rows(tmp_path):
    rows, _ = DurableOutbox(tmp_path / 'data' / 'events.sqlite', read_only=True).read_page('events', _TYPE, 0, 20)
    return rows


def _control(tmp_path, store, relay):
    """Real control service/ledger with a local synthetic execution endpoint."""
    unit = SimpleNamespace(name='alpha', strategy_dir=str(tmp_path), access=SimpleNamespace(value='direct'))
    class Config:
        def __init__(self):
            self.units = (unit,)
        def unit(self, name):
            return unit
    class Supervisor:
        event_sink = None
        def close(self):
            pass
        def read_identity(self, name):
            return None
    clock = SystemClock()
    supervisor = Supervisor()
    service = RuntimeControlService(
        Config(), supervisor=supervisor, database=ControlDatabase(str(tmp_path), clock), clock=clock,
        authenticator=SingleOperatorAuthenticator(OperatorIdentity('alice', OperatorRole.OPERATOR, ('*',))),
        recover_in_flight=False, events=store, telemetry=relay)
    def execute(**kwargs):
        store.append(EventKind.COMMAND_ACCEPTED)
        return ExecutionResult('APPLIED', 'applied')
    service._execute = execute
    return service


def test_runtime_relay_durable_ingress_and_epoch(tmp_path):
    path, doc, keys = _deployment(tmp_path)
    process = monitor_fixture.start(path)
    bridge = None
    first = EventStore(tmp_path / 'first', Clock())
    second = None
    try:
        denied = _bridge(doc, keys, first, scope=WireScope('synthetic-domain', account_ref='other-account'))
        with pytest.raises(RuntimeMonitorBridgeError, match='START_FAILED'):
            denied.start()
        assert denied.state == 'close_uncertain'
        with pytest.raises(RuntimeMonitorBridgeError, match='CLEANUP_UNCERTAIN'):
            denied.close()
        peer = WireIdentity(WireRole.SERVICE, 'wrong-peer', doc['ingress']['service_id'])
        denied_peer = _bridge(doc, keys, first, peer=peer)
        with pytest.raises(RuntimeMonitorBridgeError, match='START_FAILED'):
            denied_peer.start()
        with pytest.raises(RuntimeMonitorBridgeError, match='CLEANUP_UNCERTAIN'):
            denied_peer.close()
        bridge = _bridge(doc, keys, first)
        assert bridge.state == 'new'
        bridge.start()
        assert bridge.state == 'running'
        with pytest.raises(RuntimeMonitorBridgeError, match='UNAVAILABLE'):
            bridge.start()
        relay = TelemetryRelay(bridge, enabled=True, slow_call_s=5)
        first.relay = relay
        event = first.append(EventKind.COMMAND_ACCEPTED, unit_name='private-unit',
                             detail={'action': 'private-token', 'attempts': 2})
        assert relay.poll() == 1
        assert bridge.append(event) == 'accepted'
        rows = _rows(tmp_path)
        assert len(rows) == 1
        assert rows[0].event.data['payload'] == {'schema': 'sdk.runtime_observation.v1', 'sequence': 1, 'detail': {'attempts': 2}}
        assert 'private' not in rows[0].event.data_json
        bridge.close()
        assert bridge.state == 'closed'
        bridge.close()
        with pytest.raises(RuntimeMonitorBridgeError, match='UNAVAILABLE'):
            bridge.start()
        bridge = _bridge(doc, keys, first)
        bridge.start()
        assert bridge.append(event) == 'accepted'
        assert len(_rows(tmp_path)) == 1
        bridge.close()
        second = EventStore(tmp_path / 'second', Clock())
        bridge = _bridge(doc, keys, second)
        bridge.start()
        new_event = second.append(EventKind.COMMAND_ACCEPTED)
        assert new_event.sequence == event.sequence
        with pytest.raises(RuntimeMonitorBridgeError, match='DELIVERY_UNAVAILABLE'):
            bridge.append(event)
        assert bridge.append(new_event) == 'accepted'
        rows = _rows(tmp_path)
        assert len(rows) == 2
        assert rows[0].event.data['source_event_id'] != rows[1].event.data['source_event_id']
        bridge.close()
        bridge = None
    finally:
        if bridge is not None:
            bridge.close()
        first.close()
        if second is not None:
            second.close()
        monitor_fixture.stop(process)


@pytest.mark.parametrize('failure', ['bad_ack', 'lost_reply'])
def test_real_send_with_bad_or_lost_reply_is_not_retried(tmp_path, failure):
    path, doc, keys = _deployment(tmp_path)
    process = monitor_fixture.start(path)
    store = EventStore(tmp_path / 'events', Clock())
    bridge = _bridge(doc, keys, store)
    try:
        bridge.start()
        original = bridge._runtime.call_bound
        calls = []
        def disturbed(*args, **kwargs):
            calls.append(1)
            result = original(*args, **kwargs)  # actual durable CURVE call before fault injection
            if failure == 'lost_reply':
                raise TimeoutError('synthetic lost response')
            return replace(result, result={'source_event_id': 'wrong', 'event_id': 'bad', 'sequence': 1})
        bridge._runtime.call_bound = disturbed
        relay = TelemetryRelay(bridge, enabled=True, cooldown_s=0, slow_call_s=5)
        store.relay = relay
        assert store.append(EventKind.COMMAND_ACCEPTED) is not None
        assert relay.poll() == 0
        assert relay.poll() == 0
        assert len(calls) == 1
        assert len(_rows(tmp_path)) == 1
        assert relay.stats()['drop_reasons']['monitor_error'] == 1
    finally:
        bridge.close()
        store.close()
        monitor_fixture.stop(process)


def test_host_outage_does_not_block_control(tmp_path):
    path, doc, keys = _deployment(tmp_path)
    process = monitor_fixture.start(path)
    store = EventStore(tmp_path / 'events', Clock())
    bridge = _bridge(doc, keys, store)
    relay = TelemetryRelay(bridge, enabled=True, cooldown_s=0)
    service = None
    try:
        bridge.start()
        service = _control(tmp_path / 'control', store, relay)
        monitor_fixture.stop(process)
        process = None
        assert service.unit_action('start', 'alpha', reason='test').status == 'APPLIED'
        assert relay.stats()['queued'] > 0
        assert relay.poll() == 0
        assert relay.stats()['drop_reasons']['monitor_error'] == 1
        assert service.unit_action('stop', 'alpha', reason='test').status == 'APPLIED'
    finally:
        if service is not None:
            service.close()
        relay.close()
        try:
            bridge.close()
        except RuntimeMonitorBridgeError as error:
            assert str(error) == 'CLEANUP_UNCERTAIN'
        store.close()
        if process is not None:
            monitor_fixture.stop(process)
