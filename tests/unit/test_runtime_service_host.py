import socket
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from bt_api_run_contract import (
    AccessMode,
    ApiConfig,
    GatewayConfig,
    IdentityState,
    MonitorEventsConfig,
    OperatorConfig,
    OperatorRole,
    RestartConfig,
    StopConfig,
    UnitConfig,
    UnitKind,
    ValidatedRuntimeConfig,
)
from bt_api_runtime.control_auth import OperatorIdentity, SingleOperatorAuthenticator
from bt_api_runtime.control_http import ControlTransportError
from bt_api_runtime.events import EventStore
from bt_api_runtime.supervision import IdentityRead, SystemClock, UnitIdentity

import bt_api_py.runtime_operator_cli as operator_cli
import bt_api_py.runtime_operator_config as operator_config_module
import bt_api_py.runtime_service_host as module


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def make_unit(tmp_path, *, name, access=AccessMode.DIRECT):
    directory = tmp_path / ('strategy_' + name)
    directory.mkdir(parents=True, exist_ok=True)
    entry = directory / 'run.py'
    entry.write_text('# synthetic strategy\n', encoding='utf-8')
    gateway = GatewayConfig(str(tmp_path / ('gateway_' + name))) if access is AccessMode.GATEWAY else None
    return UnitConfig(name, UnitKind.STRATEGY, str(directory), str(entry), sys.executable, (), access,
                      1.0, 10.0, StopConfig(5.0, 1.0, 1.0 if gateway else None),
                      RestartConfig(), gateway=gateway)


def make_config(tmp_path, unit):
    return ValidatedRuntimeConfig('bt_api_runtime/v1', str(tmp_path / 'runtime_home'),
        ApiConfig('http_json', '127.0.0.1', free_port(), 'subject_token'),
        (OperatorConfig('alice', OperatorRole.OPERATOR, ('*',)),), MonitorEventsConfig(False), (unit,))


def setup_host(tmp_path, monkeypatch, *, gateway=False, monitor=False,
               recover_runs=True, recover_in_flight=True, viewer=False):
    unit = make_unit(tmp_path, name='alpha', access=AccessMode.GATEWAY if gateway else AccessMode.DIRECT)
    config = make_config(tmp_path, unit)
    config = replace(config, api=replace(config.api, port=free_port()),
                     monitor_events=MonitorEventsConfig(enabled=monitor))
    store = EventStore(config.home, SystemClock())
    role = OperatorRole.VIEWER if viewer else OperatorRole.OPERATOR
    auth = SingleOperatorAuthenticator(OperatorIdentity('alice', role, ('*',)))

    class Gateway:
        def __init__(self):
            self.calls = []
            self.closed = 0
            self.state = 'new'
            self._entries = {'one': object()}
        def client_for_strategy(self, config, strategy):
            gateway = self
            class Client:
                def pause(self, unit, **kwargs):
                    gateway.calls.append((unit.name, kwargs['strategy_uuid']))
                    return SimpleNamespace(status='APPLIED')
            return Client()
        def start(self):
            self.state = 'running'
            return self
        def close(self):
            self.closed += 1
            self.state = 'closed'
    class Monitor:
        def __init__(self):
            self._store = store
            self.entered, self.release = threading.Event(), threading.Event()
            self.owner = None
            self.closed = 0
            self.state = 'new'
        def start(self):
            self.state = 'running'
            return SimpleNamespace(healthy=True)
        def append(self, event):
            self.owner = threading.get_ident()
            self.entered.set()
            self.release.wait(5)
            return 'accepted'
        def close(self):
            self.closed += 1
            self.state = 'closed'
    monkeypatch.setattr(module, 'RuntimeGatewayControlBridge', Gateway)
    monkeypatch.setattr(module, 'RuntimeMonitorBridge', Monitor)
    gw, mon = (Gateway() if gateway else None), (Monitor() if monitor else None)
    host = module.RuntimeServiceHost(config, authenticator=auth, gateway_bridge=gw,
        monitor_bridge=mon, event_store=store, unit_strategies={'alpha': 'one'} if gateway else {},
        close_timeout=0.2, poll_interval=0.01, recover_runs=recover_runs,
        recover_in_flight=recover_in_flight)
    return host, store, unit, gw, mon


def test_preserve_history_disables_both_startup_recovery_paths(tmp_path, monkeypatch):
    observed = {}
    original = module.build_control_service

    def capture(*args, **kwargs):
        observed.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, 'build_control_service', capture)
    host, store, _, _, _ = setup_host(
        tmp_path,
        monkeypatch,
        recover_runs=False,
        recover_in_flight=False,
    )
    try:
        host.start()
        assert observed['recover_runs'] is False
        assert observed['recover_in_flight'] is False
    finally:
        host.close()
        store.close()


def test_default_host_keeps_both_startup_recovery_paths_enabled(tmp_path, monkeypatch):
    observed = {}
    original = module.build_control_service

    def capture(*args, **kwargs):
        observed.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, 'build_control_service', capture)
    host, store, _, _, _ = setup_host(tmp_path, monkeypatch)
    try:
        host.start()
        assert observed['recover_runs'] is True
        assert observed['recover_in_flight'] is True
    finally:
        host.close()
        store.close()


def test_observe_only_viewer_cannot_start_a_unit_or_create_run_state(tmp_path, monkeypatch):
    host, store, unit, _, _ = setup_host(
        tmp_path,
        monkeypatch,
        recover_runs=False,
        recover_in_flight=False,
        viewer=True,
    )
    try:
        host.start()
        listed = host._service.dispatch('runtime.units.list')
        denied = host._service.dispatch(
            'runtime.unit.start',
            unit_name=unit.name,
            command_id='observe-only-start-denied',
        )
        assert listed.status == 'OK'
        assert denied.status == 'REJECTED'
        assert denied.code == 'not_authorized'
        assert host._supervisor.store.load_run(unit.name) is None
    finally:
        host.close()
        store.close()


def test_mux_checks_declaration_and_current_supervisor_identity(tmp_path, monkeypatch):
    host, store, unit, bridge, _ = setup_host(tmp_path, monkeypatch, gateway=True)
    try:
        host.start()
        identity = lambda value: IdentityRead(IdentityState.PRESENT,
            UnitIdentity(value, 'host', unit.strategy_dir), 'identity_present')
        host._supervisor.read_identity = lambda name: identity('one')
        args = dict(strategy_uuid='one', operator='alice', command_id='id', reason='', freeze=False)
        assert host._mux.pause(unit, **args).status == 'APPLIED'
        assert host._mux.pause(replace(unit, name='other'), **args).status == 'REJECTED'
        assert host._mux.pause(unit, **{**args, 'strategy_uuid': 'two'}).status == 'REJECTED'
        host._supervisor.read_identity = lambda name: identity('two')
        assert host._mux.pause(unit, **args).status == 'REJECTED'
        assert host._mux.drain(unit, strategy_uuid='one', command_id='id', reason='', cause_id='x').accepted is False
        assert bridge.calls == [('alpha', 'one')]
    finally:
        host.close()
        assert store.available
        store.close()


def test_gateway_declarations_match_config_and_bridge_exactly(tmp_path, monkeypatch):
    host, store, _, bridge, _ = setup_host(tmp_path, monkeypatch, gateway=True)
    try:
        args = dict(authenticator=host.authenticator, gateway_bridge=bridge,
                    monitor_bridge=None, event_store=store)
        for bindings in ({}, {'other': 'one'}, {'alpha': 'two'}):
            with pytest.raises(ValueError):
                module.RuntimeServiceHost(host.config, unit_strategies=bindings, **args)
        bridge._entries['extra'] = object()
        with pytest.raises(ValueError):
            module.RuntimeServiceHost(host.config, unit_strategies={'alpha': 'one'}, **args)
        assert bridge.closed == 0 and store.available
    finally:
        store.close()


def test_failed_start_releases_lock_and_borrowed_store(tmp_path, monkeypatch):
    host, store, _, bridge, _ = setup_host(tmp_path, monkeypatch, gateway=True)
    def fail(*args, **kwargs):
        raise RuntimeError('listener failed')
    monkeypatch.setattr(module, 'build_control_http_server', fail)
    with pytest.raises(RuntimeError, match='listener failed'):
        host.start()
    assert host.state == 'closed' and bridge.closed == 1 and store.available
    assert store.relay is None
    supervisor = module.RuntimeSupervisor(host.config, process=module.PosixProcessAdapter(),
                                          clock=SystemClock())
    supervisor.close()
    store.close()


def test_blocked_monitor_never_blocks_producer_and_close_is_retryable(tmp_path, monkeypatch):
    host, store, _, _, monitor = setup_host(tmp_path, monkeypatch, monitor=True)
    try:
        host.start()
        assert host._relay.enqueue(object())
        assert monitor.entered.wait(2)
        assert monitor.owner != threading.get_ident()
        before = time.monotonic()
        assert host._relay.enqueue(object())
        assert time.monotonic() - before < 0.5
        with pytest.raises(RuntimeError, match='telemetry_owner'):
            host.close()
        assert host.state == 'close_uncertain' and monitor.closed == 0 and store.available
        assert host.cleanup_components == ('telemetry_owner',)
        monitor.release.set()
        host.close()
        assert host.state == 'closed' and monitor.closed == 1 and store.relay is None
        assert host.cleanup_components == ()
    finally:
        monitor.release.set()
        if host.state != 'closed':
            host.close()
        store.close()


def test_poll_failure_is_diagnostic_and_does_not_block_http_or_close(tmp_path, monkeypatch):
    from bt_api_run_contract import ControlOperation, ControlRequest
    from bt_api_runtime.control_http import HttpControlClient

    host, store, _, _, monitor = setup_host(tmp_path, monkeypatch, monitor=True)
    attempted = threading.Event()
    def fail_poll(self):
        attempted.set()
        raise RuntimeError('private diagnostic must not escape')
    monkeypatch.setattr(module.TelemetryRelay, 'poll', fail_poll)
    try:
        host.start()
        assert attempted.wait(2)
        host._poll_thread.join(2)
        assert host.telemetry_status == 'poll_failed'
        client = HttpControlClient(host='127.0.0.1', port=host.bound_address[1], token='test')
        assert client.request(ControlRequest(ControlOperation.UNITS_LIST)).status == 'OK'
        host.close()
        assert host.state == 'closed' and host.telemetry_status == 'poll_failed'
        assert monitor.closed == 1 and store.available
        host.close()
    finally:
        if host.state != 'closed':
            host.close()
        store.close()


def test_blocked_dispatch_keeps_home_lock_until_retry(tmp_path, monkeypatch):
    from bt_api_runtime.supervision.persistence import SingleInstanceLock
    host, store, _, _, _ = setup_host(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    thread = None
    try:
        host.start()
        def blocked(*args, **kwargs):
            entered.set()
            release.wait(5)
        host._service.dispatch = blocked
        thread = threading.Thread(target=host._gate.dispatch)
        thread.start()
        assert entered.wait(2)
        with pytest.raises(RuntimeError, match='http_dispatch'):
            host.close()
        assert 'http_dispatch' in host.cleanup_components
        lock = SingleInstanceLock(str(Path(host.config.home) / 'runtime.lock'))
        with pytest.raises(Exception, match='single_instance_lock_held'):
            lock.acquire()
        release.set()
        thread.join(2)
        host.close()
        assert host.cleanup_components == ()
        lock.acquire()
        lock.release()
    finally:
        release.set()
        if thread is not None:
            thread.join(2)
        if host.state != 'closed':
            host.close()
        store.close()


def test_partial_bridge_start_cleanup_uncertainty_is_retryable(tmp_path, monkeypatch):
    host, store, _, bridge, _ = setup_host(tmp_path, monkeypatch, gateway=True)
    def fail_start():
        raise RuntimeError('start failed')
    def close():
        bridge.closed += 1
        if bridge.closed == 1:
            raise RuntimeError('cleanup failed')
        bridge.state = 'closed'
    bridge.start, bridge.close = fail_start, close
    try:
        with pytest.raises(RuntimeError, match='START_CLEANUP_UNCERTAIN'):
            host.start()
        assert host.state == 'close_uncertain' and store.available
        host.close()
        assert host.state == 'closed' and bridge.closed == 2
    finally:
        if host.state != 'closed':
            host.close()
        store.close()


def test_cleanup_components_only_describe_latest_attempt(tmp_path, monkeypatch):
    host, store, _, gateway, monitor = setup_host(tmp_path, monkeypatch, gateway=True, monitor=True)
    try:
        host.start()
        def fail_monitor():
            monitor.closed += 1
            if monitor.closed == 1:
                raise RuntimeError('private monitor detail')
            monitor.state = 'closed'

        def fail_gateway():
            gateway.closed += 1
            if gateway.closed <= 2:
                raise RuntimeError('private gateway detail')
            gateway.state = 'closed'

        monitor.close, gateway.close = fail_monitor, fail_gateway
        with pytest.raises(RuntimeError):
            host.close()
        assert host.cleanup_components == ('monitor', 'gateway')
        with pytest.raises(RuntimeError):
            host.close()
        assert host.cleanup_components == ('gateway',)
        host.close()
        assert host.cleanup_components == () and host.state == 'closed'
    finally:
        if host.state != 'closed':
            host.close()
        store.close()


def test_http_socket_close_failure_retries_same_listener(tmp_path, monkeypatch):
    host, store, _, _, _ = setup_host(tmp_path, monkeypatch)
    try:
        host.start()
        http = host._http
        listener = http._thread
        server = http._server
        original_close = server.server_close
        attempts = []

        def fail_once():
            attempts.append(server)
            if len(attempts) == 1:
                raise OSError('synthetic socket close failure')
            original_close()

        monkeypatch.setattr(server, 'server_close', fail_once)
        with pytest.raises(RuntimeError, match='http_listener'):
            host.close()
        assert host.state == 'close_uncertain'
        assert host.cleanup_components == ('http_listener',)
        assert host._http is http and host._http_listener_thread is listener
        assert type(host._http_stop_error) is ControlTransportError
        assert host._http_stop_error.code == 'listener_close_failed'
        assert server.socket.fileno() != -1 and host._service is not None
        host.close()
        assert host.state == 'closed' and host._http is None
        assert attempts == [server, server]
        assert not listener.is_alive() and server.socket.fileno() == -1
    finally:
        if host.state != 'closed':
            host.close()
        store.close()


def test_http_stop_helper_never_overlaps_on_retry(tmp_path, monkeypatch):
    host, store, _, _, _ = setup_host(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    try:
        host.start()
        http = host._http
        original_stop = http.stop
        attempts = []

        def blocked_stop():
            attempts.append(http)
            entered.set()
            release.wait(5)
            original_stop()

        monkeypatch.setattr(http, 'stop', blocked_stop)
        with pytest.raises(RuntimeError, match='http_listener'):
            host.close()
        assert entered.is_set()
        helper = host._http_stop_thread
        with pytest.raises(RuntimeError, match='http_listener'):
            host.close()
        assert host._http_stop_thread is helper and helper.is_alive()
        assert attempts == [http] and host._service is not None
        release.set()
        helper.join(2)
        assert not helper.is_alive()
        host.close()
        assert host.state == 'closed' and attempts == [http]
    finally:
        release.set()
        if host.state != 'closed':
            host.close()
        store.close()


@pytest.mark.parametrize('code', [
    'listener_stop_pending', 'listener_stop_failed', 'listener_requests_in_flight',
    'listener_close_pending', 'listener_close_failed',
])
def test_http_finite_stop_error_retries_only_on_next_close(tmp_path, monkeypatch, code):
    host, store, _, _, _ = setup_host(tmp_path, monkeypatch)
    try:
        host.start()
        http = host._http
        original_stop = http.stop
        attempts = []

        def stop_once():
            attempts.append(http)
            if len(attempts) == 1:
                raise ControlTransportError(code)
            original_stop()

        monkeypatch.setattr(http, 'stop', stop_once)
        with pytest.raises(RuntimeError, match='http_listener'):
            host.close()
        assert host._http is http and attempts == [http]
        host.close()
        assert host.state == 'closed' and attempts == [http, http]
    finally:
        if host.state != 'closed':
            host.close()
        store.close()


def test_http_opaque_stop_errors_are_retained_without_retry(tmp_path, monkeypatch):
    class DerivedTransportError(ControlTransportError):
        pass

    for index, error in enumerate((RuntimeError('listener_stop_failed'),
                                   ControlTransportError('unknown'),
                                   DerivedTransportError('listener_stop_failed'))):
        host, store, _, _, _ = setup_host(tmp_path / str(index), monkeypatch)
        try:
            host.start()
            http = host._http
            original_stop = http.stop
            attempts = []

            def fail():
                attempts.append(http)
                raise error

            monkeypatch.setattr(http, 'stop', fail)
            for _ in range(2):
                with pytest.raises(RuntimeError, match='http_listener'):
                    host.close()
                assert host.state == 'close_uncertain' and host._http is http
                assert host._http_stop_error is error and host._service is not None
            assert attempts == [http]
        finally:
            # Release the synthetic listener after checking the sticky failure.
            monkeypatch.setattr(http, 'stop', original_stop)
            host._http_stop_error = ControlTransportError('listener_stop_failed')
            host.close()
            store.close()


def _host_with_stop_file(tmp_path, monkeypatch, *, enable_stop_file):
    unit = make_unit(tmp_path, name='alpha', access=AccessMode.DIRECT)
    config = make_config(tmp_path, unit)
    store = EventStore(config.home, SystemClock())
    auth = SingleOperatorAuthenticator(OperatorIdentity('alice', OperatorRole.OPERATOR, ('*',)))
    return module.RuntimeServiceHost(
        config, authenticator=auth, gateway_bridge=None, monitor_bridge=None,
        event_store=store, unit_strategies={}, enable_stop_file=enable_stop_file,
    )


def test_enable_stop_file_opt_in_is_validated_and_stored(tmp_path, monkeypatch):
    """D66-05 stop-file channel is opt-in: default off, explicit True stored."""
    default_host = setup_host(tmp_path, monkeypatch)[0]
    assert default_host._enable_stop_file is False
    assert _host_with_stop_file(tmp_path, monkeypatch, enable_stop_file=True)._enable_stop_file is True
    with pytest.raises(ValueError, match='INVALID_RUNTIME_HOST_BINDINGS'):
        _host_with_stop_file(tmp_path, monkeypatch, enable_stop_file=1)


@pytest.mark.parametrize('gateway,monitor', [(True, False), (False, True)])
def test_normal_mode_still_requires_configured_bridges(tmp_path, gateway, monitor):
    unit = make_unit(tmp_path, name='alpha',
                     access=AccessMode.GATEWAY if gateway else AccessMode.DIRECT)
    config = replace(make_config(tmp_path, unit),
                     monitor_events=MonitorEventsConfig(enabled=monitor))
    store = EventStore(config.home, SystemClock())
    auth = SingleOperatorAuthenticator(OperatorIdentity('alice', OperatorRole.OPERATOR, ('*',)))
    try:
        with pytest.raises(ValueError, match='INVALID_RUNTIME_HOST_BINDINGS'):
            module.RuntimeServiceHost(
                config, authenticator=auth, gateway_bridge=None, event_store=store,
                monitor_bridge=None, unit_strategies={'alpha': 'one'} if gateway else {},
            )
    finally:
        store.close()


@pytest.mark.parametrize('override', [
    {'gateway_bridge': object()},
    {'monitor_bridge': object()},
    {'enable_stop_file': True},
    {'recover_runs': True},
    {'recover_in_flight': True},
])
def test_observe_only_host_rejects_bridges_and_write_channels(tmp_path, override):
    unit = make_unit(tmp_path, name='alpha', access=AccessMode.GATEWAY)
    config = replace(make_config(tmp_path, unit), monitor_events=MonitorEventsConfig(enabled=True))
    store = EventStore(config.home, SystemClock())
    auth = SingleOperatorAuthenticator(OperatorIdentity('alice', OperatorRole.OPERATOR, ('*',)))
    arguments = {
        'authenticator': auth,
        'gateway_bridge': None,
        'monitor_bridge': None,
        'event_store': store,
        'unit_strategies': {'alpha': 'one'},
        'enable_stop_file': False,
        'recover_runs': False,
        'recover_in_flight': False,
        'observe_only': True,
    }
    arguments.update(override)
    try:
        with pytest.raises(ValueError, match='INVALID_RUNTIME_HOST_BINDINGS'):
            module.RuntimeServiceHost(config, **arguments)
    finally:
        store.close()


def test_cli_observe_only_skips_bridge_and_key_loading_and_serves_viewer(
    tmp_path, monkeypatch, capsys,
):
    unit = make_unit(tmp_path, name='alpha', access=AccessMode.GATEWAY)
    runtime = replace(make_config(tmp_path, unit), monitor_events=MonitorEventsConfig(enabled=True))
    config = SimpleNamespace(
        runtime=runtime,
        unit_strategies={'alpha': 'one'},
        token_file=object(),
        gateway=object(),
        monitor=object(),
    )
    auth = SingleOperatorAuthenticator(OperatorIdentity('alice', OperatorRole.OPERATOR, ('*',)))
    calls = {'build_bridges': 0, 'key_loader': 0}

    def forbidden_builder(*args, **kwargs):
        calls['build_bridges'] += 1
        raise AssertionError('observe-only must not construct Gateway or Monitor bridges')

    def forbidden_key_loader(*args, **kwargs):
        calls['key_loader'] += 1
        raise AssertionError('observe-only must not load CURVE keys')

    monkeypatch.setattr(operator_cli, 'load_authenticator', lambda _config: auth)
    monkeypatch.setattr(operator_cli, 'build_bridges', forbidden_builder)
    monkeypatch.setattr(operator_config_module, '_load_keys', forbidden_key_loader)
    observed = {}

    def inspect_service_then_close(host, requested, stop_directory):
        observed['host'] = host
        assert stop_directory is None
        assert host.state == 'running'
        assert host.observe_only is True
        assert host._enable_stop_file is False
        assert host._recover_runs is False
        assert host._recover_in_flight is False
        assert host.gateway_bridge is None and host.monitor_bridge is None
        assert host._gateway_started is False and host._monitor_started is False
        assert host._relay.enabled is False and host._relay.port is None
        assert host._poll_thread is None and host.telemetry_status == 'disabled'

        listed = host._service.dispatch('runtime.units.list')
        assert listed.status == 'OK' and listed.data['role'] == 'viewer'
        status = host._service.dispatch('runtime.unit.status', unit_name='alpha')
        gateway_status = status.data['unit']['gateway']
        assert gateway_status['available'] is False
        assert gateway_status['code'] == 'gateway_control_port_unavailable'
        denied = host._service.dispatch(
            'runtime.unit.start', unit_name='alpha', command_id='observe-only-start-denied',
            reason='observe-only must reject writes',
        )
        assert denied.status == 'REJECTED' and denied.code == 'not_authorized'
        assert not Path(host._supervisor.store.runs_dir).exists()

        pause = host._mux.pause(unit, strategy_uuid='one')
        assert pause.status == 'REJECTED' and pause.code == 'gateway_control_port_unavailable'
        resume = host._mux.resume(unit, strategy_uuid='one')
        assert resume.status == 'REJECTED' and resume.code == 'gateway_control_port_unavailable'
        drain = host._mux.drain(unit, strategy_uuid='one')
        assert drain.accepted is False and drain.code == 'gateway_control_port_unavailable'
        host.close()

    monkeypatch.setattr(operator_cli, '_wait_close', inspect_service_then_close)
    assert operator_cli._run(config, observe_only=True) == 0
    assert calls == {'build_bridges': 0, 'key_loader': 0}
    output = capsys.readouterr().out
    assert 'runtime_control_mode observe_only' in output
    assert f'runtime_control_listening 127.0.0.1 {runtime.api.port}' in output
    assert 'runtime_control_closed' in output
    assert observed['host'].event_store.relay is None


def test_cli_normal_mode_still_builds_bridges_and_keeps_operator_role(
    tmp_path, monkeypatch, capsys,
):
    unit = make_unit(tmp_path, name='alpha')
    runtime = make_config(tmp_path, unit)
    config = SimpleNamespace(runtime=runtime, unit_strategies={}, token_file=object())
    auth = SingleOperatorAuthenticator(OperatorIdentity('alice', OperatorRole.OPERATOR, ('*',)))
    built = []
    monkeypatch.setattr(operator_cli, 'load_authenticator', lambda _config: auth)

    def build(config_arg, event_store):
        built.append((config_arg, event_store))
        return None, None

    monkeypatch.setattr(operator_cli, 'build_bridges', build)

    def inspect_service_then_close(host, requested, stop_directory):
        assert host.observe_only is False
        assert host._recover_runs is True and host._recover_in_flight is True
        listed = host._service.dispatch('runtime.units.list')
        assert listed.status == 'OK' and listed.data['role'] == 'operator'
        host.close()

    monkeypatch.setattr(operator_cli, '_wait_close', inspect_service_then_close)
    assert operator_cli._run(config) == 0
    assert len(built) == 1 and built[0][0] is config
    output = capsys.readouterr().out
    assert 'runtime_control_mode observe_only' not in output
    assert f'runtime_control_listening 127.0.0.1 {runtime.api.port}' in output
    assert 'runtime_control_closed' in output
