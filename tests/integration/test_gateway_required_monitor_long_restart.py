"""Real CURVE Monitor restart with synthetic Gateway journal and no account I/O."""
import multiprocessing
import time
from contextlib import suppress

import pytest
import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.gateway_direct_owner import GatewayDirectOwner, GatewayDirectOwnerResources
from bt_api_py.gateway_required_monitor import RequiredMonitor, RequiredMonitorSettings
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from test_gateway_heartbeat_host import _ACCOUNT_SCOPE, _INGRESS_ID, _INGRESS_PRINCIPAL, _endpoint, _producer_config
from test_gateway_required_monitor_integration import _start_monitor, _stop_monitor, _wait


@pytest.mark.parametrize('scenario', ['automatic', 'manual', 'stop_retry'])
def test_long_monitor_outage_then_graceful_restart(tmp_path, monkeypatch, caplog, scenario):
    import bt_api_py.gateway_required_monitor as subject

    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    keys = {'server_public': server_public, 'server_secret': server_secret,
            'client_public': client_public, 'client_secret': client_secret}
    endpoint = _endpoint()
    path = tmp_path / 'monitor-registry.sqlite'
    ctx = multiprocessing.get_context('spawn')
    parent, process = _start_monitor(ctx, endpoint, path, keys)
    root = tmp_path / 'guard'
    root.mkdir()
    identity = WireIdentity(WireRole.SERVICE, 'synthetic-producer-a', 'producer-a-service')
    peer = WireIdentity(WireRole.SERVICE, _INGRESS_PRINCIPAL, _INGRESS_ID)
    runtimes, failures, holder = [], [], {}
    classify = subject._startup_category_for

    def record(error):
        failures.append((type(error).__name__, getattr(error, 'code', None)))
        return classify(error)

    monkeypatch.setattr(subject, '_startup_category_for', record)

    def runtime_factory():
        config = _producer_config(endpoint).to_public_dict()
        config['clients']['monitor']['settings']['options']['heartbeat_interval_seconds'] = 1
        config['clients']['monitor']['settings']['options']['request_timeout_ms'] = 250
        config['communication']['profiles']['monitor-connect']['options']['request_timeout_ms'] = 250
        runtime = ServiceClientRuntime(parse_service_config(config), identity=identity,
            scope_resolver=lambda _: _ACCOUNT_SCOPE, peer_resolver=lambda _: peer,
            transport_factory=ZmqTransportFactory({'producer-client-v1': ClientCurveKeys(
                server_public, client_public, client_secret)}),
            requested_operations={'upstream.telemetry': frozenset({'monitor.heartbeat'})})
        runtimes.append(runtime)
        return runtime

    settings = RequiredMonitorSettings(runtime_factory, identity, peer, _ACCOUNT_SCOPE,
        interval_seconds=0.1, timeout_seconds=0.8, automatic_resume=scenario != 'manual')

    def create_resources():
        guard = LocalAccountSessionGuard(root, _ACCOUNT_SCOPE.account_ref, 'owner')
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal.create(tmp_path / 'gateway.sqlite', _ACCOUNT_SCOPE.account_ref,
                                                      generation=generation)
        control = journal.enable_controls(guard)
        dispatcher = GatewayDirectDispatcher(journal, 'SYN', guard, lambda *_: None, lambda _: ('key',), lambda _: True)
        required = RequiredMonitor(settings, generation)
        required.initialize_on_owner(journal, control, guard)
        holder.update(required=required, control=control)
        return GatewayDirectOwnerResources(dispatcher, journal, guard.release)

    owner = GatewayDirectOwner(create_resources, queue_size=8, max_queue_wait_seconds=2, busy_timeout_seconds=1)
    try:
        assert owner.start(5)
        required, control = holder['required'], holder['control']
        def owner_call(callback):
            return owner.submit_service_call(callback).result(timeout_seconds=2)
        required.start(owner, control)
        if scenario == 'manual':
            cause, = owner_call(lambda: control.active_system_causes(required.origin))
            owner_call(lambda: control.resume(cause['cause_id'], expected_version=cause['version'],
                                              operation_id='initial-manual-release'))
        _wait(required.gate, 'initial heartbeat')
        old_epoch = runtimes[0].bindings[0].service_epoch
        _stop_monitor(parent, process)
        parent = process = None
        _wait(lambda: not required.gate(), 'outage detected')
        _wait(lambda: bool(owner_call(lambda: control.active_system_causes(required.origin))), 'outage cause')
        _wait(lambda: required.diagnostic['phase'] == 'RECONCILING', 'old session retained', seconds=6)
        _wait(lambda: runtimes[0].state == 'monitor_cleanup_pending'
              and runtimes[0].unregister_uncertainties == 1, 'unconfirmed unregister retained', seconds=6)
        assert required._thread.is_alive() and len(runtimes) == 1
        assert runtimes[0].state == 'monitor_cleanup_pending'
        assert runtimes[0].open_transport_count == 1
        assert required.diagnostic['cleanup_kind'] == 'REMOTE_PENDING'
        messages = [record.getMessage() for record in caplog.records if record.name == subject.__name__]
        assert any('phase=RECONCILING' in message and 'cleanup=REMOTE_PENDING' in message
                   for message in messages)
        assert all(endpoint not in message and _ACCOUNT_SCOPE.account_ref not in message
                   and identity.principal_ref not in message for message in messages)
        if scenario == 'stop_retry':
            assert not required.close(0.1)
            assert required._thread.is_alive() and runtimes[0].open_transport_count == 1
            assert len(runtimes) == 1
        parent, process = _start_monitor(ctx, endpoint, path, keys)
        if scenario == 'stop_retry':
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not required.close(0.5):
                pass
            assert required.close(0.5)
            assert len(runtimes) == 1 and runtimes[0].open_transport_count == 0
            assert runtimes[0].unregister_uncertainties == 0
            assert owner_call(lambda: control.active_system_causes(required.origin))
            return
        if scenario == 'manual':
            def fresh_proof():
                with required.gate._lock:
                    return required.gate._proof is not None and time.monotonic_ns() < required.gate._deadline
            _wait(fresh_proof, 'authenticated new epoch proof', seconds=8)
            assert not required.gate()
            cause, = owner_call(lambda: control.active_system_causes(required.origin))
            owner_call(lambda: control.resume(cause['cause_id'], expected_version=cause['version'],
                                              operation_id='outage-manual-release'))
        deadline = time.monotonic() + 8
        while not required.gate() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not required.gate():
            pytest.fail(f'worker_alive={required._thread.is_alive()} cleanup_uncertain={required._cleanup_uncertain} '
                        f'runtime_count={len(runtimes)} failures={sorted(set(failures))} '
                        f'runtime_state={[r.state for r in runtimes]} '
                        f'unregister_uncertainties={[r.unregister_uncertainties for r in runtimes]} '
                        f'unknown_registration_count={[r.unknown_registration_count for r in runtimes]} '
                        f'open_transports={[r.open_transport_count for r in runtimes]}')
        assert not owner_call(lambda: control.active_system_causes(required.origin))
        assert len(runtimes) == 2
        assert runtimes[1].bindings[0].service_epoch != old_epoch
        assert runtimes[0].state == 'closed' and runtimes[0].unregister_uncertainties == 0
        assert runtimes[0].unknown_registration_count == 0 and runtimes[0].open_transport_count == 0
    finally:
        if 'required' in holder:
            with suppress(Exception):
                holder['required'].close(4)
        with suppress(Exception):
            owner.close(4)
        if process is not None:
            _stop_monitor(parent, process)
