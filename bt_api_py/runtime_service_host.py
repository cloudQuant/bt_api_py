"""Programmatic Runtime HTTP composition with explicitly supplied authorities.

The event store is borrowed and must be dedicated to this host while running.
Bridges and the relay are host-owned. Closing never stops managed strategies.
This composition makes no claim about trading or account readiness.
"""
from __future__ import annotations

import math
import os
import threading
from collections.abc import Mapping
from types import MappingProxyType

from bt_api_run_contract import AccessMode, ValidatedRuntimeConfig
from bt_api_runtime.control_http import ControlTransportError, build_control_http_server
from bt_api_runtime.control_ledger import ControlDatabase
from bt_api_runtime.control_service import build_control_service
from bt_api_runtime.events import EventStore
from bt_api_runtime.gateway_control import (GatewayControlOutcome, GatewayDrainResult,
                                             GatewayStatusView)
from bt_api_runtime.supervision import (PosixProcessAdapter, RuntimeSupervisor,
                                        SystemClock, WindowsProcessAdapter)
from bt_api_runtime.telemetry_relay import TelemetryRelay
from bt_api_py.runtime_gateway_control import RuntimeGatewayControlBridge
from bt_api_py.runtime_monitor_bridge import RuntimeMonitorBridge


_RETRYABLE_HTTP_STOP_CODES = frozenset({
    'listener_stop_pending', 'listener_stop_failed', 'listener_requests_in_flight',
    'listener_close_pending', 'listener_close_failed',
})


class _ControlMux:
    def __init__(self, config, bindings, bridge):
        self._units = {unit.name: unit for unit in config.units}
        self._bindings = bindings
        self._clients = {strategy: bridge.client_for_strategy(config, strategy)
                         for strategy in bindings.values()}
        self.supervisor = None

    def _client(self, unit, strategy):
        expected = self._bindings.get(getattr(unit, 'name', None))
        if (expected is None or self._units.get(unit.name) is not unit
                or unit.access is not AccessMode.GATEWAY or strategy != expected):
            return None
        try:
            read = self.supervisor.read_identity(unit.name)
            if not read.present or read.identity.strategy_uuid != expected:
                return None
        except Exception:
            return None
        return self._clients.get(expected)

    def pause(self, unit, **kwargs):
        client = self._client(unit, kwargs.get('strategy_uuid'))
        if client is None:
            return GatewayControlOutcome('REJECTED', 'gateway_control_port_unavailable')
        return client.pause(unit, **kwargs)

    def resume(self, unit, **kwargs):
        client = self._client(unit, kwargs.get('strategy_uuid'))
        if client is None:
            return GatewayControlOutcome('REJECTED', 'gateway_control_port_unavailable')
        return client.resume(unit, **kwargs)

    def gateway_status(self, unit, **kwargs):
        client = self._client(unit, kwargs.get('strategy_uuid'))
        if client is None:
            return GatewayStatusView(False, 'gateway_control_port_unavailable', (), '', {})
        return client.gateway_status(unit, **kwargs)

    def drain(self, unit, **kwargs):
        if getattr(unit, 'access', None) is AccessMode.DIRECT:
            return GatewayDrainResult('invalid_target', False, {'reason': 'not_a_gateway_unit'})
        client = self._client(unit, kwargs.get('strategy_uuid'))
        if client is None:
            return GatewayDrainResult('gateway_control_port_unavailable', False)
        return client.drain(unit, **kwargs)


class _HttpDispatchGate:
    """Prevent a previously accepted HTTP request from entering during close."""
    def __init__(self, service):
        self._service = service
        self._condition = threading.Condition()
        self._closing = False
        self._active = 0

    def __getattr__(self, name):
        return getattr(self._service, name)

    def dispatch(self, *args, **kwargs):
        from bt_api_runtime.control_service import ControlResponse
        with self._condition:
            if self._closing:
                return ControlResponse('REJECTED', 'service_unavailable')
            self._active += 1
        try:
            return self._service.dispatch(*args, **kwargs)
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def close_gate(self, timeout):
        with self._condition:
            self._closing = True
            return self._condition.wait_for(lambda: not self._active, timeout)


class RuntimeServiceHost:
    """Own a Runtime service, listener and bridges; borrow ``event_store``.

    ``unit_strategies`` must contain exactly the gateway unit names, with a
    unique trusted strategy UUID for each. The supervisor's current identity
    is checked against that declaration on *every* control and drain call.
    Both bridge inputs must be fresh and dedicated to this host. Monitor is
    required when forwarding is enabled; no config file or secret is loaded.
    """
    def __init__(self, config, *, authenticator, gateway_bridge, event_store,
                 monitor_bridge, unit_strategies: Mapping[str, str],
                 poll_interval=0.05, close_timeout=5.0):
        if (type(config) is not ValidatedRuntimeConfig or config.api.transport != 'http_json'
                or not authenticator or not callable(getattr(authenticator, 'authenticate', None))
                or type(event_store) is not EventStore or not event_store.available
                or event_store.home != os.path.abspath(config.home) or event_store.relay is not None
                or not isinstance(unit_strategies, Mapping)
                or any(type(x) not in (int, float) or isinstance(x, bool) or not math.isfinite(x)
                       or not 0 < x <= 60 for x in (poll_interval, close_timeout))):
            raise ValueError('INVALID_RUNTIME_HOST_CONFIG')
        gateway_units = {u.name for u in config.units if u.access is AccessMode.GATEWAY}
        bindings = dict(unit_strategies)
        if (set(bindings) != gateway_units
                or any(type(x) is not str or not x or x != x.strip() for x in bindings.values())
                or len(set(bindings.values())) != len(bindings)
                or (gateway_units and not isinstance(gateway_bridge, RuntimeGatewayControlBridge))
                or (gateway_bridge is not None and gateway_bridge.state != 'new')
                or (gateway_bridge is not None and set(gateway_bridge._entries) != set(bindings.values()))
                or (not gateway_units and gateway_bridge is not None)
                or (config.monitor_events.enabled and not isinstance(monitor_bridge, RuntimeMonitorBridge))
                or (monitor_bridge is not None and (not isinstance(monitor_bridge, RuntimeMonitorBridge)
                                                  or monitor_bridge._store is not event_store
                                                  or monitor_bridge.state != 'new'))):
            raise ValueError('INVALID_RUNTIME_HOST_BINDINGS')
        self.config, self.authenticator, self.event_store = config, authenticator, event_store
        self.gateway_bridge, self.monitor_bridge = gateway_bridge, monitor_bridge
        self._mux = _ControlMux(config, MappingProxyType(bindings), gateway_bridge)
        self._poll_interval, self._close_timeout = float(poll_interval), float(close_timeout)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._state = 'new'
        self._gateway_started = self._monitor_started = False
        self._relay = self._supervisor = self._database = self._service = None
        self._gate = self._http = self._poll_thread = self._http_stop_thread = None
        self._http_stop_error = None
        self._http_listener_thread = None
        self._poll_error = None
        self._cleanup_components = ()

    @property
    def state(self):
        return self._state

    @property
    def cleanup_components(self):
        """Fixed categories from the latest close attempt, with no exception details."""
        return self._cleanup_components

    @property
    def telemetry_status(self):
        """Finite drain-loop status; telemetry failure does not imply resource leakage."""
        if self._poll_error is not None:
            return 'poll_failed'
        if not self.config.monitor_events.enabled:
            return 'disabled'
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return 'running'
        return 'stopped'

    @property
    def bound_address(self):
        if self._state != 'running':
            raise RuntimeError('RUNTIME_HOST_NOT_RUNNING')
        return self._http.bound_address

    def _poll(self):
        try:
            self._relay.claim_owner()
            while not self._stop.is_set():
                self._relay.poll()
                self._stop.wait(self._poll_interval)
        except BaseException:
            self._poll_error = 'poll_failed'

    def start(self):
        with self._lock:
            if self._state != 'new':
                raise RuntimeError('RUNTIME_HOST_LIFECYCLE_INVALID')
            self._state = 'starting'
            try:
                if self.gateway_bridge is not None:
                    self._gateway_started = True
                    self.gateway_bridge.start()
                    if self.gateway_bridge.state != 'running':
                        raise RuntimeError('RUNTIME_GATEWAY_UNAVAILABLE')
                if self.monitor_bridge is not None:
                    self._monitor_started = True
                    result = self.monitor_bridge.start()
                    if (getattr(result, 'healthy', False) is not True
                            or self.monitor_bridge.state != 'running'):
                        raise RuntimeError('RUNTIME_MONITOR_UNAVAILABLE')
                clock = SystemClock()
                self._relay = TelemetryRelay(self.monitor_bridge, enabled=self.config.monitor_events.enabled)
                process = WindowsProcessAdapter() if os.name == 'nt' else PosixProcessAdapter()
                self._supervisor = RuntimeSupervisor(self.config, process=process, clock=clock,
                                                     drain_port=self._mux)
                self._mux.supervisor = self._supervisor
                self._database = ControlDatabase(self.config.home, clock)
                self._service = build_control_service(self.config, supervisor=self._supervisor,
                    database=self._database, authenticator=self.authenticator, gateway_control=self._mux,
                    drain_port=self._mux, events=self.event_store, telemetry=self._relay,
                    clock=clock, transport='http_json')
                self._gate = _HttpDispatchGate(self._service)
                self._http = build_control_http_server(self._gate, bind=self.config.api.bind,
                                                       port=self.config.api.port, clock=clock)
                self._poll_thread = threading.Thread(target=self._poll, name='runtime-telemetry-owner', daemon=True)
                self._poll_thread.start()
                self._http.start()
                self._http_listener_thread = self._http._thread
                self._state = 'running'
                return self
            except BaseException as error:
                try:
                    self.close()
                except Exception as cleanup:
                    raise RuntimeError('RUNTIME_HOST_START_CLEANUP_UNCERTAIN') from cleanup
                raise error

    def _stop_http(self):
        try:
            self._http.stop()
        except BaseException as exc:
            self._http_stop_error = exc

    def close(self):
        with self._lock:
            if self._state == 'closed':
                return
            self._cleanup_components = ()
            self._state = 'closing'
            errors = []
            if self._gate is not None and not self._gate.close_gate(self._close_timeout):
                errors.append('http_dispatch')
            if self._http is not None:
                if self._http_listener_thread is None:
                    self._http_listener_thread = self._http._thread
                retry_stop = (self._http_stop_thread is not None
                              and not self._http_stop_thread.is_alive()
                              and type(self._http_stop_error) is ControlTransportError
                              and type(self._http_stop_error.code) is str
                              and self._http_stop_error.code in _RETRYABLE_HTTP_STOP_CODES)
                if self._http_stop_thread is None or retry_stop:
                    self._http_stop_error = None
                    self._http_stop_thread = threading.Thread(target=self._stop_http, daemon=True)
                    self._http_stop_thread.start()
                self._http_stop_thread.join(self._close_timeout)
                if (self._http_stop_thread.is_alive() or self._http_stop_error is not None
                        or (self._http_listener_thread is not None and self._http_listener_thread.is_alive())
                        or self._http._server.socket.fileno() != -1):
                    errors.append('http_listener')
                else:
                    self._http = None
            self._stop.set()
            if self._poll_thread is not None:
                self._poll_thread.join(self._close_timeout)
                if self._poll_thread.is_alive():
                    errors.append('telemetry_owner')
                else:
                    self._poll_thread = None
            # Do not release the home lock or shared resources while an HTTP
            # dispatch is active; retry close after that request exits.
            if self._http is None and (self._gate is None or self._gate.close_gate(0)):
                if self._service is not None:
                    try:
                        self._service.close()
                        self._service = self._supervisor = self._database = None
                    except Exception:
                        errors.append('service')
                else:
                    for attr in ('_database', '_supervisor'):
                        value = getattr(self, attr)
                        if value is not None:
                            try:
                                value.close()
                                setattr(self, attr, None)
                            except Exception:
                                errors.append(attr)
            if self._poll_thread is None:
                if self._relay is not None:
                    try:
                        self._relay.close()
                        if self.event_store.relay is self._relay:
                            self.event_store.relay = None
                        self._relay = None
                    except Exception:
                        errors.append('relay')
                if self._monitor_started:
                    try:
                        self.monitor_bridge.close()
                        if self.monitor_bridge.state != 'closed':
                            raise RuntimeError('RUNTIME_MONITOR_CLEANUP_UNCERTAIN')
                        self._monitor_started = False
                    except Exception:
                        errors.append('monitor')
            if self._gateway_started and (self._gate is None or self._gate.close_gate(0)):
                try:
                    self.gateway_bridge.close()
                    if self.gateway_bridge.state != 'closed':
                        raise RuntimeError('RUNTIME_GATEWAY_CLEANUP_UNCERTAIN')
                    self._gateway_started = False
                except Exception:
                    errors.append('gateway')
            if errors:
                self._cleanup_components = tuple(
                    {'_database': 'database', '_supervisor': 'supervisor'}.get(error, error)
                    for error in errors)
                self._state = 'close_uncertain'
                raise RuntimeError('RUNTIME_HOST_CLEANUP_UNCERTAIN: ' + ', '.join(errors))
            self._state = 'closed'
