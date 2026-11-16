"""Explicit local Runtime operator service entry point.

``validate`` is offline and does not read credentials. ``run`` starts a
control listener only; it never starts a managed strategy automatically.
"""
from __future__ import annotations

import argparse
import signal
import sys
import threading

from bt_api_runtime.events import EventStore
from bt_api_runtime.supervision import SystemClock

from bt_api_py.runtime_operator_config import build_bridges, load_authenticator, load_config
from bt_api_py.runtime_service_host import RuntimeServiceHost


def _emit(message, *, error=False):
    print(message, file=sys.stderr if error else sys.stdout, flush=True)


def _wait_close(host, requested):
    while True:
        requested.wait()
        requested.clear()
        try:
            host.close()
        except Exception:
            _emit('runtime_cleanup_uncertain; signal to retry', error=True)
            continue
        if host.state == 'closed':
            return
        _emit('runtime_cleanup_uncertain; signal to retry', error=True)


def _close_store(store, requested):
    """Close our borrowed store; retain it for signal-driven retry on failure."""
    while True:
        try:
            store.close()
            if store.close_confirmed is True:
                return
        except Exception:
            _emit('runtime_cleanup_uncertain; signal to retry', error=True)
        else:
            _emit('runtime_cleanup_uncertain; signal to retry', error=True)
        requested.wait()
        requested.clear()


def _run(config):
    requested = threading.Event()
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
            event_store=store, monitor_bridge=monitor, unit_strategies=config.unit_strategies)
        try:
            host.start()
        except Exception:
            startup_failed = True
            _emit('runtime_start_failed', error=True)
            if host.state == 'closed':
                return 1
            if host.state != 'close_uncertain':
                try:
                    host.close()
                except Exception:
                    _emit('runtime_cleanup_uncertain; signal to retry', error=True)
                    _wait_close(host, requested)
                    return 1
                else:
                    if host.state == 'closed':
                        return 1
            _emit('runtime_cleanup_uncertain; signal to retry', error=True)
        if not startup_failed:
            address, port = host.bound_address
            _emit(f'runtime_control_listening {address} {port}')
        _wait_close(host, requested)
        return 1 if startup_failed else 0
    except Exception:
        _emit('runtime_start_failed', error=True)
        if host is not None and host.state != 'closed':
            try:
                host.close()
            except Exception:
                _emit('runtime_cleanup_uncertain; signal to retry', error=True)
                _wait_close(host, requested)
            else:
                if host.state != 'closed':
                    _emit('runtime_cleanup_uncertain; signal to retry', error=True)
                    _wait_close(host, requested)
        return 1
    finally:
        # An uncertain host must remain alive with its borrowed EventStore;
        # the loop above exits only after a confirmed close.
        if store is not None and (host is None or host.state == 'closed'):
            _close_store(store, requested)
        if host is not None and host.state == 'closed' and not startup_failed:
            _emit('runtime_control_closed')
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run an explicitly configured Runtime control service')
    parser.add_argument('action', choices=('validate', 'run'))
    parser.add_argument('--config', required=True, help='absolute path to public deployment JSON')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except Exception:
        _emit('runtime_configuration_invalid', error=True)
        return 2
    if args.action == 'validate':
        _emit('runtime_configuration_valid')
        return 0
    return _run(config)


if __name__ == '__main__':
    raise SystemExit(main())
