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
import signal
import time
from dataclasses import dataclass

from .execution_parent_service_host import ParentConfig, ParentServiceHost, parse_config as parse_parent
from .execution_read_service_host import Config as ReadConfig, ReadServiceHost, _path, parse_config as parse_read
from .monitor_service_config import read_bounded

SCHEMA = 'bt_api_execution.combined_service.v1'


class CombinedServiceError(ValueError):
    pass


@dataclass(frozen=True)
class CombinedConfig:
    parent: object
    management: object


def _validate_pair(parent, read):
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
    return CombinedConfig(parent, read)


def parse_config(raw):
    try:
        if type(raw) is not dict or set(raw) != {'schema', 'parent', 'management'} or raw['schema'] != SCHEMA:
            raise ValueError
        return _validate_pair(parse_parent(raw['parent']), parse_read(raw['management']))
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
        if (config_path in (parent.registry_path, parent.server_public, parent.server_secret,
                            *(p for _, p in parent.principal_keys), read.server_public,
                            read.server_secret, read.client_public)
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
            _validate_pair(config.parent, config.management)
        except Exception:
            raise CombinedServiceError('INVALID_CONFIGURATION') from None
        self.config = config
        self.parent = self.management = None
        self.state = 'new'

    def start(self):
        if self.state != 'new':
            raise CombinedServiceError('INVALID_STATE')
        self.state = 'close_uncertain'
        try:
            self.parent = ParentServiceHost(self.config.parent)
            self.parent.start()
            self.management = ReadServiceHost(self.config.management, borrowed_from=self.parent)
            self.management.start()
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
        return {'parent': parent, 'management': management}

    def close(self):
        if self.state == 'closed':
            return
        self.state = 'close_uncertain'
        try:
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
        sub.add_parser(command).add_argument('--config', required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == 'validate':
            print('EXECUTION_COMBINED_CONFIG_VALID', flush=True)
            return 0
        host, signals, failed = CombinedServiceHost(config), 0, False
        def stop(_signum, _frame):
            nonlocal signals
            signals += 1
        previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            try:
                host.start()
                status = host.poll()
                print('EXECUTION_COMBINED_PARENT_' + status['parent'], flush=True)
                print('EXECUTION_COMBINED_MANAGEMENT_' + status['management'], flush=True)
                while not signals and 'UNAVAILABLE' not in status.values():
                    status = host.poll()
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
                        print('EXECUTION_COMBINED_CLEANUP_PENDING', flush=True)
                        while signals == before:
                            time.sleep(0.1)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        print('EXECUTION_COMBINED_FAILED' if failed else 'EXECUTION_COMBINED_STOPPED', flush=True)
        return int(failed)
    except Exception:
        print('EXECUTION_COMBINED_FAILED', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
