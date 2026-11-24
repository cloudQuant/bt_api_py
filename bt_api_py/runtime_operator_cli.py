"""Explicit local Runtime operator service entry point.

``validate`` is offline and does not read credentials. ``run`` starts a
control listener only; it never starts a managed strategy automatically.
"""
from __future__ import annotations

import argparse
import signal
import stat
import sys
import threading
from pathlib import Path
from uuid import uuid4

from bt_api_runtime.control_http import ControlTransportError
from bt_api_runtime.events import EventStore
from bt_api_runtime.supervision import SystemClock

from bt_api_py.monitor_service_config import read_bounded
from bt_api_py.runtime_operator_config import build_bridges, load_authenticator, load_config
from bt_api_py.runtime_service_host import RuntimeServiceHost


_CLEANUP_COMPONENTS = frozenset({
    'http_dispatch', 'http_listener', 'telemetry_owner', 'service', 'database',
    'supervisor', 'relay', 'monitor', 'gateway',
})


def _emit(message, *, error=False):
    print(message, file=sys.stderr if error else sys.stdout, flush=True)


def _emit_start_failure(error):
    _emit('runtime_start_failed', error=True)
    if (type(error) is ControlTransportError and type(error.code) is str
            and error.code in ('listener_bind_failed', 'non_loopback_bind', 'invalid_port')):
        _emit(f'runtime_start_code={error.code}', error=True)


def _emit_cleanup_uncertain(host=None, *, event_store=False):
    _emit('runtime_cleanup_uncertain; signal to retry', error=True)
    components = ()
    if event_store:
        components = ('event_store',)
    elif host is not None:
        try:
            cached = host.cleanup_components
            if type(cached) is tuple:
                components = tuple(item for item in cached
                                   if type(item) is str and item in _CLEANUP_COMPONENTS)
        except Exception:
            pass
    _emit('runtime_cleanup_components=' + (','.join(components) if components else 'unknown'),
          error=True)


def _wait_for_stop_request(requested, stop_directory):
    if stop_directory is None:
        requested.wait()
    else:
        marker = stop_directory / f'runtime-{uuid4().hex}.stop'
        _emit(f'runtime_control_stop_file {marker.name}')
        while not requested.wait(0.2):
            try:
                if (stat.S_ISREG(marker.lstat().st_mode)
                        and read_bounded(marker, 0, protected=True) == b''):
                    break
            except (OSError, ValueError):
                continue
    requested.clear()


def _wait_close(host, requested, stop_directory=None):
    while True:
        _wait_for_stop_request(requested, stop_directory)
        try:
            host.close()
        except Exception:
            _emit_cleanup_uncertain(host)
            continue
        if host.state == 'closed':
            return
        _emit_cleanup_uncertain(host)


def _close_store(store, requested, stop_directory):
    """Retain our borrowed store until an explicit cleanup retry succeeds."""
    while True:
        try:
            store.close()
            if store.close_confirmed is True:
                return
        except Exception:
            _emit_cleanup_uncertain(event_store=True)
        else:
            _emit_cleanup_uncertain(event_store=True)
        _wait_for_stop_request(requested, stop_directory)


def _run(config, *, enable_stop_file=False, enable_owner_stop_file=False):
    requested = threading.Event()
    stop_directory = Path(config.runtime.home) if enable_owner_stop_file else None
    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, lambda signum, frame: requested.set())
    store = host = None
    startup_failed = False
    try:
        auth = load_authenticator(config)
        store = EventStore(config.runtime.home, SystemClock())
        if not store.available:
            raise RuntimeError('EVENT_STORE_UNAVAILABLE')
        gateway, monitor = build_bridges(config, store)
        host = RuntimeServiceHost(config.runtime, authenticator=auth, gateway_bridge=gateway,
            event_store=store, monitor_bridge=monitor, unit_strategies=config.unit_strategies,
            enable_stop_file=enable_stop_file)
        try:
            host.start()
        except Exception as error:
            startup_failed = True
            _emit_start_failure(error)
            if host.state == 'closed':
                return 1
            if host.state != 'close_uncertain':
                try:
                    host.close()
                except Exception:
                    _emit_cleanup_uncertain(host)
                    _wait_close(host, requested, stop_directory)
                    return 1
                else:
                    if host.state == 'closed':
                        return 1
            _emit_cleanup_uncertain(host)
        if not startup_failed:
            address, port = host.bound_address
            _emit(f'runtime_control_listening {address} {port}')
        _wait_close(host, requested, stop_directory)
        return 1 if startup_failed else 0
    except Exception:
        _emit('runtime_start_failed', error=True)
        if host is not None and host.state != 'closed':
            try:
                host.close()
            except Exception:
                _emit_cleanup_uncertain(host)
                _wait_close(host, requested, stop_directory)
            else:
                if host.state != 'closed':
                    _emit_cleanup_uncertain(host)
                    _wait_close(host, requested, stop_directory)
        return 1
    finally:
        # An uncertain host must remain alive with its borrowed EventStore;
        # the loop above exits only after a confirmed close.
        if store is not None and (host is None or host.state == 'closed'):
            _close_store(store, requested, stop_directory)
        if host is not None and host.state == 'closed' and not startup_failed:
            _emit('runtime_control_closed')
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run an explicitly configured Runtime control service')
    parser.add_argument('action', choices=('validate', 'run'))
    parser.add_argument('--config', required=True, help='absolute path to public deployment JSON')
    parser.add_argument('--enable-stop-file', action='store_true',
                        help='opt in to the stop-request file channel for supervised strategy units')
    parser.add_argument('--enable-owner-stop-file', action='store_true',
                        help='allow local cooperative shutdown of this CLI with a protected empty marker')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except Exception:
        _emit('runtime_configuration_invalid', error=True)
        return 2
    if args.action == 'validate':
        _emit('runtime_configuration_valid')
        return 0
    return _run(config, enable_stop_file=args.enable_stop_file,
                enable_owner_stop_file=args.enable_owner_stop_file)


if __name__ == '__main__':
    raise SystemExit(main())
