"""Co-locate local parent intake and audited management on one journal owner.

READY describes each listener only. Local receipts and observations do not
establish provider facts, account authority, or order dispatch.

An initial intake claim is single-use even if the management listener later
fails to start. Preserve the original journal and claim; after confirmed
cleanup, the operator must explicitly remove the initial-claim setting to
resume using the existing lease. This setting is an assertion, not evidence
of account authority or a license to retry an uncertain operation.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ._owner_stop_file import marker_requested, new_marker
from .execution_parent_service_host import ParentConfig, ParentServiceHost, parse_config as parse_parent
from .execution_read_service_host import Config as ReadConfig, ReadServiceHost, _path, parse_config as parse_read
from .execution_gateway_source_config import (GatewaySourceClient, StdinSourceControl,
    parse_config as parse_source, validate_config as validate_source)
from .monitor_service_config import read_bounded

SCHEMA = 'bt_api_execution.combined_service.v1'


class CombinedServiceError(ValueError):
    pass


@dataclass(frozen=True)
class CombinedConfig:
    parent: object
    management: object
    gateway_source: object = None


def _validate_pair(parent, read, gateway_source=None):
    if type(parent) is not ParentConfig or type(read) is not ReadConfig:
        raise ValueError
    parent_endpoint = parent.service.communication['profiles'][parent.service.transport_ref]['endpoints']['rpc_bind']
    if (parent.journal_root != read.journal_root or parent.storage_id != read.storage_id
            or parent.local_scope != read.local_scope or parent_endpoint == read.endpoint
            or (parent.wire_scope.execution_domain_ref, parent.wire_scope.account_ref,
                parent.wire_scope.strategy_ref) != (read.wire_scope.execution_domain_ref,
                read.wire_scope.account_ref, read.wire_scope.strategy_ref)):
        raise ValueError
    registry, audit = parent.registry_path, read.audit_root
    if registry == audit or audit in registry.parents or registry in audit.parents:
        raise ValueError
    keys = (parent.server_public, parent.server_secret, *(p for _, p in parent.principal_keys),
            read.server_public, read.server_secret, read.client_public)
    if any(key == audit or audit in key.parents or key == registry
           or key == parent.journal_root or parent.journal_root in key.parents for key in keys):
        raise ValueError
    if gateway_source is not None:
        validate_source(gateway_source, parent, read)
    return CombinedConfig(parent, read, gateway_source)


def parse_config(raw):
    try:
        if (type(raw) is not dict or not {'schema', 'parent', 'management'} <= set(raw)
                or set(raw) - {'schema', 'parent', 'management', 'gateway_source'} or raw['schema'] != SCHEMA):
            raise ValueError
        parent, read = parse_parent(raw['parent']), parse_read(raw['management'])
        source = parse_source(raw['gateway_source'], parent, read) if 'gateway_source' in raw else None
        return _validate_pair(parent, read, source)
    except Exception:
        raise CombinedServiceError('INVALID_CONFIGURATION') from None


def load_config(path):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    try:
        config_path = _path(path)
        config = parse_config(json.loads(read_bounded(config_path, 1024 * 1024, protected=True).decode('utf-8'),
            object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())))
        parent, read = config.parent, config.management
        source_keys = (() if config.gateway_source is None else
                       (config.gateway_source.server_public, config.gateway_source.client_public,
                        config.gateway_source.client_secret))
        if (config_path in (parent.registry_path, parent.server_public, parent.server_secret,
                            *(p for _, p in parent.principal_keys), read.server_public,
                            read.server_secret, read.client_public, *source_keys)
                or config_path == parent.journal_root or parent.journal_root in config_path.parents
                or config_path == read.audit_root or read.audit_root in config_path.parents):
            raise ValueError
        return config
    except Exception:
        raise CombinedServiceError('INVALID_CONFIGURATION') from None


class CombinedServiceHost:
    """Single-use owner; the management listener must release its borrow first."""
    def __init__(self, config):
        if type(config) is not CombinedConfig:
            raise CombinedServiceError('INVALID_CONFIGURATION')
        try:
            _validate_pair(config.parent, config.management, config.gateway_source)
        except Exception:
            raise CombinedServiceError('INVALID_CONFIGURATION') from None
        self.config = config
        self.parent = self.management = self.gateway_source = None
        self._owner_pid = self._owner_thread = None
        self.state = 'new'

    def start(self):
        if self.state != 'new':
            raise CombinedServiceError('INVALID_STATE')
        self._owner_pid, self._owner_thread = os.getpid(), threading.current_thread()
        self.state = 'close_uncertain'
        try:
            self.parent = ParentServiceHost(self.config.parent)
            self.parent.start()
            self.management = ReadServiceHost(self.config.management, borrowed_from=self.parent)
            self.management.start()
            if self.config.gateway_source is not None:
                self.gateway_source = GatewaySourceClient(self.config.gateway_source, self.parent)
                self.gateway_source.start()
            self.state = 'running'
        except BaseException:
            try:
                self.close()
            except BaseException:
                raise CombinedServiceError('CLEANUP_UNKNOWN') from None
            raise CombinedServiceError('START_FAILED') from None

    def poll(self):
        if self.state != 'running' or self.parent is None or self.management is None:
            raise CombinedServiceError('INVALID_STATE')
        try:
            parent = self.parent.poll()
            if parent not in ('READY', 'UNREADY'):
                parent = 'UNAVAILABLE'
        except Exception:
            parent = 'UNAVAILABLE'
        try:
            self.management.poll()
            management = 'READY'
        except Exception:
            management = 'UNAVAILABLE'
        result = {'parent': parent, 'management': management}
        if self.config.gateway_source is not None:
            result['gateway_source'] = 'UNREADY' if self.gateway_source is None else self.gateway_source.status()
        return result

    def observe_source(self, parent_ref, *, request_id, request_sha256):
        """Make one explicit local-only SOURCE observation; never retry it."""
        if (self.state != 'running' or self.gateway_source is None or os.getpid() != self._owner_pid
                or threading.current_thread() is not self._owner_thread):
            return 'UNKNOWN'
        return self.gateway_source.observe(parent_ref, request_id=request_id, request_sha256=request_sha256)

    def close(self):
        if self.state == 'closed':
            return
        self.state = 'close_uncertain'
        try:
            if self.gateway_source is not None:
                self.gateway_source.close()
                self.gateway_source = None
            if self.management is not None:
                self.management.close()
                self.management = None
            if self.parent is not None:
                self.parent.close()
                self.parent = None
        except BaseException:
            raise CombinedServiceError('CLEANUP_UNKNOWN') from None
        self.state = 'closed'


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('validate', 'run'):
        child = sub.add_parser(command)
        child.add_argument('--config', required=True)
        if command == 'run':
            child.add_argument('--source-control-stdin', action='store_true')
            child.add_argument('--enable-owner-stop-file', action='store_true',
                               help='allow local cooperative shutdown with a protected empty marker')
    args = parser.parse_args(argv)
    control = None
    control_requested = args.command == 'run' and args.source_control_stdin
    try:
        if control_requested:
            control = StdinSourceControl(sys.stdin.fileno(), sys.stdout.fileno())
        config = load_config(args.config)
        if args.command == 'validate':
            print('EXECUTION_COMBINED_CONFIG_VALID', flush=True)
            return 0
        if args.source_control_stdin and config.gateway_source is None:
            raise CombinedServiceError('INVALID_CONFIGURATION')
        def emit(line):
            if control is None:
                print(line, flush=True)
            else:
                control.report(line)
                control.flush()
        def announce_stop_file():
            if not args.enable_owner_stop_file:
                return None
            marker = new_marker(Path(args.config).parent, 'execution')
            emit('EXECUTION_COMBINED_STOP_FILE ' + marker.name)
            return marker
        host, signals, failed = CombinedServiceHost(config), 0, False
        stop_file = None
        def stop_requested():
            return bool(signals) or marker_requested(stop_file)
        def stop(_signum, _frame):
            nonlocal signals
            signals += 1
        previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            try:
                host.start()
                status = host.poll()
                emit('EXECUTION_COMBINED_PARENT_' + status['parent'])
                emit('EXECUTION_COMBINED_MANAGEMENT_' + status['management'])
                if 'gateway_source' in status:
                    emit('EXECUTION_COMBINED_GATEWAY_SOURCE_' + status['gateway_source'])
                stop_file = announce_stop_file()
                while not stop_requested() and 'UNAVAILABLE' not in status.values():
                    status = host.poll()
                    if stop_requested() or 'UNAVAILABLE' in status.values():
                        break
                    if control is not None:
                        control.flush()
                        if not control.accepting:
                            continue
                        item, overflow = control.poll()
                        if overflow:
                            emit('EXECUTION_SOURCE_QUEUE_FULL')
                        if item is not False:
                            value = control.request(item)
                            if stop_requested():
                                break
                            result = ('INVALID_REQUEST' if value is None else host.observe_source(
                                value['parent_ref'], request_id=value['request_id'], request_sha256=value['request_sha256']))
                            if result not in ('OBSERVED', 'HISTORICAL', 'UNKNOWN', 'INVALID_REQUEST'):
                                result = 'UNKNOWN'
                            emit('EXECUTION_SOURCE_' + result)
                if 'UNAVAILABLE' in status.values():
                    failed = True
            except Exception:
                failed = True
            finally:
                while True:
                    before = signals
                    try:
                        host.close()
                        break
                    except Exception:
                        failed = True
                        emit('EXECUTION_COMBINED_CLEANUP_PENDING')
                        retry_file = announce_stop_file()
                        while signals == before and not marker_requested(retry_file):
                            time.sleep(0.1)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        emit('EXECUTION_COMBINED_FAILED' if failed else 'EXECUTION_COMBINED_STOPPED')
        if control is not None:
            control.flush(); control.close(); control = None
        return int(failed)
    except Exception:
        if control is not None:
            control.report('EXECUTION_COMBINED_FAILED'); control.flush()
            try:
                control.close()
            except Exception:
                pass
        elif not control_requested:
            print('EXECUTION_COMBINED_FAILED', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
